"""Stage 2G: timestamp-safe pre-entry probability-trajectory coverage audit.

The coverage gate is intentionally evaluated before any outcome column is read,
any trajectory feature is constructed, or any model is fitted.  When the gate
fails, the module emits explicit not-run artifacts for the requested prediction,
same-day selection, paired-fold, and exact-replay comparisons.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from selection.stage2e_path_aware import EXIT_POLICIES


PROJECT = Path(__file__).resolve().parents[1]
STAGE2F = PROJECT / "data" / "selection_stage2f"
OOF_CANDIDATES = STAGE2F / "nested_oof_models" / "stage2f_oof_predictions.csv"
PROBABILITIES = PROJECT / "data" / "probs.pkl"
OUTPUT = PROJECT / "data" / "selection_stage2g"

EXPECTED_CANDIDATES = 415
MIN_CURVATURE_POINTS = 3
MIN_FULL_TRAJECTORY_POINTS = 5
MIN_CURVATURE_SHARE = 0.90
MIN_FULL_TRAJECTORY_SHARE = 0.80
MIN_FOLD_CURVATURE_SHARE = 0.80

AUDIT_COLUMNS = (
    "stage2e_candidate_id",
    "market_id",
    "symbol",
    "benchmark",
    "economic_event_id",
    "event_family",
    "entry_date",
    "t0",
    "t_theta",
    "outer_fold",
)

MODEL_VARIANTS = (
    "A_target_b_baseline",
    "B_target_b_plus_probability_trajectory",
    "C_probability_trajectory_only_diagnostic",
)


def _json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _as_utc(value: Any) -> pd.Timestamp:
    return pd.to_datetime(value, errors="coerce", utc=True)


def _path_timestamps(path: list[tuple[Any, Any]]) -> pd.DatetimeIndex:
    if not path:
        return pd.DatetimeIndex([], tz="UTC")
    return pd.DatetimeIndex(pd.to_datetime([point[0] for point in path], errors="coerce", utc=True))


def _candidate_coverage(row: pd.Series, path: list[tuple[Any, Any]]) -> dict[str, Any]:
    """Audit one candidate without constructing a feature or reading an outcome."""
    entry = _as_utc(row["entry_date"])
    raw = _path_timestamps(path)
    valid = raw[~raw.isna()]
    ordered = valid.sort_values()
    strict_pre = ordered[ordered < entry]
    same_day = ordered[ordered.normalize() == entry.normalize()]
    post_day = ordered[ordered.normalize() > entry.normalize()]
    diffs = pd.Series(strict_pre).diff().dt.total_seconds().div(86_400).dropna()

    span_days = np.nan
    latest_age_days = np.nan
    first_age_days = np.nan
    if len(strict_pre):
        latest_age_days = (entry - strict_pre.max()).total_seconds() / 86_400
        first_age_days = (entry - strict_pre.min()).total_seconds() / 86_400
    if len(strict_pre) >= 2:
        span_days = (strict_pre.max() - strict_pre.min()).total_seconds() / 86_400

    missing_calendar_days = 0
    if len(diffs):
        missing_calendar_days = int(sum(max(int(round(gap)) - 1, 0) for gap in diffs if np.isfinite(gap)))

    all_source_times_midnight = bool(len(ordered) and ((ordered.hour == 0) & (ordered.minute == 0) & (ordered.second == 0)).all())
    entry_is_date_normalized = bool(
        pd.notna(entry)
        and entry.hour == 0
        and entry.minute == 0
        and entry.second == 0
        and entry.microsecond == 0
    )
    pre_count = int(len(strict_pre))
    if pre_count == 0:
        coverage_class = "no_strict_pre_entry_history"
    elif pre_count < MIN_CURVATURE_POINTS:
        coverage_class = "insufficient_for_slope_and_acceleration"
    elif pre_count < MIN_FULL_TRAJECTORY_POINTS:
        coverage_class = "minimal_curvature_only"
    else:
        coverage_class = "full_trajectory_point_count_only"

    result = {column: row.get(column) for column in AUDIT_COLUMNS}
    result.update(
        {
            "raw_probability_observations": int(len(ordered)),
            "invalid_probability_timestamps": int(raw.isna().sum()),
            "strict_pre_entry_observations": pre_count,
            "strict_pre_entry_span_days": span_days,
            "latest_strict_pre_entry_age_days": latest_age_days,
            "first_strict_pre_entry_age_days": first_age_days,
            "same_entry_day_ambiguous_observations": int(len(same_day)),
            "post_entry_day_observations_excluded": int(len(post_day)),
            "duplicate_probability_timestamps": int(ordered.duplicated().sum()),
            "source_order_monotonic": bool(valid.is_monotonic_increasing),
            "strict_pre_entry_irregular_intervals": bool(len(diffs) and (~np.isclose(diffs, 1.0)).any()),
            "strict_pre_entry_missing_calendar_days": missing_calendar_days,
            "all_source_times_date_normalized": all_source_times_midnight,
            "entry_decision_time_is_date_normalized": entry_is_date_normalized,
            "exact_source_availability_preserved": False,
            "exact_entry_decision_timestamp_preserved": False,
            "exact_pre_entry_ordering_verifiable": False,
            "has_slope_acceleration_point_count": pre_count >= MIN_CURVATURE_POINTS,
            "has_full_trajectory_point_count": pre_count >= MIN_FULL_TRAJECTORY_POINTS,
            "coverage_class": coverage_class,
            "feature_rows_constructed": 0,
            "post_entry_observations_used": 0,
        }
    )
    return result


def _observation_audit(row: pd.Series, path: list[tuple[Any, Any]]) -> list[dict[str, Any]]:
    """Classify every stored point; none are used unless the coverage gate passes."""
    entry = _as_utc(row["entry_date"])
    observations: list[dict[str, Any]] = []
    for position, point in enumerate(path):
        timestamp = _as_utc(point[0])
        if pd.isna(timestamp):
            temporal_class = "invalid_timestamp_excluded"
        elif timestamp.normalize() == entry.normalize():
            temporal_class = "same_entry_day_ambiguous_excluded"
        elif timestamp < entry:
            temporal_class = "strictly_before_normalized_entry_date"
        else:
            temporal_class = "post_entry_day_excluded"
        observations.append(
            {
                "stage2e_candidate_id": row["stage2e_candidate_id"],
                "market_id": row["market_id"],
                "symbol": row["symbol"],
                "benchmark": row["benchmark"],
                "outer_fold": row["outer_fold"],
                "entry_date": entry,
                "raw_observation_position": position,
                "stored_probability_timestamp": timestamp,
                "probability": point[1],
                "temporal_class": temporal_class,
                "original_source_ts_available": False,
                "original_available_at_available": False,
                "used_in_trajectory_features": False,
            }
        )
    return observations


def _fold_summary(audit: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for fold, group in audit.groupby("outer_fold", sort=True):
        rows.append(
            {
                "outer_fold": int(fold),
                "candidates": len(group),
                "independent_events": group["economic_event_id"].nunique(),
                "strict_pre_entry_observations_mean": group["strict_pre_entry_observations"].mean(),
                "strict_pre_entry_observations_median": group["strict_pre_entry_observations"].median(),
                "no_strict_pre_entry_history_count": int(group["strict_pre_entry_observations"].eq(0).sum()),
                "no_strict_pre_entry_history_share": group["strict_pre_entry_observations"].eq(0).mean(),
                "curvature_point_count_share": group["has_slope_acceleration_point_count"].mean(),
                "full_trajectory_point_count_share": group["has_full_trajectory_point_count"].mean(),
                "strict_pre_entry_span_days_median": group["strict_pre_entry_span_days"].median(),
                "latest_strict_pre_entry_age_days_median": group["latest_strict_pre_entry_age_days"].median(),
                "same_day_timestamp_ambiguity_share": group["same_entry_day_ambiguous_observations"].gt(0).mean(),
                "irregular_history_share": group["strict_pre_entry_irregular_intervals"].mean(),
                "exact_pre_entry_ordering_verifiable_share": group["exact_pre_entry_ordering_verifiable"].mean(),
            }
        )
    return pd.DataFrame(rows)


def _coverage_decision(audit: pd.DataFrame, folds: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    candidate_count_ok = len(audit) == EXPECTED_CANDIDATES
    earnings_only_ok = audit["event_family"].astype(str).str.lower().eq("earnings").all()
    exact_ordering_ok = audit["exact_pre_entry_ordering_verifiable"].all()
    curvature_share = float(audit["has_slope_acceleration_point_count"].mean())
    full_share = float(audit["has_full_trajectory_point_count"].mean())
    worst_fold_curvature_share = float(folds["curvature_point_count_share"].min())
    gates = [
        ("expected_earnings_oof_candidate_count", candidate_count_ok, len(audit), EXPECTED_CANDIDATES),
        ("earnings_only_honest_oof_scope", earnings_only_ok, bool(earnings_only_ok), True),
        ("exact_entry_and_source_ordering_verifiable", exact_ordering_ok, float(audit["exact_pre_entry_ordering_verifiable"].mean()), 1.0),
        ("at_least_three_pre_entry_points", curvature_share >= MIN_CURVATURE_SHARE, curvature_share, MIN_CURVATURE_SHARE),
        ("at_least_five_pre_entry_points", full_share >= MIN_FULL_TRAJECTORY_SHARE, full_share, MIN_FULL_TRAJECTORY_SHARE),
        ("fold_level_three_point_coverage", worst_fold_curvature_share >= MIN_FOLD_CURVATURE_SHARE, worst_fold_curvature_share, MIN_FOLD_CURVATURE_SHARE),
    ]
    gate_table = pd.DataFrame(gates, columns=["coverage_gate", "passed", "observed", "required"])
    adequate = bool(gate_table["passed"].all())
    decision = {
        "coverage_sufficient": adequate,
        "decision": "continue_to_nested_oof_models" if adequate else "insufficient_data",
        "hypothesis_testable_reliably": adequate,
        "candidate_rows": len(audit),
        "independent_events": int(audit["economic_event_id"].nunique()),
        "chronological_folds": int(audit["outer_fold"].nunique()),
        "strict_pre_entry_observations_total": int(audit["strict_pre_entry_observations"].sum()),
        "no_strict_pre_entry_history_count": int(audit["strict_pre_entry_observations"].eq(0).sum()),
        "fewer_than_three_count": int(audit["strict_pre_entry_observations"].lt(MIN_CURVATURE_POINTS).sum()),
        "fewer_than_five_count": int(audit["strict_pre_entry_observations"].lt(MIN_FULL_TRAJECTORY_POINTS).sum()),
        "same_entry_day_ambiguous_count": int(audit["same_entry_day_ambiguous_observations"].gt(0).sum()),
        "exact_ordering_verifiable_count": int(audit["exact_pre_entry_ordering_verifiable"].sum()),
        "trajectory_features_constructed": False,
        "outcome_columns_read_for_stage2g": False,
        "models_fitted": False,
        "exact_replays_run": False,
        "post_entry_observations_used": 0,
        "reason": (
            "Stored entry decisions and probability observations are date-normalized rather than exact timestamps; "
            "the majority of candidates also lack enough strictly earlier points for trajectory curvature."
        ),
    }
    return gate_table, decision


def _not_run_outputs(output_dir: Path, audit: pd.DataFrame, decision: dict[str, Any]) -> dict[str, Path]:
    reason = decision["reason"]
    prediction_dir = output_dir / "oof_prediction_and_same_day_selection"
    prediction_dir.mkdir(parents=True, exist_ok=True)
    prediction = pd.DataFrame(
        [
            {
                "model": model,
                "status": "frozen_reference_only" if model == MODEL_VARIANTS[0] else "not_run_insufficient_probability_history",
                "oof_predictions_created": False,
                "same_day_comparison_created": False,
                "reason": (
                    "Target B remains the frozen Stage 2F operational reference; no Stage 2G comparison was estimated."
                    if model == MODEL_VARIANTS[0]
                    else reason
                ),
            }
            for model in MODEL_VARIANTS
        ]
    )
    prediction_path = prediction_dir / "comparison_status.csv"
    prediction.to_csv(prediction_path, index=False)

    replay_dir = output_dir / "exact_replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    replay = pd.DataFrame(
        [
            {
                "selector": model,
                "benchmark": benchmark,
                "exit_policy": exit_policy,
                "status": "not_run_insufficient_probability_history",
                "reason": reason,
            }
            for model in MODEL_VARIANTS
            for benchmark in ("SPY", "QQQ")
            for exit_policy in EXIT_POLICIES
        ]
    )
    replay_path = replay_dir / "selector_exit_exact_replay_status.csv"
    replay.to_csv(replay_path, index=False)

    paired_dir = output_dir / "paired_folds"
    paired_dir.mkdir(parents=True, exist_ok=True)
    paired = (
        audit.groupby("outer_fold", as_index=False)
        .agg(
            validation_candidates=("stage2e_candidate_id", "size"),
            independent_events=("economic_event_id", "nunique"),
            strict_pre_entry_observations=("strict_pre_entry_observations", "sum"),
        )
    )
    paired["status"] = "not_run_insufficient_probability_history"
    paired["paired_improvement_vs_target_b"] = np.nan
    paired["reason"] = reason
    paired_path = paired_dir / "paired_fold_comparison_status.csv"
    paired.to_csv(paired_path, index=False)
    return {"prediction_status": prediction_path, "replay_status": replay_path, "paired_status": paired_path}


def _markdown_table(frame: pd.DataFrame, columns: list[str], digits: int = 4) -> str:
    view = frame.loc[:, columns].copy()
    for column in view.select_dtypes(include=["float", "float64"]).columns:
        view[column] = view[column].map(lambda value: "" if pd.isna(value) else f"{value:.{digits}f}")
    headers = [str(column) for column in view.columns]
    rows = [[str(value) for value in row] for row in view.itertuples(index=False, name=None)]
    return "\n".join(
        [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
            *("| " + " | ".join(row) + " |" for row in rows),
        ]
    )


def _build_report(
    output_dir: Path,
    audit: pd.DataFrame,
    observations: pd.DataFrame,
    folds: pd.DataFrame,
    gates: pd.DataFrame,
    decision: dict[str, Any],
) -> Path:
    report = output_dir / "stage2g_probability_trajectory_coverage_report.md"
    report.write_text(
        "\n".join(
            [
                "# Stage 2G — Pre-entry Probability-Trajectory Audit",
                "",
                "## 1. Probability-trajectory coverage and leakage audit",
                "",
                f"The audit covers all **{len(audit)}** honest earnings OOF candidates across "
                f"**{audit['outer_fold'].nunique()}** chronological folds. The stored probability artifact contains one "
                "daily close per market/date, and its original intraday availability timestamp is replaced by "
                "`00:00:00 UTC`. Stage 2E also normalizes the entry decision to a calendar date. Therefore an "
                "entry-day probability point cannot be ordered against the exact decision, even though it appears at midnight.",
                "",
                "The conservative audit admits only observations with stored dates strictly before the entry date. "
                f"It excludes every same-day point and every later point; **{decision['post_entry_observations_used']}** "
                "post-entry observations were used.",
                f"The point-level audit classifies all **{len(observations)}** candidate-linked stored observations; "
                "none entered a feature or model because the coverage gate failed.",
                "",
                f"- No strictly pre-entry history: **{decision['no_strict_pre_entry_history_count']}/{len(audit)}**.",
                f"- Fewer than three points (slope plus acceleration unavailable): **{decision['fewer_than_three_count']}/{len(audit)}**.",
                f"- Fewer than five points (full trajectory poorly supported): **{decision['fewer_than_five_count']}/{len(audit)}**.",
                f"- Ambiguous same-entry-day observation: **{decision['same_entry_day_ambiguous_count']}/{len(audit)}**.",
                f"- Exact pre-entry ordering verifiable: **{decision['exact_ordering_verifiable_count']}/{len(audit)}**.",
                "",
                "Coverage by chronological fold:",
                "",
                _markdown_table(
                    folds,
                    [
                        "outer_fold",
                        "candidates",
                        "independent_events",
                        "strict_pre_entry_observations_median",
                        "no_strict_pre_entry_history_count",
                        "curvature_point_count_share",
                        "full_trajectory_point_count_share",
                        "same_day_timestamp_ambiguity_share",
                        "exact_pre_entry_ordering_verifiable_share",
                    ],
                ),
                "",
                "Outcome-free coverage gates:",
                "",
                _markdown_table(gates, ["coverage_gate", "passed", "observed", "required"]),
                "",
                "The point-count gates are minimum engineering requirements, not tuned performance thresholds: "
                "three points are needed for slope plus acceleration, while five points provide a minimally useful "
                "path for persistence, crossings, peak/drawdown/recovery, volatility, and update-frequency summaries.",
                "",
                "## 2. OOF prediction and same-day selection comparison",
                "",
                "Not run. Model B and the trajectory-only diagnostic cannot be constructed timestamp-safely for the "
                "OOF universe. Target B remains a frozen reference; it was not re-estimated or selectively compared.",
                "",
                "## 3. Selector × exit exact replay",
                "",
                "Not run. Replaying a selector whose features cannot be constructed honestly would turn the coverage "
                "failure into a performance claim. Status rows are emitted for A/B/C × SPY/QQQ × all eight Stage 2E exits.",
                "",
                "## 4. Paired fold comparison against Target B",
                "",
                "Not run. All five fold rows are retained in the status artifact so the absence of paired estimates is explicit.",
                "",
                "## 5. Final decision",
                "",
                "**INSUFFICIENT DATA — the full pre-entry probability-trajectory hypothesis cannot be tested reliably.**",
                "",
                "This is not evidence that trajectories fail, and it is not a promotion of Target B on new performance "
                "evidence. Target B remains the existing operational selector by default. Earnings selection research "
                "should not be declared closed on this untestable comparison; Stage 3 exit research may proceed with "
                "Target B while new point-in-time probability histories are collected.",
                "",
                "Required remediation for a future test: retain each Polymarket observation's original `source_ts` and "
                "`available_at`, retain the exact equity decision timestamp, and collect enough pre-decision updates in "
                "every chronological fold. The live feature builder must enforce `available_at <= decision_ts`.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return report


def run_stage2g(output_dir: Path | str = OUTPUT) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    header = pd.read_csv(OOF_CANDIDATES, nrows=0)
    missing_columns = sorted(set(AUDIT_COLUMNS) - set(header.columns))
    if missing_columns:
        raise AssertionError(f"Stage 2F OOF input is missing audit columns: {missing_columns}")
    candidates = pd.read_csv(OOF_CANDIDATES, usecols=list(AUDIT_COLUMNS))
    if len(candidates) != EXPECTED_CANDIDATES:
        raise AssertionError(f"Expected {EXPECTED_CANDIDATES} Stage 2F OOF candidates, found {len(candidates)}")
    if candidates["stage2e_candidate_id"].duplicated().any():
        raise AssertionError("Stage 2G input contains duplicate stable candidate IDs")
    if not candidates["event_family"].astype(str).str.lower().eq("earnings").all():
        raise AssertionError("Stage 2G must contain only honestly validated earnings OOF candidates")

    probabilities = pickle.loads(PROBABILITIES.read_bytes())
    probability_lookup = {str(key): value for key, value in probabilities.items()}
    audit_rows: list[dict[str, Any]] = []
    observation_rows: list[dict[str, Any]] = []
    for _, row in candidates.iterrows():
        path = probability_lookup.get(str(row["market_id"]), [])
        audit_rows.append(_candidate_coverage(row, path))
        observation_rows.extend(_observation_audit(row, path))
    audit = pd.DataFrame(audit_rows)
    observations = pd.DataFrame(observation_rows)
    for column in ("entry_date", "t0", "t_theta"):
        audit[column] = pd.to_datetime(audit[column], errors="coerce", utc=True)

    coverage_dir = output_dir / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = coverage_dir / "probability_trajectory_candidate_audit.csv"
    audit.to_csv(candidate_path, index=False)
    observation_path = coverage_dir / "probability_trajectory_observation_audit.csv"
    observations.to_csv(observation_path, index=False)
    folds = _fold_summary(audit)
    fold_path = coverage_dir / "probability_trajectory_fold_summary.csv"
    folds.to_csv(fold_path, index=False)
    gates, decision = _coverage_decision(audit, folds)
    gate_path = coverage_dir / "probability_trajectory_coverage_gates.csv"
    gates.to_csv(gate_path, index=False)
    leakage_path = coverage_dir / "probability_trajectory_leakage_audit.json"
    _json(
        leakage_path,
        {
            **decision,
            "safe_cutoff": "stored_probability_timestamp < normalized_entry_date",
            "same_entry_day_policy": "excluded_as_ambiguous",
            "later_observation_policy": "excluded",
            "source_artifact_semantics": "one latest <=20:59 UTC probability per date, original hour discarded",
            "entry_artifact_semantics": "entry_date normalized to 00:00 UTC by Stage 2E",
            "adequacy_thresholds": {
                "minimum_three_point_candidate_share": MIN_CURVATURE_SHARE,
                "minimum_five_point_candidate_share": MIN_FULL_TRAJECTORY_SHARE,
                "minimum_three_point_share_in_every_fold": MIN_FOLD_CURVATURE_SHARE,
            },
        },
    )

    statuses = _not_run_outputs(output_dir, audit, decision)
    decision_dir = output_dir / "decision"
    decision_dir.mkdir(parents=True, exist_ok=True)
    decision_path = decision_dir / "final_decision.json"
    _json(
        decision_path,
        {
            **decision,
            "final_decision": "insufficient_data",
            "target_b_status": "retain_existing_operational_selector_by_default",
            "earnings_selection_research_status": "not_closed_because_trajectory_hypothesis_was_not_testable",
            "stage3_exit_research_status": "may_proceed_with_target_b",
            "geo_and_other_status": "out_of_scope_no_honest_oof_support",
            "promotion_rule_evaluated": False,
        },
    )
    report_path = _build_report(output_dir, audit, observations, folds, gates, decision)
    manifest_path = output_dir / "stage2g_manifest.json"
    _json(
        manifest_path,
        {
            "stage": "2G",
            "experiment": "timestamp_safe_pre_entry_polymarket_probability_trajectory",
            "candidate_scope": "415 earnings chronological OOF candidates only",
            "source_hashes": {"stage2f_oof_candidates": _hash(OOF_CANDIDATES), "probabilities": _hash(PROBABILITIES)},
            "coverage_gate_passed": bool(decision["coverage_sufficient"]),
            "models_fitted": False,
            "post_entry_observations_used": 0,
            "lockbox_opened": False,
            "outputs": {
                "report": str(report_path),
                "candidate_audit": str(candidate_path),
                "observation_audit": str(observation_path),
                "fold_summary": str(fold_path),
                "coverage_gates": str(gate_path),
                "leakage_audit": str(leakage_path),
                "prediction_status": str(statuses["prediction_status"]),
                "exact_replay_status": str(statuses["replay_status"]),
                "paired_fold_status": str(statuses["paired_status"]),
                "final_decision": str(decision_path),
            },
        },
    )
    return {
        "report": report_path,
        "candidate_audit": candidate_path,
        "observation_audit": observation_path,
        "fold_summary": fold_path,
        "coverage_gates": gate_path,
        "leakage_audit": leakage_path,
        "prediction_status": statuses["prediction_status"],
        "exact_replay_status": statuses["replay_status"],
        "paired_fold_status": statuses["paired_status"],
        "final_decision": decision_path,
        "manifest": manifest_path,
    }


if __name__ == "__main__":
    outputs = run_stage2g()
    for name, path in outputs.items():
        print(f"{name}: {path}")
