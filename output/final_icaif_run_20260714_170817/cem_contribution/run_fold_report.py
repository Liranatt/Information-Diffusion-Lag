"""Fold-level walk-forward reporting for the T2-only arm.

For every chronological fold of the T2-only (seed 42) run, re-simulates the
fold evaluation exactly as cem_search does (same policy, same eval window,
same truncation) to recover the full metric set including Sharpe and
transaction costs, and writes fold_level_results.csv.

Also quantifies policy-parameter stability: dispersion of each fitted
parameter across folds (per seed) and across seeds 42-51 (from the ladder
runs), for both the T2-only and T1+T2+T3 arms where available.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from sim_lib import ROOT, load_universe, sharpe_from_equity

from backtesting.optimize_cem import INITIAL_CAPITAL, as_utc_day, sim_opp_cost

OUT = Path(__file__).resolve().parent
SEEDS = list(range(42, 52))
T2_RUN = ROOT / "runs" / "icaif_t2_only_seed42_6x20"


def fold_rows(run_dir: Path, experiment: str) -> pd.DataFrame:
    audit = pd.read_csv(run_dir / "experiment_walkforward_folds_clean.csv")
    return audit[audit["experiment"] == experiment].copy()


def main() -> None:
    df, prices, probs, _oos_start, _oos_end = load_universe()
    theta = df["t_theta"]

    rows = []
    audit = fold_rows(T2_RUN, "T2 TrainWindows")
    for _, fold in audit.iterrows():
        bench = str(fold["benchmark"])
        policy = json.loads(fold["eval_policy_json"])
        eval_start = as_utc_day(pd.Timestamp(fold["eval_start_date"]))
        eval_end = as_utc_day(pd.Timestamp(fold["eval_end_date"]))
        eval_df = df[(theta >= eval_start) & (theta <= eval_end)].copy()
        trades, equity, stats, _, _, _ = sim_opp_cost(
            eval_df, prices, probs, policy,
            bench_sym=bench, initial=INITIAL_CAPITAL, use_kelly=False,
            start_date=eval_start, end_date=eval_end, allocation_mode="fifo",
        )
        # Consistency check against the stored audit numbers.
        assert abs(float(stats["total_return"]) - float(fold["eval_return_pct"])) < 0.02, (
            fold["fold"], bench, stats["total_return"], fold["eval_return_pct"])
        rows.append({
            "experiment": "T2 only",
            "benchmark": bench,
            "fold": int(fold["fold"]),
            "fit_label_cutoff": fold["fit_label_cutoff"],
            "eval_start": fold["eval_start_date"],
            "eval_end": fold["eval_end_date"],
            "fit_candidates": int(fold["fit_candidates"]),
            "eval_candidates": int(fold["eval_candidates"]),
            "executed_trades": int(stats["n_trades"]),
            "strategy_return_pct": float(stats["total_return"]),
            "benchmark_return_pct": float(stats["benchmark_return"]),
            "excess_return_pct": float(stats["excess_return"]),
            "sharpe": round(sharpe_from_equity(equity), 4),
            "max_dd_pct": float(stats["max_dd"]),
            "trade_txn_cost": float(stats["trade_txn_cost"]),
            "total_txn_cost": float(stats["total_txn_cost"]),
            "in_2026_test": pd.Timestamp(fold["eval_start_date"]) >= pd.Timestamp("2026-01-01"),
            "policy_json": fold["eval_policy_json"],
        })
    fold_table = pd.DataFrame(rows).sort_values(["benchmark", "fold"])
    fold_table.to_csv(OUT / "fold_level_results.csv", index=False)
    show = [c for c in fold_table.columns if c != "policy_json"]
    print(fold_table[show].to_string(index=False))

    # ── Policy-parameter stability ────────────────────────────────────────────
    stability_rows = []
    params = ["enter_floor", "enter_strong", "hold_days", "max_prob_surge",
              "max_price_runup", "atr_mult", "lock_activate", "theta_out",
              "position_size_pct", "max_concurrent"]

    # Across folds, per seed and experiment (from ladder runs).
    for experiment in ("T2 TrainWindows", "T1+T2+T3"):
        for seed in SEEDS:
            run_dir = ROOT / "runs" / f"icaif_ladder_seed{seed}"
            path = run_dir / "experiment_walkforward_folds_clean.csv"
            if not path.exists():
                continue
            audit = fold_rows(run_dir, experiment)
            for bench, g in audit.groupby("benchmark"):
                policies = pd.DataFrame([json.loads(p) for p in g["eval_policy_json"]])
                for param in params:
                    stability_rows.append({
                        "experiment": experiment, "seed": seed, "benchmark": bench,
                        "axis": "across_folds", "param": param,
                        "n": len(policies),
                        "mean": policies[param].mean(),
                        "std": policies[param].std(ddof=1),
                        "min": policies[param].min(),
                        "max": policies[param].max(),
                    })
    stability = pd.DataFrame(stability_rows)
    stability.to_csv(OUT / "fold_policy_stability.csv", index=False)
    if not stability.empty:
        agg = (stability.groupby(["experiment", "param"])
               .agg(mean_std=("std", "mean"), mean_range=("max", "mean"))
               .round(4))
        print("\nMean across-fold parameter std (averaged over seeds/benchmarks):")
        print(agg.to_string())


if __name__ == "__main__":
    main()
