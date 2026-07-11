"""Pure feature math shared by the candidate builder and any consumer.

Moved out of `backtesting.pipeline.data_loader` so there is exactly one
definition of every `feat_*`. No database, no network — just numpy/pandas.
The DB loaders that feed these functions live in the ingest layer.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

NUM_FEATURES = [
    "feat_prob_at_trigger", "feat_prob_slope_24h", "feat_prob_volatility",
    "feat_prob_surge_since_t0", "feat_time_to_resolution_days",
    "feat_has_pre_crossing_history",
    "feat_crossing_latency_days", "feat_pre_entry_volume_log",
    "feat_runup_since_t0", "feat_asset_2w_trend", "feat_sector_1m_trend",
    "feat_spy_2w_trend", "feat_ytd_change",
    "feat_connection_strength", "feat_world_size",
    "feat_runup_rank",
]
CAT_FEATURES = ["feat_archetype", "feat_sector"]

NUM_FEATURES_LEAN = [
    "feat_asset_2w_trend",
    "feat_time_to_resolution_days",
    "feat_spy_2w_trend",
    "feat_prob_at_trigger",
]
CAT_FEATURES_LEAN: list[str] = []
TARGET = "asset_return"
# Universe pre-filter: candidates only exist after the first crossing of this
# fixed theta. CEM tunes entry after this filter; it does not calibrate theta.
THETA_THRESHOLD = 0.55
TRAIN_FRACTION = 0.60
VAL_FRACTION = 0.10

SECTOR_ETFS = {
    "Basic Materials": "XLB", "Communication Services": "XLC",
    "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP",
    "Energy": "XLE", "Financial Services": "XLF",
    "Healthcare": "XLV", "Industrials": "XLI",
    "Real Estate": "XLRE", "Technology": "XLK",
    "Utilities": "XLU",
}


def _finite(v) -> float | None:
    if v is None:
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _safe_div(a, b) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return float(a) / float(b)


def _trend(bars: list[tuple], t: pd.Timestamp, days: int) -> float | None:
    """Return % change in close price over `days` ending at `t`."""
    start = t - pd.Timedelta(days=days)
    c_end = next((c for ts, c in reversed(bars) if ts <= t), None)
    c_start = next((c for ts, c in reversed(bars) if ts <= start), None)
    if c_end and c_start and c_start > 0:
        return c_end / c_start - 1.0
    return None


def _prob_slope_24h(pts: list[tuple], t_theta: pd.Timestamp) -> float | None:
    """Probability change over 24h ending at t_theta."""
    p_now = next((p for t, p in reversed(pts) if t <= t_theta), None)
    t_24h = t_theta - pd.Timedelta(hours=24)
    p_prev = next((p for t, p in reversed(pts) if t <= t_24h), None)
    if p_now is not None and p_prev is not None:
        return p_now - p_prev
    return None


def _prob_volatility(pts: list[tuple], t_theta: pd.Timestamp, window_days: int = 7) -> float | None:
    """Std dev of probabilities over window ending at t_theta."""
    start = t_theta - pd.Timedelta(days=window_days)
    window = [p for t, p in pts if start <= t <= t_theta]
    if len(window) < 3:
        return None
    return float(np.std(window))


def find_t_theta(pts: list[tuple], threshold: float = THETA_THRESHOLD) -> pd.Timestamp | None:
    """Find first timestamp where probability >= threshold."""
    for t, p in pts:
        if p >= threshold:
            return t
    return None


def compute_features(
    market_id: str,
    event_id: str,
    symbol: str,
    question: str,
    archetype: str,
    relevance: float,
    world_size: int,
    t0: pd.Timestamp,
    t_e: pd.Timestamp,
    t_theta: pd.Timestamp,
    prices: list[tuple],
    probs: list[tuple],
    spy_prices: list[tuple],
    sector_etf_prices: list[tuple],
    sector: str,
) -> dict | None:
    """Compute all features for one (market, symbol) pair."""
    if not prices or not probs:
        return None

    p_t0 = probs[0][1] if probs else 0.5
    p_theta = next((p for t, p in probs if t >= t_theta), 0.55)

    bar_theta = next((c for t, c in reversed(prices) if t <= t_theta), None)
    if not bar_theta:
        return None
    # Baseline close at T0 (= market creation) for the genuine since-T0 run-up.
    bar_t0 = next((c for t, c in reversed(prices) if t <= t0), None) or bar_theta

    # Compute asset_return: close at t_e / close at t_theta - 1
    bar_end = next((c for t, c in reversed(prices) if t <= t_e), None)
    asset_return = (bar_end / bar_theta - 1.0) * 100 if bar_end and bar_theta else 0.0

    rec = {
        "event_id": event_id,
        "market_id": market_id,
        "symbol": symbol,
        "question": question,
        "t0": t0,
        "t_theta": t_theta,
        "t_e": t_e,
        "entry_price": bar_theta,

        "feat_archetype": archetype,
        "feat_sector": sector or "Unknown",
        "feat_prob_at_trigger": p_theta,
        "feat_prob_slope_24h": _finite(_prob_slope_24h(probs, t_theta)) or 0.0,
        "feat_prob_volatility": _finite(_prob_volatility(probs, t_theta)) or 0.0,
        # Genuine change since T0 (= market creation), not a fixed lookback: the
        # probability rise from creation to the theta crossing.
        "feat_prob_surge_since_t0": _finite(p_theta - p_t0) or 0.0,
        "feat_has_pre_crossing_history": (p_theta - p_t0) > 0.0,
        "feat_time_to_resolution_days": (t_e - t_theta).total_seconds() / 86400,
        "feat_crossing_latency_days": (t_theta - t0).total_seconds() / 86400,
        "feat_pre_entry_volume_log": 0.0,  # volume data not always available
        # Genuine asset run-up since T0: close at the theta crossing vs close at
        # creation, not a fixed 20-bar lookback.
        "feat_runup_since_t0": _finite(bar_theta / bar_t0 - 1.0) if bar_t0 else 0.0,
        "feat_asset_2w_trend": _finite(_trend(prices, t_theta, 14)) or 0.0,
        "feat_sector_1m_trend": _finite(_trend(sector_etf_prices, t_theta, 30)) or 0.0,
        "feat_spy_2w_trend": _finite(_trend(spy_prices, t_theta, 14)) or 0.0,
        "feat_ytd_change": _finite(_trend(prices, t_theta, 365)) or 0.0,
        "feat_connection_strength": relevance,
        "feat_world_size": world_size,
        "feat_runup_rank": 0.5,  # filled later via groupby
        TARGET: round(asset_return, 4),
    }
    return rec


def add_rank_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add cross-sectional rank features within each event cohort."""
    out = df.copy()
    cohort = ["event_id", "market_id"]
    if "feat_runup_since_t0" in out.columns:
        out["feat_runup_rank"] = out.groupby(cohort)["feat_runup_since_t0"].rank(
            pct=True, method="average"
        )
    return out


def assign_chronological_splits(
    df: pd.DataFrame,
    *,
    train_fraction: float = TRAIN_FRACTION,
    val_fraction: float = VAL_FRACTION,
) -> pd.DataFrame:
    """Assign deterministic 60/10/30 train/val/test labels by candidate order."""
    if df.empty:
        return df.copy()
    if not (0.0 < train_fraction < 1.0 and 0.0 < val_fraction < 1.0):
        raise ValueError("train_fraction and val_fraction must be between 0 and 1.")
    if train_fraction + val_fraction >= 1.0:
        raise ValueError("train_fraction + val_fraction must leave a positive test fraction.")

    out = df.copy()
    order_cols = ["t_theta", "t_e", "event_id", "market_id", "symbol"]
    order_cols = [col for col in order_cols if col in out.columns]
    ordered_idx = out.sort_values(order_cols, kind="mergesort").index
    n = len(out)
    n_train = int(n * train_fraction)
    n_val = int(n * val_fraction)
    n_test = n - n_train - n_val
    if min(n_train, n_val, n_test) <= 0:
        raise ValueError(
            f"Not enough candidates for 60/10/30 split: "
            f"train={n_train}, val={n_val}, test={n_test}."
        )

    out["split"] = "test"
    out.loc[ordered_idx[:n_train], "split"] = "train"
    out.loc[ordered_idx[n_train:n_train + n_val], "split"] = "val"
    return out
