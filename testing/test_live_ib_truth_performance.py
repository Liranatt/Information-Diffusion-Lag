from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import pytest

from live.performance import excess_performance, passive_equity, passive_units
from live.config import LiveConfig
from live.control_pipeline import ControlPipeline
from live.data_fetcher import _partition_session_rows
from live.strategy_engine import StrategyEngine


def test_passive_benchmark_applies_external_flows_at_their_own_spy_price():
    flows = [
        {"amount": 10_000.0, "benchmark_price": 200.0},
        {"amount": -2_000.0, "benchmark_price": 250.0},
    ]

    units = passive_units(100_000.0, 100.0, flows)
    passive = passive_equity(100_000.0, 100.0, 300.0, flows)
    excess, excess_pct = excess_performance(320_000.0, passive)

    assert units == pytest.approx(1_042.0)
    assert passive == pytest.approx(312_600.0)
    assert excess == pytest.approx(7_400.0)
    assert excess_pct == pytest.approx(7_400.0 / 312_600.0 * 100.0)


def _exit_policy():
    return {
        "atr_mult": 3.0,
        "lock_activate": 0.02,
        "theta_out": 0.55,
    }


def _position(now, *, position_id=7, t_e=None):
    return {
        "position_id": position_id,
        "market_id": f"m{position_id}",
        "symbol": "AAPL",
        "qty": 3,
        "question": "Will Apple beat earnings?",
        "is_earnings": False,
        "entry_ts": now - timedelta(days=2),
        "entry_price": 100.0,
        "atr_pct": 0.05,
        "t_e": t_e or now + timedelta(days=10),
        "peak_ret": 0.0,
    }


class _ExitStore:
    def __init__(self, bars=None, probs=None):
        self.bars = bars or []
        self.probs = probs or []
        self.peaks = []

    async def daily_bars_since(self, _symbol, _since):
        return self.bars

    async def daily_prob_closes(self, _market_id):
        return self.probs

    async def update_peak(self, position_id, peak):
        self.peaks.append((position_id, peak))


def test_live_position_without_an_exit_remains_open_before_resolution():
    now = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)
    engine = StrategyEngine(_exit_policy())
    store = _ExitStore(bars=[{
        "ts": now.replace(hour=0), "open": 101.0, "high": 104.5,
        "low": 99.0, "close": 103.0,
    }], probs=[(now.date(), 0.8)])

    exits = asyncio.run(engine.scan_exits(store, [_position(now)], now=now))

    assert exits == []
    assert store.peaks == [(7, pytest.approx(0.045))]


def test_live_resolution_is_actionable_even_when_entry_cannot_be_replayed():
    now = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)
    engine = StrategyEngine(_exit_policy())
    store = _ExitStore()
    position = _position(
        now, position_id=8, t_e=now + timedelta(hours=12),
    )

    exits = asyncio.run(engine.scan_exits(store, [position], now=now))

    assert len(exits) == 1
    assert exits[0].reason == "resolution-1d"


def test_live_probability_exit_uses_actual_position_not_reconstructed_entry():
    now = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)
    engine = StrategyEngine(_exit_policy())
    store = _ExitStore(
        bars=[{
            "ts": now.replace(hour=0), "open": 101.0, "high": 102.0,
            "low": 99.0, "close": 101.0,
        }],
        probs=[(now.date(), 0.40)],
    )

    exits = asyncio.run(engine.scan_exits(store, [_position(now)], now=now))

    assert len(exits) == 1
    assert exits[0].reason == "probability-out"


def test_live_probability_exit_applies_bearish_signal_polarity(monkeypatch):
    now = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "live.strategy_engine.resolve_polarity", lambda _question, _symbol: (-1, "test"),
    )
    engine = StrategyEngine(_exit_policy())
    store = _ExitStore(
        bars=[{
            "ts": now.replace(hour=0), "open": 101.0, "high": 102.0,
            "low": 99.0, "close": 101.0,
        }],
        probs=[(now.date(), 0.90)],
    )

    exits = asyncio.run(engine.scan_exits(store, [_position(now)], now=now))

    assert len(exits) == 1
    assert exits[0].reason == "probability-out"


def test_current_daily_bar_is_not_treated_as_a_close_during_market_hours():
    now = datetime(2026, 7, 31, 19, 30, tzinfo=timezone.utc)  # 15:30 New York
    prior = {"ts": datetime(2026, 7, 30, tzinfo=timezone.utc)}
    current = {"ts": datetime(2026, 7, 31, tzinfo=timezone.utc)}

    historical, completed, incomplete = _partition_session_rows(
        [prior, current], "1d", now,
    )

    assert historical == [prior]
    assert completed == []
    assert incomplete == [current["ts"]]


def test_current_daily_bar_becomes_replaceable_after_the_close():
    now = datetime(2026, 7, 31, 20, 30, tzinfo=timezone.utc)  # 16:30 New York
    current = {"ts": datetime(2026, 7, 31, tzinfo=timezone.utc)}

    historical, completed, incomplete = _partition_session_rows(
        [current], "1d", now,
    )

    assert historical == []
    assert completed == [current]
    assert incomplete == []


def test_multiple_exits_refresh_ib_cash_between_orders():
    class Engine:
        async def scan_exits(self, _store, _positions):
            return [
                type("Signal", (), {"position_id": 1, "reason": "resolution-1d"})(),
                type("Signal", (), {"position_id": 2, "reason": "resolution-1d"})(),
            ]

    class Orders:
        execution_anomaly = False

        def __init__(self):
            self.cash_seen = []

        async def exit_position(self, _pos, _reason, _benchmark_price, *, cash):
            self.cash_seen.append(cash)
            return True

    class Positions:
        def __init__(self):
            self.calls = 0

        async def snapshot(self):
            self.calls += 1
            return {
                "valid": True,
                "cash": 250.0 if self.calls == 1 else 500.0,
                "benchmark_price": 100.0,
            }

    async def run():
        pipeline = ControlPipeline(LiveConfig())
        pipeline.store = object()  # type: ignore[assignment]
        orders = Orders()
        positions = Positions()
        await pipeline.run_exits(
            Engine(), orders, positions,
            {
                "cash": -4_694.51,
                "benchmark_price": 100.0,
                "open_positions": [
                    {"position_id": 1},
                    {"position_id": 2},
                ],
            },
        )
        return orders, positions

    orders, positions = asyncio.run(run())

    assert orders.cash_seen == [-4_694.51, 250.0]
    assert positions.calls == 2
