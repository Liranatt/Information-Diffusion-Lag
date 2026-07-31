"""One-way, idempotent cleanup of legacy live bookkeeping.

This script never connects to IB and never places an order.  It labels early
legacy resolution exits, links old orders to their strategy position where the
relationship is unambiguous, and replaces modeled costs with actual recorded
IB commissions only when every required commission is present.
"""
from __future__ import annotations

import asyncio
import json

from database.backtesting.schema import SCHEMA
from live.database import LiveStore


async def run() -> dict:
    store = await LiveStore.create()
    summary = {
        "early_resolution_labels": 0,
        "verified_pnl_rows": 0,
        "unverified_pnl_rows": 0,
        "linked_entry_orders": 0,
    }
    try:
        async with store.pool.acquire() as conn:
            async with conn.transaction():
                result = await conn.execute(
                    f"""UPDATE {SCHEMA}.live_positions
                        SET exit_reason='legacy_premature_resolution-1d'
                        WHERE status='closed'
                          AND exit_reason='resolution-1d'
                          AND exit_ts < t_e - INTERVAL '1 day'"""
                )
                summary["early_resolution_labels"] = int(result.split()[-1])

                positions = await conn.fetch(
                    f"""SELECT * FROM {SCHEMA}.live_positions
                        WHERE status='closed' AND exit_price IS NOT NULL
                        ORDER BY position_id"""
                )
                for pos in positions:
                    entry = await conn.fetchrow(
                        f"""SELECT * FROM {SCHEMA}.live_orders
                            WHERE symbol=$1 AND action='BUY' AND kind='entry'
                              AND note=$2
                              AND ABS(EXTRACT(EPOCH FROM (ts-$3))) <= 60
                            ORDER BY ABS(EXTRACT(EPOCH FROM (ts-$3)))
                            LIMIT 1""",
                        pos["symbol"], pos["question"], pos["entry_ts"],
                    )
                    fund = None
                    if float(pos["benchmark_sell_qty"] or 0.0) > 1e-9:
                        fund = await conn.fetchrow(
                            f"""SELECT * FROM {SCHEMA}.live_orders
                                WHERE action='SELL' AND kind='rotation_fund'
                                  AND note=$1
                                  AND ts <= $2
                                  AND $2-ts <= INTERVAL '60 seconds'
                                ORDER BY ts DESC LIMIT 1""",
                            f"fund {pos['symbol']}", pos["entry_ts"],
                        )
                    exit_order = await conn.fetchrow(
                        f"""SELECT * FROM {SCHEMA}.live_orders
                            WHERE position_id=$1 AND kind='exit'
                              AND fill_price IS NOT NULL AND qty > 0
                            ORDER BY ts LIMIT 1""",
                        pos["position_id"],
                    )
                    rebuys = await conn.fetch(
                        f"""SELECT * FROM {SCHEMA}.live_orders
                            WHERE position_id=$1 AND kind='rotation_rebuy'
                              AND fill_price IS NOT NULL AND qty > 0
                            ORDER BY ts""",
                        pos["position_id"],
                    )

                    entry_operation = f"legacy:{pos['position_id']}:entry"
                    exit_operation = f"legacy:{pos['position_id']}:exit"
                    if entry:
                        await conn.execute(
                            f"""UPDATE {SCHEMA}.live_orders
                                SET position_id=COALESCE(position_id,$2),
                                    operation_id=COALESCE(operation_id,$3)
                                WHERE order_id=$1""",
                            entry["order_id"], pos["position_id"], entry_operation,
                        )
                        summary["linked_entry_orders"] += 1
                    if fund:
                        await conn.execute(
                            f"""UPDATE {SCHEMA}.live_orders
                                SET position_id=COALESCE(position_id,$2),
                                    operation_id=COALESCE(operation_id,$3)
                                WHERE order_id=$1""",
                            fund["order_id"], pos["position_id"], entry_operation,
                        )
                    await conn.execute(
                        f"""UPDATE {SCHEMA}.live_orders
                            SET operation_id=COALESCE(operation_id,$2)
                            WHERE position_id=$1 AND kind IN ('exit','rotation_rebuy')""",
                        pos["position_id"], exit_operation,
                    )

                    required = [entry, exit_order]
                    if float(pos["benchmark_sell_qty"] or 0.0) > 1e-9:
                        required.append(fund)
                    required.extend(rebuys)
                    complete = all(
                        order is not None and order["commission"] is not None
                        for order in required
                    )
                    if not complete:
                        await conn.execute(
                            f"""UPDATE {SCHEMA}.live_positions
                                SET pnl_source='IB_commission_incomplete',
                                    pnl_verified=FALSE,
                                    operation_id=COALESCE(operation_id,$2)
                                WHERE position_id=$1""",
                            pos["position_id"], entry_operation,
                        )
                        summary["unverified_pnl_rows"] += 1
                        continue

                    entry_costs = float(entry["commission"])
                    if fund:
                        entry_costs += float(fund["commission"])
                    exit_costs = float(exit_order["commission"]) + sum(
                        float(order["commission"]) for order in rebuys
                    )
                    gross = float(pos["qty"]) * (
                        float(pos["exit_price"]) - float(pos["entry_price"])
                    )
                    pnl = gross - entry_costs - exit_costs
                    exposure = max(float(pos["qty"]) * float(pos["entry_price"]), 1e-12)
                    await conn.execute(
                        f"""UPDATE {SCHEMA}.live_positions
                            SET entry_costs=$2, exit_costs=$3, pnl=$4, pnl_pct=$5,
                                pnl_source='IB_recorded_commissions', pnl_verified=TRUE,
                                operation_id=COALESCE(operation_id,$6)
                            WHERE position_id=$1""",
                        pos["position_id"], entry_costs, exit_costs, round(pnl, 2),
                        round(pnl / exposure * 100.0, 4), entry_operation,
                    )
                    summary["verified_pnl_rows"] += 1
        return summary
    finally:
        await store.close()


def main() -> None:
    print(json.dumps(asyncio.run(run()), sort_keys=True))


if __name__ == "__main__":
    main()
