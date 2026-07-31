from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from live.config import LiveConfig
from live.order_manager import OrderManager
from live.position_manager import PositionManager


class _PositionStore:
    async def open_positions(self):
        return [{"symbol": "AAPL", "qty": 2, "entry_price": 9.0}]

    async def latest_close(self, symbol: str):
        return {"SPY": 100.0, "AAPL": 10.0}[symbol]

    async def reconciled_cash(self):
        return 1_000.0

    async def reconciled_position_qty(self, symbol: str):
        assert symbol == "SPY"
        return 5.0


class _PositionIB:
    async def account_summary(self):
        return {"TotalCashValue": 99_999.0, "NetLiquidation": 123_456.0}

    async def portfolio_positions(self):
        return {"SPY": 7.0, "AAPL": 2.0, "UNTY": 3.0}


def test_snapshot_values_benchmark_from_same_ledger_as_cash_and_blocks_drift():
    async def run():
        manager = PositionManager(LiveConfig(), _PositionIB(), _PositionStore())
        return await manager.snapshot()

    snapshot = asyncio.run(run())

    assert snapshot["cash"] == pytest.approx(1_000.0)
    assert snapshot["benchmark_shares"] == pytest.approx(5.0)
    assert snapshot["ib_benchmark_shares"] == pytest.approx(7.0)
    assert snapshot["equity"] == pytest.approx(1_520.0)
    assert snapshot["trade_safe"] is False
    assert "SPY: ledger=5 ib=7" in snapshot["broker_drift"]
    assert "UNTY: ledger=0 ib=3" in snapshot["broker_drift"]


class _OrderStore:
    def __init__(self):
        self.orders: list[dict] = []

    async def record_order(self, **kwargs):
        self.orders.append(kwargs)


class _Trade:
    def __init__(self):
        self.order = SimpleNamespace(orderId=123)
        self.orderStatus = SimpleNamespace(
            status="Submitted", avgFillPrice=0.0, filled=0.0, remaining=1.0
        )
        self.fills = []

    def isDone(self):
        return self.orderStatus.status in {"Filled", "Cancelled"}


class _Broker:
    def __init__(self, *, partial: bool = False):
        self.trade = _Trade()
        self.partial = partial

    def placeOrder(self, _contract, _order):
        return self.trade

    def cancelOrder(self, _order):
        if self.partial:
            self.trade.orderStatus = SimpleNamespace(
                status="Cancelled", avgFillPrice=100.0, filled=0.5, remaining=0.5
            )
        else:
            self.trade.orderStatus = SimpleNamespace(
                status="Filled", avgFillPrice=100.0, filled=1.0, remaining=0.0
            )


class _OrderIB:
    def __init__(self, broker: _Broker):
        self.broker = broker

    async def ensure_connected(self):
        return self.broker

    async def qualified_stock(self, _symbol: str):
        return object()


def test_cancel_race_records_final_fill_instead_of_stale_submitted_state():
    async def run():
        broker = _Broker()
        store = _OrderStore()
        manager = OrderManager(
            LiveConfig(order_timeout_seconds=0), _OrderIB(broker), store
        )
        price = await manager._execute("SPY", "BUY", 1.0, kind="cash_sweep")
        return manager, store, price

    manager, store, price = asyncio.run(run())

    assert price == pytest.approx(100.0)
    assert manager.execution_anomaly is False
    assert store.orders == [
        {
            "ib_order_id": 123,
            "symbol": "SPY",
            "action": "BUY",
            "qty": 1.0,
            "requested_qty": 1.0,
            "kind": "cash_sweep",
            "fill_price": 100.0,
            "status": "Filled",
            "position_id": None,
            "note": "",
            "commission": None,
            "reference_price": None,
        }
    ]


def test_partial_fill_records_only_executed_quantity_and_stops_order_chain():
    async def run():
        broker = _Broker(partial=True)
        store = _OrderStore()
        manager = OrderManager(
            LiveConfig(order_timeout_seconds=0), _OrderIB(broker), store
        )
        price = await manager._execute("SPY", "BUY", 1.0, kind="cash_sweep")
        return manager, store, price

    manager, store, price = asyncio.run(run())

    assert price is None
    assert manager.execution_anomaly is True
    assert store.orders[0]["qty"] == pytest.approx(0.5)
    assert store.orders[0]["requested_qty"] == pytest.approx(1.0)
    assert store.orders[0]["status"] == "PartiallyFilled"


class _EntryStore:
    async def latest_close(self, _symbol: str):
        return 50.0


class _UncertainEntryManager(OrderManager):
    def __init__(self):
        super().__init__(LiveConfig(), None, _EntryStore())  # type: ignore[arg-type]
        self.calls: list[tuple[str, str]] = []

    async def _execute(self, symbol: str, action: str, _qty: float, **_kwargs):
        self.calls.append((symbol, action))
        if len(self.calls) == 1:
            return 100.0  # benchmark funding sale filled
        self.execution_anomaly = True
        return None  # asset buy outcome is uncertain


def test_uncertain_asset_fill_does_not_send_compensating_spy_order():
    async def run():
        manager = _UncertainEntryManager()
        signal = SimpleNamespace(
            symbol="AAPL", market_id="m1", question="q", is_earnings=False,
            prob=0.8, atr_pct=0.02, t_e=SimpleNamespace(),
        )
        result = await manager.enter_position(
            signal,
            desired_allocation=1_000.0,
            benchmark_price=100.0,
            cash=0.0,
            benchmark_shares=10.0,
            position_size_pct=0.1,
        )
        return manager, result

    manager, result = asyncio.run(run())

    assert result is None
    assert manager.calls == [("SPY", "SELL"), ("AAPL", "BUY")]
