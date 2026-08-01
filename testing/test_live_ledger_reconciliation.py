from __future__ import annotations

import asyncio
from datetime import datetime, timezone
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

class _PositionIB:
    async def account_summary(self):
        return {"TotalCashValue": 99_999.0, "NetLiquidation": 123_456.0}

    async def portfolio_snapshot(self):
        return {
            "SPY": {
                "qty": 7.0, "market_price": 100.0, "market_value": 700.0,
                "avg_cost": 90.0, "unrealized_pnl": 70.0, "realized_pnl": 0.0,
            },
            "AAPL": {
                "qty": 2.0, "market_price": 10.0, "market_value": 20.0,
                "avg_cost": 9.0, "unrealized_pnl": 2.0, "realized_pnl": 0.0,
            },
            "UNTY": {
                "qty": 3.0, "market_price": 20.0, "market_value": 60.0,
                "avg_cost": 18.0, "unrealized_pnl": 6.0, "realized_pnl": 0.0,
            },
        }


def test_snapshot_uses_ib_for_cash_nav_quantities_prices_and_costs():
    async def run():
        manager = PositionManager(LiveConfig(), _PositionIB(), _PositionStore())
        return await manager.snapshot()

    snapshot = asyncio.run(run())

    assert snapshot["cash"] == pytest.approx(99_999.0)
    assert snapshot["benchmark_shares"] == pytest.approx(7.0)
    assert snapshot["ib_benchmark_shares"] == pytest.approx(7.0)
    assert snapshot["benchmark_price"] == pytest.approx(100.0)
    assert snapshot["equity"] == pytest.approx(123_456.0)
    assert snapshot["broker_positions"]["UNTY"]["avg_cost"] == pytest.approx(18.0)
    assert snapshot["broker_position_count"] == 2
    assert snapshot["metadata_gaps"] == ["UNTY"]
    assert snapshot["open_positions"][0]["qty"] == 2
    assert snapshot["benchmark_source"] == "IB_portfolio"
    assert snapshot["source"] == "IB"
    assert snapshot["trade_safe"] is True


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
    assert len(store.orders) == 1
    assert store.orders[0] == {
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
            "perm_id": None,
            "order_ref": store.orders[0]["order_ref"],
            "operation_id": store.orders[0]["operation_id"],
        }
    assert store.orders[0]["order_ref"].startswith("CEM:")


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


def test_actual_ib_fill_updates_tick_local_cash_projection():
    async def run():
        broker = _Broker()
        store = _OrderStore()
        manager = OrderManager(
            LiveConfig(order_timeout_seconds=0), _OrderIB(broker), store
        )
        manager.seed_broker_state({"cash": 1_000.0, "ib_positions": {"SPY": 0.0}})
        price = await manager._execute("SPY", "BUY", 1.0, kind="cash_sweep")
        return manager, price

    manager, price = asyncio.run(run())

    assert price == pytest.approx(100.0)
    assert manager.projected_cash == pytest.approx(900.0)
    assert manager.projected_positions["SPY"] == pytest.approx(1.0)


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


def test_execution_audit_copies_fill_price_quantity_and_commission_from_ib():
    execution = SimpleNamespace(
        execId="exec-123",
        shares=3.0,
        price=101.25,
        time=datetime(2026, 7, 31, 13, 30, tzinfo=timezone.utc),
        exchange="NYSE",
    )
    report = SimpleNamespace(execId="exec-123", commission=0.35, realizedPNL=12.5)
    trade = SimpleNamespace(
        order=SimpleNamespace(orderId=456),
        fills=[SimpleNamespace(execution=execution, commissionReport=report)],
    )

    rows = OrderManager._execution_rows(trade, symbol="AAPL", action="SELL")

    assert rows == [{
        "exec_id": "exec-123",
        "ib_order_id": 456,
        "symbol": "AAPL",
        "action": "SELL",
        "exec_ts": execution.time,
        "shares": 3.0,
        "price": 101.25,
        "exchange": "NYSE",
        "commission": 0.35,
        "realized_pnl": 12.5,
        "perm_id": None,
        "account": None,
        "order_ref": None,
        "kind": None,
        "position_id": None,
        "operation_id": None,
    }]
    assert OrderManager._fill_commission(trade) == pytest.approx(0.35)


class _PartialExitStore:
    def __init__(self):
        self.closed = None

    async def latest_close(self, _symbol: str):
        return 58.80

    async def position_exit_fill_summary(self, _position_id: int):
        return {
            "qty": 318.0,
            "avg_price": (230.0 * 58.75 + 88.0 * 58.79) / 318.0,
            "commission": 1.473898 + 1.123999,
        }

    async def close_position(self, position_id: int, **kwargs):
        self.closed = {"position_id": position_id, **kwargs}


class _PartialExitIB:
    async def portfolio_positions(self):
        return {"UNTY": 88.0}


class _CompletingPartialExitManager(OrderManager):
    async def _execute(self, _symbol: str, _action: str, _qty: float, **_kwargs):
        self.last_commission = 1.123999
        return 58.79


def test_completed_partial_exit_aggregates_all_prior_fill_slices_for_pnl():
    async def run():
        store = _PartialExitStore()
        manager = _CompletingPartialExitManager(
            LiveConfig(dry_run=True), _PartialExitIB(), store
        )
        manager.seed_broker_state({
            "cash": 0.0,
            "ib_positions": {"UNTY": 88.0},
        })
        closed = await manager.exit_position(
            {
                "position_id": 64,
                "symbol": "UNTY",
                "entry_price": 56.71356225,
                "entry_costs": 0.0,
            },
            "resolution-1d",
            None,
        )
        return store, closed

    store, closed = asyncio.run(run())

    assert closed is True
    assert store.closed["position_id"] == 64
    assert store.closed["exit_price"] == pytest.approx(58.7610691824)
    assert store.closed["exit_costs"] == pytest.approx(2.597897)
    assert store.closed["pnl"] == pytest.approx(648.51)
    assert store.closed["pnl_pct"] == pytest.approx(3.5959)
    assert store.closed["pnl_source"] == "IB_execution_aggregate"
