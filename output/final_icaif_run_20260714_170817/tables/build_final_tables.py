"""LaTeX tables for the final ICAIF paper, generated from the final CSVs."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(r"C:\Users\Liran\PycharmProjects\cem_clean_repo")
RUN = ROOT / "output" / "final_icaif_run_20260714_170817"
OUT = Path(__file__).resolve().parent


def pct(x, digits=3):
    return f"${x*100:+.{digits}f}\\%$"


def write(name: str, lines: list[str]) -> None:
    (OUT / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", name)


def table_h1() -> None:
    inf = pd.read_csv(RUN / "h1" / "raw_expectation_tminus1_final" / "h1_raw_expectation_cluster_inference.csv")
    rob = pd.read_csv(RUN / "h1" / "raw_expectation_tminus1_final" / "raw_expectation_robustness.csv")

    def pick(level, cluster):
        return inf[(inf["level"] == level) & (inf["cluster_type"] == cluster)].iloc[0]

    rows = []
    for label, r in (
        ("Candidate / economic event", pick("candidate_observation", "economic_event_id")),
        ("Candidate / symbol", pick("candidate_observation", "symbol")),
        ("Candidate / entry month", pick("candidate_observation", "entry_month_block")),
    ):
        rows.append(
            f"{label} & {int(r['n'])} & {pct(r['mean_raw_net_return'])} & {pct(r['median_raw_net_return'])} & "
            f"{r['win_rate']*100:.2f}\\% & $[{r['cluster_ci_lo']*100:+.3f},{r['cluster_ci_hi']*100:+.3f}]\\%$ & {r['cluster_bootstrap_p']:.4f} \\\\"
        )
    sd = rob[rob["version"] == "symbol_day_collapsed"].iloc[0]
    rows.append(
        f"Symbol-day collapsed & {int(sd['n_trades'])} & {pct(sd['mean_net_return'])} & {pct(sd['median_net_return'])} & "
        f"{sd['win_rate_net_return_gt_0']*100:.2f}\\% & -- & {sd['event_cluster_bootstrap_p_value']:.4f} \\\\"
    )
    ev = pick("economic_event", "entry_month_block")
    rows.append(
        f"Equal-weight event / entry month & {int(ev['n'])} & {pct(ev['mean_raw_net_return'])} & {pct(ev['median_raw_net_return'])} & "
        f"{ev['win_rate']*100:.2f}\\% & $[{ev['cluster_ci_lo']*100:+.3f},{ev['cluster_ci_hi']*100:+.3f}]\\%$ & {ev['cluster_bootstrap_p']:.4f} \\\\"
    )
    write("table_h1.tex", [
        "\\begin{table*}[t]",
        "\\caption{Cleaned H1 expectation. All returns are net of modeled costs. Bootstrap $p$-values are null-centered and one-sided under the stated dependence unit; intervals are uncentered percentile intervals (20{,}000 replications).}",
        "\\label{tab:h1}",
        "\\centering",
        "\\small",
        "\\begin{tabular}{lrrrrrr}",
        "\\toprule",
        "Level and dependence unit & $N$ & Mean & Median & Positive & 95\\% bootstrap CI & $p_{boot}$ \\\\",
        "\\midrule",
        *rows,
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table*}",
    ])


def table_benchmark() -> None:
    inf = pd.read_csv(RUN / "benchmark_excess" / "benchmark_excess_inference.csv")
    wanted = [
        ("SPY", "candidate_observation", "economic_event_cluster", "SPY", "Candidate / event cluster"),
        ("SPY", "candidate_observation", "entry_month_block", "SPY", "Candidate / month block"),
        ("SPY", "symbol_day_collapsed", "economic_event_cluster", "SPY", "Symbol-day / event cluster"),
        ("SPY", "economic_event", "ordinary_event_bootstrap", "SPY", "Equal-event / event bootstrap"),
        ("sector_etf", "candidate_observation", "economic_event_cluster", "Sector ETF", "Candidate / event cluster"),
        ("sector_etf", "candidate_observation", "entry_month_block", "Sector ETF", "Candidate / month block"),
        ("sector_etf", "symbol_day_collapsed", "economic_event_cluster", "Sector ETF", "Symbol-day / event cluster"),
        ("sector_etf", "economic_event", "ordinary_event_bootstrap", "Sector ETF", "Equal-event / event bootstrap"),
    ]
    rows = []
    for bench, level, scheme, bench_label, label in wanted:
        r = inf[(inf["benchmark"] == bench) & (inf["level"] == level) & (inf["scheme"] == scheme)].iloc[0]
        rows.append(
            f"{bench_label} & {label} & {int(r['n'])} & {pct(r['observed_mean_excess_return'])} & "
            f"$[{r['ci_lo']*100:+.3f},{r['ci_hi']*100:+.3f}]\\%$ & {r['p_boot_null_centered']:.4f} \\\\"
        )
    write("table_benchmark.tex", [
        "\\begin{table}[t]",
        "\\caption{Benchmark-relative H1 inference. Each stock trade is matched to an equal-notional benchmark trade over the identical entry and exit dates, with costs modeled independently on both legs. No interval excludes zero.}",
        "\\label{tab:benchmark}",
        "\\centering",
        "\\small",
        "\\setlength{\\tabcolsep}{3.5pt}",
        "\\begin{tabular}{llrrrr}",
        "\\toprule",
        "Benchmark & Level / dependence & $N$ & Mean & 95\\% CI & $p$ \\\\",
        "\\midrule",
        *rows,
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table}",
    ])


def table_cem_matrix() -> None:
    matrix = pd.read_csv(RUN / "cem_matrix" / "cem_matrix_final.csv")
    order = ["Baseline", "T1+T2", "T1+T2+T3", "T4 GeoPriority", "T1+T2+T3+T4"]
    rows = []
    for exp in order:
        spy = matrix[(matrix["experiment"] == exp) & (matrix["benchmark"] == "SPY")].iloc[0]
        qqq = matrix[(matrix["experiment"] == exp) & (matrix["benchmark"] == "QQQ")].iloc[0]
        rows.append(
            f"{exp} & {spy['test_return_pct']:.2f} & {spy['test_excess_return_pct']:.2f} & ${spy['test_max_dd_pct']:.2f}$ & {spy['test_sharpe']:.2f} & {int(spy['test_trades'])} & "
            f"{qqq['test_return_pct']:.2f} & {qqq['test_excess_return_pct']:.2f} & ${qqq['test_max_dd_pct']:.2f}$ & {qqq['test_sharpe']:.2f} & {int(qqq['test_trades'])} \\\\"
        )
    write("table_cem_matrix.tex", [
        "\\begin{table*}[t]",
        "\\caption{Audit-cleaned CEM test matrix over the single January--June 2026 test period (seed 42, six CEM iterations, population 20). Returns and excess are percentage points; benchmark buy-and-hold returned $+8.52$ (SPY) and $+17.59$ (QQQ) points.}",
        "\\label{tab:cemmatrix}",
        "\\centering",
        "\\small",
        "\\begin{tabular}{lrrrrrrrrrr}",
        "\\toprule",
        "& \\multicolumn{5}{c}{SPY benchmark} & \\multicolumn{5}{c}{QQQ benchmark} \\\\",
        "\\cmidrule(lr){2-6}\\cmidrule(lr){7-11}",
        "Configuration & Return & Excess & MaxDD & Sharpe & Trades & Return & Excess & MaxDD & Sharpe & Trades \\\\",
        "\\midrule",
        *rows,
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table*}",
    ])


def table_budget() -> None:
    summary = pd.read_csv(RUN / "robustness" / "icaif_robustness_budget_summary.csv")
    rows = []
    for budget in ["6x20", "6x30", "10x20", "10x30"]:
        cells = []
        for bench in ["SPY", "QQQ"]:
            r = summary[(summary["budget"] == budget) & (summary["benchmark"] == bench)].iloc[0]
            cells.append(
                f"${r['median_test_excess_pct']:+.2f}\\,[{r['q25_test_excess_pct']:+.2f},{r['q75_test_excess_pct']:+.2f}]$ & "
                f"{int(r['positive_excess_seed_count'])}/10 & ${r['median_test_max_dd_pct']:.2f}$"
            )
        pretty = budget.replace("x", "$\\times$")
        rows.append(f"{pretty} & " + " & ".join(cells) + " \\\\")
    write("table_budget.tex", [
        "\\begin{table*}[t]",
        "\\caption{Flagship T1+T2+T3+T4 distribution across ten seeds over the full January--June 2026 test period. Entries are median [25th, 75th percentile] excess return in percentage points, the count of seeds with positive excess, and the median maximum drawdown. No budget is selected or ranked by these results.}",
        "\\label{tab:budget}",
        "\\centering",
        "\\small",
        "\\begin{tabular}{lrrrrrr}",
        "\\toprule",
        "& \\multicolumn{3}{c}{SPY} & \\multicolumn{3}{c}{QQQ} \\\\",
        "\\cmidrule(lr){2-4}\\cmidrule(lr){5-7}",
        "Budget & Excess median [IQR] & $>0$ & MaxDD & Excess median [IQR] & $>0$ & MaxDD \\\\",
        "\\midrule",
        *rows,
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table*}",
    ])


def table_paired() -> None:
    paired = pd.read_csv(RUN / "robustness" / "icaif_paired_summary.csv")
    rows = []
    for bench in ["SPY", "QQQ"]:
        r = paired[paired["benchmark"] == bench].iloc[0]
        cost_cell = f"${r['median_delta_trade_txn_cost']:+,.0f}$".replace(",", "{,}")
        rows.append(
            f"{bench} & ${r['median_delta_excess_pct']:+.2f}\\,[{r['q25_delta_excess_pct']:+.2f},{r['q75_delta_excess_pct']:+.2f}]$ & "
            f"${r['median_delta_sharpe']:+.2f}$ & ${r['median_delta_max_dd_pct']:+.2f}$ & "
            + cost_cell + f" & {r['pct_seeds_all_beats_baseline']:.0f}\\% \\\\"
        )
    write("table_paired.tex", [
        "\\begin{table}[t]",
        "\\caption{Paired comparison at the standard $6\\times20$ budget: T1+T2+T3+T4 minus Baseline on the same seed and benchmark (ten seeds each). Entries are median [IQR] differences over the full test period; the last column is the share of seeds where the treated configuration has higher excess return.}",
        "\\label{tab:paired}",
        "\\centering",
        "\\small",
        "\\setlength{\\tabcolsep}{3.5pt}",
        "\\begin{tabular}{lrrrrr}",
        "\\toprule",
        "& $\\Delta$Excess [IQR] & $\\Delta$Sharpe & $\\Delta$MaxDD & $\\Delta$Cost (\\$) & Wins \\\\",
        "\\midrule",
        *rows,
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table}",
    ])


if __name__ == "__main__":
    table_h1()
    table_benchmark()
    table_cem_matrix()
    table_budget()
    table_paired()
