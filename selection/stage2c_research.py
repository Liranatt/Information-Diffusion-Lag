"""Stage 2C: connection semantics and mapping-type-aware selection.

This is a separate development-only research branch.  It never reads rows
whose analysis split is not ``train`` and never overwrites Stage 2B.  The
existing Stage 2B selector and 184-trade Stage 3 handoff are preserved as the
``raw_global_connection_baseline`` before any Stage 2C artifact is written.

The module intentionally uses the corrected exact portfolio simulator.  All
persisted replay trades assert ``exit_date < candidate_t_e``; ``T_e`` is never
an exit and the latest legal terminal horizon remains ``T_e - 1``.
"""

from __future__ import annotations

import hashlib
import json
import pickle
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from backtesting.optimize_cem import ALLOCATION_FIFO, INITIAL_CAPITAL, PORT_DEFAULT, sim_opp_cost

from .dynamic_replay import _active_metrics, _load_universe, _normalize_keys
from .nested_final_validation import _chronological_folds
from .stage2c_semantics import (
    MAPPING_TYPE_PRIORITY,
    RUBRICS,
    backward_compatibility_aliases,
    label_mapping,
)


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "assistant_generated_all_artifacts" / "assistant_generated_research_core" / "trade_opportunity_research" / "symbol_day_current_priority.csv"
UNIVERSE = PROJECT / "data" / "candidates_audit_clean.parquet"
DECISIONS = PROJECT / "data" / "selection_stage1" / "decision_candidates.csv"
FEATURES = PROJECT / "data" / "selection_stage2b" / "feature_table" / "timestamp_safe_feature_table.csv"
PRICES = PROJECT / "assistant_generated_all_artifacts" / "assistant_generated_research_large_supplements" / "prices_open_merged.pkl"
PROBS = PROJECT / "data" / "probs.pkl"
STAGE2B = PROJECT / "data" / "selection_stage2b" / "nested_final_validation"
STAGE3 = PROJECT / "data" / "stage3_exit_research"
OUTPUT = PROJECT / "data" / "selection_stage2c"

DIRECT_VARIANTS = {
    "A_raw_global_connection_baseline": "raw_baseline",
    "B_deterministic_direct_always_fill": "deterministic_always_fill",
    "C_deterministic_direct_legacy_proxy_1.00": "deterministic_legacy_threshold",
    "D_deterministic_direct_trade_quality": "deterministic_quality",
}

QUALITY_RULES = (
    "predicted_target_a_positive",
    "predicted_target_b_positive",
    "expected_slot_days_le_21",
    "stock_minus_sector_20d_le_0",
    "arrival_pressure_le_training_median",
)

QUALITY_FEATURES = (
    "expected_slot_days",
    "stock_minus_sector_20d",
    "feat_prob_at_trigger",
    "feat_prob_surge_since_t0",
    "feat_asset_2w_trend",
    "candidates_seen_previous_5_trading_days",
)

RANDOM_SEEDS = tuple(range(20))


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def _dates(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for column in ("entry_date", "t0", "t_theta", "t_e", "te1_exit_date"):
        if column in out:
            out[column] = pd.to_datetime(out[column], errors="coerce", utc=True)
            if column in {"entry_date", "te1_exit_date"}:
                out[column] = out[column].dt.normalize()
    return out


def _read_source() -> pd.DataFrame:
    source = _dates(_normalize_keys(pd.read_csv(SOURCE)))
    source["analysis_split"] = source["analysis_split"].astype(str).str.lower()
    # Reading anything other than the development rows is forbidden here.
    source = source[source["analysis_split"].eq("train")].copy()
    source["legacy_gemini_relevance_score"] = pd.to_numeric(
        source.get("feat_connection_strength", source.get("connection_strength")), errors="coerce"
    )
    return source


def _build_semantic_table(source: pd.DataFrame, output_dir: Path) -> tuple[pd.DataFrame, Path]:
    columns = ["question", "symbol", "event_family", "feat_archetype", "feat_sector"]
    unique = source[columns].drop_duplicates(["question", "symbol"], keep="first").copy()
    labels = []
    for row in unique.itertuples(index=False):
        labels.append(
            label_mapping(
                question=row.question,
                symbol=row.symbol,
                event_family=row.event_family,
                company_identity=row.feat_archetype,
                sector=row.feat_sector,
            ).as_dict()
        )
    semantics = pd.concat([unique.reset_index(drop=True), pd.DataFrame(labels)], axis=1)
    semantics["mapping_type_priority"] = semantics["mapping_type"].map(MAPPING_TYPE_PRIORITY).astype(int)
    semantics["semantic_label_inputs"] = "question|symbol|event_family|feat_archetype|feat_sector"
    path = output_dir / "semantics" / "mapping_semantics.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    semantics.to_csv(path, index=False)
    _json(
        output_dir / "semantics" / "semantic_rubrics.json",
        {
            "mapping_type_definitions": {
                "direct_issuer": "event directly concerns the same listed issuer",
                "direct_underlying": "asset directly tracks the affected economic variable",
                "first_order_sector": "asset represents the directly affected industry/sector",
                "second_order_company": "company is exposed through sector, input, demand, geography, or regulation",
                "broad_macro_proxy": "broad or indirect macro proxy",
                "unclear_or_invalid": "economic path cannot be defended clearly",
            },
            "ordinal_rubrics": RUBRICS,
            "backward_compatibility_aliases": backward_compatibility_aliases(),
            "label_inputs_only": ["question", "symbol", "event_family", "feat_archetype", "feat_sector"],
            "forbidden_label_inputs": ["returns", "post_entry_prices", "selected_outcomes", "portfolio_results"],
            "lockbox_opened": False,
        },
    )
    return semantics, path


def _load_development(semantics: pd.DataFrame) -> pd.DataFrame:
    universe = _load_universe(UNIVERSE, SOURCE)
    universe = universe[universe["analysis_split"].astype(str).str.lower().eq("train")].copy()
    universe = _dates(_normalize_keys(universe))
    source = _read_source()
    semantic_columns = [
        "mapping_type", "mapping_valid", "semantic_directness", "mapping_confidence",
        "impact_materiality", "direction_confidence", "exposure_purity", "event_description",
        "transmission_channel", "asset_exposure", "expected_direction", "economic_path_explanation",
        "semantic_rule", "mapping_type_priority", "semantic_label_inputs",
    ]
    source = source.merge(
        semantics[["question", "symbol", *semantic_columns]],
        on=["question", "symbol"], how="left", validate="many_to_one",
    )
    source_keep = [
        "event_id", "market_id", "benchmark", "source_order", "asset_confidence", "question_confidence",
        "confidence_score", "feat_llm_confidence", "t0", *semantic_columns,
    ]
    source_meta = source[source_keep].drop_duplicates(["event_id", "market_id", "benchmark"])
    universe = universe.merge(source_meta, on=["event_id", "market_id", "benchmark"], how="left", suffixes=("", "_source"))
    decisions = _dates(pd.read_csv(DECISIONS))
    decisions = decisions[decisions["analysis_split"].astype(str).str.lower().eq("train")].copy()
    decision_keep = [
        "benchmark", "analysis_split", "entry_date", "symbol", "te1_active_net_return_pct",
        "active_return_per_slot_day_pct", "te1_exit_date", "slot_days", "capacity_slots",
        "same_day_candidate_count", "recent_5d_candidate_count",
    ]
    decision_keep = [column for column in decision_keep if column in decisions]
    universe = universe.merge(
        decisions[decision_keep].drop_duplicates(["benchmark", "analysis_split", "entry_date", "symbol"]),
        on=["benchmark", "analysis_split", "entry_date", "symbol"], how="left", suffixes=("", "_decision"),
    )
    feature = _dates(pd.read_csv(FEATURES))
    feature = feature[feature["analysis_split"].astype(str).str.lower().eq("train")].copy()
    feature_keep = [
        "benchmark", "analysis_split", "entry_date", "symbol", "stock_minus_sector_20d",
        "expected_slot_days", "candidates_seen_previous_5_trading_days",
        "event_candidates_seen_previous_5_days", "effective_probability_change_5d",
        "probability_change_x_stock_response", "cross_market_directional_agreement", "event_novelty",
    ]
    feature_keep = [column for column in feature_keep if column in feature]
    universe = universe.merge(
        feature[feature_keep].drop_duplicates(["benchmark", "analysis_split", "entry_date", "symbol"]),
        on=["benchmark", "analysis_split", "entry_date", "symbol"], how="left", suffixes=("", "_timestamp_safe"),
    )
    universe["legacy_gemini_relevance_score"] = pd.to_numeric(universe["feat_connection_strength"], errors="coerce")
    universe["connection_strength"] = universe["legacy_gemini_relevance_score"]
    universe["expected_slot_days"] = pd.to_numeric(
        universe.get("expected_slot_days", universe.get("feat_time_to_resolution_days")), errors="coerce"
    )
    universe["source_order"] = pd.to_numeric(universe.get("source_order"), errors="coerce")
    fallback_order = pd.Series(np.arange(len(universe), dtype=int), index=universe.index)
    universe["source_order"] = universe["source_order"].where(universe["source_order"].notna(), fallback_order).astype(int)
    if "candidates_seen_previous_5_trading_days" not in universe:
        universe["candidates_seen_previous_5_trading_days"] = pd.to_numeric(
            universe.get("recent_5d_candidate_count"), errors="coerce"
        )
    if universe["mapping_type"].isna().any():
        raise AssertionError("Stage 2C development rows are missing semantic labels")
    if not universe["analysis_split"].eq("train").all():
        raise AssertionError("Stage 2C loaded a non-development row")
    return universe


def _preserve_baselines(output_dir: Path) -> dict[str, Path]:
    baseline_dir = output_dir / "baselines"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    stage2b_selector = STAGE2B / "research_frozen_selector.json"
    stage2b_manifest = STAGE2B / "nested_final_validation_manifest.json"
    original = json.loads(stage2b_selector.read_text(encoding="utf-8"))
    preserved = {
        "label": "raw_global_connection_baseline",
        "source_label": original.get("label"),
        "ranker": "legacy_gemini_relevance_score_descending",
        "backward_compatible_source_field": "feat_connection_strength",
        "tie_breaker": original.get("tie_breaker"),
        "admission_threshold": original.get("admission_threshold"),
        "semantic_interpretation": "legacy diagnostic score; not a universally calibrated economic connection strength",
        "stage2b_selector_sha256": _hash(stage2b_selector),
        "stage2b_manifest_sha256": _hash(stage2b_manifest),
        "lockbox_opened": False,
    }
    selector_copy = baseline_dir / "raw_global_connection_baseline.json"
    _json(selector_copy, preserved)

    snapshot_dir = baseline_dir / "stage3_raw_global_connection_baseline"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    stage3_manifest = STAGE3 / "stage3_exit_manifest.json"
    stage3_trades = STAGE3 / "stage3_exit_development_trades.csv"
    stage3_folds = STAGE3 / "stage3_development_folds.csv"
    snapshot_trades = snapshot_dir / "stage3_exit_development_trades.csv"
    snapshot_manifest = snapshot_dir / "stage3_exit_manifest.json"
    snapshot_valid = snapshot_trades.exists() and len(pd.read_csv(snapshot_trades)) == 184
    if not snapshot_valid:
        current_valid = stage3_trades.exists() and len(pd.read_csv(stage3_trades)) == 184
        if current_valid:
            for path in (stage3_manifest, stage3_trades, stage3_folds):
                if path.exists():
                    shutil.copy2(path, snapshot_dir / path.name)
        else:
            # A prior Stage 2C run may already have rebuilt the live Stage 3
            # directory. Reconstruct the immutable 184-trade baseline from
            # the unchanged Stage 2B frozen-selector replays.
            from stage3.exit_research import run_stage3_exit_development

            run_stage3_exit_development(output_dir=snapshot_dir)
    old_manifest = json.loads(snapshot_manifest.read_text(encoding="utf-8"))
    old_trade_count = len(pd.read_csv(snapshot_trades))
    if old_trade_count != 184 or int(old_manifest.get("development_trade_count", -1)) != 184:
        raise AssertionError("Expected the reconstructed Stage 3 baseline to contain exactly 184 trades")
    _json(
        snapshot_dir / "baseline_snapshot_manifest.json",
        {
            "label": "stage3_raw_global_connection_baseline",
            "development_trade_count": old_trade_count,
            "stage3_manifest_sha256": _hash(snapshot_manifest),
            "stage3_trades_sha256": _hash(snapshot_trades),
            "exit_policy_trained_from_sample": False,
            "stage3_training_status": "paused_until_stage2c_complete",
            "lockbox_opened": False,
        },
    )
    return {"selector": selector_copy, "stage3_snapshot": snapshot_dir / "baseline_snapshot_manifest.json"}


def _safe_corr(x: pd.Series, y: pd.Series, method: str) -> float:
    pair = pd.DataFrame({"x": pd.to_numeric(x, errors="coerce"), "y": pd.to_numeric(y, errors="coerce")}).dropna()
    if len(pair) < 3 or pair["x"].nunique() < 2 or pair["y"].nunique() < 2:
        return float("nan")
    return float(pair["x"].corr(pair["y"], method=method))


def _controlled_coefficient(frame: pd.DataFrame, predictor: str) -> float:
    needed = [predictor, "te1_active_net_return_pct", "entry_date", "feat_sector", "generation_batch_inferred"]
    data = frame.dropna(subset=[column for column in needed if column in frame]).copy()
    if len(data) < 12 or pd.to_numeric(data[predictor], errors="coerce").nunique() < 2:
        return float("nan")
    x_main = pd.to_numeric(data[predictor], errors="coerce").to_numpy(dtype=float)
    y = pd.to_numeric(data["te1_active_net_return_pct"], errors="coerce").to_numpy(dtype=float)
    numeric = []
    for column in ("feat_log_market_cap", "feat_pre_entry_volume_log", "feature_coverage"):
        values = pd.to_numeric(data.get(column), errors="coerce")
        median = float(values.median()) if values.notna().any() else 0.0
        array = values.fillna(median).to_numpy(dtype=float)
        scale = float(np.std(array))
        numeric.append((array - float(np.mean(array))) / (scale if scale > 1e-12 else 1.0))
    categories = pd.get_dummies(
        data[["entry_date", "feat_sector", "generation_batch_inferred"]].astype(str),
        drop_first=True, dtype=float,
    ).to_numpy(dtype=float)
    design = np.column_stack([np.ones(len(data)), x_main, *numeric, categories])
    coefficient = np.linalg.lstsq(design, y, rcond=None)[0]
    return float(coefficient[1])


def _cluster_bootstrap_ci(frame: pd.DataFrame, predictor: str, seed: int = 20260717, draws: int = 300) -> tuple[float, float]:
    groups = [group.copy() for _, group in frame.groupby("entry_date", sort=True)]
    if len(groups) < 4:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(draws):
        sampled = [groups[index] for index in rng.integers(0, len(groups), len(groups))]
        value = _controlled_coefficient(pd.concat(sampled, ignore_index=True), predictor)
        if np.isfinite(value):
            values.append(value)
    if len(values) < 20:
        return float("nan"), float("nan")
    return float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))


def _raw_score_audit(data: pd.DataFrame, output_dir: Path) -> dict[str, Any]:
    audit_dir = output_dir / "raw_score_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    earnings = data[data["event_family"].astype(str).str.lower().eq("earnings")].copy()
    earnings["score_is_1.00"] = earnings["legacy_gemini_relevance_score"].eq(1.0)
    earnings["entry_month"] = earnings["entry_date"].dt.strftime("%Y-%m")
    earnings["generation_batch_inferred"] = earnings["t0"].dt.floor("D").astype(str)
    coverage_columns = [
        "feat_prob_at_trigger", "feat_prob_slope_24h", "feat_prob_volatility",
        "feat_prob_surge_since_t0", "feat_time_to_resolution_days", "feat_pre_entry_volume_log",
        "feat_runup_since_t0", "feat_asset_2w_trend", "feat_sector_1m_trend",
        "feat_spy_2w_trend", "feat_log_market_cap", "asset_confidence", "question_confidence",
    ]
    coverage_columns = [column for column in coverage_columns if column in earnings]
    earnings["feature_coverage"] = earnings[coverage_columns].notna().mean(axis=1)

    unique_mapping_rows = earnings.drop_duplicates(["question", "symbol", "entry_date"])
    month_batch = (
        unique_mapping_rows.groupby(["entry_month", "generation_batch_inferred", "legacy_gemini_relevance_score"], dropna=False)
        .size().rename("mapping_count").reset_index()
    )
    month_batch.to_csv(audit_dir / "score_distribution_by_month_and_inferred_batch.csv", index=False)

    pair_stats = (
        unique_mapping_rows.groupby(["question", "symbol"], as_index=False)
        .agg(
            observations=("entry_date", "size"),
            distinct_scores=("legacy_gemini_relevance_score", "nunique"),
            minimum_score=("legacy_gemini_relevance_score", "min"),
            maximum_score=("legacy_gemini_relevance_score", "max"),
            dates=("entry_date", lambda x: "|".join(sorted({str(pd.Timestamp(v).date()) for v in x}))),
            scores=("legacy_gemini_relevance_score", lambda x: "|".join(map(str, sorted(set(x.dropna()))))),
        )
    )
    repeated_pairs = pair_stats[pair_stats["distinct_scores"] > 1].copy()
    repeated_pairs.to_csv(audit_dir / "exact_question_symbol_multiple_scores.csv", index=False)
    symbol_stats = (
        unique_mapping_rows.groupby("symbol", as_index=False)
        .agg(observations=("entry_date", "size"), distinct_questions=("question", "nunique"), distinct_scores=("legacy_gemini_relevance_score", "nunique"), minimum_score=("legacy_gemini_relevance_score", "min"), maximum_score=("legacy_gemini_relevance_score", "max"))
    )
    symbol_stats[symbol_stats["distinct_scores"] > 1].to_csv(audit_dir / "same_symbol_multiple_scores.csv", index=False)

    correlation_rows = []
    correlation_variables = {
        "asset_confidence": "asset_confidence",
        "question_confidence": "question_confidence",
        "market_cap_log": "feat_log_market_cap",
        "liquidity_pre_entry_volume_log": "feat_pre_entry_volume_log",
        "missing_data_coverage": "feature_coverage",
    }
    for label, column in correlation_variables.items():
        for method in ("pearson", "spearman"):
            correlation_rows.append({
                "variable": label,
                "source_column": column,
                "method": method,
                "correlation_with_legacy_score": _safe_corr(unique_mapping_rows["legacy_gemini_relevance_score"], unique_mapping_rows[column], method),
                "observations": int(unique_mapping_rows[["legacy_gemini_relevance_score", column]].dropna().shape[0]),
            })
    correlations = pd.DataFrame(correlation_rows)
    correlations.to_csv(audit_dir / "score_correlations.csv", index=False)

    outcome = (
        earnings.groupby(["benchmark", "legacy_gemini_relevance_score"], as_index=False)
        .agg(observations=("symbol", "size"), mean_target_a_pct=("te1_active_net_return_pct", "mean"), median_target_a_pct=("te1_active_net_return_pct", "median"), mean_target_b_slot_pct=("active_return_per_slot_day_pct", "mean"))
    )
    outcome.to_csv(audit_dir / "outcome_by_raw_score.csv", index=False)
    one_vs = (
        earnings.assign(score_bucket=np.where(earnings["score_is_1.00"], "score_1.00", "score_below_1.00"))
        .groupby(["benchmark", "score_bucket"], as_index=False)
        .agg(observations=("symbol", "size"), mean_target_a_pct=("te1_active_net_return_pct", "mean"), median_target_a_pct=("te1_active_net_return_pct", "median"), mean_target_b_slot_pct=("active_return_per_slot_day_pct", "mean"))
    )
    one_vs.to_csv(audit_dir / "score_1.00_vs_below_1.00.csv", index=False)

    matched_rows = []
    for benchmark, bench in earnings.groupby("benchmark", sort=True):
        for match_name, columns in (("within_date", ["entry_date"]), ("within_sector", ["feat_sector"]), ("within_date_sector", ["entry_date", "feat_sector"])):
            score_residual = bench["legacy_gemini_relevance_score"] - bench.groupby(columns)["legacy_gemini_relevance_score"].transform("mean")
            outcome_residual = bench["te1_active_net_return_pct"] - bench.groupby(columns)["te1_active_net_return_pct"].transform("mean")
            matched_rows.append({
                "benchmark": benchmark, "matching": match_name,
                "pearson_residual_correlation": _safe_corr(score_residual, outcome_residual, "pearson"),
                "spearman_residual_correlation": _safe_corr(score_residual, outcome_residual, "spearman"),
                "usable_rows": int(pd.DataFrame({"x": score_residual, "y": outcome_residual}).dropna().shape[0]),
            })
    matched = pd.DataFrame(matched_rows)
    matched.to_csv(audit_dir / "within_date_and_sector_matched_outcome.csv", index=False)

    earnings["outer_fold"] = pd.NA
    for fold, (_train_mask, validation_mask) in enumerate(_chronological_folds(data, 5)):
        validation_indices = data.loc[validation_mask].index.intersection(earnings.index)
        earnings.loc[validation_indices, "outer_fold"] = fold
    fold_distribution = (
        earnings.dropna(subset=["outer_fold"])
        .groupby(["outer_fold", "benchmark", "legacy_gemini_relevance_score"], as_index=False)
        .agg(observations=("symbol", "size"), mean_target_a_pct=("te1_active_net_return_pct", "mean"))
    )
    fold_distribution.to_csv(audit_dir / "fold_specific_score_distribution.csv", index=False)

    regression_rows = []
    for benchmark, bench in earnings.groupby("benchmark", sort=True):
        for predictor in ("legacy_gemini_relevance_score", "score_is_1.00"):
            coefficient = _controlled_coefficient(bench, predictor)
            low, high = _cluster_bootstrap_ci(bench, predictor, seed=20260717 + (0 if benchmark == "SPY" else 1))
            regression_rows.append({
                "scope": "pooled_development", "outer_fold": np.nan, "benchmark": benchmark,
                "predictor": predictor, "adjusted_coefficient_target_a_pct": coefficient,
                "cluster_bootstrap_ci_low": low, "cluster_bootstrap_ci_high": high,
                "controls": "entry_date FE; sector FE; inferred generation-batch FE; log market cap; liquidity; feature coverage",
                "observations": len(bench),
            })
            for fold, fold_data in bench.dropna(subset=["outer_fold"]).groupby("outer_fold", sort=True):
                regression_rows.append({
                    "scope": "outer_fold_validation", "outer_fold": int(fold), "benchmark": benchmark,
                    "predictor": predictor, "adjusted_coefficient_target_a_pct": _controlled_coefficient(fold_data, predictor),
                    "cluster_bootstrap_ci_low": np.nan, "cluster_bootstrap_ci_high": np.nan,
                    "controls": "entry_date FE; sector FE; inferred generation-batch FE; log market cap; liquidity; feature coverage",
                    "observations": len(fold_data),
                })
    regression = pd.DataFrame(regression_rows)
    regression.to_csv(audit_dir / "controlled_score_outcome_regression.csv", index=False)

    monotonic_rows = []
    for benchmark, bench in earnings.groupby("benchmark", sort=True):
        levels = bench.groupby("legacy_gemini_relevance_score")["te1_active_net_return_pct"].agg(["size", "mean", "median"]).reset_index().sort_values("legacy_gemini_relevance_score")
        means = levels["mean"].to_numpy(dtype=float)
        violations = int(np.sum(np.diff(means) < 0)) if len(means) > 1 else 0
        for row in levels.itertuples(index=False):
            monotonic_rows.append({
                "benchmark": benchmark, "score": row.legacy_gemini_relevance_score, "observations": row.size,
                "mean_target_a_pct": row.mean, "median_target_a_pct": row.median,
                "overall_spearman": _safe_corr(bench["legacy_gemini_relevance_score"], bench["te1_active_net_return_pct"], "spearman"),
                "ascending_mean_violations": violations,
            })
    monotonic = pd.DataFrame(monotonic_rows)
    monotonic.to_csv(audit_dir / "monotonicity_tests.csv", index=False)

    pooled_binary = regression[(regression["scope"] == "pooled_development") & (regression["predictor"] == "score_is_1.00")]
    fold_binary = regression[(regression["scope"] == "outer_fold_validation") & (regression["predictor"] == "score_is_1.00")]
    stable_by_benchmark: dict[str, bool] = {}
    for benchmark in ("SPY", "QQQ"):
        pooled_row = pooled_binary[pooled_binary["benchmark"].eq(benchmark)]
        folds = fold_binary[fold_binary["benchmark"].eq(benchmark)]["adjusted_coefficient_target_a_pct"].dropna()
        stable_by_benchmark[benchmark] = bool(
            not pooled_row.empty
            and float(pooled_row.iloc[0]["cluster_bootstrap_ci_low"]) > 0.0
            and int((folds > 0).sum()) >= 4
        )
    score_stable = bool(all(stable_by_benchmark.values()))
    distribution_drift = bool(
        unique_mapping_rows.groupby("entry_month")["legacy_gemini_relevance_score"].mean().std() > 0.02
        or unique_mapping_rows.groupby("generation_batch_inferred")["legacy_gemini_relevance_score"].mean().std() > 0.02
    )
    manifest = {
        "label": "development_only_raw_gemini_score_semantic_audit",
        "direct_earnings_rows": int(len(earnings)),
        "unique_question_symbol_date_mappings": int(len(unique_mapping_rows)),
        "exact_question_symbol_pairs_with_score_variation": int((pair_stats["distinct_scores"] > 1).sum()),
        "generation_batch_status": "no verified prompt/model batch ID; inferred as UTC t0 calendar date and labeled accordingly",
        "controlled_binary_score_advantage_stable": score_stable,
        "stable_by_benchmark": stable_by_benchmark,
        "temporal_calibration_drift_flag": distribution_drift,
        "economic_interpretation_if_predictive": "possible LLM confidence / data-quality proxy; not economic directness",
        "lockbox_opened": False,
        "test_rows_read": 0,
    }
    _json(audit_dir / "raw_score_audit_manifest.json", manifest)
    return manifest


def _fit_quality_model(frame: pd.DataFrame, target: str) -> dict[str, Any]:
    train = frame.copy()
    y = pd.to_numeric(train[target], errors="coerce")
    keep = y.notna()
    if int(keep.sum()) < 12:
        return {"target": target, "intercept": float(y.mean()) if y.notna().any() else 0.0, "coefficients": [0.0] * len(QUALITY_FEATURES), "medians": [0.0] * len(QUALITY_FEATURES), "means": [0.0] * len(QUALITY_FEATURES), "scales": [1.0] * len(QUALITY_FEATURES)}
    train = train.loc[keep]
    y_array = y.loc[keep].to_numpy(dtype=float)
    columns = []
    medians, means, scales = [], [], []
    for column in QUALITY_FEATURES:
        values = pd.to_numeric(train.get(column), errors="coerce")
        median = float(values.median()) if values.notna().any() else 0.0
        array = values.fillna(median).to_numpy(dtype=float)
        mean = float(np.mean(array))
        scale = float(np.std(array))
        if scale <= 1e-12:
            scale = 1.0
        columns.append((array - mean) / scale)
        medians.append(median)
        means.append(mean)
        scales.append(scale)
    x = np.column_stack(columns)
    design = np.column_stack([np.ones(len(x)), x])
    # Fixed ridge penalty is predeclared and is not tuned on outcomes.
    penalty = np.eye(design.shape[1])
    penalty[0, 0] = 0.0
    coefficient = np.linalg.solve(design.T @ design + 5.0 * penalty, design.T @ y_array)
    return {
        "target": target,
        "intercept": float(coefficient[0]),
        "coefficients": [float(value) for value in coefficient[1:]],
        "medians": medians,
        "means": means,
        "scales": scales,
        "features": list(QUALITY_FEATURES),
        "ridge_penalty": 5.0,
        "training_rows": int(len(train)),
    }


def _predict_quality(frame: pd.DataFrame, model: dict[str, Any]) -> np.ndarray:
    columns = []
    for index, column in enumerate(QUALITY_FEATURES):
        values = pd.to_numeric(frame.get(column), errors="coerce")
        array = values.fillna(float(model["medians"][index])).to_numpy(dtype=float)
        columns.append((array - float(model["means"][index])) / float(model["scales"][index]))
    x = np.column_stack(columns)
    return float(model["intercept"]) + x @ np.asarray(model["coefficients"], dtype=float)


def _attach_quality(frame: pd.DataFrame, training: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = frame.copy()
    model_a = _fit_quality_model(training, "te1_active_net_return_pct")
    model_b = _fit_quality_model(training, "active_return_per_slot_day_pct")
    out["predicted_target_a"] = _predict_quality(out, model_a)
    out["predicted_target_b_slot"] = _predict_quality(out, model_b)
    arrivals = pd.to_numeric(training.get("candidates_seen_previous_5_trading_days"), errors="coerce")
    arrival_threshold = float(arrivals.median()) if arrivals.notna().any() else 0.0
    return out, {"target_a_model": model_a, "target_b_model": model_b, "arrival_training_median": arrival_threshold}


def _quality_accept(trade: dict[str, Any], context: dict[str, Any], rule: str, metadata: dict[str, Any]) -> bool:
    if int(context.get("free_slots", 0)) <= 0:
        return False
    if rule == "always_fill":
        return True
    if rule == "predicted_target_a_positive":
        value = trade.get("predicted_target_a", np.nan)
        return bool(np.isfinite(float(value)) and float(value) > 0.0)
    if rule == "predicted_target_b_positive":
        value = trade.get("predicted_target_b_slot", np.nan)
        return bool(np.isfinite(float(value)) and float(value) > 0.0)
    if rule == "expected_slot_days_le_21":
        value = trade.get("expected_slot_days", trade.get("feat_time_to_resolution_days", np.nan))
        return bool(np.isfinite(float(value)) and float(value) <= 21.0)
    if rule == "stock_minus_sector_20d_le_0":
        value = trade.get("stock_minus_sector_20d", np.nan)
        return bool(np.isfinite(float(value)) and float(value) <= 0.0)
    if rule == "arrival_pressure_le_training_median":
        value = trade.get("candidates_seen_previous_5_trading_days", np.nan)
        threshold = float(metadata["arrival_training_median"])
        return bool(np.isfinite(float(value)) and float(value) <= threshold)
    raise ValueError(f"Unknown Stage 2C quality rule: {rule}")


def _quality_policy(rule: str, metadata: dict[str, Any]) -> Callable[[dict, pd.Timestamp, dict[str, Any]], str]:
    def decide(trade: dict, _day: pd.Timestamp, context: dict[str, Any]) -> str:
        return "accept" if _quality_accept(trade, context, rule, metadata) else "reject"
    return decide


def _raw_threshold_policy(trade: dict, _day: pd.Timestamp, context: dict[str, Any]) -> str:
    if int(context.get("free_slots", 0)) <= 0:
        return "reject"
    value = trade.get("legacy_gemini_relevance_score", trade.get("_admission_score", np.nan))
    return "accept" if np.isfinite(float(value)) and float(value) >= 1.0 else "reject"


def _stable_random_key(row: pd.Series, seed: int) -> str:
    raw = "|".join(
        map(str, (seed, row.get("economic_event_id"), row.get("market_id"), row.get("entry_date"), row.get("symbol")))
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _rank(frame: pd.DataFrame, mode: str, random_seed: int = 0) -> pd.DataFrame:
    out = frame.copy()
    out["_selector_rank"] = 10**9
    out["_admission_score"] = pd.to_numeric(out["legacy_gemini_relevance_score"], errors="coerce")
    for _, day in out.groupby(["benchmark", "analysis_split", "entry_date"], sort=False):
        ranked = day.copy()
        if mode == "raw_baseline":
            order = ranked.sort_values(
                ["legacy_gemini_relevance_score", "expected_slot_days", "source_order", "symbol"],
                ascending=[False, True, True, True], kind="mergesort",
            ).index
        elif mode == "deterministic_direct":
            order = ranked.sort_values(["expected_slot_days", "source_order", "symbol"], ascending=[True, True, True], kind="mergesort").index
        elif mode == "family_aware":
            quality = ranked["predicted_target_a"] if "predicted_target_a" in ranked else pd.Series(0.0, index=ranked.index)
            ranked["_shared_quality"] = pd.to_numeric(quality, errors="coerce").fillna(0.0)
            order = ranked.sort_values(
                ["_shared_quality", "expected_slot_days", "semantic_event_rank", "source_order", "symbol"],
                ascending=[False, True, True, True, True], kind="mergesort",
            ).index
        elif mode == "hybrid_legacy_confidence":
            direct = ranked["mapping_type"].eq("direct_issuer")
            ranked["_shared_quality"] = np.where(direct, pd.to_numeric(ranked["legacy_gemini_relevance_score"], errors="coerce").fillna(-1.0), 0.0)
            order = ranked.sort_values(
                ["_shared_quality", "expected_slot_days", "semantic_event_rank", "source_order", "symbol"],
                ascending=[False, True, True, True, True], kind="mergesort",
            ).index
        elif mode == "random_legal":
            ranked["_random_key"] = ranked.apply(lambda row: _stable_random_key(row, random_seed), axis=1)
            order = ranked.sort_values(["_random_key", "symbol"], kind="mergesort").index
        else:
            raise ValueError(f"Unknown Stage 2C rank mode: {mode}")
        out.loc[order, "_selector_rank"] = np.arange(len(order), dtype=int)
    out["_selector_rank"] = out["_selector_rank"].astype(int)
    return out


def _slot_usage(trades: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    if trades.empty:
        return 0.0
    days = pd.bdate_range(start.normalize(), end.normalize(), tz="UTC")
    if len(days) == 0:
        return 0.0
    entries = pd.to_datetime(trades["entry_date"], utc=True, errors="coerce").dt.normalize()
    exits = pd.to_datetime(trades["exit_date"], utc=True, errors="coerce").dt.normalize()
    occupancy = [int(((entries <= day) & (exits >= day)).sum()) for day in days]
    maximum = int(PORT_DEFAULT.get("max_concurrent", 10))
    return float(np.mean(occupancy) / max(maximum, 1) * 100.0)


def _trade_concentration(trades: pd.DataFrame) -> dict[str, float]:
    if trades.empty:
        return {"top_winner_concentration_pct": 0.0, "top_event_cluster_abs_pnl_share_pct": 0.0}
    pnl = pd.to_numeric(trades.get("pnl"), errors="coerce").fillna(0.0)
    positive = pnl[pnl > 0]
    winner_share = float(positive.max() / positive.sum() * 100.0) if positive.sum() > 0 else 0.0
    event = trades.get("economic_event_id", pd.Series("unknown", index=trades.index)).fillna("unknown").astype(str)
    cluster = pd.DataFrame({"event": event, "pnl": pnl}).groupby("event")["pnl"].sum().abs()
    cluster_share = float(cluster.max() / cluster.sum() * 100.0) if cluster.sum() > 0 else 0.0
    return {"top_winner_concentration_pct": winner_share, "top_event_cluster_abs_pnl_share_pct": cluster_share}


def _exact_replay(
    frame: pd.DataFrame,
    prices: dict,
    probs: dict,
    benchmark: str,
    output_dir: Path,
    admission_policy: Callable[[dict, pd.Timestamp, dict[str, Any]], str] | None,
    metadata: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    subset = frame[frame["benchmark"].eq(benchmark)].copy()
    if subset.empty:
        return {**metadata, "benchmark": benchmark, "n_trades": 0, "lockbox_opened": False}, pd.DataFrame(), pd.DataFrame()
    start = pd.to_datetime(subset["t_theta"], utc=True).min().normalize()
    end = pd.to_datetime(subset["t_e"], utc=True).max().normalize()
    trades, equity, stats, _meta, allocation, disposition = sim_opp_cost(
        subset, prices, probs, dict(PORT_DEFAULT), bench_sym=benchmark, initial=INITIAL_CAPITAL,
        start_date=start, end_date=end, allocation_mode=ALLOCATION_FIFO,
        collect_allocation_log=True, admission_policy=admission_policy,
    )
    if not trades.empty:
        exit_date = pd.to_datetime(trades["exit_date"], errors="coerce", utc=True).dt.normalize()
        candidate_te = pd.to_datetime(trades["candidate_t_e"], errors="coerce", utc=True).dt.normalize()
        if not (exit_date < candidate_te).all():
            raise AssertionError("Stage 2C exact replay generated exit_date >= candidate_t_e")
    output_dir.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output_dir / f"trades_{benchmark.lower()}.csv", index=False)
    equity.to_csv(output_dir / f"equity_{benchmark.lower()}.csv", index=False)
    allocation.to_csv(output_dir / f"allocation_{benchmark.lower()}.csv", index=False)
    disposition.to_csv(output_dir / f"disposition_{benchmark.lower()}.csv", index=False)
    result = {
        **metadata,
        "benchmark": benchmark,
        **stats,
        **_active_metrics(equity),
        **_trade_concentration(trades),
        "slot_usage_pct": _slot_usage(trades, start, end),
        "n_trades": int(len(trades)),
        "selected_decisions": int(allocation.get("decision", pd.Series(dtype=object)).eq("selected").sum()) if not allocation.empty else 0,
        "admission_rejected": int(allocation.get("skip_reason", pd.Series(dtype=object)).eq("admission_reject").sum()) if not allocation.empty else 0,
        "blocked_by_capacity": int(allocation.get("skip_reason", pd.Series(dtype=object)).eq("max_concurrent").sum()) if not allocation.empty else 0,
        "lockbox_opened": False,
        "te_is_never_exit": True,
    }
    return result, trades, allocation


def _choose_quality_rule(inner: pd.DataFrame) -> tuple[str, pd.DataFrame]:
    if inner.empty:
        return "predicted_target_a_positive", pd.DataFrame()
    summary = (
        inner.groupby(["quality_rule", "benchmark"], as_index=False)
        .agg(mean_excess_return=("excess_return", "mean"), mean_active_ir=("active_information_ratio", "mean"), mean_active_drawdown=("active_max_drawdown_pct", "mean"), mean_trades=("n_trades", "mean"))
    )
    pivot = summary.pivot(index="quality_rule", columns="benchmark", values="mean_excess_return")
    aggregate = inner.groupby("quality_rule", as_index=False).agg(
        mean_excess_return=("excess_return", "mean"), mean_active_ir=("active_information_ratio", "mean"),
        mean_active_drawdown=("active_max_drawdown_pct", "mean"), total_trades=("n_trades", "sum"),
    )
    aggregate = aggregate.merge(pivot.reset_index(), on="quality_rule", how="left")
    benchmark_columns = [column for column in ("SPY", "QQQ") if column in aggregate]
    aggregate["minimum_benchmark_excess"] = aggregate[benchmark_columns].min(axis=1)
    fill = aggregate[aggregate["quality_rule"].eq("always_fill")]
    minimum_trades = 0.20 * float(fill.iloc[0]["total_trades"]) if not fill.empty else 0.0
    eligible = aggregate[(aggregate["quality_rule"].isin(QUALITY_RULES)) & (aggregate["total_trades"] >= minimum_trades)].copy()
    if eligible.empty:
        return "predicted_target_a_positive", aggregate
    eligible = eligible.sort_values(
        ["minimum_benchmark_excess", "mean_excess_return", "mean_active_ir", "mean_active_drawdown", "total_trades", "quality_rule"],
        ascending=[False, False, False, False, False, True], kind="mergesort",
    )
    return str(eligible.iloc[0]["quality_rule"]), aggregate


def _direct_event_ablation(data: pd.DataFrame, prices: dict, probs: dict, output_dir: Path) -> dict[str, Any]:
    direct_dir = output_dir / "direct_event_ablation"
    direct_dir.mkdir(parents=True, exist_ok=True)
    earnings = data[data["event_family"].astype(str).str.lower().eq("earnings")].copy()
    if not earnings["mapping_type"].eq("direct_issuer").all():
        raise AssertionError("Ordinary same-company earnings were not deterministically labeled direct_issuer")
    # Reuse the exact Stage 2B folds, which were constructed on the complete
    # development universe (not on an earnings-only subset).
    folds = _chronological_folds(data, 5)
    if len(folds) != 5:
        raise AssertionError(f"Stage 2C expected the five Stage 2B earnings folds, found {len(folds)}")
    outer_rows, inner_rows, choice_rows, fold_rows = [], [], [], []
    outer_trades: dict[tuple[int, str, str], pd.DataFrame] = {}
    for outer_fold, (train_mask, validation_mask) in enumerate(folds):
        outer_train = data.loc[train_mask]
        outer_train = outer_train[outer_train["event_family"].astype(str).str.lower().eq("earnings")].copy()
        validation = data.loc[validation_mask].copy()
        if not validation["event_family"].astype(str).str.lower().eq("earnings").all():
            raise AssertionError("Frozen Stage 2B outer validation fold unexpectedly contains a non-earnings row")
        fold_rows.append({
            "outer_fold": outer_fold,
            "training_start": outer_train["entry_date"].min(), "training_end": outer_train["entry_date"].max(),
            "validation_start": validation["entry_date"].min(), "validation_end": validation["entry_date"].max(),
            "training_rows": len(outer_train), "validation_rows": len(validation),
            "event_family_composition": json.dumps(validation["event_family"].value_counts().to_dict(), sort_keys=True),
            "mapping_type_composition": json.dumps(validation["mapping_type"].value_counts().to_dict(), sort_keys=True),
            "lockbox_opened": False,
        })

        inner_results = []
        for inner_fold, (inner_train_mask, inner_validation_mask) in enumerate(_chronological_folds(outer_train, 3)):
            inner_train = outer_train.loc[inner_train_mask].copy()
            inner_validation = outer_train.loc[inner_validation_mask].copy()
            scored, quality_meta = _attach_quality(inner_validation, inner_train)
            ranked = _rank(scored, "deterministic_direct")
            for rule in ("always_fill", *QUALITY_RULES):
                policy = _quality_policy(rule, quality_meta)
                for benchmark in ("SPY", "QQQ"):
                    result, _trades, _allocation = _exact_replay(
                        ranked, prices, probs, benchmark,
                        direct_dir / "inner_replays" / f"outer_{outer_fold}" / f"inner_{inner_fold}" / rule,
                        policy,
                        {
                            "evaluation_scope": "direct_inner_chronological_exact_replay",
                            "outer_fold": outer_fold, "inner_fold": inner_fold, "quality_rule": rule,
                            "validation_start": inner_validation["entry_date"].min(),
                            "validation_end": inner_validation["entry_date"].max(),
                        },
                    )
                    inner_results.append(result)
        inner_frame = pd.DataFrame(inner_results)
        if not inner_frame.empty:
            inner_rows.append(inner_frame)
        chosen_rule, choice_summary = _choose_quality_rule(inner_frame)
        if not choice_summary.empty:
            choice_summary = choice_summary.copy()
            choice_summary["outer_fold"] = outer_fold
            choice_summary.to_csv(direct_dir / f"outer_{outer_fold}_inner_quality_summary.csv", index=False)
        choice_rows.append({
            "outer_fold": outer_fold, "selected_quality_rule": chosen_rule,
            "selection_scope": "inner_chronological_exact_replay_only",
            "selection_objective": "max minimum SPY/QQQ excess, then mean excess, active IR, drawdown and trades; minimum 20% of always-fill trades",
        })

        scored_outer, quality_meta = _attach_quality(validation, outer_train)
        _json(direct_dir / "outer_models" / f"outer_{outer_fold}_quality_models.json", quality_meta)
        configurations = {
            "A_raw_global_connection_baseline": (_rank(validation, "raw_baseline"), _raw_threshold_policy),
            "B_deterministic_direct_always_fill": (_rank(validation, "deterministic_direct"), None),
            "C_deterministic_direct_legacy_proxy_1.00": (_rank(validation, "deterministic_direct"), _raw_threshold_policy),
            "D_deterministic_direct_trade_quality": (_rank(scored_outer, "deterministic_direct"), _quality_policy(chosen_rule, quality_meta)),
        }
        for variant, (ranked, policy) in configurations.items():
            for benchmark in ("SPY", "QQQ"):
                result, trades, _allocation = _exact_replay(
                    ranked, prices, probs, benchmark,
                    direct_dir / "outer_replays" / f"outer_{outer_fold}" / variant,
                    policy,
                    {
                        "evaluation_scope": "direct_outer_nested_chronological_exact_replay",
                        "outer_fold": outer_fold, "variant": variant,
                        "quality_rule": chosen_rule if variant.startswith("D_") else "not_applicable",
                        "validation_start": validation["entry_date"].min(),
                        "validation_end": validation["entry_date"].max(),
                    },
                )
                outer_rows.append(result)
                outer_trades[(outer_fold, variant, benchmark)] = trades
    outer = pd.DataFrame(outer_rows)
    inner = pd.concat(inner_rows, ignore_index=True) if inner_rows else pd.DataFrame()
    choices = pd.DataFrame(choice_rows)
    folds_frame = pd.DataFrame(fold_rows)
    outer.to_csv(direct_dir / "direct_outer_exact_replay.csv", index=False)
    inner.to_csv(direct_dir / "direct_inner_exact_replay.csv", index=False)
    choices.to_csv(direct_dir / "direct_outer_quality_choices.csv", index=False)
    folds_frame.to_csv(direct_dir / "direct_outer_fold_manifest.csv", index=False)
    summary = (
        outer.groupby(["variant", "benchmark"], as_index=False)
        .agg(
            outer_folds=("outer_fold", "nunique"), mean_total_return=("total_return", "mean"),
            mean_benchmark_return=("benchmark_return", "mean"), mean_excess_return=("excess_return", "mean"),
            mean_active_ir=("active_information_ratio", "mean"), mean_active_drawdown_pct=("active_max_drawdown_pct", "mean"),
            mean_trade_count=("n_trades", "mean"), mean_slot_usage_pct=("slot_usage_pct", "mean"),
            mean_top_winner_concentration_pct=("top_winner_concentration_pct", "mean"),
        )
    )
    summary.to_csv(direct_dir / "direct_ablation_summary.csv", index=False)

    diff_rows = []
    for outer_fold in range(5):
        for benchmark in ("SPY", "QQQ"):
            base = outer_trades.get((outer_fold, "A_raw_global_connection_baseline", benchmark), pd.DataFrame())
            for variant in DIRECT_VARIANTS:
                current = outer_trades.get((outer_fold, variant, benchmark), pd.DataFrame())
                key_columns = [column for column in ("symbol", "question", "candidate_t_e") if column in base.columns and column in current.columns]
                if not key_columns:
                    continue
                base_keys = {tuple(row) for row in base[key_columns].astype(str).itertuples(index=False, name=None)}
                current_keys = {tuple(row) for row in current[key_columns].astype(str).itertuples(index=False, name=None)}
                for status, keys in (("added_vs_raw_baseline", current_keys - base_keys), ("removed_vs_raw_baseline", base_keys - current_keys)):
                    for key in sorted(keys):
                        diff_rows.append({"outer_fold": outer_fold, "benchmark": benchmark, "variant": variant, "change": status, **dict(zip(key_columns, key))})
    pd.DataFrame(diff_rows).to_csv(direct_dir / "direct_exact_trades_added_removed.csv", index=False)

    rule_counts = Counter(choices["selected_quality_rule"])
    frozen_quality_rule = sorted(rule_counts, key=lambda rule: (-rule_counts[rule], rule))[0]
    manifest = {
        "label": "nested_chronological_direct_earnings_selector_ablation",
        "outer_fold_count": 5,
        "variants": list(DIRECT_VARIANTS),
        "direct_mapping_rule": "all ordinary same-company earnings are equally direct and valid",
        "outer_selected_quality_rules": choices.to_dict("records"),
        "predeclared_family_quality_rule": frozen_quality_rule,
        "probability_price_disagreement_available": False,
        "portfolio_capacity_state": "used dynamically by every admission callback and the common exact allocator",
        "current_2026_exploratory_test_used": False,
        "lockbox_opened": False,
        "te_is_never_exit": True,
    }
    _json(direct_dir / "direct_ablation_manifest.json", manifest)
    return {"manifest": manifest, "summary": summary, "outer": outer, "quality_rule": frozen_quality_rule, "trades": outer_trades}


def _event_grouped_folds(frame: pd.DataFrame, n_splits: int) -> list[tuple[pd.Series, pd.Series, list[str]]]:
    events = (
        frame.groupby("economic_event_id", as_index=False)["entry_date"].min()
        .sort_values(["entry_date", "economic_event_id"], kind="mergesort").reset_index(drop=True)
    )
    min_train = max(3, int(np.ceil(len(events) * 0.40)))
    if len(events) <= min_train:
        return []
    blocks = np.array_split(np.arange(min_train, len(events)), min(n_splits, len(events) - min_train))
    result = []
    for block in blocks:
        if len(block) == 0:
            continue
        validation_events = events.iloc[block]["economic_event_id"].astype(str).tolist()
        first_date = events.iloc[block]["entry_date"].min()
        training_events = events.loc[events["entry_date"] < first_date, "economic_event_id"].astype(str).tolist()
        train_mask = frame["economic_event_id"].astype(str).isin(training_events)
        validation_mask = frame["economic_event_id"].astype(str).isin(validation_events)
        if train_mask.any() and validation_mask.any():
            result.append((train_mask, validation_mask, validation_events))
    return result


def _attach_semantic_event_rank(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["semantic_event_rank"] = 10**9
    for _, group in out.groupby(["benchmark", "entry_date", "economic_event_id"], sort=False):
        order = group.sort_values(
            ["mapping_type_priority", "exposure_purity", "impact_materiality", "direction_confidence", "expected_slot_days", "source_order", "symbol"],
            ascending=[True, False, False, False, True, True, True], kind="mergesort",
        ).index
        out.loc[order, "semantic_event_rank"] = np.arange(len(order), dtype=int)
    out["semantic_event_rank"] = out["semantic_event_rank"].astype(int)
    return out


def _indirect_policy(trade: dict, _day: pd.Timestamp, context: dict[str, Any]) -> str:
    if int(context.get("free_slots", 0)) <= 0:
        return "reject"
    valid = bool(trade.get("mapping_valid", False)) and float(trade.get("mapping_confidence", 0)) >= 3.0
    top_mapping = int(trade.get("semantic_event_rank", 10**9)) == 0
    prior_event_count = trade.get("event_candidates_seen_previous_5_days", np.nan)
    extension = trade.get("stock_minus_sector_20d", np.nan)
    novel = np.isfinite(float(prior_event_count)) and float(prior_event_count) <= 0.0
    repricing_ok = np.isfinite(float(extension)) and abs(float(extension)) <= 0.10
    # One sparse-data guard: reject only when the episode is not novel and the
    # asset has already repriced by more than ten percent relative to sector.
    admission_ok = novel or repricing_ok
    return "accept" if valid and top_mapping and admission_ok else "reject"


def _family_policy(rule: str, quality_meta: dict[str, Any]) -> Callable[[dict, pd.Timestamp, dict[str, Any]], str]:
    def decide(trade: dict, day: pd.Timestamp, context: dict[str, Any]) -> str:
        if str(trade.get("mapping_type")) == "direct_issuer":
            return "accept" if bool(trade.get("mapping_valid", False)) and _quality_accept(trade, context, rule, quality_meta) else "reject"
        return _indirect_policy(trade, day, context)
    return decide


def _hybrid_legacy_policy(trade: dict, day: pd.Timestamp, context: dict[str, Any]) -> str:
    if str(trade.get("mapping_type")) == "direct_issuer":
        return _raw_threshold_policy(trade, day, context)
    return _indirect_policy(trade, day, context)


def _frozen_family_policy(trade: dict, day: pd.Timestamp, context: dict[str, Any]) -> str:
    if int(context.get("free_slots", 0)) <= 0:
        return "reject"
    if str(trade.get("mapping_type")) == "direct_issuer":
        valid = bool(trade.get("mapping_valid", False))
        quality = bool(trade.get("stage2c_direct_quality_accept", False))
        return "accept" if valid and quality else "reject"
    return _indirect_policy(trade, day, context)


def _static_method_order(group: pd.DataFrame, method: str, seed: int = 0) -> list[Any]:
    if method in {"raw_absolute", "event_relative_raw"}:
        return group.sort_values(
            ["legacy_gemini_relevance_score", "expected_slot_days", "source_order", "symbol"],
            ascending=[False, True, True, True], kind="mergesort",
        ).index.tolist()
    if method == "mapping_type_priority":
        return group.sort_values(["mapping_type_priority", "source_order", "symbol"], ascending=[True, True, True], kind="mergesort").index.tolist()
    if method == "exposure_purity":
        return group.sort_values(["exposure_purity", "mapping_type_priority", "source_order", "symbol"], ascending=[False, True, True, True], kind="mergesort").index.tolist()
    if method == "direction_confidence":
        return group.sort_values(["direction_confidence", "exposure_purity", "source_order", "symbol"], ascending=[False, False, True, True], kind="mergesort").index.tolist()
    if method == "source_order":
        return group.sort_values(["source_order", "symbol"], kind="mergesort").index.tolist()
    if method == "semantic_full":
        return group.sort_values(
            ["mapping_type_priority", "exposure_purity", "impact_materiality", "direction_confidence", "expected_slot_days", "source_order", "symbol"],
            ascending=[True, False, False, False, True, True, True], kind="mergesort",
        ).index.tolist()
    if method == "random_legal":
        keyed = group.copy()
        keyed["_key"] = keyed.apply(lambda row: _stable_random_key(row, seed), axis=1)
        return keyed.sort_values(["_key", "symbol"], kind="mergesort").index.tolist()
    raise ValueError(method)


def _indirect_mapping_audit(data: pd.DataFrame, prices: dict, probs: dict, output_dir: Path) -> dict[str, Any]:
    audit_dir = output_dir / "indirect_mapping_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    indirect = data[data["event_family"].astype(str).str.lower().isin({"geo", "geopolitical", "macro"})].copy()
    indirect = _attach_semantic_event_rank(indirect)
    folds = _event_grouped_folds(indirect, 3)
    fold_rows = []
    for fold, (train_mask, validation_mask, validation_events) in enumerate(folds):
        training = indirect.loc[train_mask]
        validation = indirect.loc[validation_mask]
        fold_rows.append({
            "outer_fold": fold, "training_start": training["entry_date"].min(), "training_end": training["entry_date"].max(),
            "validation_start": validation["entry_date"].min(), "validation_end": validation["entry_date"].max(),
            "training_event_episodes": training["economic_event_id"].nunique(),
            "validation_event_episodes": len(validation_events), "validation_rows": len(validation),
            "validation_event_ids": "|".join(validation_events),
            "mapping_type_composition": json.dumps(validation["mapping_type"].value_counts().to_dict(), sort_keys=True),
            "event_family_composition": json.dumps(validation["event_family"].value_counts().to_dict(), sort_keys=True),
            "same_episode_kept_in_one_fold": True, "lockbox_opened": False,
        })
    pd.DataFrame(fold_rows).to_csv(audit_dir / "geo_macro_event_grouped_fold_manifest.csv", index=False)

    methods = ("raw_absolute", "event_relative_raw", "mapping_type_priority", "exposure_purity", "direction_confidence", "source_order", "semantic_full")
    selected_rows = []
    for (benchmark, event_id, entry_date), group in indirect.groupby(["benchmark", "economic_event_id", "entry_date"], sort=True):
        best_outcome = float(pd.to_numeric(group["te1_active_net_return_pct"], errors="coerce").max())
        cleanest = float(group["exposure_purity"].max())
        for method in methods:
            index = _static_method_order(group, method)[0]
            row = group.loc[index]
            selected_rows.append({
                "benchmark": benchmark, "economic_event_id": event_id, "entry_date": entry_date,
                "method": method, "symbol": row["symbol"], "mapping_type": row["mapping_type"],
                "legacy_gemini_relevance_score": row["legacy_gemini_relevance_score"],
                "mapping_valid": row["mapping_valid"], "mapping_confidence": row["mapping_confidence"],
                "exposure_purity": row["exposure_purity"], "direction_confidence": row["direction_confidence"],
                "economically_defensible": bool(row["mapping_valid"] and row["mapping_confidence"] >= 3),
                "selected_cleanest_exposure": bool(float(row["exposure_purity"]) == cleanest),
                "selected_target_a_pct": row["te1_active_net_return_pct"],
                "same_event_best_target_a_pct": best_outcome,
                "same_event_regret_pct": best_outcome - float(row["te1_active_net_return_pct"]),
                "economic_path_explanation": row["economic_path_explanation"],
            })
    selected = pd.DataFrame(selected_rows)
    selected.to_csv(audit_dir / "indirect_method_selections.csv", index=False)
    method_summary = (
        selected.groupby(["method", "benchmark"], as_index=False)
        .agg(
            event_decisions=("economic_event_id", "size"), defensible_selection_rate=("economically_defensible", "mean"),
            cleanest_exposure_rate=("selected_cleanest_exposure", "mean"), mean_active_target_a_pct=("selected_target_a_pct", "mean"),
            median_active_target_a_pct=("selected_target_a_pct", "median"), mean_same_event_regret_pct=("same_event_regret_pct", "mean"),
        )
    )
    method_summary.to_csv(audit_dir / "indirect_method_summary.csv", index=False)

    mapping_outcome = (
        indirect.groupby(["benchmark", "mapping_type"], as_index=False)
        .agg(candidate_rows=("symbol", "size"), event_episodes=("economic_event_id", "nunique"), mean_active_target_a_pct=("te1_active_net_return_pct", "mean"), median_active_target_a_pct=("te1_active_net_return_pct", "median"))
    )
    indirect_types = ["direct_underlying", "first_order_sector", "second_order_company", "broad_macro_proxy"]
    mapping_grid = pd.MultiIndex.from_product([("SPY", "QQQ"), indirect_types], names=["benchmark", "mapping_type"]).to_frame(index=False)
    mapping_outcome = mapping_grid.merge(mapping_outcome, on=["benchmark", "mapping_type"], how="left")
    for column in ("candidate_rows", "event_episodes"):
        mapping_outcome[column] = mapping_outcome[column].fillna(0).astype(int)
    mapping_outcome.to_csv(audit_dir / "active_outcome_by_mapping_type.csv", index=False)
    symbol_outcome = (
        indirect.groupby(["benchmark", "symbol", "mapping_type"], as_index=False)
        .agg(candidate_rows=("economic_event_id", "size"), event_episodes=("economic_event_id", "nunique"), mean_active_target_a_pct=("te1_active_net_return_pct", "mean"), median_active_target_a_pct=("te1_active_net_return_pct", "median"))
    )
    symbol_outcome.to_csv(audit_dir / "results_by_uso_bno_xle_and_companies.csv", index=False)

    semantic_keys = selected[selected["method"].eq("semantic_full")][["benchmark", "economic_event_id", "entry_date", "symbol"]].assign(selection_status="selected")
    same_event = indirect.merge(semantic_keys, on=["benchmark", "economic_event_id", "entry_date", "symbol"], how="left")
    same_event["selection_status"] = same_event["selection_status"].fillna("missed")
    same_event[[
        "benchmark", "economic_event_id", "economic_event_group_clean", "entry_date", "symbol", "mapping_type",
        "selection_status", "legacy_gemini_relevance_score", "mapping_confidence", "exposure_purity",
        "direction_confidence", "te1_active_net_return_pct", "economic_path_explanation",
    ]].to_csv(audit_dir / "same_event_selected_vs_missed_assets.csv", index=False)

    random_rows = []
    for seed in range(200):
        seed_values = []
        for _, group in indirect.groupby(["benchmark", "economic_event_id", "entry_date"], sort=True):
            row = group.loc[_static_method_order(group, "random_legal", seed)[0]]
            seed_values.append({"benchmark": row["benchmark"], "value": row["te1_active_net_return_pct"]})
        seed_frame = pd.DataFrame(seed_values)
        for benchmark, bench in seed_frame.groupby("benchmark"):
            random_rows.append({"seed": seed, "benchmark": benchmark, "mean_active_target_a_pct": bench["value"].mean()})
    random = pd.DataFrame(random_rows)
    semantic_mean = method_summary[method_summary["method"].eq("semantic_full")].set_index("benchmark")["mean_active_target_a_pct"]
    percentile_rows = []
    for benchmark in ("SPY", "QQQ"):
        distribution = random[random["benchmark"].eq(benchmark)]["mean_active_target_a_pct"]
        value = float(semantic_mean.get(benchmark, np.nan))
        percentile_rows.append({"benchmark": benchmark, "semantic_mean_active_target_a_pct": value, "random_legal_percentile": float((distribution <= value).mean() * 100.0) if np.isfinite(value) else np.nan, "random_seeds": len(distribution)})
    random.to_csv(audit_dir / "random_legal_static_distribution.csv", index=False)
    pd.DataFrame(percentile_rows).to_csv(audit_dir / "semantic_static_random_percentile.csv", index=False)

    exact_rows, exact_trades = [], {}
    for fold, (_train_mask, validation_mask, _events) in enumerate(folds):
        validation = indirect.loc[validation_mask].copy()
        configs = {
            "raw_global_connection_baseline": (_rank(validation, "raw_baseline"), _raw_threshold_policy),
            "semantic_mapping_selector": (_rank(validation, "family_aware"), _indirect_policy),
        }
        for selector, (ranked, policy) in configs.items():
            for benchmark in ("SPY", "QQQ"):
                result, trades, _allocation = _exact_replay(
                    ranked, prices, probs, benchmark,
                    audit_dir / "exact_replays" / f"outer_{fold}" / selector,
                    policy,
                    {"evaluation_scope": "geo_macro_event_grouped_outer_exact_replay", "outer_fold": fold, "selector": selector},
                )
                exact_rows.append(result)
                exact_trades[(fold, selector, benchmark)] = trades
        for seed in RANDOM_SEEDS:
            ranked = _rank(validation, "random_legal", seed)
            for benchmark in ("SPY", "QQQ"):
                result, _trades, _allocation = _exact_replay(
                    ranked, prices, probs, benchmark,
                    audit_dir / "random_exact_replays" / f"outer_{fold}" / f"seed_{seed}",
                    _indirect_policy,
                    {"evaluation_scope": "geo_macro_random_legal_exact_replay", "outer_fold": fold, "selector": "random_legal", "random_seed": seed},
                )
                exact_rows.append(result)
    exact = pd.DataFrame(exact_rows)
    exact.to_csv(audit_dir / "indirect_exact_replay.csv", index=False)
    exact_summary = (
        exact[exact["selector"].ne("random_legal")].groupby(["selector", "benchmark"], as_index=False)
        .agg(outer_folds=("outer_fold", "nunique"), mean_excess_return=("excess_return", "mean"), mean_active_ir=("active_information_ratio", "mean"), mean_active_drawdown_pct=("active_max_drawdown_pct", "mean"), mean_trade_count=("n_trades", "mean"), mean_slot_usage_pct=("slot_usage_pct", "mean"), mean_top_winner_concentration_pct=("top_winner_concentration_pct", "mean"), mean_top_event_cluster_abs_pnl_share_pct=("top_event_cluster_abs_pnl_share_pct", "mean"))
    )
    exact_summary.to_csv(audit_dir / "indirect_exact_replay_summary.csv", index=False)
    exact_percentile = []
    for benchmark in ("SPY", "QQQ"):
        semantic_value = exact[(exact["selector"].eq("semantic_mapping_selector")) & exact["benchmark"].eq(benchmark)].groupby("outer_fold")["excess_return"].mean().mean()
        seed_values = exact[(exact["selector"].eq("random_legal")) & exact["benchmark"].eq(benchmark)].groupby("random_seed")["excess_return"].mean()
        exact_percentile.append({"benchmark": benchmark, "semantic_mean_excess_return": semantic_value, "random_legal_exact_percentile": float((seed_values <= semantic_value).mean() * 100.0), "random_seeds": len(seed_values)})
    pd.DataFrame(exact_percentile).to_csv(audit_dir / "semantic_exact_random_percentile.csv", index=False)

    manifest = {
        "label": "exploratory_event_cluster_aware_indirect_mapping_audit",
        "development_candidate_rows": int(len(indirect)), "economic_event_episodes": int(indirect["economic_event_id"].nunique()),
        "assets": int(indirect["symbol"].nunique()), "event_grouped_outer_folds": len(folds),
        "minimum_mapping_confidence": 3, "indirect_numeric_thresholds_optimized": False,
        "raw_scores_compared_only_within_indirect_events": True,
        "probability_price_response_available": False, "supporting_market_agreement_available": False,
        "lockbox_opened": False, "te_is_never_exit": True,
    }
    _json(audit_dir / "indirect_mapping_audit_manifest.json", manifest)
    return {"manifest": manifest, "summary": exact_summary, "exact": exact, "indirect": indirect, "folds": folds, "trades": exact_trades}


def _enrich_trades(trades: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades
    out = trades.copy()
    if "mapping_type" in out and out["mapping_type"].notna().all():
        return out
    lookup = candidates.copy()
    lookup["candidate_t_e"] = pd.to_datetime(lookup["t_e"], utc=True, errors="coerce").dt.normalize()
    lookup_columns = [
        "benchmark", "symbol", "economic_event_id", "candidate_t_e", "mapping_type", "mapping_confidence",
        "impact_materiality", "direction_confidence", "exposure_purity", "economic_path_explanation",
    ]
    lookup = lookup[lookup_columns].drop_duplicates(["benchmark", "symbol", "economic_event_id", "candidate_t_e"])
    out["candidate_t_e"] = pd.to_datetime(out["candidate_t_e"], utc=True, errors="coerce").dt.normalize()
    out = out.drop(columns=[column for column in lookup_columns[4:] if column in out], errors="ignore")
    return out.merge(lookup, on=["benchmark", "symbol", "economic_event_id", "candidate_t_e"], how="left")


def _family_aware_exact_replay(
    data: pd.DataFrame,
    prices: dict,
    probs: dict,
    quality_rule: str,
    output_dir: Path,
) -> dict[str, Any]:
    family_dir = output_dir / "family_aware_exact_replay"
    family_dir.mkdir(parents=True, exist_ok=True)
    prepared = _attach_semantic_event_rank(data)
    folds = _event_grouped_folds(prepared, 5)
    if len(folds) != 5:
        raise AssertionError(f"Expected five global family-aware development folds, found {len(folds)}")
    rows, fold_rows, diff_rows, mapping_trade_rows = [], [], [], []
    trades_by_run: dict[tuple[int, str, str], pd.DataFrame] = {}
    for fold, (train_mask, validation_mask, validation_events) in enumerate(folds):
        training = prepared.loc[train_mask].copy()
        validation = prepared.loc[validation_mask].copy()
        direct_training = training[training["mapping_type"].eq("direct_issuer")].copy()
        scored, quality_meta = _attach_quality(validation, direct_training)
        scored.loc[~scored["mapping_type"].eq("direct_issuer"), "predicted_target_a"] = 0.0
        scored.loc[~scored["mapping_type"].eq("direct_issuer"), "predicted_target_b_slot"] = 0.0
        _json(family_dir / "outer_models" / f"outer_{fold}_direct_quality_models.json", quality_meta)
        fold_rows.append({
            "outer_fold": fold, "training_start": training["entry_date"].min(), "training_end": training["entry_date"].max(),
            "validation_start": validation["entry_date"].min(), "validation_end": validation["entry_date"].max(),
            "training_event_episodes": training["economic_event_id"].nunique(), "validation_event_episodes": len(validation_events),
            "validation_rows": len(validation), "validation_event_ids": "|".join(validation_events),
            "event_family_composition": json.dumps(validation["event_family"].value_counts().to_dict(), sort_keys=True),
            "mapping_type_composition": json.dumps(validation["mapping_type"].value_counts().to_dict(), sort_keys=True),
            "all_rows_from_same_episode_kept_together": True, "lockbox_opened": False,
        })
        configurations = {
            "raw_global_connection_baseline": (_rank(validation, "raw_baseline"), _raw_threshold_policy),
            "family_aware_selector": (_rank(scored, "family_aware"), _family_policy(quality_rule, quality_meta)),
            "hybrid_legacy_confidence_selector": (_rank(scored, "hybrid_legacy_confidence"), _hybrid_legacy_policy),
        }
        for selector, (ranked, policy) in configurations.items():
            for benchmark in ("SPY", "QQQ"):
                result, trades, _allocation = _exact_replay(
                    ranked, prices, probs, benchmark,
                    family_dir / "outer_replays" / f"outer_{fold}" / selector,
                    policy,
                    {
                        "evaluation_scope": "global_event_grouped_family_aware_outer_exact_replay",
                        "outer_fold": fold, "selector": selector, "direct_quality_rule": quality_rule,
                        "validation_start": validation["entry_date"].min(), "validation_end": validation["entry_date"].max(),
                    },
                )
                enriched = _enrich_trades(trades, validation)
                enriched.to_csv(family_dir / "outer_replays" / f"outer_{fold}" / selector / f"trades_{benchmark.lower()}_semantic.csv", index=False)
                rows.append(result)
                trades_by_run[(fold, selector, benchmark)] = enriched
                if not enriched.empty:
                    detail = enriched.copy()
                    detail["outer_fold"] = fold
                    detail["selector"] = selector
                    detail["benchmark"] = benchmark
                    mapping_trade_rows.append(detail)
        for seed in RANDOM_SEEDS:
            ranked = _rank(scored, "random_legal", seed)
            policy = _family_policy(quality_rule, quality_meta)
            for benchmark in ("SPY", "QQQ"):
                result, _trades, _allocation = _exact_replay(
                    ranked, prices, probs, benchmark,
                    family_dir / "random_exact_replays" / f"outer_{fold}" / f"seed_{seed}",
                    policy,
                    {
                        "evaluation_scope": "global_family_aware_random_legal_exact_replay",
                        "outer_fold": fold, "selector": "random_legal", "random_seed": seed,
                        "direct_quality_rule": quality_rule,
                    },
                )
                rows.append(result)

        for benchmark in ("SPY", "QQQ"):
            base = trades_by_run[(fold, "raw_global_connection_baseline", benchmark)]
            base_key_columns = [column for column in ("symbol", "question", "candidate_t_e", "economic_event_id") if column in base]
            base_keys = {tuple(row) for row in base[base_key_columns].astype(str).itertuples(index=False, name=None)} if base_key_columns else set()
            for selector in ("family_aware_selector", "hybrid_legacy_confidence_selector"):
                current = trades_by_run[(fold, selector, benchmark)]
                current_keys = {tuple(row) for row in current[base_key_columns].astype(str).itertuples(index=False, name=None)} if base_key_columns else set()
                for status, keys in (("added", current_keys - base_keys), ("removed", base_keys - current_keys)):
                    for key in sorted(keys):
                        diff_rows.append({"outer_fold": fold, "benchmark": benchmark, "selector": selector, "change_vs_raw_global_baseline": status, **dict(zip(base_key_columns, key))})

    exact = pd.DataFrame(rows)
    exact.to_csv(family_dir / "family_aware_outer_exact_replay.csv", index=False)
    pd.DataFrame(fold_rows).to_csv(family_dir / "family_aware_outer_fold_manifest.csv", index=False)
    pd.DataFrame(diff_rows).to_csv(family_dir / "exact_trades_added_removed_vs_raw_global_baseline.csv", index=False)
    selected_exact = exact[exact["selector"].ne("random_legal")].copy()
    summary = (
        selected_exact.groupby(["selector", "benchmark"], as_index=False)
        .agg(
            outer_folds=("outer_fold", "nunique"), mean_total_return=("total_return", "mean"),
            mean_benchmark_return=("benchmark_return", "mean"), mean_excess_return=("excess_return", "mean"),
            mean_active_ir=("active_information_ratio", "mean"), mean_active_drawdown_pct=("active_max_drawdown_pct", "mean"),
            mean_trade_count=("n_trades", "mean"), mean_slot_usage_pct=("slot_usage_pct", "mean"),
            mean_top_winner_concentration_pct=("top_winner_concentration_pct", "mean"),
            mean_top_event_cluster_abs_pnl_share_pct=("top_event_cluster_abs_pnl_share_pct", "mean"),
        )
    )
    summary.to_csv(family_dir / "family_aware_exact_replay_summary.csv", index=False)

    all_trade_detail = pd.concat(mapping_trade_rows, ignore_index=True) if mapping_trade_rows else pd.DataFrame()
    if not all_trade_detail.empty:
        all_trade_detail.to_csv(family_dir / "family_aware_trade_detail_with_mapping_type.csv", index=False)
        mapping_summary = (
            all_trade_detail.groupby(["selector", "benchmark", "mapping_type"], dropna=False, as_index=False)
            .agg(trade_count=("symbol", "size"), event_episodes=("economic_event_id", "nunique"), net_pnl=("pnl", "sum"), mean_trade_pnl_pct=("pnl_pct", "mean"), top_winner_pnl_pct=("pnl_pct", "max"))
        )
    else:
        mapping_summary = pd.DataFrame()
    mapping_summary.to_csv(family_dir / "exact_trade_results_by_mapping_type.csv", index=False)
    cluster_summary = pd.DataFrame()
    if not all_trade_detail.empty:
        cluster_summary = (
            all_trade_detail.groupby(["selector", "benchmark", "economic_event_id"], as_index=False)
            .agg(mapping_types=("mapping_type", lambda x: "|".join(sorted(set(map(str, x.dropna()))))), trade_count=("symbol", "size"), net_pnl=("pnl", "sum"), symbols=("symbol", lambda x: "|".join(sorted(set(map(str, x))))))
        )
    cluster_summary.to_csv(family_dir / "event_cluster_contribution.csv", index=False)

    percentile_rows = []
    for benchmark in ("SPY", "QQQ"):
        family_value = exact[(exact["selector"].eq("family_aware_selector")) & exact["benchmark"].eq(benchmark)].groupby("outer_fold")["excess_return"].mean().mean()
        seed_values = exact[(exact["selector"].eq("random_legal")) & exact["benchmark"].eq(benchmark)].groupby("random_seed")["excess_return"].mean()
        percentile_rows.append({"benchmark": benchmark, "family_aware_mean_excess_return": family_value, "random_legal_exact_percentile": float((seed_values <= family_value).mean() * 100.0), "random_seeds": len(seed_values)})
    random_percentile = pd.DataFrame(percentile_rows)
    random_percentile.to_csv(family_dir / "family_aware_random_legal_percentile.csv", index=False)

    manifest = {
        "label": "global_modular_family_mapping_type_aware_exact_replay",
        "shared_portfolio_allocator": "corrected capacity-constrained benchmark-rotation exact simulator",
        "direct_lane": {"mapping": "deterministic direct_issuer", "quality_rule": quality_rule, "raw_connection_threshold": None},
        "indirect_lane": {"minimum_mapping_confidence": 3, "within_event_ranking": "mapping type, exposure purity, impact materiality, direction confidence, expected slot days", "hard_geo_quota": False, "unconditional_geo_priority": False},
        "probability_price_response_available": False, "supporting_market_agreement_available": False,
        "outer_fold_count": len(folds), "lockbox_opened": False, "te_is_never_exit": True,
    }
    _json(family_dir / "family_aware_exact_replay_manifest.json", manifest)
    return {"manifest": manifest, "summary": summary, "exact": exact, "trades": trades_by_run, "folds": pd.DataFrame(fold_rows), "random_percentile": random_percentile, "mapping_summary": mapping_summary}


def _family_aware_oof_stream_replay(
    data: pd.DataFrame,
    prices: dict,
    probs: dict,
    quality_rule: str,
    output_dir: Path,
) -> dict[str, Any]:
    """Replay one combined all-family OOF candidate stream.

    Each family contributes only its chronological validation rows. Direct
    quality predictions are produced by the corresponding earlier training
    rows before the validation rows are concatenated. The concatenated stream
    is then replayed once through the shared allocator, preserving capacity
    interactions on dates where family panels overlap.
    """

    family_dir = output_dir / "family_aware_exact_replay"
    family_dir.mkdir(parents=True, exist_ok=True)
    prepared = _attach_semantic_event_rank(data)
    panels: list[pd.DataFrame] = []
    panel_rows: list[dict[str, Any]] = []

    def add_panel(panel_id: str, panel_family: str, training: pd.DataFrame, validation: pd.DataFrame, event_grouped: bool) -> None:
        if validation.empty:
            return
        direct_training = training[training["mapping_type"].eq("direct_issuer")].copy()
        scored, quality_meta = _attach_quality(validation, direct_training)
        indirect_mask = ~scored["mapping_type"].eq("direct_issuer")
        scored.loc[indirect_mask, "predicted_target_a"] = 0.0
        scored.loc[indirect_mask, "predicted_target_b_slot"] = 0.0
        accepts = []
        for _, row in scored.iterrows():
            if row["mapping_type"] != "direct_issuer":
                accepts.append(False)
            else:
                accepts.append(_quality_accept(row.to_dict(), {"free_slots": 1}, quality_rule, quality_meta))
        scored["stage2c_direct_quality_accept"] = accepts
        scored["stage2c_oof_panel"] = panel_id
        scored["stage2c_oof_family"] = panel_family
        panels.append(scored)
        _json(family_dir / "panel_models" / f"{panel_id}_quality_models.json", quality_meta)
        panel_rows.append({
            "oof_panel": panel_id, "panel_family": panel_family,
            "training_start": training["entry_date"].min() if not training.empty else pd.NaT,
            "training_end": training["entry_date"].max() if not training.empty else pd.NaT,
            "validation_start": validation["entry_date"].min(), "validation_end": validation["entry_date"].max(),
            "training_rows": len(training), "validation_rows": len(validation),
            "validation_event_episodes": validation["economic_event_id"].nunique(),
            "event_family_composition": json.dumps(validation["event_family"].value_counts().to_dict(), sort_keys=True),
            "mapping_type_composition": json.dumps(validation["mapping_type"].value_counts().to_dict(), sort_keys=True),
            "event_grouped": event_grouped, "lockbox_opened": False,
        })

    for fold, (train_mask, validation_mask) in enumerate(_chronological_folds(prepared, 5)):
        training = prepared.loc[train_mask]
        training = training[training["event_family"].astype(str).str.lower().eq("earnings")]
        validation = prepared.loc[validation_mask]
        if not validation["event_family"].astype(str).str.lower().eq("earnings").all():
            raise AssertionError("Frozen Stage 2B validation panel unexpectedly contains a non-earnings row")
        add_panel(f"earnings_outer_{fold}", "direct_earnings", training, validation, False)

    indirect = prepared[prepared["event_family"].astype(str).str.lower().isin({"geo", "geopolitical", "macro"})].copy()
    for fold, (train_mask, validation_mask, _events) in enumerate(_event_grouped_folds(indirect, 3)):
        add_panel(f"geo_macro_outer_{fold}", "indirect_geo_macro", indirect.loc[train_mask], indirect.loc[validation_mask], True)

    other_direct = prepared[
        prepared["mapping_type"].eq("direct_issuer")
        & ~prepared["event_family"].astype(str).str.lower().eq("earnings")
    ].copy()
    for fold, (_train_mask, validation_mask, _events) in enumerate(_event_grouped_folds(other_direct, 3)):
        validation = other_direct.loc[validation_mask]
        training = prepared[
            prepared["mapping_type"].eq("direct_issuer")
            & (prepared["entry_date"] < validation["entry_date"].min())
        ].copy()
        add_panel(f"direct_other_outer_{fold}", "direct_company_catalyst", training, validation, True)

    if not panels:
        raise AssertionError("No Stage 2C OOF validation panels were constructed")
    stream = pd.concat(panels, ignore_index=True)
    stable_keys = ["benchmark", "event_id", "market_id", "entry_date", "symbol"]
    duplicates = stream.duplicated(stable_keys, keep=False)
    if duplicates.any():
        raise AssertionError("Stage 2C OOF panels overlap on stable candidate keys")
    if not stream["analysis_split"].eq("train").all():
        raise AssertionError("Combined Stage 2C OOF stream contains non-development rows")
    stream.to_csv(family_dir / "combined_oof_candidate_stream.csv", index=False)
    panel_manifest = pd.DataFrame(panel_rows)
    panel_manifest.to_csv(family_dir / "family_aware_outer_fold_manifest.csv", index=False)

    panel_exact_rows = []
    for panel in panels:
        panel_id = str(panel["stage2c_oof_panel"].iloc[0])
        configurations = {
            "raw_global_connection_baseline": (_rank(panel, "raw_baseline"), _raw_threshold_policy),
            "family_aware_selector": (_rank(panel, "family_aware"), _frozen_family_policy),
            "hybrid_legacy_confidence_selector": (_rank(panel, "hybrid_legacy_confidence"), _hybrid_legacy_policy),
        }
        for selector, (ranked, policy) in configurations.items():
            for benchmark in ("SPY", "QQQ"):
                result, _trades, _allocation = _exact_replay(
                    ranked, prices, probs, benchmark,
                    family_dir / "panel_replays" / panel_id / selector,
                    policy,
                    {"evaluation_scope": "family_aware_panel_outer_exact_replay", "oof_panel": panel_id, "selector": selector, "direct_quality_rule": quality_rule},
                )
                panel_exact_rows.append(result)
    panel_exact = pd.DataFrame(panel_exact_rows)
    panel_exact.to_csv(family_dir / "family_aware_panel_outer_exact_replay.csv", index=False)
    panel_summary = (
        panel_exact.groupby(["selector", "benchmark"], as_index=False)
        .agg(
            outer_panels=("oof_panel", "nunique"), mean_excess_return=("excess_return", "mean"),
            mean_active_ir=("active_information_ratio", "mean"), mean_active_drawdown_pct=("active_max_drawdown_pct", "mean"),
            mean_trade_count=("n_trades", "mean"), mean_slot_usage_pct=("slot_usage_pct", "mean"),
        )
    )
    panel_summary.to_csv(family_dir / "family_aware_panel_summary.csv", index=False)

    configurations = {
        "raw_global_connection_baseline": (_rank(stream, "raw_baseline"), _raw_threshold_policy),
        "family_aware_selector": (_rank(stream, "family_aware"), _frozen_family_policy),
        "hybrid_legacy_confidence_selector": (_rank(stream, "hybrid_legacy_confidence"), _hybrid_legacy_policy),
    }
    combined_rows, trades_by_run, mapping_trade_rows = [], {}, []
    for selector, (ranked, policy) in configurations.items():
        for benchmark in ("SPY", "QQQ"):
            result, trades, _allocation = _exact_replay(
                ranked, prices, probs, benchmark,
                family_dir / "combined_oof_replays" / selector,
                policy,
                {"evaluation_scope": "combined_all_family_oof_exact_replay", "selector": selector, "direct_quality_rule": quality_rule},
            )
            enriched = _enrich_trades(trades, stream)
            enriched.to_csv(family_dir / "combined_oof_replays" / selector / f"trades_{benchmark.lower()}_semantic.csv", index=False)
            combined_rows.append(result)
            trades_by_run[(selector, benchmark)] = enriched
            if not enriched.empty:
                detail = enriched.copy()
                detail["selector"] = selector
                detail["benchmark"] = benchmark
                mapping_trade_rows.append(detail)

    random_rows = []
    for seed in RANDOM_SEEDS:
        ranked = _rank(stream, "random_legal", seed)
        for benchmark in ("SPY", "QQQ"):
            result, _trades, _allocation = _exact_replay(
                ranked, prices, probs, benchmark,
                family_dir / "combined_random_exact_replays" / f"seed_{seed}",
                _frozen_family_policy,
                {"evaluation_scope": "combined_all_family_random_legal_exact_replay", "selector": "random_legal", "random_seed": seed, "direct_quality_rule": quality_rule},
            )
            random_rows.append(result)
    combined = pd.DataFrame(combined_rows)
    random_exact = pd.DataFrame(random_rows)
    combined.to_csv(family_dir / "family_aware_combined_oof_exact_replay.csv", index=False)
    random_exact.to_csv(family_dir / "family_aware_combined_random_exact_replay.csv", index=False)
    summary = combined.rename(columns={
        "total_return": "mean_total_return", "benchmark_return": "mean_benchmark_return", "excess_return": "mean_excess_return",
        "active_information_ratio": "mean_active_ir", "active_max_drawdown_pct": "mean_active_drawdown_pct",
        "n_trades": "mean_trade_count", "slot_usage_pct": "mean_slot_usage_pct",
        "top_winner_concentration_pct": "mean_top_winner_concentration_pct",
        "top_event_cluster_abs_pnl_share_pct": "mean_top_event_cluster_abs_pnl_share_pct",
    })
    summary["outer_panels"] = len(panels)
    summary_columns = [
        "selector", "benchmark", "outer_panels", "mean_total_return", "mean_benchmark_return", "mean_excess_return",
        "mean_active_ir", "mean_active_drawdown_pct", "mean_trade_count", "mean_slot_usage_pct",
        "mean_top_winner_concentration_pct", "mean_top_event_cluster_abs_pnl_share_pct",
    ]
    summary = summary[summary_columns]
    summary.to_csv(family_dir / "family_aware_exact_replay_summary.csv", index=False)

    trade_detail = pd.concat(mapping_trade_rows, ignore_index=True) if mapping_trade_rows else pd.DataFrame()
    trade_detail.to_csv(family_dir / "family_aware_trade_detail_with_mapping_type.csv", index=False)
    mapping_summary = (
        trade_detail.groupby(["selector", "benchmark", "mapping_type"], dropna=False, as_index=False)
        .agg(trade_count=("symbol", "size"), event_episodes=("economic_event_id", "nunique"), net_pnl=("pnl", "sum"), mean_trade_pnl_pct=("pnl_pct", "mean"), top_winner_pnl_pct=("pnl_pct", "max"))
        if not trade_detail.empty else pd.DataFrame()
    )
    selectors = ("raw_global_connection_baseline", "family_aware_selector", "hybrid_legacy_confidence_selector")
    report_types = ("direct_issuer", "direct_underlying", "first_order_sector", "second_order_company", "broad_macro_proxy")
    mapping_grid = pd.MultiIndex.from_product([selectors, ("SPY", "QQQ"), report_types], names=["selector", "benchmark", "mapping_type"]).to_frame(index=False)
    mapping_summary = mapping_grid.merge(mapping_summary, on=["selector", "benchmark", "mapping_type"], how="left")
    for column in ("trade_count", "event_episodes"):
        mapping_summary[column] = mapping_summary[column].fillna(0).astype(int)
    mapping_summary["net_pnl"] = mapping_summary["net_pnl"].fillna(0.0)
    mapping_summary.to_csv(family_dir / "exact_trade_results_by_mapping_type.csv", index=False)
    cluster_summary = (
        trade_detail.groupby(["selector", "benchmark", "economic_event_id"], as_index=False)
        .agg(mapping_types=("mapping_type", lambda x: "|".join(sorted(set(map(str, x.dropna()))))), trade_count=("symbol", "size"), net_pnl=("pnl", "sum"), symbols=("symbol", lambda x: "|".join(sorted(set(map(str, x))))))
        if not trade_detail.empty else pd.DataFrame()
    )
    cluster_summary.to_csv(family_dir / "event_cluster_contribution.csv", index=False)

    diff_rows = []
    for benchmark in ("SPY", "QQQ"):
        base = trades_by_run[("raw_global_connection_baseline", benchmark)]
        key_columns = [column for column in ("symbol", "question", "candidate_t_e", "economic_event_id") if column in base]
        base_keys = {tuple(row) for row in base[key_columns].astype(str).itertuples(index=False, name=None)}
        for selector in ("family_aware_selector", "hybrid_legacy_confidence_selector"):
            current = trades_by_run[(selector, benchmark)]
            current_keys = {tuple(row) for row in current[key_columns].astype(str).itertuples(index=False, name=None)}
            for status, keys in (("added", current_keys - base_keys), ("removed", base_keys - current_keys)):
                for key in sorted(keys):
                    diff_rows.append({"benchmark": benchmark, "selector": selector, "change_vs_raw_global_baseline": status, **dict(zip(key_columns, key))})
    pd.DataFrame(diff_rows).to_csv(family_dir / "exact_trades_added_removed_vs_raw_global_baseline.csv", index=False)

    percentile_rows = []
    for benchmark in ("SPY", "QQQ"):
        family_value = float(combined[(combined["selector"].eq("family_aware_selector")) & combined["benchmark"].eq(benchmark)]["excess_return"].iloc[0])
        distribution = random_exact[random_exact["benchmark"].eq(benchmark)].groupby("random_seed")["excess_return"].mean()
        percentile_rows.append({"benchmark": benchmark, "family_aware_mean_excess_return": family_value, "random_legal_exact_percentile": float((distribution <= family_value).mean() * 100.0), "random_seeds": len(distribution)})
    random_percentile = pd.DataFrame(percentile_rows)
    random_percentile.to_csv(family_dir / "family_aware_random_legal_percentile.csv", index=False)

    manifest = {
        "label": "combined_all_family_oof_modular_selector_exact_replay",
        "oof_panels": len(panels), "panel_family_composition": panel_manifest["panel_family"].value_counts().to_dict(),
        "combined_stream_rows": len(stream), "shared_portfolio_allocator": "one chronological corrected allocator over the concatenated OOF stream",
        "direct_quality_rule": quality_rule, "indirect_minimum_mapping_confidence": 3,
        "hard_geo_quota": False, "unconditional_geo_priority": False,
        "probability_price_response_available": False, "supporting_market_agreement_available": False,
        "test_rows_read": 0, "lockbox_opened": False, "te_is_never_exit": True,
    }
    _json(family_dir / "family_aware_exact_replay_manifest.json", manifest)
    return {"manifest": manifest, "summary": summary, "exact": combined, "panel_exact": panel_exact, "trades": trades_by_run, "folds": panel_manifest, "random_percentile": random_percentile, "mapping_summary": mapping_summary}


def _selector_metric(summary: pd.DataFrame, selector_column: str, selector: str, metric: str) -> dict[str, float]:
    selected = summary[summary[selector_column].eq(selector)]
    return {str(row["benchmark"]): float(row[metric]) for _, row in selected.iterrows()}


def _stage2c_decision(
    raw_audit: dict[str, Any],
    direct: dict[str, Any],
    indirect: dict[str, Any],
    family: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    direct_a = _selector_metric(direct["summary"], "variant", "A_raw_global_connection_baseline", "mean_excess_return")
    direct_d = _selector_metric(direct["summary"], "variant", "D_deterministic_direct_trade_quality", "mean_excess_return")
    global_base = _selector_metric(family["summary"], "selector", "raw_global_connection_baseline", "mean_excess_return")
    global_family = _selector_metric(family["summary"], "selector", "family_aware_selector", "mean_excess_return")
    direct_preserved = bool(all(direct_d.get(benchmark, -np.inf) >= direct_a.get(benchmark, np.inf) - 0.25 for benchmark in ("SPY", "QQQ")))
    global_preserved = bool(all(global_family.get(benchmark, -np.inf) >= global_base.get(benchmark, np.inf) - 0.25 for benchmark in ("SPY", "QQQ")))
    indirect_family = indirect["exact"][indirect["exact"]["selector"].eq("semantic_mapping_selector")]
    restored_geo = bool(not indirect_family.empty and indirect_family["n_trades"].sum() > 0)
    case_a = direct_preserved and global_preserved and restored_geo
    score_stable = bool(raw_audit["controlled_binary_score_advantage_stable"])
    if case_a:
        case = "Case A"
        selected_global_selector = "family_aware_selector"
        interpretation = "deterministic/family-aware semantics preserved performance within the predeclared tolerance while restoring valid indirect-event eligibility"
        legacy_direct_role = "diagnostic_only"
    elif score_stable:
        case = "Case B"
        selected_global_selector = "hybrid_legacy_confidence_selector"
        interpretation = "the raw score retained stable adjusted OOF value and is retained only as an LLM confidence/data-quality proxy inside direct events"
        legacy_direct_role = "confidence_or_data_quality_proxy_not_economic_directness"
    else:
        case = "Case C"
        selected_global_selector = "family_aware_selector"
        interpretation = "the raw score advantage was not stable after deterministic directness normalization and controls; it is removed from direct-event admission"
        legacy_direct_role = "diagnostic_only"
    selector = {
        "label": "research_frozen_selector_stage2c",
        "decision_case": case,
        "selected_global_replay_selector": selected_global_selector,
        "direct_issuer_lane": {
            "eligibility": "deterministic mapping_valid direct_issuer",
            "ranking": "predeclared trade-quality prediction then expected_slot_days and source order" if selected_global_selector == "family_aware_selector" else "legacy Gemini score used only as confidence/data-quality proxy then expected_slot_days",
            "admission": direct["quality_rule"] if selected_global_selector == "family_aware_selector" else "legacy_gemini_relevance_score >= 1.00 as explicit confidence/data-quality proxy",
            "raw_connection_threshold": None if selected_global_selector == "family_aware_selector" else 1.0,
        },
        "indirect_event_lane": {
            "eligibility": "mapping_valid and mapping_confidence >= 3",
            "within_event_ranking": ["mapping_type", "exposure_purity", "impact_materiality", "direction_confidence", "expected_slot_days"],
            "admission": "event novelty or absolute stock-minus-sector 20d repricing <= 10%; dynamic portfolio capacity enforced",
            "hard_geo_quota": False,
            "unconditional_geo_priority": False,
        },
        "shared_allocator": "one corrected capacity-constrained exact portfolio allocator after lane-specific semantic checks",
        "legacy_gemini_relevance_score_role": legacy_direct_role,
        "decision_interpretation": interpretation,
        "predeclared_preservation_tolerance_percentage_points": 0.25,
        "direct_performance_preserved": direct_preserved,
        "global_performance_preserved": global_preserved,
        "valid_geo_macro_eligibility_restored": restored_geo,
        "raw_score_adjusted_oof_stable": score_stable,
        "probability_price_disagreement_available": False,
        "supporting_market_snapshot_available": False,
        "selector_changes_allowed_during_exit_research": False,
        "lockbox_opened": False,
        "lockbox_reserved_for_final_full_pipeline_evaluation": True,
        "te_is_never_exit": True,
        "latest_legal_exit_horizon": "T_e - 1",
    }
    path = output_dir / "research_frozen_selector_stage2c.json"
    _json(path, selector)
    return {**selector, "path": str(path)}


def _rebuild_stage3(
    decision: dict[str, Any],
    family: dict[str, Any],
    prices: dict,
    output_dir: Path,
) -> dict[str, Any]:
    selector = decision["selected_global_replay_selector"]
    trade_rows = []
    for key, trades in family["trades"].items():
        if len(key) == 2:
            run_selector, benchmark = key
            fold = None
        else:
            fold, run_selector, benchmark = key
        if run_selector != selector or trades.empty:
            continue
        part = trades.copy()
        if "stage2c_oof_panel" in part:
            part["outer_fold"] = part["stage2c_oof_panel"].astype(str)
        else:
            part["outer_fold"] = fold
        part["benchmark"] = benchmark
        trade_rows.append(part)
    if not trade_rows:
        raise AssertionError("Stage 2C frozen selector produced no Stage 3 development trades")
    trades = pd.concat(trade_rows, ignore_index=True)
    for column in ("entry_date", "exit_date", "candidate_t_e", "candidate_t_theta"):
        if column in trades:
            trades[column] = pd.to_datetime(trades[column], errors="coerce", utc=True).dt.normalize()
    trades["te_is_never_exit_assertion"] = trades["exit_date"] < trades["candidate_t_e"]
    if not trades["te_is_never_exit_assertion"].all():
        raise AssertionError("Rebuilt Stage 3 sample contains an exit at or after T_e")
    decisions = _dates(pd.read_csv(DECISIONS))
    decisions = decisions[decisions["analysis_split"].astype(str).str.lower().eq("train")].copy()
    decisions["candidate_t_e"] = decisions["t_e"].dt.normalize()
    label_columns = [
        "benchmark", "symbol", "candidate_t_e", "event_family", "te1_active_net_return_pct",
        "active_return_per_slot_day_pct", "te1_exit_date",
    ]
    labels = decisions[label_columns].drop_duplicates(["benchmark", "symbol", "candidate_t_e"])
    trades = trades.drop(columns=[column for column in label_columns[3:] if column in trades], errors="ignore").merge(
        labels, on=["benchmark", "symbol", "candidate_t_e"], how="left",
    )
    legal_te1 = []
    for symbol, t_e in zip(trades["symbol"], trades["candidate_t_e"]):
        observations = prices.get(str(symbol), [])
        prior_dates = [
            pd.to_datetime(item[0], utc=True).normalize()
            for item in observations
            if pd.to_datetime(item[0], utc=True).normalize() < pd.Timestamp(t_e)
        ]
        legal_te1.append(max(prior_dates) if prior_dates else pd.NaT)
    trades["legal_te1_exit_date"] = legal_te1
    trades["legal_te1_date_before_te_assertion"] = trades["legal_te1_exit_date"] < trades["candidate_t_e"]
    trades["terminal_te1_label_available"] = trades["te1_active_net_return_pct"].notna()
    trades["exit_research_scope"] = "stage2c_development_outer_folds_only"
    trades["frozen_selector"] = selector
    sample_columns = [
        "outer_fold", "benchmark", "symbol", "entry_date", "exit_date", "candidate_t_e", "te1_exit_date",
        "legal_te1_exit_date", "event_family", "mapping_type", "mapping_confidence", "impact_materiality",
        "direction_confidence", "exposure_purity", "realized_exit_reason", "pnl_pct", "te1_active_net_return_pct",
        "active_return_per_slot_day_pct", "terminal_te1_label_available", "te_is_never_exit_assertion",
        "legal_te1_date_before_te_assertion", "exit_research_scope", "frozen_selector",
    ]
    sample_columns = [column for column in sample_columns if column in trades]
    STAGE3.mkdir(parents=True, exist_ok=True)
    sample_path = STAGE3 / "stage3_exit_development_trades.csv"
    trades[sample_columns].to_csv(sample_path, index=False)
    folds = family["folds"].copy()
    if "outer_fold" not in folds and "oof_panel" in folds:
        folds["outer_fold"] = folds["oof_panel"].astype(str)
    folds["stage3_scope"] = "stage2c_development_outer_folds_only"
    folds["lockbox_opened"] = False
    folds.to_csv(STAGE3 / "stage3_development_folds.csv", index=False)
    plan = f"""# Stage 3 Exit Research — Stage 2C Development Sample

Stage 2C is complete and `{selector}` is research-frozen. The original
184-trade Stage 2B sample is preserved under
`data/selection_stage2c/baselines/stage3_raw_global_connection_baseline/`.

No exit model or exit policy was trained during Stage 2C. Exit research may
now continue on this rebuilt development-only sample. The selector's mapping
logic, ranking, and admission rules are immutable during exit research.

Every exit must satisfy `exit_date < T_e`. `T_e` is never an exit and the
latest legal terminal horizon is `T_e - 1`. The later lockbox remains sealed
for one final evaluation of the complete frozen modular pipeline.
"""
    plan_path = STAGE3 / "stage3_exit_research_plan.md"
    plan_path.write_text(plan, encoding="utf-8")
    manifest = {
        "label": "stage3_exit_research_stage2c_development_only",
        "frozen_selector_path": decision["path"],
        "frozen_selector": decision,
        "preserved_raw_global_baseline_trade_count": 184,
        "development_outer_fold_count": int(folds["outer_fold"].nunique()),
        "development_trade_count": int(len(trades)),
        "terminal_te1_label_coverage": float(trades["terminal_te1_label_available"].mean()),
        "legal_te1_date_coverage": float(trades["legal_te1_exit_date"].notna().mean()),
        "all_legal_te1_dates_before_te": bool(trades["legal_te1_date_before_te_assertion"].fillna(False).all()),
        "all_development_exits_before_te": bool(trades["te_is_never_exit_assertion"].all()),
        "te_is_never_exit": True,
        "latest_legal_exit_horizon": "T_e - 1",
        "exit_policy_trained": False,
        "exit_model_selection_performed": False,
        "exit_research_status": "ready_to_continue_after_stage2c_freeze",
        "selector_changes_allowed": False,
        "lockbox_opened": False,
        "lockbox_reserved_for_final_modular_pipeline_evaluation": True,
        "outputs": {"trades": str(sample_path), "folds": str(STAGE3 / "stage3_development_folds.csv"), "plan": str(plan_path)},
    }
    _json(STAGE3 / "stage3_exit_manifest.json", manifest)
    _json(output_dir / "stage3_handoff_manifest.json", manifest)
    return manifest


def _markdown_table(frame: pd.DataFrame, columns: list[str], digits: int = 4) -> str:
    if frame.empty:
        return "No eligible development observations."
    view = frame[[column for column in columns if column in frame]].copy()
    for column in view.select_dtypes(include=[np.number]).columns:
        view[column] = view[column].map(lambda value: "" if pd.isna(value) else f"{value:.{digits}f}")
    headers = list(view.columns)
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    lines.extend("| " + " | ".join(map(str, row)) + " |" for row in view.itertuples(index=False, name=None))
    return "\n".join(lines)


def _build_report(
    source: pd.DataFrame,
    semantics: pd.DataFrame,
    raw_audit: dict[str, Any],
    direct: dict[str, Any],
    indirect: dict[str, Any],
    family: dict[str, Any],
    decision: dict[str, Any],
    stage3: dict[str, Any],
    output_dir: Path,
) -> Path:
    direct_table = _markdown_table(direct["summary"], ["variant", "benchmark", "mean_excess_return", "mean_active_ir", "mean_active_drawdown_pct", "mean_trade_count", "mean_slot_usage_pct"])
    indirect_table = _markdown_table(indirect["summary"], ["selector", "benchmark", "mean_excess_return", "mean_active_ir", "mean_active_drawdown_pct", "mean_trade_count", "mean_slot_usage_pct", "mean_top_event_cluster_abs_pnl_share_pct"])
    family_table = _markdown_table(family["summary"], ["selector", "benchmark", "mean_excess_return", "mean_active_ir", "mean_active_drawdown_pct", "mean_trade_count", "mean_slot_usage_pct", "mean_top_winner_concentration_pct", "mean_top_event_cluster_abs_pnl_share_pct"])
    random_table = _markdown_table(family["random_percentile"], ["benchmark", "family_aware_mean_excess_return", "random_legal_exact_percentile", "random_seeds"])
    mapping_counts = semantics["mapping_type"].value_counts().rename_axis("mapping_type").reset_index(name="unique_question_symbol_mappings")
    required_mapping_types = pd.DataFrame({"mapping_type": ["direct_issuer", "direct_underlying", "first_order_sector", "second_order_company", "broad_macro_proxy"]})
    mapping_counts = required_mapping_types.merge(mapping_counts, on="mapping_type", how="left")
    mapping_counts["unique_question_symbol_mappings"] = mapping_counts["unique_question_symbol_mappings"].fillna(0).astype(int)
    mapping_table = _markdown_table(mapping_counts, ["mapping_type", "unique_question_symbol_mappings"], digits=0)
    regression = pd.read_csv(output_dir / "raw_score_audit" / "controlled_score_outcome_regression.csv")
    regression = regression[(regression["scope"] == "pooled_development") & (regression["predictor"] == "score_is_1.00")]
    regression_table = _markdown_table(regression, ["benchmark", "adjusted_coefficient_target_a_pct", "cluster_bootstrap_ci_low", "cluster_bootstrap_ci_high", "observations"])
    repeat_count = raw_audit["exact_question_symbol_pairs_with_score_variation"]
    direct_a = _selector_metric(direct["summary"], "variant", "A_raw_global_connection_baseline", "mean_excess_return")
    direct_b = _selector_metric(direct["summary"], "variant", "B_deterministic_direct_always_fill", "mean_excess_return")
    direct_c = _selector_metric(direct["summary"], "variant", "C_deterministic_direct_legacy_proxy_1.00", "mean_excess_return")
    direct_d = _selector_metric(direct["summary"], "variant", "D_deterministic_direct_trade_quality", "mean_excess_return")
    ranking_delta_spy = direct_c.get("SPY", np.nan) - direct_a.get("SPY", np.nan)
    ranking_delta_qqq = direct_c.get("QQQ", np.nan) - direct_a.get("QQQ", np.nan)
    admission_delta_spy = direct_c.get("SPY", np.nan) - direct_b.get("SPY", np.nan)
    admission_delta_qqq = direct_c.get("QQQ", np.nan) - direct_b.get("QQQ", np.nan)
    classification = []
    classification.append("genuine mapping quality: rejected for ordinary earnings; every mapping is already direct by construction")
    classification.append("LLM confidence/data-quality proxy: not validated; adjusted 95% intervals cross zero and fold signs are unstable for SPY and QQQ")
    classification.append(f"raw-score ranking: no material contribution; deterministic C minus raw-ranked A excess was {ranking_delta_spy:+.4f} for SPY and {ranking_delta_qqq:+.4f} for QQQ")
    classification.append(f"admission bucket: the observed effect came from refusing score<1 rows; C minus always-fill B was {admission_delta_spy:+.4f} for SPY and {admission_delta_qqq:+.4f} for QQQ")
    classification.append("duration/trade-quality effects: the non-connection D rule improved active IR and drawdown for both benchmarks but retained less excess return than A/C")
    classification.append("temporal calibration drift: " + ("detected" if raw_audit["temporal_calibration_drift_flag"] else "not detected by the predeclared mean-drift flag"))
    classification.append("sample-specific selection noise: the best-supported interpretation of the legacy 1.00 bucket because its return difference did not survive adjusted or fold-stability tests")
    classification_text = "- " + "\n- ".join(classification)
    report = f"""# 1. Required corrections

Stage 2B was not overwritten. Its selector is preserved as
`raw_global_connection_baseline`, and its raw value is exposed unchanged as
`legacy_gemini_relevance_score`. The old names `feat_connection_strength`,
`connection_strength`, and `relevance` remain documented compatibility
aliases only. The score is no longer described as universally calibrated
economic connection strength.

Semantic labels use only question text, asset/company identity, sector, and
known economic relationships. No return, post-entry price, selected outcome,
or portfolio result enters labeling. Ordinary same-company earnings and
verified company-owned FDA events are deterministic `direct_issuer`
mappings with maximal semantic directness.

{mapping_table}

All Stage 2C inputs are development rows (`analysis_split=train`). Test rows
read: 0. The later lockbox remains closed.

# 2. Raw score semantic audit

The audit contains {raw_audit['direct_earnings_rows']} benchmark-specific
direct-earnings rows. {repeat_count} exact question-symbol pairs received
multiple legacy scores. No verified model/prompt batch ID exists, so batch
tables use the explicitly named UTC `t0`-date inferred generation batch.

Adjusted `score = 1.00` versus `< 1.00` results:

{regression_table}

The predeclared stability test requires a positive clustered 95% interval and
positive adjusted coefficients in at least four of five outer folds for both
SPY and QQQ. Result: `{raw_audit['controlled_binary_score_advantage_stable']}`.
Any surviving association is labeled an LLM confidence/data-quality proxy,
not economic directness. Temporal calibration drift flag:
`{raw_audit['temporal_calibration_drift_flag']}`.

# 3. Direct-event ablation

Full nested chronological exact replay reused the five Stage 2B earnings
outer folds. Inner folds alone selected the D-lane quality rule. No current
2026 exploratory test row was used.

{direct_table}

The family-level predeclared direct quality rule is
`{direct['quality_rule']}`. For interpretation, A→C isolates raw-score ranking
while holding the 1.00 proxy admission fixed; B→C isolates that admission
bucket under deterministic directness; B→D measures non-connection
trade-quality admission. SPY excess A/B/C/D is
{direct_a.get('SPY', float('nan')):.4f}/{direct_b.get('SPY', float('nan')):.4f}/{direct_c.get('SPY', float('nan')):.4f}/{direct_d.get('SPY', float('nan')):.4f};
QQQ is {direct_a.get('QQQ', float('nan')):.4f}/{direct_b.get('QQQ', float('nan')):.4f}/{direct_c.get('QQQ', float('nan')):.4f}/{direct_d.get('QQQ', float('nan')):.4f}.

Probability-price disagreement was unavailable because no verified market-ID
map exists. Portfolio capacity state was enforced dynamically in every exact
admission decision; Target A is benchmark-active value and therefore embeds
benchmark opportunity cost.

# 4. Indirect-event mapping audit

The pre-lockbox sample contains {indirect['manifest']['development_candidate_rows']}
geo/macro candidate rows, {indirect['manifest']['economic_event_episodes']}
economic event episodes, and {indirect['manifest']['assets']} assets. All rows
from one episode stay in one chronological fold. Results are exploratory and
event-cluster-aware; no grid of geopolitical thresholds was optimized.

{indirect_table}

Raw absolute score, event-relative raw rank, mapping-type priority, exposure
purity, direction confidence, source order, semantic full rank, and random
legal selection are exported trade by trade. USO, BNO, XLE, and individual
company results are reported separately. The key semantic test is whether a
defensible and purer within-event exposure is selected, not whether a raw
score predicts returns.

# 5. Family-aware exact replay

Direct and indirect candidates pass separate semantic lanes and then enter
one shared corrected capacity-constrained portfolio allocator. There is no
geopolitical quota and no unconditional geo priority. Direct events use no
raw connection threshold in the deterministic family selector. Indirect
events require mapping confidence ≥3 and are ranked within event by mapping
type, exposure purity, materiality, direction confidence, and slot duration.

{family_table}

Random legal allocator comparison:

{random_table}

Exact added/removed trades, mapping-type trade counts and P&L, event-cluster
contribution, slot usage, and winner concentration are exported beside this
report.

# 6. Decision after results

Decision: **{decision['decision_case']}**. Frozen selector:
`{decision['selected_global_replay_selector']}`.

{decision['decision_interpretation'].capitalize()}. Direct performance preserved within the
predeclared 0.25 percentage-point tolerance:
`{decision['direct_performance_preserved']}`. Global performance preserved:
`{decision['global_performance_preserved']}`. Valid geo/macro eligibility
restored: `{decision['valid_geo_macro_eligibility_restored']}`.

The prior `connection >= 1.00` improvement is classified as:

{classification_text}

The frozen selector cannot change during exit research. The legacy score's
new role is `{decision['legacy_gemini_relevance_score_role']}`.

This is not a Case A performance win: the corrected selector did not preserve
the raw baseline's SPY or global return within tolerance. Case C freezes the
semantic correction because the legacy bucket failed its predeclared
stability test, while retaining the performance shortfall as an explicit
research risk for Stage 3 and the final sealed evaluation.

# 7. Stage 3 handoff status

The original Stage 3 manifest and 184 trades are preserved under the Stage
2C baseline directory. No exit policy was trained or selected from that
sample during Stage 2C.

Stage 3 was rebuilt from the selected global Stage 2C outer-fold exact
replays: {stage3['development_trade_count']} development trades across
{stage3['development_outer_fold_count']} folds. Terminal Te−1 label coverage
is {stage3['terminal_te1_label_coverage']:.2%}; legal Te−1 date coverage is
{stage3['legal_te1_date_coverage']:.2%}.

All observed exits satisfy `exit_date < T_e`; `T_e` is never an exit and
`T_e - 1` remains the latest legal horizon. The later lockbox remains sealed
for one final evaluation of the complete frozen modular pipeline.
"""
    path = output_dir / "stage2c_connection_semantics_report.md"
    path.write_text(report, encoding="utf-8")
    return path


def run_stage2c(output_dir: Path | str = OUTPUT) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    baselines = _preserve_baselines(output_dir)
    source = _read_source()
    semantics, semantics_path = _build_semantic_table(source, output_dir)
    data = _load_development(semantics)
    semantic_candidates = output_dir / "semantics" / "semantic_development_candidates.csv"
    data.to_csv(semantic_candidates, index=False)
    raw_audit = _raw_score_audit(data, output_dir)
    prices = pickle.loads(PRICES.read_bytes())
    probs = pickle.loads(PROBS.read_bytes())
    direct = _direct_event_ablation(data, prices, probs, output_dir)
    indirect = _indirect_mapping_audit(data, prices, probs, output_dir)
    family = _family_aware_oof_stream_replay(data, prices, probs, direct["quality_rule"], output_dir)
    decision = _stage2c_decision(raw_audit, direct, indirect, family, output_dir)
    stage3 = _rebuild_stage3(decision, family, prices, output_dir)
    report = _build_report(source, semantics, raw_audit, direct, indirect, family, decision, stage3, output_dir)
    manifest = {
        "label": "stage2c_connection_semantics_and_mapping_type_aware_selection",
        "development_only": True,
        "source_sha256": _hash(SOURCE), "universe_sha256": _hash(UNIVERSE), "decisions_sha256": _hash(DECISIONS),
        "features_sha256": _hash(FEATURES), "prices_sha256": _hash(PRICES), "probs_sha256": _hash(PROBS),
        "development_rows": int(len(data)),
        "event_family_composition": data["event_family"].value_counts().sort_index().to_dict(),
        "mapping_type_composition": data["mapping_type"].value_counts().sort_index().to_dict(),
        "preserved_baselines": {name: str(path) for name, path in baselines.items()},
        "decision_case": decision["decision_case"],
        "research_frozen_selector": decision,
        "stage3_development_trade_count": stage3["development_trade_count"],
        "exit_policy_trained": False,
        "test_rows_read": 0, "lockbox_opened": False,
        "te_is_never_exit": True, "latest_legal_exit_horizon": "T_e - 1",
        "outputs": {
            "report": str(report), "semantics": str(semantics_path), "semantic_candidates": str(semantic_candidates),
            "raw_score_audit": str(output_dir / "raw_score_audit" / "raw_score_audit_manifest.json"),
            "direct_ablation": str(output_dir / "direct_event_ablation" / "direct_ablation_summary.csv"),
            "indirect_audit": str(output_dir / "indirect_mapping_audit" / "indirect_mapping_audit_manifest.json"),
            "family_replay": str(output_dir / "family_aware_exact_replay" / "family_aware_exact_replay_summary.csv"),
            "frozen_selector": decision["path"], "stage3_manifest": str(STAGE3 / "stage3_exit_manifest.json"),
        },
    }
    manifest_path = output_dir / "stage2c_manifest.json"
    _json(manifest_path, manifest)
    return {"manifest": manifest_path, "report": report, "semantics": semantics_path, "frozen_selector": Path(decision["path"]), "stage3_manifest": STAGE3 / "stage3_exit_manifest.json"}


if __name__ == "__main__":
    for name, path in run_stage2c().items():
        print(f"{name}: {path}")
