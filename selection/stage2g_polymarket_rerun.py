"""Stage 2G rerun using freshly downloaded minute Polymarket histories.

The experiment is earnings-only and development-only.  It preserves the five
Stage 2F outer validation assignments, uses the same small ridge/logistic model
framework and inner chronological search, and replays all three selectors under
the eight frozen Stage 2E exits for SPY and QQQ.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import balanced_accuracy_score, brier_score_loss, roc_auc_score

from analysis.download_stage2g_polymarket_history import decision_timestamp
from backtesting.optimize_cem import PORT_DEFAULT
from core.polarity import resolve_polarity
from selection.stage2c_research import QUALITY_FEATURES, _attach_semantic_event_rank
from selection.stage2e_path_aware import EXIT_POLICIES, PRICES, PROBS
from selection.stage2f_family_selection import (
    TASKS,
    _as_bool,
    _attach_exit_plan,
    _build_exit_plans,
    _exact_replay,
    _fit_predict_bundle,
    _load_development,
    _safe_spearman,
    _semantic_static_accept,
)


PROJECT = Path(__file__).resolve().parents[1]
STAGE2F = PROJECT / "data" / "selection_stage2f"
DOWNLOAD = PROJECT / "data" / "selection_stage2g" / "polymarket_download"
HISTORY = DOWNLOAD / "polymarket_probability_history.csv"
DOWNLOAD_MANIFEST = DOWNLOAD / "polymarket_download_manifest.json"
ORACLE = STAGE2F / "oracle_labels" / "full_path_oracle_labels.csv"
STAGE2F_OOF = STAGE2F / "nested_oof_models" / "stage2f_oof_predictions.csv"
OUTPUT = PROJECT / "data" / "selection_stage2g" / "polymarket_rerun"

ENTRY_THRESHOLD = float(PORT_DEFAULT["enter_floor"])
MATERIAL_DRAWDOWN_TOLERANCE_PCT = 1.0

VARIANTS = (
    "A_target_b_baseline",
    "B_target_b_plus_trajectory",
    "C_probability_trajectory_only_diagnostic",
)

SECTOR_ETF = {
    "Consumer Cyclical": "XLY",
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Consumer Defensive": "XLP",
    "Industrials": "XLI",
    "Communication Services": "XLC",
    "Healthcare": "XLV",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Basic Materials": "XLB",
    "Unknown": "SPY",
}

TRAJECTORY_FEATURES = (
    "traj_log_observation_count",
    "traj_log_span_hours",
    "traj_probability_latest",
    "traj_slope_full_per_day",
    "traj_slope_24h_per_day",
    "traj_acceleration_24h_per_day2",
    "traj_persistence_above_threshold_full",
    "traj_persistence_above_threshold_24h",
    "traj_upward_crossings",
    "traj_downward_recrossings",
    "traj_log_hours_since_first_crossing",
    "traj_log_hours_since_last_crossing",
    "traj_recent_peak_24h",
    "traj_drawdown_from_peak_24h",
    "traj_recovery_from_trough_24h",
    "traj_probability_volatility_24h",
    "traj_update_frequency_per_hour_24h",
    "traj_probability_change_6h",
    "traj_probability_change_24h",
    "traj_change_24h_minus_stock_1d",
    "traj_change_24h_minus_sector_1d",
    "traj_change_24h_minus_benchmark_1d",
)

FEATURES_BY_VARIANT = {
    "A_target_b_baseline": tuple(QUALITY_FEATURES),
    "B_target_b_plus_trajectory": tuple(QUALITY_FEATURES) + TRAJECTORY_FEATURES,
    "C_probability_trajectory_only_diagnostic": TRAJECTORY_FEATURES,
}

PREFIX = {
    "A_target_b_baseline": "A",
    "B_target_b_plus_trajectory": "B",
    "C_probability_trajectory_only_diagnostic": "C",
}


def _json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _slope_per_day(timestamps: pd.Series, values: np.ndarray) -> float:
    if len(values) < 2:
        return np.nan
    x = (timestamps.astype("int64").to_numpy(dtype=float) - float(timestamps.astype("int64").iloc[0])) / 86_400e9
    if np.ptp(x) <= 1e-12:
        return np.nan
    return float(np.polyfit(x, values, 1)[0])


def _asof_probability(timestamps: pd.Series, values: np.ndarray, target: pd.Timestamp) -> float:
    position = int(timestamps.searchsorted(target, side="right")) - 1
    return float(values[position]) if position >= 0 else np.nan


def _prior_close_return(prices: dict, symbol: str, entry_date: pd.Timestamp) -> float:
    entry_date = pd.Timestamp(entry_date)
    if entry_date.tzinfo is None:
        entry_date = entry_date.tz_localize("UTC")
    else:
        entry_date = entry_date.tz_convert("UTC")
    closes = []
    for bar in prices.get(symbol, []):
        date = pd.Timestamp(bar[0])
        if date.tzinfo is None:
            date = date.tz_localize("UTC")
        else:
            date = date.tz_convert("UTC")
        if date.normalize() < entry_date.normalize():
            closes.append(float(bar[4] if len(bar) >= 5 else bar[-1]))
    if len(closes) < 2 or closes[-2] == 0:
        return np.nan
    return float(closes[-1] / closes[-2] - 1.0)


def _trajectory_row(row: pd.Series, path: pd.DataFrame, prices: dict) -> dict[str, Any]:
    cutoff = decision_timestamp(row["entry_date"])
    t0 = pd.to_datetime(row["t0"], errors="coerce", utc=True)
    selected = path[(path["source_ts_utc"] >= t0) & (path["source_ts_utc"] < cutoff)].sort_values("source_ts_utc")
    if selected.empty:
        raise AssertionError(f"No strict pre-entry path for {row['stage2e_candidate_id']}")
    if not (selected["source_ts_utc"] < cutoff).all():
        raise AssertionError("Stage 2G feature path contains a point at or after entry")
    timestamps = selected["source_ts_utc"].reset_index(drop=True)
    raw = pd.to_numeric(selected["probability_yes"], errors="coerce").to_numpy(dtype=float)
    polarity, polarity_source = resolve_polarity(str(row["question"]), str(row["symbol"]))
    if polarity == 0:
        raise AssertionError(f"No effective probability polarity for earnings candidate {row['stage2e_candidate_id']}")
    values = raw if polarity == 1 else 1.0 - raw
    latest = float(values[-1])
    span_hours = (timestamps.iloc[-1] - timestamps.iloc[0]).total_seconds() / 3600.0

    recent24_mask = timestamps >= cutoff - pd.Timedelta(hours=24)
    recent24_ts = timestamps[recent24_mask].reset_index(drop=True)
    recent24 = values[recent24_mask.to_numpy()]
    recent12_mask = timestamps >= cutoff - pd.Timedelta(hours=12)
    prior12_mask = (timestamps >= cutoff - pd.Timedelta(hours=24)) & (timestamps < cutoff - pd.Timedelta(hours=12))
    recent12_slope = _slope_per_day(timestamps[recent12_mask].reset_index(drop=True), values[recent12_mask.to_numpy()])
    prior12_slope = _slope_per_day(timestamps[prior12_mask].reset_index(drop=True), values[prior12_mask.to_numpy()])
    acceleration = (recent12_slope - prior12_slope) / 0.5 if np.isfinite(recent12_slope) and np.isfinite(prior12_slope) else np.nan

    above = values >= ENTRY_THRESHOLD
    upward = int(np.sum((~above[:-1]) & above[1:])) if len(above) > 1 else 0
    downward = int(np.sum(above[:-1] & (~above[1:]))) if len(above) > 1 else 0
    crossing_positions = np.flatnonzero((~above[:-1]) & above[1:]) + 1 if len(above) > 1 else np.array([], dtype=int)
    if not len(crossing_positions) and above[0]:
        crossing_positions = np.array([0], dtype=int)
    first_cross_hours = (cutoff - timestamps.iloc[int(crossing_positions[0])]).total_seconds() / 3600 if len(crossing_positions) else np.nan
    last_cross_hours = (cutoff - timestamps.iloc[int(crossing_positions[-1])]).total_seconds() / 3600 if len(crossing_positions) else np.nan

    peak24 = float(np.max(recent24))
    trough24 = float(np.min(recent24))
    probability_change_6h = latest - _asof_probability(timestamps, values, cutoff - pd.Timedelta(hours=6))
    probability_change_24h = latest - _asof_probability(timestamps, values, cutoff - pd.Timedelta(hours=24))
    stock_return = _prior_close_return(prices, str(row["symbol"]), pd.Timestamp(row["entry_date"]))
    sector_symbol = SECTOR_ETF.get(str(row.get("feat_sector", "Unknown")), "SPY")
    sector_return = _prior_close_return(prices, sector_symbol, pd.Timestamp(row["entry_date"]))
    benchmark_return = _prior_close_return(prices, str(row["benchmark"]), pd.Timestamp(row["entry_date"]))
    changes24 = np.diff(recent24)
    recent24_span = max((recent24_ts.iloc[-1] - recent24_ts.iloc[0]).total_seconds() / 3600.0, 1.0 / 60.0)

    return {
        "stage2e_candidate_id": row["stage2e_candidate_id"],
        "decision_ts_utc": cutoff,
        "path_first_source_ts_utc": timestamps.iloc[0],
        "path_last_source_ts_utc": timestamps.iloc[-1],
        "latest_observation_age_seconds": (cutoff - timestamps.iloc[-1]).total_seconds(),
        "strict_pre_entry_observations": len(values),
        "strict_pre_entry_span_hours": span_hours,
        "polarity": polarity,
        "polarity_source": polarity_source,
        "post_entry_observations_used": 0,
        "sector_etf": sector_symbol,
        "stock_prior_session_return": stock_return,
        "sector_prior_session_return": sector_return,
        "benchmark_prior_session_return": benchmark_return,
        "traj_log_observation_count": float(np.log1p(len(values))),
        "traj_log_span_hours": float(np.log1p(max(span_hours, 0.0))),
        "traj_probability_latest": latest,
        "traj_slope_full_per_day": _slope_per_day(timestamps, values),
        "traj_slope_24h_per_day": _slope_per_day(recent24_ts, recent24),
        "traj_acceleration_24h_per_day2": acceleration,
        "traj_persistence_above_threshold_full": float(np.mean(above)),
        "traj_persistence_above_threshold_24h": float(np.mean(recent24 >= ENTRY_THRESHOLD)),
        "traj_upward_crossings": upward,
        "traj_downward_recrossings": downward,
        "traj_log_hours_since_first_crossing": float(np.log1p(first_cross_hours)) if np.isfinite(first_cross_hours) else np.nan,
        "traj_log_hours_since_last_crossing": float(np.log1p(last_cross_hours)) if np.isfinite(last_cross_hours) else np.nan,
        "traj_recent_peak_24h": peak24,
        "traj_drawdown_from_peak_24h": peak24 - latest,
        "traj_recovery_from_trough_24h": latest - trough24,
        "traj_probability_volatility_24h": float(np.std(recent24)),
        "traj_update_frequency_per_hour_24h": float(np.sum(np.abs(changes24) > 1e-12) / recent24_span),
        "traj_probability_change_6h": probability_change_6h,
        "traj_probability_change_24h": probability_change_24h,
        "traj_change_24h_minus_stock_1d": probability_change_24h - stock_return if np.isfinite(probability_change_24h) and np.isfinite(stock_return) else np.nan,
        "traj_change_24h_minus_sector_1d": probability_change_24h - sector_return if np.isfinite(probability_change_24h) and np.isfinite(sector_return) else np.nan,
        "traj_change_24h_minus_benchmark_1d": probability_change_24h - benchmark_return if np.isfinite(probability_change_24h) and np.isfinite(benchmark_return) else np.nan,
    }


def _load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    development = _load_development()
    development = development[development["event_family"].eq("earnings")].copy()
    oracle = pd.read_csv(ORACLE)
    oracle["event_family"] = oracle["event_family"].astype(str).str.lower()
    data = development.merge(
        oracle,
        on=["stage2e_candidate_id", "benchmark", "symbol", "event_family", "mapping_type"],
        how="inner",
        validate="one_to_one",
    )
    if len(data) != 492:
        raise AssertionError(f"Expected 492 labeled earnings development rows, found {len(data)}")
    data = _attach_semantic_event_rank(data)
    history = pd.read_csv(HISTORY, dtype={"market_id": str}, parse_dates=["source_ts_utc", "available_at_utc"])
    prices = pickle.loads(PRICES.read_bytes())
    paths = {market_id: group.sort_values("source_ts_utc") for market_id, group in history.groupby("market_id", sort=False)}
    feature_rows = []
    for _, row in data.iterrows():
        path = paths.get(str(row["market_id"]))
        if path is None:
            raise AssertionError(f"Downloaded history missing market {row['market_id']}")
        feature_rows.append(_trajectory_row(row, path, prices))
    features = pd.DataFrame(feature_rows)
    data = data.merge(features, on="stage2e_candidate_id", how="inner", validate="one_to_one")
    current_oof = pd.read_csv(
        STAGE2F_OOF,
        usecols=["stage2e_candidate_id", "outer_fold", "oof_predicted_target_b_slot"],
    )
    if len(current_oof) != 415:
        raise AssertionError("Frozen Stage 2F OOF assignment no longer has 415 rows")
    return data, features, current_oof, prices


def _outer_training(data: pd.DataFrame, validation: pd.DataFrame) -> pd.DataFrame:
    validation_start = pd.to_datetime(validation["entry_date"], utc=True).min()
    event_end = data.groupby("economic_event_id")["entry_date"].max()
    training_events = set(event_end[event_end < validation_start].index.astype(str))
    training = data[data["economic_event_id"].astype(str).isin(training_events)].copy()
    if set(training["economic_event_id"].astype(str)) & set(validation["economic_event_id"].astype(str)):
        raise AssertionError("Stage 2G event episode crossed an outer fold boundary")
    if training["entry_date"].max() >= validation["entry_date"].min():
        raise AssertionError("Stage 2G outer training reaches validation time")
    return training


def _nested_oof(data: pd.DataFrame, current_oof: pd.DataFrame, output_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    parts: list[pd.DataFrame] = []
    fold_rows: list[dict[str, Any]] = []
    model_records: list[dict[str, Any]] = []
    for fold in sorted(current_oof["outer_fold"].unique()):
        assignment = current_oof[current_oof["outer_fold"].eq(fold)]
        validation = data[data["stage2e_candidate_id"].isin(assignment["stage2e_candidate_id"])].copy()
        validation = validation.merge(assignment, on="stage2e_candidate_id", how="left", validate="one_to_one")
        training = _outer_training(data, validation)
        scored = validation.copy()
        selected_specs: dict[str, Any] = {}
        for variant in VARIANTS:
            prefix = PREFIX[variant]
            predicted, specs, records = _fit_predict_bundle(
                training,
                scored,
                FEATURES_BY_VARIANT[variant],
                prefix,
                variant,
                "earnings",
                int(fold),
            )
            prediction_columns = [column for column in predicted.columns if column.startswith(f"{prefix}_")]
            for column in prediction_columns:
                scored[column] = predicted[column].to_numpy()
            selected_specs[variant] = specs
            model_records.extend(records)
        parts.append(scored)
        fold_rows.append(
            {
                "outer_fold": int(fold),
                "training_start": training["entry_date"].min(),
                "training_end": training["entry_date"].max(),
                "validation_start": validation["entry_date"].min(),
                "validation_end": validation["entry_date"].max(),
                "training_rows": len(training),
                "validation_rows": len(validation),
                "training_events": training["economic_event_id"].nunique(),
                "validation_events": validation["economic_event_id"].nunique(),
                "selected_specs": json.dumps(selected_specs, default=str, sort_keys=True),
            }
        )
        print(f"[stage2g] outer fold {fold}: train={len(training)} validation={len(validation)}", flush=True)
    oof = pd.concat(parts, ignore_index=True)
    if len(oof) != 415 or oof["stage2e_candidate_id"].duplicated().any():
        raise AssertionError("Stage 2G OOF assembly did not reproduce 415 unique validation rows")
    model_dir = output_dir / "nested_oof_models"
    model_dir.mkdir(parents=True, exist_ok=True)
    oof.to_csv(model_dir / "stage2g_oof_predictions.csv", index=False)
    folds = pd.DataFrame(fold_rows)
    folds.to_csv(model_dir / "outer_fold_manifest.csv", index=False)
    (model_dir / "model_records.json").write_text(json.dumps(model_records, indent=2, default=str) + "\n", encoding="utf-8")
    return oof, folds, model_records


def _prediction_metrics(oof: pd.DataFrame, output_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    aggregate_rows: list[dict[str, Any]] = []
    fold_rows: list[dict[str, Any]] = []
    for variant in VARIANTS:
        prefix = PREFIX[variant]
        for task_name, spec in TASKS.items():
            target = spec["target"]
            prediction = f"{prefix}_{task_name}"
            for fold, frame in [("all", oof), *[(str(key), group) for key, group in oof.groupby("outer_fold")]]:
                y = pd.to_numeric(frame[target], errors="coerce")
                p = pd.to_numeric(frame[prediction], errors="coerce")
                keep = y.notna() & p.notna()
                y, p = y[keep], p[keep]
                row = {
                    "model": variant,
                    "task": task_name,
                    "target": target,
                    "outer_fold": fold,
                    "observations": len(y),
                    "independent_events": frame.loc[keep, "economic_event_id"].nunique(),
                    "auc": np.nan,
                    "balanced_accuracy": np.nan,
                    "brier": np.nan,
                    "mae": np.nan,
                    "spearman": np.nan,
                }
                if spec["kind"] == "binary" and len(y) and y.nunique() >= 2:
                    threshold_col = f"{prediction}_threshold"
                    threshold = float(pd.to_numeric(frame.loc[keep, threshold_col], errors="coerce").median())
                    row["auc"] = float(roc_auc_score(y.astype(int), p))
                    row["balanced_accuracy"] = float(balanced_accuracy_score(y.astype(int), p >= threshold))
                    row["brier"] = float(brier_score_loss(y.astype(int), np.clip(p, 0.0, 1.0)))
                elif spec["kind"] == "regression" and len(y):
                    row["mae"] = float(np.mean(np.abs(y - p)))
                    row["spearman"] = _safe_spearman(y, p)
                (aggregate_rows if fold == "all" else fold_rows).append(row)
    root = output_dir / "prediction"
    root.mkdir(parents=True, exist_ok=True)
    aggregate = pd.DataFrame(aggregate_rows)
    by_fold = pd.DataFrame(fold_rows)
    aggregate.to_csv(root / "oof_prediction_metrics.csv", index=False)
    by_fold.to_csv(root / "oof_prediction_metrics_by_fold.csv", index=False)
    paired = by_fold.pivot_table(index=["outer_fold", "task"], columns="model", values=["auc", "balanced_accuracy", "mae", "spearman"]).reset_index()
    paired.columns = ["_".join(str(part) for part in column if part) if isinstance(column, tuple) else str(column) for column in paired.columns]
    paired.to_csv(root / "paired_fold_prediction_metrics.csv", index=False)
    return aggregate, by_fold


def _high_failure(row: pd.Series, prefix: str) -> bool:
    for task in ("never_profitable_probability", "persistent_loss_probability", "severe_adverse_probability"):
        probability = pd.to_numeric(row.get(f"{prefix}_{task}"), errors="coerce")
        threshold = pd.to_numeric(row.get(f"{prefix}_{task}_threshold"), errors="coerce")
        if np.isfinite(probability) and np.isfinite(threshold) and probability >= threshold:
            return True
    return False


def _rank_variant(frame: pd.DataFrame, variant: str) -> pd.DataFrame:
    out = frame.copy()
    out["_selector_rank"] = 10**9
    prefix = PREFIX[variant]
    for _, day in out.groupby(["benchmark", "analysis_split", "entry_date"], sort=False):
        ranked = day.copy()
        if variant == "A_target_b_baseline":
            order = ranked.sort_values(
                ["oof_predicted_target_b_slot", "expected_slot_days", "source_order", "symbol"],
                ascending=[False, True, True, True],
                kind="mergesort",
            ).index
        else:
            order = ranked.sort_values(
                [f"{prefix}_opportunity_probability", f"{prefix}_expected_best_legal_return", f"{prefix}_expected_time_to_opportunity", "oof_predicted_target_b_slot", "source_order", "symbol"],
                ascending=[False, False, True, False, True, True],
                kind="mergesort",
            ).index
        out.loc[order, "_selector_rank"] = np.arange(len(order), dtype=int)
    out["_selector_rank"] = out["_selector_rank"].astype(int)
    baseline_accept = pd.to_numeric(out["oof_predicted_target_b_slot"], errors="coerce").gt(0.0)
    if variant == "A_target_b_baseline":
        accept = baseline_accept
    elif variant == "B_target_b_plus_trajectory":
        accept = baseline_accept & ~out.apply(lambda row: _high_failure(row, "B"), axis=1)
    else:
        accept = ~out.apply(lambda row: _high_failure(row, "C"), axis=1)
    accept &= out.apply(_semantic_static_accept, axis=1)
    out["_admission_score"] = pd.to_numeric(out["oof_predicted_target_b_slot"], errors="coerce")
    out["stage2f_policy"] = variant
    out["stage2f_policy_accept"] = accept
    return out


def _same_day(oof: pd.DataFrame, output_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    eligible = oof[oof.apply(_semantic_static_accept, axis=1)].copy()
    for (benchmark, date), group in eligible.groupby(["benchmark", "entry_date"], sort=True):
        if len(group) < 2:
            continue
        oracle = group.loc[pd.to_numeric(group["best_legal_net_active_return_pct"], errors="coerce").idxmax()]
        for variant in VARIANTS:
            ranked = _rank_variant(group, variant).sort_values("_selector_rank")
            survivors = ranked[ranked["stage2f_policy_accept"].map(_as_bool)]
            selected = survivors.iloc[0] if not survivors.empty else None
            value = float(selected["best_legal_net_active_return_pct"]) if selected is not None else 0.0
            rows.append(
                {
                    "outer_fold": int(group["outer_fold"].iloc[0]),
                    "benchmark": benchmark,
                    "entry_date": date,
                    "model": variant,
                    "candidate_count": len(group),
                    "admitted": selected is not None,
                    "selected_candidate_id": selected["stage2e_candidate_id"] if selected is not None else "",
                    "selected_best_legal_net_active_return_pct": value,
                    "selected_never_profitable": bool(selected["never_profitable_after_costs"]) if selected is not None else False,
                    "selected_persistent_loser": bool(selected["persistent_loser"]) if selected is not None else False,
                    "selected_severe_adverse": bool(selected["severe_adverse_before_meaningful_gain"]) if selected is not None else False,
                    "oracle_best_legal_net_active_return_pct": float(oracle["best_legal_net_active_return_pct"]),
                    "same_day_oracle_regret_pct": float(oracle["best_legal_net_active_return_pct"]) - value,
                    "selected_oracle_exactly": bool(selected is not None and selected["stage2e_candidate_id"] == oracle["stage2e_candidate_id"]),
                }
            )
    decisions = pd.DataFrame(rows)
    summary = decisions.groupby(["model", "benchmark"], as_index=False).agg(
        same_day_decisions=("entry_date", "size"),
        admission_rate=("admitted", "mean"),
        mean_selected_best_legal_net_active_pct=("selected_best_legal_net_active_return_pct", "mean"),
        selected_never_profitable_rate=("selected_never_profitable", "mean"),
        selected_persistent_loser_rate=("selected_persistent_loser", "mean"),
        selected_severe_adverse_rate=("selected_severe_adverse", "mean"),
        mean_same_day_oracle_regret_pct=("same_day_oracle_regret_pct", "mean"),
        oracle_exact_hit_rate=("selected_oracle_exactly", "mean"),
    )
    root = output_dir / "same_day_selection"
    root.mkdir(parents=True, exist_ok=True)
    decisions.to_csv(root / "same_day_selection_decisions.csv", index=False)
    summary.to_csv(root / "same_day_selection_summary.csv", index=False)
    fold_summary = decisions.groupby(["outer_fold", "model"], as_index=False).agg(
        decisions=("entry_date", "size"),
        mean_selected_best_legal_net_active_pct=("selected_best_legal_net_active_return_pct", "mean"),
        selected_never_profitable_rate=("selected_never_profitable", "mean"),
        selected_persistent_loser_rate=("selected_persistent_loser", "mean"),
        mean_same_day_oracle_regret_pct=("same_day_oracle_regret_pct", "mean"),
    )
    fold_summary.to_csv(root / "same_day_selection_by_fold.csv", index=False)
    return decisions, summary


def _replay(oof: pd.DataFrame, prices: dict, output_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    plans = _build_exit_plans(oof, output_dir)
    probs = pickle.loads(PROBS.read_bytes())
    combined_rows: list[dict[str, Any]] = []
    fold_rows: list[dict[str, Any]] = []
    trade_parts: list[pd.DataFrame] = []
    replay_dir = output_dir / "exact_replay"
    for variant in VARIANTS:
        ranked = _rank_variant(oof, variant)
        for exit_policy in EXIT_POLICIES:
            planned = _attach_exit_plan(ranked, plans, exit_policy)
            for benchmark in ("SPY", "QQQ"):
                result, trades = _exact_replay(
                    planned,
                    prices,
                    probs,
                    benchmark,
                    variant,
                    exit_policy,
                    replay_dir / "combined" / variant / exit_policy,
                    {"evaluation_scope": "combined_stage2g_oof_exact_replay"},
                )
                combined_rows.append(result)
                if not trades.empty:
                    detail = trades.copy()
                    detail["selector_policy"] = variant
                    detail["exit_policy"] = exit_policy
                    detail["benchmark"] = benchmark
                    trade_parts.append(detail)
            for fold, fold_frame in planned.groupby("outer_fold", sort=True):
                for benchmark in ("SPY", "QQQ"):
                    result, _ = _exact_replay(
                        fold_frame,
                        prices,
                        probs,
                        benchmark,
                        variant,
                        exit_policy,
                        None,
                        {"evaluation_scope": "stage2g_outer_fold_exact_replay", "outer_fold": int(fold)},
                    )
                    fold_rows.append(result)
        print(f"[stage2g] exact replay complete: {variant}", flush=True)
    combined = pd.DataFrame(combined_rows)
    folds = pd.DataFrame(fold_rows)
    trades = pd.concat(trade_parts, ignore_index=True) if trade_parts else pd.DataFrame()
    combined.to_csv(replay_dir / "selector_exit_combined_exact_results.csv", index=False)
    folds.to_csv(replay_dir / "selector_exit_outer_fold_exact_results.csv", index=False)
    trades.to_csv(replay_dir / "selector_exit_trade_detail.csv", index=False)
    return combined, folds


def _paired_results(
    same_day: pd.DataFrame,
    combined: pd.DataFrame,
    folds: pd.DataFrame,
    output_dir: Path,
) -> dict[str, pd.DataFrame]:
    baseline = "A_target_b_baseline"
    candidate = "B_target_b_plus_trajectory"
    exit_pairs = combined[combined["selector_policy"].isin([baseline, candidate])].pivot_table(
        index=["benchmark", "exit_policy"], columns="selector_policy", values=["excess_return", "active_max_drawdown_pct"]
    )
    exit_pairs.columns = [f"{metric}_{model}" for metric, model in exit_pairs.columns]
    exit_pairs = exit_pairs.reset_index()
    exit_pairs["paired_excess_improvement"] = exit_pairs[f"excess_return_{candidate}"] - exit_pairs[f"excess_return_{baseline}"]
    exit_pairs["paired_drawdown_change_pct"] = exit_pairs[f"active_max_drawdown_pct_{candidate}"] - exit_pairs[f"active_max_drawdown_pct_{baseline}"]

    fold_pairs = folds[folds["selector_policy"].isin([baseline, candidate])].pivot_table(
        index=["outer_fold", "benchmark", "exit_policy"], columns="selector_policy", values=["excess_return", "active_max_drawdown_pct"]
    )
    fold_pairs.columns = [f"{metric}_{model}" for metric, model in fold_pairs.columns]
    fold_pairs = fold_pairs.reset_index()
    fold_pairs["paired_excess_improvement"] = fold_pairs[f"excess_return_{candidate}"] - fold_pairs[f"excess_return_{baseline}"]
    fold_pairs["paired_drawdown_change_pct"] = fold_pairs[f"active_max_drawdown_pct_{candidate}"] - fold_pairs[f"active_max_drawdown_pct_{baseline}"]
    fold_summary = fold_pairs.groupby("outer_fold", as_index=False).agg(
        mean_paired_excess_improvement=("paired_excess_improvement", "mean"),
        median_paired_excess_improvement=("paired_excess_improvement", "median"),
        mean_paired_drawdown_change_pct=("paired_drawdown_change_pct", "mean"),
    )

    same_pairs = same_day[same_day["model"].isin([baseline, candidate])].pivot_table(
        index=["benchmark"], columns="model", values=[
            "mean_selected_best_legal_net_active_pct",
            "selected_never_profitable_rate",
            "selected_persistent_loser_rate",
            "mean_same_day_oracle_regret_pct",
        ]
    )
    same_pairs.columns = [f"{metric}_{model}" for metric, model in same_pairs.columns]
    same_pairs = same_pairs.reset_index()
    root = output_dir / "paired_comparison"
    root.mkdir(parents=True, exist_ok=True)
    exit_pairs.to_csv(root / "paired_exit_policy_comparison.csv", index=False)
    fold_pairs.to_csv(root / "paired_outer_fold_cells.csv", index=False)
    fold_summary.to_csv(root / "paired_outer_fold_summary.csv", index=False)
    same_pairs.to_csv(root / "paired_same_day_comparison.csv", index=False)
    return {"exit": exit_pairs, "fold_cells": fold_pairs, "fold": fold_summary, "same_day": same_pairs}


def _decision(
    paired: dict[str, pd.DataFrame],
    combined: pd.DataFrame,
    output_dir: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    baseline = "A_target_b_baseline"
    candidate = "B_target_b_plus_trajectory"
    same = paired["same_day"]
    regret_improved = bool((same[f"mean_same_day_oracle_regret_pct_{candidate}"] < same[f"mean_same_day_oracle_regret_pct_{baseline}"]).any())
    best_improved = bool((same[f"mean_selected_best_legal_net_active_pct_{candidate}"] > same[f"mean_selected_best_legal_net_active_pct_{baseline}"]).any())
    never_improved = bool((same[f"selected_never_profitable_rate_{candidate}"] < same[f"selected_never_profitable_rate_{baseline}"]).any())
    persistent_improved = bool((same[f"selected_persistent_loser_rate_{candidate}"] < same[f"selected_persistent_loser_rate_{baseline}"]).any())
    same_day_gate = regret_improved or best_improved or never_improved or persistent_improved

    fold_cells = paired["fold_cells"]
    paired_median = float(fold_cells["paired_excess_improvement"].median())
    paired_median_gate = paired_median > 0.0
    fold_summary = paired["fold"]
    positive_folds = int(fold_summary["median_paired_excess_improvement"].gt(0.0).sum())
    leave_one_out = []
    for fold in sorted(fold_cells["outer_fold"].unique()):
        leave_one_out.append(float(fold_cells.loc[~fold_cells["outer_fold"].eq(fold), "paired_excess_improvement"].median()))
    not_one_fold_gate = positive_folds >= 3 and min(leave_one_out) > 0.0

    portfolio = combined.groupby(["selector_policy", "benchmark"], as_index=False).agg(
        mean_excess_return=("excess_return", "mean"),
        mean_active_drawdown_pct=("active_max_drawdown_pct", "mean"),
    )
    pivot = portfolio[portfolio["selector_policy"].isin([baseline, candidate])].pivot(
        index="benchmark", columns="selector_policy", values="mean_excess_return"
    )
    benchmark_deltas = pivot[candidate] - pivot[baseline]
    both_benchmarks_gate = bool((benchmark_deltas > 0.0).all())
    exit_mean = paired["exit"].groupby("exit_policy")["paired_excess_improvement"].mean()
    worse_exit_count = int(exit_mean.lt(0.0).sum())
    exit_gate = worse_exit_count <= 3
    drawdown_by_benchmark = paired["exit"].groupby("benchmark")["paired_drawdown_change_pct"].mean()
    drawdown_gate = bool((drawdown_by_benchmark >= -MATERIAL_DRAWDOWN_TOLERANCE_PCT).all())

    gates = pd.DataFrame(
        [
            ("same_day_opportunity_ranking_or_bad_trade_rejection", same_day_gate, {"regret": regret_improved, "best": best_improved, "never": never_improved, "persistent": persistent_improved}),
            ("positive_paired_median_fold_improvement", paired_median_gate, paired_median),
            ("portfolio_improves_spy_and_qqq", both_benchmarks_gate, benchmark_deltas.to_dict()),
            ("not_worse_in_more_than_three_exits", exit_gate, {"worse_exit_policies": worse_exit_count, "limit": 3}),
            ("not_driven_by_one_fold", not_one_fold_gate, {"positive_folds": positive_folds, "leave_one_fold_out_medians": leave_one_out}),
            ("drawdown_not_materially_worse", drawdown_gate, {"mean_change_by_benchmark": drawdown_by_benchmark.to_dict(), "tolerance_pct": MATERIAL_DRAWDOWN_TOLERANCE_PCT}),
        ],
        columns=["promotion_gate", "passed", "observed"],
    )
    promoted = bool(gates["passed"].all())
    decision = {
        "final_decision": "promote_probability_trajectory_selector" if promoted else "close_earnings_selection_and_proceed_to_exits",
        "promoted": promoted,
        "selected_operational_selector": candidate if promoted else baseline,
        "coverage_sufficient": True,
        "paired_median_fold_excess_improvement": paired_median,
        "positive_folds": positive_folds,
        "benchmark_mean_excess_improvement": benchmark_deltas.to_dict(),
        "worse_exit_policy_count": worse_exit_count,
        "mean_drawdown_change_by_benchmark": drawdown_by_benchmark.to_dict(),
        "lockbox_opened": False,
        "geo_and_other_included": False,
    }
    root = output_dir / "decision"
    root.mkdir(parents=True, exist_ok=True)
    gates.to_csv(root / "promotion_gates.csv", index=False)
    _json(root / "final_decision.json", decision)
    return gates, decision


def _markdown_table(frame: pd.DataFrame, columns: list[str], digits: int = 4) -> str:
    view = frame.loc[:, columns].copy()
    for column in view.columns:
        if pd.api.types.is_float_dtype(view[column]):
            view[column] = view[column].map(lambda value: "" if pd.isna(value) else f"{value:.{digits}f}")
    headers = [str(column) for column in view.columns]
    rows = [[str(value) for value in row] for row in view.itertuples(index=False, name=None)]
    return "\n".join([
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(row) + " |" for row in rows),
    ])


def _report(
    features: pd.DataFrame,
    fold_manifest: pd.DataFrame,
    metrics: pd.DataFrame,
    same_day: pd.DataFrame,
    combined: pd.DataFrame,
    paired: dict[str, pd.DataFrame],
    gates: pd.DataFrame,
    decision: dict[str, Any],
    output_dir: Path,
) -> Path:
    coverage = features.merge(
        pd.read_csv(STAGE2F_OOF, usecols=["stage2e_candidate_id", "outer_fold"]),
        on="stage2e_candidate_id",
        how="inner",
    )
    coverage_fold = coverage.groupby("outer_fold", as_index=False).agg(
        candidates=("stage2e_candidate_id", "size"),
        min_observations=("strict_pre_entry_observations", "min"),
        median_observations=("strict_pre_entry_observations", "median"),
        median_span_hours=("strict_pre_entry_span_hours", "median"),
        max_latest_age_seconds=("latest_observation_age_seconds", "max"),
        post_entry_observations_used=("post_entry_observations_used", "sum"),
    )
    portfolio = combined.groupby(["selector_policy", "benchmark"], as_index=False).agg(
        mean_excess_return=("excess_return", "mean"),
        mean_active_information_ratio=("active_information_ratio", "mean"),
        mean_active_drawdown_pct=("active_max_drawdown_pct", "mean"),
        mean_trades=("n_trades", "mean"),
    )
    path = output_dir / "stage2g_polymarket_trajectory_rerun_report.md"
    path.write_text(
        "\n".join(
            [
                "# Stage 2G — Polymarket Probability-Trajectory Rerun",
                "",
                "## 1. Coverage and leakage audit",
                "",
                "Fresh public CLOB histories were downloaded at one-minute fidelity. Features use only "
                "`source_ts < scheduled NYSE entry close`; the 2025-11-28 early close is handled at 13:00 America/New_York.",
                "",
                _markdown_table(coverage_fold, list(coverage_fold.columns)),
                "",
                f"All 415 OOF candidates have at least {int(coverage['strict_pre_entry_observations'].min())} observations; "
                f"median coverage is {coverage['strict_pre_entry_observations'].median():.0f} points. "
                f"Post-entry observations used: {int(coverage['post_entry_observations_used'].sum())}.",
                "",
                "## 2. OOF opportunity and failure prediction",
                "",
                _markdown_table(metrics[metrics["task"].isin(["opportunity_probability", "never_profitable_probability", "persistent_loss_probability", "severe_adverse_probability"])], ["model", "task", "observations", "auc", "balanced_accuracy", "brier"]),
                "",
                "## 3. Same-day selection",
                "",
                _markdown_table(same_day, ["model", "benchmark", "same_day_decisions", "admission_rate", "mean_selected_best_legal_net_active_pct", "selected_never_profitable_rate", "selected_persistent_loser_rate", "mean_same_day_oracle_regret_pct"]),
                "",
                "## 4. Selector × exit exact replay",
                "",
                _markdown_table(portfolio, list(portfolio.columns)),
                "",
                "Paired B minus A results by exit and benchmark:",
                "",
                _markdown_table(paired["exit"], ["benchmark", "exit_policy", "paired_excess_improvement", "paired_drawdown_change_pct"]),
                "",
                "## 5. Paired chronological folds",
                "",
                _markdown_table(paired["fold"], list(paired["fold"].columns)),
                "",
                "## 6. Promotion decision",
                "",
                _markdown_table(gates, ["promotion_gate", "passed", "observed"]),
                "",
                f"**Final decision: {decision['final_decision']}.**",
                "",
                f"Operational selector: `{decision['selected_operational_selector']}`. The lockbox remained sealed; geo and other catalysts were excluded.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def run(output_dir: Path | str = OUTPUT) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    download_manifest = json.loads(DOWNLOAD_MANIFEST.read_text(encoding="utf-8"))
    if download_manifest["complete_markets"] != 273 or download_manifest["failed_markets"] != 0:
        raise AssertionError("Stage 2G Polymarket download is incomplete")
    data, features, current_oof, prices = _load_data()
    coverage_dir = output_dir / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)
    features.to_csv(coverage_dir / "candidate_trajectory_features_and_leakage_audit.csv", index=False)
    if features["post_entry_observations_used"].sum() != 0:
        raise AssertionError("Stage 2G trajectory builder used post-entry observations")
    oof, fold_manifest, model_records = _nested_oof(data, current_oof, output_dir)
    metrics, fold_metrics = _prediction_metrics(oof, output_dir)
    same_day_decisions, same_day_summary = _same_day(oof, output_dir)
    combined, replay_folds = _replay(oof, prices, output_dir)
    paired = _paired_results(same_day_summary, combined, replay_folds, output_dir)
    gates, decision = _decision(paired, combined, output_dir)
    report = _report(features, fold_manifest, metrics, same_day_summary, combined, paired, gates, decision, output_dir)
    manifest = output_dir / "stage2g_polymarket_rerun_manifest.json"
    _json(
        manifest,
        {
            "stage": "2G",
            "experiment": "fresh_polymarket_minute_trajectory_vs_target_b",
            "development_earnings_candidates": len(data),
            "oof_candidates": len(oof),
            "outer_folds": int(oof["outer_fold"].nunique()),
            "trajectory_features": list(TRAJECTORY_FEATURES),
            "models": list(VARIANTS),
            "exit_policies": list(EXIT_POLICIES),
            "combined_exact_replays": len(combined),
            "outer_fold_exact_replays": len(replay_folds),
            "post_entry_observations_used": 0,
            "lockbox_opened": False,
            "final_decision": decision,
            "source_hashes": {
                "downloaded_history": _hash(HISTORY),
                "download_manifest": _hash(DOWNLOAD_MANIFEST),
                "stage2f_oracle_labels": _hash(ORACLE),
                "stage2f_oof_assignments": _hash(STAGE2F_OOF),
                "prices": _hash(PRICES),
                "execution_probabilities": _hash(PROBS),
            },
            "outputs": {
                "report": str(report),
                "oof_predictions": str(output_dir / "nested_oof_models" / "stage2g_oof_predictions.csv"),
                "prediction_metrics": str(output_dir / "prediction" / "oof_prediction_metrics.csv"),
                "same_day_summary": str(output_dir / "same_day_selection" / "same_day_selection_summary.csv"),
                "combined_exact_replay": str(output_dir / "exact_replay" / "selector_exit_combined_exact_results.csv"),
                "fold_exact_replay": str(output_dir / "exact_replay" / "selector_exit_outer_fold_exact_results.csv"),
                "promotion_gates": str(output_dir / "decision" / "promotion_gates.csv"),
                "final_decision": str(output_dir / "decision" / "final_decision.json"),
            },
        },
    )
    return {"report": report, "manifest": manifest, "decision": output_dir / "decision" / "final_decision.json"}


if __name__ == "__main__":
    for name, path in run().items():
        print(f"{name}: {path}")
