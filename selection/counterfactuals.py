"""Exact Target C diagnostics from genuine dynamic capacity blocks.

For every candidate explicitly blocked by ``max_concurrent`` on the frozen
connection path, this module moves that candidate to the front of its actual
entry-day order and reruns the corrected simulator.  The resulting change in
final portfolio value is compared with the original path.  The same operation
is repeated with legacy continuation ordering.  These are policy-conditioned
counterfactual values, not absolute labels.
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
from .dynamic_replay import (
    DEFAULT_PRICES,
    DEFAULT_PROBS,
    DEFAULT_SOURCE,
    DEFAULT_UNIVERSE,
    _load_universe,
    _selector_universe,
)


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_REPLAY = PROJECT / "data" / "selection_stage2b" / "dynamic_replay"
DEFAULT_OUTPUT = PROJECT / "data" / "selection_stage2b" / "target_c_counterfactuals"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sim(frame: pd.DataFrame, prices: dict, probs: dict, benchmark: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    subset = frame[(frame["benchmark"].eq(benchmark)) & (frame["analysis_split"].eq("train"))].copy()
    start = pd.to_datetime(subset["t_theta"], utc=True).min().normalize()
    end = pd.to_datetime(subset["t_e"], utc=True).max().normalize()
    trades, _equity, stats, _meta, _allocation, _disposition = sim_opp_cost(
        subset,
        prices,
        probs,
        dict(PORT_DEFAULT),
        bench_sym=benchmark,
        initial=INITIAL_CAPITAL,
        start_date=start,
        end_date=end,
        allocation_mode=ALLOCATION_FIFO,
        collect_allocation_log=False,
    )
    if not trades.empty:
        exit_date = pd.to_datetime(trades["exit_date"], utc=True, errors="coerce").dt.normalize()
        candidate_te = pd.to_datetime(trades["candidate_t_e"], utc=True, errors="coerce").dt.normalize()
        if (exit_date >= candidate_te).any():
            raise AssertionError("Target C replay generated an exit at or after t_e")
    return trades, stats


def _force_first(frame: pd.DataFrame, market_id: str, symbol: str, entry_date: pd.Timestamp) -> tuple[pd.DataFrame, bool]:
    out = frame.copy()
    if "_selector_rank" not in out.columns:
        out["_selector_rank"] = np.nan
    mask = (
        out["market_id"].astype(str).eq(str(market_id))
        & out["symbol"].astype(str).eq(str(symbol))
        & pd.to_datetime(out["entry_date"], utc=True, errors="coerce").dt.normalize().eq(entry_date)
    )
    if not mask.any():
        return out, False
    out.loc[mask, "_selector_rank"] = -1
    return out, True


def _admitted(trades: pd.DataFrame, symbol: str, entry_date: pd.Timestamp) -> bool:
    if trades.empty:
        return False
    dates = pd.to_datetime(trades["entry_date"], utc=True, errors="coerce").dt.normalize()
    return bool((trades["symbol"].astype(str).eq(str(symbol)) & dates.eq(entry_date)).any())


def run_counterfactuals(
    universe_path: Path | str = DEFAULT_UNIVERSE,
    source_path: Path | str = DEFAULT_SOURCE,
    prices_path: Path | str = DEFAULT_PRICES,
    probs_path: Path | str = DEFAULT_PROBS,
    replay_dir: Path | str = DEFAULT_REPLAY,
    output_dir: Path | str = DEFAULT_OUTPUT,
) -> dict[str, Path]:
    universe_path, source_path, prices_path, probs_path, replay_dir, output_dir = map(
        Path, (universe_path, source_path, prices_path, probs_path, replay_dir, output_dir)
    )
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
    choice_path = PROJECT / "data" / "selection_stage2b" / "connection_tiebreakers" / "connection_tiebreaker_oof_choice.json"
    choice = json.loads(choice_path.read_text(encoding="utf-8"))
    random_seed = int(choice.get("random_median_seed", 0) or 0)
    connection = _selector_universe(universe, "connection_oof_tiebreaker", score_paths, random_seed)
    legacy = _selector_universe(universe, "legacy", score_paths, random_seed)
    target_labels = pd.read_csv(PROJECT / "data" / "selection_stage2b" / "target_diagnostics" / "target_labels.csv")
    target_labels["entry_date"] = pd.to_datetime(target_labels["entry_date"], utc=True, errors="coerce").dt.normalize()
    target_lookup = target_labels.set_index(["benchmark", "analysis_split", "entry_date", "symbol"])
    rows: list[dict[str, Any]] = []
    for benchmark in ("SPY", "QQQ"):
        allocation_path = replay_dir / "connection_oof_tiebreaker" / f"allocation_{benchmark.lower()}_train.csv"
        allocation = pd.read_csv(allocation_path)
        allocation["entry_date"] = pd.to_datetime(allocation["entry_date"], utc=True, errors="coerce").dt.normalize()
        blocked = allocation[allocation["skip_reason"].astype(str).eq("max_concurrent")].copy()
        blocked = blocked.drop_duplicates(["market_id", "symbol", "entry_date"])
        connection_base_trades, connection_base = _sim(connection, prices, probs, benchmark)
        legacy_base_trades, legacy_base = _sim(legacy, prices, probs, benchmark)
        subset_connection = connection[connection["benchmark"].eq(benchmark) & connection["analysis_split"].eq("train")].copy()
        subset_legacy = legacy[legacy["benchmark"].eq(benchmark) & legacy["analysis_split"].eq("train")].copy()
        for ordinal, (_, candidate) in enumerate(blocked.iterrows(), start=1):
            market_id = str(candidate.get("market_id", ""))
            symbol = str(candidate.get("symbol", ""))
            entry_date = pd.Timestamp(candidate["entry_date"])
            key = (benchmark, "train", entry_date, symbol)
            label = target_lookup.loc[key] if key in target_lookup.index else pd.Series(dtype=object)
            same_day = allocation[allocation["date"].astype(str).eq(str(candidate.get("date", "")))]
            blockers = same_day[same_day["decision"].astype(str).eq("selected")]["symbol"].astype(str).drop_duplicates().tolist()
            connection_forced, found_connection = _force_first(subset_connection, market_id, symbol, entry_date)
            legacy_forced, found_legacy = _force_first(subset_legacy, market_id, symbol, entry_date)
            if not found_connection or not found_legacy:
                continue
            connection_alt_trades, connection_alt = _sim(connection_forced, prices, probs, benchmark)
            legacy_alt_trades, legacy_alt = _sim(legacy_forced, prices, probs, benchmark)
            for continuation, base_stats, alt_stats, alt_trades in (
                ("connection_strength", connection_base, connection_alt, connection_alt_trades),
                ("legacy", legacy_base, legacy_alt, legacy_alt_trades),
            ):
                rows.append(
                    {
                        "continuation_policy": continuation,
                        "benchmark": benchmark,
                        "analysis_split": "train",
                        "market_id": market_id,
                        "symbol": symbol,
                        "entry_date": entry_date,
                        "blocking_reason": "max_concurrent",
                        "blocking_candidate_symbols": "|".join(blockers),
                        "base_final": float(base_stats.get("final", np.nan)),
                        "counterfactual_final": float(alt_stats.get("final", np.nan)),
                        "policy_conditioned_counterfactual_value": float(alt_stats.get("final", np.nan) - base_stats.get("final", np.nan)),
                        "base_excess_return": float(base_stats.get("excess_return", np.nan)),
                        "counterfactual_excess_return": float(alt_stats.get("excess_return", np.nan)),
                        "delta_excess_return": float(alt_stats.get("excess_return", np.nan) - base_stats.get("excess_return", np.nan)),
                        "forced_candidate_admitted": _admitted(alt_trades, symbol, entry_date),
                        "candidate_target_a_pct": label.get("target_a_active_return_pct", np.nan),
                        "candidate_target_b_slot_pct": label.get("target_b_active_per_slot_day_pct", np.nan),
                        "candidate_target_b_sqrt_pct": label.get("target_b_active_per_sqrt_slot_day_pct", np.nan),
                        "te1_exit_date": label.get("te1_exit_date", pd.NaT),
                        "t_e": label.get("t_e", pd.NaT),
                        "te1_horizon_assertion": bool(pd.to_datetime(label.get("te1_exit_date"), utc=True, errors="coerce") < pd.to_datetime(label.get("t_e"), utc=True, errors="coerce")),
                        "counterfactual_scope": "exact_dynamic_replay_from_genuine_max_concurrent_block",
                    }
                )
            if ordinal % 25 == 0:
                print(f"{benchmark}: processed {ordinal}/{len(blocked)} blocked candidates", flush=True)
    output_path = output_dir / "target_c_policy_conditioned_counterfactuals.csv"
    result = pd.DataFrame(rows)
    result.to_csv(output_path, index=False)
    summary_path = output_dir / "target_c_summary.csv"
    if result.empty:
        summary = pd.DataFrame()
    else:
        summary = result.groupby(["continuation_policy", "benchmark"], as_index=False).agg(
            n_candidates=("symbol", "size"),
            forced_admission_rate=("forced_candidate_admitted", "mean"),
            mean_policy_conditioned_value=("policy_conditioned_counterfactual_value", "mean"),
            median_policy_conditioned_value=("policy_conditioned_counterfactual_value", "median"),
            q10_policy_conditioned_value=("policy_conditioned_counterfactual_value", lambda x: x.quantile(0.10)),
            mean_delta_excess_return=("delta_excess_return", "mean"),
        )
    summary.to_csv(summary_path, index=False)
    manifest = {
        "label": "target_c_policy_conditioned_counterfactual_value",
        "continuation_policies": ["connection_strength", "legacy"],
        "competition_source": "exact replay rows with skip_reason=max_concurrent",
        "test_period_used_for_choice": False,
        "current_2026_test_is_exploratory": True,
        "includes_benchmark_opportunity_cost_future_capacity_costs": True,
        "is_absolute_ground_truth": False,
        "te_is_never_exit": True,
        "terminal_horizon": "every generated trade asserts exit_date < candidate_t_e",
        "outputs": {"detail": str(output_path), "summary": str(summary_path)},
    }
    manifest_path = output_dir / "target_c_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return {"detail": output_path, "summary": summary_path, "manifest": manifest_path}


if __name__ == "__main__":
    for name, path in run_counterfactuals().items():
        print(f"{name}: {path}")
