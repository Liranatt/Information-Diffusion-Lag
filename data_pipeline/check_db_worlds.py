"""Check what's in the DB across all prompt versions."""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from database.db_connection import connect
from database.backtesting.schema import SCHEMA

async def main():
    conn = await connect()

    pvs = ["pipeline-v1", "claude-pipeline-v1",
           "historical-pass-world-v6-gemini-one-call-local-completion",
           "historical-pass-world-v9-reasoning-no-ticker-leakage",
           "historical-pass-world-v7-gemini-two-pass-tight-mapping",
           "historical-pass-world-v5-gemini-one-call"]

    for pv in pvs:
        rows = await conn.fetch(
            f"SELECT DISTINCT universe_name FROM {SCHEMA}.historical_asset_worlds WHERE prompt_version = $1 LIMIT 15", pv
        )
        names = [r["universe_name"] for r in rows]
        print(f"\n{pv} ({len(names)} sample universes):")
        for n in names:
            print(f"  {n[:100]}")

    # Check macro questions specifically
    print("\n\n=== Fed/tariff/Iran questions by prompt_version ===")
    all_worlds = await conn.fetch(
        f"SELECT w.market_id, w.universe_name, w.prompt_version FROM {SCHEMA}.historical_asset_worlds w"
    )
    macro_kw = ["fed", "fomc", "rate cut", "tariff", "iran", "hormuz", "inflation", "cpi"]
    for pv in pvs:
        pv_worlds = [w for w in all_worlds if w["prompt_version"] == pv]
        macro = [w for w in pv_worlds if any(kw in w["universe_name"].lower() for kw in macro_kw)]
        if macro:
            print(f"\n{pv}: {len(macro)} macro worlds")
            for m in macro[:5]:
                print(f"  {m['universe_name'][:100]}")

    # Check prob coverage for pipeline-v1
    pv1_mids = [w["market_id"] for w in all_worlds if w["prompt_version"] == "pipeline-v1"]
    covered = await conn.fetch(
        f"SELECT DISTINCT market_id FROM {SCHEMA}.historical_probability_coverage WHERE market_id = ANY($1::text[])",
        pv1_mids
    )
    print(f"\n\npipeline-v1: {len(set(pv1_mids))} markets, {len(covered)} have prob coverage")

    await conn.close()

asyncio.run(main())
