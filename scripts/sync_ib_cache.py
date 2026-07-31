"""Refresh the Postgres cache from IB without running strategy logic or orders."""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
from datetime import datetime, timezone

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
        observed_at = datetime.now(timezone.utc)
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
        print(
            json.dumps(
                {
                    "source": "IB",
                    "observed_at": observed_at.isoformat(),
                    "net_liquidation": snapshot["equity"],
                    "cash": snapshot["cash"],
                    "positions": snapshot["ib_positions"],
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
