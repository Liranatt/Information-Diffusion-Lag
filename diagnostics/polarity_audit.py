"""Print the complete human-review surface for signal polarity.

Run after generating ``data/polarity_labels.json`` and before trusting a
backtest::

    python -m diagnostics.polarity_audit

Every ``-1`` and ``0`` pair is printed with its resolver source.  The report
also prints every bullish pair whose question contains a token associated with
negation, de-escalation, adverse outcomes, macro thresholds, or supply changes.
That final surface makes likely false-negative inversions visible.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

from core.polarity import explain_polarity, resolve_polarity

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = ROOT / "data" / "candidates.parquet"

# These tokens do not determine polarity.  They merely identify +1 labels that
# deserve a human look.  Word boundaries intentionally keep company names such
# as CrowdStrike from matching the standalone event word "strike".
RISKY = re.compile(
    r"\bno\b|\bnot\b|\bnever\b|\bwithout\b|\brefrains?\b"
    r"|\bends?\b|\bceasefire\b|\bde-?escalat\w*\b|\bwithdraw\w*\b"
    r"|\breturns?\s+to\s+normal\b|\blift(?:ed|s|ing)?\b|\bblockade\b"
    r"|\bpeace\b|\bdiplomatic\b|\btalks?\b|\bstrike(?:s|n)?\b"
    r"|\bwar\b|\battacks?\b|\bmilitary\s+action\b|\bescalat\w*\b"
    r"|\babove\b|\bexceed\w*\b|\bhikes?\b|\braises?\b|\bbelow\b"
    r"|\bmiss(?:es|ed)?\b|\bfalls?\b|\bdeclin\w*\b|\bcrash\w*\b"
    r"|\bfails?\b|\breject\w*\b|\bproduction\b|\boutput\b|\bsupply\b"
    r"|\bcuts?\b|\blower\w*\b|\bdowngrade\w*\b|\bdefaults?\b"
    r"|\bbankrupt\w*\b|\blayoffs?\b",
    re.IGNORECASE,
)


def _print_pairs(frame: pd.DataFrame, polarity: int, heading: str) -> None:
    subset = frame[frame["polarity"] == polarity]
    candidate_rows = int(subset["candidate_rows"].sum()) if not subset.empty else 0
    print(
        f"\n[{polarity:+d}] {heading} "
        f"({len(subset)} pairs, {candidate_rows} candidate rows)\n"
    )
    for row in subset.itertuples(index=False):
        print(f"  {row.symbol:<6} [{row.source}] {row.question}")
        if row.source == "regex":
            print(f"  {'':<6} rules: {row.rules}")


def main() -> int:
    df = pd.read_parquet(CANDIDATES)
    required = {"question", "symbol"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(
            f"{CANDIDATES} is missing required columns: {', '.join(sorted(missing))}"
        )

    raw = df[["question", "symbol"]].fillna("").astype(str)
    counts = (
        raw.groupby(["question", "symbol"], dropna=False)
        .size()
        .rename("candidate_rows")
        .reset_index()
    )
    pairs = counts.sort_values(["question", "symbol"], kind="stable")

    rows: list[dict] = []
    for question, symbol, candidate_rows in pairs.itertuples(index=False, name=None):
        polarity, source = resolve_polarity(question, symbol)
        _, fired = explain_polarity(question)
        rows.append(
            {
                "question": question,
                "symbol": symbol,
                "polarity": polarity,
                "source": source,
                "rules": ",".join(fired) or "(none fired)",
                "candidate_rows": int(candidate_rows),
            }
        )
    audit = pd.DataFrame(rows)

    print("=" * 100)
    print(
        f"  POLARITY AUDIT -- {len(audit)} (question, symbol) pairs, "
        f"{len(df)} candidate rows"
    )
    print("=" * 100)

    print("\n  resolved by source:")
    for source, count in audit["source"].value_counts().items():
        print(f"    {source:<10} {count}")

    print("\n  label distribution:")
    for polarity in (-1, 0, 1):
        subset = audit[audit["polarity"] == polarity]
        print(
            f"    {polarity:+d}         {len(subset):>4} pairs  "
            f"{int(subset['candidate_rows'].sum()):>4} candidate rows"
        )

    _print_pairs(audit, -1, "TRADE NO -- raw probability path is flipped")
    _print_pairs(audit, 0, "NEITHER SIDE IS CLEAN -- pair is skipped")

    bullish = audit[audit["polarity"] == 1]
    risky = bullish[
        bullish["question"].str.contains(RISKY, regex=True, na=False)
    ]
    print(
        "\n[+1] CONTAINS A RISKY TOKEN BUT RESOLVED BULLISH -- review these "
        f"({len(risky)} pairs)\n"
    )
    for row in risky.itertuples(index=False):
        print(f"  {row.symbol:<6} [{row.source}] {row.question}")
        if row.source == "regex":
            print(f"  {'':<6} rules: {row.rules}")

    print("\n" + "=" * 100)
    print("  Every -1, 0, and risky +1 line above must be read before the backtest.")
    print("  A wrong sign can still produce plausible-looking P&L in the wrong thesis.")
    print("  Override evidence and resolver precedence live in core/polarity.py.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
