"""Honest performance statistics for the dashboard — both planes.

Self-contained (numpy/scipy) so the read-only dashboard never imports the heavy
research battery. The formulas mirror analysis/statistical_tests.py: annualized
Sharpe from daily equity returns, peak-to-trough max drawdown, a one-sided
t-test and an IID bootstrap CI on per-trade returns.

Respects the "no overstated significance" rule: `return_stats` reports `n` and a
`significant` flag, and callers must footnote (not headline) any result that is
not significant or is backed by too few trades.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import stats

MIN_TRADES_FOR_SIGNIFICANCE = 20
MIN_POINTS_FOR_SHARPE = 20


def daily_sharpe(equity: list[float]) -> float | None:
    """Annualized Sharpe from an equity curve's daily simple returns."""
    e = np.asarray(equity, dtype=float)
    e = e[np.isfinite(e)]
    if len(e) < MIN_POINTS_FOR_SHARPE + 1:
        return None
    r = np.diff(e) / e[:-1]
    r = r[np.isfinite(r)]
    if len(r) < MIN_POINTS_FOR_SHARPE:
        return None
    sd = r.std(ddof=1)
    if sd <= 1e-12:
        return None
    return float(r.mean() / sd * math.sqrt(252.0))


def max_drawdown_pct(equity: list[float]) -> float | None:
    """Worst peak-to-trough drawdown of an equity curve, in percent (<= 0)."""
    e = np.asarray(equity, dtype=float)
    e = e[np.isfinite(e)]
    if len(e) < 2:
        return None
    peak = np.maximum.accumulate(e)
    return float((e / peak - 1.0).min() * 100.0)


def _one_sided_t_greater(x: np.ndarray) -> tuple[float | None, float | None]:
    """t-stat and one-sided p-value for H1: mean(x) > 0."""
    if len(x) < 3 or x.std(ddof=1) <= 1e-12:
        return None, None
    t, p_two = stats.ttest_1samp(x, 0.0)
    p_one = p_two / 2.0 if t > 0 else 1.0 - p_two / 2.0
    return float(t), float(p_one)


def _bootstrap_mean_ci(x: np.ndarray, n_boot: int = 10000, alpha: float = 0.05,
                       seed: int = 0) -> tuple[float | None, float | None]:
    if len(x) < 3:
        return None, None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(x), size=(n_boot, len(x)))
    means = x[idx].mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2.0, 1.0 - alpha / 2.0])
    return float(lo), float(hi)


def return_stats(returns_pct: list[float]) -> dict:
    """Distribution + significance for a set of per-trade returns (in percent)."""
    x = np.asarray(returns_pct, dtype=float)
    x = x[np.isfinite(x)]
    n = int(len(x))
    if n == 0:
        return {"n": 0}
    t, p = _one_sided_t_greater(x)
    lo, hi = _bootstrap_mean_ci(x)
    wins = int((x > 0).sum())
    return {
        "n": n,
        "mean_pct": round(float(x.mean()), 3),
        "median_pct": round(float(np.median(x)), 3),
        "win_rate": round(wins / n * 100.0, 1),
        "t_stat": round(t, 3) if t is not None else None,
        "p_one_sided": round(p, 4) if p is not None else None,
        "ci_low": round(lo, 3) if lo is not None else None,
        "ci_high": round(hi, 3) if hi is not None else None,
        # Only claim significance with both a real p-value AND enough trades.
        "significant": bool(p is not None and p < 0.05 and n >= MIN_TRADES_FOR_SIGNIFICANCE),
        "underpowered": bool(n < MIN_TRADES_FOR_SIGNIFICANCE),
    }


def equity_stats(equity: list[float]) -> dict:
    """Sharpe + max drawdown for an equity curve."""
    return {
        "sharpe": (round(s, 2) if (s := daily_sharpe(equity)) is not None else None),
        "max_dd_pct": (round(d, 2) if (d := max_drawdown_pct(equity)) is not None else None),
    }
