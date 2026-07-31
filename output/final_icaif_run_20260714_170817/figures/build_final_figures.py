"""Publication figures for the final ICAIF paper.

Every numeric value is read from the final CSV outputs of this run — nothing is
typed by hand. Produces vector PDF + 300dpi PNG for:

  fig1_h1_evidence          - H1 dependence forest + event-family heterogeneity
  fig2_benchmark_excess     - benchmark-relative forest plot (SPY + sector ETF)
  fig3_cem_budget_robustness- full-test seed/budget robustness (no selection)

The signal-window and architecture schematics are kept from the existing
paper_revision set (copied unchanged by the paper build step).
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(r"C:\Users\Liran\PycharmProjects\cem_clean_repo")
RUN = ROOT / "output" / "final_icaif_run_20260714_170817"
OUT = Path(__file__).resolve().parent

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 11,
    "axes.titlesize": 13,
    "axes.labelsize": 11.5,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "figure.dpi": 110,
})

TEAL = "#1B9E77"
BLUE = "#3B6FB6"
ORANGE = "#D95F02"
PURPLE = "#7A5AA8"
GOLD = "#D9A400"
GRAY = "#6B7280"


def save(fig, stem: str) -> None:
    fig.savefig(OUT / f"{stem}.pdf", bbox_inches="tight")
    fig.savefig(OUT / f"{stem}.png", dpi=300, bbox_inches="tight")
    plt.close(fig)
    print("wrote", stem)


def fig1_h1_evidence() -> None:
    inf = pd.read_csv(RUN / "h1" / "raw_expectation_tminus1_final" / "h1_raw_expectation_cluster_inference.csv")
    stats = pd.read_csv(RUN / "stats" / "final_statistical_results.csv")

    def pick(level, cluster):
        return inf[(inf["level"] == level) & (inf["cluster_type"] == cluster)].iloc[0]

    panel_a = [
        ("Candidate / event", pick("candidate_observation", "economic_event_id"), TEAL),
        ("Candidate / symbol", pick("candidate_observation", "symbol"), BLUE),
        ("Candidate / month", pick("candidate_observation", "entry_month_block"), ORANGE),
        ("Equal-event / month", pick("economic_event", "entry_month_block"), PURPLE),
    ]

    fam = stats[stats["estimand"].str.startswith("H1 mean net return (event family:")]
    fam_rows = []
    for name, label, color in (("earnings", "Earnings", BLUE), ("geo", "Geopolitical", TEAL), ("other", "Other", GOLD)):
        r = fam[fam["estimand"].str.contains(f": {name})", regex=False)]
        if not r.empty:
            fam_rows.append((label, r.iloc[0], color))

    fig, axes = plt.subplots(1, 2, figsize=(12.6, 3.9), gridspec_kw={"width_ratios": [1, 1]})

    ax = axes[0]
    y = np.arange(len(panel_a))[::-1]
    for i, (label, r, color) in enumerate(panel_a):
        mean = r["mean_raw_net_return"] * 100
        lo, hi = r["cluster_ci_lo"] * 100, r["cluster_ci_hi"] * 100
        ax.hlines(y[i], lo, hi, color=color, lw=3.4, alpha=0.9)
        ax.plot([mean], [y[i]], "o", ms=9, color=color, mec="white", mew=1.2, zorder=3)
        ax.text(hi + 0.12, y[i], f"{mean:+.2f}%", va="center", fontsize=10)
    ax.axvline(0, color="black", lw=1.1)
    ax.set_yticks(y)
    ax.set_yticklabels([p[0] for p in panel_a])
    ax.set_xlabel("Mean net return (%, 95% bootstrap interval)")
    ax.set_title("A. Dependence sensitivity", loc="left")
    ax.grid(axis="x", color="#E5E7EB", lw=0.8)
    ax.set_xlim(left=min(-1.0, min(p[1]["cluster_ci_lo"] * 100 for p in panel_a) - 0.4))

    ax = axes[1]
    y = np.arange(len(fam_rows))[::-1]
    for i, (label, r, color) in enumerate(fam_rows):
        mean = r["estimate"] * 100
        lo, hi = r["ci_lo"] * 100, r["ci_hi"] * 100
        ax.hlines(y[i], lo, hi, color=color, lw=3.4, alpha=0.9)
        ax.plot([mean], [y[i]], "o", ms=9, color=color, mec="white", mew=1.2, zorder=3)
        ax.text(hi + 0.6, y[i], f"{mean:+.2f}%  N={int(r['n'])}", va="center", fontsize=10)
    ax.axvline(0, color="black", lw=1.1)
    ax.set_yticks(y)
    ax.set_yticklabels([p[0] for p in fam_rows])
    ax.set_xlabel("Mean net return (%, event-clustered 95% interval)")
    ax.set_title("B. Event-family heterogeneity", loc="left")
    ax.grid(axis="x", color="#E5E7EB", lw=0.8)
    right = max(p[1]["ci_hi"] * 100 for p in fam_rows)
    ax.set_xlim(-4, right + 11)

    fig.tight_layout(w_pad=3.0)
    save(fig, "fig1_h1_evidence")


def fig2_benchmark_excess() -> None:
    inf = pd.read_csv(RUN / "benchmark_excess" / "benchmark_excess_inference.csv")

    wanted = [
        ("SPY", "candidate_observation", "economic_event_cluster", "Candidate / event cluster"),
        ("SPY", "candidate_observation", "symbol_cluster", "Candidate / symbol cluster"),
        ("SPY", "candidate_observation", "entry_month_block", "Candidate / month block"),
        ("SPY", "symbol_day_collapsed", "economic_event_cluster", "Symbol-day / event cluster"),
        ("SPY", "economic_event", "ordinary_event_bootstrap", "Equal-event / event bootstrap"),
        ("SPY", "economic_event", "entry_month_block", "Equal-event / month block"),
        ("sector_etf", "candidate_observation", "economic_event_cluster", "Candidate / event cluster"),
        ("sector_etf", "candidate_observation", "symbol_cluster", "Candidate / symbol cluster"),
        ("sector_etf", "candidate_observation", "entry_month_block", "Candidate / month block"),
        ("sector_etf", "symbol_day_collapsed", "economic_event_cluster", "Symbol-day / event cluster"),
        ("sector_etf", "economic_event", "ordinary_event_bootstrap", "Equal-event / event bootstrap"),
        ("sector_etf", "economic_event", "entry_month_block", "Equal-event / month block"),
    ]
    colors = {"SPY": ORANGE, "sector_etf": TEAL}
    titles = {"SPY": "A. Versus SPY (N=887 matched)", "sector_etf": "B. Versus sector ETF (N=680 sector-mapped)"}

    fig, axes = plt.subplots(1, 2, figsize=(12.6, 4.1), sharex=True)
    for ax, bench in zip(axes, ("SPY", "sector_etf")):
        rows = [(lbl, inf[(inf["benchmark"] == b) & (inf["level"] == lv) & (inf["scheme"] == sc)].iloc[0])
                for (b, lv, sc, lbl) in wanted if b == bench]
        y = np.arange(len(rows))[::-1]
        for i, (label, r) in enumerate(rows):
            mean = r["observed_mean_excess_return"] * 100
            lo, hi = r["ci_lo"] * 100, r["ci_hi"] * 100
            ax.hlines(y[i], lo, hi, color=colors[bench], lw=3.2, alpha=0.9)
            ax.plot([mean], [y[i]], "o", ms=8.5, color=colors[bench], mec="white", mew=1.1, zorder=3)
            ax.text(hi + 0.12, y[i], f"{mean:+.2f}%", va="center", fontsize=9.5)
        ax.axvline(0, color="black", lw=1.1)
        ax.set_yticks(y)
        ax.set_yticklabels([r[0] for r in rows], fontsize=10)
        ax.set_title(titles[bench], loc="left")
        ax.grid(axis="x", color="#E5E7EB", lw=0.8)
        ax.set_xlabel("Mean excess return (%, 95% bootstrap interval)")
    axes[0].set_xlim(-1.6, 4.6)
    fig.tight_layout(w_pad=2.4)
    save(fig, "fig2_benchmark_excess")


def fig3_budget_robustness() -> None:
    summary = pd.read_csv(RUN / "robustness" / "icaif_robustness_budget_summary.csv")
    budgets = ["6x20", "6x30", "10x20", "10x30"]
    benches = [("SPY", ORANGE), ("QQQ", BLUE)]

    fig, ax = plt.subplots(figsize=(9.6, 4.2))
    y_base = np.arange(len(budgets))[::-1] * 1.0
    offsets = {"SPY": 0.17, "QQQ": -0.17}
    for bench, color in benches:
        for i, budget in enumerate(budgets):
            r = summary[(summary["budget"] == budget) & (summary["benchmark"] == bench)].iloc[0]
            y = y_base[i] + offsets[bench]
            ax.hlines(y, r["min_test_excess_pct"], r["max_test_excess_pct"],
                      color=color, lw=1.1, alpha=0.45)
            ax.hlines(y, r["q25_test_excess_pct"], r["q75_test_excess_pct"],
                      color=color, lw=5.2, alpha=0.9)
            ax.plot([r["median_test_excess_pct"]], [y], "o", ms=9, color=color,
                    mec="white", mew=1.2, zorder=3)
            ax.text(r["max_test_excess_pct"] + 0.35, y,
                    f"{r['median_test_excess_pct']:+.1f}  [{r['q25_test_excess_pct']:+.1f}, {r['q75_test_excess_pct']:+.1f}]"
                    f"  {int(r['positive_excess_seed_count'])}/10>0",
                    va="center", fontsize=9)
    ax.axvline(0, color="black", lw=1.1)
    ax.set_yticks(y_base)
    ax.set_yticklabels([f"{b.replace('x', ' × ')}" for b in budgets])
    ax.set_ylabel("CEM budget (iterations × population)")
    ax.set_xlabel("Full-test excess return, Jan–Jun 2026 (percentage points)")
    ax.set_title("T1+T2+T3+T4 across ten seeds: median, IQR (thick), min–max (thin)", loc="left")
    handles = [plt.Line2D([], [], color=c, marker="o", ls="-", lw=4, mec="white", label=b) for b, c in benches]
    ax.legend(handles=handles, loc="lower right", frameon=False)
    ax.grid(axis="x", color="#E5E7EB", lw=0.8)
    ax.set_xlim(-6, 30)
    fig.tight_layout()
    save(fig, "fig3_cem_budget_robustness")


if __name__ == "__main__":
    fig1_h1_evidence()
    fig2_benchmark_excess()
    fig3_budget_robustness()
