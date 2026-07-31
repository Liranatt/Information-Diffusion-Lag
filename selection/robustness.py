"""Capacity and legal-random-allocation robustness diagnostics."""

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
DEFAULT_OUTPUT = PROJECT / "data" / "selection_stage2b" / "robustness"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(frame: pd.DataFrame, prices: dict, probs: dict, benchmark: str, split: str, capacity: int) -> dict[str, Any]:
    subset = frame[frame["benchmark"].eq(benchmark) & frame["analysis_split"].eq(split)].copy()
    policy = dict(PORT_DEFAULT)
    policy["max_concurrent"] = int(capacity)
    start = pd.to_datetime(subset["t_theta"], utc=True).min().normalize()
    end = pd.to_datetime(subset["t_e"], utc=True).max().normalize()
    trades, equity, stats, _meta, _allocation, _disposition = sim_opp_cost(
        subset, prices, probs, policy, bench_sym=benchmark, initial=INITIAL_CAPITAL,
        start_date=start, end_date=end, allocation_mode=ALLOCATION_FIFO, collect_allocation_log=True,
    )
    exit_date = pd.to_datetime(trades["exit_date"], utc=True, errors="coerce").dt.normalize() if not trades.empty else pd.Series(dtype="datetime64[ns, UTC]")
    candidate_te = pd.to_datetime(trades["candidate_t_e"], utc=True, errors="coerce").dt.normalize() if not trades.empty else pd.Series(dtype="datetime64[ns, UTC]")
    if not trades.empty and (exit_date >= candidate_te).any():
        raise AssertionError("Robustness replay generated an exit at or after t_e")
    pnl = pd.to_numeric(trades.get("pnl", pd.Series(dtype=float)), errors="coerce")
    winners = pnl.sort_values(ascending=False).reset_index(drop=True)
    remove_one = float(stats.get("total_return", 0.0)) - float(winners.iloc[0] / INITIAL_CAPITAL * 100.0) if len(winners) else float(stats.get("total_return", 0.0))
    remove_five = float(stats.get("total_return", 0.0)) - float(winners.head(5).sum() / INITIAL_CAPITAL * 100.0) if len(winners) else float(stats.get("total_return", 0.0))
    selector_value = frame["_robustness_selector"].iloc[0] if "_robustness_selector" in frame.columns and not frame.empty else ""
    return {
        "selector": str(selector_value),
        "benchmark": benchmark,
        "analysis_split": split,
        "capacity": capacity,
        "total_return": stats.get("total_return", np.nan),
        "benchmark_return": stats.get("benchmark_return", np.nan),
        "excess_return": stats.get("excess_return", np.nan),
        "active_information_ratio": stats.get("active_information_ratio", np.nan),
        "active_max_drawdown_pct": stats.get("active_max_drawdown_pct", np.nan),
        "n_trades": stats.get("n_trades", 0),
        "turnover_notional": float(trades.get("_asset_entry_notional", pd.Series(dtype=float)).sum()) if not trades.empty else 0.0,
        "total_txn_cost": stats.get("total_txn_cost", np.nan),
        "gross_event_exposure": stats.get("gross_event_exposure", np.nan),
        "mean_trade_active_return_proxy_pct": float(pd.to_numeric(trades.get("pnl_pct", pd.Series(dtype=float)), errors="coerce").mean()) if not trades.empty else np.nan,
        "return_without_largest_winner": remove_one,
        "return_without_five_largest_winners": remove_five,
        "te_is_never_exit": True,
    }


def run_robustness(
    universe_path: Path | str = DEFAULT_UNIVERSE,
    source_path: Path | str = DEFAULT_SOURCE,
    prices_path: Path | str = DEFAULT_PRICES,
    probs_path: Path | str = DEFAULT_PROBS,
    output_dir: Path | str = DEFAULT_OUTPUT,
) -> dict[str, Path]:
    universe_path, source_path, prices_path, probs_path, output_dir = map(Path, (universe_path, source_path, prices_path, probs_path, output_dir))
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
    choice = json.loads((PROJECT / "data" / "selection_stage2b" / "connection_tiebreakers" / "connection_tiebreaker_oof_choice.json").read_text(encoding="utf-8"))
    random_seed = int(choice.get("random_median_seed", 0) or 0)
    selector_names = ("connection_oof_tiebreaker", "pairwise_v2", "monotonic_additive", "pooled_set")
    rows = []
    for selector in selector_names:
        frame = _selector_universe(universe, selector, score_paths, random_seed)
        frame["_robustness_selector"] = selector
        for capacity in (8, 10, 12):
            for benchmark in ("SPY", "QQQ"):
                for split in ("train", "test"):
                    rows.append(_run(frame, prices, probs, benchmark, split, capacity))
    capacity_path = output_dir / "capacity_robustness.csv"
    pd.DataFrame(rows).to_csv(capacity_path, index=False)
    random_rows = []
    for seed in range(20):
        frame = _selector_universe(universe, "connection_random_median", score_paths, seed)
        frame["_robustness_selector"] = "random_legal_tie_allocator"
        for benchmark in ("SPY", "QQQ"):
            for split in ("train", "test"):
                row = _run(frame, prices, probs, benchmark, split, 10)
                row["random_seed"] = seed
                random_rows.append(row)
    random_path = output_dir / "random_legal_allocator.csv"
    pd.DataFrame(random_rows).to_csv(random_path, index=False)
    manifest = {
        "label": "stage2b_robustness_diagnostics",
        "capacity_grid": [8, 10, 12],
        "random_legal_allocator_seeds": list(range(20)),
        "random_allocator_scope": "connection tie groups with deterministic final keys",
        "test_period_used_for_choice": False,
        "current_2026_test_is_exploratory": True,
        "cost_model": "frozen corrected simulator; cost stress not used to tune selector",
        "te_is_never_exit": True,
        "outputs": {"capacity": str(capacity_path), "random_allocator": str(random_path)},
    }
    manifest_path = output_dir / "robustness_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"capacity": capacity_path, "random_allocator": random_path, "manifest": manifest_path}


if __name__ == "__main__":
    for name, path in run_robustness().items():
        print(f"{name}: {path}")
