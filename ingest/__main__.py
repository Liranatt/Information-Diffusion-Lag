"""Ingestion CLI.

    python -m ingest --rebuild                 # rebuild parquet + pkl from the DB
                                               #   (free; the nightly job runs this)
    python -m ingest --backfill                # historical scan->...->download
    python -m ingest --backfill --since 2024-07-01 --until 2026-05-27
    python -m ingest --live                    # one live discovery pass (test)

--backfill and --live make paid Gemini calls (catalyst gate + asset mapping);
--rebuild is free (DB reads + file writes only).
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

import httpx


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


async def _run_live() -> None:
    from database.db_connection import connect
    from database.backtesting.schema import SCHEMA
    from ingest.chain import discover_and_clean

    conn = await connect()
    try:
        rows = await conn.fetch(f"SELECT market_id FROM {SCHEMA}.live_tracked_markets")
    finally:
        await conn.close()
    known = {r["market_id"] for r in rows}

    async with httpx.AsyncClient(timeout=httpx.Timeout(60)) as client:
        passed = await discover_and_clean(client, known=known)
    print(f"discover_and_clean: {len(passed)} markets passed")
    for m in passed[:20]:
        print(f"  {m['market_id'][:14]}  {m['question'][:80]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="CEM ingestion pipeline")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--rebuild", action="store_true",
                      help="rebuild candidates.parquet + prices.pkl + probs.pkl from the DB (free)")
    mode.add_argument("--backfill", action="store_true",
                      help="historical scan -> clean -> download (paid: Gemini)")
    mode.add_argument("--live", action="store_true",
                      help="one live discovery pass (paid: Gemini)")
    parser.add_argument("--since", type=_dt, help="backfill window start (YYYY-MM-DD)")
    parser.add_argument("--until", type=_dt, help="backfill window end (YYYY-MM-DD)")
    args = parser.parse_args()

    if args.rebuild:
        from ingest.artifacts import rebuild
        asyncio.run(rebuild())
    elif args.backfill:
        from ingest.chain import backfill, SCAN_START, SCAN_END
        asyncio.run(backfill(args.since or SCAN_START, args.until or SCAN_END))
    else:
        asyncio.run(_run_live())


if __name__ == "__main__":
    main()
