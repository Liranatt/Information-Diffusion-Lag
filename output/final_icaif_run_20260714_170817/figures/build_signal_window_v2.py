"""Rebuild the H1 signal-window schematic (v2).

Distinguishes, on schematic (non-data) paths:
  T_theta   - first crossing of the fixed 0.55 screening threshold
  T_entry   - policy-accepted entry close (strong threshold or floor + confirmation)
  [T_entry, T_e-1] - the H1 holding interval
  T_e-1     - last eligible equity close before the endpoint (exit)
  T_e       - scheduled contract endpoint stored in the research dataset
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = Path(__file__).resolve().parent
PAPER = OUT.parent / "paper"

GRAY = "#6B7280"
BLUE = "#2563EB"
GREEN = "#059669"
ORANGE = "#D97706"

x = np.linspace(0, 10, 400)
# Schematic favorable-side probability path: drifts up, crosses 0.55 at x=3,
# dips, then confirms above the policy floor and is accepted at x=4.6.
prob = 0.38 + 0.30 / (1 + np.exp(-(x - 3.1) * 1.8)) + 0.012 * np.sin(2.4 * x) + 0.10 / (1 + np.exp(-(x - 4.4) * 2.2))
prob = np.clip(prob, 0, 0.95)

# Schematic equity closes.
eq = 100 + 1.1 * x + 1.6 * np.sin(1.1 * x) + 2.4 / (1 + np.exp(-(x - 5.2) * 1.4))

T_THETA = 3.0
T_ENTRY = 4.6
T_EM1 = 8.6
T_E = 9.4

fig, (ax1, ax2) = plt.subplots(
    2, 1, figsize=(10.6, 3.9), sharex=True,
    gridspec_kw={"height_ratios": [1, 1], "hspace": 0.12},
)

# ── Top: probability lane ────────────────────────────────────────────────────
ax1.plot(x, prob, color=BLUE, lw=1.8)
ax1.axhline(0.55, color=GRAY, lw=0.9, ls="--")
ax1.text(0.15, 0.565, "fixed screening threshold 0.55", fontsize=8, color=GRAY, va="bottom")
ax1.axhline(0.70, color=ORANGE, lw=0.9, ls=":")
ax1.text(0.15, 0.715, "policy entry threshold (CEM walk-forward schedule)",
         fontsize=8, color=ORANGE, va="bottom")
p_theta = float(np.interp(T_THETA, x, prob))
p_entry = float(np.interp(T_ENTRY, x, prob))
ax1.scatter([T_THETA], [p_theta], s=42, color=GRAY, zorder=5)
ax1.scatter([T_ENTRY], [p_entry], s=48, color=ORANGE, zorder=5)
ax1.annotate(r"$T_\theta$: first 0.55 cross" + "\n(candidate created)",
             xy=(T_THETA, p_theta), xytext=(1.15, 0.86), fontsize=8.5,
             arrowprops=dict(arrowstyle="->", color=GRAY, lw=0.9), color="#111827")
ax1.annotate(r"$T_{entry}$: policy accepts" + "\n(confirmation satisfied)",
             xy=(T_ENTRY, p_entry), xytext=(4.9, 0.40), fontsize=8.5,
             arrowprops=dict(arrowstyle="->", color=ORANGE, lw=0.9), color="#111827")
ax1.set_ylabel("favorable-side\nprobability", fontsize=8.5)
ax1.set_ylim(0.30, 1.0)
ax1.tick_params(labelsize=8)

# ── Bottom: equity lane ──────────────────────────────────────────────────────
ax2.plot(x, eq, color=GREEN, lw=1.8)
e_entry = float(np.interp(T_ENTRY, x, eq))
e_exit = float(np.interp(T_EM1, x, eq))
ax2.axvspan(T_ENTRY, T_EM1, color=GREEN, alpha=0.12)
ax2.scatter([T_ENTRY], [e_entry], s=48, color=GREEN, zorder=5)
ax2.scatter([T_EM1], [e_exit], s=48, color=GREEN, zorder=5, marker="s")
ax2.text((T_ENTRY + T_EM1) / 2, eq.min() + 0.6,
         r"H1 holding interval $[T_{entry},\,T_e{-}1]$" + "\n(entry close → last eligible close; no intervening stop)",
         fontsize=8.5, ha="center", color="#065F46")
ax2.annotate(r"$T_e{-}1$: exit at last eligible close",
             xy=(T_EM1, e_exit), xytext=(5.1, eq.max() + 0.4), fontsize=8.5,
             arrowprops=dict(arrowstyle="->", color=GREEN, lw=0.9), color="#111827")
ax2.set_ylim(eq.min() - 0.8, eq.max() + 1.6)
ax2.set_ylabel("mapped equity\nclose", fontsize=8.5)
ax2.set_xlabel("time (schematic; paths are illustrative, not data)", fontsize=8.5)
ax2.tick_params(labelsize=8)
ax2.set_xticks([])

# ── Shared vertical guides ───────────────────────────────────────────────────
for ax in (ax1, ax2):
    for xv, color in ((T_THETA, GRAY), (T_ENTRY, ORANGE), (T_EM1, GREEN), (T_E, "#B91C1C")):
        ax.axvline(xv, color=color, lw=0.8, ls="--", alpha=0.55)
    ax.spines[["top", "right"]].set_visible(False)
ax1.text(T_E, 0.99, r"$T_e$: scheduled endpoint stored" + "\nin the research dataset (no trading)",
         fontsize=8.5, ha="center", va="top", color="#B91C1C")

fig.align_ylabels((ax1, ax2))
fig.tight_layout()
for ext in ("pdf", "png"):
    fig.savefig(OUT / f"fig_signal_window_v2.{ext}", dpi=300, bbox_inches="tight")
fig.savefig(PAPER / "fig_signal_window_v2.pdf", bbox_inches="tight")
print("written:", OUT / "fig_signal_window_v2.pdf", "and paper copy")
