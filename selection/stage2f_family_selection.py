"""Stage 2F: family-specific trade-opportunity selection.

The branch creates ex-post full-path oracle labels, but models use only
timestamp-safe pre-entry features.  Model and threshold selection is nested
inside chronological development folds.  The later lockbox remains sealed and
no exit model is trained.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import balanced_accuracy_score, brier_score_loss, log_loss, roc_auc_score

from backtesting.optimize_cem import (
    ALLOCATION_FIFO,
    INITIAL_CAPITAL,
    PORT_DEFAULT,
    ib_cost,
    sim_opp_cost,
)
from selection.dynamic_replay import _active_metrics
from selection.stage2c_research import QUALITY_FEATURES, _attach_semantic_event_rank, _fit_quality_model, _predict_quality, _slot_usage, _trade_concentration
from selection.stage2e_path_aware import (
    EXIT_POLICIES,
    PRICES,
    PROBS,
    SEMANTIC_CANDIDATES,
    STAGE2C,
    STAGE3,
    _as_bool,
    _candidate_id,
    _choose_exit,
    _hash,
    _parse_dates,
    _semantic_indirect_accept,
    _turnover,
)


PROJECT = Path(__file__).resolve().parents[1]
STAGE2E = PROJECT / "data" / "selection_stage2e"
OUTPUT = PROJECT / "data" / "selection_stage2f"
PATH_TABLE = STAGE2E / "legal_paths" / "candidate_legal_path_table.csv"
PATH_SUMMARY = STAGE2E / "legal_paths" / "candidate_path_summary.csv"

POLICIES = (
    "A_target_b_baseline",
    "B_opportunity_ranking_only",
    "C_failure_filter_only",
    "D_failure_then_opportunity",
    "E_family_specific_shared_allocator",
)

PENALTIES = (1.0, 5.0, 20.0)
THRESHOLDS = (0.40, 0.50, 0.60)
MIN_FAMILY_EVENTS = 25
MIN_FAMILY_ROWS = 40
STANDARD_LABEL_NOTIONAL = 10_000.0

TASKS = {
    "opportunity_probability": {"target": "reaches_2pct_active_net", "kind": "binary", "role": "opportunity"},
    "expected_best_legal_return": {"target": "best_legal_net_active_return_pct", "kind": "regression", "role": "opportunity"},
    "expected_time_to_opportunity": {"target": "time_to_first_1pct_days", "kind": "regression", "role": "opportunity"},
    "never_profitable_probability": {"target": "never_profitable_after_costs", "kind": "binary", "role": "failure"},
    "persistent_loss_probability": {"target": "persistent_loser", "kind": "binary", "role": "failure"},
    "severe_adverse_probability": {"target": "severe_adverse_before_meaningful_gain", "kind": "binary", "role": "failure"},
}

COMMON_FEATURES = (
    "expected_slot_days",
    "stock_minus_sector_20d",
    "feat_prob_at_trigger",
    "feat_prob_surge_since_t0",
    "feat_prob_volatility",
    "feat_runup_since_t0",
    "feat_asset_2w_trend",
    "feat_sector_1m_trend",
    "feat_spy_2w_trend",
    "feat_beta",
    "feat_log_market_cap",
    "feat_pre_entry_volume_log",
    "candidates_seen_previous_5_trading_days",
    "event_candidates_seen_previous_5_days",
)

FAMILY_FEATURES = {
    "earnings": COMMON_FEATURES,
    "geo": (
        "mapping_confidence", "impact_materiality", "direction_confidence", "exposure_purity",
        "mapping_type_priority", "expected_slot_days", "stock_minus_sector_20d", "event_novelty",
        "feat_prob_at_trigger", "feat_prob_surge_since_t0", "feat_prob_volatility",
        "feat_runup_since_t0", "feat_asset_2w_trend", "feat_sector_1m_trend",
        "event_candidates_seen_previous_5_days",
    ),
    "other": COMMON_FEATURES + ("mapping_confidence", "impact_materiality", "direction_confidence", "exposure_purity"),
}

POOLED_FEATURES = tuple(dict.fromkeys(COMMON_FEATURES + (
    "mapping_confidence", "impact_materiality", "direction_confidence", "exposure_purity", "mapping_type_priority",
)))


def _json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def _stable_key(*values: Any) -> str:
    return hashlib.sha256("|".join(map(str, values)).encode("utf-8")).hexdigest()


def _family(value: Any) -> str:
    text = str(value).strip().lower()
    return "geo" if text in {"geo", "geopolitical", "macro"} else text


def _load_development() -> pd.DataFrame:
    data = _parse_dates(pd.read_csv(SEMANTIC_CANDIDATES))
    if not data["analysis_split"].astype(str).str.lower().eq("train").all():
        raise AssertionError("Stage 2F input contains non-development rows")
    data["event_family"] = data["event_family"].map(_family)
    data["stage2e_candidate_id"] = data.apply(_candidate_id, axis=1)
    if data["stage2e_candidate_id"].duplicated().any():
        raise AssertionError("Stage 2F input contains duplicate stable candidate IDs")
    return data


def _candidate_net_path(group: pd.DataFrame, summary: pd.Series) -> pd.DataFrame:
    path = group.sort_values("legal_holding_day").copy()
    entry_price = float(summary["actual_entry_price"])
    first = path.iloc[0]
    benchmark_entry = float(first["benchmark_close"]) / (1.0 + float(first["benchmark_return_pct"]) / 100.0)
    asset_qty = max(int(STANDARD_LABEL_NOTIONAL / max(entry_price, 1e-12)), 1)
    asset_notional = asset_qty * entry_price
    benchmark_qty = asset_notional / max(benchmark_entry, 1e-12)
    entry_cost = ib_cost(asset_qty, entry_price, False) + ib_cost(benchmark_qty, benchmark_entry, True)
    net_returns = []
    cost_rates = []
    for _, row in path.iterrows():
        close = float(row["stock_close"])
        benchmark_close = float(row["benchmark_close"])
        asset_exit_value = asset_qty * close
        benchmark_rebuy_qty = asset_exit_value / max(benchmark_close, 1e-12)
        exit_cost = ib_cost(asset_qty, close, True) + ib_cost(benchmark_rebuy_qty, benchmark_close, False)
        cost_pct = (entry_cost + exit_cost) / max(asset_notional, 1e-12) * 100.0
        cost_rates.append(cost_pct)
        net_returns.append(float(row["active_return_pct"]) - cost_pct)
    path["round_trip_rotation_cost_pct"] = cost_rates
    path["net_active_return_pct"] = net_returns
    return path


def _oracle_from_path(path: pd.DataFrame, summary: pd.Series) -> dict[str, Any]:
    path = path.sort_values("legal_holding_day")
    net = pd.to_numeric(path["net_active_return_pct"], errors="coerce")
    best_position = int(np.nanargmax(net.to_numpy(dtype=float)))
    best_row = path.iloc[best_position]
    best = float(net.iloc[best_position])
    terminal = float(net.iloc[-1])
    first1 = path.loc[net >= 1.0, "legal_holding_day"]
    first2 = path.loc[net >= 2.0, "legal_holding_day"]
    first1_day = float(first1.iloc[0]) if not first1.empty else np.nan
    first2_day = float(first2.iloc[0]) if not first2.empty else np.nan
    before_best = net.iloc[: best_position + 1]
    if not first1.empty:
        first1_position = int(np.flatnonzero(net.to_numpy(dtype=float) >= 1.0)[0])
        before_meaningful = net.iloc[: first1_position + 1]
    else:
        before_meaningful = net
    first4 = net.iloc[: min(4, len(net))]
    after_first4 = net.iloc[min(4, len(net)):]
    positive_fraction = float((net > 0.0).mean())
    persistent = bool(best < 1.0 and terminal <= -1.0 and positive_fraction <= 0.25)
    early_winner_giveback = bool(first4.max() >= 1.0 and (terminal <= 0.0 or terminal <= 0.25 * float(first4.max())))
    early_loser_recovery = bool(first4.min() <= -2.0 and (not after_first4.empty and after_first4.max() >= 1.0))
    return {
        "stage2e_candidate_id": str(summary.name),
        "benchmark": summary["benchmark"],
        "symbol": summary["symbol"],
        "event_family": _family(summary["event_family"]),
        "mapping_type": summary["mapping_type"],
        "best_legal_net_active_return_pct": best,
        "day_of_best_legal_return": int(best_row["legal_holding_day"]),
        "active_mfe_net_pct": float(net.max()),
        "active_mae_net_pct": float(net.min()),
        "mae_before_best_opportunity_pct": float(before_best.min()),
        "mae_before_meaningful_gain_pct": float(before_meaningful.min()),
        "time_to_first_1pct_days": first1_day,
        "time_to_first_2pct_days": first2_day,
        "ever_profitable_after_costs": bool(best > 0.0),
        "never_profitable_after_costs": bool(best <= 0.0),
        "never_meaningfully_profitable": bool(best < 1.0),
        "reaches_2pct_active_net": bool(best >= 2.0),
        "persistent_loser": persistent,
        "early_winner_later_giveback": early_winner_giveback,
        "early_loser_later_recovery": early_loser_recovery,
        "severe_adverse_before_meaningful_gain": bool(before_meaningful.min() <= -3.0),
        "terminal_net_active_return_pct": terminal,
        "positive_net_active_day_fraction": positive_fraction,
        "legal_holding_days": int(path["legal_holding_day"].max()),
        "mean_round_trip_rotation_cost_pct": float(path["round_trip_rotation_cost_pct"].mean()),
        "oracle_label_role": "ex_post_research_only_never_live_feature",
    }


def _build_oracle_labels(output_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    paths = pd.read_csv(PATH_TABLE)
    summaries = pd.read_csv(PATH_SUMMARY)
    for column in ("entry_date", "candidate_t_e", "path_date"):
        if column in paths:
            paths[column] = pd.to_datetime(paths[column], errors="coerce", utc=True)
    for column in ("actual_entry_date", "legal_te1_exit_date", "reference_exit_date"):
        if column in summaries:
            summaries[column] = pd.to_datetime(summaries[column], errors="coerce", utc=True)
    if paths[["path_date", "candidate_t_e"]].isna().any().any():
        raise AssertionError("Stage 2F legal paths contain missing path_date or T_e values")
    if not (paths["path_date"] < paths["candidate_t_e"]).all():
        raise AssertionError("Stage 2F oracle path includes T_e or a later date; T_e is never an exit")
    summary_lookup = summaries.set_index("stage2e_candidate_id")
    net_paths: list[pd.DataFrame] = []
    labels: list[dict[str, Any]] = []
    for candidate_id, group in paths.groupby("stage2e_candidate_id", sort=False):
        summary = summary_lookup.loc[candidate_id]
        net_path = _candidate_net_path(group, summary)
        net_paths.append(net_path)
        labels.append(_oracle_from_path(net_path, summary))
    net_path_table = pd.concat(net_paths, ignore_index=True)
    oracle = pd.DataFrame(labels)
    label_dir = output_dir / "oracle_labels"
    label_dir.mkdir(parents=True, exist_ok=True)
    net_path_table.to_csv(label_dir / "candidate_legal_net_active_paths.csv", index=False)
    oracle.to_csv(label_dir / "full_path_oracle_labels.csv", index=False)
    _json(label_dir / "oracle_label_manifest.json", {
        "label": "stage2f_full_path_historical_oracle_labels",
        "candidate_rows": len(oracle),
        "path_rows": len(net_path_table),
        "standard_label_notional": STANDARD_LABEL_NOTIONAL,
        "costs": "asset buy/sell plus benchmark sell/rebuy under the corrected IB cost and slippage model",
        "opportunity_thresholds_pct": {"profitable": 0.0, "meaningful": 1.0, "primary_opportunity": 2.0},
        "persistent_loser_rule": "best net active <1%, terminal <=-1%, and <=25% positive net-active days",
        "severe_adverse_rule": "net active MAE <=-3% before first +1% opportunity, or over the full path when +1% is never reached",
        "live_feature_eligible": False,
        "te_is_never_exit": True,
        "lockbox_opened": False,
    })
    return net_path_table, oracle


def _chronological_event_folds(frame: pd.DataFrame, n_splits: int, min_train_fraction: float = 0.20) -> list[tuple[pd.Series, pd.Series, dict[str, Any]]]:
    events = (
        frame.groupby("economic_event_id", as_index=False)
        .agg(event_start=("entry_date", "min"), event_end=("entry_date", "max"), event_family=("event_family", "first"))
        .sort_values(["event_start", "economic_event_id"], kind="mergesort")
    )
    dates = events["event_start"].drop_duplicates().sort_values().tolist()
    if len(dates) < n_splits + 2:
        return []
    event_target = max(int(np.ceil(len(events) * min_train_fraction)), 3)
    cumulative = events.groupby("event_start").size().reindex(dates).cumsum()
    train_end_position = int(np.flatnonzero(cumulative.to_numpy() >= event_target)[0])
    validation_dates = dates[train_end_position + 1:]
    blocks = [list(block) for block in np.array_split(np.asarray(validation_dates, dtype=object), min(n_splits, len(validation_dates))) if len(block)]
    result = []
    for fold, block in enumerate(blocks):
        validation_start = pd.Timestamp(block[0])
        validation_end = pd.Timestamp(block[-1])
        validation_events = events.loc[events["event_start"].isin(block), "economic_event_id"].astype(str)
        training_events = events.loc[events["event_end"] < validation_start, "economic_event_id"].astype(str)
        train_mask = frame["economic_event_id"].astype(str).isin(training_events)
        validation_mask = frame["economic_event_id"].astype(str).isin(validation_events)
        if not train_mask.any() or not validation_mask.any():
            continue
        if set(frame.loc[train_mask, "economic_event_id"].astype(str)) & set(frame.loc[validation_mask, "economic_event_id"].astype(str)):
            raise AssertionError("Stage 2F event episode crossed a fold boundary")
        if frame.loc[train_mask, "entry_date"].max() >= frame.loc[validation_mask, "entry_date"].min():
            raise AssertionError("Stage 2F chronological fold leaked future entries")
        result.append((train_mask, validation_mask, {
            "outer_fold": fold,
            "training_start": frame.loc[train_mask, "entry_date"].min(),
            "training_end": frame.loc[train_mask, "entry_date"].max(),
            "validation_start": frame.loc[validation_mask, "entry_date"].min(),
            "validation_end": frame.loc[validation_mask, "entry_date"].max(),
            "training_rows": int(train_mask.sum()),
            "validation_rows": int(validation_mask.sum()),
            "training_events": int(frame.loc[train_mask, "economic_event_id"].nunique()),
            "validation_events": int(frame.loc[validation_mask, "economic_event_id"].nunique()),
            "validation_family_composition": json.dumps(frame.loc[validation_mask, "event_family"].value_counts().to_dict(), sort_keys=True),
        }))
    return result


def _prepare_matrix(frame: pd.DataFrame, features: tuple[str, ...], preprocessor: dict[str, Any] | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    if preprocessor is None:
        medians, means, scales = [], [], []
        for feature in features:
            values = pd.to_numeric(frame.get(feature), errors="coerce")
            median = float(values.median()) if values.notna().any() else 0.0
            array = values.fillna(median).to_numpy(dtype=float)
            mean = float(np.mean(array))
            scale = float(np.std(array))
            if scale <= 1e-12:
                scale = 1.0
            medians.append(median)
            means.append(mean)
            scales.append(scale)
        preprocessor = {"features": list(features), "medians": medians, "means": means, "scales": scales}
    columns = []
    for index, feature in enumerate(preprocessor["features"]):
        values = pd.to_numeric(frame.get(feature), errors="coerce")
        array = values.fillna(float(preprocessor["medians"][index])).to_numpy(dtype=float)
        columns.append((array - float(preprocessor["means"][index])) / float(preprocessor["scales"][index]))
    return np.column_stack(columns), preprocessor


def _fit_task(frame: pd.DataFrame, features: tuple[str, ...], task_name: str, penalty: float) -> dict[str, Any]:
    spec = TASKS[task_name]
    target = spec["target"]
    y = pd.to_numeric(frame[target], errors="coerce")
    keep = y.notna()
    train = frame.loc[keep]
    y_array = y.loc[keep].to_numpy(dtype=float)
    x, preprocessor = _prepare_matrix(train, features)
    if spec["kind"] == "binary":
        prior = float(np.mean(y_array)) if len(y_array) else 0.5
        if len(y_array) < 20 or len(np.unique(y_array)) < 2 or min(np.bincount(y_array.astype(int), minlength=2)) < 3:
            return {"kind": "constant_binary", "constant": prior, "preprocessor": preprocessor, "penalty": penalty, "training_rows": len(y_array), "target": target}
        estimator = LogisticRegression(C=1.0 / penalty, l1_ratio=0.0, solver="lbfgs", class_weight="balanced", max_iter=1000, random_state=20260717)
        estimator.fit(x, y_array.astype(int))
        return {"kind": "logistic", "estimator": estimator, "preprocessor": preprocessor, "penalty": penalty, "training_rows": len(y_array), "target": target}
    if len(y_array) < 12:
        return {"kind": "constant_regression", "constant": float(np.nanmean(y_array)) if len(y_array) else 0.0, "preprocessor": preprocessor, "penalty": penalty, "training_rows": len(y_array), "target": target}
    lower, upper = np.nanquantile(y_array, [0.05, 0.95])
    clipped = np.clip(y_array, lower, upper)
    estimator = Ridge(alpha=penalty)
    estimator.fit(x, clipped)
    return {"kind": "ridge", "estimator": estimator, "preprocessor": preprocessor, "penalty": penalty, "training_rows": len(y_array), "target": target, "target_clip": [float(lower), float(upper)]}


def _predict_task(model: dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    if model["kind"].startswith("constant"):
        return np.full(len(frame), float(model["constant"]), dtype=float)
    x, _ = _prepare_matrix(frame, tuple(model["preprocessor"]["features"]), model["preprocessor"])
    if model["kind"] == "logistic":
        return model["estimator"].predict_proba(x)[:, 1]
    return model["estimator"].predict(x)


def _model_record(model: dict[str, Any], scope: str, family: str, fold: int, task: str, threshold: float | None) -> dict[str, Any]:
    estimator = model.get("estimator")
    coefficients: list[float]
    intercept: float
    if estimator is None:
        coefficients = [0.0] * len(model["preprocessor"]["features"])
        intercept = float(model.get("constant", 0.0))
    else:
        raw = estimator.coef_[0] if model["kind"] == "logistic" else estimator.coef_
        coefficients = [float(value) for value in np.asarray(raw).ravel()]
        raw_intercept = estimator.intercept_[0] if np.ndim(estimator.intercept_) else estimator.intercept_
        intercept = float(raw_intercept)
    return {
        "scope": scope, "family": family, "outer_fold": fold, "task": task,
        "target": model["target"], "kind": model["kind"], "penalty": model["penalty"],
        "threshold": threshold, "training_rows": model["training_rows"],
        "features": model["preprocessor"]["features"], "medians": model["preprocessor"]["medians"],
        "means": model["preprocessor"]["means"], "scales": model["preprocessor"]["scales"],
        "coefficients": coefficients, "intercept": intercept,
    }


def _binary_threshold(y: np.ndarray, probability: np.ndarray, role: str) -> float:
    best_threshold = 0.50
    best_score = np.inf
    y = y.astype(int)
    for threshold in THRESHOLDS:
        prediction = probability >= threshold
        false_negative = int(((y == 1) & ~prediction).sum())
        false_positive = int(((y == 0) & prediction).sum())
        if role == "failure":
            score = (2.0 * false_negative + false_positive) / max(len(y), 1)
        else:
            positives = max(int((y == 1).sum()), 1)
            negatives = max(int((y == 0).sum()), 1)
            score = 0.5 * false_negative / positives + 0.5 * false_positive / negatives
        if score < best_score - 1e-12 or (abs(score - best_score) <= 1e-12 and threshold > best_threshold):
            best_score = score
            best_threshold = threshold
    return float(best_threshold)


def _select_specs(frame: pd.DataFrame, features: tuple[str, ...]) -> dict[str, dict[str, float]]:
    inner_folds = _chronological_event_folds(frame, 3, min_train_fraction=0.35)
    selected: dict[str, dict[str, float]] = {}
    for task_name, spec in TASKS.items():
        predictions_by_penalty: dict[float, tuple[np.ndarray, np.ndarray]] = {}
        scores: dict[float, float] = {}
        for penalty in PENALTIES:
            y_parts, prediction_parts = [], []
            for train_mask, validation_mask, _meta in inner_folds:
                training = frame.loc[train_mask]
                validation = frame.loc[validation_mask]
                model = _fit_task(training, features, task_name, penalty)
                prediction = _predict_task(model, validation)
                y = pd.to_numeric(validation[spec["target"]], errors="coerce").to_numpy(dtype=float)
                keep = np.isfinite(y) & np.isfinite(prediction)
                y_parts.extend(y[keep].tolist())
                prediction_parts.extend(prediction[keep].tolist())
            y_array = np.asarray(y_parts, dtype=float)
            prediction_array = np.asarray(prediction_parts, dtype=float)
            predictions_by_penalty[penalty] = (y_array, prediction_array)
            if len(y_array) == 0:
                scores[penalty] = np.inf
            elif spec["kind"] == "binary":
                clipped = np.clip(prediction_array, 1e-6, 1.0 - 1e-6)
                scores[penalty] = float(log_loss(y_array.astype(int), clipped, labels=[0, 1]))
            else:
                scores[penalty] = float(np.mean(np.abs(y_array - prediction_array)))
        finite = [penalty for penalty in PENALTIES if np.isfinite(scores[penalty])]
        penalty = min(finite, key=lambda value: (scores[value], value)) if finite else 5.0
        threshold = np.nan
        if spec["kind"] == "binary":
            y_array, prediction_array = predictions_by_penalty[penalty]
            threshold = _binary_threshold(y_array, prediction_array, spec["role"]) if len(y_array) else 0.50
        selected[task_name] = {"penalty": float(penalty), "threshold": float(threshold) if np.isfinite(threshold) else np.nan, "inner_loss": float(scores.get(penalty, np.nan))}
    return selected


def _fit_predict_bundle(
    training: pd.DataFrame,
    validation: pd.DataFrame,
    features: tuple[str, ...],
    prefix: str,
    scope: str,
    family: str,
    fold: int,
) -> tuple[pd.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    specs = _select_specs(training, features)
    output = validation.copy()
    records: list[dict[str, Any]] = []
    for task_name, task_spec in TASKS.items():
        selected = specs[task_name]
        model = _fit_task(training, features, task_name, selected["penalty"])
        output[f"{prefix}_{task_name}"] = _predict_task(model, validation)
        if task_spec["kind"] == "binary":
            output[f"{prefix}_{task_name}_threshold"] = selected["threshold"]
        records.append(_model_record(model, scope, family, fold, task_name, selected.get("threshold")))
    return output, specs, records


def _family_supported(training: pd.DataFrame) -> tuple[bool, dict[str, Any]]:
    events = int(training["economic_event_id"].nunique())
    rows = int(len(training))
    class_counts = {}
    binary_supported = True
    for task_name, spec in TASKS.items():
        if spec["kind"] != "binary":
            continue
        counts = pd.to_numeric(training[spec["target"]], errors="coerce").dropna().astype(int).value_counts().to_dict()
        class_counts[task_name] = counts
        if len(counts) < 2 or min(counts.values()) < 5:
            binary_supported = False
    supported = rows >= MIN_FAMILY_ROWS and events >= MIN_FAMILY_EVENTS and binary_supported
    return supported, {"rows": rows, "independent_events": events, "binary_class_counts": class_counts, "minimum_rows": MIN_FAMILY_ROWS, "minimum_events": MIN_FAMILY_EVENTS}


def _neutral_family_predictions(validation: pd.DataFrame, family: str) -> pd.DataFrame:
    output = validation.copy()
    expected_time = pd.to_numeric(output.get("expected_slot_days"), errors="coerce").fillna(10.0).clip(lower=1.0)
    output["family_opportunity_probability"] = 0.50
    output["family_expected_best_legal_return"] = 0.0
    output["family_expected_time_to_opportunity"] = expected_time
    for task_name in ("never_profitable_probability", "persistent_loss_probability", "severe_adverse_probability"):
        output[f"family_{task_name}"] = 0.0
        output[f"family_{task_name}_threshold"] = 1.0
    output["family_opportunity_probability_threshold"] = 1.0
    output["family_model_status"] = f"exploratory_{family}_transparent_fallback"
    return output


def _nested_oof_models(data: pd.DataFrame, output_dir: Path) -> dict[str, Any]:
    folds = _chronological_event_folds(data, 5, min_train_fraction=0.20)
    if len(folds) < 3:
        raise AssertionError("Stage 2F requires at least three chronological outer folds")
    oof_parts: list[pd.DataFrame] = []
    fold_rows: list[dict[str, Any]] = []
    model_records: list[dict[str, Any]] = []
    model_dir = output_dir / "nested_oof_models" / "outer_models"
    model_dir.mkdir(parents=True, exist_ok=True)

    for outer_fold, (train_mask, validation_mask, meta) in enumerate(folds):
        training = data.loc[train_mask].copy()
        validation = data.loc[validation_mask].copy()
        validation["outer_fold"] = outer_fold

        target_b_model = _fit_quality_model(training, "active_return_per_slot_day_pct")
        validation["oof_predicted_target_b_slot"] = _predict_quality(validation, target_b_model)

        pooled, pooled_specs, pooled_records = _fit_predict_bundle(
            training, validation, POOLED_FEATURES, "pooled", "pooled", "all", outer_fold,
        )
        model_records.extend(pooled_records)

        family_parts: list[pd.DataFrame] = []
        family_manifest: dict[str, Any] = {}
        for family, family_validation in pooled.groupby("event_family", sort=False):
            family_training = training[training["event_family"].eq(family)].copy()
            supported, support = _family_supported(family_training)
            support["status"] = "fitted" if supported else "exploratory_not_fitted"
            family_manifest[str(family)] = support
            if supported:
                modeled, family_specs, family_records = _fit_predict_bundle(
                    family_training,
                    family_validation,
                    FAMILY_FEATURES.get(str(family), COMMON_FEATURES),
                    "family",
                    "family_specific",
                    str(family),
                    outer_fold,
                )
                modeled["family_model_status"] = "fitted"
                model_records.extend(family_records)
                support["selected_specs"] = family_specs
                family_parts.append(modeled)
            else:
                family_parts.append(_neutral_family_predictions(family_validation, str(family)))
        scored = pd.concat(family_parts).sort_index()
        if len(scored) != len(validation):
            raise AssertionError("Stage 2F family prediction assembly lost validation rows")
        oof_parts.append(scored)
        fold_row = {**meta, "outer_fold": outer_fold, "lockbox_opened": False}
        fold_row["family_model_status"] = json.dumps({key: value["status"] for key, value in family_manifest.items()}, sort_keys=True)
        fold_rows.append(fold_row)
        _json(model_dir / f"outer_{outer_fold}_model_manifest.json", {
            "outer_fold": outer_fold,
            "pooled_selected_specs": pooled_specs,
            "family_models": family_manifest,
            "target_b_baseline_model": target_b_model,
            "training_end": meta["training_end"],
            "validation_start": meta["validation_start"],
            "oracle_labels_used_as_features": False,
            "lockbox_opened": False,
        })

    oof = pd.concat(oof_parts, ignore_index=True)
    if oof["stage2e_candidate_id"].duplicated().any():
        raise AssertionError("Stage 2F outer validation folds overlap")
    if not oof["analysis_split"].astype(str).str.lower().eq("train").all():
        raise AssertionError("Stage 2F OOF predictions contain non-development rows")
    folds_frame = pd.DataFrame(fold_rows)
    models_frame = pd.DataFrame(model_records)
    model_root = output_dir / "nested_oof_models"
    oof.to_csv(model_root / "stage2f_oof_predictions.csv", index=False)
    folds_frame.to_csv(model_root / "outer_fold_manifest.csv", index=False)
    models_frame.to_json(model_root / "model_records.json", orient="records", indent=2)
    return {"oof": oof, "folds": folds_frame, "models": models_frame, "outer_fold_count": len(folds)}


def _safe_auc(y: pd.Series, prediction: pd.Series) -> float:
    data = pd.DataFrame({"y": pd.to_numeric(y, errors="coerce"), "p": pd.to_numeric(prediction, errors="coerce")}).dropna()
    if data.empty or data["y"].nunique() < 2:
        return np.nan
    return float(roc_auc_score(data["y"].astype(int), data["p"]))


def _safe_spearman(left: pd.Series, right: pd.Series, minimum_rows: int = 3) -> float:
    data = pd.DataFrame({
        "left": pd.to_numeric(left, errors="coerce"),
        "right": pd.to_numeric(right, errors="coerce"),
    }).dropna()
    if len(data) < minimum_rows or data["left"].nunique() < 2 or data["right"].nunique() < 2:
        return np.nan
    return float(data["left"].corr(data["right"], method="spearman"))


def _prediction_metrics(oof: pd.DataFrame, output_dir: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    scopes: list[tuple[str, str, pd.DataFrame]] = [("pooled", "all", oof)]
    scopes.extend(("pooled_within_family", family, group) for family, group in oof.groupby("event_family"))
    fitted_family = oof[oof["family_model_status"].eq("fitted")]
    scopes.extend(("family_specific", family, group) for family, group in fitted_family.groupby("event_family"))
    for scope, family, frame in scopes:
        prefix = "family" if scope == "family_specific" else "pooled"
        for task_name, spec in TASKS.items():
            prediction_column = f"{prefix}_{task_name}"
            if prediction_column not in frame:
                continue
            target = pd.to_numeric(frame[spec["target"]], errors="coerce")
            prediction = pd.to_numeric(frame[prediction_column], errors="coerce")
            keep = target.notna() & prediction.notna()
            selected = frame.loc[keep]
            y = target.loc[keep]
            p = prediction.loc[keep]
            row = {"scope": scope, "family": family, "task": task_name, "target": spec["target"], "observations": int(keep.sum()), "independent_events": int(selected["economic_event_id"].nunique())}
            if spec["kind"] == "binary":
                threshold_column = f"{prefix}_{task_name}_threshold"
                thresholds = pd.to_numeric(selected[threshold_column], errors="coerce").fillna(0.5)
                row.update({
                    "auc": _safe_auc(y, p),
                    "brier": float(brier_score_loss(y.astype(int), np.clip(p, 0.0, 1.0))) if len(y) else np.nan,
                    "balanced_accuracy": float(balanced_accuracy_score(y.astype(int), p >= thresholds)) if y.nunique() > 1 else np.nan,
                    "mae": np.nan,
                    "spearman": np.nan,
                })
            else:
                row.update({
                    "auc": np.nan, "brier": np.nan, "balanced_accuracy": np.nan,
                    "mae": float(np.mean(np.abs(y - p))) if len(y) else np.nan,
                    "spearman": _safe_spearman(y, p),
                })
            rows.append(row)
    metrics = pd.DataFrame(rows)
    metrics.to_csv(output_dir / "nested_oof_models" / "oof_prediction_metrics.csv", index=False)
    return metrics


def _feature_diagnostics(data: pd.DataFrame, model_records: pd.DataFrame, output_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    coefficient_rows: list[dict[str, Any]] = []
    for _, record in model_records.iterrows():
        features = record["features"] if isinstance(record["features"], list) else json.loads(record["features"])
        coefficients = record["coefficients"] if isinstance(record["coefficients"], list) else json.loads(record["coefficients"])
        for feature, coefficient in zip(features, coefficients):
            coefficient_rows.append({
                "scope": record["scope"], "family": record["family"], "outer_fold": record["outer_fold"],
                "task": record["task"], "feature": feature, "standardized_coefficient": coefficient,
            })
    coefficients = pd.DataFrame(coefficient_rows)
    stability = (
        coefficients.groupby(["scope", "family", "task", "feature"], as_index=False)
        .agg(
            folds=("outer_fold", "nunique"),
            mean_standardized_coefficient=("standardized_coefficient", "mean"),
            median_standardized_coefficient=("standardized_coefficient", "median"),
            coefficient_sign_consistency=("standardized_coefficient", lambda values: float(max((values > 0).mean(), (values < 0).mean()))),
        )
        if not coefficients.empty else pd.DataFrame()
    )
    stability.to_csv(output_dir / "nested_oof_models" / "feature_coefficient_stability.csv", index=False)

    correlation_rows: list[dict[str, Any]] = []
    for family, family_data in data.groupby("event_family"):
        features = FAMILY_FEATURES.get(str(family), COMMON_FEATURES)
        for feature in features:
            x = pd.to_numeric(family_data.get(feature), errors="coerce")
            for target in ("best_legal_net_active_return_pct", "never_profitable_after_costs", "persistent_loser", "severe_adverse_before_meaningful_gain"):
                y = pd.to_numeric(family_data[target], errors="coerce")
                keep = x.notna() & y.notna()
                correlation_rows.append({
                    "family": family, "feature": feature, "target": target,
                    "observations": int(keep.sum()), "independent_events": int(family_data.loc[keep, "economic_event_id"].nunique()),
                    "spearman": _safe_spearman(x.loc[keep], y.loc[keep], minimum_rows=4),
                    "diagnostic_role": "descriptive_only" if family in {"geo", "other"} else "supporting_not_selection",
                })
    correlations = pd.DataFrame(correlation_rows)
    correlations.to_csv(output_dir / "nested_oof_models" / "family_feature_diagnostics.csv", index=False)
    return stability, correlations


def _high_failure(row: pd.Series | dict[str, Any], prefix: str) -> bool:
    for task_name in ("never_profitable_probability", "persistent_loss_probability", "severe_adverse_probability"):
        probability = pd.to_numeric(row.get(f"{prefix}_{task_name}"), errors="coerce")
        threshold = pd.to_numeric(row.get(f"{prefix}_{task_name}_threshold"), errors="coerce")
        if np.isfinite(probability) and np.isfinite(threshold) and float(probability) >= float(threshold):
            return True
    return False


def _semantic_static_accept(row: pd.Series | dict[str, Any]) -> bool:
    if str(row.get("mapping_type")) == "direct_issuer":
        return _as_bool(row.get("mapping_valid", False))
    valid = _as_bool(row.get("mapping_valid", False)) and float(row.get("mapping_confidence", 0) or 0) >= 3.0
    top_mapping = int(float(row.get("semantic_event_rank", 10**9))) == 0
    prior = pd.to_numeric(row.get("event_candidates_seen_previous_5_days"), errors="coerce")
    extension = pd.to_numeric(row.get("stock_minus_sector_20d"), errors="coerce")
    novel = bool(np.isfinite(prior) and float(prior) <= 0.0)
    repricing_ok = bool(np.isfinite(extension) and abs(float(extension)) <= 0.10)
    return valid and top_mapping and (novel or repricing_ok)


def _policy_accepts_row(row: pd.Series | dict[str, Any], policy: str) -> bool:
    if not _semantic_static_accept(row):
        return False
    if policy == "A_target_b_baseline" and str(row.get("mapping_type")) == "direct_issuer":
        value = pd.to_numeric(row.get("oof_predicted_target_b_slot"), errors="coerce")
        return bool(np.isfinite(value) and float(value) > 0.0)
    if policy in {"C_failure_filter_only", "D_failure_then_opportunity"}:
        return not _high_failure(row, "pooled")
    if policy == "E_family_specific_shared_allocator" and str(row.get("family_model_status")) == "fitted":
        return not _high_failure(row, "family")
    return True


def _rank_policy(frame: pd.DataFrame, policy: str, random_seed: int | None = None, source_only: bool = False) -> pd.DataFrame:
    out = frame.copy()
    out["_selector_rank"] = 10**9
    for _, day in out.groupby(["benchmark", "analysis_split", "entry_date"], sort=False):
        ranked = day.copy()
        if random_seed is not None:
            ranked["_random_key"] = ranked.apply(lambda row: _stable_key(random_seed, row["stage2e_candidate_id"]), axis=1)
            order = ranked.sort_values(["_random_key", "symbol"], kind="mergesort").index
        elif source_only:
            order = ranked.sort_values(["source_order", "symbol"], kind="mergesort").index
        elif policy in {"A_target_b_baseline"}:
            order = ranked.sort_values(
                ["oof_predicted_target_b_slot", "expected_slot_days", "source_order", "symbol"],
                ascending=[False, True, True, True], kind="mergesort",
            ).index
        elif policy in {"B_opportunity_ranking_only", "D_failure_then_opportunity"}:
            order = ranked.sort_values(
                ["pooled_opportunity_probability", "pooled_expected_best_legal_return", "pooled_expected_time_to_opportunity", "expected_slot_days", "source_order", "symbol"],
                ascending=[False, False, True, True, True, True], kind="mergesort",
            ).index
        elif policy == "C_failure_filter_only":
            order = ranked.sort_values(["expected_slot_days", "source_order", "symbol"], ascending=[True, True, True], kind="mergesort").index
        elif policy == "E_family_specific_shared_allocator":
            ranked["_family_opp_probability"] = np.where(
                ranked["family_model_status"].eq("fitted"),
                pd.to_numeric(ranked["family_opportunity_probability"], errors="coerce").fillna(0.5),
                0.5,
            )
            ranked["_family_expected_best"] = np.where(
                ranked["family_model_status"].eq("fitted"),
                pd.to_numeric(ranked["family_expected_best_legal_return"], errors="coerce").fillna(0.0),
                0.0,
            )
            ranked["_family_expected_time"] = np.where(
                ranked["family_model_status"].eq("fitted"),
                pd.to_numeric(ranked["family_expected_time_to_opportunity"], errors="coerce").fillna(10.0),
                pd.to_numeric(ranked["expected_slot_days"], errors="coerce").fillna(10.0),
            )
            order = ranked.sort_values(
                ["_family_opp_probability", "_family_expected_best", "_family_expected_time", "expected_slot_days", "semantic_event_rank", "source_order", "symbol"],
                ascending=[False, False, True, True, True, True, True], kind="mergesort",
            ).index
        else:
            raise ValueError(policy)
        out.loc[order, "_selector_rank"] = np.arange(len(order), dtype=int)
    out["_selector_rank"] = out["_selector_rank"].astype(int)
    out["_admission_score"] = pd.to_numeric(out.get("oof_predicted_target_b_slot"), errors="coerce")
    out["stage2f_policy"] = policy
    out["stage2f_policy_accept"] = out.apply(lambda row: _policy_accepts_row(row, policy), axis=1)
    if policy == "E_family_specific_shared_allocator":
        prefix = "family"
    else:
        prefix = "pooled"
    out["stage2f_family_model_status"] = out.get("family_model_status", "")
    out["stage2f_opportunity_probability"] = pd.to_numeric(out.get(f"{prefix}_opportunity_probability"), errors="coerce")
    out["stage2f_expected_best_legal_return"] = pd.to_numeric(out.get(f"{prefix}_expected_best_legal_return"), errors="coerce")
    out["stage2f_expected_time_to_opportunity"] = pd.to_numeric(out.get(f"{prefix}_expected_time_to_opportunity"), errors="coerce")
    for task_name in ("never_profitable_probability", "persistent_loss_probability", "severe_adverse_probability"):
        out[f"stage2f_{task_name}"] = pd.to_numeric(out.get(f"{prefix}_{task_name}"), errors="coerce")
    return out


def _same_day_selection(oof: pd.DataFrame, output_dir: Path) -> dict[str, pd.DataFrame]:
    diagnostic_dir = output_dir / "same_day_selection"
    diagnostic_dir.mkdir(parents=True, exist_ok=True)
    eligible = oof[oof.apply(_semantic_static_accept, axis=1)].copy()
    methods: dict[str, tuple[str | None, int | None, bool]] = {
        "source_order": (None, None, True),
        "expected_slot_days": ("C_failure_filter_only", None, False),
        **{policy: (policy, None, False) for policy in POLICIES},
    }
    decision_rows: list[dict[str, Any]] = []
    for (benchmark, date), group in eligible.groupby(["benchmark", "entry_date"], sort=True):
        if len(group) < 2:
            continue
        oracle_index = group["best_legal_net_active_return_pct"].astype(float).idxmax()
        oracle = group.loc[oracle_index]
        method_frames: list[tuple[str, pd.DataFrame]] = []
        for method, (policy, seed, source_only) in methods.items():
            if method == "expected_slot_days":
                ranked = group.sort_values(["expected_slot_days", "source_order", "symbol"], kind="mergesort")
                ranked = ranked.assign(stage2f_policy_accept=True)
            elif source_only:
                ranked = _rank_policy(group, "C_failure_filter_only", source_only=True).sort_values("_selector_rank")
                ranked["stage2f_policy_accept"] = True
            else:
                ranked = _rank_policy(group, str(policy)).sort_values("_selector_rank")
            method_frames.append((method, ranked))
        for seed in range(20):
            ranked = _rank_policy(group, "C_failure_filter_only", random_seed=seed).sort_values("_selector_rank")
            ranked["stage2f_policy_accept"] = True
            method_frames.append((f"random_legal_seed_{seed}", ranked))
        for method, ranked in method_frames:
            survivors = ranked[ranked["stage2f_policy_accept"].map(_as_bool)]
            selected = survivors.iloc[0] if not survivors.empty else None
            selected_value = float(selected["best_legal_net_active_return_pct"]) if selected is not None else 0.0
            decision_rows.append({
                "benchmark": benchmark, "entry_date": date, "method": method,
                "candidate_count": len(group), "admitted": selected is not None,
                "selected_candidate_id": selected["stage2e_candidate_id"] if selected is not None else "",
                "selected_symbol": selected["symbol"] if selected is not None else "",
                "selected_best_legal_net_active_return_pct": selected_value,
                "selected_never_profitable": bool(selected["never_profitable_after_costs"]) if selected is not None else False,
                "selected_persistent_loser": bool(selected["persistent_loser"]) if selected is not None else False,
                "selected_severe_adverse": bool(selected["severe_adverse_before_meaningful_gain"]) if selected is not None else False,
                "oracle_candidate_id": oracle["stage2e_candidate_id"],
                "oracle_symbol": oracle["symbol"],
                "oracle_best_legal_net_active_return_pct": float(oracle["best_legal_net_active_return_pct"]),
                "same_day_oracle_regret_pct": float(oracle["best_legal_net_active_return_pct"]) - selected_value,
                "selected_oracle_exactly": bool(selected is not None and selected["stage2e_candidate_id"] == oracle["stage2e_candidate_id"]),
                "oracle_role": "diagnostic_only",
            })
    decisions = pd.DataFrame(decision_rows)
    decisions["method_family"] = decisions["method"].str.replace(r"_seed_\d+$", "", regex=True)
    summary = (
        decisions.groupby(["method_family", "benchmark"], as_index=False)
        .agg(
            same_day_decisions=("entry_date", "size"),
            admission_rate=("admitted", "mean"),
            mean_selected_best_legal_net_active_pct=("selected_best_legal_net_active_return_pct", "mean"),
            selected_never_profitable_rate=("selected_never_profitable", "mean"),
            selected_persistent_loser_rate=("selected_persistent_loser", "mean"),
            selected_severe_adverse_rate=("selected_severe_adverse", "mean"),
            mean_same_day_oracle_regret_pct=("same_day_oracle_regret_pct", "mean"),
            oracle_exact_hit_rate=("selected_oracle_exactly", "mean"),
        )
    )
    decisions.to_csv(diagnostic_dir / "same_day_selection_decisions.csv", index=False)
    summary.to_csv(diagnostic_dir / "same_day_selection_summary.csv", index=False)
    return {"decisions": decisions, "summary": summary}


def _build_exit_plans(oof: pd.DataFrame, output_dir: Path) -> pd.DataFrame:
    (output_dir / "exact_replay").mkdir(parents=True, exist_ok=True)
    paths = pd.read_csv(PATH_TABLE)
    summaries = pd.read_csv(PATH_SUMMARY)
    for column in ("entry_date", "candidate_t_e", "path_date"):
        if column in paths:
            paths[column] = pd.to_datetime(paths[column], errors="coerce", utc=True)
    for column in ("actual_entry_date", "legal_te1_exit_date", "reference_exit_date"):
        if column in summaries:
            summaries[column] = pd.to_datetime(summaries[column], errors="coerce", utc=True)
    summary_lookup = summaries.set_index("stage2e_candidate_id")
    grouped_paths = {candidate_id: group.sort_values("legal_holding_day") for candidate_id, group in paths.groupby("stage2e_candidate_id")}
    rows: list[dict[str, Any]] = []
    for candidate_id in oof["stage2e_candidate_id"].unique():
        if candidate_id not in summary_lookup.index or candidate_id not in grouped_paths:
            continue
        summary = summary_lookup.loc[candidate_id]
        if not _as_bool(summary.get("kernel_executable", False)):
            continue
        path = grouped_paths[candidate_id]
        for exit_policy in EXIT_POLICIES:
            plan = _choose_exit(exit_policy, path, summary)
            candidate_te = pd.to_datetime(path.iloc[0]["candidate_t_e"], utc=True)
            if pd.to_datetime(plan["exit_date"], utc=True) >= candidate_te:
                raise AssertionError("Stage 2F exit plan reached T_e")
            rows.append({
                "stage2e_candidate_id": candidate_id, "exit_policy": exit_policy,
                "planned_exit_date": plan["exit_date"], "planned_exit_price": plan["exit_price"],
                "planned_exit_reason": plan["exit_reason"], "candidate_t_e": candidate_te,
            })
    plans = pd.DataFrame(rows)
    plans.to_csv(output_dir / "exact_replay" / "stage2f_candidate_exit_plans.csv", index=False)
    return plans


def _attach_exit_plan(frame: pd.DataFrame, plans: pd.DataFrame, exit_policy: str) -> pd.DataFrame:
    plan = plans[plans["exit_policy"].eq(exit_policy)][["stage2e_candidate_id", "planned_exit_date", "planned_exit_price", "planned_exit_reason"]].rename(columns={
        "planned_exit_date": "_stage2f_exit_date", "planned_exit_price": "_stage2f_exit_price", "planned_exit_reason": "_stage2f_exit_reason",
    })
    return frame.drop(columns=["_stage2f_exit_date", "_stage2f_exit_price", "_stage2f_exit_reason"], errors="ignore").merge(plan, on="stage2e_candidate_id", how="left", validate="one_to_one")


def _admission_callback(trade: dict, _day: pd.Timestamp, context: dict[str, Any]) -> str:
    if int(context.get("free_slots", 0)) <= 0:
        return "reject"
    return "accept" if _as_bool(trade.get("stage2f_policy_accept", False)) else "reject"


def _exact_replay(
    frame: pd.DataFrame,
    prices: dict,
    probs: dict,
    benchmark: str,
    policy: str,
    exit_policy: str,
    output_dir: Path | None,
    metadata: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    subset = frame[frame["benchmark"].eq(benchmark)].copy()
    if subset.empty:
        return {**metadata, "benchmark": benchmark, "selector_policy": policy, "exit_policy": exit_policy, "n_trades": 0}, pd.DataFrame()
    start = pd.to_datetime(subset["t_theta"], utc=True).min().normalize()
    end = pd.to_datetime(subset["t_e"], utc=True).max().normalize()
    trades, equity, stats, _frozen, allocation, disposition = sim_opp_cost(
        subset, prices, probs, dict(PORT_DEFAULT), bench_sym=benchmark, initial=INITIAL_CAPITAL,
        start_date=start, end_date=end, allocation_mode=ALLOCATION_FIFO, collect_allocation_log=True,
        admission_policy=_admission_callback,
        exit_plan_columns=("_stage2f_exit_date", "_stage2f_exit_price", "_stage2f_exit_reason"),
    )
    if not trades.empty:
        exits = pd.to_datetime(trades["exit_date"], errors="coerce", utc=True)
        tes = pd.to_datetime(trades["candidate_t_e"], errors="coerce", utc=True)
        if not (exits < tes).all():
            raise AssertionError("Stage 2F exact replay produced an exit at or after T_e")
    turnover_notional, turnover_x = _turnover(trades, equity)
    result = {
        **metadata, "benchmark": benchmark, "selector_policy": policy, "exit_policy": exit_policy,
        **stats, **_active_metrics(equity), **_trade_concentration(trades),
        "turnover_notional": turnover_notional, "turnover_x_average_equity": turnover_x,
        "slot_usage_pct": _slot_usage(trades, start, end), "n_trades": int(len(trades)),
        "selected_decisions": int(allocation.get("decision", pd.Series(dtype=object)).eq("selected").sum()) if not allocation.empty else 0,
        "admission_rejected": int(allocation.get("skip_reason", pd.Series(dtype=object)).eq("admission_reject").sum()) if not allocation.empty else 0,
        "blocked_by_capacity": int(allocation.get("skip_reason", pd.Series(dtype=object)).eq("max_concurrent").sum()) if not allocation.empty else 0,
        "lockbox_opened": False, "te_is_never_exit": True,
    }
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        trades.to_csv(output_dir / f"trades_{benchmark.lower()}.csv", index=False)
        equity.to_csv(output_dir / f"equity_{benchmark.lower()}.csv", index=False)
        allocation.to_csv(output_dir / f"allocation_{benchmark.lower()}.csv", index=False)
        disposition.to_csv(output_dir / f"disposition_{benchmark.lower()}.csv", index=False)
    return result, trades


def _run_exact_matrix(
    oof: pd.DataFrame,
    plans: pd.DataFrame,
    prices: dict,
    probs: dict,
    output_dir: Path,
) -> dict[str, pd.DataFrame]:
    replay_dir = output_dir / "exact_replay"
    combined_rows: list[dict[str, Any]] = []
    fold_rows: list[dict[str, Any]] = []
    no_other_rows: list[dict[str, Any]] = []
    trade_parts: list[pd.DataFrame] = []

    for policy in POLICIES:
        ranked = _rank_policy(oof, policy)
        for exit_policy in EXIT_POLICIES:
            planned = _attach_exit_plan(ranked, plans, exit_policy)
            for benchmark in ("SPY", "QQQ"):
                result, trades = _exact_replay(
                    planned, prices, probs, benchmark, policy, exit_policy,
                    replay_dir / "combined" / policy / exit_policy,
                    {"evaluation_scope": "combined_stage2f_oof_exact_replay", "other_family_excluded": False},
                )
                combined_rows.append(result)
                if not trades.empty:
                    detail = trades.copy()
                    detail["selector_policy"] = policy
                    detail["exit_policy"] = exit_policy
                    detail["benchmark"] = benchmark
                    trade_parts.append(detail)

                without_other = planned[~planned["event_family"].eq("other")]
                no_other_result, _ = _exact_replay(
                    without_other, prices, probs, benchmark, policy, exit_policy,
                    None,
                    {"evaluation_scope": "combined_stage2f_oof_exact_replay_without_other", "other_family_excluded": True},
                )
                no_other_rows.append(no_other_result)

            for outer_fold, fold_frame in planned.groupby("outer_fold", sort=True):
                for benchmark in ("SPY", "QQQ"):
                    result, _ = _exact_replay(
                        fold_frame, prices, probs, benchmark, policy, exit_policy, None,
                        {
                            "evaluation_scope": "stage2f_outer_fold_exact_replay", "outer_fold": int(outer_fold),
                            "validation_start": fold_frame["entry_date"].min(), "validation_end": fold_frame["entry_date"].max(),
                            "event_family_composition": json.dumps(fold_frame["event_family"].value_counts().to_dict(), sort_keys=True),
                            "other_family_excluded": False,
                        },
                    )
                    fold_rows.append(result)

    combined = pd.DataFrame(combined_rows)
    folds = pd.DataFrame(fold_rows)
    no_other = pd.DataFrame(no_other_rows)
    trades = pd.concat(trade_parts, ignore_index=True) if trade_parts else pd.DataFrame()
    combined.to_csv(replay_dir / "selector_exit_combined_exact_results.csv", index=False)
    folds.to_csv(replay_dir / "selector_exit_outer_fold_exact_results.csv", index=False)
    no_other.to_csv(replay_dir / "selector_exit_combined_without_other.csv", index=False)
    trades.to_csv(replay_dir / "selector_exit_trade_detail.csv", index=False)

    family = (
        trades.groupby(["selector_policy", "exit_policy", "benchmark", "event_family"], dropna=False, as_index=False)
        .agg(
            trade_count=("symbol", "size"), net_pnl=("pnl", "sum"), mean_trade_pnl_pct=("pnl_pct", "mean"),
            win_rate=("pnl", lambda values: float((pd.to_numeric(values, errors="coerce") > 0).mean())),
        ) if not trades.empty else pd.DataFrame()
    )
    clusters = (
        trades.groupby(["selector_policy", "exit_policy", "benchmark", "economic_event_id"], dropna=False, as_index=False)
        .agg(trade_count=("symbol", "size"), net_pnl=("pnl", "sum"), symbols=("symbol", lambda values: "|".join(sorted(set(map(str, values))))))
        if not trades.empty else pd.DataFrame()
    )
    family.to_csv(replay_dir / "results_by_event_family.csv", index=False)
    clusters.to_csv(replay_dir / "event_cluster_contribution.csv", index=False)
    return {"combined": combined, "folds": folds, "no_other": no_other, "trades": trades, "family": family, "clusters": clusters}


def _robust_ranking(results: pd.DataFrame, output_dir: Path, label: str) -> dict[str, Any]:
    ranked = results.copy()
    groups = ["exit_policy", "benchmark"]
    ranked["rank_excess"] = ranked.groupby(groups)["excess_return"].rank(method="average", ascending=False)
    ranked["rank_ir"] = ranked.groupby(groups)["active_information_ratio"].rank(method="average", ascending=False)
    ranked["rank_drawdown"] = ranked.groupby(groups)["active_max_drawdown_pct"].rank(method="average", ascending=False)
    ranked["composite_rank"] = ranked[["rank_excess", "rank_ir", "rank_drawdown"]].mean(axis=1)
    summary = (
        ranked.groupby("selector_policy", as_index=False)
        .agg(
            median_composite_rank=("composite_rank", "median"),
            q75_composite_rank=("composite_rank", lambda values: float(np.quantile(values, 0.75))),
            worst_composite_rank=("composite_rank", "max"),
            mean_excess_return=("excess_return", "mean"), median_excess_return=("excess_return", "median"),
            mean_active_ir=("active_information_ratio", "mean"), mean_active_drawdown_pct=("active_max_drawdown_pct", "mean"),
            mean_event_cluster_concentration_pct=("top_event_cluster_abs_pnl_share_pct", "mean"),
            max_event_cluster_concentration_pct=("top_event_cluster_abs_pnl_share_pct", "max"),
        )
    )
    summary["robust_rank_score"] = 0.5 * summary["median_composite_rank"] + 0.5 * summary["q75_composite_rank"]
    summary = summary.sort_values(["robust_rank_score", "mean_excess_return"], ascending=[True, False], kind="mergesort").reset_index(drop=True)
    summary["robust_order"] = np.arange(1, len(summary) + 1)
    by_exit = ranked.groupby(["exit_policy", "selector_policy"], as_index=False).agg(mean_excess_return=("excess_return", "mean"), mean_composite_rank=("composite_rank", "mean"))
    root = output_dir / "robustness"
    root.mkdir(parents=True, exist_ok=True)
    ranked.to_csv(root / f"{label}_cell_ranks.csv", index=False)
    summary.to_csv(root / f"{label}_robust_summary.csv", index=False)
    by_exit.to_csv(root / f"{label}_results_by_exit.csv", index=False)
    return {"ranked": ranked, "summary": summary, "by_exit": by_exit}


def _fold_stability(folds: pd.DataFrame, output_dir: Path) -> pd.DataFrame:
    stability = (
        folds.groupby(["selector_policy", "exit_policy", "benchmark"], as_index=False)
        .agg(
            outer_folds=("outer_fold", "nunique"), mean_fold_excess=("excess_return", "mean"),
            median_fold_excess=("excess_return", "median"), positive_fold_share=("excess_return", lambda values: float((pd.to_numeric(values, errors="coerce") > 0).mean())),
            mean_fold_ir=("active_information_ratio", "mean"), mean_fold_drawdown_pct=("active_max_drawdown_pct", "mean"),
        )
    )
    stability.to_csv(output_dir / "robustness" / "outer_fold_stability.csv", index=False)
    return stability


def _metric_value(metrics: pd.DataFrame, scope: str, family: str, task: str, column: str) -> float:
    selected = metrics[(metrics["scope"].eq(scope)) & (metrics["family"].eq(family)) & (metrics["task"].eq(task))]
    return float(selected.iloc[0][column]) if not selected.empty and pd.notna(selected.iloc[0][column]) else np.nan


def _policy_mean(summary: pd.DataFrame, policy: str, column: str) -> float:
    selected = summary[summary["selector_policy"].eq(policy)]
    return float(selected.iloc[0][column]) if not selected.empty else np.nan


def _same_day_mean(summary: pd.DataFrame, method: str, column: str) -> float:
    selected = summary[summary["method_family"].eq(method)]
    return float(pd.to_numeric(selected[column], errors="coerce").mean()) if not selected.empty else np.nan


def _decision_gates(
    metrics: pd.DataFrame,
    same_day: pd.DataFrame,
    robust: dict[str, Any],
    no_other_robust: dict[str, Any],
    fold_stability: pd.DataFrame,
    output_dir: Path,
) -> tuple[pd.DataFrame, str]:
    baseline = "A_target_b_baseline"
    baseline_robust = _policy_mean(robust["summary"], baseline, "robust_rank_score")
    baseline_excess = _policy_mean(robust["summary"], baseline, "mean_excess_return")
    baseline_no_other = _policy_mean(no_other_robust["summary"], baseline, "mean_excess_return")
    baseline_cluster = _policy_mean(robust["summary"], baseline, "max_event_cluster_concentration_pct")
    baseline_same_best = _same_day_mean(same_day, baseline, "mean_selected_best_legal_net_active_pct")
    baseline_same_regret = _same_day_mean(same_day, baseline, "mean_same_day_oracle_regret_pct")
    baseline_never = _same_day_mean(same_day, baseline, "selected_never_profitable_rate")
    baseline_persistent = _same_day_mean(same_day, baseline, "selected_persistent_loser_rate")
    baseline_fold = fold_stability[fold_stability["selector_policy"].eq(baseline)]
    baseline_fold_mean = float(baseline_fold["mean_fold_excess"].mean())
    baseline_positive_share = float(baseline_fold["positive_fold_share"].mean())

    gate_rows: list[dict[str, Any]] = []
    for policy in POLICIES[1:]:
        family_policy = policy == "E_family_specific_shared_allocator"
        metric_scope = "family_specific" if family_policy else "pooled"
        metric_family = "earnings" if family_policy else "all"
        opportunity_auc = _metric_value(metrics, metric_scope, metric_family, "opportunity_probability", "auc")
        failure_aucs = [
            _metric_value(metrics, metric_scope, metric_family, task, "auc")
            for task in ("never_profitable_probability", "persistent_loss_probability", "severe_adverse_probability")
        ]
        opportunity_identified = bool(np.isfinite(opportunity_auc) and opportunity_auc >= 0.55)
        failures_identified = sum(bool(np.isfinite(value) and value >= 0.55) for value in failure_aucs) >= 2
        same_best = _same_day_mean(same_day, policy, "mean_selected_best_legal_net_active_pct")
        same_regret = _same_day_mean(same_day, policy, "mean_same_day_oracle_regret_pct")
        never_rate = _same_day_mean(same_day, policy, "selected_never_profitable_rate")
        persistent_rate = _same_day_mean(same_day, policy, "selected_persistent_loser_rate")
        same_day_improved = bool(same_best >= baseline_same_best + 0.10 and same_regret <= baseline_same_regret)
        bad_trade_improved = bool(never_rate <= baseline_never and persistent_rate <= baseline_persistent)
        robust_score = _policy_mean(robust["summary"], policy, "robust_rank_score")
        mean_excess = _policy_mean(robust["summary"], policy, "mean_excess_return")
        exact_improved = bool(robust_score < baseline_robust and mean_excess >= baseline_excess)
        candidate_exit = robust["by_exit"][robust["by_exit"]["selector_policy"].eq(policy)].set_index("exit_policy")["mean_excess_return"]
        baseline_exit = robust["by_exit"][robust["by_exit"]["selector_policy"].eq(baseline)].set_index("exit_policy")["mean_excess_return"]
        common = candidate_exit.index.intersection(baseline_exit.index)
        exits_not_worse = int((candidate_exit.loc[common] >= baseline_exit.loc[common]).sum())
        several_exits = exits_not_worse >= 4
        candidate_fold = fold_stability[fold_stability["selector_policy"].eq(policy)]
        fold_mean = float(candidate_fold["mean_fold_excess"].mean())
        positive_share = float(candidate_fold["positive_fold_share"].mean())
        fold_stable = bool(fold_mean >= baseline_fold_mean and positive_share >= baseline_positive_share - 0.05)
        no_other_excess = _policy_mean(no_other_robust["summary"], policy, "mean_excess_return")
        credible_without_other = bool(no_other_excess >= baseline_no_other)
        max_cluster = _policy_mean(robust["summary"], policy, "max_event_cluster_concentration_pct")
        concentration_ok = bool(max_cluster <= max(baseline_cluster + 5.0, 35.0))
        passed = all((opportunity_identified, failures_identified, same_day_improved, bad_trade_improved, exact_improved, several_exits, fold_stable, credible_without_other, concentration_ok))
        gate_rows.append({
            "selector_policy": policy,
            "opportunity_auc": opportunity_auc, "opportunity_identified": opportunity_identified,
            "failure_auc_never_profitable": failure_aucs[0], "failure_auc_persistent": failure_aucs[1], "failure_auc_severe_adverse": failure_aucs[2], "failures_identified": failures_identified,
            "same_day_best_improved": same_day_improved, "same_day_bad_trade_rates_improved": bad_trade_improved,
            "exact_robust_rank_improved": exact_improved, "exit_policies_not_worse_than_target_b": exits_not_worse, "several_exits_gate": several_exits,
            "fold_stability_gate": fold_stable, "credible_without_other": credible_without_other, "event_cluster_concentration_gate": concentration_ok,
            "all_freeze_gates_passed": passed,
        })
    gates = pd.DataFrame(gate_rows)
    passing = gates[gates["all_freeze_gates_passed"]]
    if passing.empty:
        selected_policy = baseline
    else:
        candidates = passing.merge(robust["summary"][["selector_policy", "robust_rank_score"]], on="selector_policy", how="left")
        selected_policy = str(candidates.sort_values(["robust_rank_score", "selector_policy"], kind="mergesort").iloc[0]["selector_policy"])
    (output_dir / "decision").mkdir(parents=True, exist_ok=True)
    gates.to_csv(output_dir / "decision" / "selector_freeze_gates.csv", index=False)
    return gates, selected_policy


def _preserve_audits(output_dir: Path) -> dict[str, Any]:
    source = STAGE2E / "audit_baselines_manifest.json"
    audits = json.loads(source.read_text(encoding="utf-8"))
    for sample in audits["samples"]:
        path = Path(sample["path"])
        if len(pd.read_csv(path)) != int(sample["rows"]) or _hash(path) != sample["sha256"]:
            raise AssertionError(f"Stage 2F audit baseline changed before research: {sample['label']}")
    manifest = {
        "label": "stage2f_preserved_stage3_audit_baselines",
        "samples": audits["samples"], "samples_overwritten": False,
        "exit_model_training_performed": False, "lockbox_opened": False,
    }
    _json(output_dir / "audit_baselines_manifest.json", manifest)
    return manifest


def _freeze_selector(
    selected_policy: str,
    gates: pd.DataFrame,
    robust: dict[str, Any],
    no_other_robust: dict[str, Any],
    oof_family_composition: dict[str, int],
    output_dir: Path,
) -> dict[str, Any]:
    learned = selected_policy != "A_target_b_baseline"
    selected_gate = gates[gates["selector_policy"].eq(selected_policy)]
    reason = (
        "a learned Stage 2F selector passed every predeclared opportunity, failure, same-day, exact replay, fold, concentration, and no-other-family gate"
        if learned else
        "no learned Stage 2F selector passed every predeclared gate; retain the simplest valid Stage 2E Target B baseline"
    )
    selected_gate_records = selected_gate.to_dict("records")
    if not selected_gate_records:
        selected_gate_records = [{
            "selector_policy": selected_policy,
            "gate_role": "retained_baseline_after_no_learned_challenger_passed_all_predeclared_gates",
        }]
    only_earnings_oof = set(oof_family_composition) == {"earnings"}
    manifest = {
        "label": "research_frozen_selector_stage2f",
        "selected_stage2f_policy": selected_policy,
        "selected_performance_selector": selected_policy if learned else "target_b_per_slot_day",
        "learned_stage2f_selector_established": learned,
        "decision_reason": reason,
        "opportunity_and_failure_targets_kept_separate": True,
        "family_specific_fit_policy": {
            "earnings": "fit only when each outer training fold has >=25 independent events, >=40 rows, and adequate binary classes",
            "geo": "exploratory transparent fallback when support gate is not met",
            "other": "exploratory transparent fallback when support gate is not met",
        },
        "selected_policy_gate_record": selected_gate_records,
        "selected_policy_robust_record": robust["summary"][robust["summary"]["selector_policy"].eq(selected_policy)].to_dict("records"),
        "selected_policy_without_other_record": no_other_robust["summary"][no_other_robust["summary"]["selector_policy"].eq(selected_policy)].to_dict("records"),
        "oof_validation_family_composition": oof_family_composition,
        "family_predictive_validation_scope": "earnings_only" if only_earnings_oof else "multiple_families",
        "without_other_replay_interpretation": (
            "identical by construction because the globally chronological OOF validation stream contains earnings only; "
            "this rules out other-family result concentration but does not validate geo/other predictive models"
            if only_earnings_oof else
            "diagnostic replay excluding the small other family"
        ),
        "oracle_labels_live_feature_eligible": False,
        "exit_policy_frozen": False,
        "exit_model_training_performed": False,
        "selector_changes_allowed_during_exit_research": False,
        "semantic_stage2c_conclusions_frozen": True,
        "lockbox_opened": False,
        "lockbox_reserved_for_final_modular_pipeline_evaluation": True,
        "te_is_never_exit": True,
        "latest_legal_exit_horizon": "T_e - 1",
    }
    path = output_dir / "research_frozen_selector_stage2f.json"
    _json(path, manifest)
    return {**manifest, "path": str(path)}


def _markdown_table(frame: pd.DataFrame, columns: list[str], digits: int = 4, max_rows: int | None = None) -> str:
    if frame.empty:
        return "No observations."
    view = frame[[column for column in columns if column in frame]].copy()
    if max_rows is not None:
        view = view.head(max_rows)
    for column in view.select_dtypes(include=[np.number]).columns:
        view[column] = view[column].map(lambda value: "" if pd.isna(value) else f"{value:.{digits}f}")
    lines = ["| " + " | ".join(view.columns) + " |", "| " + " | ".join(["---"] * len(view.columns)) + " |"]
    lines.extend("| " + " | ".join(map(str, row)) + " |" for row in view.itertuples(index=False, name=None))
    return "\n".join(lines)


def _build_report(
    data: pd.DataFrame,
    oof_result: dict[str, Any],
    metrics: pd.DataFrame,
    feature_stability: pd.DataFrame,
    correlations: pd.DataFrame,
    same_day: dict[str, pd.DataFrame],
    matrix: dict[str, pd.DataFrame],
    robust: dict[str, Any],
    no_other_robust: dict[str, Any],
    folds: pd.DataFrame,
    gates: pd.DataFrame,
    frozen: dict[str, Any],
    output_dir: Path,
) -> Path:
    oof = oof_result["oof"]
    oof_families = {str(key): int(value) for key, value in oof["event_family"].value_counts().sort_index().items()}
    only_earnings_oof = set(oof_families) == {"earnings"}
    prevalence = (
        data.groupby("event_family", as_index=False)
        .agg(
            candidates=("stage2e_candidate_id", "size"), independent_events=("economic_event_id", "nunique"),
            reaches_2pct_rate=("reaches_2pct_active_net", "mean"), never_profitable_rate=("never_profitable_after_costs", "mean"),
            persistent_loser_rate=("persistent_loser", "mean"), severe_adverse_rate=("severe_adverse_before_meaningful_gain", "mean"),
            mean_best_legal_net_active_pct=("best_legal_net_active_return_pct", "mean"),
        )
    )
    prevalence_table = _markdown_table(prevalence, list(prevalence.columns))
    metrics_view = metrics[metrics["task"].isin({"opportunity_probability", "expected_best_legal_return", "never_profitable_probability", "persistent_loss_probability", "severe_adverse_probability"})]
    metrics_table = _markdown_table(metrics_view, ["scope", "family", "task", "observations", "independent_events", "auc", "balanced_accuracy", "mae", "spearman"], max_rows=30)
    outer_fold_table = _markdown_table(
        oof_result["folds"],
        ["outer_fold", "training_end", "validation_start", "validation_end", "training_rows", "validation_rows", "training_events", "validation_events", "validation_family_composition", "family_model_status"],
    )
    same_day_table = _markdown_table(same_day["summary"], [
        "method_family", "benchmark", "same_day_decisions", "admission_rate", "mean_selected_best_legal_net_active_pct",
        "selected_never_profitable_rate", "selected_persistent_loser_rate", "selected_severe_adverse_rate",
        "mean_same_day_oracle_regret_pct", "oracle_exact_hit_rate",
    ])
    robust_table = _markdown_table(robust["summary"], [
        "selector_policy", "robust_rank_score", "mean_excess_return", "median_excess_return", "mean_active_ir",
        "mean_active_drawdown_pct", "mean_event_cluster_concentration_pct", "max_event_cluster_concentration_pct", "robust_order",
    ])
    benchmark_summary = (
        matrix["combined"].groupby(["selector_policy", "benchmark"], as_index=False)
        .agg(
            mean_excess_return=("excess_return", "mean"),
            mean_active_ir=("active_information_ratio", "mean"),
            mean_active_drawdown_pct=("active_max_drawdown_pct", "mean"),
            mean_trade_count=("n_trades", "mean"),
            mean_slot_usage_pct=("slot_usage_pct", "mean"),
        )
    )
    benchmark_table = _markdown_table(benchmark_summary, list(benchmark_summary.columns))
    no_other_table = _markdown_table(no_other_robust["summary"], ["selector_policy", "robust_rank_score", "mean_excess_return", "mean_active_ir", "mean_active_drawdown_pct", "robust_order"])
    fold_summary = folds.groupby("selector_policy", as_index=False).agg(mean_fold_excess=("mean_fold_excess", "mean"), median_fold_excess=("median_fold_excess", "median"), mean_positive_fold_share=("positive_fold_share", "mean"))
    fold_table = _markdown_table(fold_summary, list(fold_summary.columns))
    gate_table = _markdown_table(gates, [
        "selector_policy", "opportunity_auc", "opportunity_identified", "failure_auc_never_profitable",
        "failure_auc_persistent", "failure_auc_severe_adverse", "failures_identified", "same_day_best_improved",
        "same_day_bad_trade_rates_improved", "exact_robust_rank_improved", "exit_policies_not_worse_than_target_b",
        "fold_stability_gate", "credible_without_other", "event_cluster_concentration_gate", "all_freeze_gates_passed",
    ])

    earnings_features = feature_stability[
        feature_stability["scope"].eq("family_specific") & feature_stability["family"].eq("earnings")
    ].copy()
    if not earnings_features.empty:
        earnings_features["abs_median"] = earnings_features["median_standardized_coefficient"].abs()
        earnings_features = earnings_features.sort_values(["task", "abs_median"], ascending=[True, False]).groupby("task", as_index=False).head(5)
    earnings_feature_table = _markdown_table(earnings_features, ["task", "feature", "folds", "median_standardized_coefficient", "coefficient_sign_consistency"], max_rows=25)
    exploratory = correlations[correlations["family"].isin(["geo", "other"])].copy()
    exploratory["abs_spearman"] = exploratory["spearman"].abs()
    exploratory = exploratory.sort_values(["family", "target", "abs_spearman"], ascending=[True, True, False]).groupby(["family", "target"], as_index=False).head(3)
    exploratory_table = _markdown_table(exploratory, ["family", "target", "feature", "observations", "independent_events", "spearman", "diagnostic_role"], max_rows=24)

    pooled_opp_auc = _metric_value(metrics, "pooled", "all", "opportunity_probability", "auc")
    earnings_opp_auc = _metric_value(metrics, "family_specific", "earnings", "opportunity_probability", "auc")
    pooled_failure_aucs = [
        _metric_value(metrics, "pooled", "all", task, "auc")
        for task in ("never_profitable_probability", "persistent_loss_probability", "severe_adverse_probability")
    ]
    opportunity_answer = (
        f"Partially: pooled OOF AUC is {pooled_opp_auc:.3f} and family-specific earnings AUC is {earnings_opp_auc:.3f}."
        if any(np.isfinite(value) and value >= 0.55 for value in (pooled_opp_auc, earnings_opp_auc)) else
        f"No reliable evidence: pooled OOF AUC is {pooled_opp_auc:.3f} and earnings AUC is {earnings_opp_auc:.3f}."
    )
    bad_answer = (
        "Yes, at least two pooled failure tasks cleared AUC 0.55; exact selection gates still determine whether this is portfolio-useful."
        if sum(np.isfinite(value) and value >= 0.55 for value in pooled_failure_aucs) >= 2 else
        "Not reliably: fewer than two pooled failure tasks cleared OOF AUC 0.55."
    )
    learned = frozen["learned_stage2f_selector_established"]
    useful_feature_evidence = bool(
        (np.isfinite(pooled_opp_auc) and pooled_opp_auc >= 0.55)
        or (np.isfinite(earnings_opp_auc) and earnings_opp_auc >= 0.55)
        or sum(np.isfinite(value) and value >= 0.55 for value in pooled_failure_aucs) >= 2
    )
    feature_answer = (
        "No feature set is established as working. Some earnings coefficient signs are stable enough to remain hypotheses, "
        "but their associated OOF tasks did not achieve useful discrimination. Geo and other correlations are descriptive only."
        if not useful_feature_evidence else
        "Only features attached to an OOF task clearing the predeclared discrimination gate should be treated as supported; all other coefficient and correlation patterns remain hypotheses."
    )
    portfolio_answer = (
        f"Yes. `{frozen['selected_stage2f_policy']}` passed all predeclared gates."
        if learned else
        "No learned family-specific selector passed every gate; Target B is retained as the simplest valid baseline."
    )

    report = f"""# Stage 2F — Family-Specific Trade Opportunity Selection

## 1. Scope and oracle labels

Stage 2F used {len(data)} development candidates and opened zero test/lockbox rows. Every label uses legal closes only through `T_e-1`; `T_e` is never an exit. Standardized net-active labels subtract asset buy/sell and benchmark sell/rebuy costs and slippage at a $10,000 reference notional.

These full-path labels are ex-post research targets only and are explicitly prohibited as live features.

{prevalence_table}

## 2. Nested chronological prediction results

All preprocessing, ridge penalties from `{PENALTIES}`, and binary thresholds from `{THRESHOLDS}` were selected inside inner chronological folds. Outer folds keep event episodes intact and train only on events completed before validation starts. No RL, deep model, or large search was used.

{metrics_table}

Outer-fold date ranges and validation-family composition:

{outer_fold_table}

Earnings has enough independent events for fitted family models in supported outer folds. Geo ({int(prevalence.loc[prevalence.event_family.eq('geo'), 'independent_events'].iloc[0])} events) and other ({int(prevalence.loc[prevalence.event_family.eq('other'), 'independent_events'].iloc[0])} events) remain exploratory and use transparent semantic/ordering fallbacks.

The concatenated OOF validation composition is `{json.dumps(oof_families, sort_keys=True)}`. {"Because every OOF validation row is earnings, the chronological evidence validates only the earnings family. Geo and other occurred too early and/or are too sparse to obtain honest global-chronology outer validation; they are not claimed as predictive models." if only_earnings_oof else "Multiple families receive chronological OOF validation."}

## 3. Features by family

Most stable fitted earnings coefficients:

{earnings_feature_table}

Geo and other descriptive correlations—diagnostic only, never selected as fitted evidence:

{exploratory_table}

Coefficient magnitude is not causal importance. A feature is credible only when its sign is reasonably stable across outer folds and the corresponding OOF task has useful discrimination.

## 4. Same-day opportunity selection

The oracle picks the candidate with the highest ex-post best legal net active return on each benchmark/date and is diagnostic only. Regret charges an abstention as a zero-return choice.

{same_day_table}

## 5. Exact selector × exit-policy replay

Every one of the five selector policies was replayed independently under all eight Stage 2E exits for SPY and QQQ. Each of the 80 combined runs and every outer-fold run rebuilt capital, capacity, later admissions, benchmark rotation, costs, and slippage.

{robust_table}

SPY and QQQ separately (mean across the eight predeclared exits):

{benchmark_table}

Chronological fold stability:

{fold_table}

Robustness after excluding the small `other` catalyst family:

{no_other_table}

{"This table is identical to the full OOF table by construction because the OOF stream contains earnings only. It confirms that `other` did not drive the Stage 2F selector comparison; it is not an independent robustness test and does not validate geo/other models." if only_earnings_oof else "This is a distinct diagnostic replay with `other` candidates removed."}

## 6. Freeze decision

{gate_table}

Selected policy: `{frozen['selected_stage2f_policy']}`. {frozen['decision_reason'].capitalize()}.

No exit model was trained and no exit policy was frozen. The 184- and 138-trade Stage 3 samples remain audit-only. The later lockbox remains sealed.

## 7. Required answers

1. **Can opportunity be identified in advance?** {opportunity_answer}
2. **Can bad trades be identified more reliably than exact winners?** {bad_answer}
3. **Which features work by family?** {feature_answer}
4. **Does family-specific selection improve the real portfolio decision without overfitting?** {portfolio_answer}
"""
    path = output_dir / "stage2f_family_specific_selection_report.md"
    path.write_text(report, encoding="utf-8")
    return path


def _update_stage3(frozen: dict[str, Any], audits: dict[str, Any], report: Path) -> Path:
    sample = STAGE3 / "stage3_exit_development_trades.csv"
    manifest = {
        "label": "stage3_exit_research_paused_after_stage2f",
        "performance_selector_status": "research_frozen_after_stage2f; exit training remains paused",
        "research_frozen_selector_stage2f": frozen,
        "preserved_audit_baselines": audits["samples"],
        "current_138_trade_file": str(sample), "current_138_trade_file_role": "audit_baseline_only", "current_138_trade_file_sha256": _hash(sample),
        "stage2f_report": str(report),
        "exit_policy_trained": False, "exit_model_selection_performed": False, "exit_model_training_status": "paused",
        "lockbox_opened": False, "lockbox_reserved_for_final_modular_pipeline_evaluation": True,
        "te_is_never_exit": True, "latest_legal_exit_horizon": "T_e - 1",
    }
    path = STAGE3 / "stage3_exit_manifest.json"
    _json(path, manifest)
    plan = f"""# Stage 3 Exit Research — Paused After Stage 2F

Stage 2F selected `{frozen['selected_stage2f_policy']}`. {frozen['decision_reason']}.

Do not train an exit model yet. The existing 184- and 138-trade samples remain audit-only and were not overwritten. Review `{report}` before building any new Stage 3 sample.

All future exits must be strictly before `T_e`; `T_e-1` is the latest legal horizon. The later lockbox remains sealed for the final complete-pipeline evaluation.
"""
    (STAGE3 / "stage3_exit_research_plan.md").write_text(plan, encoding="utf-8")
    return path


def run_stage2f(output_dir: Path | str = OUTPUT) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    audits = _preserve_audits(output_dir)
    audit_hashes = {sample["label"]: sample["sha256"] for sample in audits["samples"]}
    development = _load_development()
    net_paths, oracle = _build_oracle_labels(output_dir)
    data = development.merge(oracle, on=["stage2e_candidate_id", "benchmark", "symbol", "event_family", "mapping_type"], how="inner", validate="one_to_one")
    if len(data) != len(development):
        raise AssertionError("Stage 2F oracle labels do not cover every development candidate")
    data = _attach_semantic_event_rank(data)
    oof_result = _nested_oof_models(data, output_dir)
    oof = oof_result["oof"]
    metrics = _prediction_metrics(oof, output_dir)
    feature_stability, correlations = _feature_diagnostics(data, oof_result["models"], output_dir)
    same_day = _same_day_selection(oof, output_dir)
    plans = _build_exit_plans(oof, output_dir)
    prices = pickle.loads(PRICES.read_bytes())
    probs = pickle.loads(PROBS.read_bytes())
    matrix = _run_exact_matrix(oof, plans, prices, probs, output_dir)
    robust = _robust_ranking(matrix["combined"], output_dir, "full_oof")
    no_other_robust = _robust_ranking(matrix["no_other"], output_dir, "without_other")
    fold_stability = _fold_stability(matrix["folds"], output_dir)
    gates, selected_policy = _decision_gates(metrics, same_day["summary"], robust, no_other_robust, fold_stability, output_dir)
    oof_family_composition = {str(key): int(value) for key, value in oof["event_family"].value_counts().sort_index().items()}
    frozen = _freeze_selector(selected_policy, gates, robust, no_other_robust, oof_family_composition, output_dir)
    report = _build_report(data, oof_result, metrics, feature_stability, correlations, same_day, matrix, robust, no_other_robust, fold_stability, gates, frozen, output_dir)
    stage3_manifest = _update_stage3(frozen, audits, report)

    for sample in audits["samples"]:
        if _hash(Path(sample["path"])) != audit_hashes[sample["label"]]:
            raise AssertionError(f"Stage 2F modified audit baseline {sample['label']}")
    if not (pd.to_datetime(plans["planned_exit_date"], utc=True) < pd.to_datetime(plans["candidate_t_e"], utc=True)).all():
        raise AssertionError("Stage 2F persisted an illegal exit plan")
    manifest = {
        "label": "stage2f_family_specific_trade_opportunity_selection",
        "development_only": True, "development_candidates": len(data), "oof_candidates": len(oof),
        "outer_folds": oof_result["outer_fold_count"], "oracle_path_rows": len(net_paths),
        "policies": list(POLICIES), "exit_policies": list(EXIT_POLICIES),
        "combined_exact_replays": len(matrix["combined"]), "outer_fold_exact_replays": len(matrix["folds"]),
        "without_other_exact_replays": len(matrix["no_other"]),
        "oof_validation_family_composition": oof_family_composition,
        "family_predictive_validation_scope": "earnings_only" if set(oof_family_composition) == {"earnings"} else "multiple_families",
        "without_other_replay_is_distinct": set(oof_family_composition) != {"earnings"},
        "research_frozen_selector": frozen,
        "oracle_labels_live_feature_eligible": False, "exit_model_training_performed": False,
        "test_rows_read": 0, "lockbox_opened": False, "te_is_never_exit": True, "latest_legal_exit_horizon": "T_e - 1",
        "source_hashes": {"semantic_candidates": _hash(SEMANTIC_CANDIDATES), "stage2e_paths": _hash(PATH_TABLE), "stage2e_path_summary": _hash(PATH_SUMMARY), "prices": _hash(PRICES), "probs": _hash(PROBS)},
        "outputs": {
            "report": str(report), "frozen_selector": frozen["path"],
            "oracle_labels": str(output_dir / "oracle_labels" / "full_path_oracle_labels.csv"),
            "oof_predictions": str(output_dir / "nested_oof_models" / "stage2f_oof_predictions.csv"),
            "same_day_summary": str(output_dir / "same_day_selection" / "same_day_selection_summary.csv"),
            "combined_matrix": str(output_dir / "exact_replay" / "selector_exit_combined_exact_results.csv"),
            "fold_matrix": str(output_dir / "exact_replay" / "selector_exit_outer_fold_exact_results.csv"),
            "decision_gates": str(output_dir / "decision" / "selector_freeze_gates.csv"),
            "stage3_manifest": str(stage3_manifest),
        },
    }
    manifest_path = output_dir / "stage2f_manifest.json"
    _json(manifest_path, manifest)
    return {"manifest": manifest_path, "report": report, "frozen_selector": Path(frozen["path"]), "stage3_manifest": stage3_manifest}


if __name__ == "__main__":
    for name, path in run_stage2f().items():
        print(f"{name}: {path}")
