"""Generate 5 publication-quality charts for the Information Diffusion Lag paper.

Reads empirical data directly from the repo's output CSVs and pickled caches.
Run from the project root:  python generate_paper_charts.py
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.dates as mdates
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.ticker import FuncFormatter
from scipy.stats import gaussian_kde

PROJECT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT / "output" / "paper_charts"
TRADES_CSV = PROJECT / "output" / "raw_expectation_tminus1" / "raw_expectation_trades_candidate_level.csv"
INFERENCE_CSV = PROJECT / "output" / "raw_expectation_tminus1" / "h1_inference_summary.csv"
PRICES_PKL = PROJECT / "data" / "prices.pkl"
PROBS_PKL = PROJECT / "data" / "probs.pkl"

# ── Palette ──────────────────────────────────────────────────────────────────
NAVY       = "#1f4e79"
DARK_GREY  = "#5a6b7f"
MUTED_ORANGE = "#d4782f"
ACCENT_GREEN = "#2d8659"
LIGHT_BLUE = "#4a90d9"
SOFT_RED   = "#c0504d"
PALE_BLUE  = "#dce9f7"
PALE_ORANGE = "#fde8d0"
DARK_TEAL  = "#2e6f6a"


def _setup():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.size": 11,
        "axes.titlesize": 14,
        "axes.titleweight": "bold",
        "axes.labelsize": 12,
        "figure.facecolor": "white",
    })


def _remove_spines(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


# ═════════════════════════════════════════════════════════════════════════════
# Chart 1 — The Paradigm Shift  (Continuous vs. Discrete)
# ═════════════════════════════════════════════════════════════════════════════
# Uses HPE Q2-2026 (C1025, market 2297855):
#   • "Will Hewlett Packard Enterprise (HPE) beat quarterly earnings?"
#   • Prob 0.86→0.97, Price $32.62→$43.04, net +31.8%
#   • Entry 2026-05-19, Exit T-1 2026-05-29, Resolution 2026-06-01

def chart1_paradigm_shift():
    PRICE_COLOR = "#8b5e3c"   # warm brown — visually distinct from navy prob line

    with open(PROBS_PKL, "rb") as f:
        probs = pickle.load(f)
    with open(PRICES_PKL, "rb") as f:
        prices = pickle.load(f)

    # Probability path
    prob_path = probs["2297855"]
    prob_dates = [ts.tz_localize(None) if ts.tzinfo else ts for ts, _ in prob_path]
    prob_vals  = [v for _, v in prob_path]

    # Price path — wider window to show pre-entry flatness
    hpe_bars = prices["HPE"]
    window_start = pd.Timestamp("2026-05-08")
    window_end   = pd.Timestamp("2026-06-01")   # stop at T_e; later closes blow past ylim
    price_dates = [ts.tz_localize(None) if ts.tzinfo else ts
                   for ts, _, _, _ in hpe_bars
                   if window_start <= (ts.tz_localize(None) if ts.tzinfo else ts) <= window_end]
    price_vals  = [c for ts, _, _, c in hpe_bars
                   if window_start <= (ts.tz_localize(None) if ts.tzinfo else ts) <= window_end]

    entry_date = pd.Timestamp("2026-05-19")
    exit_date  = pd.Timestamp("2026-05-29")
    entry_threshold = 0.71
    price_move_date = pd.Timestamp("2026-05-22")

    _bbox_white = dict(facecolor="white", alpha=0.85, edgecolor="none",
                       boxstyle="round,pad=0.2")

    fig, ax1 = plt.subplots(figsize=(12, 5.5))
    ax2 = ax1.twinx()

    # Price line (right axis) — warm brown, clearly distinct from navy
    ax2.plot(price_dates, price_vals, color=PRICE_COLOR, linewidth=2.5,
             linestyle="-", marker="s", markersize=3.5,
             label="HPE Equity Price", zorder=3)
    ax2.set_ylabel("Equity Price ($)", color=PRICE_COLOR, fontsize=12)
    ax2.tick_params(axis="y", labelcolor=PRICE_COLOR)
    ax2.spines["top"].set_visible(False)

    # Probability line (left axis) — navy, solid
    ax1.plot(prob_dates, prob_vals, color=NAVY, linewidth=2.8,
             marker="o", markersize=4,
             label="P(HPE beats earnings)", zorder=4)
    ax1.set_ylabel("Polymarket Probability", color=NAVY, fontsize=12)
    ax1.tick_params(axis="y", labelcolor=NAVY)
    _remove_spines(ax1)

    # Entry threshold line
    ax1.axhline(entry_threshold, color=MUTED_ORANGE, linestyle="--",
                linewidth=1.5, alpha=0.8, zorder=2)
    ax1.text(price_dates[0] + pd.Timedelta(days=0.3), entry_threshold + 0.015,
             f"CEM Entry Threshold ({entry_threshold:.0%})", color=MUTED_ORANGE,
             fontsize=9, va="bottom", ha="left", fontweight="bold",
             bbox=_bbox_white)

    # Shade the holding period (light green, behind everything)
    ax1.axvspan(entry_date, exit_date, alpha=0.06, color=ACCENT_GREEN, zorder=0)

    # Shade the Information Diffusion Lag (orange, narrower band)
    ax1.axvspan(entry_date, price_move_date, alpha=0.15, color=MUTED_ORANGE,
                zorder=0)

    # Entry annotation — short arrow from the empty area above-left
    ax1.axvline(entry_date, color=ACCENT_GREEN, linestyle="-", linewidth=1.3,
                alpha=0.6, zorder=1)
    ax1.annotate("Entry\n(Prob = 86%)",
                 xy=(entry_date, 0.868),
                 xytext=(entry_date - pd.Timedelta(days=3.5), 0.955),
                 fontsize=10, color=ACCENT_GREEN, fontweight="bold",
                 arrowprops=dict(arrowstyle="-|>", color=ACCENT_GREEN, lw=1.8),
                 ha="center", va="center", zorder=10,
                 bbox=dict(facecolor="white", alpha=0.95, edgecolor=ACCENT_GREEN,
                           boxstyle="round,pad=0.3"))

    # Exit annotation
    ax1.axvline(exit_date, color=SOFT_RED, linestyle="-", linewidth=1.3,
                alpha=0.6, zorder=1)
    ax2.annotate("Exit T$_{e-1}$\n(\\$43.04)",
                 xy=(exit_date, 43.04),
                 xytext=(exit_date + pd.Timedelta(days=1.8), 36.5),
                 fontsize=10, color=SOFT_RED, fontweight="bold",
                 arrowprops=dict(arrowstyle="-|>", color=SOFT_RED, lw=1.8),
                 ha="center", va="center", zorder=10,
                 bbox=dict(facecolor="white", alpha=0.95, edgecolor=SOFT_RED,
                           boxstyle="round,pad=0.3"))

    # Scheduled resolution T_e — the market resolves the day after exit
    te_date = pd.Timestamp("2026-06-01")
    ax1.axvline(te_date, color=NAVY, linestyle=":", linewidth=1.6, alpha=0.75,
                zorder=1)
    ax1.text(te_date - pd.Timedelta(days=0.15), 1.008, "T$_e$: Resolution",
             ha="right", va="top", fontsize=9, color=NAVY, fontweight="bold",
             bbox=dict(facecolor="white", alpha=0.85, edgecolor="none",
                       boxstyle="round,pad=0.2"), zorder=10)

    # Information Diffusion Lag — horizontal gap arrow inside the shaded band
    mid_lag = entry_date + (price_move_date - entry_date) / 2
    ax1.annotate("", xy=(entry_date, 0.635), xytext=(price_move_date, 0.635),
                 arrowprops=dict(arrowstyle="<->", color=MUTED_ORANGE, lw=2),
                 zorder=9)
    ax1.text(mid_lag, 0.588, "Information\nDiffusion Lag", fontsize=10.5,
             color=MUTED_ORANGE, fontweight="bold", ha="center", va="center",
             bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                       edgecolor=MUTED_ORANGE, linewidth=1.5, alpha=0.95),
             zorder=10)

    # Price-flat annotation — bottom-left, clear of the entry callout
    # (escape the dollar signs: a matched $...$ pair triggers mathtext)
    ax2.annotate("Price flat:\n\\$32 – \\$34",
                 xy=(entry_date - pd.Timedelta(days=1.5), 32.9),
                 xytext=(entry_date - pd.Timedelta(days=4), 27.8),
                 fontsize=9, color=PRICE_COLOR, ha="center", va="center",
                 style="italic", fontweight="bold",
                 arrowprops=dict(arrowstyle="-|>", color=PRICE_COLOR, lw=1.2),
                 zorder=10,
                 bbox=dict(facecolor="white", alpha=0.95, edgecolor=PRICE_COLOR,
                           boxstyle="round,pad=0.25"))

    ax1.set_ylim(0.55, 1.02)
    ax2.set_ylim(26, 50)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax1.xaxis.set_major_locator(mdates.DayLocator(interval=3))
    fig.autofmt_xdate(rotation=30)

    ax1.set_title(
        "The Paradigm Shift: Continuous Signal vs. Discrete Price Adjustment\n"
        'HPE — "Will Hewlett Packard Enterprise beat quarterly earnings?"',
        fontsize=13, pad=12,
    )

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="lower right",
               framealpha=0.95, fontsize=10, edgecolor=DARK_GREY)

    fig.tight_layout()
    out = OUTPUT_DIR / "chart1_paradigm_shift.png"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ═════════════════════════════════════════════════════════════════════════════
# Chart 2 — The Data Pipeline & Filtration
# ═════════════════════════════════════════════════════════════════════════════

def chart2_pipeline():
    # Stage counts sourced from the repo:
    #   39,546 = markets in data/markets_cache_historical.json (tag-filtered scan)
    #   13,984 screened / 12,790 NOISE / 1,194 CATALYST = data/catalyst_results.json
    #   1,064 markets → 1,192 (question, symbol) pairs = data/candidates.parquet
    fig, ax = plt.subplots(figsize=(13, 3.0))
    ax.set_xlim(0, 10)
    ax.set_ylim(0.9, 3.3)
    ax.axis("off")
    ax.set_title("Data Pipeline & Filtration Cascade  (Sep 2024 – Jun 2026)",
                 fontsize=15, fontweight="bold", pad=14)

    y_center = 1.8
    nodes = [
        (1.2, y_center, "Polymarket\nRaw Data",
         "All prediction markets\nwith financial /\ngeopolitical tags",
         DARK_GREY),
        (3.6, y_center, "Noise Filter",
         "Gemini 2.5 Flash gate:\n12,790 of 13,984 markets\ndropped as NOISE",
         SOFT_RED),
        (6.0, y_center, "LLM Ontology\nMapping",
         "Gemini 2.5 Flash:\nMap question → U.S.\nequity + polarity",
         LIGHT_BLUE),
        (8.5, y_center, "Cleaned Pool",
         "1,192 valid U.S.\n(question, symbol)\ncandidates",
         ACCENT_GREEN),
    ]

    box_w, box_h = 1.8, 1.4

    for x, y, title, desc, color in nodes:
        rect = FancyBboxPatch(
            (x - box_w / 2, y - box_h / 2), box_w, box_h,
            boxstyle="round,pad=0.12", facecolor="white",
            edgecolor=color, linewidth=2.5,
        )
        ax.add_patch(rect)
        ax.text(x, y + 0.18, title, ha="center", va="center",
                fontsize=11, fontweight="bold", color=color)
        ax.text(x, y - 0.35, desc, ha="center", va="center",
                fontsize=8.5, color=DARK_GREY, linespacing=1.3)

    # Arrows
    arrow_kw = dict(arrowstyle="-|>", color=NAVY, lw=2.5,
                    connectionstyle="arc3,rad=0")
    for i in range(len(nodes) - 1):
        x1 = nodes[i][0] + box_w / 2 + 0.05
        x2 = nodes[i + 1][0] - box_w / 2 - 0.05
        y  = nodes[i][1]
        ax.annotate("", xy=(x2, y), xytext=(x1, y),
                     arrowprops=arrow_kw)

    # Funnel counts centered above each arrow, raised above box tops
    counts = ["39,546 tagged markets", "1,194 catalyst markets", "1,064 markets mapped"]
    arrow_xs = [
        (nodes[0][0] + box_w / 2, nodes[1][0] - box_w / 2),
        (nodes[1][0] + box_w / 2, nodes[2][0] - box_w / 2),
        (nodes[2][0] + box_w / 2, nodes[3][0] - box_w / 2),
    ]
    label_y = y_center + box_h / 2 + 0.25
    for (x1, x2), label in zip(arrow_xs, counts):
        mid = (x1 + x2) / 2
        ax.text(mid, label_y, label, ha="center", va="bottom",
                fontsize=9.5, color=NAVY, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.18", facecolor=PALE_BLUE,
                          edgecolor="none", alpha=0.8))

    fig.tight_layout()
    out = OUTPUT_DIR / "chart2_pipeline.png"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ═════════════════════════════════════════════════════════════════════════════
# Chart 3 — Strict Leakage Prevention Timeline
# ═════════════════════════════════════════════════════════════════════════════

def chart3_timeline():
    fig, ax = plt.subplots(figsize=(13, 3.8))
    ax.set_xlim(-0.5, 10.5)
    ax.set_ylim(-1.8, 2.8)
    ax.axis("off")
    ax.set_title("Strict Leakage Prevention: Trade Lifecycle",
                 fontsize=15, fontweight="bold", pad=16)

    y_line = 0.5

    # Main timeline axis
    ax.annotate("", xy=(10.2, y_line), xytext=(0.0, y_line),
                arrowprops=dict(arrowstyle="-|>", color=DARK_GREY, lw=2.2))
    ax.text(10.35, y_line, "time", fontsize=10, color=DARK_GREY, va="center",
            style="italic")

    # Marker positions — labels alternate above / below for clarity
    markers = [
        ("t0",    1.5, "Market Created\n(Polymarket)",  DARK_GREY,  "above"),
        ("theta", 3.2, "θ Crossed\n(P ≥ 0.55)",        LIGHT_BLUE, "below"),
        ("entry", 4.8, "CEM Entry\nThreshold Crossed",  ACCENT_GREEN, "above"),
        ("te_m1", 7.5, "Hard Exit:\nT$_{e-1}$",         SOFT_RED,   "below"),
        ("te",    9.0, "T$_e$: Scheduled\nResolution",  NAVY,       "above"),
    ]

    for key, x, label, color, pos in markers:
        ax.plot(x, y_line, "o", color=color, markersize=11, zorder=5)
        ax.plot([x, x], [y_line - 0.18, y_line + 0.18], color=color, lw=2.5)

        if pos == "above":
            y_text = y_line + 0.6
            va = "bottom"
        else:
            y_text = y_line - 0.6
            va = "top"
        ax.text(x, y_text, label, ha="center", va=va, fontsize=10,
                fontweight="bold", color=color)

    # Isolated Raw Expectation Window (entry to T_e-1)
    entry_x, exit_x = 4.8, 7.5
    bracket_y = y_line + 1.7
    ax.annotate("", xy=(entry_x, bracket_y), xytext=(exit_x, bracket_y),
                arrowprops=dict(arrowstyle="<->", color=NAVY, lw=2))
    ax.fill_between([entry_x, exit_x], y_line - 0.3, y_line + 0.3,
                    alpha=0.12, color=ACCENT_GREEN, zorder=0)
    ax.text((entry_x + exit_x) / 2, bracket_y + 0.12,
            "Isolated Raw Expectation Window",
            ha="center", va="bottom", fontsize=11, fontweight="bold",
            color=NAVY,
            bbox=dict(boxstyle="round,pad=0.25", facecolor=PALE_BLUE,
                      edgecolor=NAVY, alpha=0.85))

    # No-exposure zone between the hard exit and resolution
    te_x = 9.0
    ax.fill_between([exit_x, te_x], y_line - 0.3, y_line + 0.3,
                    alpha=0.07, color=SOFT_RED, zorder=0)

    # Barrier before T_e — one clean solid wall
    barrier_x = 8.4
    wall = mpatches.Rectangle((barrier_x - 0.05, y_line - 0.75), 0.10, 1.5,
                              facecolor=SOFT_RED, edgecolor="none", zorder=6)
    ax.add_patch(wall)
    ax.text(barrier_x, y_line - 1.05, "Zero\nLook-Ahead",
            ha="center", va="top", fontsize=10, fontweight="bold",
            color=SOFT_RED,
            bbox=dict(facecolor="white", alpha=0.9, edgecolor=SOFT_RED,
                      boxstyle="round,pad=0.2"))

    # Holding period bracket
    ax.annotate("", xy=(entry_x, y_line - 1.15), xytext=(exit_x, y_line - 1.15),
                arrowprops=dict(arrowstyle="<->", color=DARK_GREY, lw=1.3))
    ax.text((entry_x + exit_x) / 2, y_line - 1.3, "Holding Period",
            ha="center", va="top", fontsize=9, color=DARK_GREY, style="italic")

    fig.tight_layout()
    out = OUTPUT_DIR / "chart3_leakage_timeline.png"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ═════════════════════════════════════════════════════════════════════════════
# Chart 4 — The Expectancy Distribution
# ═════════════════════════════════════════════════════════════════════════════

def chart4_expectancy():
    df = pd.read_csv(TRADES_CSV)
    net_returns = df["net_return"].values * 100  # convert to pct

    n = len(net_returns)
    mean_ret = np.mean(net_returns)
    median_ret = np.median(net_returns)
    win_rate = np.mean(net_returns > 0) * 100

    fig, ax = plt.subplots(figsize=(10, 5.5))

    # Clip extremes for visualization but compute stats on full data
    clip_lo, clip_hi = np.percentile(net_returns, [1, 99])
    bins = np.linspace(clip_lo, clip_hi, 60)
    _n, _bins, _ = ax.hist(net_returns, bins=bins, color=LIGHT_BLUE,
                           edgecolor="white", linewidth=0.5, alpha=0.8,
                           zorder=3, density=False)

    # KDE overlay — scaled to match histogram counts
    kde = gaussian_kde(net_returns, bw_method=0.25)
    x_kde = np.linspace(clip_lo, clip_hi, 300)
    bin_width = bins[1] - bins[0]
    y_kde = kde(x_kde) * n * bin_width
    ax.plot(x_kde, y_kde, color=MUTED_ORANGE, linewidth=2.5, zorder=5,
            label="KDE")

    # ZERO line — thick, prominent, solid black
    ax.axvline(0, color="black", linewidth=2.5, linestyle="-", zorder=6,
               label="Zero (break-even)")

    # Mean line
    ax.axvline(mean_ret, color=NAVY, linewidth=2.2, linestyle="-",
               label=f"Mean: +{mean_ret:.2f}%", zorder=7)
    # Median line
    ax.axvline(median_ret, color=ACCENT_GREEN, linewidth=2.2, linestyle="--",
               label=f"Median: +{median_ret:.2f}%", zorder=7)

    ax.set_xlabel("Net Return per Trade (%)", fontsize=12)
    ax.set_ylabel("Frequency", fontsize=12)
    ax.set_title("Expectancy Distribution: 890 Independent T$_{e-1}$ Trades",
                 fontsize=13, fontweight="bold", pad=12)
    _remove_spines(ax)

    # Stats box
    stats_text = (
        f"N = {n:,}\n"
        f"Mean = +{mean_ret:.2f}%\n"
        f"Median = +{median_ret:.2f}%\n"
        f"Win Rate = {win_rate:.2f}%\n"
        f"t-stat p < 0.001"
    )
    ax.text(0.97, 0.95, stats_text, transform=ax.transAxes,
            fontsize=10, verticalalignment="top", horizontalalignment="right",
            bbox=dict(boxstyle="round,pad=0.4", facecolor="white",
                      edgecolor=NAVY, linewidth=1.5, alpha=0.95),
            fontfamily="monospace", color=NAVY)

    ax.legend(loc="upper left", fontsize=10, framealpha=0.95,
              edgecolor=DARK_GREY)

    fig.tight_layout()

    # Honest-display footnote below the axes: extremes are clipped from view,
    # never from the statistics
    fig.text(0.5, -0.015,
             "Display clipped to the 1st–99th percentile; "
             "statistics computed on all 890 trades.",
             ha="center", va="top", fontsize=8.5, color=DARK_GREY,
             style="italic")

    out = OUTPUT_DIR / "chart4_expectancy_distribution.png"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ═════════════════════════════════════════════════════════════════════════════
# Chart 5 — Statistical Robustness (Symmetric Tail Trimming)
# ═════════════════════════════════════════════════════════════════════════════

def chart5_tail_trimming():
    inf = pd.read_csv(INFERENCE_CSV)

    trim_pcts = ["No trim", "1%", "5%", "10%"]
    levels = {
        "Candidate-level": ("candidate_level", NAVY),
        "Symbol-day collapsed": ("symbol_day_collapsed", MUTED_ORANGE),
        "Event-level": ("event_level", ACCENT_GREEN),
    }
    trim_variants = [
        "net",                  # core (no trim)
        "symmetric_trim_1pct",
        "symmetric_trim_5pct",
        "symmetric_trim_10pct",
    ]

    fig, ax = plt.subplots(figsize=(8, 5))

    for label, (level_key, color) in levels.items():
        means = []
        for variant in trim_variants:
            if variant == "net":
                row = inf[(inf["level"] == level_key) &
                          (inf["family"] == "core") &
                          (inf["variant"] == "net")]
            else:
                row = inf[(inf["level"] == level_key) &
                          (inf["family"] == "tail") &
                          (inf["variant"] == variant)]
            means.append(float(row["mean"].iloc[0]) * 100)

        ax.plot(trim_pcts, means, marker="o", linewidth=2.2, markersize=7,
                color=color, label=label, zorder=3)

        # End-point value label at the 10% trim (Candidate-level goes below
        # its marker; the other two sit above — the lines converge there)
        dy = -15 if label == "Candidate-level" else 9
        ax.annotate(f"{means[-1]:.2f}%", xy=(trim_pcts[-1], means[-1]),
                    xytext=(-4, dy), textcoords="offset points",
                    ha="right", fontsize=9.5, fontweight="bold", color=color)

    # Y=0 baseline — thick, prominent dashed black line
    ax.axhline(0, color="#2c2c2c", linewidth=2.8, linestyle="--", alpha=0.85,
               zorder=2, label="Y = 0")

    ax.set_xlabel("Symmetric Tail Trim", fontsize=12)
    ax.set_ylabel("Mean Net Return (%)", fontsize=12)
    ax.set_title("Statistical Robustness: Symmetric Tail Trimming",
                 fontsize=13, fontweight="bold", pad=12)
    _remove_spines(ax)

    ax.legend(loc="upper right", fontsize=10, framealpha=0.95,
              edgecolor=DARK_GREY)
    ax.set_ylim(bottom=-0.4)

    # Annotate all-positive message — positioned with proper padding
    ax.text(0.38, 0.12,
            "All three aggregation levels remain positive\n"
            "under symmetric trimming up to 10%",
            transform=ax.transAxes, fontsize=9.5, color=ACCENT_GREEN,
            style="italic", va="bottom", ha="center",
            bbox=dict(facecolor="white", alpha=0.85, edgecolor=ACCENT_GREEN,
                      boxstyle="round,pad=0.3"))

    fig.tight_layout()
    out = OUTPUT_DIR / "chart5_tail_trimming.png"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ═════════════════════════════════════════════════════════════════════════════
# Chart 6 — OOS Portfolio Monetization (Strategy vs. Benchmark)
# ═════════════════════════════════════════════════════════════════════════════
# Same data behind data/cem_t1_t2_t3_t4_{QQQ,SPY}_individual.png
# (optimize_cem.py): the T1+T2+T3+T4 test-split equity logs. KPIs are
# recomputed from the CSVs so the boxes always match what is plotted
# (cross-checked against data/experiment_results_clean.csv:
#   QQQ arm: test_return 27.22%, sharpe 2.354, max_dd -8.94%
#   SPY arm: test_return 23.16%, sharpe 2.477, max_dd -6.55%).

EQUITY_DIR = PROJECT / "data" / "experiment_equity_logs_clean"
PALE_TAN = "#d9a05f"   # SPY B&H — light warm tan, pairs with the orange arm


def _curve_stats(series: pd.Series) -> dict:
    """Return / PnL / Sharpe / Sortino / MaxDD, definitions per optimize_cem's
    _calc_advanced_metrics (Sortino = mean over downside-only std, ann.)."""
    r = series.pct_change().dropna()
    down = r[r < 0]
    cum = series / series.iloc[0]
    return {
        "ret": (series.iloc[-1] / series.iloc[0] - 1) * 100,
        "pnl": series.iloc[-1] - series.iloc[0],
        "sharpe": r.mean() / r.std() * np.sqrt(252),
        "sortino": r.mean() / down.std() * np.sqrt(252),
        "max_dd": ((cum / cum.cummax() - 1) * 100).min(),
    }


def _jensens_alpha(rp: np.ndarray, rm: np.ndarray, rf_annual: float = 0.05) -> dict:
    """CAPM alpha on daily returns — exact replica of statistical_tests.py's
    jensens_alpha_test (OLS, Rf = 5% annual, one-sided p)."""
    from scipy import stats as sp_stats
    daily_rf = (1 + rf_annual) ** (1 / 252) - 1
    n = min(len(rp), len(rm))
    y, x = rp[:n] - daily_rf, rm[:n] - daily_rf
    X = np.column_stack([np.ones(n), x])
    beta_vec, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta_vec
    se_alpha = np.sqrt(np.sum(resid ** 2) / (n - 2)
                       * (1 / n + x.mean() ** 2 / np.sum((x - x.mean()) ** 2)))
    t_alpha = beta_vec[0] / se_alpha if se_alpha > 0 else 0.0
    return {
        "alpha_annual_pct": beta_vec[0] * 252 * 100,
        "beta": float(beta_vec[1]),
        "p_value": float(1 - sp_stats.t.cdf(t_alpha, df=n - 2)),
        "n_days": n,
    }


def _load_arm(benchmark: str) -> dict:
    """Load one benchmark arm's test equity log and compute its KPIs."""
    eq = pd.read_csv(EQUITY_DIR / f"{benchmark.lower()}_t1_t2_t3_t4_test.csv")
    dates = pd.to_datetime(eq["date"])
    strat = eq["equity"].astype(float)
    bench = eq["benchmark_equity"].astype(float)
    return {
        "benchmark": benchmark,
        "dates": dates,
        "strat": strat,
        "bench": bench,
        "s": _curve_stats(strat),
        "b": _curve_stats(bench),
        "capm": _jensens_alpha(strat.pct_change().dropna().values,
                               bench.pct_change().dropna().values),
        "period": f"{dates.iloc[0]:%b}–{dates.iloc[-1]:%b} {dates.iloc[-1]:%Y}",
    }


def _format_equity_axis(ax):
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    plt.setp(ax.get_xticklabels(), rotation=15, ha="right")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v / 1000:,.0f}K"))
    _remove_spines(ax)


def chart6a_oos_equity_combined():
    """Variant A: both arms on one axes, hue-paired (blue arm / orange arm)."""
    qqq = _load_arm("QQQ")
    spy = _load_arm("SPY")

    fig, ax = plt.subplots(figsize=(12, 5.8))

    ax.plot(qqq["dates"], qqq["strat"], color=NAVY, linewidth=2.8,
            label="Strategy (QQQ arm)", zorder=5)
    ax.plot(qqq["dates"], qqq["bench"], color=LIGHT_BLUE, linewidth=1.6,
            linestyle="--", label="QQQ (Buy & Hold)", zorder=3)
    ax.plot(spy["dates"], spy["strat"], color=MUTED_ORANGE, linewidth=2.8,
            label="Strategy (SPY arm)", zorder=4)
    ax.plot(spy["dates"], spy["bench"], color=PALE_TAN, linewidth=1.6,
            linestyle="--", label="SPY (Buy & Hold)", zorder=2)

    ax.set_title("Walk-Forward CEM Monetization: Out-of-Sample Equity Curves",
                 fontsize=14, fontweight="bold", pad=12)
    ax.set_ylabel("Portfolio Equity", fontsize=12)
    _format_equity_axis(ax)

    # Two-column KPI box — top-left is empty (equity rises left-to-right)
    kpi_text = (
        f"OOS Period: {qqq['period']}\n"
        f"            QQQ arm  SPY arm\n"
        f"Strategy    {qqq['s']['ret']:+7.2f}%  {spy['s']['ret']:+6.2f}%\n"
        f"Benchmark   {qqq['b']['ret']:+7.2f}%  {spy['b']['ret']:+6.2f}%\n"
        f"Sharpe      {qqq['s']['sharpe']:7.2f}   {spy['s']['sharpe']:6.2f}\n"
        f"Max DD      {qqq['s']['max_dd']:7.2f}%  {spy['s']['max_dd']:6.2f}%"
    )
    ax.text(0.03, 0.95, kpi_text, transform=ax.transAxes,
            fontsize=9.5, va="top", ha="left", color=NAVY,
            fontfamily="monospace",
            bbox=dict(boxstyle="round,pad=0.5", facecolor="white",
                      edgecolor=NAVY, linewidth=1.5, alpha=0.95))

    ax.legend(loc="lower right", fontsize=10, framealpha=0.95,
              edgecolor=DARK_GREY)

    fig.tight_layout()
    out = OUTPUT_DIR / "chart6a_oos_equity_combined.png"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


def chart6b_oos_equity_side_by_side():
    """Variant B: one panel per arm — navy strategy vs orange dashed B&H,
    green excess-return shading, per-panel KPI box."""
    arms = [_load_arm("QQQ"), _load_arm("SPY")]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2), sharey=True)

    for ax, arm in zip(axes, arms):
        b = arm["benchmark"]
        ax.plot(arm["dates"], arm["strat"], color=NAVY, linewidth=2.8,
                label="Strategy Equity", zorder=4)
        ax.plot(arm["dates"], arm["bench"], color=MUTED_ORANGE, linewidth=1.8,
                linestyle="--", label=f"{b} (Buy & Hold)", zorder=3)
        ax.fill_between(arm["dates"], arm["bench"], arm["strat"],
                        where=(arm["strat"] > arm["bench"]).values,
                        interpolate=True, color=ACCENT_GREEN, alpha=0.15,
                        linewidth=0, zorder=2, label="Excess return")

        ax.set_title(f"vs. {b} (Buy & Hold)", fontsize=12, fontweight="bold")
        _format_equity_axis(ax)

        # Strategy-vs-benchmark KPI table + CAPM alpha (honest p-value)
        s, bn, capm = arm["s"], arm["b"], arm["capm"]

        def _row(label, sv, bv):
            return f"{label:<8}{sv:>10}{bv:>10}"

        # Descriptive realized outcomes only — no inference claims in the box.
        # The CAPM alpha estimate is NOT significant at n=111 days, so it
        # lives in the footnote as a stated limitation, not up here as a KPI.
        kpi_text = "\n".join([
            _row("", "Strategy", f"{b} B&H"),
            _row("Return", f"{s['ret']:+.2f}%", f"{bn['ret']:+.2f}%"),
            _row("PnL", f"${s['pnl'] / 1000:+,.1f}K", f"${bn['pnl'] / 1000:+,.1f}K"),
            _row("Sharpe", f"{s['sharpe']:.2f}", f"{bn['sharpe']:.2f}"),
            _row("Sortino", f"{s['sortino']:.2f}", f"{bn['sortino']:.2f}"),
            _row("Max DD", f"{s['max_dd']:.2f}%", f"{bn['max_dd']:.2f}%"),
            f"Excess vs {b} B&H: {s['ret'] - bn['ret']:+.2f}pp",
        ])
        # parse_math=False: the "$" in the PnL row must render literally,
        # not open a mathtext span (which eats spacing and italicizes)
        ax.text(0.04, 0.95, kpi_text, transform=ax.transAxes,
                fontsize=9, va="top", ha="left", color=NAVY,
                fontfamily="monospace", parse_math=False,
                bbox=dict(boxstyle="round,pad=0.4", facecolor="white",
                          edgecolor=NAVY, linewidth=1.4, alpha=0.95))
        ax.legend(loc="lower right", fontsize=8.5, framealpha=0.95,
                  edgecolor=DARK_GREY)

    axes[0].set_ylabel("Portfolio Equity", fontsize=12)
    fig.suptitle(
        "Walk-Forward CEM Monetization: Out-of-Sample Equity Curves "
        f"({arms[0]['period']})",
        fontsize=14, fontweight="bold")

    fig.tight_layout(rect=(0, 0, 1, 0.96))

    # Limitation footnote — mirrors statistical_tests.py's own framing: the
    # portfolio-level alpha is an honesty check, not the paper's headline
    # claim, and at n=111 days it is NOT statistically significant. All boxed
    # figures are realized, descriptive outcomes of a single OOS window.
    qqq_capm, spy_capm = arms[0]["capm"], arms[1]["capm"]
    fig.text(0.5, -0.015,
             "All figures are realized outcomes of a single out-of-sample window "
             f"(n = {qqq_capm['n_days']} trading days). CAPM α is positive but not "
             f"statistically significant at this sample size "
             f"(p = {qqq_capm['p_value']:.2f} vs QQQ, {spy_capm['p_value']:.2f} vs SPY); "
             "the statistically validated result is the trade-level expectancy "
             "(Chart 4: N = 890, p < 0.001).",
             ha="center", va="top", fontsize=8.5, color=DARK_GREY,
             style="italic")

    out = OUTPUT_DIR / "chart6b_oos_equity_side_by_side.png"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ═════════════════════════════════════════════════════════════════════════════

def main():
    _setup()
    print("Generating paper charts...")
    chart1_paradigm_shift()
    chart2_pipeline()
    chart3_timeline()
    chart4_expectancy()
    chart5_tail_trimming()
    chart6a_oos_equity_combined()
    chart6b_oos_equity_side_by_side()
    print(f"\nAll charts saved to {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
