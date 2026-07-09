"""Regenerate wide trade forensic CSVs from existing clean experiment logs."""
from __future__ import annotations

from pathlib import Path

from pipeline.trade_forensics import write_existing_trade_forensics


def main() -> None:
    outputs = write_existing_trade_forensics(data_dir=Path("data"))
    print(f"Wrote {len(outputs)} forensic artifact(s):")
    for path in outputs:
        print(f"  {path}")


if __name__ == "__main__":
    main()
