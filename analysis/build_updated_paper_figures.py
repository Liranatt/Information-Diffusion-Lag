"""Build publication figures for the cleaned-expectation manuscript."""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
H1 = ROOT / "output" / "raw_expectation_tminus1_audit_clean"
OUT = ROOT / "output" / "pdf" / "paper_revision"
CLEAN_EQUITY = ROOT / "data" / "experiment_equity_logs_clean"
LEGACY_EQUITY = ROOT / "runs" / "paper_legacy_key_arms" / "experiment_equity_logs_clean"

NAVY = "#17324D"
BLUE = "#2C7FB8"
TEAL = "#1B9E77"
ORANGE = "#D95F02"
GRAY = "#6B7280"
LIGHT = "#EAF2F8"


def _style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.labelsize": 9,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.dpi": 150,
            "savefig.dpi": 300,
            "savefig.bbox": "tight",
        }
    )


def pipeline_figure() -> None:
    fig, ax = plt.subplots(figsize=(7.2, 2.2))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    boxes = [
        (0.02, "Continuous\nmarket belief", "Polymarket levels,\nrevisions, deadlines"),
        (0.27, "Semantic audit\n+ polarity", "36 duplicates collapsed;\n75 audited rows gated"),
        (0.52, "Cleaned H1\nexpectation", "Fixed notional; signal\nto final pre-event close"),
        (0.77, "Walk-forward CEM\nmonetization", "Policy, costs, and\ncapital limits"),
    ]
    colors = [LIGHT, "#E8F5E9", "#FFF3E0", "#F3E5F5"]
    for index, ((x, title, subtitle), color) in enumerate(zip(boxes, colors)):
        patch = FancyBboxPatch(
            (x, 0.25),
            0.20,
            0.52,
            boxstyle="round,pad=0.012,rounding_size=0.02",
            linewidth=1.0,
            edgecolor=NAVY,
            facecolor=color,
        )
        ax.add_patch(patch)
        ax.text(
            x + 0.10,
            0.60,
            title,
            ha="center",
            va="center",
            fontsize=7.8,
            weight="bold",
            color=NAVY,
        )
        ax.text(x + 0.10, 0.38, subtitle, ha="center", va="center", fontsize=6.8, color=GRAY)
        if index < len(boxes) - 1:
            ax.annotate(
                "",
                xy=(x + 0.245, 0.51),
                xytext=(x + 0.205, 0.51),
                arrowprops=dict(arrowstyle="->", color=NAVY, lw=1.5),
            )
    ax.text(
        0.5,
        0.08,
        "Primary claim: observed conditional net-return expectation.   Secondary claim: feasible portfolio implementation.",
        ha="center",
        color=NAVY,
        fontsize=8,
    )
    fig.savefig(OUT / "fig1_audited_pipeline.png")
    plt.close(fig)


def h1_figure() -> None:
    inference = pd.read_csv(H1 / "h1_raw_expectation_cluster_inference.csv")
    sensitivity = pd.read_csv(H1 / "h1_sensitivity.csv")

    candidate = inference[
        (inference["level"] == "candidate_observation")
        & (inference["cluster_type"] == "economic_event_id")
    ].iloc[0]
    event = inference[
        (inference["level"] == "economic_event")
        & (inference["cluster_type"] == "entry_month_block")
    ].iloc[0]

    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.7), gridspec_kw={"width_ratios": [1.05, 1.35]})
    ax = axes[0]
    labels = ["Candidate observations\n(event-clustered)", "Equal-weight events\n(month-blocked)"]
    means = np.array([candidate["mean_raw_net_return"], event["mean_raw_net_return"]]) * 100
    lows = np.array([candidate["cluster_ci_lo"], event["cluster_ci_lo"]]) * 100
    highs = np.array([candidate["cluster_ci_hi"], event["cluster_ci_hi"]]) * 100
    y = np.arange(2)
    ax.errorbar(
        means,
        y,
        xerr=np.vstack([means - lows, highs - means]),
        fmt="o",
        color=BLUE,
        ecolor=NAVY,
        capsize=4,
        lw=1.5,
    )
    ax.axvline(0, color=GRAY, lw=1, ls="--")
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("Mean net return (%) with 95% interval")
    ax.set_title("A. Dependence-aware expectation")
    for yi, value in zip(y, means):
        ax.text(value + 0.12, yi + 0.12, f"{value:+.2f}%", color=NAVY, fontsize=8)

    ax = axes[1]
    variants = ["Full sample", "Drop top 1%", "Symmetric trim 5%"]
    candidate_values = [
        candidate["mean_raw_net_return"] * 100,
        sensitivity.query("level == 'candidate_observation' and variant == 'drop_top_1pct'")[
            "mean_raw_net_return"
        ].iloc[0]
        * 100,
        sensitivity.query("level == 'candidate_observation' and variant == 'symmetric_trim_5pct'")[
            "mean_raw_net_return"
        ].iloc[0]
        * 100,
    ]
    event_values = [
        event["mean_raw_net_return"] * 100,
        sensitivity.query("level == 'economic_event' and variant == 'drop_top_1pct'")[
            "mean_raw_net_return"
        ].iloc[0]
        * 100,
        sensitivity.query("level == 'economic_event' and variant == 'symmetric_trim_5pct'")[
            "mean_raw_net_return"
        ].iloc[0]
        * 100,
    ]
    x = np.arange(len(variants))
    width = 0.36
    ax.bar(x - width / 2, candidate_values, width, label="Candidate", color=BLUE)
    ax.bar(x + width / 2, event_values, width, label="Economic event", color=TEAL)
    ax.axhline(0, color=GRAY, lw=1)
    ax.set_xticks(x, variants, rotation=15, ha="right")
    ax.set_ylabel("Mean net return (%)")
    ax.set_title("B. Tail sensitivity")
    ax.legend(frameon=False, fontsize=8)
    for xpos, values in ((x - width / 2, candidate_values), (x + width / 2, event_values)):
        for xx, value in zip(xpos, values):
            ax.text(xx, value + (0.06 if value >= 0 else -0.12), f"{value:+.2f}", ha="center", fontsize=7)

    fig.tight_layout()
    fig.savefig(OUT / "fig2_cleaned_h1_expectation.png")
    plt.close(fig)


def family_figure() -> None:
    sensitivity = pd.read_csv(H1 / "h1_sensitivity.csv")
    family = sensitivity[sensitivity["family"] == "event_family"].copy()
    order = ["earnings", "geo", "other"]
    family["variant"] = pd.Categorical(family["variant"], order, ordered=True)
    family = family.sort_values("variant")

    fig, ax = plt.subplots(figsize=(5.2, 2.8))
    x = np.arange(len(family))
    means = family["mean_raw_net_return"].to_numpy(float) * 100
    medians = family["median_raw_net_return"].to_numpy(float) * 100
    bars = ax.bar(x - 0.18, means, 0.36, color=BLUE, label="Mean")
    ax.bar(x + 0.18, medians, 0.36, color=TEAL, label="Median")
    ax.axhline(0, color=GRAY, lw=1)
    labels = [f"{name.title()}\nN={int(n)}" for name, n in zip(family["variant"], family["n"])]
    ax.set_xticks(x, labels)
    ax.set_ylabel("Net return (%)")
    ax.set_title("Cleaned H1 by event family (descriptive subgroup result)")
    ax.legend(frameon=False)
    for bar, value in zip(bars, means):
        ax.text(bar.get_x() + bar.get_width() / 2, value + 0.22, f"{value:+.2f}%", ha="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "fig3_event_family_expectation.png")
    plt.close(fig)


def _equity(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["date"]).sort_values("date")
    frame["strategy_index"] = frame["equity"] / frame["equity"].iloc[0] * 100
    frame["benchmark_index"] = frame["benchmark_equity"] / frame["benchmark_equity"].iloc[0] * 100
    return frame


def cem_figure() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.8), sharey=True)
    for ax, benchmark in zip(axes, ("SPY", "QQQ")):
        slug = benchmark.lower()
        clean = _equity(CLEAN_EQUITY / f"{slug}_t1_t2_t3_t4_test.csv")
        original = _equity(LEGACY_EQUITY / f"{slug}_t1_t2_t3_t4_test.csv")
        ax.plot(clean["date"], clean["strategy_index"], color=TEAL, lw=1.8, label="Audit-cleaned")
        ax.plot(original["date"], original["strategy_index"], color=ORANGE, lw=1.4, label="Original")
        ax.plot(clean["date"], clean["benchmark_index"], color=GRAY, lw=1.2, ls="--", label=benchmark)
        ax.set_title(benchmark)
        ax.set_xlabel("2026 OOS date")
        ax.tick_params(axis="x", rotation=30)
        ax.grid(axis="y", color="#D1D5DB", lw=0.5)
        ax.text(clean["date"].iloc[-1], clean["strategy_index"].iloc[-1], f" {clean['strategy_index'].iloc[-1]-100:+.1f}%", color=TEAL, fontsize=7)
        ax.text(original["date"].iloc[-1], original["strategy_index"].iloc[-1], f" {original['strategy_index'].iloc[-1]-100:+.1f}%", color=ORANGE, fontsize=7)
    axes[0].set_ylabel("Growth of $100")
    axes[0].legend(frameon=False, fontsize=7, loc="upper left")
    fig.suptitle("Matched T1+T2+T3+T4 OOS portfolio paths", y=1.02, fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / "fig4_cem_oos_cleaning_comparison.png")
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    _style()
    pipeline_figure()
    h1_figure()
    family_figure()
    cem_figure()
    for path in sorted(OUT.glob("fig*.png")):
        print(path)


if __name__ == "__main__":
    main()
