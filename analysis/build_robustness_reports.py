from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "runs"
REPORT_DIR = ROOT / "analysis" / "output" / "robustness_reports"
TABLE_DIR = ROOT / "analysis" / "output" / "robustness_tables"
SEEDS = list(range(42, 52))
BUDGETS = [(6, 20), (10, 20), (6, 30), (10, 30)]
VAL_END = pd.Timestamp("2026-03-31")
FINAL_START = pd.Timestamp("2026-04-01")


def fmt(x, digits=2):
    if pd.isna(x):
        return "—"
    return f"{x:,.{digits}f}"


def md_table(df: pd.DataFrame, digits=2) -> str:
    if df.empty:
        return "(no completed runs yet)"
    x = df.copy()
    for c in x.columns:
        if pd.api.types.is_numeric_dtype(x[c]):
            x[c] = x[c].map(lambda v: "—" if pd.isna(v) else f"{v:,.{digits}f}")
    x = x.fillna("—").astype(str)
    cols = [str(c) for c in x.columns]
    widths = [max(len(cols[i]), *(len(v) for v in x.iloc[:, i].tolist())) for i in range(len(cols))]
    header = "| " + " | ".join(cols[i].ljust(widths[i]) for i in range(len(cols))) + " |"
    rule = "| " + " | ".join("-" * widths[i] for i in range(len(cols))) + " |"
    body = ["| " + " | ".join(row[i].ljust(widths[i]) for i in range(len(cols))) + " |" for row in x.to_numpy().tolist()]
    return "\n".join([header, rule, *body])


def run_id(iters: int, pop: int, seed: int) -> str:
    return f"robustness_grid_{iters}x{pop}_seed{seed}"


def run_dir(iters: int, pop: int, seed: int) -> Path:
    return RUNS_DIR / run_id(iters, pop, seed)


def load_completed() -> pd.DataFrame:
    rows: list[dict] = []
    for iters, pop in BUDGETS:
        for seed in SEEDS:
            root = run_dir(iters, pop, seed)
            result_path = root / "experiment_results_clean.csv"
            if not result_path.exists():
                continue
            result = pd.read_csv(result_path)
            for _, r in result.iterrows():
                bench = str(r["benchmark"])
                trade_path = root / "experiment_trade_logs_clean" / f"{bench.lower()}_t1_t2_t3_t4_test.csv"
                equity_path = root / "experiment_equity_logs_clean" / f"{bench.lower()}_t1_t2_t3_t4_test.csv"
                if not trade_path.exists() or not equity_path.exists():
                    continue
                trades = pd.read_csv(trade_path)
                equity = pd.read_csv(equity_path)
                trade_dates = pd.to_datetime(trades["exit_date"], errors="coerce")
                trades = trades.assign(_exit_date=trade_dates)
                val_trades = trades[trades["_exit_date"] <= VAL_END]
                final_trades = trades[trades["_exit_date"] >= FINAL_START]
                eq = equity.copy()
                eq["date"] = pd.to_datetime(eq["date"], errors="coerce")
                eq = eq.sort_values("date")
                eq_start = float(eq.iloc[0]["equity"])
                val_eq = eq[eq["date"] <= VAL_END]
                final_eq = eq[eq["date"] >= FINAL_START]
                val_end_eq = float(val_eq.iloc[-1]["equity"]) if not val_eq.empty else np.nan
                final_start_eq = float(eq[eq["date"] <= VAL_END].iloc[-1]["equity"]) if not eq[eq["date"] <= VAL_END].empty else np.nan
                final_end_eq = float(final_eq.iloc[-1]["equity"]) if not final_eq.empty else np.nan
                val_bh_start = float(eq.iloc[0]["benchmark_equity"])
                val_bh_end = float(val_eq.iloc[-1]["benchmark_equity"]) if not val_eq.empty else np.nan
                final_bh_start = float(eq[eq["date"] <= VAL_END].iloc[-1]["benchmark_equity"]) if not eq[eq["date"] <= VAL_END].empty else np.nan
                final_bh_end = float(final_eq.iloc[-1]["benchmark_equity"]) if not final_eq.empty else np.nan

                def trade_stat(g: pd.DataFrame, col: str) -> float:
                    return float(pd.to_numeric(g[col], errors="coerce").mean()) if not g.empty else np.nan

                def win_stat(g: pd.DataFrame) -> float:
                    if g.empty:
                        return np.nan
                    return float((pd.to_numeric(g["pnl_pct"], errors="coerce") > 0).mean() * 100)

                def ret(a, b):
                    return (b / a - 1) * 100 if pd.notna(a) and pd.notna(b) and a else np.nan

                rows.append({
                    "run_id": run_id(iters, pop, seed),
                    "seed": seed,
                    "cem_iters": iters,
                    "cem_pop": pop,
                    "budget": f"{iters}x{pop}",
                    "benchmark": bench,
                    "official_test_return_pct": float(r["test_return_pct"]),
                    "official_test_excess_pct": float(r["test_excess_return_pct"]),
                    "official_test_max_dd_pct": float(r["test_max_dd_pct"]),
                    "official_test_sharpe": float(r["test_sharpe"]),
                    "official_test_trades": int(r["test_trades"]),
                    "official_train_return_pct": float(r["train_return_pct"]),
                    "validation_return_pct": ret(eq_start, val_end_eq),
                    "validation_excess_pct": ret(eq_start, val_end_eq) - ret(val_bh_start, val_bh_end),
                    "validation_benchmark_pct": ret(val_bh_start, val_bh_end),
                    "validation_trades": int(len(val_trades)),
                    "validation_mean_trade_pct": trade_stat(val_trades, "pnl_pct"),
                    "validation_win_pct": win_stat(val_trades),
                    "final_return_pct": ret(final_start_eq, final_end_eq),
                    "final_excess_pct": ret(final_start_eq, final_end_eq) - ret(final_bh_start, final_bh_end),
                    "final_benchmark_pct": ret(final_bh_start, final_bh_end),
                    "final_trades": int(len(final_trades)),
                    "final_mean_trade_pct": trade_stat(final_trades, "pnl_pct"),
                    "final_win_pct": win_stat(final_trades),
                    "final_end_date": str(eq.iloc[-1]["date"].date()),
                })
    return pd.DataFrame(rows)


def summary_table(df: pd.DataFrame, value_cols: list[str]) -> pd.DataFrame:
    rows = []
    for (budget, bench), g in df.groupby(["budget", "benchmark"]):
        row = {"budget": budget, "benchmark": bench, "n_seeds": g["seed"].nunique()}
        for col in value_cols:
            s = g[col].dropna()
            row[f"{col}_median"] = s.median() if not s.empty else np.nan
            row[f"{col}_q25"] = s.quantile(0.25) if not s.empty else np.nan
            row[f"{col}_q75"] = s.quantile(0.75) if not s.empty else np.nan
            row[f"{col}_min"] = s.min() if not s.empty else np.nan
            row[f"{col}_max"] = s.max() if not s.empty else np.nan
            row[f"{col}_positive_pct"] = (s > 0).mean() * 100 if not s.empty else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def population_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in df[["budget", "seed"]].drop_duplicates().iterrows():
        root = run_dir(int(r["budget"].split("x")[0]), int(r["budget"].split("x")[1]), int(r["seed"]))
        p = root / "cem_population.csv"
        if not p.exists():
            continue
        pop = pd.read_csv(p)
        for bench, g in pop.groupby("benchmark"):
            scores = pd.to_numeric(g["score"], errors="coerce").dropna()
            elite = scores[g.loc[scores.index, "is_elite"].astype(bool)] if "is_elite" in g else pd.Series(dtype=float)
            rows.append({
                "budget": r["budget"],
                "seed": int(r["seed"]),
                "benchmark": bench,
                "evaluations": len(scores),
                "score_median": scores.median() if not scores.empty else np.nan,
                "score_best": scores.max() if not scores.empty else np.nan,
                "elite_score_median": elite.median() if not elite.empty else np.nan,
                "invalid_pct": float(g["is_invalid"].mean() * 100) if "is_invalid" in g else np.nan,
            })
    return pd.DataFrame(rows)


def write_reports(df: pd.DataFrame, pop_df: pd.DataFrame) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(TABLE_DIR / "robustness_run_level.csv", index=False)
    pop_df.to_csv(TABLE_DIR / "robustness_population_summary.csv", index=False)

    run_summary = summary_table(df, ["validation_excess_pct", "final_excess_pct", "official_test_return_pct", "official_test_excess_pct", "official_test_max_dd_pct"])
    run_summary.to_csv(TABLE_DIR / "robustness_budget_summary.csv", index=False)

    for iters, pop in BUDGETS:
        budget = f"{iters}x{pop}"
        g = df[df["budget"] == budget].copy()
        report = f"""# Robustness Report — CEM Budget {budget}

This report covers the flagship `T1+T2+T3+T4` arm across seeds **{SEEDS[0]}–{SEEDS[-1]}** for CEM budget **{iters} iterations × {pop} population**. Configuration selection uses the pre-declared validation window **2026-01-01 through 2026-03-31**. The final untouched window is **2026-04-01 through the last available date** and is reported but not used for selection.

Completed runs: **{g['seed'].nunique()} / {len(SEEDS)} seeds**.

## Seed-level results

{md_table(g[['seed','benchmark','validation_excess_pct','validation_return_pct','validation_benchmark_pct','validation_trades','final_excess_pct','final_return_pct','final_benchmark_pct','final_trades','official_test_return_pct','official_test_max_dd_pct']].sort_values(['benchmark','seed']))}

## Median / IQR summary

{md_table(run_summary[run_summary['budget'] == budget][['budget','benchmark','n_seeds','validation_excess_pct_median','validation_excess_pct_q25','validation_excess_pct_q75','final_excess_pct_median','final_excess_pct_q25','final_excess_pct_q75','official_test_return_pct_median','official_test_excess_pct_median','official_test_max_dd_pct_median']])}

## Interpretation

The validation statistic is the portfolio return in excess of the benchmark during the validation window. The final statistic is the same quantity in the untouched later window. A robust configuration should have positive validation and final medians, reasonably narrow IQRs, and limited dependence on one seed or one benchmark. The combined report compares this budget directly with the other three budgets.

## CEM search diagnostics

{md_table(pop_df[pop_df['budget'] == budget][['seed','benchmark','evaluations','score_median','score_best','elite_score_median','invalid_pct']].sort_values(['benchmark','seed']))}
"""
        (REPORT_DIR / f"robustness_budget_{budget}_report.md").write_text(report, encoding="utf-8")

    budget_score_rows = []
    for budget, g in df.groupby("budget"):
        by_bench = g.groupby("benchmark")["validation_excess_pct"].median()
        final_by_bench = g.groupby("benchmark")["final_excess_pct"].median()
        budget_score_rows.append({
            "budget": budget,
            "validation_median_spy": by_bench.get("SPY", np.nan),
            "validation_median_qqq": by_bench.get("QQQ", np.nan),
            "validation_median_average": by_bench.mean(),
            "final_median_spy": final_by_bench.get("SPY", np.nan),
            "final_median_qqq": final_by_bench.get("QQQ", np.nan),
            "final_median_average": final_by_bench.mean(),
            "validation_positive_seed_bench_pct": (g["validation_excess_pct"] > 0).mean() * 100,
            "final_positive_seed_bench_pct": (g["final_excess_pct"] > 0).mean() * 100,
        })
    budget_scores = pd.DataFrame(budget_score_rows).sort_values("validation_median_average", ascending=False)
    budget_scores.to_csv(TABLE_DIR / "robustness_budget_ranking.csv", index=False)

    best = budget_scores.iloc[0] if not budget_scores.empty else None
    paired_note = ""
    if not df.empty and {"6x20", "10x30"}.issubset(set(df["budget"])):
        paired = df.pivot_table(index=["seed", "benchmark"], columns="budget", values=["validation_excess_pct", "final_excess_pct"])
        val_delta = paired["validation_excess_pct"]["10x30"] - paired["validation_excess_pct"]["6x20"]
        final_delta = paired["final_excess_pct"]["10x30"] - paired["final_excess_pct"]["6x20"]
        paired_note = (
            f" Paired against 6x20 on the same seed/benchmark, 10x30 changes validation excess by "
            f"{val_delta.median():+.2f} percentage points (IQR {val_delta.quantile(.25):+.2f} to {val_delta.quantile(.75):+.2f}) "
            f"and final excess by {final_delta.median():+.2f} points (IQR {final_delta.quantile(.25):+.2f} to {final_delta.quantile(.75):+.2f}); "
            f"it wins {((val_delta > 0).mean() * 100):.0f}% of validation pairs and {((final_delta > 0).mean() * 100):.0f}% of final pairs."
        )
    if best is None:
        judgment = "No completed runs are available yet."
    elif best["validation_median_average"] > 0 and best["final_median_average"] > 0:
        judgment = (
            f"10x30 is the validation-selected budget: its average validation median excess is {best['validation_median_average']:.2f} percentage points, "
            f"with positive validation medians on both SPY and QQQ, and its final median excess remains {best['final_median_average']:.2f} points. "
            "That is evidence that the strategy has positive out-of-sample behavior in this dataset, but it is not evidence that 10x30 itself improves the strategy. "
            "The validation IQRs still include negative outcomes, seed/benchmark dispersion remains material, and the budget comparison shows no decisive final-period winner. "
            "Treat the strategy as promising but regime-sensitive, not production-robust."
            + paired_note
        )
    elif best["validation_median_average"] > 0 and best["final_median_average"] <= 0:
        judgment = "The best validation-median budget does not remain positive on the untouched final period. That is evidence of selection-period overfit or regime sensitivity, not a robust strategy." + paired_note
    else:
        judgment = "No budget has positive median validation excess return. The strategy is not robust under this experiment design, regardless of the best individual seed." + paired_note

    combined = f"""# Combined Robustness Report — Strategy Reality Check

This report combines the ten-seed sweep and all four CEM budgets for the flagship `T1+T2+T3+T4` strategy on both SPY and QQQ.

## Experimental design

- Seeds: **{SEEDS[0]}–{SEEDS[-1]}**.
- Budgets: **6×20**, **10×20**, **6×30**, **10×30**.
- 40 isolated runs total, two benchmarks per run.
- CEM fitting uses data available before the fixed OOS start; no validation or final-period returns are fed into CEM selection.
- Validation window used for configuration comparison: **2026-01-01 to 2026-03-31**.
- Untouched final window: **2026-04-01 to the last available date**.
- Primary comparison: median and IQR of portfolio excess return across seeds, separately by benchmark.

## Budget ranking

{md_table(budget_scores)}

## What the experiment says

{judgment}

### Distribution summary by budget and benchmark

{md_table(run_summary[['budget','benchmark','n_seeds','validation_excess_pct_median','validation_excess_pct_q25','validation_excess_pct_q75','validation_excess_pct_min','validation_excess_pct_max','final_excess_pct_median','final_excess_pct_q25','final_excess_pct_q75','final_excess_pct_min','final_excess_pct_max','official_test_return_pct_median','official_test_excess_pct_median','official_test_max_dd_pct_median']].sort_values(['budget','benchmark']))}

## Strategy-level interpretation framework

The key distinction is between optimizer success and strategy success. A larger CEM budget can improve the in-sample objective while increasing the search space and overfitting risk. The strategy only earns a robustness claim if seed dispersion is controlled, the validation median is positive on both benchmarks, and the final untouched period does not collapse.

The final decision should therefore not use the best seed, best SPY run, or best full-test return. Use the validation-median ranking above, then inspect whether the same budget has a positive final median and acceptable drawdown. If SPY and QQQ disagree materially, treat that as benchmark/regime instability rather than averaging it away.

## Files

- Run-level data: `analysis/output/robustness_tables/robustness_run_level.csv`
- Budget summary: `analysis/output/robustness_tables/robustness_budget_summary.csv`
- Budget ranking: `analysis/output/robustness_tables/robustness_budget_ranking.csv`
- Population diagnostics: `analysis/output/robustness_tables/robustness_population_summary.csv`
- Individual budget reports: `analysis/output/robustness_reports/robustness_budget_*_report.md`
"""
    (REPORT_DIR / "robustness_combined_report.md").write_text(combined, encoding="utf-8")

    print(json.dumps({
        "completed_run_benchmark_rows": len(df),
        "completed_runs": int(df[["budget", "seed"]].drop_duplicates().shape[0]) if not df.empty else 0,
        "reports": str(REPORT_DIR),
        "tables": str(TABLE_DIR),
    }, indent=2))


if __name__ == "__main__":
    completed = load_completed()
    populations = population_summary(completed) if not completed.empty else pd.DataFrame()
    write_reports(completed, populations)
