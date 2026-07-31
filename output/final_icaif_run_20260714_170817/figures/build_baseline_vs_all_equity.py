"""Comparison equity curves: frozen Baseline vs T1+T2+T3+T4 (seed 42, 6x20, audit-clean).

Reads the regenerated logs in runs/icaif_base_vs_all_seed42 and draws a 2x2 grid:
rows = training period (2024-08..2025-12) / test period (2026-01..2026-06),
cols = SPY / QQQ. Each panel: Baseline, T1+T2+T3+T4, and buy-and-hold benchmark.
The training paths are labeled in-sample / schedule-replay diagnostics.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(r"C:\Users\Liran\PycharmProjects\cem_clean_repo")
LOGS = ROOT / "runs" / "icaif_base_vs_all_seed42" / "experiment_equity_logs_clean"
OUT = Path(__file__).resolve().parent

COLORS = {"Baseline": "#2563EB", "T1+T2+T3+T4": "#D97706", "bench": "#6B7280"}


def load(bench: str, slug: str, stage: str) -> pd.DataFrame:
    df = pd.read_csv(LOGS / f"{bench.lower()}_{slug}_{stage}.csv", parse_dates=["date"])
    return df.sort_values("date")


fig, axes = plt.subplots(2, 2, figsize=(11.5, 6.4), sharex="row")
for col, bench in enumerate(("SPY", "QQQ")):
    for row, stage in enumerate(("train", "test")):
        ax = axes[row][col]
        base = load(bench, "baseline", stage)
        allt = load(bench, "t1_t2_t3_t4", stage)
        ax.plot(base["date"], base["equity"] / 1000, color=COLORS["Baseline"], lw=1.6,
                label="Baseline (frozen batch fit)")
        ax.plot(allt["date"], allt["equity"] / 1000, color=COLORS["T1+T2+T3+T4"], lw=1.6,
                label="T1+T2+T3+T4 (walk-forward)")
        ax.plot(base["date"], base["benchmark_equity"] / 1000, color=COLORS["bench"], lw=1.2,
                ls="--", label=f"{bench} buy & hold")
        stage_label = ("TRAIN 2024-08 → 2025-12 (in-sample for Baseline;"
                       " schedule replay for T1+T2+T3+T4)") if stage == "train" else \
                      "TEST Jan–Jun 2026 (out-of-sample for both)"
        ax.set_title(f"{bench} — {stage_label}", fontsize=9)
        ax.grid(True, ls="--", alpha=0.4)
        ax.tick_params(labelsize=8)
        if col == 0:
            ax.set_ylabel("equity ($k)", fontsize=9)
        # annotate terminal values
        for df_, key in ((base, "Baseline"), (allt, "T1+T2+T3+T4")):
            ax.annotate(f"{df_['equity'].iloc[-1] / 1000:,.0f}k",
                        (df_["date"].iloc[-1], df_["equity"].iloc[-1] / 1000),
                        textcoords="offset points", xytext=(2, -2), fontsize=7.5,
                        color=COLORS[key])
axes[0][0].legend(fontsize=8, loc="upper left")
fig.suptitle("Frozen Baseline vs T1+T2+T3+T4 — seed 42, 6×20, audit-clean universe", fontsize=11)
fig.tight_layout(rect=(0, 0, 1, 0.96))
for ext in ("png", "pdf"):
    fig.savefig(OUT / f"fig_baseline_vs_all_equity.{ext}", dpi=200, bbox_inches="tight")
print("written:", OUT / "fig_baseline_vs_all_equity.png")
