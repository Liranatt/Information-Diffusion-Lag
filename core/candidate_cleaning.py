"""Deterministic candidate cleaning and treatment-audit enforcement.

The source candidate parquet remains immutable.  This module produces:

* an annotated, exactly-deduplicated research surface; and
* a primary-eligible parquet for CEM.

The treatment audit is keyed by ``(market_id, symbol)``.  Audited questions
that need a new semantic transform are quarantined instead of being fed to CEM
with their old binary interpretation.  Unreviewed historical candidates pass
through unchanged and are labelled explicitly; the audit only covered the
question set listed in ``data/candidate_treatment_audit.csv``.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

import numpy as np
import pandas as pd


AUDIT_VERSION = "polymarket-treatment-audit-2026-07-13"

VERDICT_ACTION = {
    "Mostly correct": "primary",
    "Mostly correct after controls": "primary_controlled",
    "Data repair, then keep": "primary",
    "Partly correct": "quarantine",
    "Needs redesign": "quarantine",
    "Secondary only": "secondary",
    "Exclude from primary": "exclude",
    "Incorrect endpoint": "exclude",
}

PRIMARY_ACTIONS = frozenset({"primary", "primary_controlled", "unreviewed_passthrough"})
AUDIT_REQUIRED_COLUMNS = frozenset(
    {
        "market_id",
        "asset_symbol",
        "question",
        "treatment_verdict",
        "question_structure",
        "economic_event_group",
    }
)
CANDIDATE_REQUIRED_COLUMNS = frozenset(
    {"market_id", "symbol", "question", "event_id", "t_theta", "t_e", "split"}
)


@dataclass(frozen=True)
class CandidateCleaningResult:
    annotated: pd.DataFrame
    primary: pd.DataFrame
    dispositions: pd.DataFrame
    summary: dict[str, Any]


def _normalize_id(series: pd.Series) -> pd.Series:
    return series.astype("string").fillna("").str.strip().str.removeprefix("mkt:")


def _normalize_text(value: Any) -> str:
    text = "" if pd.isna(value) else str(value)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _event_identifier(audit_group: Any, economic_event_group: Any) -> str:
    canonical = f"{_normalize_text(audit_group)}|{_normalize_text(economic_event_group)}"
    return f"audit:{sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


def _require_columns(frame: pd.DataFrame, required: frozenset[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {missing}")


def load_treatment_audit(path: str | Path) -> pd.DataFrame:
    """Load and validate the canonical question-asset treatment audit."""
    path = Path(path)
    audit = pd.read_csv(path, dtype={"market_id": "string", "asset_symbol": "string"})
    _require_columns(audit, AUDIT_REQUIRED_COLUMNS, str(path))

    audit = audit.copy()
    audit["market_id"] = _normalize_id(audit["market_id"])
    audit["asset_symbol"] = audit["asset_symbol"].astype("string").str.strip().str.upper()
    audit["treatment_verdict"] = audit["treatment_verdict"].astype("string").str.strip()

    unknown = sorted(set(audit["treatment_verdict"].dropna()) - set(VERDICT_ACTION))
    if unknown:
        raise ValueError(f"Unknown treatment verdicts in {path}: {unknown}")

    mapped_action = audit["treatment_verdict"].map(VERDICT_ACTION)
    if "treatment_action" in audit.columns:
        supplied = audit["treatment_action"].astype("string").str.strip()
        mismatch = supplied.notna() & supplied.ne(mapped_action)
        if mismatch.any():
            sample = audit.loc[mismatch, ["market_id", "asset_symbol", "treatment_verdict", "treatment_action"]]
            raise ValueError(
                "Treatment action disagrees with the frozen verdict mapping:\n"
                + sample.head(10).to_string(index=False)
            )
    audit["treatment_action"] = mapped_action
    audit["audit_version"] = audit.get("audit_version", AUDIT_VERSION)
    audit["audit_version"] = audit["audit_version"].fillna(AUDIT_VERSION)

    duplicate_keys = audit.duplicated(["market_id", "asset_symbol"], keep=False)
    if duplicate_keys.any():
        sample = audit.loc[duplicate_keys, ["market_id", "asset_symbol", "question"]]
        raise ValueError(
            "Treatment audit must contain one row per market_id/symbol key:\n"
            + sample.head(10).to_string(index=False)
        )
    return audit


def _deduplicate_exact_candidates(candidates: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Collapse exact copies and reject ambiguous duplicate market-asset keys."""
    frame = candidates.copy()
    frame["market_id"] = _normalize_id(frame["market_id"])
    frame["symbol"] = frame["symbol"].astype("string").str.strip().str.upper()
    frame["source_row_number"] = np.arange(len(frame), dtype=np.int64)

    # Count copies before source_row_number makes each record unique.
    comparison_columns = [column for column in frame.columns if column != "source_row_number"]
    exact_duplicate = frame.duplicated(comparison_columns, keep="first")
    exact_excess = int(exact_duplicate.sum())

    key_counts = frame.groupby(["market_id", "symbol"], dropna=False)["market_id"].transform("size")
    frame["source_duplicate_count"] = key_counts.astype("int64")
    deduped = frame.loc[~exact_duplicate].copy()

    ambiguous = deduped.duplicated(["market_id", "symbol"], keep=False)
    if ambiguous.any():
        cols = ["market_id", "symbol", "question", "t_theta", "t_e", "source_row_number"]
        sample = deduped.loc[ambiguous, [column for column in cols if column in deduped.columns]]
        raise ValueError(
            "Non-identical candidate rows share a market_id/symbol key. "
            "They require an explicit economic-event aggregation rule:\n"
            + sample.head(20).to_string(index=False)
        )
    return deduped.reset_index(drop=True), exact_excess


def clean_candidates(candidates: pd.DataFrame, audit: pd.DataFrame) -> CandidateCleaningResult:
    """Annotate, deduplicate, gate, and event-cluster a candidate dataframe."""
    _require_columns(candidates, CANDIDATE_REQUIRED_COLUMNS, "candidate dataframe")
    _require_columns(audit, AUDIT_REQUIRED_COLUMNS | {"treatment_action"}, "treatment audit")

    deduped, exact_excess = _deduplicate_exact_candidates(candidates)
    audit_for_merge = audit.rename(
        columns={"asset_symbol": "symbol", "question": "audit_question"}
    ).copy()

    annotated = deduped.merge(
        audit_for_merge,
        on=["market_id", "symbol"],
        how="left",
        validate="one_to_one",
        suffixes=("", "_audit"),
        indicator="audit_merge_status",
    )
    annotated["audit_reviewed"] = annotated["audit_merge_status"].eq("both")

    reviewed = annotated["audit_reviewed"]
    question_mismatch = reviewed & annotated.apply(
        lambda row: _normalize_text(row["question"]) != _normalize_text(row["audit_question"]),
        axis=1,
    )
    if question_mismatch.any():
        sample = annotated.loc[
            question_mismatch, ["market_id", "symbol", "question", "audit_question"]
        ]
        raise ValueError(
            "Candidate question text disagrees with the frozen audit key:\n"
            + sample.head(10).to_string(index=False)
        )

    annotated["treatment_verdict"] = annotated["treatment_verdict"].fillna("Not reviewed")
    annotated["treatment_action"] = annotated["treatment_action"].fillna(
        "unreviewed_passthrough"
    )
    if "audit_version" not in annotated.columns:
        annotated["audit_version"] = AUDIT_VERSION
    else:
        annotated["audit_version"] = annotated["audit_version"].fillna(AUDIT_VERSION)
    if "required_probability_transform" not in annotated.columns:
        annotated["required_probability_transform"] = "legacy_binary_unreviewed"
    else:
        annotated["required_probability_transform"] = annotated[
            "required_probability_transform"
        ].fillna("legacy_binary_unreviewed")
    for column in ("requires_external_baseline", "requires_path_aggregation"):
        if column not in annotated.columns:
            annotated[column] = False
        else:
            annotated[column] = annotated[column].fillna(False).astype(bool)
    if "standalone_signal_role" not in annotated.columns:
        annotated["standalone_signal_role"] = "unreviewed_passthrough"
    else:
        annotated["standalone_signal_role"] = annotated["standalone_signal_role"].fillna(
            "unreviewed_passthrough"
        )
    annotated["primary_signal_eligible"] = annotated["treatment_action"].isin(PRIMARY_ACTIONS)
    annotated["secondary_signal_only"] = annotated["treatment_action"].eq("secondary")
    annotated["requires_semantic_redesign"] = annotated["treatment_action"].eq("quarantine")
    annotated["invalid_endpoint"] = annotated["treatment_verdict"].eq("Incorrect endpoint")
    annotated["cem_eligible"] = annotated["primary_signal_eligible"] & ~annotated["invalid_endpoint"]

    audited_event = reviewed & annotated["economic_event_group"].notna()
    fallback_group = annotated["event_id"].astype("string").fillna(annotated["market_id"])
    annotated["economic_event_group_clean"] = fallback_group
    annotated.loc[audited_event, "economic_event_group_clean"] = annotated.loc[
        audited_event, "economic_event_group"
    ].astype("string")

    annotated["economic_event_id"] = "source:" + fallback_group
    annotated.loc[audited_event, "economic_event_id"] = annotated.loc[audited_event].apply(
        lambda row: _event_identifier(row.get("audit_group", ""), row["economic_event_group"]),
        axis=1,
    )

    annotated["economic_event_candidate_count"] = annotated.groupby(
        "economic_event_id", dropna=False
    )["market_id"].transform("size").astype("int64")
    annotated["economic_event_asset_count"] = annotated.groupby(
        "economic_event_id", dropna=False
    )["symbol"].transform("nunique").astype("int64")
    eligible_counts = annotated["cem_eligible"].astype("int64").groupby(
        annotated["economic_event_id"], dropna=False
    ).transform("sum")
    annotated["economic_event_primary_count"] = eligible_counts.astype("int64")
    annotated["economic_event_weight"] = np.where(
        annotated["cem_eligible"] & eligible_counts.gt(0), 1.0 / eligible_counts, 0.0
    )

    annotated["cleaning_disposition"] = annotated["treatment_action"].map(
        {
            "primary": "keep_primary",
            "primary_controlled": "keep_primary_controlled",
            "unreviewed_passthrough": "keep_unreviewed_passthrough",
            "quarantine": "quarantine_pending_transform",
            "secondary": "secondary_only",
            "exclude": "exclude_from_primary",
        }
    )
    annotated["deduplication_note"] = np.where(
        annotated["source_duplicate_count"].gt(1),
        annotated["source_duplicate_count"].map(
            lambda count: f"collapsed {int(count)} exact source copies to one row"
        ),
        "unique source row",
    )

    primary = annotated.loc[annotated["cem_eligible"]].copy().reset_index(drop=True)
    disposition_columns = [
        "market_id",
        "symbol",
        "question",
        "event_id",
        "economic_event_id",
        "economic_event_group_clean",
        "source_duplicate_count",
        "deduplication_note",
        "audit_reviewed",
        "treatment_verdict",
        "treatment_action",
        "cleaning_disposition",
        "question_structure",
        "required_probability_transform",
        "requires_external_baseline",
        "requires_path_aggregation",
        "standalone_signal_role",
        "recommended_family",
        "recommended_polarity_treatment",
        "recommended_probability_treatment",
        "recommended_entry_treatment",
        "recommended_exit_treatment",
        "recommended_weighting",
        "overfit_guard",
        "primary_signal_eligible",
        "secondary_signal_only",
        "requires_semantic_redesign",
        "invalid_endpoint",
        "cem_eligible",
        "polymarket_url",
        "authority_source_url",
    ]
    dispositions = annotated[
        [column for column in disposition_columns if column in annotated.columns]
    ].copy()

    action_counts = {
        str(key): int(value)
        for key, value in annotated["treatment_action"].value_counts(dropna=False).items()
    }
    verdict_counts = {
        str(key): int(value)
        for key, value in annotated.loc[reviewed, "treatment_verdict"].value_counts().items()
    }
    summary: dict[str, Any] = {
        "audit_version": AUDIT_VERSION,
        "input_rows": int(len(candidates)),
        "exact_duplicate_excess_rows_removed": exact_excess,
        "annotated_rows_after_exact_deduplication": int(len(annotated)),
        "audited_rows_after_deduplication": int(reviewed.sum()),
        "unreviewed_passthrough_rows": int((~reviewed).sum()),
        "primary_output_rows": int(len(primary)),
        "rows_removed_from_primary": int(len(annotated) - len(primary)),
        "economic_events_after_deduplication": int(annotated["economic_event_id"].nunique()),
        "primary_economic_events": int(primary["economic_event_id"].nunique()),
        "treatment_action_counts": action_counts,
        "audited_verdict_counts": verdict_counts,
    }
    return CandidateCleaningResult(annotated, primary, dispositions, summary)


def write_cleaning_outputs(
    result: CandidateCleaningResult,
    *,
    annotated_path: str | Path,
    primary_path: str | Path,
    disposition_path: str | Path,
    summary_path: str | Path,
) -> None:
    """Write cleaning artifacts without modifying the source candidate file."""
    annotated_path = Path(annotated_path)
    primary_path = Path(primary_path)
    disposition_path = Path(disposition_path)
    summary_path = Path(summary_path)
    for path in (annotated_path, primary_path, disposition_path, summary_path):
        path.parent.mkdir(parents=True, exist_ok=True)

    result.annotated.to_parquet(annotated_path, engine="pyarrow", compression="snappy", index=False)
    result.primary.to_parquet(primary_path, engine="pyarrow", compression="snappy", index=False)
    result.dispositions.to_csv(disposition_path, index=False)
    summary_path.write_text(
        json.dumps(result.summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
