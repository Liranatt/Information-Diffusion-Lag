"""Export the reviewed question-asset treatment rows as canonical CSV config.

This is a provenance utility: it converts the source-backed audit JSON used to
build the review workbook into the compact table consumed by candidate cleaning.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from core.candidate_cleaning import AUDIT_VERSION, VERDICT_ACTION


STRUCTURE_TRANSFORM = {
    "central-bank decision": "outcome_distribution_vs_ois",
    "commodity supply statistic": "consensus_surprise",
    "company KPI threshold": "threshold_distribution_vs_consensus",
    "corporate-governance vote": "mechanism_conditioned_probability_revision",
    "court/policy ruling": "exposure_weighted_probability_revision",
    "diplomatic process event": "process_confirmation_only",
    "endogenous relative-performance": "excluded_endogenous_target",
    "labor-disruption deadline": "deadline_hazard",
    "macro data threshold": "threshold_distribution_vs_consensus",
    "observable commodity-price threshold": "threshold_distribution_vs_consensus",
    "operational resumption": "duration_adjusted_probability_revision",
    "policy/agreement deadline": "deadline_hazard",
    "prediction-market derivative": "excluded_circular_derivative",
    "product announcement bundle": "probability_weighted_product_bundle",
    "scheduled regulatory decision": "probability_revision",
    "substantive ceasefire deadline": "probability_revision",
    "territorial military outcome": "mechanism_conditioned_secondary_signal",
}

EXTERNAL_BASELINE_STRUCTURES = frozenset(
    {
        "central-bank decision",
        "commodity supply statistic",
        "company KPI threshold",
        "court/policy ruling",
        "macro data threshold",
        "observable commodity-price threshold",
        "product announcement bundle",
    }
)

PATH_AGGREGATION_STRUCTURES = frozenset(
    {
        "central-bank decision",
        "company KPI threshold",
        "labor-disruption deadline",
        "macro data threshold",
        "observable commodity-price threshold",
        "policy/agreement deadline",
        "product announcement bundle",
    }
)


COLUMNS = [
    "audit_version",
    "audit_group",
    "market_id",
    "asset_symbol",
    "question",
    "treatment_verdict",
    "treatment_action",
    "review_priority",
    "question_structure",
    "required_probability_transform",
    "requires_external_baseline",
    "requires_path_aggregation",
    "standalone_signal_role",
    "current_family",
    "recommended_family",
    "economic_event_group",
    "current_gemini_side",
    "current_gemini_confidence",
    "current_gemini_reason",
    "recommended_polarity_treatment",
    "recommended_probability_treatment",
    "recommended_entry_treatment",
    "recommended_exit_treatment",
    "recommended_weighting",
    "generalizable_edge",
    "overfit_guard",
    "polymarket_rule_summary",
    "polymarket_end_date",
    "polymarket_url",
    "authority_source_url",
    "api_source_url",
]


def export_audit(source: Path, output: Path) -> pd.DataFrame:
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows = pd.DataFrame(payload["review_rows"])
    rows["audit_version"] = AUDIT_VERSION
    rows["treatment_action"] = rows["treatment_verdict"].map(VERDICT_ACTION)
    rows["required_probability_transform"] = rows["question_structure"].map(
        STRUCTURE_TRANSFORM
    )
    unknown_structures = sorted(
        rows.loc[rows["required_probability_transform"].isna(), "question_structure"].unique()
    )
    if unknown_structures:
        raise ValueError(f"No frozen transform for question structures: {unknown_structures}")
    rows["requires_external_baseline"] = rows["question_structure"].isin(
        EXTERNAL_BASELINE_STRUCTURES
    )
    rows["requires_path_aggregation"] = rows["question_structure"].isin(
        PATH_AGGREGATION_STRUCTURES
    )
    rows["standalone_signal_role"] = rows["treatment_action"].map(
        {
            "primary": "primary",
            "primary_controlled": "primary_with_event_controls",
            "quarantine": "quarantined_until_transform_available",
            "secondary": "secondary_confirmation_only",
            "exclude": "excluded",
        }
    )
    missing = [column for column in COLUMNS if column not in rows.columns]
    if missing:
        raise ValueError(f"Audit payload is missing export columns: {missing}")

    exported = rows[COLUMNS].copy()
    exported["market_id"] = exported["market_id"].astype("string").str.removeprefix("mkt:")
    exported["asset_symbol"] = exported["asset_symbol"].astype("string").str.upper()
    exported = exported.sort_values(
        ["audit_group", "economic_event_group", "question", "asset_symbol", "market_id"],
        kind="mergesort",
    ).reset_index(drop=True)
    if exported.duplicated(["market_id", "asset_symbol"]).any():
        raise ValueError("Export produced duplicate market_id/asset_symbol audit keys.")

    output.parent.mkdir(parents=True, exist_ok=True)
    exported.to_csv(output, index=False)
    return exported


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    exported = export_audit(args.source, args.output)
    print(f"[treatment-audit] wrote {len(exported)} rows to {args.output}")


if __name__ == "__main__":
    main()
