"""Refresh the Postgres cache from IB without running strategy logic or orders."""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json

from live.config import CONFIG
from live.connection import IBConnection
from live.database import LiveStore
from live.position_manager import PositionManager


async def run(client_id: int) -> int:
    cfg = dataclasses.replace(CONFIG, ib_client_id=client_id)
    store = await LiveStore.create()
    ib_conn = IBConnection(cfg)
    try:
        snapshot = await PositionManager(cfg, ib_conn, store).snapshot()
        if not snapshot["valid"]:
            raise RuntimeError("IB did not return a complete account snapshot")
        observed_at = snapshot["observed_at"]
        await store.cache_broker_state(
            observed_at=observed_at,
            account_summary=snapshot["account_summary"],
            positions=snapshot["broker_positions"],
        )
        await store.mark_runtime_event(
            "broker_sync",
            observed_at,
            {
                "source": "IB",
                "position_count": len(snapshot["broker_positions"]),
                "metadata_gaps": snapshot.get("metadata_gaps", []),
                "manual_cache_refresh": True,
            },
        )
        fills = await ib_conn.execution_snapshot()
        await store.record_execution_fills(fills)

        baseline = None
        passive = None
        cumulative_flows = 0.0
        if (
            snapshot.get("benchmark_price")
            and str(snapshot.get("benchmark_source") or "").startswith("IB_")
        ):
            baseline = await store.bootstrap_performance_baseline(
                benchmark=cfg.benchmark,
                current_nav=float(snapshot["equity"]),
                current_benchmark_price=float(snapshot["benchmark_price"]),
                observed_at=observed_at,
            )
            passive, baseline, cumulative_flows = await store.current_passive_equity(
                benchmark_price=float(snapshot["benchmark_price"]),
                observed_at=observed_at,
            )
        await store.snapshot_equity(
            equity=float(snapshot["equity"]),
            cash=float(snapshot["cash"]),
            benchmark_shares=float(snapshot["benchmark_shares"]),
            benchmark_price=snapshot.get("benchmark_price"),
            open_positions=int(snapshot.get("broker_position_count", 0)),
            passive_equity=passive,
            source="IB_same_snapshot_v2",
            observed_at=observed_at,
            benchmark_observed_at=observed_at,
            baseline_key=baseline.get("baseline_key") if baseline else None,
            baseline_verified=bool(baseline.get("verified")) if baseline else None,
            cumulative_cash_flows=cumulative_flows,
        )

        db_qty = {
            str(pos["symbol"]): float(pos["qty"])
            for pos in snapshot.get("db_open_positions", [])
        }
        ib_qty = {
            str(symbol): float(qty)
            for symbol, qty in snapshot.get("ib_positions", {}).items()
            if symbol != cfg.benchmark
        }
        for symbol in sorted(set(db_qty) | set(ib_qty)):
            if abs(ib_qty.get(symbol, 0.0) - db_qty.get(symbol, 0.0)) <= 1e-6:
                continue
            await store.record_reconciliation_event(
                observed_at=observed_at,
                severity="warning",
                event_type="position_quantity_gap",
                symbol=symbol,
                broker_value=ib_qty.get(symbol, 0.0),
                database_value=db_qty.get(symbol, 0.0),
                details={"action": "warning_only", "source_of_truth": "IB"},
            )
        print(
            json.dumps(
                {
                    "source": "IB",
                    "observed_at": observed_at.isoformat(),
                    "net_liquidation": snapshot["equity"],
                    "cash": snapshot["cash"],
                    "positions": snapshot["ib_positions"],
                    "execution_fills_synced": len(fills),
                    "passive_equity": passive,
                    "baseline_source": baseline.get("source") if baseline else None,
                    "baseline_verified": bool(baseline.get("verified")) if baseline else None,
                    "metadata_gaps": snapshot.get("metadata_gaps", []),
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        await ib_conn.disconnect()
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy current IB state into the DB cache")
    parser.add_argument("--client-id", type=int, default=98)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.client_id)))


if __name__ == "__main__":
    main()
