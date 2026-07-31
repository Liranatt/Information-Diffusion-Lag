from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from live import control_pipeline as control_module
from live.config import LiveConfig
from live.control_pipeline import ControlPipeline
from live.order_manager import OrderManager


class _FakeOrderManager(OrderManager):
    def __init__(self, cfg: LiveConfig) -> None:
        super().__init__(cfg, ib_conn=None, store=None)  # type: ignore[arg-type]
        self.executed: list[dict] = []

    async def _execute(self, symbol: str, action: str, qty: float, **kwargs) -> float | None:
        self.executed.append(
            {"symbol": symbol, "action": action, "qty": qty, **kwargs}
        )
        price = float(kwargs.get("reference_price") or 100.0)
        self._apply_fill_projection(
            symbol=symbol,
            action=action,
            qty=qty,
            price=price,
            commission=0.0,
        )
        return price

    async def confirm_broker_cash(self) -> bool:
        return True


def test_restore_no_margin_sells_only_minimum_spy_needed():
    async def run() -> _FakeOrderManager:
        manager = _FakeOrderManager(
            LiveConfig(
                benchmark="SPY",
                fractional_benchmark=False,
                min_order_notional=200.0,
                execution_buffer_pct=0.01,
            )
        )
        manager.seed_broker_state({
            "cash": -3_918.53,
            "ib_positions": {"SPY": 212.0},
        })
        sold = await manager.restore_no_margin_from_benchmark(
            cash=-3_918.53,
            benchmark_price=746.04,
            benchmark_shares=212.0,
            reason="post-exits",
        )
        return manager, sold

    manager, sold = asyncio.run(run())

    assert sold == pytest.approx(6.0)
    assert manager.executed == [{
        "symbol": "SPY",
        "action": "SELL",
        "qty": 6.0,
        "kind": "cash_rebalance",
        "note": "cover negative IB cash (post-exits)",
        "reference_price": 746.04,
    }]
    assert manager.projected_cash == pytest.approx(557.71)
    assert manager.projected_positions["SPY"] == pytest.approx(206.0)


def test_restore_no_margin_stops_tick_when_spy_cannot_cover_deficit():
    async def run() -> _FakeOrderManager:
        manager = _FakeOrderManager(LiveConfig(benchmark="SPY"))
        manager.seed_broker_state({
            "cash": -5_000.0,
            "ib_positions": {"SPY": 1.0},
        })
        sold = await manager.restore_no_margin_from_benchmark(
            cash=-5_000.0,
            benchmark_price=100.0,
            benchmark_shares=1.0,
            reason="post-exits",
        )
        return manager, sold

    manager, sold = asyncio.run(run())

    assert sold is None
    assert manager.execution_anomaly is True
    assert manager.executed[0]["qty"] == pytest.approx(1.0)


def test_entry_with_margin_sells_only_the_amount_needed_for_the_strategy_entry():
    class Store:
        def __init__(self):
            self.position = None

        async def latest_close(self, _symbol):
            return 50.0

        async def insert_position(self, position):
            self.position = position
            return 1

    async def run():
        store = Store()
        manager = _FakeOrderManager(LiveConfig())
        manager.store = store
        signal = SimpleNamespace(
            symbol="UNTY", market_id="m1", question="question",
            is_earnings=False, prob=0.8, atr_pct=0.02,
            t_e=SimpleNamespace(),
        )
        position = await manager.enter_position(
            signal,
            desired_allocation=1_000.0,
            benchmark_price=100.0,
            cash=-100.0,
            benchmark_shares=20.0,
            position_size_pct=0.1,
        )
        return manager, position

    manager, position = asyncio.run(run())

    assert position is not None
    assert manager.executed[0]["symbol"] == "SPY"
    assert manager.executed[0]["action"] == "SELL"
    assert manager.executed[0]["qty"] == pytest.approx(11.0)
    assert manager.executed[0]["kind"] == "rotation_fund"
    assert manager.executed[1]["symbol"] == "UNTY"
    assert manager.executed[1]["action"] == "BUY"


def test_entry_does_not_sell_benchmark_when_inventory_cannot_cover_margin_and_buy():
    class Store:
        async def latest_close(self, _symbol):
            return 50.0

    async def run():
        manager = _FakeOrderManager(LiveConfig())
        manager.store = Store()
        signal = SimpleNamespace(symbol="UNTY")
        result = await manager.enter_position(
            signal,
            desired_allocation=1_000.0,
            benchmark_price=100.0,
            cash=-5_000.0,
            benchmark_shares=40.0,
            position_size_pct=0.1,
        )
        return manager, result

    manager, result = asyncio.run(run())

    assert result is None
    assert manager.executed == []


def test_cash_sweep_uses_fill_projected_cash_when_ib_summary_is_stale():
    async def run() -> _FakeOrderManager:
        manager = _FakeOrderManager(
            LiveConfig(
                benchmark="SPY",
                fractional_benchmark=False,
                min_order_notional=200.0,
                execution_buffer_pct=0.01,
            )
        )
        manager.seed_broker_state({
            "cash": 9_511.55,
            "ib_positions": {"SPY": 194.0},
        })
        await manager.sweep_idle_cash(
            cash=13_989.0,  # stale IB summary from before a just-filled SPY buy
            benchmark_price=746.06,
        )
        return manager

    manager = asyncio.run(run())

    assert manager.executed[0]["symbol"] == "SPY"
    assert manager.executed[0]["action"] == "BUY"
    assert manager.executed[0]["qty"] == pytest.approx(12.0)


def test_final_hour_sweep_stops_when_cash_cannot_buy_one_whole_spy(monkeypatch):
    class Orders:
        execution_anomaly = False

        def __init__(self):
            self.calls = 0

        async def sweep_idle_cash(self, **_kwargs):
            self.calls += 1
            return 0.0

    class Positions:
        async def snapshot(self):
            raise AssertionError("no resnapshot should be needed")

    monkeypatch.setattr(control_module, "seconds_to_market_close", lambda: 2_000.0)
    pipeline = ControlPipeline(LiveConfig(
        benchmark="SPY",
        fractional_benchmark=False,
        min_order_notional=200.0,
        close_sweep_buffer_pct=0.03,
    ))
    orders = Orders()
    snapshot = {
        "cash": 565.20,
        "benchmark_price": 747.47,
        "trade_safe": True,
    }

    result = asyncio.run(pipeline.final_hour_cash_sweep(
        orders, Positions(), snapshot,
    ))

    assert result is snapshot
    assert orders.calls == 0
