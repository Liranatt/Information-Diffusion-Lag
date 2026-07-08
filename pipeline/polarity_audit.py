"""Eyeball every polarity classification before trusting a run.

    python -m pipeline.polarity_audit

Prints (a) every question classified bearish-YES, with the rules that fired, and
(b) every question that contains a polarity-risky token but was still classified
bullish -- the false-negative surface. A keyword heuristic caused the original
inverted-signal bug; this exists so the next one is visible rather than silent.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pipeline.strategy import explain_polarity  # noqa: E402

CANDIDATES = Path(__file__).resolve().parent.parent / "data" / "candidates.parquet"

# Tokens that *could* indicate an inverted question. Anything matching one of
# these but landing on polarity +1 is printed for review.
RISKY = re.compile(
    r"\bno\b|\bnot\b|\bnever\b|\bend|\bceasefire\b|\bwithdraw|\breturns?\s+to\b"
    r"|\babove\b|\bexceed|\bhike|\braise|\bbelow\b|\bmiss|\bfall|\bdeclin"
    r"|\bcrash|\bfails?\b|\breject|\bproduction\b|\boutput\b|\bsupply\b"
    r"|\bcut|\blower|\bdowngrade|\bdefault|\bbankrupt",
    re.I,
)


def main() -> int:
    df = pd.read_parquet(CANDIDATES)
    pairs = (
        df[["question", "symbol"]]
        .fillna("")
        .drop_duplicates()
        .sort_values(["question", "symbol"])
    )

    by_question = pairs.groupby("question")["symbol"].apply(
        lambda s: ",".join(sorted(set(s)))
    )

    inverted: list[tuple[str, str, list[str]]] = []
    risky_but_bullish: list[tuple[str, str]] = []

    for question, symbols in by_question.items():
        polarity, fired = explain_polarity(question)
        if polarity == -1:
            inverted.append((question, symbols, fired))
        elif RISKY.search(question):
            risky_but_bullish.append((question, symbols))

    n_rows_inverted = int(
        df["question"].fillna("").map(lambda q: explain_polarity(q)[0] == -1).sum()
    )

    print("=" * 100)
    print(f"  POLARITY AUDIT — {len(by_question)} unique questions, {len(df)} candidate rows")
    print("=" * 100)

    print(f"\n[-1] BEARISH-YES — probability path is flipped ({len(inverted)} questions, "
          f"{n_rows_inverted} candidate rows)\n")
    for question, symbols, fired in sorted(inverted):
        print(f"  {symbols:<12} {question}")
        print(f"  {'':<12} rules: {', '.join(fired)}")

    print(f"\n[+1] CONTAINS A RISKY TOKEN BUT CLASSIFIED BULLISH — review these "
          f"({len(risky_but_bullish)} questions)\n")
    for question, symbols in sorted(risky_but_bullish):
        print(f"  {symbols:<12} {question}")

    print("\n" + "=" * 100)
    print("  Every line above must be read. A wrong sign is a trade taken in the")
    print("  wrong direction, and it will still book a P&L that looks plausible.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
