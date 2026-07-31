"""Final train-only nested chronological validation for Stage 2B.

The later lockbox is intentionally out of scope.  This module evaluates only
rows whose source split is ``train``.  For each outer chronological fold, the
inner folds choose the connection tie-breaker and admission threshold; the
outer fold then evaluates the frozen choice.  In parallel, all predeclared
tie-breaker/threshold cells are replayed to isolate ranking from admission.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtesting.optimize_cem import ALLOCATION_FIFO, INITIAL_CAPITAL, PORT_DEFAULT, sim_opp_cost

from .admission import make_admission_policy
from .baselines import DEFAULT_CAPACITY, DEFAULT_CANDIDATES, _merge_capacity, _read_capacity, _read_candidates
from .connection_tiebreakers import _prepare, _tie_order
from .dynamic_replay import (
    DEFAULT_PRICES,
    DEFAULT_PROBS,
    DEFAULT_SOURCE,
    DEFAULT_UNIVERSE,
    _active_metrics,
    _hash,
    _load_universe,
)


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT / "data" / "selection_stage2b" / "nested_final_validation"

TIE_BREAKERS = ("source_order", "expected_slot_days")
THRESHOLDS: tuple[float | None, ...] = (None, 0.90, 0.95, 0.98, 1.00)
THRESHOLD_LABELS = {None: "none", 0.90: "0.90", 0.95: "0.95", 0.98: "0.98", 1.00: "1.00"}
CONFIGS = tuple((tie, threshold) for tie in TIE_BREAKERS for threshold in THRESHOLDS)
FACTORIAL_CONFIGS = {
    "A_connection_source_order_always_fill": ("source_order", None),
    "B_connection_expected_slot_days_always_fill": ("expected_slot_days", None),
    "C_connection_source_order_min_connection_1.00": ("source_order", 1.00),
    "D_connection_expected_slot_days_min_connection_1.00": ("expected_slot_days", 1.00),
}


def _date_groups(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame[["benchmark", "entry_date"]]
        .drop_duplicates()
        .sort_values(["entry_date", "benchmark"], kind="mergesort")
        .reset_index(drop=True)
    )


def _chronological_folds(frame: pd.DataFrame, n_splits: int) -> list[tuple[pd.Series, pd.Series]]:
    """Return expanding train/validation masks at benchmark/date-group level."""
    groups = _date_groups(frame)
    min_train = max(3, int(np.ceil(len(groups) * 0.40)))
    if len(groups) <= min_train:
        return []
    blocks = np.array_split(np.arange(min_train, len(groups)), min(n_splits, len(groups) - min_train))
    values = list(zip(frame["benchmark"], frame["entry_date"]))
    folds = []
    for fold_block in blocks:
        if len(fold_block) == 0:
            continue
        validation_groups = set(zip(groups.iloc[fold_block]["benchmark"], groups.iloc[fold_block]["entry_date"]))
        first_date = groups.iloc[fold_block]["entry_date"].min()
        train_groups = set(zip(groups.loc[groups["entry_date"] < first_date, "benchmark"], groups.loc[groups["entry_date"] < first_date, "entry_date"]))
        train_mask = pd.Series([value in train_groups for value in values], index=frame.index)
        validation_mask = pd.Series([value in validation_groups for value in values], index=frame.index)
        if train_mask.any() and validation_mask.any():
            folds.append((train_mask, validation_mask))
    return folds


def _capacity_value(day: pd.DataFrame) -> int:
    values = pd.to_numeric(day.get("capacity_slots", pd.Series(dtype=float)), errors="coerce").dropna()
    return max(int(round(float(values.iloc[0]))) if not values.empty else 0, 0)


def _rank_order(day: pd.DataFrame, tie_breaker: str) -> list[Any]:
    return _tie_order(_prepare(day.copy()), tie_breaker, seed=0)


def _static_decisions(frame: pd.DataFrame, tie_breaker: str, threshold: float | None, fold: int, scope: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (benchmark, entry_date), day in frame.groupby(["benchmark", "entry_date"], sort=True):
        order = _rank_order(day, tie_breaker)
        capacity = _capacity_value(day)
        accepted = 0
        for same_day_rank, index in enumerate(order, start=1):
            row = day.loc[index]
            strength = float(pd.to_numeric(row.get("feat_connection_strength", np.nan), errors="coerce"))
            if accepted >= capacity:
                decision = "blocked"
                reason = "capacity_exhausted"
            elif threshold is not None and (not np.isfinite(strength) or strength < threshold):
                decision = "rejected"
                reason = "below_connection_threshold"
            else:
                decision = "accepted"
                reason = "always_fill" if threshold is None else "threshold_pass"
            if decision == "accepted":
                accepted += 1
            rows.append(
                {
                    "evaluation_scope": scope,
                    "outer_fold": fold,
                    "benchmark": benchmark,
                    "entry_date": entry_date,
                    "symbol": row.get("symbol", ""),
                    "event_family": row.get("event_family", "other"),
                    "same_day_rank": same_day_rank,
                    "capacity_slots": capacity,
                    "connection_strength": strength,
                    "tie_breaker": tie_breaker,
                    "admission_threshold": threshold,
                    "admission_threshold_label": THRESHOLD_LABELS[threshold],
                    "decision": decision,
                    "decision_reason": reason,
                    "te1_active_net_return_pct": row.get("te1_active_net_return_pct", np.nan),
                    "active_return_per_slot_day_pct": row.get("active_return_per_slot_day_pct", np.nan),
                    "te1_exit_date": row.get("te1_exit_date", pd.NaT),
                    "t_e": row.get("t_e", pd.NaT),
                }
            )
    return pd.DataFrame(rows)


def _group_mean_target(decisions: pd.DataFrame) -> float:
    selected = decisions[decisions["decision"].eq("accepted")]
    if selected.empty:
        return float("nan")
    group_means = selected.groupby(["benchmark", "entry_date"])["te1_active_net_return_pct"].mean()
    return float(group_means.mean()) if not group_means.empty else float("nan")


def _inner_select(outer_train: pd.DataFrame, outer_fold: int) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    inner_rows: list[dict[str, Any]] = []
    inner_folds = _chronological_folds(outer_train, n_splits=3)
    for inner_fold, (_inner_train_mask, inner_validation_mask) in enumerate(inner_folds):
        validation = outer_train.loc[inner_validation_mask].copy()
        for tie_breaker, threshold in CONFIGS:
            decisions = _static_decisions(validation, tie_breaker, threshold, inner_fold, "inner_validation")
            inner_rows.append(
                {
                    "outer_fold": outer_fold,
                    "inner_fold": inner_fold,
                    "tie_breaker": tie_breaker,
                    "admission_threshold": threshold,
                    "admission_threshold_label": THRESHOLD_LABELS[threshold],
                    "validation_start": validation["entry_date"].min(),
                    "validation_end": validation["entry_date"].max(),
                    "validation_rows": len(validation),
                    "selected_rows": int(decisions["decision"].eq("accepted").sum()),
                    "mean_group_target_a_pct": _group_mean_target(decisions),
                    "median_selected_target_a_pct": float(pd.to_numeric(decisions.loc[decisions["decision"].eq("accepted"), "te1_active_net_return_pct"], errors="coerce").median()),
                }
            )
    inner_detail = pd.DataFrame(inner_rows)
    if inner_detail.empty:
        fallback = {"outer_fold": outer_fold, "tie_breaker": "expected_slot_days", "admission_threshold": None, "selection_source": "fallback_no_inner_fold"}
        return fallback, inner_detail, pd.DataFrame()
    summary = (
        inner_detail.groupby(["tie_breaker", "admission_threshold", "admission_threshold_label"], dropna=False, as_index=False)
        .agg(
            inner_fold_mean_group_target_a_pct=("mean_group_target_a_pct", "mean"),
            inner_fold_median_group_target_a_pct=("mean_group_target_a_pct", "median"),
            inner_total_selected_rows=("selected_rows", "sum"),
            inner_fold_count=("inner_fold", "nunique"),
        )
        .sort_values(
            ["inner_fold_mean_group_target_a_pct", "inner_fold_median_group_target_a_pct", "inner_total_selected_rows", "tie_breaker", "admission_threshold_label"],
            ascending=[False, False, False, True, True],
            kind="mergesort",
        )
    )
    chosen = summary.iloc[0]
    choice = {
        "outer_fold": outer_fold,
        "tie_breaker": str(chosen["tie_breaker"]),
        "admission_threshold": None if pd.isna(chosen["admission_threshold"]) else float(chosen["admission_threshold"]),
        "admission_threshold_label": str(chosen["admission_threshold_label"]),
        "selection_source": "inner_chronological_validation_only",
        "inner_fold_mean_group_target_a_pct": float(chosen["inner_fold_mean_group_target_a_pct"]),
    }
    return choice, inner_detail, summary


def _rank_universe(frame: pd.DataFrame, tie_breaker: str) -> pd.DataFrame:
    out = frame.copy()
    out["_selector_rank"] = 10**9
    out["_admission_score"] = pd.to_numeric(out.get("feat_connection_strength"), errors="coerce")
    for _, day in out.groupby(["benchmark", "analysis_split", "entry_date"], sort=False):
        order = _rank_order(day, tie_breaker)
        out.loc[order, "_selector_rank"] = np.arange(len(order), dtype=int)
    return out


def _config_slug(tie_breaker: str, threshold: float | None) -> str:
    return f"{tie_breaker}__threshold_{THRESHOLD_LABELS[threshold].replace('.', '_')}"


def _exact_replay(
    frame: pd.DataFrame,
    prices: dict,
    probs: dict,
    benchmark: str,
    fold: int,
    tie_breaker: str,
    threshold: float | None,
    output_dir: Path,
    outer_start: pd.Timestamp,
    outer_end: pd.Timestamp,
) -> tuple[dict[str, Any], pd.DataFrame]:
    subset = frame[frame["benchmark"].eq(benchmark)].copy()
    if subset.empty:
        return {"outer_fold": fold, "benchmark": benchmark, "tie_breaker": tie_breaker, "admission_threshold": threshold, "n_trades": 0}, pd.DataFrame()
    start = pd.to_datetime(subset["t_theta"], utc=True).min().normalize()
    end = pd.to_datetime(subset["t_e"], utc=True).max().normalize()
    admission = make_admission_policy("min_connection_strength", threshold) if threshold is not None else None
    trades, equity, stats, _meta, allocation, disposition = sim_opp_cost(
        subset,
        prices,
        probs,
        dict(PORT_DEFAULT),
        bench_sym=benchmark,
        initial=INITIAL_CAPITAL,
        start_date=start,
        end_date=end,
        allocation_mode=ALLOCATION_FIFO,
        collect_allocation_log=True,
        admission_policy=admission,
    )
    if not trades.empty:
        exit_date = pd.to_datetime(trades["exit_date"], utc=True, errors="coerce").dt.normalize()
        candidate_te = pd.to_datetime(trades["candidate_t_e"], utc=True, errors="coerce").dt.normalize()
        if (exit_date >= candidate_te).any():
            raise AssertionError(f"nested fold {fold}/{benchmark} generated exit_date >= candidate_t_e")
    run_dir = output_dir / "replays" / f"outer_fold_{fold}" / _config_slug(tie_breaker, threshold)
    run_dir.mkdir(parents=True, exist_ok=True)
    trades.to_csv(run_dir / f"trades_{benchmark.lower()}.csv", index=False)
    equity.to_csv(run_dir / f"equity_{benchmark.lower()}.csv", index=False)
    allocation.to_csv(run_dir / f"allocation_{benchmark.lower()}.csv", index=False)
    disposition.to_csv(run_dir / f"disposition_{benchmark.lower()}.csv", index=False)
    result = {
        "evaluation_scope": "nested_outer_exact_dynamic_replay",
        "outer_fold": fold,
        "outer_start": outer_start,
        "outer_end": outer_end,
        "benchmark": benchmark,
        "tie_breaker": tie_breaker,
        "admission_threshold": threshold,
        "admission_threshold_label": THRESHOLD_LABELS[threshold],
        **stats,
        **_active_metrics(equity),
        "n_trades": int(len(trades)),
        "turnover_notional": float(trades.get("_asset_entry_notional", pd.Series(dtype=float)).sum()) if not trades.empty else 0.0,
        "selected_decisions": int((allocation.get("decision", pd.Series(dtype=object)) == "selected").sum()) if not allocation.empty else 0,
        "rejected_or_skipped_decisions": int((allocation.get("decision", pd.Series(dtype=object)) == "skipped").sum()) if not allocation.empty else 0,
        "admission_rejected": int((allocation.get("skip_reason", pd.Series(dtype=object)) == "admission_reject").sum()) if not allocation.empty else 0,
        "blocked_by_capacity": int((allocation.get("skip_reason", pd.Series(dtype=object)) == "max_concurrent").sum()) if not allocation.empty else 0,
    }
    return result, trades


def _mean_by_benchmark(summary: pd.DataFrame, metric: str) -> pd.DataFrame:
    return summary.groupby(["tie_breaker", "admission_threshold", "admission_threshold_label", "benchmark"], dropna=False, as_index=False)[metric].mean()


def _select_research_freeze(exact: pd.DataFrame) -> dict[str, Any]:
    grouped = (
        exact.groupby(["tie_breaker", "admission_threshold", "admission_threshold_label"], dropna=False)
        .agg(
            mean_excess_return=("excess_return", "mean"),
            mean_active_information_ratio=("active_information_ratio", "mean"),
            mean_active_max_drawdown_pct=("active_max_drawdown_pct", "mean"),
            mean_trade_count=("n_trades", "mean"),
            outer_fold_count=("outer_fold", "nunique"),
        )
        .reset_index()
    )
    benchmark = _mean_by_benchmark(exact, "excess_return")
    pivot = benchmark.pivot_table(index=["tie_breaker", "admission_threshold", "admission_threshold_label"], columns="benchmark", values="excess_return", aggfunc="mean").reset_index()
    grouped = grouped.merge(pivot, on=["tie_breaker", "admission_threshold", "admission_threshold_label"], how="left")
    grouped["min_benchmark_excess_return"] = grouped[[column for column in ("SPY", "QQQ") if column in grouped.columns]].min(axis=1)
    grouped = grouped.sort_values(
        ["min_benchmark_excess_return", "mean_excess_return", "mean_active_information_ratio", "mean_active_max_drawdown_pct", "admission_threshold_label", "tie_breaker"],
        ascending=[False, False, False, False, True, True],
        kind="mergesort",
    )
    chosen = grouped.iloc[0]
    return {
        "label": "research_frozen_selector",
        "ranker": "connection_strength_descending",
        "tie_breaker": str(chosen["tie_breaker"]),
        "admission_threshold": None if pd.isna(chosen["admission_threshold"]) else float(chosen["admission_threshold"]),
        "admission_threshold_label": str(chosen["admission_threshold_label"]),
        "selection_rule": "maximize the minimum of SPY and QQQ outer-fold mean excess; tie-break mean excess, active IR, then less-negative mean active drawdown",
        "selection_source": "full_nested_chronological_outer_evaluation_on_training_development_data",
        "outer_fold_count": int(chosen["outer_fold_count"]),
        "mean_excess_return": float(chosen["mean_excess_return"]),
        "min_benchmark_excess_return": float(chosen["min_benchmark_excess_return"]),
        "spy_mean_excess_return": float(chosen.get("SPY", np.nan)),
        "qqq_mean_excess_return": float(chosen.get("QQQ", np.nan)),
        "mean_active_information_ratio": float(chosen["mean_active_information_ratio"]),
        "mean_active_max_drawdown_pct": float(chosen["mean_active_max_drawdown_pct"]),
        "lockbox_opened": False,
        "lockbox_reserved_for_final_modular_pipeline_evaluation": True,
        "do_not_change_during_exit_research": True,
        "te_is_never_exit": True,
    }


def run_nested_final_validation(
    candidates_path: Path | str = DEFAULT_CANDIDATES,
    capacity_path: Path | str = DEFAULT_CAPACITY,
    universe_path: Path | str = DEFAULT_UNIVERSE,
    source_path: Path | str = DEFAULT_SOURCE,
    prices_path: Path | str = DEFAULT_PRICES,
    probs_path: Path | str = DEFAULT_PROBS,
    output_dir: Path | str = DEFAULT_OUTPUT,
    n_outer: int = 5,
) -> dict[str, Path]:
    candidates_path, capacity_path, universe_path, source_path, prices_path, probs_path, output_dir = map(
        Path, (candidates_path, capacity_path, universe_path, source_path, prices_path, probs_path, output_dir)
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = _merge_capacity(_read_candidates(candidates_path), _read_capacity(capacity_path))
    # The later lockbox is not an input to this validation.  Only the existing
    # development/training rows are retained and every output asserts that
    # split is train.
    train = candidates[candidates["analysis_split"].astype(str).str.lower().eq("train")].copy()
    if train.empty:
        raise ValueError("Nested final validation found no training/development rows")
    train["analysis_split"] = "train"
    folds = _chronological_folds(train, n_outer)
    if not folds:
        raise ValueError("Not enough chronological training groups for nested outer folds")
    inner_detail_rows, inner_summary_rows, choice_rows, outer_fold_rows, outer_decision_rows = [], [], [], [], []
    exact_rows: list[dict[str, Any]] = []
    prices = pickle.loads(prices_path.read_bytes())
    probs = pickle.loads(probs_path.read_bytes())
    universe = _load_universe(universe_path, source_path)
    universe = universe[universe["analysis_split"].astype(str).str.lower().eq("train")].copy()
    key_columns = ["benchmark", "entry_date", "symbol"]
    train_keys = train[key_columns].drop_duplicates()
    universe = universe.merge(train_keys, on=key_columns, how="inner")
    for outer_fold, (outer_train_mask, outer_validation_mask) in enumerate(folds):
        outer_train = train.loc[outer_train_mask].copy()
        outer_validation = train.loc[outer_validation_mask].copy()
        choice, inner_detail, inner_summary = _inner_select(outer_train, outer_fold)
        if not inner_detail.empty:
            inner_detail_rows.append(inner_detail)
        if not inner_summary.empty:
            inner_summary = inner_summary.copy()
            inner_summary["outer_fold"] = outer_fold
            inner_summary_rows.append(inner_summary)
        choice_rows.append(choice)
        family_counts = outer_validation["event_family"].fillna("other").astype(str).value_counts().sort_index().to_dict()
        outer_fold_rows.append(
            {
                "outer_fold": outer_fold,
                "outer_train_start": outer_train["entry_date"].min(),
                "outer_train_end": outer_train["entry_date"].max(),
                "outer_validation_start": outer_validation["entry_date"].min(),
                "outer_validation_end": outer_validation["entry_date"].max(),
                "outer_train_groups": int(outer_train[["benchmark", "entry_date"]].drop_duplicates().shape[0]),
                "outer_validation_groups": int(outer_validation[["benchmark", "entry_date"]].drop_duplicates().shape[0]),
                "outer_validation_rows": int(len(outer_validation)),
                "outer_validation_event_family_composition": json.dumps(family_counts, sort_keys=True),
                "lockbox_rows_evaluated": 0,
                "lockbox_opened": False,
            }
        )
        # Full outer decision table: every predeclared tie/threshold cell is
        # recorded, not just the inner-selected cell.
        for tie_breaker, threshold in CONFIGS:
            decisions = _static_decisions(outer_validation, tie_breaker, threshold, outer_fold, "outer_validation")
            if not decisions.empty:
                outer_decision_rows.append(decisions)
            config_universe = universe[universe["entry_date"].isin(outer_validation["entry_date"].unique())].copy()
            config_universe = _rank_universe(config_universe, tie_breaker)
            for benchmark in ("SPY", "QQQ"):
                replay, _trades = _exact_replay(
                    config_universe,
                    prices,
                    probs,
                    benchmark,
                    outer_fold,
                    tie_breaker,
                    threshold,
                    output_dir,
                    outer_validation["entry_date"].min(),
                    outer_validation["entry_date"].max(),
                )
                exact_rows.append(replay)
    outer_decisions = pd.concat(outer_decision_rows, ignore_index=True)
    inner_detail = pd.concat(inner_detail_rows, ignore_index=True) if inner_detail_rows else pd.DataFrame()
    inner_summary = pd.concat(inner_summary_rows, ignore_index=True) if inner_summary_rows else pd.DataFrame()
    exact = pd.DataFrame(exact_rows)
    outer_folds = pd.DataFrame(outer_fold_rows)
    choices = pd.DataFrame(choice_rows)
    # Keep only valid train outer results in all persisted summaries.
    outer_decisions["lockbox_opened"] = False
    exact["lockbox_opened"] = False
    outer_decisions.to_csv(output_dir / "nested_outer_decisions.csv", index=False)
    inner_detail.to_csv(output_dir / "nested_inner_fold_detail.csv", index=False)
    inner_summary.to_csv(output_dir / "nested_inner_choice_summary.csv", index=False)
    outer_folds.to_csv(output_dir / "nested_outer_fold_manifest.csv", index=False)
    choices.to_csv(output_dir / "nested_outer_choices.csv", index=False)
    exact.to_csv(output_dir / "nested_outer_exact_replay_summary.csv", index=False)
    factorial_rows = []
    for name, (tie, threshold) in FACTORIAL_CONFIGS.items():
        part = exact[exact["tie_breaker"].eq(tie) & (exact["admission_threshold"].fillna(-999).eq(-999 if threshold is None else threshold))].copy()
        part["factorial_cell"] = name
        factorial_rows.append(part)
    factorial = pd.concat(factorial_rows, ignore_index=True) if factorial_rows else pd.DataFrame()
    factorial.to_csv(output_dir / "factorial_ablation_outer_replays.csv", index=False)
    factorial_summary = (
        factorial.groupby(["factorial_cell", "tie_breaker", "admission_threshold", "admission_threshold_label", "benchmark"], dropna=False, as_index=False)
        .agg(
            outer_fold_count=("outer_fold", "nunique"),
            mean_total_return=("total_return", "mean"),
            mean_benchmark_return=("benchmark_return", "mean"),
            mean_excess_return=("excess_return", "mean"),
            mean_trade_count=("n_trades", "mean"),
            mean_active_information_ratio=("active_information_ratio", "mean"),
            mean_active_max_drawdown_pct=("active_max_drawdown_pct", "mean"),
        )
    )
    factorial_summary.to_csv(output_dir / "factorial_ablation_summary.csv", index=False)
    threshold_stability = exact[
        ["outer_fold", "outer_start", "outer_end", "benchmark", "tie_breaker", "admission_threshold", "admission_threshold_label", "total_return", "benchmark_return", "excess_return", "n_trades", "active_information_ratio", "active_max_drawdown_pct", "turnover_notional", "total_txn_cost"]
    ].copy()
    threshold_stability.to_csv(output_dir / "threshold_stability_outer_fold.csv", index=False)
    stability_summary = (
        threshold_stability.groupby(["tie_breaker", "admission_threshold", "admission_threshold_label", "benchmark"], dropna=False, as_index=False)
        .agg(
            outer_fold_count=("outer_fold", "nunique"),
            mean_total_return=("total_return", "mean"),
            mean_benchmark_return=("benchmark_return", "mean"),
            mean_excess_return=("excess_return", "mean"),
            mean_trade_count=("n_trades", "mean"),
            mean_active_information_ratio=("active_information_ratio", "mean"),
            mean_active_max_drawdown_pct=("active_max_drawdown_pct", "mean"),
        )
    )
    stability_summary.to_csv(output_dir / "threshold_stability_summary.csv", index=False)
    selected = []
    for _, choice in choices.iterrows():
        threshold = None if pd.isna(choice.get("admission_threshold")) else float(choice["admission_threshold"])
        selected.append(exact[exact["outer_fold"].eq(int(choice["outer_fold"])) & exact["tie_breaker"].eq(choice["tie_breaker"]) & exact["admission_threshold"].fillna(-999).eq(-999 if threshold is None else threshold)])
    selected_exact = pd.concat(selected, ignore_index=True) if selected else pd.DataFrame()
    selected_exact.to_csv(output_dir / "nested_selected_outer_exact_replays.csv", index=False)
    frozen = _select_research_freeze(exact)
    (output_dir / "research_frozen_selector.json").write_text(json.dumps(frozen, indent=2, default=str) + "\n", encoding="utf-8")
    feature_status = {
        "monotonic_and_pooled_evaluated": True,
        "verified_probability_price_disagreement_feature_available": False,
        "verified_supporting_market_snapshot_features_available": False,
        "interpretation": "current underperformance does not reject these feature hypotheses; they were not present in the evaluated model inputs",
        "lockbox_opened": False,
    }
    (output_dir / "feature_hypothesis_status.json").write_text(json.dumps(feature_status, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "label": "full_nested_chronological_oof_stage2b_final_validation",
        "candidates_sha256": _hash(candidates_path),
        "capacity_sha256": _hash(capacity_path),
        "outer_fold_count": len(folds),
        "inner_folds_per_outer_max": 3,
        "tie_breakers": list(TIE_BREAKERS),
        "threshold_grid": ["none", 0.90, 0.95, 0.98, 1.00],
        "outer_evaluation_split": "train_only_development_rows",
        "lockbox_opened": False,
        "lockbox_rows_evaluated": 0,
        "target_c_primary_training_target": False,
        "target_c_status": "diagnostic_only_continuation_policy_dependent",
        "research_frozen_selector": frozen,
        "outputs": {
            "outer_fold_manifest": str(output_dir / "nested_outer_fold_manifest.csv"),
            "inner_choices": str(output_dir / "nested_outer_choices.csv"),
            "outer_decisions": str(output_dir / "nested_outer_decisions.csv"),
            "exact_replay": str(output_dir / "nested_outer_exact_replay_summary.csv"),
            "factorial_summary": str(output_dir / "factorial_ablation_summary.csv"),
            "threshold_stability": str(output_dir / "threshold_stability_summary.csv"),
            "frozen_selector": str(output_dir / "research_frozen_selector.json"),
        },
    }
    manifest_path = output_dir / "nested_final_validation_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return {
        "manifest": manifest_path,
        "outer_folds": output_dir / "nested_outer_fold_manifest.csv",
        "inner_choices": output_dir / "nested_outer_choices.csv",
        "outer_decisions": output_dir / "nested_outer_decisions.csv",
        "exact_replay": output_dir / "nested_outer_exact_replay_summary.csv",
        "factorial": output_dir / "factorial_ablation_summary.csv",
        "threshold_stability": output_dir / "threshold_stability_summary.csv",
        "frozen_selector": output_dir / "research_frozen_selector.json",
    }


if __name__ == "__main__":
    for name, path in run_nested_final_validation().items():
        print(f"{name}: {path}")
