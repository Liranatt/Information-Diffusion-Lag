"""Aggregate the 10-seed treatment ladder + flagship + simple reference.

Outputs:
  cem_ladder_seed_results.csv   one row per (arm, seed, benchmark)
  cem_ladder_summary.csv        median/IQR/min/max/positive-seed stats per arm
  cem_ladder_paired_deltas.csv  adjacent-step paired deltas across seeds
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(r"C:\Users\Liran\PycharmProjects\cem_clean_repo")
OUT = Path(__file__).resolve().parent
BUNDLE = OUT.parent
SEEDS = list(range(42, 52))

ARMS = ["Baseline", "T2 TrainWindows", "T1+T2", "T1+T2+T3"]
KEEP = ["test_return_pct", "test_benchmark_return_pct", "test_excess_return_pct",
        "test_sharpe", "test_max_dd_pct", "test_trades", "test_trade_txn_cost",
        "test_total_txn_cost", "test_win_rate_pct"]


def main() -> None:
    rows = []
    for seed in SEEDS:
        results = pd.read_csv(ROOT / "runs" / f"icaif_ladder_seed{seed}" / "experiment_results_clean.csv")
        for _, r in results.iterrows():
            if r["experiment"] not in ARMS:
                continue
            rows.append({"arm": r["experiment"], "seed": seed, "benchmark": r["benchmark"],
                         **{k: float(r[k]) for k in KEEP}})
    # Flagship seed rows from the preserved robustness run-level CSV (6x20 only).
    run_level = pd.read_csv(BUNDLE / "robustness" / "icaif_robustness_run_level.csv")
    flag = run_level[(run_level["experiment"] == "T1+T2+T3+T4") & (run_level["budget"] == "6x20")]
    for _, r in flag.iterrows():
        rows.append({"arm": "T1+T2+T3+T4", "seed": int(r["seed"]), "benchmark": r["benchmark"],
                     **{k: float(r[k]) for k in KEEP}})
    frame = pd.DataFrame(rows)
    frame.to_csv(OUT / "cem_ladder_seed_results.csv", index=False)

    # Simple reference for positive-seed-rate comparison.
    decomp = pd.read_csv(OUT / "cem_decomposition_results.csv")
    simple = {b: float(decomp[(decomp["arm"] == "SIMPLE_DEFAULT") & (decomp["benchmark"] == b)]["test_excess_return_pct"].iloc[0])
              for b in ("SPY", "QQQ")}

    summary_rows = []
    for (arm, bench), g in frame.groupby(["arm", "benchmark"]):
        excess = g["test_excess_return_pct"]
        summary_rows.append({
            "arm": arm, "benchmark": bench, "n_seeds": len(g),
            "median_excess": excess.median(),
            "q25_excess": excess.quantile(0.25), "q75_excess": excess.quantile(0.75),
            "min_excess": excess.min(), "max_excess": excess.max(),
            "positive_excess_seeds": int((excess > 0).sum()),
            "seeds_beating_simple_reference": int((excess > simple[bench]).sum()),
            "simple_reference_excess": simple[bench],
            "median_return": g["test_return_pct"].median(),
            "median_sharpe": g["test_sharpe"].median(),
            "median_max_dd": g["test_max_dd_pct"].median(),
            "median_trades": g["test_trades"].median(),
            "median_trade_txn_cost": g["test_trade_txn_cost"].median(),
        })
    summary = pd.DataFrame(summary_rows).sort_values(["benchmark", "arm"])
    summary.to_csv(OUT / "cem_ladder_summary.csv", index=False)
    print(summary.to_string(index=False))

    ladder = ["Baseline", "T2 TrainWindows", "T1+T2", "T1+T2+T3", "T1+T2+T3+T4"]
    delta_rows = []
    wide = frame.pivot_table(index=["seed", "benchmark"], columns="arm",
                             values="test_excess_return_pct")
    for i in range(1, len(ladder)):
        upper, lower = ladder[i], ladder[i - 1]
        delta = (wide[upper] - wide[lower]).dropna()
        for bench in ("SPY", "QQQ"):
            d = delta.xs(bench, level="benchmark")
            delta_rows.append({
                "step": f"{upper} - {lower}", "benchmark": bench, "n": len(d),
                "median_delta_excess": d.median(),
                "q25": d.quantile(0.25), "q75": d.quantile(0.75),
                "min": d.min(), "max": d.max(),
                "wins": int((d > 0).sum()),
            })
    deltas = pd.DataFrame(delta_rows)
    deltas.to_csv(OUT / "cem_ladder_paired_deltas.csv", index=False)
    print()
    print(deltas.to_string(index=False))


if __name__ == "__main__":
    main()
