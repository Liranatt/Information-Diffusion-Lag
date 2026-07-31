"""Build uncluttered vector figures for the revised technical paper."""
from __future__ import annotations

from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
H1_DIR = ROOT / "output" / "raw_expectation_tminus1_audit_clean"
OUT_DIR = ROOT / "output" / "pdf" / "paper_revision"
ROBUSTNESS = ROOT / "analysis" / "output" / "robustness_tables" / "robustness_budget_summary.csv"
CLEAN_EQUITY = ROOT / "data" / "experiment_equity_logs_clean"
ORIGINAL_EQUITY = ROOT / "runs" / "paper_legacy_key_arms" / "experiment_equity_logs_clean"

INK = "#172033"
BLUE = "#285F8F"
ORANGE = "#B65C2A"
GRAY = "#697386"
GRID = "#D9DEE7"


def configure() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Serif",
            "font.size": 8.5,
            "axes.titlesize": 9.5,
            "axes.labelsize": 8.5,
            "axes.edgecolor": INK,
            "axes.linewidth": 0.7,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "xtick.labelsize": 7.5,
            "ytick.labelsize": 7.5,
            "legend.fontsize": 7.5,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.04,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def save(fig: plt.Figure, stem: str) -> None:
    fig.savefig(OUT_DIR / f"{stem}.pdf")
    fig.savefig(OUT_DIR / f"{stem}.png", dpi=240)
    plt.close(fig)


def clustered_family_interval(
    frame: pd.DataFrame,
    family: str,
    *,
    seed: int = 20260713,
    draws: int = 20_000,
) -> tuple[float, float, float, int]:
    subset = frame.loc[frame["event_family"].eq(family), ["event_id", "net_return"]].dropna()
    grouped = subset.groupby("event_id")["net_return"].agg(["sum", "size"])
    sums = grouped["sum"].to_numpy(float)
    sizes = grouped["size"].to_numpy(float)
    rng = np.random.default_rng(seed)
    simulated: list[np.ndarray] = []
    chunk = 1_000
    for start in range(0, draws, chunk):
        n = min(chunk, draws - start)
        indices = rng.integers(0, len(grouped), size=(n, len(grouped)))
        simulated.append(sums[indices].sum(axis=1) / sizes[indices].sum(axis=1))
    boot = np.concatenate(simulated)
    return (
        float(subset["net_return"].mean()),
        float(np.quantile(boot, 0.025)),
        float(np.quantile(boot, 0.975)),
        int(len(subset)),
    )


def h1_evidence() -> None:
    inference = pd.read_csv(H1_DIR / "h1_raw_expectation_cluster_inference.csv")
    trades = pd.read_csv(H1_DIR / "raw_expectation_trades_candidate_level.csv")

    requested = [
        ("candidate_observation", "economic_event_id", "Candidates / event clusters"),
        ("candidate_observation", "symbol", "Candidates / symbol clusters"),
        ("candidate_observation", "entry_month_block", "Candidates / month blocks"),
        ("economic_event", "entry_month_block", "Equal-weight events / month blocks"),
    ]
    rows = []
    for level, cluster, label in requested:
        row = inference.loc[
            inference["level"].eq(level) & inference["cluster_type"].eq(cluster)
        ].iloc[0]
        rows.append((label, row))

    fig, axes = plt.subplots(1, 2, figsize=(7.05, 2.72), gridspec_kw={"width_ratios": [1.18, 1.0]})

    ax = axes[0]
    y = np.arange(len(rows))[::-1]
    for yi, (label, row) in zip(y, rows):
        mean = float(row["mean_raw_net_return"]) * 100
        lo = float(row["cluster_ci_lo"]) * 100
        hi = float(row["cluster_ci_hi"]) * 100
        ax.errorbar(
            mean,
            yi,
            xerr=[[mean - lo], [hi - mean]],
            fmt="o",
            ms=4.5,
            color=BLUE,
            ecolor=BLUE,
            elinewidth=1.2,
            capsize=2.5,
        )
    ax.axvline(0, color=INK, lw=0.8)
    ax.set_yticks(y, [label for label, _ in rows])
    ax.set_xlabel("Mean net return (%, 95% bootstrap CI)")
    ax.set_title("A. Dependence assumptions")
    ax.grid(axis="x", color=GRID, lw=0.55)

    ax = axes[1]
    families = ["earnings", "geo", "other"]
    labels = ["Earnings", "Geopolitical", "Other"]
    stats = [clustered_family_interval(trades, family, seed=20260713 + i) for i, family in enumerate(families)]
    y = np.arange(len(stats))[::-1]
    for yi, label, (mean, lo, hi, n) in zip(y, labels, stats):
        mean_pct, lo_pct, hi_pct = mean * 100, lo * 100, hi * 100
        ax.errorbar(
            mean_pct,
            yi,
            xerr=[[mean_pct - lo_pct], [hi_pct - mean_pct]],
            fmt="o",
            ms=4.5,
            color=ORANGE,
            ecolor=ORANGE,
            elinewidth=1.2,
            capsize=2.5,
        )
        label_x = hi_pct + 0.45
        ax.text(label_x, yi, f"{mean_pct:+.2f}%  (N={n})", ha="left", va="center", fontsize=7)
    ax.axvline(0, color=INK, lw=0.8)
    ax.set_yticks(y, labels)
    ax.set_xlabel("Mean net return (%, event-clustered 95% CI)")
    ax.set_title("B. Event-family heterogeneity")
    ax.grid(axis="x", color=GRID, lw=0.55)

    fig.subplots_adjust(left=0.22, right=0.995, top=0.86, bottom=0.22, wspace=0.43)
    save(fig, "fig1_h1_evidence")


def budget_robustness() -> None:
    data = pd.read_csv(ROBUSTNESS)
    order = ["6x20", "6x30", "10x20", "10x30"]
    x = np.arange(len(order), dtype=float)

    fig, axes = plt.subplots(1, 2, figsize=(7.05, 2.65), sharey=False)
    panels = [
        ("validation", "A. Validation: Jan--Mar 2026"),
        ("final", "B. Untouched final: Apr--Jun 2026"),
    ]
    for ax, (prefix, title) in zip(axes, panels):
        for benchmark, color, marker, offset in [
            ("SPY", BLUE, "o", -0.08),
            ("QQQ", ORANGE, "s", 0.08),
        ]:
            subset = data.loc[data["benchmark"].eq(benchmark)].set_index("budget").loc[order]
            median = subset[f"{prefix}_excess_pct_median"].to_numpy(float)
            q25 = subset[f"{prefix}_excess_pct_q25"].to_numpy(float)
            q75 = subset[f"{prefix}_excess_pct_q75"].to_numpy(float)
            ax.errorbar(
                x + offset,
                median,
                yerr=np.vstack([median - q25, q75 - median]),
                fmt=marker,
                ms=4.5,
                color=color,
                ecolor=color,
                elinewidth=1.2,
                capsize=2.5,
                label=benchmark,
            )
        ax.axhline(0, color=INK, lw=0.8)
        ax.set_xticks(x, [value.replace("x", r"$\times$") for value in order])
        ax.set_xlabel(r"CEM iterations $\times$ population")
        ax.set_title(title)
        ax.grid(axis="y", color=GRID, lw=0.55)
    axes[0].set_ylabel("Median excess return and IQR (pp)")
    axes[0].legend(frameon=False, ncol=2, loc="upper left")
    fig.subplots_adjust(left=0.15, right=0.995, top=0.87, bottom=0.22, wspace=0.24)
    save(fig, "fig2_cem_budget_robustness")


def normalized_equity(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["date"]).sort_values("date")
    frame["strategy"] = frame["equity"] / frame["equity"].iloc[0] * 100
    frame["benchmark"] = frame["benchmark_equity"] / frame["benchmark_equity"].iloc[0] * 100
    return frame


def cleaning_paths() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.05, 2.55), sharey=True)
    for ax, benchmark in zip(axes, ["SPY", "QQQ"]):
        slug = benchmark.lower()
        clean = normalized_equity(CLEAN_EQUITY / f"{slug}_t1_t2_t3_t4_test.csv")
        original = normalized_equity(ORIGINAL_EQUITY / f"{slug}_t1_t2_t3_t4_test.csv")
        ax.plot(clean["date"], clean["strategy"], color=BLUE, lw=1.4, label="Audit-cleaned")
        ax.plot(original["date"], original["strategy"], color=ORANGE, lw=1.2, label="Original")
        ax.plot(clean["date"], clean["benchmark"], color=GRAY, lw=1.0, ls="--", label="Benchmark")
        ax.set_title(benchmark)
        ax.grid(axis="y", color=GRID, lw=0.55)
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
        ax.set_xlabel("2026 OOS month")
    axes[0].set_ylabel("Growth of $100")
    axes[0].legend(frameon=False, loc="upper left")
    fig.subplots_adjust(left=0.09, right=0.995, top=0.88, bottom=0.20, wspace=0.10)
    save(fig, "fig3_cleaning_equity_paths")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    configure()
    h1_evidence()
    budget_robustness()
    cleaning_paths()
    for path in sorted(OUT_DIR.glob("fig[123]_*.pdf")):
        print(path)


if __name__ == "__main__":
    main()
