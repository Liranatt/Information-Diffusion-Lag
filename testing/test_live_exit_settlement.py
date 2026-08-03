"""Regression tests for the exit path that silently stopped the live trader.

On 2026-08-03 every hourly tick sold one position at IB and then raised inside
``close_position``'s audit UPDATE.  The exception aborted the whole tick, so the
DB row stayed ``open`` while IB was already flat, and no entry, sweep or NAV
snapshot ever ran again.  Two invariants keep that from recurring:

  1. a bookkeeping failure after an executed IB sell must not abort the tick;
  2. a DB row left open after IB is flat must be settled from IB's own fills.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from live.config import LiveConfig
from live.control_pipeline import ControlPipeline


def _pipeline(store) -> ControlPipeline:
    pipeline = ControlPipeline(LiveConfig(dry_run=True))
    pipeline.store = store
    return pipeline


class _SettlementStore:
    def __init__(self, summaries: dict[int, dict | None]):
        self.summaries = summaries
        self.closed: list[dict] = []

    async def position_exit_fill_summary(self, position_id: int):
        return self.summaries.get(position_id)

    async def close_position(self, position_id: int, **kwargs):
        self.closed.append({"position_id": position_id, **kwargs})


_OBSERVED_AT = datetime(2026, 8, 3, 17, 32, tzinfo=timezone.utc)
_EXIT_TS = datetime(2026, 8, 3, 13, 37, 51, tzinfo=timezone.utc)


def _snapshot(db_open, ib_positions):
    return {
        "observed_at": _OBSERVED_AT,
        "db_open_positions": db_open,
        "ib_positions": ib_positions,
    }


def test_position_flat_at_ib_is_settled_from_the_recorded_exit_fills():
    store = _SettlementStore({
        26: {
            "qty": 47.0,
            "avg_price": 246.55,
            "commission": 1.248016,
            "rebuy_commission": 1.000045,
            "last_exit_ts": _EXIT_TS,
            "exit_reason": "probability-out",
        },
    })
    snapshot = _snapshot(
        [{
            "position_id": 26, "symbol": "HON", "qty": 47,
            "entry_price": 222.65, "entry_costs": 10.70369256,
        }],
        {"SPY": 260.0},
    )

    settled = asyncio.run(_pipeline(store).settle_broker_closed_positions(snapshot))

    assert settled == 1
    closed = store.closed[0]
    assert closed["position_id"] == 26
    assert closed["exit_price"] == pytest.approx(246.55)
    assert closed["exit_ts"] == _EXIT_TS
    assert closed["exit_reason"] == "probability-out"
    # 47 * (246.55 - 222.65) - 10.70369256 - (1.248016 + 1.000045)
    assert closed["pnl"] == pytest.approx(1110.35, abs=0.01)
    assert closed["pnl_source"] == "IB_execution_aggregate_reconciled"


def test_position_still_held_at_ib_is_never_settled():
    store = _SettlementStore({})
    snapshot = _snapshot(
        [{
            "position_id": 60, "symbol": "AAPL", "qty": 32,
            "entry_price": 342.12, "entry_costs": 5.0,
        }],
        {"AAPL": 32.0, "SPY": 260.0},
    )

    settled = asyncio.run(_pipeline(store).settle_broker_closed_positions(snapshot))

    assert settled == 0
    assert store.closed == []


def test_flat_position_without_an_executed_exit_is_left_for_review():
    """No recorded sell means no fill to book -- never fabricate one."""
    store = _SettlementStore({99: None})
    snapshot = _snapshot(
        [{
            "position_id": 99, "symbol": "XYZ", "qty": 10,
            "entry_price": 10.0, "entry_costs": 1.0,
        }],
        {"SPY": 260.0},
    )

    settled = asyncio.run(_pipeline(store).settle_broker_closed_positions(snapshot))

    assert settled == 0
    assert store.closed == []


class _ExitScanEngine:
    def __init__(self, signals):
        self.signals = signals

    async def scan_exits(self, _store, _positions):
        return self.signals


class _RaisingOrders:
    """The IB sell filled; the bookkeeping that follows it fails."""

    def __init__(self):
        self.execution_anomaly = False
        self.calls = 0

    async def exit_position(self, *_args, **_kwargs):
        self.calls += 1
        raise RuntimeError("audit UPDATE failed after the IB sell filled")


class _StaticPositions:
    async def snapshot(self):  # pragma: no cover - not reached in this test
        raise AssertionError("no dependent snapshot after a failed exit")


def test_bookkeeping_failure_after_an_ib_fill_does_not_abort_the_tick():
    from live.strategy_engine import ExitSignal

    orders = _RaisingOrders()
    signals = [
        ExitSignal(position_id=26, symbol="HON", qty=47, reason="probability-out"),
        ExitSignal(position_id=34, symbol="INFY", qty=894, reason="probability-out"),
    ]
    snapshot = {
        "open_positions": [
            {"position_id": 26, "symbol": "HON", "entry_price": 222.65,
             "entry_costs": 0.0},
            {"position_id": 34, "symbol": "INFY", "entry_price": 11.55,
             "entry_costs": 0.0},
        ],
        "benchmark_price": 750.0,
        "cash": 0.0,
    }

    pipeline = _pipeline(_SettlementStore({}))
    asyncio.run(
        pipeline.run_exits(
            _ExitScanEngine(signals), orders, _StaticPositions(), snapshot,
        )
    )

    # The tick survives, the exit chain stops, and the anomaly flag blocks the
    # entries/sweeps that would otherwise size against unreconciled inventory.
    assert orders.calls == 1
    assert orders.execution_anomaly is True
