"""Reconcile IB inventory to the strategy ledger without changing strategy P&L.

Inspection is the default.  ``--execute`` is deliberately restricted to the
paper account and regular NYSE hours; ``--wait-until-open`` can be used by a
one-shot scheduled process.  Broker-only corrections are audited in
``live_broker_reconciliations`` and never enter ``live_orders``.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
from datetime import datetime, timezone

try:
    from ib_async import MarketOrder
except ImportError:  # pragma: no cover
    from ib_insync import MarketOrder  # type: ignore[no-redef]

from live.config import CONFIG, LiveConfig
from live.connection import IBConnection
from live.database import LiveStore
from live.position_manager import PositionManager
from live.utils import is_market_hours, market_session_status


def _commission(trade) -> float | None:
    values = []
    for fill in getattr(trade, "fills", []) or []:
        report = getattr(fill, "commissionReport", None)
        value = getattr(report, "commission", None) if report else None
        if value is not None:
            values.append(float(value))
    return sum(values) if values else None


async def _expected_positions(store: LiveStore, cfg: LiveConfig) -> dict[str, float]:
    expected: dict[str, float] = {}
    for pos in await store.open_positions():
        symbol = str(pos["symbol"])
        expected[symbol] = expected.get(symbol, 0.0) + float(pos["qty"])
    expected[cfg.benchmark] = await store.reconciled_position_qty(cfg.benchmark)
    return expected


def _plan(expected: dict[str, float], actual: dict[str, float]) -> list[dict]:
    plan = []
    for symbol in sorted(set(expected) | set(actual)):
        expected_qty = float(expected.get(symbol, 0.0))
        actual_qty = float(actual.get(symbol, 0.0))
        delta = actual_qty - expected_qty
        if abs(delta) <= 1e-6:
            continue
        plan.append(
            {
                "symbol": symbol,
                "expected_qty": expected_qty,
                "ib_qty": actual_qty,
                "action": "SELL" if delta > 0 else "BUY",
                "qty": abs(delta),
            }
        )
    return plan


async def _execute_item(
    item: dict,
    *,
    cfg: LiveConfig,
    ib_conn: IBConnection,
    store: LiveStore,
) -> None:
    symbol = str(item["symbol"])
    action = str(item["action"])
    qty = float(item["qty"])
    contract = await ib_conn.qualified_stock(symbol)
    if contract is None:
        await store.record_broker_reconciliation(
            ib_order_id=None,
            symbol=symbol,
            expected_qty=float(item["expected_qty"]),
            ib_qty_before=float(item["ib_qty"]),
            action=action,
            requested_qty=qty,
            filled_qty=0.0,
            fill_price=None,
            commission=None,
            status="Unqualified",
            note="IB-to-ledger inventory reconciliation",
        )
        raise RuntimeError(f"IB could not qualify {symbol}")

    ib = await ib_conn.ensure_connected()
    order = MarketOrder(action, qty)
    order.orderRef = "CEM_LEDGER_RECONCILE"
    trade = ib.placeOrder(contract, order)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + cfg.order_timeout_seconds
    while not trade.isDone() and loop.time() < deadline:
        await asyncio.sleep(0.5)
    if not trade.isDone():
        ib.cancelOrder(trade.order)
        cancel_deadline = loop.time() + 5.0
        while not trade.isDone() and loop.time() < cancel_deadline:
            await asyncio.sleep(0.25)

    # Commission reports can arrive just after orderStatus becomes Filled.
    await asyncio.sleep(1.0)
    status = str(trade.orderStatus.status or "Unknown")
    filled_qty = float(trade.orderStatus.filled or 0.0)
    remaining_qty = float(trade.orderStatus.remaining or 0.0)
    fill_price = float(trade.orderStatus.avgFillPrice or 0.0) or None
    complete = (
        fill_price is not None
        and filled_qty >= qty - 1e-6
        and remaining_qty <= 1e-6
    )
    recorded_status = "Filled" if complete else status
    await store.record_broker_reconciliation(
        ib_order_id=int(trade.order.orderId),
        symbol=symbol,
        expected_qty=float(item["expected_qty"]),
        ib_qty_before=float(item["ib_qty"]),
        action=action,
        requested_qty=qty,
        filled_qty=filled_qty,
        fill_price=fill_price,
        commission=_commission(trade),
        status=recorded_status,
        note="IB-to-ledger inventory reconciliation; excluded from strategy P&L",
    )
    if not complete:
        raise RuntimeError(
            f"incomplete reconciliation for {symbol}: status={status} "
            f"filled={filled_qty:g} remaining={remaining_qty:g}"
        )


async def run(args: argparse.Namespace) -> int:
    if args.execute and not is_market_hours():
        if not args.wait_until_open:
            raise RuntimeError(
                "regular US equity session is closed; use --wait-until-open"
            )
        print(json.dumps({"status": "waiting_for_market_open", **market_session_status()}))
        while not is_market_hours():
            await asyncio.sleep(30.0)

    cfg = dataclasses.replace(CONFIG, ib_client_id=args.client_id)
    store = await LiveStore.create()
    ib_conn = IBConnection(cfg)
    try:
        ib = await ib_conn.ensure_connected()
        open_orders = await ib.reqAllOpenOrdersAsync()
        if open_orders:
            raise RuntimeError(
                f"refusing reconciliation while {len(open_orders)} IB orders are open"
            )

        expected = await _expected_positions(store, cfg)
        actual = await ib_conn.portfolio_positions()
        plan = _plan(expected, actual)
        print(
            json.dumps(
                {
                    "status": "plan",
                    "market": market_session_status(),
                    "orders": plan,
                },
                sort_keys=True,
            )
        )
        if not args.execute:
            return 0

        buys = [item for item in plan if item["action"] == "BUY"]
        if buys and not args.allow_buys:
            symbols = ", ".join(str(item["symbol"]) for item in buys)
            raise RuntimeError(
                f"reconciliation would open/increase positions ({symbols}); "
                "--allow-buys is required"
            )

        for item in plan:
            # Re-read before every order.  A manual/broker change between the
            # plan and execution therefore changes the order instead of racing it.
            current = await ib_conn.portfolio_positions()
            refreshed = _plan(expected, current)
            live_item = next(
                (row for row in refreshed if row["symbol"] == item["symbol"]),
                None,
            )
            if live_item is None:
                continue
            if live_item["action"] == "BUY" and not args.allow_buys:
                raise RuntimeError(
                    f"live delta for {item['symbol']} changed to BUY; refusing"
                )
            await _execute_item(
                live_item,
                cfg=cfg,
                ib_conn=ib_conn,
                store=store,
            )

        snapshot = await PositionManager(cfg, ib_conn, store).snapshot()
        drift = list(snapshot.get("broker_drift") or [])
        await store.mark_runtime_event(
            "broker_drift",
            datetime.now(timezone.utc),
            {
                "items": drift,
                "ledger_benchmark_shares": snapshot.get("benchmark_shares"),
                "ib_benchmark_shares": snapshot.get("ib_benchmark_shares"),
                "reconciliation": True,
            },
        )
        print(
            json.dumps(
                {
                    "status": "complete" if not drift else "incomplete",
                    "remaining_drift": drift,
                    "ib_positions": snapshot.get("ib_positions"),
                },
                sort_keys=True,
            )
        )
        return 0 if not drift else 2
    finally:
        await ib_conn.disconnect()
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile IB paper inventory to the strategy ledger"
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--wait-until-open", action="store_true")
    parser.add_argument("--allow-buys", action="store_true")
    parser.add_argument("--client-id", type=int, default=98)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
