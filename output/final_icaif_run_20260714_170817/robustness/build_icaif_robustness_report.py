"""Full-test robustness reporting for the final ICAIF run.

Replaces the legacy analysis/build_robustness_reports.py reporting layer, which
split 2026 into a Jan-Mar validation window and an Apr-Jun final window and
ranked budgets. Here the complete January-June 2026 test period is aggregated
as ONE window, no budget is selected or ranked, and every official metric is
recomputed from the equity logs as a verification step.

Inputs:  runs/icaif_grid_{iters}x{pop}_seed{seed}   (T1+T2+T3+T4, SPY+QQQ)
         runs/icaif_base_6x20_seed{seed}            (Baseline,     SPY+QQQ)
Outputs (this directory):
         icaif_robustness_run_level.csv
         icaif_robustness_budget_summary.csv
         icaif_paired_baseline_vs_all_6x20.csv
         icaif_paired_summary.csv
         icaif_robustness_report.md
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\Users\Liran\PycharmProjects\cem_clean_repo")
RUNS = ROOT / "runs"
OUT = Path(__file__).resolve().parent

SEEDS = list(range(42, 52))
BUDGETS = [(6, 20), (6, 30), (10, 20), (10, 30)]
TOL = dict(ret=0.02, sharpe=0.02, dd=0.02)  # recomputation tolerance, pct points


INITIAL_CAPITAL = 100_000.0


def recompute_from_equity(equity_path: Path) -> dict:
    # Official total_return divides terminal equity by the $100k initial capital
    # (pre-initial-cost); the equity log's first row is post-cost.
    eq = pd.read_csv(equity_path, parse_dates=["date"]).sort_values("date")
    equity = eq["equity"].astype(float).to_numpy()
    bench = eq["benchmark_equity"].astype(float).to_numpy()
    ret = (equity[-1] / INITIAL_CAPITAL - 1.0) * 100.0
    bench_ret = (bench[-1] / INITIAL_CAPITAL - 1.0) * 100.0
    daily = pd.Series(equity).pct_change().dropna()
    sharpe = float(daily.mean() / daily.std(ddof=1) * math.sqrt(252.0)) if daily.std(ddof=1) > 1e-12 else 0.0
    peaks = np.maximum.accumulate(equity)
    max_dd = float(np.min(np.where(peaks > 0, equity / peaks - 1.0, 0.0)) * 100.0)
    return {
        "recomputed_return_pct": ret,
        "recomputed_benchmark_return_pct": bench_ret,
        "recomputed_excess_pct": ret - bench_ret,
        "recomputed_sharpe": sharpe,
        "recomputed_max_dd_pct": max_dd,
        "equity_start": str(eq["date"].iloc[0].date()),
        "equity_end": str(eq["date"].iloc[-1].date()),
        "n_equity_days": len(eq),
    }


def load_cell(run_id: str, experiment_label: str, slug: str) -> list[dict]:
    root = RUNS / run_id
    result_path = root / "experiment_results_clean.csv"
    if not result_path.exists():
        return []
    result = pd.read_csv(result_path)
    result = result[result["experiment"] == experiment_label]
    rows = []
    for _, r in result.iterrows():
        bench = str(r["benchmark"])
        equity_path = root / "experiment_equity_logs_clean" / f"{bench.lower()}_{slug}_test.csv"
        rec = recompute_from_equity(equity_path) if equity_path.exists() else {}
        official = {
            "run_id": run_id,
            "experiment": experiment_label,
            "benchmark": bench,
            "seed": int(r["base_seed"]),
            "cem_iters": int(r["cem_iters"]),
            "cem_pop": int(r["cem_pop"]),
            "budget": f"{int(r['cem_iters'])}x{int(r['cem_pop'])}",
            "test_return_pct": float(r["test_return_pct"]),
            "test_benchmark_return_pct": float(r["test_benchmark_return_pct"]),
            "test_excess_return_pct": float(r["test_excess_return_pct"]),
            "test_sharpe": float(r["test_sharpe"]),
            "test_max_dd_pct": float(r["test_max_dd_pct"]),
            "test_trades": int(r["test_trades"]),
            "test_trade_txn_cost": float(r["test_trade_txn_cost"]),
            "test_total_txn_cost": float(r["test_total_txn_cost"]),
            "test_win_rate_pct": float(r["test_win_rate_pct"]),
            "train_return_pct": float(r["train_return_pct"]),
            "train_excess_return_pct": float(r["train_excess_return_pct"]),
            "train_sharpe": float(r["train_sharpe"]),
            "train_max_dd_pct": float(r["train_max_dd_pct"]),
            "train_trades": int(r["train_trades"]),
            "cem_objective": float(r["cem_objective"]),
            "test_start_date": str(r["test_start_date"]),
            "test_end_date": str(r["test_end_date"]),
        }
        official.update(rec)
        if rec:
            official["recompute_return_match"] = abs(official["test_return_pct"] - rec["recomputed_return_pct"]) <= TOL["ret"]
            official["recompute_sharpe_match"] = abs(official["test_sharpe"] - rec["recomputed_sharpe"]) <= TOL["sharpe"]
            official["recompute_dd_match"] = abs(official["test_max_dd_pct"] - rec["recomputed_max_dd_pct"]) <= TOL["dd"]
        rows.append(official)
    return rows


def q(s: pd.Series, p: float) -> float:
    return float(s.quantile(p)) if not s.empty else np.nan


def main() -> None:
    rows: list[dict] = []
    for iters, pop in BUDGETS:
        for seed in SEEDS:
            rows.extend(load_cell(f"icaif_grid_{iters}x{pop}_seed{seed}", "T1+T2+T3+T4", "t1_t2_t3_t4"))
    for seed in SEEDS:
        rows.extend(load_cell(f"icaif_base_6x20_seed{seed}", "Baseline", "baseline"))

    run_level = pd.DataFrame(rows)
    run_level.to_csv(OUT / "icaif_robustness_run_level.csv", index=False)

    flagship = run_level[run_level["experiment"] == "T1+T2+T3+T4"]
    summary_rows = []
    for (budget, bench), g in flagship.groupby(["budget", "benchmark"]):
        excess = g["test_excess_return_pct"]
        summary_rows.append({
            "budget": budget,
            "benchmark": bench,
            "n_seeds": int(g["seed"].nunique()),
            "median_test_excess_pct": excess.median(),
            "q25_test_excess_pct": q(excess, 0.25),
            "q75_test_excess_pct": q(excess, 0.75),
            "min_test_excess_pct": excess.min(),
            "max_test_excess_pct": excess.max(),
            "positive_excess_seed_count": int((excess > 0).sum()),
            "median_test_return_pct": g["test_return_pct"].median(),
            "median_test_sharpe": g["test_sharpe"].median(),
            "median_test_max_dd_pct": g["test_max_dd_pct"].median(),
            "median_test_trades": g["test_trades"].median(),
            "median_test_trade_txn_cost": g["test_trade_txn_cost"].median(),
        })
    budget_summary = pd.DataFrame(summary_rows).sort_values(["budget", "benchmark"])
    budget_summary.to_csv(OUT / "icaif_robustness_budget_summary.csv", index=False)

    # Paired Baseline vs T1+T2+T3+T4 at the standard 6x20 budget.
    base = run_level[(run_level["experiment"] == "Baseline") & (run_level["budget"] == "6x20")]
    flag = flagship[flagship["budget"] == "6x20"]
    paired = flag.merge(
        base,
        on=["seed", "benchmark"],
        suffixes=("_all", "_base"),
        validate="one_to_one",
    )
    paired["delta_excess_pct"] = paired["test_excess_return_pct_all"] - paired["test_excess_return_pct_base"]
    paired["delta_return_pct"] = paired["test_return_pct_all"] - paired["test_return_pct_base"]
    paired["delta_sharpe"] = paired["test_sharpe_all"] - paired["test_sharpe_base"]
    paired["delta_max_dd_pct"] = paired["test_max_dd_pct_all"] - paired["test_max_dd_pct_base"]
    paired["delta_trade_txn_cost"] = paired["test_trade_txn_cost_all"] - paired["test_trade_txn_cost_base"]
    paired["all_beats_baseline"] = paired["delta_excess_pct"] > 0
    keep = ["seed", "benchmark",
            "test_excess_return_pct_all", "test_excess_return_pct_base", "delta_excess_pct",
            "test_return_pct_all", "test_return_pct_base", "delta_return_pct",
            "test_sharpe_all", "test_sharpe_base", "delta_sharpe",
            "test_max_dd_pct_all", "test_max_dd_pct_base", "delta_max_dd_pct",
            "test_trade_txn_cost_all", "test_trade_txn_cost_base", "delta_trade_txn_cost",
            "all_beats_baseline"]
    paired[keep].to_csv(OUT / "icaif_paired_baseline_vs_all_6x20.csv", index=False)

    paired_summary_rows = []
    for bench, g in paired.groupby("benchmark"):
        paired_summary_rows.append({
            "benchmark": bench,
            "n_pairs": len(g),
            "median_delta_excess_pct": g["delta_excess_pct"].median(),
            "q25_delta_excess_pct": q(g["delta_excess_pct"], 0.25),
            "q75_delta_excess_pct": q(g["delta_excess_pct"], 0.75),
            "min_delta_excess_pct": g["delta_excess_pct"].min(),
            "max_delta_excess_pct": g["delta_excess_pct"].max(),
            "median_delta_sharpe": g["delta_sharpe"].median(),
            "median_delta_max_dd_pct": g["delta_max_dd_pct"].median(),
            "median_delta_trade_txn_cost": g["delta_trade_txn_cost"].median(),
            "pct_seeds_all_beats_baseline": float(g["all_beats_baseline"].mean() * 100.0),
        })
    both = {
        "benchmark": "BOTH",
        "n_pairs": len(paired),
        "median_delta_excess_pct": paired["delta_excess_pct"].median(),
        "q25_delta_excess_pct": q(paired["delta_excess_pct"], 0.25),
        "q75_delta_excess_pct": q(paired["delta_excess_pct"], 0.75),
        "min_delta_excess_pct": paired["delta_excess_pct"].min(),
        "max_delta_excess_pct": paired["delta_excess_pct"].max(),
        "median_delta_sharpe": paired["delta_sharpe"].median(),
        "median_delta_max_dd_pct": paired["delta_max_dd_pct"].median(),
        "median_delta_trade_txn_cost": paired["delta_trade_txn_cost"].median(),
        "pct_seeds_all_beats_baseline": float(paired["all_beats_baseline"].mean() * 100.0),
    }
    paired_summary = pd.DataFrame(paired_summary_rows + [both])
    paired_summary.to_csv(OUT / "icaif_paired_summary.csv", index=False)

    mismatches = run_level[
        ~(run_level.get("recompute_return_match", True)
          & run_level.get("recompute_sharpe_match", True)
          & run_level.get("recompute_dd_match", True))
    ] if "recompute_return_match" in run_level.columns else pd.DataFrame()

    def md(df: pd.DataFrame, digits: int = 2) -> str:
        x = df.copy()
        for c in x.columns:
            if pd.api.types.is_numeric_dtype(x[c]) and not pd.api.types.is_bool_dtype(x[c]):
                x[c] = x[c].map(lambda v: "" if pd.isna(v) else f"{v:,.{digits}f}")
        cols = list(map(str, x.columns))
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
        for row in x.astype(str).to_numpy().tolist():
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)

    report = f"""# ICAIF Robustness Report — full January–June 2026 test period

Flagship `T1+T2+T3+T4`, seeds 42–51, budgets 6x20 / 6x30 / 10x20 / 10x30, SPY and QQQ,
audit-clean candidate universe (`data/candidates_audit_clean.parquet`). All statistics
aggregate the complete test period (2026-01-02 through 2026-06-12 portfolio dates).
No validation/final split is used and no budget is selected or ranked by test performance.

Completed flagship cells: {flagship[['budget','seed']].drop_duplicates().shape[0]} / 40.
Completed Baseline 6x20 cells: {base['seed'].nunique()} / 10.

Verification: official test return / Sharpe / MaxDD were recomputed from the equity logs
for every run-benchmark row; {0 if mismatches.empty else len(mismatches)} rows disagree beyond tolerance
(|Δ| > {TOL['ret']} return pts / {TOL['sharpe']} Sharpe / {TOL['dd']} DD pts).

## Budget × benchmark summary (10 seeds each)

{md(budget_summary)}

## Paired Baseline vs T1+T2+T3+T4 at 6x20 (same seed, same benchmark)

{md(paired_summary)}

## Per-seed paired table

{md(paired[keep].sort_values(['benchmark','seed']))}
"""
    (OUT / "icaif_robustness_report.md").write_text(report, encoding="utf-8")
    print(report)
    if not mismatches.empty:
        print("RECOMPUTATION MISMATCHES:")
        print(mismatches[["run_id", "benchmark", "test_return_pct", "recomputed_return_pct",
                          "test_sharpe", "recomputed_sharpe", "test_max_dd_pct", "recomputed_max_dd_pct"]])


if __name__ == "__main__":
    main()
