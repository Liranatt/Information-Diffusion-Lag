"""Build audit-annotated and primary-eligible candidate artifacts.

Usage:
    python -m ingest.clean_candidates
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from core.candidate_cleaning import (
    clean_candidates,
    load_treatment_audit,
    write_cleaning_outputs,
)


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

DEFAULT_INPUT = DATA / "candidates.parquet"
DEFAULT_AUDIT = DATA / "candidate_treatment_audit.csv"
DEFAULT_ANNOTATED = DATA / "candidates_audit_annotated.parquet"
DEFAULT_PRIMARY = DATA / "candidates_audit_clean.parquet"
DEFAULT_DISPOSITIONS = DATA / "candidate_cleaning_disposition.csv"
DEFAULT_SUMMARY = DATA / "candidate_cleaning_summary.json"


def build_clean_candidate_artifacts(
    *,
    input_path: Path = DEFAULT_INPUT,
    audit_path: Path = DEFAULT_AUDIT,
    annotated_path: Path = DEFAULT_ANNOTATED,
    primary_path: Path = DEFAULT_PRIMARY,
    disposition_path: Path = DEFAULT_DISPOSITIONS,
    summary_path: Path = DEFAULT_SUMMARY,
) -> dict:
    candidates = pd.read_parquet(input_path)
    audit = load_treatment_audit(audit_path)
    result = clean_candidates(candidates, audit)
    write_cleaning_outputs(
        result,
        annotated_path=annotated_path,
        primary_path=primary_path,
        disposition_path=disposition_path,
        summary_path=summary_path,
    )
    return result.summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply the frozen Polymarket treatment audit to candidate data."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--annotated-output", type=Path, default=DEFAULT_ANNOTATED)
    parser.add_argument("--primary-output", type=Path, default=DEFAULT_PRIMARY)
    parser.add_argument("--disposition-output", type=Path, default=DEFAULT_DISPOSITIONS)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    summary = build_clean_candidate_artifacts(
        input_path=args.input,
        audit_path=args.audit,
        annotated_path=args.annotated_output,
        primary_path=args.primary_output,
        disposition_path=args.disposition_output,
        summary_path=args.summary_output,
    )
    print("[candidate-cleaning] completed")
    for key, value in summary.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
