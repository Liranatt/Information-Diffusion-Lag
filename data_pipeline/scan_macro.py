"""Send the ~2,725 macro/geo questions that the regex killed straight to Gemini catalyst filter.

These are Fed, CPI, jobs, tariff, GDP, Iran, FDA, IPO etc. questions that scored
below 0.6 in the regex pipeline or had negative sentiment. We bypass the regex
and let Gemini decide CATALYST vs NOISE.

Results are appended to the same catalyst_results.json so the main pipeline picks them up.
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

from LLM.gemini_client import GeminiClient
from LLM.build_world import CatalystBatch, GEMINI_CATALYST_PROMPT
from run_claude_pipeline import evaluate_pass1
from LLM.build_world import QUESTION_RELEVANCE_FLOOR

ROOT = Path(__file__).resolve().parent
CACHE_PATH = ROOT / "data" / "markets_cache_historical.json"
CATALYST_RESULTS_PATH = ROOT / "data" / "catalyst_results_macro.json"
BATCH_SIZE = 50

MACRO_PATTERN = re.compile(
    r'\bfed\b|\bfomc\b|rate\s+cut|rate\s+hike|interest\s+rate'
    r'|\bcpi\b|inflation|\bjobs?\b|payroll|nonfarm|unemployment'
    r'|\bgdp\b|\bpmi\b|\bism\b|retail\s+sales|tariff'
    r'|trade\s+war|consumer\s+sentiment|durable\s+goods'
    r'|housing\s+starts|building\s+permits|industrial\s+production'
    r'|trade\s+balance|\bboj\b|\becb\b|\bboe\b'
    r'|bank\s+of\s+(canada|japan|england)|sanctions?'
    r'|ceasefire|peace\s+deal|strait\s+of\s+hormuz'
    r'|\biran\b|strike\s+iran|invade|invasion'
    r'|\bipos?\b|fda\s+approv|antitrust|merger'
    r'|acquisition|executive\s+order|government\s+shutdown'
    r'|debt\s+ceiling|deficit',
    re.IGNORECASE,
)


async def main():
    markets = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    print(f"Loaded {len(markets)} markets from cache")

    # Find macro questions that the regex killed
    macro_killed = []
    for m in markets:
        if not MACRO_PATTERN.search(m.get("question", "")):
            continue
        p1 = evaluate_pass1(m)
        if p1.question_relevance >= QUESTION_RELEVANCE_FLOOR and p1.positive_sentiment:
            continue  # already passed regex, already in the main run
        macro_killed.append(m)

    print(f"Macro/geo questions killed by regex: {len(macro_killed)}")

    # Load existing results for resumability
    existing: dict[str, dict] = {}
    if CATALYST_RESULTS_PATH.exists():
        for r in json.loads(CATALYST_RESULTS_PATH.read_text(encoding="utf-8")):
            existing[r["market_id"]] = r
        print(f"Resuming: {len(existing)} already classified")

    remaining = [m for m in macro_killed if m["market_id"] not in existing]
    total_batches = (len(remaining) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"Remaining to classify: {len(remaining)} → {total_batches} API calls")

    if not remaining:
        print("Nothing to do!")
        _print_summary(existing, macro_killed)
        return

    gemini = GeminiClient()
    done_batches = 0
    failed_batches = 0
    t0 = time.time()

    for i in range(0, len(remaining), BATCH_SIZE):
        batch = remaining[i : i + BATCH_SIZE]
        lines = []
        for j, m in enumerate(batch, 1):
            created = m["created_at"][:10]
            lines.append(f'{j}. [{created}] "{m["question"]}"')
        payload_text = "\n".join(lines)

        try:
            response = await gemini.structured(
                system_prompt=GEMINI_CATALYST_PROMPT,
                payload={"questions": payload_text},
                response_model=CatalystBatch,
                max_tokens=BATCH_SIZE * 40 + 500,
            )
            verdict_map = {c.id: c for c in response.classifications}
            for j, m in enumerate(batch, 1):
                c = verdict_map.get(j)
                existing[m["market_id"]] = {
                    "market_id": m["market_id"],
                    "verdict": c.verdict if c else "UNKNOWN",
                    "positive_sentiment": c.positive_sentiment if c else True,
                    "reason": c.reason if c else "Missing from response",
                }
        except Exception as e:
            failed_batches += 1
            print(f"  BATCH {done_batches+1} FAILED: {e}")
            for m in batch:
                existing[m["market_id"]] = {
                    "market_id": m["market_id"],
                    "verdict": "ERROR",
                    "positive_sentiment": True,
                    "reason": str(e)[:200],
                }

        done_batches += 1
        if done_batches % 10 == 0 or done_batches == total_batches:
            n_cat = sum(1 for mid in [m["market_id"] for m in macro_killed]
                        if existing.get(mid, {}).get("verdict") == "CATALYST")
            n_noise = sum(1 for mid in [m["market_id"] for m in macro_killed]
                          if existing.get(mid, {}).get("verdict") == "NOISE")
            elapsed = time.time() - t0
            print(f"  [{done_batches}/{total_batches}] "
                  f"CATALYST={n_cat} NOISE={n_noise} failed={failed_batches} "
                  f"({elapsed:.0f}s)", flush=True)

        if done_batches % 20 == 0:
            CATALYST_RESULTS_PATH.write_text(
                json.dumps(list(existing.values()), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    await gemini.close()

    # Save all results
    CATALYST_RESULTS_PATH.write_text(
        json.dumps(list(existing.values()), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    _print_summary(existing, macro_killed)


def _print_summary(existing: dict, macro_killed: list):
    macro_ids = {m["market_id"] for m in macro_killed}
    macro_results = [existing[mid] for mid in macro_ids if mid in existing]

    catalysts = [r for r in macro_results if r["verdict"] == "CATALYST"]
    noise = [r for r in macro_results if r["verdict"] == "NOISE"]
    pos = [r for r in catalysts if r["positive_sentiment"]]
    neg = [r for r in catalysts if not r["positive_sentiment"]]

    print(f"\n=== MACRO SUPPLEMENT RESULTS ===")
    print(f"  Total macro classified: {len(macro_results)}")
    print(f"  CATALYST: {len(catalysts)} (positive: {len(pos)}, negative: {len(neg)})")
    print(f"  NOISE: {len(noise)}")

    # Load cache for question text
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    by_id = {m["market_id"]: m for m in cache}

    print(f"\n  === 30 sample MACRO CATALYSTS ===")
    for r in catalysts[:30]:
        m = by_id.get(r["market_id"], {})
        sent = "+" if r["positive_sentiment"] else "-"
        print(f"  [{sent}] {m.get('question', '???')[:100]}")
        print(f"      {r['reason'][:120]}")


if __name__ == "__main__":
    asyncio.run(main())
