from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from live.config import LiveConfig
from live.order_manager import OrderManager
from live.utils import benchmark_sell_qty_for_cash_deficit, ib_cost


def _modeled_sell_proceeds(qty: float, price: float) -> float:
    return qty * price - ib_cost(qty, price, True)


def test_whole_share_deficit_sell_covers_negative_cash():
    price = 751.34
    qty = benchmark_sell_qty_for_cash_deficit(
        -19_228.0,
        price,
        78.0,
        fractional=False,
        min_notional=200.0,
        buffer_pct=0.01,
    )

    assert qty == pytest.approx(26.0)
    assert _modeled_sell_proceeds(qty, price * 0.99) >= 19_228.0


def test_fractional_deficit_sell_uses_minimum_notional_but_caps_to_holdings():
    price = 100.0
    qty = benchmark_sell_qty_for_cash_deficit(
        -20.0,
        price,
        1.5,
        fractional=True,
        min_notional=200.0,
        buffer_pct=0.0,
    )

    assert qty == pytest.approx(1.5)


class _FakeOrderManager(OrderManager):
    def __init__(self, cfg: LiveConfig) -> None:
        super().__init__(cfg, ib_conn=None, store=None)  # type: ignore[arg-type]
        self.executed: list[dict] = []

    async def _execute(self, symbol: str, action: str, qty: float, **kwargs) -> float | None:
        self.executed.append(
            {"symbol": symbol, "action": action, "qty": qty, **kwargs}
        )
        return float(kwargs.get("reference_price") or 100.0)


def test_restore_no_margin_sells_spy_gap_before_new_buys():
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
        assert restored is True
        return manager

    manager = asyncio.run(run())

    assert manager.executed == [
        {
            "symbol": "SPY",
            "action": "SELL",
            "qty": pytest.approx(26.0),
            "kind": "margin_rebalance",
            "note": "pre-entry: cover negative reconciled cash",
            "reference_price": 751.34,
        }
    ]


def test_entry_refuses_to_open_when_cash_is_still_negative():
    async def run() -> dict | None:
        manager = _FakeOrderManager(LiveConfig())
        signal = SimpleNamespace(symbol="UNTY")
        return await manager.enter_position(
            signal,
            desired_allocation=1_000.0,
            benchmark_price=100.0,
            cash=-1.0,
            benchmark_shares=10.0,
            position_size_pct=0.1,
        )

    assert asyncio.run(run()) is None
