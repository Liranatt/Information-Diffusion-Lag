"""Figure: CEM contribution — decomposition ladder + matched random selection.

Panel A: full-test excess return by arm (median dot, IQR thick bar, min-max
thin bar across seeds 42-51 where stochastic; single deterministic values as
diamonds), SPY and QQQ.
Panel B: matched random-selection distributions (500 draws) vs the observed
CEM-selection arm, per benchmark.

Reads only: cem_decomposition_results.csv, cem_ladder_seed_results.csv,
random_selection_draws.csv, random_selection_results.csv.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent

COL = {"SPY": "#D95F02", "QQQ": "#1B9E77"}

decomp = pd.read_csv(OUT / "cem_decomposition_results.csv")
ladder = pd.read_csv(OUT / "cem_ladder_seed_results.csv")
draws = pd.read_csv(OUT / "random_selection_draws.csv")
rand = pd.read_csv(OUT / "random_selection_results.csv")

ARMS = [
    ("All eligible + fixed exec.", "ALL_ELIGIBLE_SIMPLE_EXEC", "decomp_det"),
    ("Fixed default policy (no CEM)", "SIMPLE_DEFAULT", "decomp_det"),
    ("CEM selection + fixed exec.", "CEM_SEL_SIMPLE_EXEC", "decomp"),
    ("Default selection + CEM exec.", "SIMPLE_SEL_CEM_EXEC", "decomp"),
    ("Standard CEM (full)", "CEM_FULL", "decomp"),
    ("T2 only (walk-forward)", "T2 TrainWindows", "ladder"),
    ("T1+T2+T3+T4", "T1+T2+T3+T4", "ladder"),
]

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.8, 4.4), gridspec_kw={"width_ratios": [1.35, 1]})

y_positions = np.arange(len(ARMS))[::-1]
for offset, bench in ((0.16, "SPY"), (-0.16, "QQQ")):
    for i, (label, key, source) in enumerate(ARMS):
        y = y_positions[i] + offset
        if source == "decomp_det":
            value = decomp[(decomp["arm"] == key) & (decomp["benchmark"] == bench)]["test_excess_return_pct"].iloc[0]
            ax1.scatter([value], [y], marker="D", s=42, color=COL[bench], zorder=5)
            continue
        frame = decomp if source == "decomp" else ladder
        g = frame[(frame["arm"] == key) & (frame["benchmark"] == bench)]["test_excess_return_pct"]
        med, q25, q75 = g.median(), g.quantile(0.25), g.quantile(0.75)
        ax1.hlines(y, g.min(), g.max(), color=COL[bench], lw=1.0, alpha=0.55)
        ax1.hlines(y, q25, q75, color=COL[bench], lw=4.2, alpha=0.9)
        ax1.scatter([med], [y], s=44, color=COL[bench], edgecolor="white", lw=0.8, zorder=5)
ax1.axvline(0, color="black", lw=0.9, ls="--")
ax1.set_yticks(y_positions)
ax1.set_yticklabels([a[0] for a in ARMS], fontsize=8.6)
ax1.set_xlabel("Full-test excess return, Jan–Jun 2026 (pct. points)", fontsize=9)
ax1.set_title("A. Selection / execution decomposition (10 seeds; diamonds = deterministic)", fontsize=9)
ax1.grid(axis="x", color="#E5E7EB", lw=0.7)
handles = [plt.Line2D([], [], color=COL[b], lw=3, label=b) for b in ("SPY", "QQQ")]
ax1.legend(handles=handles, fontsize=8.4, loc="lower right")

for i, bench in enumerate(("SPY", "QQQ")):
    d = draws[draws["benchmark"] == bench]["excess"]
    r = rand[rand["benchmark"] == bench].iloc[0]
    ax2.hist(d, bins=34, alpha=0.5, color=COL[bench], density=True,
             label=f"{bench}: 500 matched random selections")
    ax2.axvline(float(r["observed_excess_pct"]), color=COL[bench], lw=2.0)
    ax2.annotate(
        f"observed {bench}\n({r['excess_percentile_rank']:.0f}th pct.)",
        xy=(float(r["observed_excess_pct"]), 0.055 - 0.020 * i),
        xytext=(float(r["observed_excess_pct"]) + (3.5 if bench == 'SPY' else -14.5), 0.062 - 0.020 * i),
        fontsize=8.2, color=COL[bench],
        arrowprops=dict(arrowstyle="->", color=COL[bench], lw=0.9),
    )
ax2.axvline(0, color="black", lw=0.9, ls="--")
ax2.set_xlabel("Excess return of matched random selections (pct. points)", fontsize=9)
ax2.set_ylabel("density", fontsize=9)
ax2.set_title("B. CEM selection vs matched random selection (fixed execution)", fontsize=9)
ax2.legend(fontsize=8.0, loc="upper left")
for ax in (ax1, ax2):
    ax.tick_params(labelsize=8.2)
    ax.spines[["top", "right"]].set_visible(False)

fig.tight_layout()
for ext in ("pdf", "png"):
    fig.savefig(OUT / f"fig_cem_contribution.{ext}", dpi=250, bbox_inches="tight")
print("written fig_cem_contribution.pdf/png")
