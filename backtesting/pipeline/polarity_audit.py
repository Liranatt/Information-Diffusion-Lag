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

from pipeline.strategy import explain_polarity, resolve_polarity  # noqa: E402

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
        .astype(str)
        .drop_duplicates()
        .sort_values(["question", "symbol"])
    )

    rows = []
    for question, symbol in pairs.itertuples(index=False, name=None):
        polarity, source = resolve_polarity(question, symbol)
        _, fired = explain_polarity(question)
        rows.append(
            {
                "question": question,
                "symbol": symbol,
                "polarity": polarity,
                "source": source,
                "rules": ",".join(fired) or "(none fired)",
            }
        )
    audit = pd.DataFrame(rows)

    n_rows_inverted = int(
        sum(resolve_polarity(str(q), str(s))[0] == -1 for q, s in zip(df["question"], df["symbol"]))
    )

    print("=" * 100)
    print(f"  POLARITY AUDIT — {len(pairs)} (question, symbol) pairs, {len(df)} candidate rows")
    print("=" * 100)
    print("\n  resolved by source:")
    for source, n in audit["source"].value_counts().items():
        print(f"    {source:<10} {n}")

    inverted = audit[audit.polarity == -1]
    print(f"\n[-1] BEARISH-YES — probability path is flipped ({len(inverted)} pairs, "
          f"{n_rows_inverted} candidate rows)\n")
    for r in inverted.itertuples():
        print(f"  {r.symbol:<6} [{r.source}] {r.question}")
        if r.source == "regex":
            print(f"  {'':<6} rules: {r.rules}")

    bullish = audit[audit.polarity == 1]
    risky = bullish[bullish.question.str.contains(RISKY)]
    print(f"\n[+1] CONTAINS A RISKY TOKEN BUT RESOLVED BULLISH — review these "
          f"({len(risky)} pairs)\n")
    for r in risky.itertuples():
        print(f"  {r.symbol:<6} [{r.source}] {r.question}")

    print("\n" + "=" * 100)
    print("  Every line above must be read. A wrong sign is a trade taken in the")
    print("  wrong direction, and it will still book a P&L that looks plausible.")
    print("  `override` rows encode a domain fact the LLM got wrong; see")
    print("  POLARITY_OVERRIDES in pipeline/strategy.py for the evidence.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
