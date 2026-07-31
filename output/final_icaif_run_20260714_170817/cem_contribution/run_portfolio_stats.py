"""Dependence-aware portfolio-level inference for the principal arms.

For each arm (SIMPLE_DEFAULT, ALL_ELIGIBLE_SIMPLE_EXEC, CEM_FULL seed 42) and
benchmark: mean daily excess return, annualized Sharpe of the strategy, a 95%
moving-block bootstrap CI for the mean daily excess (block length 5, 20,000
replications, seed 42), and a null-centered one-sided p-value.

Output: portfolio_block_bootstrap.csv
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from sim_lib import ROOT, SIMPLE_POLICY, hybrid_policy, run_test, sharpe_from_equity

OUT = Path(__file__).resolve().parent
N_BOOT = 20_000
BLOCK = 5
SEED = 42

PERMISSIVE_SELECTION = {
    "enter_strong": 0.55, "enter_floor": 0.55, "hold_days": 1,
    "max_prob_surge": 999.0, "max_price_runup": 999.0,
}


def seed42_baseline_policy(bench: str) -> dict:
    results = pd.read_csv(ROOT / "runs" / "icaif_base_vs_all_seed42" / "experiment_results_clean.csv")
    row = results[(results["experiment"] == "Baseline") & (results["benchmark"] == bench)].iloc[0]
    return json.loads(row["policy_snapshot_json"])


def block_bootstrap(x: np.ndarray, rng: np.random.Generator) -> dict:
    n = len(x)
    block = min(BLOCK, n)
    starts = np.arange(0, n - block + 1)
    n_blocks = int(np.ceil(n / block))
    boot = np.empty(N_BOOT)
    null = np.empty(N_BOOT)
    centered = x - x.mean()
    for i in range(N_BOOT):
        chosen = rng.choice(starts, size=n_blocks, replace=True)
        idx = np.concatenate([np.arange(s, s + block) for s in chosen])[:n]
        boot[i] = x[idx].mean()
        null[i] = centered[idx].mean()
    observed = float(x.mean())
    return {
        "mean_daily_excess": observed,
        "boot_ci_lo": float(np.quantile(boot, 0.025)),
        "boot_ci_hi": float(np.quantile(boot, 0.975)),
        "p_one_sided_null_centered": float((1 + np.sum(null >= observed)) / (N_BOOT + 1)),
        "block_length": BLOCK,
        "n_bootstrap": N_BOOT,
        "n_daily_returns": n,
    }


def main() -> None:
    rng = np.random.default_rng(SEED)
    rows = []
    for bench in ("SPY", "QQQ"):
        arms = (
            ("SIMPLE_DEFAULT", dict(SIMPLE_POLICY)),
            ("ALL_ELIGIBLE_SIMPLE_EXEC", hybrid_policy(PERMISSIVE_SELECTION, SIMPLE_POLICY)),
            ("CEM_FULL_seed42", seed42_baseline_policy(bench)),
        )
        for label, policy in arms:
            _t, equity, stats = run_test(policy, bench)
            eq = equity.copy()
            eq["strategy_return"] = eq["equity"].astype(float).pct_change()
            eq["bench_return"] = eq["benchmark_equity"].astype(float).pct_change()
            excess = (eq["strategy_return"] - eq["bench_return"]).dropna().to_numpy(float)
            rows.append({
                "arm": label,
                "benchmark": bench,
                "test_return_pct": stats["total_return"],
                "test_excess_return_pct": stats["excess_return"],
                "annualized_sharpe": round(sharpe_from_equity(equity), 4),
                **block_bootstrap(excess, rng),
            })
            print(f"{label} {bench}: excess {stats['excess_return']:+.2f} done", flush=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(OUT / "portfolio_block_bootstrap.csv", index=False)
    print(frame.to_string(index=False))


if __name__ == "__main__":
    main()
