"""Label the signal polarity of each (question, symbol) pair.

    python -m LLM.label_polarity --dry-run    # exact call count + cost, spends nothing
    python -m LLM.label_polarity              # run
    python -m LLM.label_polarity --compare    # agreement vs the regex in pipeline.strategy

Polarity answers exactly one question: *if this market resolves YES, is that
bullish or bearish for a LONG position in this symbol?*

This is deliberately NOT the question `LLM/build_world.py` asks. That prompt
judges the tone of the event and explicitly forbids reasoning about asset
direction ("Do not predict the outcome or trading direction"), which is why no
per-candidate sign has ever existed. Polarity is a property of the (question,
symbol) PAIR, not of the question alone: "Will OPEC crude oil production be
above 18 million barrels per day in May?" is bearish for USO (more supply, lower
crude) while an identical sentence about a company's own output would be bullish
for that company.

Results cache to data/polarity_labels.json keyed by sha256(question|symbol|
PROMPT_VERSION), so re-runs are free and a prompt change invalidates cleanly.
The Postgres cache used by label_questions.py is not used here -- that DB is only
reachable from the home network.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import os

# Directional judgement on a one-sentence question. Cheap model, no thinking
# budget -- same call as label_questions.py, which cost $1.99 for 5,063 rows.
os.environ.setdefault("GEMINI_MODEL", "gemini-2.5-flash")
os.environ.setdefault("GEMINI_THINKING_LEVEL", "none")

from LLM.gemini_client import GeminiClient  # noqa: E402
from pipeline.strategy import explain_polarity  # noqa: E402

PROMPT_VERSION = "polarity-v1"
BATCH_SIZE = 20
CACHE_PATH = ROOT / "data" / "polarity_labels.json"
CANDIDATES = ROOT / "data" / "candidates.parquet"

# gemini-2.5-flash list price, USD per 1M tokens. Used only for the dry-run quote.
PRICE_IN_PER_M = 0.30
PRICE_OUT_PER_M = 2.50

SYSTEM_PROMPT = """You judge the SIGNAL POLARITY of prediction-market questions for a long-only equity strategy.

For each (question, symbol) pair, answer exactly one thing:

  If this market resolves YES, is that BULLISH or BEARISH for a LONG position in that symbol?

Return polarity = 1 for bullish, polarity = -1 for bearish.

Reason about the causal chain from the event to the asset's price. Think about what
the symbol actually is -- an oil ETF (USO, BNO), an energy sector fund (XLE), a
shipping company (ZIM), a short-duration treasury fund (SHY), or an individual
equity -- and what YES would do to it.

Rules and traps:

- NEGATION AND CESSATION FLIP THE SIGN. "Will the US not strike Iran?" resolving YES
  means peace, which is bearish for crude. "Military action against Iran ends by
  April 10?" resolving YES means the war is over: bearish for USO. "East coast port
  strike ends in October?" resolving YES means shipping normalizes: bearish for ZIM.
- A RISING LEVEL IS NOT ALWAYS BULLISH. It depends entirely on the subject.
  "Annual inflation above 2.5%?" YES is bearish. "Robinhood Gold Subscribers above
  4.2M?" YES is bullish. "Will OPEC crude oil production be above 18 million barrels
  per day?" YES means more supply, so it is BEARISH for USO.
- COMPANY NAMES CONTAIN MISLEADING WORDS. "Five Below (FIVE)" contains "below".
  "Tractor Supply (TSCO)" contains "supply". "CrowdStrike (CRWD)" contains "strike".
  Ignore the name; judge the event.
- Judge the EVENT'S EFFECT ON THAT SYMBOL, not whether YES is likely, and not
  whether the event is morally good or bad.

confidence is your own 0-1 certainty in the sign. Use < 0.7 when the causal chain
is genuinely ambiguous, or when the symbol's exposure to the event is unclear.

reason must be one short clause naming the causal link, e.g. "war ends -> crude
risk premium falls -> USO down".

Return one label object per input id. Do not invent ids. Output MINIFIED JSON on a
single line -- no indentation, no line breaks. The response must be complete."""


class PolarityLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    polarity: Literal[-1, 1]
    confidence: float
    reason: str


class PolarityLabelBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    labels: list[PolarityLabel] = Field(max_length=BATCH_SIZE)


def pair_hash(question: str, symbol: str) -> str:
    key = f"{question.strip().lower()}|{symbol.strip().upper()}|{PROMPT_VERSION}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def gather_pairs() -> list[tuple[str, str]]:
    df = pd.read_parquet(CANDIDATES)
    pairs = (
        df[["question", "symbol"]]
        .dropna()
        .astype(str)
        .drop_duplicates()
        .sort_values(["question", "symbol"])
    )
    return [(q, s) for q, s in pairs.itertuples(index=False, name=None) if len(q) >= 8]


def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def quote(todo: list[tuple[str, str]]) -> None:
    n_batches = (len(todo) + BATCH_SIZE - 1) // BATCH_SIZE
    sys_tok = len(SYSTEM_PROMPT) // 4
    payload_tok = sum(len(q) + len(s) for q, s in todo) // 4 + 12 * len(todo)
    in_tok = n_batches * sys_tok + payload_tok
    out_tok = len(todo) * 45
    cost = in_tok / 1e6 * PRICE_IN_PER_M + out_tok / 1e6 * PRICE_OUT_PER_M
    print("\n" + "=" * 72)
    print("  PAID RUN QUOTE  (nothing is sent until you drop --dry-run)")
    print("=" * 72)
    print(f"  model          : {os.environ['GEMINI_MODEL']} (thinking={os.environ['GEMINI_THINKING_LEVEL']})")
    print(f"  pairs to label : {len(todo)}")
    print(f"  batch size     : {BATCH_SIZE}")
    print(f"  API CALLS      : {n_batches}")
    print(f"  input tokens   : ~{in_tok:,}")
    print(f"  output tokens  : ~{out_tok:,}")
    print(f"  ESTIMATED COST : ~${cost:.3f}")
    print("=" * 72)


async def label_batch(client: GeminiClient, batch: list[tuple[str, str]]) -> list[dict]:
    payload = {
        "pairs": [
            {"id": i, "question": q, "symbol": s} for i, (q, s) in enumerate(batch)
        ]
    }
    result = await client.structured(
        system_prompt=SYSTEM_PROMPT,
        payload=payload,
        response_model=PolarityLabelBatch,
        max_tokens=8192,
        prefer_prompt_schema=True,
    )
    out = []
    for lab in result.labels:
        if 0 <= lab.id < len(batch):
            q, s = batch[lab.id]
            out.append(
                {
                    "question": q,
                    "symbol": s,
                    "polarity": int(lab.polarity),
                    "confidence": float(lab.confidence),
                    "reason": lab.reason,
                    "model": client.model_name,
                    "prompt_version": PROMPT_VERSION,
                }
            )
    return out


def compare_to_regex(cache: dict) -> int:
    """Agreement between the LLM and the keyword heuristic it is meant to replace."""
    rows = []
    for rec in cache.values():
        llm = rec["polarity"]
        rgx, fired = explain_polarity(rec["question"])
        rows.append(
            {
                "question": rec["question"],
                "symbol": rec["symbol"],
                "llm": llm,
                "regex": rgx,
                "rules": ",".join(fired),
                "confidence": rec["confidence"],
                "reason": rec["reason"],
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        print("cache is empty; run the labeller first")
        return 1

    agree = (df.llm == df.regex).mean()
    print("\n" + "=" * 100)
    print(f"  LLM vs REGEX -- {len(df)} pairs, agreement {agree:.1%}")
    print("=" * 100)
    print(pd.crosstab(df.regex, df.llm, rownames=["regex"], colnames=["llm"]).to_string())

    dis = df[df.llm != df.regex].sort_values("confidence", ascending=False)
    print(f"\n  {len(dis)} DISAGREEMENTS (highest LLM confidence first)\n")
    for r in dis.itertuples():
        print(f"  {r.symbol:<6} regex={r.regex:+d} llm={r.llm:+d} conf={r.confidence:.2f}")
        print(f"         {r.question}")
        print(f"         llm: {r.reason}")
        print(f"         regex rules: {r.rules or '(none fired)'}")

    low = df[df.confidence < 0.7]
    print(f"\n  {len(low)} pairs labelled with confidence < 0.7 (LLM unsure)\n")
    for r in low.itertuples():
        print(f"  {r.symbol:<6} llm={r.llm:+d} conf={r.confidence:.2f}  {r.question}")
        print(f"         {r.reason}")
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the quote, send nothing")
    ap.add_argument("--compare", action="store_true", help="compare the cache against the regex")
    ap.add_argument("--limit", type=int, default=None, help="label at most N pairs (smoke test)")
    args = ap.parse_args()

    cache = load_cache()
    if args.compare:
        return compare_to_regex(cache)

    pairs = gather_pairs()
    todo = [(q, s) for q, s in pairs if pair_hash(q, s) not in cache]
    if args.limit:
        todo = todo[: args.limit]

    print(f"[plan] pairs={len(pairs)}  cached={len(pairs) - len(todo)}  to_label={len(todo)}")
    quote(todo)
    if args.dry_run or not todo:
        return 0

    client = GeminiClient()
    try:
        batches = [todo[i : i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
        results = await asyncio.gather(*(label_batch(client, b) for b in batches))
    finally:
        await client.close()

    n_new = 0
    for batch_rows in results:
        for rec in batch_rows:
            cache[pair_hash(rec["question"], rec["symbol"])] = rec
            n_new += 1
    save_cache(cache)
    print(f"\n[done] labelled {n_new} pairs -> {CACHE_PATH.relative_to(ROOT)}")
    print(f"       run `python -m LLM.label_polarity --compare` to diff against the regex")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
