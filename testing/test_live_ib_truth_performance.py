from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from live.performance import excess_performance, passive_equity, passive_units
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


def test_live_resolution_reason_is_ignored_until_one_day_before_resolution(monkeypatch):
    now = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)
    engine = StrategyEngine({})
    engine._candidate = AsyncMock(return_value=({}, {}, {}, [], None))
    monkeypatch.setattr(
        "live.strategy_engine._simulate_one_py",
        lambda *_args, **_kwargs: {
            "exit_reason": "resolution-1d",
            "peak_pct": 4.5,
        },
    )

    class Store:
        def __init__(self):
            self.peaks = []

        async def update_peak(self, position_id, peak):
            self.peaks.append((position_id, peak))

    store = Store()
    positions = [{
        "position_id": 7,
        "market_id": "m1",
        "symbol": "AAPL",
        "qty": 3,
        "question": "q",
        "is_earnings": False,
        "entry_ts": now - timedelta(days=2),
        "t_e": now + timedelta(days=10),
        "peak_ret": 0.0,
    }]

    exits = asyncio.run(engine.scan_exits(store, positions, now=now))

    assert exits == []
    assert store.peaks == [(7, pytest.approx(0.045))]


def test_live_resolution_reason_is_actionable_inside_resolution_window(monkeypatch):
    now = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)
    engine = StrategyEngine({})
    engine._candidate = AsyncMock(return_value=({}, {}, {}, [], None))
    monkeypatch.setattr(
        "live.strategy_engine._simulate_one_py",
        lambda *_args, **_kwargs: {"exit_reason": "resolution-1d", "peak_pct": 0.0},
    )

    class Store:
        async def update_peak(self, _position_id, _peak):
            raise AssertionError("zero peak should not update")

    positions = [{
        "position_id": 8,
        "market_id": "m2",
        "symbol": "MSFT",
        "qty": 2,
        "question": "q",
        "is_earnings": False,
        "entry_ts": now - timedelta(days=2),
        "t_e": now + timedelta(hours=12),
        "peak_ret": 0.0,
    }]

    exits = asyncio.run(engine.scan_exits(Store(), positions, now=now))

    assert len(exits) == 1
    assert exits[0].reason == "resolution-1d"
