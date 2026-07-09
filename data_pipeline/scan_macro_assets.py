"""Run asset mapping + prob/price downloads for macro catalysts.

Reads catalyst_results_macro.json, filters to CATALYST+positive_sentiment,
then runs steps 2c/3/4 from scan_historical.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scan_historical import step2c_asset_mapping, step3_download_probs, step4_download_prices

ROOT = Path(__file__).resolve().parent
CACHE_PATH = ROOT / "data" / "markets_cache_historical.json"
MACRO_RESULTS_PATH = ROOT / "data" / "catalyst_results_macro.json"


async def main():
    markets = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    by_id = {m["market_id"]: m for m in markets}

    macro = json.loads(MACRO_RESULTS_PATH.read_text(encoding="utf-8"))
    positive = [r for r in macro if r["verdict"] == "CATALYST" and r["positive_sentiment"]]
    print(f"Macro positive catalysts: {len(positive)}")

    catalyst_markets = []
    for r in positive:
        m = by_id.get(r["market_id"])
        if m:
            catalyst_markets.append(m)
    print(f"Matched to cache: {len(catalyst_markets)}")

    passed = await step2c_asset_mapping(catalyst_markets)
    await step3_download_probs(passed)
    await step4_download_prices(passed)

    print(f"\n=== MACRO PIPELINE DONE ===")
    print(f"  Positive catalysts: {len(positive)}")
    print(f"  With mapped assets: {len(passed)}")


if __name__ == "__main__":
    asyncio.run(main())
