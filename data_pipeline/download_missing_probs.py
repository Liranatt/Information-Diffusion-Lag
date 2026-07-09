"""Download prob + price data for ALL markets missing coverage across both prompt versions."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from database.db_connection import connect
from database.backtesting.schema import SCHEMA
from scan_historical import step3_download_probs, step4_download_prices

ROOT = Path(__file__).resolve().parent
PROMPT_VERSIONS = ["claude-pipeline-v1", "pipeline-v1"]


async def main():
    conn = await connect()
    try:
        missing = await conn.fetch(f"""
            SELECT DISTINCT w.market_id, w.prompt_version
            FROM {SCHEMA}.historical_asset_worlds w
            JOIN {SCHEMA}.historical_asset_world_assets a ON a.world_id = w.world_id
            WHERE w.prompt_version = ANY($1::text[])
            AND w.market_id NOT IN (
                SELECT market_id FROM {SCHEMA}.historical_probability_coverage
            )
        """, PROMPT_VERSIONS)
    finally:
        await conn.close()

    missing_ids = {r["market_id"] for r in missing}
    by_pv = {}
    for r in missing:
        by_pv.setdefault(r["prompt_version"], set()).add(r["market_id"])

    print(f"Markets missing prob data: {len(missing_ids)}")
    for pv, ids in by_pv.items():
        print(f"  {pv}: {len(ids)}")

    if not missing_ids:
        print("Nothing to download!")
        return

    # Load both caches
    market_meta = {}
    for cache_file in ["markets_cache.json", "markets_cache_historical.json"]:
        p = ROOT / "data" / cache_file
        if p.exists():
            for m in json.loads(p.read_text(encoding="utf-8")):
                market_meta.setdefault(m["market_id"], m)

    markets = [market_meta[mid] for mid in missing_ids if mid in market_meta]
    not_in_cache = missing_ids - set(market_meta.keys())
    print(f"Matched to cache: {len(markets)}")
    if not_in_cache:
        print(f"NOT in cache (cannot download): {len(not_in_cache)}")

    if not markets:
        print("Nothing to download!")
        return

    await step3_download_probs(markets)
    await step4_download_prices(markets)
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
