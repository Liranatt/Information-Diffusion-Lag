"""Exact selector-specific replay through the corrected portfolio engine.

This module freezes the execution, sizing, costs, benchmark rotation, and
kernel exit policy from ``backtesting.optimize_cem``.  Only the candidate
ordering is changed per selector.  The engine then recomputes capacity,
capital, exposure, and later admissions along each independent portfolio path.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtesting.optimize_cem import (
    ALLOCATION_FIFO,
    PORT_DEFAULT,
    INITIAL_CAPITAL,
    sim_opp_cost,
)
from .baselines import _read_candidates
from .connection_tiebreakers import _prepare, _stable_hash, _tie_order
from .admission import make_admission_policy


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_UNIVERSE = PROJECT / "data" / "candidates_audit_clean.parquet"
DEFAULT_SOURCE = (
    PROJECT
    / "assistant_generated_all_artifacts"
    / "assistant_generated_research_core"
    / "trade_opportunity_research"
    / "symbol_day_current_priority.csv"
)
DEFAULT_PRICES = (
    PROJECT
    / "assistant_generated_all_artifacts"
    / "assistant_generated_research_large_supplements"
    / "prices_open_merged.pkl"
)
DEFAULT_PROBS = PROJECT / "data" / "probs.pkl"
DEFAULT_OUTPUT = PROJECT / "data" / "selection_stage2b" / "dynamic_replay"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_keys(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    def normalize(value: Any) -> str:
        text = str(value)
        try:
            number = float(text)
            if np.isfinite(number) and number.is_integer():
                return str(int(number))
        except (TypeError, ValueError):
            pass
        return text
    for col in ("event_id", "market_id"):
        if col in out.columns:
            out[col] = out[col].map(normalize)
    return out


def _load_universe(universe_path: Path, source_path: Path) -> pd.DataFrame:
    universe = _normalize_keys(pd.read_parquet(universe_path))
    source = _normalize_keys(pd.read_csv(source_path))
    for frame in (universe, source):
        for col in ("t_theta", "t_e", "entry_date"):
            if col in frame.columns:
                frame[col] = pd.to_datetime(frame[col], errors="coerce", utc=True).dt.normalize()
    source_columns = [
        c
        for c in (
            "event_id",
            "market_id",
            "benchmark",
            "entry_date",
            "symbol",
            "economic_event_group_clean",
            "economic_event_id",
            "source_row_number",
            "event_family",
            "feat_sector",
            "entry_prob",
        )
        if c in source.columns
    ]
    source = source[source_columns].drop_duplicates(["event_id", "market_id", "benchmark"])
    merged = universe.merge(source, on=["event_id", "market_id"], how="left", suffixes=("", "_source"))
    # Stage 2B is defined on the current-priority opportunity universe.  The
    # cleaned engine parquet contains additional legacy rows that have no
    # benchmark/date row in that universe; exclude them explicitly rather than
    # inventing a benchmark assignment or entry date.
    merged = merged.loc[merged["benchmark"].notna() & merged["entry_date"].notna()].copy()
    merged["analysis_split"] = merged["split"].astype(str).str.lower().replace({"val": "test"})
    merged["entry_date"] = pd.to_datetime(merged["entry_date"], utc=True).dt.normalize()
    merged["t_theta"] = pd.to_datetime(merged["t_theta"], utc=True)
    merged["t_e"] = pd.to_datetime(merged["t_e"], utc=True)
    return merged


def _merge_score(universe: pd.DataFrame, score_path: Path, score_column: str) -> pd.DataFrame:
    scores = _normalize_keys(pd.read_csv(score_path))
    scores["entry_date"] = pd.to_datetime(scores["entry_date"], errors="coerce", utc=True).dt.normalize()
    keep = [c for c in ("benchmark", "analysis_split", "entry_date", "symbol", score_column) if c in scores.columns]
    if score_column not in keep:
        raise ValueError(f"{score_path} does not contain {score_column}")
    scores = scores[keep].drop_duplicates(["benchmark", "analysis_split", "entry_date", "symbol"])
    merged = universe.merge(
        scores,
        on=["benchmark", "analysis_split", "entry_date", "symbol"],
        how="left",
        suffixes=("", "_score"),
    )
    return merged


def _set_score_rank(frame: pd.DataFrame, score_column: str, rank_column: str) -> pd.DataFrame:
    out = frame.copy()
    out[score_column] = pd.to_numeric(out[score_column], errors="coerce")
    out[rank_column] = np.nan
    for _, day in out.groupby(["benchmark", "analysis_split", "entry_date"], sort=False):
        order = day.sort_values([score_column, "entry_prob", "symbol"], ascending=[False, False, True], kind="mergesort").index
        out.loc[order, rank_column] = np.arange(len(order), dtype=int)
    out[rank_column] = out[rank_column].fillna(10**9).astype(int)
    out["_admission_score"] = out[score_column]
    return out


def _set_connection_rank(frame: pd.DataFrame, tie_breaker: str, seed: int = 0) -> pd.DataFrame:
    out = _prepare(frame)
    out["_selector_rank"] = 10**9
    for _, day in out.groupby(["benchmark", "analysis_split", "entry_date"], sort=False):
        order = _tie_order(day, tie_breaker, seed)
        out.loc[order, "_selector_rank"] = np.arange(len(order), dtype=int)
    out["_admission_score"] = pd.to_numeric(out["feat_connection_strength"], errors="coerce")
    return out


def _selector_universe(
    universe: pd.DataFrame,
    selector: str,
    score_paths: dict[str, Path],
    random_seed: int,
    admission_policy_name: str | None = None,
) -> pd.DataFrame:
    if selector == "legacy":
        out = universe.copy()
        out["_selector_rank"] = np.nan
        out["_admission_score"] = pd.to_numeric(out.get("feat_connection_strength"), errors="coerce")
        return out
    if selector == "connection_source_order":
        return _set_connection_rank(universe, "source_order")
    if selector == "connection_oof_tiebreaker":
        return _set_connection_rank(universe, "expected_slot_days")
    if selector == "connection_random_median":
        return _set_connection_rank(universe, "random", random_seed)
    score_specs = {
        "fixed_monotonic": (score_paths["v1"], "score_fixed_monotonic"),
        "pairwise_v1": (score_paths["v1"], "score_pairwise_v1"),
        "pairwise_v2": (score_paths["v2"], "score_pairwise_v2"),
        "monotonic_additive": (score_paths["monotonic"], "score_monotonic"),
        "pooled_set": (score_paths["pooled"], "score_pooled"),
    }
    path, score_column = score_specs[selector]
    out = _merge_score(universe, path, score_column)
    return _set_score_rank(out, score_column, "_selector_rank")


def _attach_admission_values(
    frame: pd.DataFrame,
    admission_policy_name: str | None,
    score_paths: dict[str, Path],
) -> pd.DataFrame:
    if admission_policy_name is None or admission_policy_name == "always_fill":
        return frame
    out = frame.copy()
    if admission_policy_name == "min_connection_strength":
        out["_admission_score"] = pd.to_numeric(out["feat_connection_strength"], errors="coerce")
        return out
    mono = _merge_score(out, score_paths["monotonic"], "score_monotonic")
    mono_score = pd.to_numeric(mono["score_monotonic"], errors="coerce")
    out["_admission_score"] = mono_score.to_numpy()
    horizon = pd.to_numeric(out["feat_time_to_resolution_days"], errors="coerce").clip(lower=1.0)
    if admission_policy_name == "min_predicted_target_b_slot":
        out["_admission_target"] = mono_score.to_numpy() / horizon.to_numpy()
    elif admission_policy_name == "min_predicted_target_b_sqrt":
        out["_admission_target"] = mono_score.to_numpy() / np.sqrt(horizon.to_numpy())
    elif admission_policy_name == "min_predicted_target_a":
        out["_admission_target"] = mono_score.to_numpy()
    else:
        raise ValueError(f"Unknown admission policy: {admission_policy_name}")
    return out


def _active_metrics(equity: pd.DataFrame) -> dict[str, float]:
    if equity.empty or len(equity) < 2:
        return {"active_information_ratio": 0.0, "active_max_drawdown_pct": 0.0}
    active = equity["equity"].astype(float) / equity["benchmark_equity"].astype(float)
    returns = active.pct_change().dropna()
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    ir = float(returns.mean() / std * np.sqrt(252.0)) if std > 1e-12 else 0.0
    peaks = np.maximum.accumulate(active.to_numpy(dtype=float))
    dd = active.to_numpy(dtype=float) / np.maximum(peaks, 1e-12) - 1.0
    return {
        "active_information_ratio": ir,
        "active_max_drawdown_pct": float(dd.min() * 100.0) if len(dd) else 0.0,
    }


def _replay_one(
    frame: pd.DataFrame,
    prices: dict,
    probs: dict,
    selector: str,
    benchmark: str,
    split: str,
    admission_policy_name: str | None = None,
    admission_threshold: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    subset = frame[(frame["benchmark"].eq(benchmark)) & (frame["analysis_split"].eq(split))].copy()
    if subset.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {
            "selector": selector,
            "admission_policy": admission_policy_name or "always_fill",
            "admission_threshold": admission_threshold,
            "benchmark": benchmark,
            "analysis_split": split,
            "n_trades": 0,
        }
    policy = dict(PORT_DEFAULT)
    start = pd.to_datetime(subset["t_theta"], utc=True).min().normalize()
    end = pd.to_datetime(subset["t_e"], utc=True).max().normalize()
    trades, equity, stats, _, allocation, disposition = sim_opp_cost(
        subset,
        prices,
        probs,
        policy,
        bench_sym=benchmark,
        initial=INITIAL_CAPITAL,
        start_date=start,
        end_date=end,
        allocation_mode=ALLOCATION_FIFO,
        collect_allocation_log=True,
        admission_policy=(
            make_admission_policy(admission_policy_name, admission_threshold)
            if admission_policy_name is not None
            else None
        ),
    )
    if not trades.empty:
        exit_date = pd.to_datetime(trades["exit_date"], errors="coerce", utc=True).dt.normalize()
        candidate_te = pd.to_datetime(trades["candidate_t_e"], errors="coerce", utc=True).dt.normalize()
        if (exit_date >= candidate_te).any():
            raise AssertionError(f"{selector}/{benchmark}/{split} contains an exit at or after t_e")
    result = {
        "selector": selector,
        "admission_policy": admission_policy_name or "always_fill",
        "admission_threshold": admission_threshold,
        "benchmark": benchmark,
        "analysis_split": split,
        **stats,
        **_active_metrics(equity),
        "turnover_notional": float(trades["_asset_entry_notional"].sum()) if not trades.empty and "_asset_entry_notional" in trades else 0.0,
        "selected_decisions": int((allocation.get("decision", pd.Series(dtype=object)) == "selected").sum()) if not allocation.empty else 0,
        "rejected_or_skipped_decisions": int((allocation.get("decision", pd.Series(dtype=object)) == "skipped").sum()) if not allocation.empty else 0,
        "admission_rejected": int((allocation.get("skip_reason", pd.Series(dtype=object)) == "admission_reject").sum()) if not allocation.empty else 0,
        "admission_stopped": int((allocation.get("skip_reason", pd.Series(dtype=object)) == "admission_stop").sum()) if not allocation.empty else 0,
        "blocked_by_capacity": int((allocation.get("skip_reason", pd.Series(dtype=object)) == "max_concurrent").sum()) if not allocation.empty else 0,
    }
    return trades, equity, allocation, result


def run_dynamic_replay(
    universe_path: Path | str = DEFAULT_UNIVERSE,
    source_path: Path | str = DEFAULT_SOURCE,
    prices_path: Path | str = DEFAULT_PRICES,
    probs_path: Path | str = DEFAULT_PROBS,
    output_dir: Path | str = DEFAULT_OUTPUT,
) -> dict[str, Path]:
    universe_path = Path(universe_path)
    source_path = Path(source_path)
    prices_path = Path(prices_path)
    probs_path = Path(probs_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    universe = _load_universe(universe_path, source_path)
    prices = pickle.loads(prices_path.read_bytes())
    probs = pickle.loads(probs_path.read_bytes())
    score_paths = {
        "v1": PROJECT / "data" / "selection_stage1" / "selection_scores.csv",
        "v2": PROJECT / "data" / "selection_stage2b" / "pairwise_v2" / "pairwise_v2_scores.csv",
        "monotonic": PROJECT / "data" / "selection_stage2b" / "ranking_models" / "monotonic_scores.csv",
        "pooled": PROJECT / "data" / "selection_stage2b" / "ranking_models" / "pooled_scores.csv",
    }
    for path in score_paths.values():
        if not path.exists():
            raise FileNotFoundError(path)
    choice_path = PROJECT / "data" / "selection_stage2b" / "connection_tiebreakers" / "connection_tiebreaker_oof_choice.json"
    choice = json.loads(choice_path.read_text(encoding="utf-8"))
    random_seed = int(choice.get("random_median_seed", 0) or 0)
    selectors = (
        ("legacy", None, None),
        ("connection_source_order", None, None),
        ("connection_oof_tiebreaker", None, None),
        ("connection_random_median", None, None),
        ("fixed_monotonic", None, None),
        ("pairwise_v1", None, None),
        ("pairwise_v2", None, None),
        ("monotonic_additive", None, None),
        ("pooled_set", None, None),
    )
    admission_choice_path = PROJECT / "data" / "selection_stage2b" / "admission" / "admission_choices.json"
    if admission_choice_path.exists():
        admission_choices = json.loads(admission_choice_path.read_text(encoding="utf-8"))
        selected = admission_choices.get("selected_for_initial_dynamic_replay", {})
        selected_name = selected.get("policy")
        selected_threshold = selected.get("threshold")
        if selected_name and selected_name != "always_fill":
            selectors = selectors + (
                ("connection_oof_tiebreaker", selected_name, selected_threshold),
            )

    results = []
    for selector, admission_name, admission_threshold in selectors:
        ranked = _selector_universe(universe, selector, score_paths, random_seed, admission_name)
        ranked = _attach_admission_values(ranked, admission_name, score_paths)
        run_name = selector if admission_name is None else f"{selector}__admission_{admission_name}"
        selector_dir = output_dir / run_name
        for benchmark in ("SPY", "QQQ"):
            for split in ("train", "test"):
                trades, equity, allocation, result = _replay_one(
                    ranked,
                    prices,
                    probs,
                    selector,
                    benchmark,
                    split,
                    admission_name,
                    admission_threshold,
                )
                selector_dir.mkdir(parents=True, exist_ok=True)
                stem = f"{benchmark.lower()}_{split}"
                trades.to_csv(selector_dir / f"trades_{stem}.csv", index=False)
                equity.to_csv(selector_dir / f"equity_{stem}.csv", index=False)
                allocation.to_csv(selector_dir / f"allocation_{stem}.csv", index=False)
                results.append(result)
    summary_path = output_dir / "exact_replay_summary.csv"
    manifest_path = output_dir / "exact_replay_manifest.json"
    pd.DataFrame(results).to_csv(summary_path, index=False)
    manifest = {
        "label": "exact_dynamic_selector_replay",
        "universe_sha256": _hash(universe_path),
        "source_sha256": _hash(source_path),
        "prices_sha256": _hash(prices_path),
        "probs_sha256": _hash(probs_path),
        "selectors": [
            {"selector": selector, "admission_policy": admission_name or "always_fill", "threshold": threshold}
            for selector, admission_name, threshold in selectors
        ],
        "connection_oof_tiebreaker": choice.get("selected_deterministic_tie_breaker"),
        "random_median_seed": random_seed,
        "execution_engine": "backtesting.optimize_cem.sim_opp_cost",
        "frozen_policy": dict(PORT_DEFAULT),
        "frozen_sizing_costs_execution": True,
        "terminal_horizon": "kernel closes no later than t_e - 1; assertion exit_date < candidate_t_e",
        "te_is_never_exit": True,
        "current_2026_test_is_exploratory": True,
        "admission_thresholds_selected_on_training_oof_only": True,
        "outputs": {"summary": str(summary_path)},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return {"summary": summary_path, "manifest": manifest_path, "directory": output_dir}


if __name__ == "__main__":
    for name, path in run_dynamic_replay().items():
        print(f"{name}: {path}")
