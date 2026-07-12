"""Regenerate wide trade forensic CSVs from existing clean experiment logs."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtesting.pipeline.trade_forensics import write_existing_trade_forensics


def main() -> None:
    outputs = write_existing_trade_forensics(data_dir=Path(__file__).resolve().parent.parent / "data")
    print(f"Wrote {len(outputs)} forensic artifact(s):")
    for path in outputs:
        print(f"  {path}")


if __name__ == "__main__":
    main()
