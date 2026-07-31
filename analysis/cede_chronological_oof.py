"""Fixed five-fold chronological OOF score test for CEDE earnings.

The existing Stage 2F outer-fold assignment is reused.  For a held-out fold,
training contains only economic events whose decision is strictly earlier than
the first validation decision.  CEDE probability/price features are likewise
strictly pre-decision.  There is one L2 logistic and one ridge model per fold
and benchmark, with no hyperparameter search.

This script evaluates the selected cohort against existing full-path labels.
Those labels are research targets, not CEDE live features, and they are not a
replacement for the separate exact CEDE exit replay.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from core.features import SECTOR_ETFS
from core.polarity import resolve_polarity
from selection.causal_event_dislocation import CEDEConfig, cluster_event_legs, score_and_admit
from selection.cede_event_map import build_canonical_event_map, load_policy
from selection.cede_pipeline import (
    PriceBook,
    _attach_expanding_thresholds,
    _coverage_summary,
    _load_probability_paths,
    _probability_features,
    _session_close,
)
from selection.stage2f_family_selection import _load_development


PROJECT = Path(__file__).resolve().parents[1]
STAGE2F_OOF = PROJECT / "data" / "selection_stage2f" / "nested_oof_models" / "stage2f_oof_predictions.csv"
ORACLE = PROJECT / "data" / "selection_stage2f" / "oracle_labels" / "full_path_oracle_labels.csv"
OUTPUT = PROJECT / "data" / "cede" / "oof_20260717"
EXTENDED_EARNINGS_HISTORY = PROJECT / "data" / "cede" / "extended_earnings_probability_download" / "extended_probability_history.csv"
FEATURES = (
    "event_probability", "event_delta_logit", "event_agreement", "probability_z",
    "dislocation", "abnormal_return_1d", "abnormal_return_2d", "rv20_pct",
    "business_days_to_event_end", "mapping_confidence",
)
COST = 0.0025


def _utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _days_to(end: Any, decision: Any) -> int:
    return int(np.busday_count(_utc(decision).date(), _utc(end).date()))


def _raw_oof_candidates() -> tuple[pd.DataFrame, pd.DataFrame]:
    # The 415-row OOF file deliberately omits the earliest development block:
    # those rows are train-only for the first outer fold.  Reconstruct the
    # complete 492-row labelled development table, then attach the OOF fold
    # only to rows that were actually held out.
    source = _load_development()
    oracle = pd.read_csv(ORACLE)
    source = source.merge(
        oracle,
        on=["stage2e_candidate_id", "benchmark", "symbol", "event_family", "mapping_type"],
        how="inner", validate="one_to_one",
    )
    assignments = pd.read_csv(STAGE2F_OOF, usecols=["stage2e_candidate_id", "outer_fold"])
    source = source.merge(assignments, on="stage2e_candidate_id", how="left", validate="one_to_one")
    source["market_id"] = source["market_id"].astype(str)
    source = source[source["event_family"].eq("earnings")].copy()
    source["decision_ts_utc"] = source["entry_date"].map(_session_close)
    source["polarity"] = [resolve_polarity(str(q), str(s))[0] for q, s in zip(source["question"], source["symbol"], strict=True)]
    source["mapping_valid"] = source["mapping_valid"].fillna(False).astype(bool)
    source["sector_etf"] = source["feat_sector"].astype(str).map(SECTOR_ETFS).fillna("SPY")
    raw = source[[
        "economic_event_id", "event_family", "market_id", "symbol", "question", "t0", "t_e",
        "decision_ts_utc", "polarity", "mapping_valid", "mapping_confidence", "mapping_type", "sector_etf",
    ]].drop_duplicates()
    source["trade_event_id"] = source["economic_event_id"].astype(str) + "@" + source["entry_date"].dt.date.astype(str)
    return raw, source


def _feature_events(raw: pd.DataFrame, earnings_history: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    event_map, canonical_legs, mapping_issues = build_canonical_event_map(raw, load_policy())
    prices = PriceBook.from_default_file()
    price_rows = []
    for _, event in event_map.iterrows():
        components = json.loads(event["components_json"])
        price_rows.append({"trade_event_id": event["trade_event_id"], **prices.features(components, str(event["hedge"]), _utc(event["decision_ts_utc"]))})
    legs = canonical_legs.merge(pd.DataFrame(price_rows), on="trade_event_id", how="left", validate="many_to_one")
    paths = _load_probability_paths(earnings_history=earnings_history)
    probability_rows = [_probability_features(row, paths.get(str(row["market_id"]))) for _, row in legs.iterrows()]
    legs = pd.concat([legs.reset_index(drop=True), pd.DataFrame(probability_rows)], axis=1)
    legs["business_days_to_event_end"] = [
        _days_to(end, decision) for end, decision in zip(legs["market_event_end_utc"], legs["decision_ts_utc"], strict=True)
    ]
    legs = _coverage_summary(legs, type("Cfg", (), {"min_pre_entry_observations": 30, "min_history_hours": 24.0, "max_latest_age_minutes": 180.0})())
    legs["available_at_utc"] = pd.to_datetime(legs.get("path_last_available_at_utc"), errors="coerce", utc=True)
    for column in ("family_delta_logit_q80", "family_dislocation_q80", "family_signed_ar2_q60"):
        legs[column] = np.nan
    events = cluster_event_legs(legs.rename(columns={"trade_event_id": "economic_event_id"}), CEDEConfig()).rename(columns={"economic_event_id": "trade_event_id"})
    events = events.merge(
        event_map,
        on=["trade_event_id", "family", "asset", "hedge", "expected_direction", "mapping_confidence", "decision_ts_utc"],
        how="left", validate="one_to_one",
    )
    coverage = legs.groupby("trade_event_id", as_index=False).agg(
        coverage_sufficient=("coverage_sufficient", "all"),
        min_pre_entry_observations=("strict_pre_entry_observations", "min"),
        min_pre_entry_span_hours=("strict_pre_entry_span_hours", "min"),
        max_latest_observation_age_minutes=("latest_observation_age_minutes", "max"),
        post_decision_observations_used=("post_decision_observations_used", "sum"),
    )
    events = events.merge(coverage, on="trade_event_id", how="left", validate="one_to_one")
    events["timestamp_safe"] = events["timestamp_safe"] & events["coverage_sufficient"].fillna(False)
    events = _attach_expanding_thresholds(events, minimum_events=20)
    events["calibration_available"] = events["prior_family_events"].ge(20)
    return events, legs, mapping_issues


def _model() -> Pipeline:
    return Pipeline([
        ("impute", SimpleImputer(strategy="median", add_indicator=True)),
        ("scale", StandardScaler()),
        ("model", LogisticRegression(C=1.0, penalty="l2", max_iter=2000, random_state=7)),
    ])


def _ridge() -> Pipeline:
    return Pipeline([
        ("impute", SimpleImputer(strategy="median", add_indicator=True)),
        ("scale", StandardScaler()),
        ("model", Ridge(alpha=1.0)),
    ])


def _eligible_for_model(frame: pd.DataFrame) -> pd.Series:
    return frame["coverage_sufficient"].fillna(False) & frame["calibration_available"].fillna(False)


def _fit_fold(train: pd.DataFrame, validation: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    keep = _eligible_for_model(train)
    train = train[keep].copy()
    output = validation.copy()
    record: dict[str, Any] = {"training_rows": len(train), "validation_rows": len(validation), "status": "fitted"}
    if len(train) < 30 or train["ever_profitable_after_costs"].nunique() < 2 or train["persistent_loser"].nunique() < 2:
        output["probability_positive"] = np.nan
        output["expected_positive_return"] = np.nan
        output["probability_loss"] = np.nan
        output["expected_shortfall"] = np.nan
        output["all_in_rotation_cost"] = COST
        output["family_edge_score_q80"] = np.nan
        record["status"] = "insufficient_training_class_balance"
        return output, record
    x_train = train.loc[:, FEATURES]
    x_validation = validation.loc[:, FEATURES]
    positive = _model().fit(x_train, train["ever_profitable_after_costs"].astype(int))
    loss = _model().fit(x_train, train["persistent_loser"].astype(int))
    positive_value = _ridge().fit(x_train, train["best_legal_net_active_return_pct"].clip(lower=0.0) / 100.0)
    shortfall = _ridge().fit(x_train, (-train["terminal_net_active_return_pct"]).clip(lower=0.0) / 100.0)
    output["probability_positive"] = positive.predict_proba(x_validation)[:, 1]
    output["expected_positive_return"] = positive_value.predict(x_validation).clip(min=0.0)
    output["probability_loss"] = loss.predict_proba(x_validation)[:, 1]
    output["expected_shortfall"] = shortfall.predict(x_validation).clip(min=0.0)
    output["all_in_rotation_cost"] = COST
    train_score = (
        positive.predict_proba(x_train)[:, 1] * positive_value.predict(x_train).clip(min=0.0)
        - loss.predict_proba(x_train)[:, 1] * shortfall.predict(x_train).clip(min=0.0) - COST
    )
    output["family_edge_score_q80"] = float(np.quantile(train_score, 0.80))
    record["training_events"] = int(train["trade_event_id"].nunique())
    record["threshold"] = float(np.quantile(train_score, 0.80))
    return output, record


def _admit_scores(frame: pd.DataFrame) -> pd.DataFrame:
    meta_columns = [
        "probability_positive", "expected_positive_return", "probability_loss",
        "expected_shortfall", "all_in_rotation_cost", "family_edge_score_q80",
    ]
    results = []
    for _, group in frame.groupby("benchmark", sort=False):
        candidates = group.dropna(subset=["probability_positive", "probability_loss", "family_edge_score_q80"]).copy()
        base = group.drop(columns=[column for column in ("entry_eligible", "edge_score") if column in group], errors="ignore")
        if candidates.empty:
            base["entry_eligible"] = False
            base["edge_score"] = np.nan
            results.append(base)
            continue
        candidate_input = candidates.drop(columns=[*meta_columns, "economic_event_id"], errors="ignore").rename(columns={"trade_event_id": "economic_event_id"})
        admitted = score_and_admit(
            candidate_input,
            candidates[[
                "trade_event_id", "probability_positive", "expected_positive_return", "probability_loss",
                "expected_shortfall", "all_in_rotation_cost", "family_edge_score_q80",
            ]].rename(columns={"trade_event_id": "economic_event_id"}),
            CEDEConfig(),
        ).rename(columns={"economic_event_id": "trade_event_id"})
        base = base.merge(admitted[["trade_event_id", "entry_eligible", "edge_score"]], on="trade_event_id", how="left", validate="one_to_one")
        base["entry_eligible"] = base["entry_eligible"].fillna(False)
        results.append(base)
    return pd.concat(results, ignore_index=True)


def _evaluate(scored: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    selected = scored[scored["entry_eligible"]].copy()
    rows: list[dict[str, Any]] = []
    for benchmark, group in scored.groupby("benchmark", sort=True):
        for cohort, subset in (("all_oof", group), ("cede_selected", group[group["entry_eligible"]])):
            if subset.empty:
                continue
            rows.append({
                "benchmark": benchmark, "cohort": cohort, "rows": len(subset), "events": subset["trade_event_id"].nunique(),
                "profitable_share": subset["ever_profitable_after_costs"].mean(),
                "persistent_loser_share": subset["persistent_loser"].mean(),
                "mean_best_legal_active_pct": subset["best_legal_net_active_return_pct"].mean(),
                "mean_terminal_active_pct": subset["terminal_net_active_return_pct"].mean(),
                "median_terminal_active_pct": subset["terminal_net_active_return_pct"].median(),
                "mean_active_mae_pct": subset["active_mae_net_pct"].mean(),
            })
    folds = selected.groupby(["benchmark", "outer_fold"], as_index=False).agg(
        rows=("trade_event_id", "size"), events=("trade_event_id", "nunique"),
        profitable_share=("ever_profitable_after_costs", "mean"), persistent_loser_share=("persistent_loser", "mean"),
        mean_terminal_active_pct=("terminal_net_active_return_pct", "mean"),
    )
    return pd.DataFrame(rows), folds


def run(output: Path = OUTPUT, earnings_history: Path = EXTENDED_EARNINGS_HISTORY) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    raw, labels = _raw_oof_candidates()
    if not earnings_history.exists():
        raise FileNotFoundError(f"Run download_cede_extended_earnings_history first: {earnings_history}")
    events, legs, issues = _feature_events(raw, earnings_history)
    oof_labels = labels[labels["outer_fold"].notna()].copy()
    fold_map = oof_labels.groupby("trade_event_id")["outer_fold"].nunique()
    if (fold_map > 1).any():
        raise AssertionError("One CEDE event decision appears in more than one Stage 2F outer fold")
    events = events.merge(oof_labels[["trade_event_id", "outer_fold"]].drop_duplicates(), on="trade_event_id", how="left", validate="one_to_one")
    labels = labels.merge(events, on="trade_event_id", how="inner", validate="many_to_one", suffixes=("", "_cede"))
    parts = []
    records = []
    for benchmark, benchmark_rows in labels.groupby("benchmark", sort=True):
        for fold in sorted(benchmark_rows["outer_fold"].dropna().unique()):
            validation = benchmark_rows[benchmark_rows["outer_fold"].eq(fold)].copy()
            start = validation["decision_ts_utc"].min()
            training = benchmark_rows[benchmark_rows["decision_ts_utc"] < start].copy()
            predicted, record = _fit_fold(training, validation)
            record.update({"benchmark": benchmark, "outer_fold": int(fold), "validation_start": start, "training_end": training["decision_ts_utc"].max() if len(training) else None})
            parts.append(predicted)
            records.append(record)
    scored = _admit_scores(pd.concat(parts, ignore_index=True))
    summary, folds = _evaluate(scored)
    raw.to_csv(output / "oof_raw_candidates.csv", index=False)
    events.to_csv(output / "oof_cede_event_features.csv", index=False)
    legs.to_csv(output / "oof_cede_probability_legs.csv", index=False)
    issues.to_csv(output / "oof_canonical_mapping_rejections.csv", index=False)
    scored.to_csv(output / "oof_cede_predictions_and_selections.csv", index=False)
    pd.DataFrame(records).to_csv(output / "oof_fold_model_manifest.csv", index=False)
    summary.to_csv(output / "oof_label_selection_summary.csv", index=False)
    folds.to_csv(output / "oof_selected_fold_summary.csv", index=False)
    manifest = {
        "scope": "earnings_only", "method": "five_fold_expanding_chronological_oof_fixed_regularized_models",
        "feature_leakage": "strict_pre_decision_probability_and_prior_session_price_only",
        "label_use": "full_path_labels_as_train_targets_only",
        "canonical_events": int(events["trade_event_id"].nunique()), "label_rows": len(scored),
        "selected_rows": int(scored["entry_eligible"].sum()), "selected_events": int(scored.loc[scored["entry_eligible"], "trade_event_id"].nunique()),
        "exact_cede_exit_replay": "not_run_by_this_selection_test",
    }
    (output / "oof_manifest.json").write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
