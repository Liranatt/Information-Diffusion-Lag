"""Fold-level results for the all-treatment arm (T1+T2+T3+T4, seed 42).

Same re-simulation approach as run_fold_report.py but with the arm's actual
evaluation settings: half-Kelly sizing ON and event-priority allocation, as in
cem_search's fold evaluation. Appends rows to fold_level_results.csv.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from sim_lib import ROOT, load_universe, sharpe_from_equity

from backtesting.optimize_cem import INITIAL_CAPITAL, as_utc_day, sim_opp_cost

OUT = Path(__file__).resolve().parent
RUN = ROOT / "runs" / "icaif_base_vs_all_seed42"


def main() -> None:
    df, prices, probs, _s, _e = load_universe()
    theta = df["t_theta"]
    audit = pd.read_csv(RUN / "experiment_walkforward_folds_clean.csv")
    audit = audit[audit["experiment"] == "T1+T2+T3+T4"]

    rows = []
    for _, fold in audit.iterrows():
        bench = str(fold["benchmark"])
        policy = json.loads(fold["eval_policy_json"])
        eval_start = as_utc_day(pd.Timestamp(fold["eval_start_date"]))
        eval_end = as_utc_day(pd.Timestamp(fold["eval_end_date"]))
        eval_df = df[(theta >= eval_start) & (theta <= eval_end)].copy()
        trades, equity, stats, _, _, _ = sim_opp_cost(
            eval_df, prices, probs, policy,
            bench_sym=bench, initial=INITIAL_CAPITAL, use_kelly=True,
            start_date=eval_start, end_date=eval_end,
            allocation_mode="event_priority",
        )
        assert abs(float(stats["total_return"]) - float(fold["eval_return_pct"])) < 0.02, (
            fold["fold"], bench, stats["total_return"], fold["eval_return_pct"])
        rows.append({
            "experiment": "T1+T2+T3+T4",
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
    new = pd.DataFrame(rows).sort_values(["benchmark", "fold"])

    existing = pd.read_csv(OUT / "fold_level_results.csv")
    existing = existing[existing["experiment"] != "T1+T2+T3+T4"]
    combined = pd.concat([existing, new], ignore_index=True)
    combined.to_csv(OUT / "fold_level_results.csv", index=False)

    show = [c for c in new.columns if c != "policy_json"]
    print(new[show].to_string(index=False))

    t2 = existing[existing["experiment"] == "T2 only"]
    merged = t2.merge(new, on=["benchmark", "fold"], suffixes=("_t2", "_all"))
    print("\nFold excess comparison (T2-only vs T1+T2+T3+T4):")
    print(merged[["benchmark", "fold", "eval_start_t2",
                  "excess_return_pct_t2", "excess_return_pct_all"]].to_string(index=False))
    corr = merged["excess_return_pct_t2"].corr(merged["excess_return_pct_all"])
    print(f"\ncorrelation of fold excess across arms: {corr:.3f}")


if __name__ == "__main__":
    main()
