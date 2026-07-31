"""Build the publication figures for the polarity-aware Polymarket paper."""
from __future__ import annotations

from pathlib import Path

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
H1_DIR = ROOT / "output" / "raw_expectation_tminus1_audit_clean"
OUT_DIR = ROOT / "output" / "pdf" / "paper_revision"
ROBUSTNESS = ROOT / "analysis" / "output" / "robustness_tables" / "robustness_budget_summary.csv"

INK = "#15233C"
MUTED = "#667085"
GRID = "#D9E1EA"
PAPER = "#FBFCFE"
TEAL = "#159A8C"
TEAL_SOFT = "#DDF4F0"
BLUE = "#3478B8"
BLUE_SOFT = "#E4F0FA"
CORAL = "#E56B51"
CORAL_SOFT = "#FBE8E3"
GOLD = "#E5A823"
GOLD_SOFT = "#FFF1C9"
PURPLE = "#7A5AA6"
PURPLE_SOFT = "#EEE7F6"
SLATE_SOFT = "#EEF1F5"


def configure() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Serif",
            "font.size": 8.5,
            "axes.titlesize": 10,
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
    fig.savefig(OUT_DIR / f"{stem}.pdf", facecolor="white")
    fig.savefig(OUT_DIR / f"{stem}.png", dpi=260, facecolor="white")
    plt.close(fig)


def rounded_box(
    ax: plt.Axes,
    xy: tuple[float, float],
    width: float,
    height: float,
    *,
    face: str,
    edge: str,
    title: str,
    lines: list[str],
    title_color: str = INK,
) -> None:
    x, y = xy
    patch = FancyBboxPatch(
        (x, y),
        width,
        height,
        boxstyle="round,pad=0.018,rounding_size=0.035",
        linewidth=1.2,
        edgecolor=edge,
        facecolor=face,
        zorder=2,
    )
    ax.add_patch(patch)
    ax.text(x + 0.04 * width, y + 0.78 * height, title, color=title_color,
            fontsize=8.5, fontweight="bold", ha="left", va="center", zorder=3)
    ax.text(x + 0.04 * width, y + 0.48 * height, "\n".join(lines), color=INK,
            fontsize=7.0, ha="left", va="center", linespacing=1.28, zorder=3)


def arrow(ax: plt.Axes, start: tuple[float, float], end: tuple[float, float], color: str = MUTED) -> None:
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=10,
            linewidth=1.25,
            color=color,
            shrinkA=2,
            shrinkB=2,
            zorder=4,
        )
    )


def signal_window() -> None:
    """Schematic of the estimand and the information available at each time."""
    fig = plt.figure(figsize=(7.05, 2.68))
    gs = fig.add_gridspec(2, 1, height_ratios=[1.12, 0.88], hspace=0.12)
    axp = fig.add_subplot(gs[0])
    axe = fig.add_subplot(gs[1], sharex=axp)

    x = np.arange(10)
    prob = np.array([0.43, 0.47, 0.50, 0.58, 0.63, 0.68, 0.66, 0.73, 0.78, 0.82])
    theta = 0.60
    t_theta, t_last, t_end = 4, 8, 9

    for ax in (axp, axe):
        ax.axvspan(t_theta, t_last, color=TEAL_SOFT, zorder=0)
        ax.axvline(t_theta, color=TEAL, lw=1.2, ls="--")
        ax.axvline(t_last, color=GOLD, lw=1.2, ls="--")
        ax.axvline(t_end, color=CORAL, lw=1.2, ls=":")

    axp.plot(x, prob, color=BLUE, lw=2.2, marker="o", ms=3.4, mfc="white", mec=BLUE, zorder=3)
    axp.fill_between(x, theta, prob, where=(x >= t_theta) & (x <= t_last),
                     color=BLUE_SOFT, alpha=0.85, interpolate=True, zorder=1)
    axp.axhline(theta, color=MUTED, lw=0.9, ls="--")
    axp.text(0.12, theta + 0.012, r"eligibility threshold $\theta$", color=MUTED,
             fontsize=7, va="bottom")
    axp.scatter([t_theta], [prob[t_theta]], s=45, color=TEAL, edgecolor="white", linewidth=0.8, zorder=5)
    axp.text(t_theta + 0.10, prob[t_theta] + 0.052, r"first eligible signal $T_\theta$",
             fontsize=7.2, color=TEAL, fontweight="bold")
    axp.set_ylim(0.38, 0.88)
    axp.set_ylabel("Favorable-side\nprobability")
    axp.set_yticks([0.4, 0.6, 0.8], ["40%", "60%", "80%"])
    axp.tick_params(axis="x", bottom=False, labelbottom=False)
    axp.spines["bottom"].set_visible(False)
    axp.grid(axis="y", color=GRID, lw=0.55)

    equity = np.array([100.0, 99.4, 100.6, 101.0, 101.7, 103.6, 102.8, 105.4, 106.2, 104.9])
    axe.plot(x[:t_theta + 1], equity[:t_theta + 1], color="#AAB2BF", lw=1.3)
    axe.plot(x[t_theta:t_last + 1], equity[t_theta:t_last + 1], color=TEAL, lw=2.2)
    axe.plot(x[t_last:], equity[t_last:], color=CORAL, lw=1.1, ls=":")
    axe.scatter([t_theta], [equity[t_theta]], s=38, color=TEAL, zorder=5)
    axe.scatter([t_last], [equity[t_last]], s=42, color=GOLD, edgecolor=INK, linewidth=0.5, zorder=5)
    axe.annotate(
        "",
        xy=(t_last - 0.05, 104.8),
        xytext=(t_theta + 0.05, 104.8),
        arrowprops=dict(arrowstyle="<->", color=INK, lw=1.0),
    )
    axe.text((t_theta + t_last) / 2, 105.08, r"H1 window: $[T_\theta,\,T_{end}-1]$",
             ha="center", va="bottom", fontsize=8, color=INK, fontweight="bold")
    axe.text(t_theta + 0.08, equity[t_theta] - 0.75, r"entry close $P_{T_\theta}$", color=TEAL, fontsize=7)
    axe.text(t_last - 0.08, equity[t_last] + 0.72, r"final eligible close $P_{T_{end}-1}$",
             color="#9B6D00", fontsize=7, ha="right")
    axe.text(t_end, 99.05, "event deadline", color=CORAL, fontsize=7, ha="right")
    axe.set_ylim(98.2, 107.4)
    axe.set_yticks([])
    axe.set_ylabel("Equity\nclose")
    axe.set_xlim(-0.15, 9.15)
    axe.set_xticks([0, t_theta, t_last, t_end],
                   ["candidate observed", r"$T_\theta$", r"$T_{end}-1$", r"$T_{end}$"])
    axe.spines["left"].set_visible(False)
    axe.spines["bottom"].set_color(GRID)

    fig.text(0.985, 0.015, "Schematic - not to scale", ha="right", va="bottom",
             fontsize=6.6, color=MUTED)
    fig.subplots_adjust(left=0.105, right=0.995, top=0.98, bottom=0.19)
    save(fig, "fig_signal_window")


def strategy_architecture() -> None:
    """Visual separation of candidate construction, H1, and the CEM portfolio."""
    fig, ax = plt.subplots(figsize=(7.05, 3.22))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    rounded_box(ax, (0.015, 0.34), 0.16, 0.30, face=BLUE_SOFT, edge=BLUE,
                title="1  Event feed", lines=["Polymarket question", "probability path", "mapped equity + deadline"])
    rounded_box(ax, (0.205, 0.34), 0.18, 0.30, face=PURPLE_SOFT, edge=PURPLE,
                title="2  Semantic layer", lines=["exact duplicate collapse", "economic-event identity", "YES/NO polarity"])
    rounded_box(ax, (0.415, 0.36), 0.17, 0.28, face=TEAL_SOFT, edge=TEAL,
                title=r"3  Eligibility at $T_\theta$", lines=["favorable probability", "deadline checks", "point-in-time features"])
    arrow(ax, (0.175, 0.49), (0.205, 0.49), BLUE)
    arrow(ax, (0.385, 0.49), (0.415, 0.49), PURPLE)

    ax.plot([0.585, 0.615], [0.50, 0.70], color=MUTED, lw=1.1)
    ax.plot([0.585, 0.615], [0.50, 0.27], color=MUTED, lw=1.1)
    rounded_box(ax, (0.615, 0.59), 0.17, 0.22, face=GOLD_SOFT, edge=GOLD,
                title="H1 observation", lines=["all eligible candidates", "fixed notional", r"hold to $T_{end}-1$"])
    ax.text(0.700, 0.835, "EMPIRICAL BRANCH", color="#9B6D00", fontsize=7,
            fontweight="bold", ha="center")

    rounded_box(ax, (0.615, 0.09), 0.17, 0.32, face=CORAL_SOFT, edge=CORAL,
                title="4  CEM policy", lines=["select + size overlaps", "bounded thresholds", "capital constraints"])
    ax.text(0.700, 0.045, "PORTFOLIO BRANCH", color=CORAL, fontsize=7,
            fontweight="bold", ha="center")

    # Four treatment chips inside the route to the portfolio.
    chip_y = [0.39, 0.30, 0.21, 0.12]
    chip_specs = [
        ("T1", "friction-aware fit", BLUE, BLUE_SOFT),
        ("T2", "walk-forward labels", PURPLE, PURPLE_SOFT),
        ("T3", "half-Kelly sizing", TEAL, TEAL_SOFT),
        ("T4", "event priority", GOLD, GOLD_SOFT),
    ]
    for y, (tag, label, edge, face) in zip(chip_y, chip_specs):
        ax.add_patch(FancyBboxPatch((0.815, y), 0.17, 0.062,
                                   boxstyle="round,pad=0.008,rounding_size=0.018",
                                   facecolor=face, edgecolor=edge, linewidth=1.0))
        ax.text(0.828, y + 0.031, tag, color=edge, fontsize=7.2, fontweight="bold", va="center")
        ax.text(0.862, y + 0.031, label, color=INK, fontsize=6.4, va="center")
    arrow(ax, (0.785, 0.25), (0.815, 0.25), CORAL)

    rounded_box(ax, (0.815, 0.59), 0.17, 0.18, face=BLUE_SOFT, edge=BLUE,
                title="Portfolio output", lines=["event positions", "+ benchmark sweep"])
    ax.add_patch(FancyArrowPatch((0.900, 0.47), (0.900, 0.59), arrowstyle="-|>",
                                mutation_scale=10, color=CORAL, lw=1.2))

    ax.text(0.50, 0.975, "One cleaned candidate source, two different research objects",
            color=INK, fontsize=9.5, fontweight="bold", ha="center", va="top")
    ax.text(0.50, 0.900, "H1 estimates the opportunity set; CEM tests one constrained way to allocate it.",
            color=MUTED, fontsize=7.4, ha="center", va="top")
    fig.subplots_adjust(left=0.005, right=0.995, top=0.99, bottom=0.02)
    save(fig, "fig_strategy_architecture")


def clustered_family_interval(
    frame: pd.DataFrame,
    family: str,
    *,
    seed: int,
    draws: int = 20_000,
) -> tuple[float, float, float, int]:
    subset = frame.loc[frame["event_family"].eq(family), ["event_id", "net_return"]].dropna()
    grouped = subset.groupby("event_id")["net_return"].agg(["sum", "size"])
    sums = grouped["sum"].to_numpy(float)
    sizes = grouped["size"].to_numpy(float)
    rng = np.random.default_rng(seed)
    simulated: list[np.ndarray] = []
    for start in range(0, draws, 1_000):
        n = min(1_000, draws - start)
        indices = rng.integers(0, len(grouped), size=(n, len(grouped)))
        simulated.append(sums[indices].sum(axis=1) / sizes[indices].sum(axis=1))
    boot = np.concatenate(simulated)
    return (float(subset["net_return"].mean()), float(np.quantile(boot, 0.025)),
            float(np.quantile(boot, 0.975)), int(len(subset)))


def h1_evidence() -> None:
    inference = pd.read_csv(H1_DIR / "h1_raw_expectation_cluster_inference.csv")
    trades = pd.read_csv(H1_DIR / "raw_expectation_trades_candidate_level.csv")
    requested = [
        ("candidate_observation", "economic_event_id", "Candidate / event"),
        ("candidate_observation", "symbol", "Candidate / symbol"),
        ("candidate_observation", "entry_month_block", "Candidate / month"),
        ("economic_event", "entry_month_block", "Equal-event / month"),
    ]
    rows = []
    for level, cluster, label in requested:
        row = inference.loc[inference["level"].eq(level) & inference["cluster_type"].eq(cluster)].iloc[0]
        rows.append((label, row))

    fig, axes = plt.subplots(1, 2, figsize=(7.05, 2.72), gridspec_kw={"width_ratios": [1.12, 1.0]})
    ax = axes[0]
    y = np.arange(len(rows))[::-1]
    dep_colors = [TEAL, BLUE, CORAL, PURPLE]
    for idx, (yi, (label, row), color) in enumerate(zip(y, rows, dep_colors)):
        if idx % 2 == 0:
            ax.axhspan(yi - 0.42, yi + 0.42, color=SLATE_SOFT, alpha=0.58, zorder=0)
        mean = float(row["mean_raw_net_return"]) * 100
        lo = float(row["cluster_ci_lo"]) * 100
        hi = float(row["cluster_ci_hi"]) * 100
        ax.hlines(yi, lo, hi, color=color, lw=2.0, zorder=2)
        ax.scatter(mean, yi, s=42, color=color, edgecolor="white", lw=0.8, zorder=3)
        ax.text(hi + 0.10, yi, f"{mean:+.2f}%", fontsize=6.8, color=INK, va="center")
    ax.axvline(0, color=INK, lw=0.8)
    ax.set_yticks(y, [label for label, _ in rows])
    ax.set_xlabel("Mean net return (%, 95% bootstrap interval)")
    ax.set_title("A. Dependence sensitivity", loc="left")
    ax.grid(axis="x", color=GRID, lw=0.55)

    ax = axes[1]
    families = ["earnings", "geo", "other"]
    labels = ["Earnings", "Geopolitical", "Other"]
    family_colors = [BLUE, TEAL, GOLD]
    stats = [clustered_family_interval(trades, family, seed=20260713 + i) for i, family in enumerate(families)]
    y = np.arange(len(stats))[::-1]
    for yi, label, color, (mean, lo, hi, n) in zip(y, labels, family_colors, stats):
        mean_pct, lo_pct, hi_pct = mean * 100, lo * 100, hi * 100
        ax.hlines(yi, lo_pct, hi_pct, color=color, lw=2.0)
        ax.vlines([lo_pct, hi_pct], yi - 0.06, yi + 0.06, color=color, lw=1.0)
        ax.scatter(mean_pct, yi, s=48, color=color, edgecolor="white", lw=0.8, zorder=3)
        ax.text(hi_pct + 0.35, yi, f"{mean_pct:+.2f}%  N={n}", fontsize=6.8, color=INK, va="center")
    ax.axvline(0, color=INK, lw=0.8)
    ax.set_yticks(y, labels)
    ax.set_xlabel("Mean net return (%, event-clustered 95% interval)")
    ax.set_title("B. Event-family heterogeneity", loc="left")
    ax.grid(axis="x", color=GRID, lw=0.55)
    fig.subplots_adjust(left=0.18, right=0.995, top=0.87, bottom=0.22, wspace=0.40)
    save(fig, "fig1_h1_evidence")


def budget_robustness() -> None:
    data = pd.read_csv(ROBUSTNESS)
    order = ["6x20", "6x30", "10x20", "10x30"]
    panels = [("validation", "A. Validation: Jan-Mar 2026"), ("final", "B. Final: Apr-Jun 2026")]
    cmap = mcolors.LinearSegmentedColormap.from_list("paper_div", [CORAL, "#FFF8ED", TEAL])
    norm = mcolors.TwoSlopeNorm(vmin=-2.0, vcenter=0.0, vmax=14.0)

    fig, axes = plt.subplots(1, 2, figsize=(7.05, 2.86), gridspec_kw={"wspace": 0.24})
    for ax, (prefix, title) in zip(axes, panels):
        med = np.zeros((len(order), 2))
        q25 = np.zeros_like(med)
        q75 = np.zeros_like(med)
        for col, benchmark in enumerate(["SPY", "QQQ"]):
            subset = data.loc[data["benchmark"].eq(benchmark)].set_index("budget").loc[order]
            med[:, col] = subset[f"{prefix}_excess_pct_median"].to_numpy(float)
            q25[:, col] = subset[f"{prefix}_excess_pct_q25"].to_numpy(float)
            q75[:, col] = subset[f"{prefix}_excess_pct_q75"].to_numpy(float)
        ax.imshow(med, cmap=cmap, norm=norm, aspect="auto", interpolation="nearest")
        for row in range(len(order)):
            for col in range(2):
                ax.text(col, row - 0.10, f"{med[row, col]:+.2f}", ha="center", va="center",
                        fontsize=8.1, color=INK, fontweight="bold")
                ax.text(col, row + 0.19, f"[{q25[row, col]:+.2f}, {q75[row, col]:+.2f}]",
                        ha="center", va="center", fontsize=6.3, color=INK)
        ax.add_patch(Rectangle((-0.49, 2.51), 1.98, 0.98, fill=False, edgecolor=GOLD,
                               linewidth=2.0, zorder=4))
        ax.set_xticks([0, 1], ["SPY", "QQQ"])
        ax.xaxis.tick_top()
        ax.tick_params(axis="x", length=0, pad=5)
        ax.set_yticks(range(len(order)), [value.replace("x", r"$\times$") for value in order])
        ax.tick_params(axis="y", length=0)
        ax.set_title(title, loc="left", pad=11)
        for spine in ax.spines.values():
            spine.set_visible(False)
    cbar_ax = fig.add_axes([0.22, 0.065, 0.56, 0.035])
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    cbar = fig.colorbar(sm, cax=cbar_ax, orientation="horizontal")
    cbar.set_label("Excess return (percentage points)", fontsize=7)
    cbar.ax.tick_params(labelsize=6.5, length=2)
    cbar.outline.set_visible(False)
    fig.subplots_adjust(left=0.09, right=0.995, top=0.82, bottom=0.19)
    save(fig, "fig2_cem_budget_robustness")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    configure()
    signal_window()
    strategy_architecture()
    h1_evidence()
    budget_robustness()
    for stem in ["fig_signal_window", "fig_strategy_architecture", "fig1_h1_evidence",
                 "fig2_cem_budget_robustness"]:
        print(OUT_DIR / f"{stem}.pdf")


if __name__ == "__main__":
    main()
