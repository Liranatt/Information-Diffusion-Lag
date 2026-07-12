"""Generate committed three-state polarity labels for candidate pairs.

Usage::

    python -m ingest.label_polarity --dry-run
    python -m ingest.label_polarity
    python -m ingest.label_polarity --compare

Polarity is a property of a ``(question, symbol)`` pair.  It identifies which
resolution of the binary market is a clean bullish signal for a *long*
position: ``+1`` for YES, ``-1`` for NO, and ``0`` when neither side is clean.

The generated cache is also the reproducibility artifact consumed by
``core.polarity``.  Keys are SHA-256 hashes of the normalized pair plus the
prompt version, so an unchanged rerun is free and a prompt change cannot reuse
stale judgements.  Earnings and direct "FDA approves ..." templates are
deterministic ``+1`` records; only the remaining pairs are sent to Gemini.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

# This labelling pass is deliberately pinned.  Changing the model is a new
# experiment and should also result in a new PROMPT_VERSION.
MODEL_NAME = "gemini-2.5-flash"
os.environ["GEMINI_MODEL"] = MODEL_NAME
os.environ["GEMINI_THINKING_LEVEL"] = "none"
# Keep GeminiClient's optional usage ledger on the same official rate card as
# the preflight quote.
os.environ["GEMINI_PRICE_INPUT_PER_M"] = "0.30"
os.environ["GEMINI_PRICE_OUTPUT_PER_M"] = "2.50"

from core.polarity import explain_polarity  # noqa: E402
from ingest.gemini_client import GeminiClient  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
PROMPT_VERSION = "polarity-v2"
BATCH_SIZE = 6
CACHE_PATH = ROOT / "data" / "polarity_labels.json"
CANDIDATES = ROOT / "data" / "candidates.parquet"

# Gemini 2.5 Flash list prices used only for the preflight estimate.  The call
# count is exact; token count and cost are necessarily estimates.
PRICE_IN_PER_M = 0.30
PRICE_OUT_PER_M = 2.50

_EARNINGS_RE = re.compile(r"\bearnings?\b", re.IGNORECASE)
_FDA_APPROVES_RE = re.compile(
    r"\bfda\s+(?:directly\s+)?approv(?:e|es)\b", re.IGNORECASE
)

SYSTEM_PROMPT = """You label prediction-market SIGNAL POLARITY for a long-only trading strategy.

For every (question, symbol) pair, answer this exact question:

  Which resolution of this market is clearly BULLISH for a LONG position in
  SYMBOL -- YES, NO, or NEITHER?

Return polarity = 1 when YES is clearly bullish, polarity = -1 when NO is
clearly bullish, and polarity = 0 when NEITHER resolution has a clean bullish
reading for that symbol.

The distinction between NO and NEITHER is essential.  NO qualifies only when
the NO resolution is itself clearly bullish, not merely because YES is absent.
For example, for an oil ETF, NO to "war ends" means war continues and the crude
risk premium persists, so NO is bullish (-1).  But NO to "the Fed cuts rates"
could mean either a hold or a hike; that ambiguity does not make NO bullish.  If
YES is clearly bullish in that example, use +1; use 0 only when neither side is
a clean bullish signal.

Reason about the causal chain from the event to the named asset.  Pay attention
to what the symbol represents: an oil ETF (USO/BNO), energy fund (XLE), shipping
company (ZIM), short-duration Treasury fund (SHY), or an individual company.

Rules and traps:

- Negation and cessation alter the event itself.  "The US does not strike Iran"
  resolving YES is de-escalation; "military action ends" resolving YES removes
  the oil risk premium.
- A rising level is subject-dependent.  Higher inflation or OPEC production can
  be bearish, while more subscribers or higher company revenue can be bullish.
- Company names contain misleading words: Five Below, Tractor Supply, and
  CrowdStrike are names, not directional instructions.
- Judge price impact on that symbol, not likelihood, morality, or the everyday
  emotional tone of the event.
- Prefer polarity 0 when the causal exposure or either resolution's bullish
  meaning is genuinely ambiguous.  Do not force a binary answer.

confidence is a number from 0 to 1.  reason is one short clause naming the
causal link.  Return exactly one object for every supplied id, use no invented
ids, and return only JSON matching the supplied schema."""


class PolarityLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=0)
    polarity: Literal[-1, 0, 1]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=3, max_length=300)


class PolarityLabelBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    labels: list[PolarityLabel] = Field(min_length=1, max_length=BATCH_SIZE)


@dataclass(frozen=True)
class PairSpec:
    question: str
    symbol: str
    auto_reason: str | None = None


def pair_hash(question: str, symbol: str) -> str:
    """Stable cache key for a normalized pair under the current prompt."""
    key = f"{question.strip().lower()}|{symbol.strip().upper()}|{PROMPT_VERSION}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _automatic_reason(question: str, archetype: str) -> str | None:
    if "earnings" in archetype.strip().lower() or _EARNINGS_RE.search(question):
        return "automatic earnings rule: YES is the bullish company outcome"
    if _FDA_APPROVES_RE.search(question):
        return "automatic FDA-approval rule: YES approval is bullish for the company"
    return None


def gather_pair_specs() -> list[PairSpec]:
    """Read and normalize the unique candidate ``(question, symbol)`` pairs."""
    df = pd.read_parquet(CANDIDATES)
    required = {"question", "symbol"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(
            f"{CANDIDATES} is missing required columns: {', '.join(sorted(missing))}"
        )

    has_archetype = "feat_archetype" in df.columns
    columns = ["question", "symbol"] + (["feat_archetype"] if has_archetype else [])

    # Deduplicate on the same normalization used by core.polarity.  If repeated
    # rows disagree on archetype, an automatic rule firing on any row wins.
    by_key: dict[tuple[str, str], PairSpec] = {}
    for values in df[columns].fillna("").itertuples(index=False, name=None):
        question = str(values[0]).strip()
        symbol = str(values[1]).strip().upper()
        archetype = str(values[2]) if has_archetype else ""
        if len(question) < 8 or not symbol:
            continue
        key = (question.lower(), symbol)
        reason = _automatic_reason(question, archetype)
        previous = by_key.get(key)
        if previous is None:
            by_key[key] = PairSpec(question, symbol, reason)
        elif previous.auto_reason is None and reason is not None:
            by_key[key] = PairSpec(previous.question, previous.symbol, reason)

    return sorted(
        by_key.values(), key=lambda pair: (pair.question.lower(), pair.symbol)
    )


def gather_pairs() -> list[tuple[str, str]]:
    """Compatibility helper returning just the pair values."""
    return [(pair.question, pair.symbol) for pair in gather_pair_specs()]


def _is_current_record(key: str, record: object) -> bool:
    if not isinstance(record, dict):
        return False
    try:
        question = str(record["question"])
        symbol = str(record["symbol"])
        polarity = int(record["polarity"])
    except (KeyError, TypeError, ValueError):
        return False
    return (
        key == pair_hash(question, symbol)
        and record.get("prompt_version") == PROMPT_VERSION
        and polarity in {-1, 0, 1}
    )


def load_cache() -> dict[str, dict]:
    """Load only self-consistent v2 records; stale prompt versions are ignored."""
    if not CACHE_PATH.exists():
        return {}
    raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{CACHE_PATH} must contain a JSON object keyed by sha256")
    return {
        str(key): record
        for key, record in raw.items()
        if _is_current_record(str(key), record)
    }


def save_cache(cache: dict[str, dict]) -> None:
    """Atomically write the reproducible, reviewable label artifact."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cache, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    temporary = CACHE_PATH.with_suffix(CACHE_PATH.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(CACHE_PATH)


def _auto_record(pair: PairSpec) -> dict:
    assert pair.auto_reason is not None
    return {
        "question": pair.question,
        "symbol": pair.symbol,
        "polarity": 1,
        "confidence": 1.0,
        "reason": pair.auto_reason,
        "model": "deterministic-rule",
        "prompt_version": PROMPT_VERSION,
    }


def _batch_payload(batch: list[tuple[str, str]]) -> dict:
    return {
        "pairs": [
            {"id": idx, "question": question, "symbol": symbol}
            for idx, (question, symbol) in enumerate(batch)
        ]
    }


def quote(todo: list[tuple[str, str]]) -> None:
    """Print the exact call count and an intentionally conservative cost quote."""
    batches = [
        todo[start : start + BATCH_SIZE]
        for start in range(0, len(todo), BATCH_SIZE)
    ]
    n_batches = len(batches)

    # `prefer_prompt_schema=True` makes GeminiClient append this schema text to
    # every system prompt. Mirror that request construction so the quote does
    # not omit the largest repeated input component.
    schema = PolarityLabelBatch.model_json_schema()
    instructions = (
        SYSTEM_PROMPT
        + "\n\nReturn only JSON that validates exactly against this schema. "
        + "The schema is enforced again by the caller:\n"
        + json.dumps(schema, ensure_ascii=False)
    )
    input_chars = sum(
        len(instructions)
        + len(json.dumps(_batch_payload(batch), ensure_ascii=False, default=str))
        for batch in batches
    )
    input_tokens = math.ceil(input_chars / 4)
    output_tokens = len(todo) * 55
    cost = (
        input_tokens / 1_000_000 * PRICE_IN_PER_M
        + output_tokens / 1_000_000 * PRICE_OUT_PER_M
    )
    validation_retries = int(os.environ.get("GEMINI_VALIDATION_RETRIES", "2"))
    max_validation_attempts = validation_retries + 1

    print("\n" + "=" * 72)
    print("  PAID RUN QUOTE  (nothing is sent when --dry-run is set)")
    print("=" * 72)
    print(f"  model          : {MODEL_NAME} (thinking=none)")
    print(f"  pairs to label : {len(todo)}")
    print(f"  batch size     : {BATCH_SIZE}")
    print(f"  API CALLS      : {n_batches} planned (before any validation retry)")
    print(f"  input tokens   : ~{input_tokens:,}")
    print(f"  output tokens  : ~{output_tokens:,}")
    print(f"  BASE COST      : ~${cost:.3f} (one valid response per batch)")
    if validation_retries:
        print(
            f"  RETRY CEILING  : {n_batches * max_validation_attempts} successful "
            f"generations / ~${cost * max_validation_attempts:.3f}"
        )
        print("                   only if every batch uses all validation retries")
    print("=" * 72)


async def label_batch(
    client: GeminiClient, batch: list[tuple[str, str]]
) -> list[dict]:
    payload = _batch_payload(batch)
    result = await client.structured(
        system_prompt=SYSTEM_PROMPT,
        payload=payload,
        response_model=PolarityLabelBatch,
        max_tokens=8192,
        prefer_prompt_schema=True,
    )

    labels_by_id: dict[int, PolarityLabel] = {}
    for label in result.labels:
        if label.id in labels_by_id:
            raise RuntimeError(f"Gemini returned duplicate polarity id {label.id}")
        labels_by_id[label.id] = label
    expected = set(range(len(batch)))
    received = set(labels_by_id)
    if received != expected:
        raise RuntimeError(
            "Gemini returned an incomplete polarity batch: "
            f"missing={sorted(expected - received)}, unexpected={sorted(received - expected)}"
        )

    rows: list[dict] = []
    for idx, (question, symbol) in enumerate(batch):
        label = labels_by_id[idx]
        rows.append(
            {
                "question": question,
                "symbol": symbol,
                "polarity": int(label.polarity),
                "confidence": float(label.confidence),
                "reason": label.reason.strip(),
                "model": client.model_name,
                "prompt_version": PROMPT_VERSION,
            }
        )
    return rows


def compare_to_regex(cache: dict[str, dict]) -> int:
    """Print all differences between v2 labels and the binary regex fallback."""
    rows = []
    for record in cache.values():
        regex_polarity, fired = explain_polarity(str(record["question"]))
        rows.append(
            {
                "question": str(record["question"]),
                "symbol": str(record["symbol"]),
                "label": int(record["polarity"]),
                "regex": regex_polarity,
                "rules": ",".join(fired),
                "confidence": float(record.get("confidence", 0.0)),
                "reason": str(record.get("reason", "")),
                "model": str(record.get("model", "unknown")),
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        print("cache is empty; run the labeler first")
        return 1

    agreement = (frame["label"] == frame["regex"]).mean()
    print("\n" + "=" * 100)
    print(f"  LABELS vs REGEX -- {len(frame)} pairs, exact agreement {agreement:.1%}")
    print("=" * 100)
    print(
        pd.crosstab(
            frame["regex"],
            frame["label"],
            rownames=["regex"],
            colnames=["label"],
        ).to_string()
    )

    disagreements = frame[frame["label"] != frame["regex"]].sort_values(
        ["confidence", "question"], ascending=[False, True]
    )
    print(f"\n  {len(disagreements)} DISAGREEMENTS (highest confidence first)\n")
    for row in disagreements.itertuples(index=False):
        print(
            f"  {row.symbol:<6} regex={row.regex:+d} label={row.label:+d} "
            f"conf={row.confidence:.2f} [{row.model}]"
        )
        print(f"         {row.question}")
        print(f"         label: {row.reason}")
        print(f"         regex rules: {row.rules or '(none fired)'}")

    low_confidence = frame[frame["confidence"] < 0.7]
    print(f"\n  {len(low_confidence)} labels with confidence < 0.7\n")
    for row in low_confidence.itertuples(index=False):
        print(
            f"  {row.symbol:<6} label={row.label:+d} "
            f"conf={row.confidence:.2f}  {row.question}"
        )
        print(f"         {row.reason}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate cached -1/0/+1 long-only polarity labels."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print exact call count and estimated cost; make no calls or writes",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="print differences between the committed cache and regex fallback",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="label at most N uncached non-rule pairs (smoke testing only)",
    )
    return parser


async def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.limit is not None and args.limit < 0:
        raise SystemExit("--limit must be non-negative")

    cache = load_cache()
    if args.compare:
        return compare_to_regex(cache)

    pairs = gather_pair_specs()
    active_hashes = {pair_hash(pair.question, pair.symbol) for pair in pairs}
    # Do not carry old-corpus records into the committed artifact.
    output = {key: value for key, value in cache.items() if key in active_hashes}

    automatic = [pair for pair in pairs if pair.auto_reason is not None]
    for pair in automatic:
        output[pair_hash(pair.question, pair.symbol)] = _auto_record(pair)

    pending_all = [
        (pair.question, pair.symbol)
        for pair in pairs
        if pair.auto_reason is None
        and pair_hash(pair.question, pair.symbol) not in output
    ]
    todo = pending_all if args.limit is None else pending_all[: args.limit]
    cached_llm = len(pairs) - len(automatic) - len(pending_all)
    print(
        f"[plan] pairs={len(pairs)}  auto_+1={len(automatic)}  "
        f"cached={cached_llm}  pending={len(pending_all)}  "
        f"this_run={len(todo)}"
    )
    quote(todo)
    if args.dry_run:
        return 0

    if todo:
        client = GeminiClient()
        try:
            batches = [
                todo[start : start + BATCH_SIZE]
                for start in range(0, len(todo), BATCH_SIZE)
            ]
            results = await asyncio.gather(
                *(label_batch(client, batch) for batch in batches)
            )
        finally:
            await client.close()

        for batch_rows in results:
            for record in batch_rows:
                output[pair_hash(record["question"], record["symbol"])] = record

    save_cache(output)
    remaining = sum(
        pair.auto_reason is None
        and pair_hash(pair.question, pair.symbol) not in output
        for pair in pairs
    )
    print(f"\n[done] wrote {len(output)} records -> {CACHE_PATH.relative_to(ROOT)}")
    if remaining:
        print(f"       {remaining} non-rule pairs remain unlabeled (limited run)")
    else:
        print("       artifact is complete for the current candidate corpus")
    print("       run `python -m ingest.label_polarity --compare` for the regex diff")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
