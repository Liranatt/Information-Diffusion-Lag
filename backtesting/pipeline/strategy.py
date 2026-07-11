"""Backtest research helpers on top of the shared kernel.

The trade semantics (entry/exit, the numba fast path, the Python reference) now
live in `core.kernel`; the policy space lives in
`core.policy`. This module keeps only the batch-backtest conveniences that are
specific to the research plane, and re-exports the core names that existing
call sites (optimize_cem, the invariants test) import from here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.policy import DEFAULT_POLICY, CEM_BOUNDS, RELEVANCE_COL
from core.kernel import (
    HAVE_NUMBA,
    _simulate_one_py,
    calc_atr,
    clear_kernel_caches,
    entry_day,
    scan_candidate,
    simulate_one,
)

__all__ = [
    "DEFAULT_POLICY", "CEM_BOUNDS", "RELEVANCE_COL", "HAVE_NUMBA",
    "calc_atr", "clear_kernel_caches", "entry_day",
    "scan_candidate", "simulate_one", "_simulate_one_py",
    "run_backtest", "policy_from_vector",
    "score_sharpe_per_day", "score_mean_return",
]


def run_backtest(
    df: pd.DataFrame,
    prices: dict[str, list[tuple]],
    probs: dict[str, list[tuple]],
    policy: dict,
    split_filter: str | None = None,
) -> pd.DataFrame:
    """Run the full backtest for a given policy."""
    subset = df if split_filter is None else df[df["split"] == split_filter]
    trades = [
        t for t in (simulate_one(r, prices, probs, policy) for _, r in subset.iterrows())
        if t is not None
    ]
    return pd.DataFrame(trades) if trades else pd.DataFrame()


def policy_from_vector(vec: np.ndarray) -> dict:
    """Convert CEM sample vector to clipped policy dict."""
    names = list(CEM_BOUNDS.keys())
    p = {}
    for i, name in enumerate(names):
        lo, hi = CEM_BOUNDS[name]
        p[name] = float(np.clip(vec[i], lo, hi))
    p["hold_days"] = int(round(p["hold_days"]))
    if p["enter_strong"] < p["enter_floor"]:
        p["enter_strong"] = p["enter_floor"]
    return p


def score_sharpe_per_day(tdf: pd.DataFrame) -> float:
    """Annualised Sharpe from per-trade daily returns."""
    if len(tdf) < 3:
        return -999.0
    entry = pd.to_datetime(tdf["entry_date"])
    exit_ = pd.to_datetime(tdf["exit_date"])
    days = (exit_ - entry).dt.days.clip(lower=1)
    daily_ret = tdf["return_pct"].values / days.values
    mu = daily_ret.mean()
    sigma = daily_ret.std()
    if sigma < 1e-9:
        return -999.0
    return float(mu / sigma * np.sqrt(252))


def score_mean_return(tdf: pd.DataFrame) -> float:
    if tdf.empty:
        return -999.0
    return float(tdf["return_pct"].mean())
