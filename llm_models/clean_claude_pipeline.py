"""Clean claude-pipeline-v1: delete price brackets, send ambiguous questions to Gemini.

1. Delete price-bracket worlds from DB
2. Keep earnings as-is
3. Send remaining (~116) through Gemini CATALYST/NOISE filter
4. Delete NOISE results from DB
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from database.db_connection import connect
from database.backtesting.schema import SCHEMA
from LLM.gemini_client import GeminiClient
from LLM.build_world import CatalystBatch, GEMINI_CATALYST_PROMPT

ROOT = Path(__file__).resolve().parent
BATCH_SIZE = 50

PRICE_BRACKET_RE = re.compile(
    r'hit\s*\(?(HIGH|LOW)\)?'
    r'|finish\s+week.*above'
    r'|finish\s+week.*below'
    r'|close\s+above'
    r'|close\s+below'
    r'|hit\s+\$\d'
    r'|above\s+\$\d'
    r'|below\s+\$\d'
    r'|all\s+time\s+high'
    r'|weekly\s+(high|low)'
    r'|\$\d+.*week\s+of',
    re.IGNORECASE,
)

EARNINGS_RE = re.compile(
    r'beat\s+quarterly\s+earnings|beat\s+its\s+quarterly\s+eps',
    re.IGNORECASE,
)


async def main():
    conn = await connect()

    # Get all claude-pipeline-v1 worlds
    worlds = await conn.fetch(f"""
        SELECT w.world_id, w.market_id, w.universe_name
        FROM {SCHEMA}.historical_asset_worlds w
        WHERE w.prompt_version = 'claude-pipeline-v1'
    """)
    print(f"claude-pipeline-v1 worlds: {len(worlds)}")

    # Load caches for question text
    cache = json.loads((ROOT / "data" / "markets_cache_historical.json").read_text(encoding="utf-8"))
    cache2 = json.loads((ROOT / "data" / "markets_cache.json").read_text(encoding="utf-8"))
    by_id = {m["market_id"]: m for m in cache}
    for m in cache2:
        if m["market_id"] not in by_id:
            by_id[m["market_id"]] = m

    # Classify
    price_bracket_ids = []
    earnings_ids = []
    needs_gemini = []

    for w in worlds:
        mid = w["market_id"]
        m = by_id.get(mid, {})
        q = m.get("question", "")

        if EARNINGS_RE.search(q):
            earnings_ids.append(mid)
        elif PRICE_BRACKET_RE.search(q):
            price_bracket_ids.append(w["world_id"])
        else:
            needs_gemini.append((mid, m, w["world_id"]))

    # Dedupe
    price_bracket_world_ids = list(set(price_bracket_ids))
    earnings_market_ids = list(set(earnings_ids))
    gemini_items = {}
    for mid, m, wid in needs_gemini:
        if mid not in gemini_items:
            gemini_items[mid] = {"market": m, "world_ids": []}
        gemini_items[mid]["world_ids"].append(wid)

    print(f"\n=== Classification ===")
    print(f"  Earnings (keep):          {len(earnings_market_ids)}")
    print(f"  Price brackets (delete):  {len(price_bracket_world_ids)} worlds")
    print(f"  Needs Gemini:             {len(gemini_items)} markets")

    # Step 1: Delete price bracket worlds
    print(f"\nDeleting {len(price_bracket_world_ids)} price bracket worlds...")
    for i in range(0, len(price_bracket_world_ids), 500):
        batch = price_bracket_world_ids[i:i+500]
        await conn.execute(
            f"DELETE FROM {SCHEMA}.historical_asset_world_assets WHERE world_id = ANY($1::uuid[])",
            batch,
        )
        await conn.execute(
            f"DELETE FROM {SCHEMA}.historical_asset_worlds WHERE world_id = ANY($1::uuid[])",
            batch,
        )
    print("  Done.")

    # Step 2: Send remaining to Gemini
    gemini_list = list(gemini_items.items())
    total_batches = (len(gemini_list) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"\nSending {len(gemini_list)} questions to Gemini ({total_batches} API calls)...")

    gemini = GeminiClient()
    results: dict[str, dict] = {}

    for i in range(0, len(gemini_list), BATCH_SIZE):
        batch = gemini_list[i:i+BATCH_SIZE]
        lines = []
        for j, (mid, info) in enumerate(batch, 1):
            m = info["market"]
            created = m.get("created_at", "")[:10]
            lines.append(f'{j}. [{created}] "{m.get("question", "")}"')
        payload_text = "\n".join(lines)

        try:
            response = await gemini.structured(
                system_prompt=GEMINI_CATALYST_PROMPT,
                payload={"questions": payload_text},
                response_model=CatalystBatch,
                max_tokens=BATCH_SIZE * 40 + 500,
            )
            verdict_map = {c.id: c for c in response.classifications}
            for j, (mid, info) in enumerate(batch, 1):
                c = verdict_map.get(j)
                results[mid] = {
                    "verdict": c.verdict if c else "UNKNOWN",
                    "positive_sentiment": c.positive_sentiment if c else True,
                    "reason": c.reason if c else "Missing",
                    "world_ids": info["world_ids"],
                }
        except Exception as e:
            print(f"  Batch failed: {e}")
            for mid, info in batch:
                results[mid] = {
                    "verdict": "ERROR",
                    "positive_sentiment": True,
                    "reason": str(e)[:200],
                    "world_ids": info["world_ids"],
                }

        batch_num = i // BATCH_SIZE + 1
        print(f"  [{batch_num}/{total_batches}] done", flush=True)

    await gemini.close()

    # Step 3: Delete NOISE worlds
    catalyst_count = 0
    noise_count = 0
    noise_world_ids = []

    for mid, r in results.items():
        if r["verdict"] == "CATALYST" and r["positive_sentiment"]:
            catalyst_count += 1
        else:
            noise_count += 1
            noise_world_ids.extend(r["world_ids"])

    print(f"\n=== Gemini results ===")
    print(f"  CATALYST (positive): {catalyst_count}")
    print(f"  NOISE/negative:      {noise_count}")

    # Show what Gemini kept
    print(f"\n  Catalysts kept:")
    for mid, r in results.items():
        if r["verdict"] == "CATALYST" and r["positive_sentiment"]:
            m = by_id.get(mid, {})
            print(f"    {m.get('question', '???')[:120]}")
            print(f"      {r['reason'][:140]}")

    print(f"\n  Sample NOISE (first 15):")
    shown = 0
    for mid, r in results.items():
        if r["verdict"] != "CATALYST" or not r["positive_sentiment"]:
            m = by_id.get(mid, {})
            print(f"    [{r['verdict']}] {m.get('question', '???')[:110]}")
            shown += 1
            if shown >= 15:
                break

    print(f"\nDeleting {len(noise_world_ids)} noise worlds...")
    for i in range(0, len(noise_world_ids), 500):
        batch = noise_world_ids[i:i+500]
        await conn.execute(
            f"DELETE FROM {SCHEMA}.historical_asset_world_assets WHERE world_id = ANY($1::uuid[])",
            batch,
        )
        await conn.execute(
            f"DELETE FROM {SCHEMA}.historical_asset_worlds WHERE world_id = ANY($1::uuid[])",
            batch,
        )
    print("  Done.")

    # Final count
    remaining = await conn.fetchval(f"""
        SELECT COUNT(DISTINCT w.market_id)
        FROM {SCHEMA}.historical_asset_worlds w
        WHERE w.prompt_version = 'claude-pipeline-v1'
    """)
    print(f"\n=== claude-pipeline-v1 remaining: {remaining} markets ===")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
