"""Generate LaTeX tables for paper_final_cem_contribution.tex from result CSVs."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parent

ladder = pd.read_csv(OUT / "cem_ladder_summary.csv")
decomp = pd.read_csv(OUT / "cem_decomposition_results.csv")
folds = pd.read_csv(OUT / "fold_level_results.csv")

ORDER = [
    ("Fixed default policy (no CEM)", None),
    ("Frozen CEM baseline", "Baseline"),
    ("T2 only (walk-forward)", "T2 TrainWindows"),
    ("T1+T2", "T1+T2"),
    ("T1+T2+T3", "T1+T2+T3"),
    ("T1+T2+T3+T4", "T1+T2+T3+T4"),
]


def simple_row(bench: str) -> dict:
    r = decomp[(decomp["arm"] == "SIMPLE_DEFAULT") & (decomp["benchmark"] == bench)].iloc[0]
    return {
        "excess": f"${r['test_excess_return_pct']:.2f}$", "iqr": "--",
        "pos": "--", "beats": "--",
        "sharpe": f"{r['test_sharpe']:.2f}",
        "dd": f"${r['test_max_dd_pct']:.2f}$",
        "trades": f"{int(r['test_trades'])}",
        "cost": f"{r['test_trade_txn_cost']:,.0f}",
    }


def cem_row(arm: str, bench: str) -> dict:
    r = ladder[(ladder["arm"] == arm) & (ladder["benchmark"] == bench)].iloc[0]
    return {
        "excess": f"${r['median_excess']:.2f}$",
        "iqr": f"$[{r['q25_excess']:.2f},{r['q75_excess']:.2f}]$",
        "pos": f"{int(r['positive_excess_seeds'])}/10",
        "beats": f"{int(r['seeds_beating_simple_reference'])}/10",
        "sharpe": f"{r['median_sharpe']:.2f}",
        "dd": f"${r['median_max_dd']:.2f}$",
        "trades": f"{int(r['median_trades'])}",
        "cost": f"{r['median_trade_txn_cost']:,.0f}",
    }


lines = [
    r"\begin{table*}[t]",
    r"\caption{Main portfolio comparison over the single January--June 2026 test period"
    r" (audit-clean universe, identical costs and mechanics, $6\times20$ CEM budget,"
    r" seeds 42--51 for stochastic arms; the fixed default policy is deterministic)."
    r" Excess is percentage points versus buy-and-hold ($+8.52$ SPY, $+17.59$ QQQ)."
    r" $>0$ counts seeds with positive excess; $>$ref counts seeds beating the fixed"
    r" default policy. Sharpe, MaxDD, trades, and one-way trade costs (\$) are medians.}",
    r"\label{tab:cemmain}",
    r"\centering",
    r"\footnotesize",
    r"\setlength{\tabcolsep}{3.4pt}",
    r"\begin{tabular}{llrrrrrrr}",
    r"\toprule",
    r"Benchmark & Configuration & Excess med.\ [IQR] & $>0$ & $>$ref & Sharpe & MaxDD & Trades & Cost \\",
    r"\midrule",
]
for bench in ("SPY", "QQQ"):
    for i, (label, arm) in enumerate(ORDER):
        r = simple_row(bench) if arm is None else cem_row(arm, bench)
        bcell = bench if i == 0 else ""
        lines.append(
            f"{bcell} & {label} & {r['excess']}\\,{r['iqr']} & {r['pos']} & {r['beats']} & "
            f"{r['sharpe']} & {r['dd']} & {r['trades']} & {r['cost']} \\\\")
    if bench == "SPY":
        lines.append(r"\midrule")
lines += [r"\bottomrule", r"\end{tabular}", r"\end{table*}"]
(OUT / "table_cem_main.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")

flines = [
    r"\begin{table*}[t]",
    r"\caption{Walk-forward folds of the T2-only arm (seed 42). Each fold fits only on"
    r" candidates whose outcomes completed before the fold start and is evaluated on the"
    r" next block; folds 4--5 lie inside the 2026 test period. Returns are percentage"
    r" points over the fold window; costs are dollars.}",
    r"\label{tab:folds}",
    r"\centering",
    r"\footnotesize",
    r"\setlength{\tabcolsep}{3.2pt}",
    r"\begin{tabular}{lrllrrrrrrrr}",
    r"\toprule",
    r"Bench. & Fold & Fit cutoff & Eval window & Fit $n$ & Eval $n$ & Trades & Return & Bench. & Excess & Sharpe & MaxDD \\",
    r"\midrule",
]
for bench in ("SPY", "QQQ"):
    g = folds[folds["benchmark"] == bench].sort_values("fold")
    for i, (_, r) in enumerate(g.iterrows()):
        bcell = bench if i == 0 else ""
        window = f"{r['eval_start']}--{str(r['eval_end'])[5:]}"
        flines.append(
            f"{bcell} & {int(r['fold'])} & {r['fit_label_cutoff']} & {window} & {int(r['fit_candidates'])} & "
            f"{int(r['eval_candidates'])} & {int(r['executed_trades'])} & ${r['strategy_return_pct']:.2f}$ & "
            f"${r['benchmark_return_pct']:.2f}$ & ${r['excess_return_pct']:.2f}$ & {r['sharpe']:.2f} & "
            f"${r['max_dd_pct']:.2f}$ \\\\")
    if bench == "SPY":
        flines.append(r"\midrule")
flines += [r"\bottomrule", r"\end{tabular}", r"\end{table*}"]
(OUT / "table_folds.tex").write_text("\n".join(flines) + "\n", encoding="utf-8")
print("tables written")
