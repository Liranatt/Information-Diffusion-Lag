"""Recover missing strategy metadata for holdings that IB says are real.

This is read-only at IB and never sends orders.  Run without ``--apply`` to
print the plan; use ``--apply`` only after reviewing the historical matches.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json

from live.config import CONFIG
from live.connection import IBConnection
from live.database import LiveStore
from live.position_manager import PositionManager


async def run(client_id: int, apply: bool) -> list[dict]:
    cfg = dataclasses.replace(CONFIG, ib_client_id=client_id)
    store = await LiveStore.create()
    ib_conn = IBConnection(cfg)
    try:
        snapshot = await PositionManager(cfg, ib_conn, store).snapshot()
        if not snapshot["valid"]:
            raise RuntimeError("IB did not return a complete account snapshot")
        results = await store.recover_broker_metadata_from_history(
            symbols=list(snapshot.get("metadata_gaps") or []),
            broker_positions=snapshot["broker_positions"],
            observed_at=snapshot["observed_at"],
            apply=apply,
        )
        print(json.dumps({
            "apply": apply,
            "source_of_truth": "IB",
            "orders_sent": 0,
            "results": results,
        }, default=str, sort_keys=True))
        return results
    finally:
        await ib_conn.disconnect()
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id", type=int, default=97)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.client_id, args.apply))


if __name__ == "__main__":
    main()
