"""Aggregate the T4 allocator experiment and build the figure + report tables.

Reads: t4_allocator_seed_results.csv, t4_random_allocator_distribution.csv,
t4_contested_summary.csv, t4_contested_aggregates.csv.
Writes: t4_seed_vs_random.csv, t4_fifo_paired.csv,
        t4_allocator_figure.pdf/png, and prints the aggregates.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
SEEDS = list(range(42, 52))
BENCHES = ("SPY", "QQQ")
COL = {"SPY": "#D95F02", "QQQ": "#1B9E77"}

det = pd.read_csv(HERE / "t4_allocator_seed_results.csv")
rnd = pd.read_csv(HERE / "t4_random_allocator_distribution.csv")


def main() -> None:
    rows = []
    for bench in BENCHES:
        for seed in SEEDS:
            r = rnd[(rnd["seed"] == seed) & (rnd["benchmark"] == bench)]["test_excess_return_pct"]
            t4 = det[(det["seed"] == seed) & (det["benchmark"] == bench) & (det["arm"] == "t4")].iloc[0]
            fifo = det[(det["seed"] == seed) & (det["benchmark"] == bench) & (det["arm"] == "fifo")].iloc[0]
            t4x = float(t4["test_excess_return_pct"])
            n = len(r)
            rows.append({
                "seed": seed, "benchmark": bench, "n_random": n,
                "t4_excess": t4x,
                "fifo_excess": float(fifo["test_excess_return_pct"]),
                "random_median": r.median(),
                "random_p05": r.quantile(0.05),
                "random_p95": r.quantile(0.95),
                "t4_percentile": float((r < t4x).mean() * 100.0),
                "p_random_ge_t4": float((1 + (r >= t4x).sum()) / (1 + n)),
                "t4_beats_median": bool(t4x > r.median()),
                "t4_above_p95": bool(t4x > r.quantile(0.95)),
                "fifo_percentile": float((r < float(fifo["test_excess_return_pct"])).mean() * 100.0),
                "t4_sharpe": float(t4["test_sharpe"]), "fifo_sharpe": float(fifo["test_sharpe"]),
                "random_sharpe_median": rnd[(rnd["seed"] == seed) & (rnd["benchmark"] == bench)]["test_sharpe"].median(),
                "t4_max_dd": float(t4["test_max_dd_pct"]), "fifo_max_dd": float(fifo["test_max_dd_pct"]),
                "t4_trades": int(t4["test_trades"]), "fifo_trades": int(fifo["test_trades"]),
                "random_trades_median": rnd[(rnd["seed"] == seed) & (rnd["benchmark"] == bench)]["test_trades"].median(),
                "t4_txn": float(t4["test_trade_txn_cost"]), "fifo_txn": float(fifo["test_trade_txn_cost"]),
            })
    svr = pd.DataFrame(rows)
    svr.to_csv(HERE / "t4_seed_vs_random.csv", index=False)

    paired_rows = []
    for bench in BENCHES:
        g = svr[svr["benchmark"] == bench]
        d_excess = g["t4_excess"] - g["fifo_excess"]
        d_sharpe = g["t4_sharpe"] - g["fifo_sharpe"]
        d_dd = g["t4_max_dd"] - g["fifo_max_dd"]
        d_txn = g["t4_txn"] - g["fifo_txn"]
        paired_rows.append({
            "benchmark": bench,
            "d_excess_median": d_excess.median(),
            "d_excess_q25": d_excess.quantile(0.25), "d_excess_q75": d_excess.quantile(0.75),
            "d_excess_min": d_excess.min(), "d_excess_max": d_excess.max(),
            "excess_wins": int((d_excess > 0).sum()),
            "d_sharpe_median": d_sharpe.median(), "sharpe_wins": int((d_sharpe > 0).sum()),
            "d_maxdd_median": d_dd.median(), "dd_wins": int((d_dd > 0).sum()),
            "d_txn_median": d_txn.median(),
            "median_t4_percentile": g["t4_percentile"].median(),
            "median_p_random_ge_t4": g["p_random_ge_t4"].median(),
            "seeds_beat_random_median": int(g["t4_beats_median"].sum()),
            "seeds_above_p95": int(g["t4_above_p95"].sum()),
            "median_fifo_percentile": g["fifo_percentile"].median(),
        })
    paired = pd.DataFrame(paired_rows)
    paired.to_csv(HERE / "t4_fifo_paired.csv", index=False)
    print(svr.round(2).to_string(index=False))
    print()
    print(paired.round(3).to_string(index=False))

    # Figure: per-seed random band with T4 and FIFO markers.
    fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.6), sharey=True)
    for ax, bench in zip(axes, BENCHES):
        g = svr[svr["benchmark"] == bench].sort_values("seed")
        y = np.arange(len(g))[::-1]
        for i, (_, r) in enumerate(g.iterrows()):
            rr = rnd[(rnd["seed"] == r["seed"]) & (rnd["benchmark"] == bench)]["test_excess_return_pct"]
            ax.hlines(y[i], rr.quantile(0.05), rr.quantile(0.95), color="#9CA3AF", lw=5, alpha=0.55,
                      label="random 5th–95th pct." if i == 0 else None)
            ax.scatter(rr.median(), y[i], marker="|", s=210, color="#374151",
                       label="random median" if i == 0 else None, zorder=4)
            ax.scatter(r["t4_excess"], y[i], s=52, color=COL[bench], zorder=5,
                       label="T4 event-priority" if i == 0 else None)
            ax.scatter(r["fifo_excess"], y[i], s=46, marker="s", facecolor="white",
                       edgecolor=COL[bench], lw=1.4, zorder=5,
                       label="FIFO" if i == 0 else None)
        ax.set_yticks(y)
        ax.set_yticklabels([f"seed {int(s)}" for s in g["seed"]], fontsize=8.4)
        ax.axvline(0, color="black", lw=0.9, ls="--")
        ax.set_xlabel("Full-test excess return (pct. points)", fontsize=9)
        ax.set_title(f"{bench}: T4 vs FIFO vs 1,000 random allocations", fontsize=9.5)
        ax.grid(axis="x", color="#E5E7EB", lw=0.7)
        ax.tick_params(labelsize=8.4)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].legend(fontsize=8, loc="lower left")
    fig.suptitle("Allocator experiment: identical frozen T1+T2+T3+T4 stack, only the allocation rule varies",
                 fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    for ext in ("pdf", "png"):
        fig.savefig(HERE / f"t4_allocator_figure.{ext}", dpi=250, bbox_inches="tight")
    print("figure written")


if __name__ == "__main__":
    main()
