from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from live.config import LiveConfig
from live.order_manager import OrderManager


class _FakeOrderManager(OrderManager):
    def __init__(self, cfg: LiveConfig) -> None:
        super().__init__(cfg, ib_conn=None, store=None)  # type: ignore[arg-type]
        self.executed: list[dict] = []

    async def _execute(self, symbol: str, action: str, qty: float, **kwargs) -> float | None:
        self.executed.append(
            {"symbol": symbol, "action": action, "qty": qty, **kwargs}
        )
        return float(kwargs.get("reference_price") or 100.0)


def test_restore_no_margin_is_warning_only_and_never_sells_spy():
    async def run() -> _FakeOrderManager:
        manager = _FakeOrderManager(
            LiveConfig(
                benchmark="SPY",
                fractional_benchmark=False,
                min_order_notional=200.0,
                execution_buffer_pct=0.01,
            )
        )
        restored = await manager.restore_no_margin_from_benchmark(
            cash=-19_228.0,
            benchmark_price=751.34,
            benchmark_shares=78.0,
            reason="pre-entry",
        )
        assert restored is False
        return manager

    manager = asyncio.run(run())

    assert manager.executed == []


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
