"""Transparent sequential admission policies for Stage 2B.

Ranking and admission are deliberately separate.  The ranker orders a legal
same-day candidate set; an admission policy decides whether the next ranked
candidate is worth consuming an available slot.  Thresholds are selected on
chronological training OOF rows only.  The current 2026 rows are never used
by this module for policy choice.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from .connection_tiebreakers import _prepare, _tie_order


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_OOF = PROJECT / "data" / "selection_stage2b" / "ranking_models" / "monotonic_oof.csv"
DEFAULT_FULL = PROJECT / "data" / "selection_stage1" / "decision_candidates.csv"
DEFAULT_OUTPUT = PROJECT / "data" / "selection_stage2b" / "admission"

PRIMARY_TARGET = "te1_active_net_return_pct"
TARGET_B_SLOT = "active_return_per_slot_day_pct"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for column in ("entry_date", "t_e", "te1_exit_date"):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce", utc=True).dt.normalize()
    numeric = (
        "feat_connection_strength",
        "connection_strength",
        "entry_prob",
        "feat_time_to_resolution_days",
        "slot_days",
        "capacity_slots",
        "free_slots_before",
        PRIMARY_TARGET,
        TARGET_B_SLOT,
        "score_monotonic",
    )
    for column in numeric:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "feat_connection_strength" not in frame and "connection_strength" in frame:
        frame["feat_connection_strength"] = frame["connection_strength"]
    frame["expected_slot_days"] = frame.get(
        "feat_time_to_resolution_days", frame.get("slot_days", np.nan)
    )
    frame["expected_slot_days"] = pd.to_numeric(frame["expected_slot_days"], errors="coerce")
    frame["target_b_slot"] = frame.get(TARGET_B_SLOT, np.nan)
    frame["target_b_sqrt"] = pd.to_numeric(frame[PRIMARY_TARGET], errors="coerce") / np.sqrt(
        np.maximum(frame["expected_slot_days"].fillna(1.0), 1.0)
    )
    # For the transparent admission baseline, the monotonic score is the
    # frozen OOF prediction.  Target-B predicted scores are deliberately simple
    # predeclared transformations rather than a second tuned model.
    score = pd.to_numeric(frame.get("score_monotonic", np.nan), errors="coerce")
    frame["predicted_target_a"] = score
    frame["predicted_target_b_slot"] = score / np.maximum(frame["expected_slot_days"], 1.0)
    frame["predicted_target_b_sqrt"] = score / np.sqrt(np.maximum(frame["expected_slot_days"], 1.0))
    return frame


def _legal_order(group: pd.DataFrame) -> list[Any]:
    prepared = _prepare(group.copy())
    # The chosen deterministic connection baseline is the first-stage ranker.
    # The explicit expected-slot-days tie-breaker was selected on training OOF.
    return _tie_order(prepared, "expected_slot_days", seed=0)


def _capacity(group: pd.DataFrame) -> int:
    for column in ("free_slots_before", "capacity_slots"):
        if column in group:
            values = pd.to_numeric(group[column], errors="coerce").dropna()
            if not values.empty:
                return max(int(round(float(values.iloc[0]))), 0)
    return 0


def _value(row: pd.Series, policy_name: str) -> float:
    if policy_name == "min_connection_strength":
        return float(row.get("feat_connection_strength", np.nan))
    if policy_name == "min_predicted_target_a":
        return float(row.get("predicted_target_a", np.nan))
    if policy_name == "min_predicted_target_b_slot":
        return float(row.get("predicted_target_b_slot", np.nan))
    if policy_name == "min_predicted_target_b_sqrt":
        return float(row.get("predicted_target_b_sqrt", np.nan))
    raise ValueError(f"Policy {policy_name!r} has no threshold value")


def _threshold_grid(frame: pd.DataFrame, policy_name: str) -> list[float | None]:
    if policy_name == "always_fill":
        return [None]
    if policy_name == "min_connection_strength":
        return [0.5, 0.75, 0.9, 1.0]
    column = {
        "min_predicted_target_a": "predicted_target_a",
        "min_predicted_target_b_slot": "predicted_target_b_slot",
        "min_predicted_target_b_sqrt": "predicted_target_b_sqrt",
    }[policy_name]
    values = pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        return [0.0]
    # The grid is fixed before looking at any held-out/test outcome.  Quantiles
    # are estimated from training OOF predictions only.
    return [float(values.quantile(q)) for q in (0.10, 0.25, 0.50, 0.75, 0.90)]


def evaluate_policy(frame: pd.DataFrame, policy_name: str, threshold: float | None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Evaluate a transparent sequential policy on legal training OOF groups."""
    rows: list[dict[str, Any]] = []
    selected_values: list[pd.Series] = []
    grouped = frame.groupby(["benchmark", "entry_date"], sort=True, dropna=False)
    for (benchmark, entry_date), group in grouped:
        order = _legal_order(group)
        free_slots = _capacity(group)
        accepted = 0
        for rank, index in enumerate(order, start=1):
            row = group.loc[index]
            value = _value(row, policy_name) if policy_name != "always_fill" else np.nan
            if accepted >= free_slots:
                decision = "blocked"
                reason = "capacity_exhausted"
            elif policy_name == "always_fill":
                decision = "accepted"
                reason = "always_fill"
            elif not np.isfinite(value) or value < float(threshold):
                decision = "rejected"
                reason = "below_threshold" if np.isfinite(value) else "missing_score"
            else:
                decision = "accepted"
                reason = "threshold_pass"
            if decision == "accepted":
                accepted += 1
                selected_values.append(row)
            rows.append(
                {
                    "policy": policy_name,
                    "threshold": threshold,
                    "benchmark": benchmark,
                    "entry_date": entry_date,
                    "symbol": row.get("symbol", ""),
                    "event_family": row.get("event_family", ""),
                    "same_day_rank": rank,
                    "free_slots_before": free_slots,
                    "decision": decision,
                    "decision_reason": reason,
                    "connection_strength": row.get("feat_connection_strength", np.nan),
                    "predicted_target_a": row.get("predicted_target_a", np.nan),
                    "predicted_target_b_slot": row.get("predicted_target_b_slot", np.nan),
                    "predicted_target_b_sqrt": row.get("predicted_target_b_sqrt", np.nan),
                    PRIMARY_TARGET: row.get(PRIMARY_TARGET, np.nan),
                    TARGET_B_SLOT: row.get(TARGET_B_SLOT, np.nan),
                    "te1_exit_date": row.get("te1_exit_date", pd.NaT),
                    "t_e": row.get("t_e", pd.NaT),
                }
            )
    decisions = pd.DataFrame(rows)
    if decisions.empty:
        return decisions, {"policy": policy_name, "threshold": threshold, "n_groups": 0, "n_candidates": 0}
    accepted_frame = decisions[decisions["decision"].eq("accepted")]
    active = pd.to_numeric(accepted_frame[PRIMARY_TARGET], errors="coerce")
    b_slot = pd.to_numeric(accepted_frame[TARGET_B_SLOT], errors="coerce")
    metrics = {
        "policy": policy_name,
        "threshold": threshold,
        "n_groups": int(decisions[["benchmark", "entry_date"]].drop_duplicates().shape[0]),
        "n_candidates": int(len(decisions)),
        "accepted": int((decisions["decision"] == "accepted").sum()),
        "rejected": int((decisions["decision"] == "rejected").sum()),
        "blocked": int((decisions["decision"] == "blocked").sum()),
        "acceptance_rate_pct": float((decisions["decision"] == "accepted").mean() * 100.0),
        "mean_target_a_pct": float(active.mean()) if active.notna().any() else np.nan,
        "median_target_a_pct": float(active.median()) if active.notna().any() else np.nan,
        "mean_target_b_slot_pct": float(b_slot.mean()) if b_slot.notna().any() else np.nan,
        "median_target_b_slot_pct": float(b_slot.median()) if b_slot.notna().any() else np.nan,
        "downside_q10_target_a_pct": float(active.quantile(0.10)) if active.notna().any() else np.nan,
        "downside_q10_target_b_slot_pct": float(b_slot.quantile(0.10)) if b_slot.notna().any() else np.nan,
        "te1_horizon_assertion": bool(
            pd.to_datetime(decisions["te1_exit_date"], utc=True, errors="coerce")
            .lt(pd.to_datetime(decisions["t_e"], utc=True, errors="coerce"))
            .fillna(False)
            .all()
        ),
    }
    return decisions, metrics


def _choose(summary: pd.DataFrame) -> dict[str, Any]:
    # Predeclared objective: maximize mean Target A among policies with at
    # least 20% of always-fill's accepted rows.  Target B is reported, not
    # substituted after seeing the exploratory test.
    fill = summary.loc[summary["policy"].eq("always_fill"), "accepted"]
    minimum = 0.20 * float(fill.iloc[0]) if not fill.empty else 0.0
    eligible = summary.loc[summary["accepted"] >= minimum].copy()
    eligible["_score"] = eligible["mean_target_a_pct"].fillna(-np.inf)
    eligible = eligible.sort_values(["_score", "accepted", "threshold"], ascending=[False, False, True], kind="mergesort")
    if eligible.empty:
        return {"policy": "always_fill", "threshold": None, "selection_reason": "fallback_no_eligible_threshold"}
    row = eligible.iloc[0]
    threshold = row["threshold"]
    return {
        "policy": str(row["policy"]),
        "threshold": None if pd.isna(threshold) else float(threshold),
        "selection_reason": "max_train_oof_mean_target_a_with_minimum_fill",
        "minimum_accepted_rows": minimum,
    }


def _choose_family(family: pd.DataFrame, fill: pd.DataFrame) -> dict[str, Any]:
    """Choose a threshold inside one policy family, using the same rule."""
    combined = pd.concat([fill, family], ignore_index=True)
    chosen = _choose(combined)
    if chosen.get("policy") == "always_fill" and not family.empty:
        eligible = family[family["accepted"] >= 0.20 * float(fill["accepted"].iloc[0])].copy()
        if not eligible.empty:
            eligible = eligible.sort_values(
                ["mean_target_a_pct", "accepted", "threshold"],
                ascending=[False, False, True],
                kind="mergesort",
            )
            row = eligible.iloc[0]
            chosen = {
                "policy": str(row["policy"]),
                "threshold": None if pd.isna(row["threshold"]) else float(row["threshold"]),
                "selection_reason": "max_train_oof_mean_target_a_with_minimum_fill_within_family",
                "minimum_accepted_rows": 0.20 * float(fill["accepted"].iloc[0]),
            }
    return chosen


def _choices_for_full(full: pd.DataFrame, choices: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for policy_name, choice in choices.items():
        if policy_name not in {
            "always_fill",
            "min_connection_strength",
            "min_predicted_target_a",
            "min_predicted_target_b_slot",
            "min_predicted_target_b_sqrt",
        }:
            continue
        decision, _ = evaluate_policy(full, policy_name, choice.get("threshold"))
        if not decision.empty:
            decision = decision.copy()
            decision["choice_scope"] = "training_oof_choice_applied_to_full_candidate_audit"
            rows.append(decision)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def make_admission_policy(policy_name: str, threshold: float | None = None) -> Callable[[dict, pd.Timestamp, dict[str, Any]], str]:
    """Return an engine-compatible policy callback."""
    def decide(trade: dict, _day: pd.Timestamp, context: dict[str, Any]) -> str:
        if int(context.get("free_slots", 0)) <= 0:
            return "reject"
        if policy_name == "always_fill":
            return "accept"
        if policy_name == "min_connection_strength":
            value = trade.get("_admission_score", np.nan)
        elif policy_name == "min_predicted_target_a":
            value = trade.get("_admission_score", np.nan)
        elif policy_name == "min_predicted_target_b_slot":
            value = trade.get("_admission_target", np.nan)
        elif policy_name == "min_predicted_target_b_sqrt":
            value = trade.get("_admission_target", np.nan)
        else:
            raise ValueError(f"Unknown admission policy: {policy_name}")
        return "accept" if np.isfinite(float(value)) and float(value) >= float(threshold) else "reject"
    return decide


def run_admission_experiments(
    oof_path: Path | str = DEFAULT_OOF,
    full_path: Path | str = DEFAULT_FULL,
    output_dir: Path | str = DEFAULT_OUTPUT,
) -> dict[str, Path]:
    oof_path, full_path, output_dir = Path(oof_path), Path(full_path), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    oof = _read(oof_path)
    oof = oof[oof.get("analysis_split", "train").astype(str).str.lower().eq("train")].copy()
    if oof.empty:
        raise ValueError("No training OOF rows available for admission selection")
    policies = ("always_fill", "min_connection_strength", "min_predicted_target_a", "min_predicted_target_b_slot", "min_predicted_target_b_sqrt")
    detail_rows, summary_rows = [], []
    for policy_name in policies:
        for threshold in _threshold_grid(oof, policy_name):
            detail, metrics = evaluate_policy(oof, policy_name, threshold)
            if not detail.empty:
                detail["evaluation_scope"] = "chronological_training_oof"
                detail_rows.append(detail)
            summary_rows.append(metrics)
    detail = pd.concat(detail_rows, ignore_index=True)
    summary = pd.DataFrame(summary_rows)
    choices: dict[str, Any] = {}
    fill_summary = summary[summary["policy"].eq("always_fill")].copy()
    for policy_name in policies:
        family = summary[summary["policy"].eq(policy_name)]
        choices[policy_name] = _choose_family(family, fill_summary)
    # Store every family's best threshold as an auditable frozen candidate,
    # while the global best is the one used by the initial dynamic replay.
    for policy_name in policies:
        family = summary[summary["policy"].eq(policy_name)].copy()
        if family.empty:
            continue
        family = family.sort_values(["mean_target_a_pct", "accepted", "threshold"], ascending=[False, False, True], kind="mergesort")
        row = family.iloc[0]
        choices[policy_name]["family_best_threshold"] = None if pd.isna(row["threshold"]) else float(row["threshold"])
        choices[policy_name]["family_best_mean_target_a_pct"] = float(row["mean_target_a_pct"])
    family_candidates = [
        choice for name, choice in choices.items() if name != "always_fill" and choice.get("policy") == name
    ]
    if family_candidates:
        selected_choice = sorted(
            family_candidates,
            key=lambda item: (
                float(item.get("family_best_mean_target_a_pct", -np.inf)),
                float(item.get("threshold", -np.inf)) if item.get("threshold") is not None else -np.inf,
            ),
            reverse=True,
        )[0]
    else:
        selected_choice = choices["always_fill"]
    choices["selected_for_initial_dynamic_replay"] = selected_choice
    choice_path = output_dir / "admission_choices.json"
    detail_path = output_dir / "admission_oof_detail.csv"
    summary_path = output_dir / "admission_oof_summary.csv"
    decisions_path = output_dir / "admission_decisions.csv"
    detail.to_csv(detail_path, index=False)
    summary.to_csv(summary_path, index=False)
    full = _read(full_path)
    score_path = PROJECT / "data" / "selection_stage2b" / "ranking_models" / "monotonic_scores.csv"
    if score_path.exists() and ("score_monotonic" not in full or full["score_monotonic"].isna().all()):
        scores = _read(score_path)
        score_cols = ["benchmark", "analysis_split", "entry_date", "symbol", "score_monotonic"]
        scores = scores[[c for c in score_cols if c in scores.columns]].drop_duplicates(
            ["benchmark", "analysis_split", "entry_date", "symbol"]
        )
        full = full.drop(columns=["score_monotonic"], errors="ignore").merge(
            scores, on=["benchmark", "analysis_split", "entry_date", "symbol"], how="left"
        )
        full["predicted_target_a"] = pd.to_numeric(full["score_monotonic"], errors="coerce")
        full["predicted_target_b_slot"] = full["predicted_target_a"] / np.maximum(full["expected_slot_days"], 1.0)
        full["predicted_target_b_sqrt"] = full["predicted_target_a"] / np.sqrt(np.maximum(full["expected_slot_days"], 1.0))
    full_decisions = _choices_for_full(full, choices)
    full_decisions.to_csv(decisions_path, index=False)
    choice_path.write_text(json.dumps(choices, indent=2, default=str) + "\n", encoding="utf-8")
    manifest = {
        "label": "sequential_admission_stage2b",
        "oof_sha256": _hash(oof_path),
        "full_candidate_sha256": _hash(full_path),
        "policies": list(policies),
        "primary_target": PRIMARY_TARGET,
        "reported_target_b": ["active_return_per_slot_day_pct", "active_return_per_sqrt_slot_day"],
        "threshold_selection": "chronological training OOF only; current 2026 test excluded",
        "minimum_fill_rule": "at least 20 percent of always-fill accepted rows",
        "te_is_never_exit": True,
        "terminal_horizon_assertion": bool((pd.to_datetime(oof["te1_exit_date"], utc=True, errors="coerce") < pd.to_datetime(oof["t_e"], utc=True, errors="coerce")).all()),
        "outputs": {
            "oof_detail": str(detail_path),
            "oof_summary": str(summary_path),
            "choices": str(choice_path),
            "decisions": str(decisions_path),
        },
    }
    manifest_path = output_dir / "admission_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return {"detail": detail_path, "summary": summary_path, "choices": choice_path, "decisions": decisions_path, "manifest": manifest_path}


if __name__ == "__main__":
    for name, path in run_admission_experiments().items():
        print(f"{name}: {path}")
