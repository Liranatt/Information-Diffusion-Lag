"""Compute features for the strategy and RF model.

Downloads price and probability data, then computes all 21 numerical + 2
categorical features that liran_strategy.py expects.
"""
from __future__ import annotations

import asyncio
import math

import numpy as np
import pandas as pd

from database.db_connection import connect
from database.backtesting.schema import SCHEMA

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


def _prob_change_lookback(
    pts: list[tuple],
    t_theta: pd.Timestamp,
    days: int = 7,
) -> float | None:
    """Probability change over a fixed lookback ending at t_theta."""
    p_now = next((p for t, p in reversed(pts) if t <= t_theta), None)
    t_prev = t_theta - pd.Timedelta(days=days)
    p_prev = next((p for t, p in reversed(pts) if t <= t_prev), None)
    if p_now is not None and p_prev is not None:
        return p_now - p_prev
    return None


def _trading_return_lookback(
    bars: list[tuple],
    t: pd.Timestamp,
    lookback_bars: int = 20,
) -> float | None:
    """Return over a fixed number of completed trading bars ending at t."""
    closes = [(ts, c) for ts, c in bars if ts <= t]
    if len(closes) <= lookback_bars:
        return None
    c_end = closes[-1][1]
    c_start = closes[-(lookback_bars + 1)][1]
    if c_end and c_start and c_start > 0:
        return c_end / c_start - 1.0
    return None


async def load_prices_from_db(symbols: list[str]) -> dict[str, list[tuple]]:
    """Load daily close prices from DB. Returns {symbol: [(ts, close), ...]}."""
    conn = await connect()
    try:
        rows = await conn.fetch(
            f"SELECT symbol, ts, high, low, close FROM {SCHEMA}.historical_price_bars "
            f"WHERE resolution='1d' AND symbol=ANY($1::text[]) ORDER BY symbol, ts",
            symbols,
        )
    finally:
        await conn.close()
    out: dict[str, list[tuple]] = {}
    for r in rows:
        out.setdefault(r["symbol"], []).append((
            pd.Timestamp(r["ts"]).tz_convert("UTC").normalize(),
            float(r["close"]),
        ))
    return out


async def load_probs_from_db(market_ids: list[str]) -> dict[str, list[tuple]]:
    """Load hourly probability points from DB. Returns {market_id: [(ts, prob), ...]}."""
    conn = await connect()
    try:
        rows = await conn.fetch(
            f"""SELECT DISTINCT ON (market_id, (hour_ts AT TIME ZONE 'UTC')::date)
                market_id, (hour_ts AT TIME ZONE 'UTC')::date AS d, probability
                FROM {SCHEMA}.historical_probability_points
                WHERE market_id=ANY($1::text[])
                  AND EXTRACT(HOUR FROM hour_ts AT TIME ZONE 'UTC') <= 20
                ORDER BY market_id, (hour_ts AT TIME ZONE 'UTC')::date, hour_ts DESC""",
            market_ids,
        )
    finally:
        await conn.close()
    out: dict[str, list[tuple]] = {}
    for r in rows:
        out.setdefault(r["market_id"], []).append((
            pd.Timestamp(r["d"]).tz_localize("UTC"),
            float(r["probability"]),
        ))
    for k in out:
        out[k].sort()
    return out


async def load_metadata(symbols: list[str]) -> dict[str, dict]:
    """Load asset metadata (sector etc) from DB."""
    conn = await connect()
    try:
        rows = await conn.fetch(
            f"SELECT symbol, sector, sector_etf FROM {SCHEMA}.historical_asset_metadata "
            f"WHERE symbol=ANY($1::text[])",
            symbols,
        )
    finally:
        await conn.close()
    return {r["symbol"]: dict(r) for r in rows}


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
        "feat_prob_surge_since_t0": _finite(_prob_change_lookback(probs, t_theta, 7)) or 0.0,
        "feat_has_pre_crossing_history": (p_theta - p_t0) > 0.0,
        "feat_time_to_resolution_days": (t_e - t_theta).total_seconds() / 86400,
        "feat_crossing_latency_days": (t_theta - t0).total_seconds() / 86400,
        "feat_pre_entry_volume_log": 0.0,  # volume data not always available
        "feat_runup_since_t0": _finite(_trading_return_lookback(prices, t_theta, 20)) or 0.0,
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


async def build_dataset_from_db(
    relevance_floor: float = 0.5,
    output_path: str | None = None,
) -> pd.DataFrame:
    """Build the full candidate dataset from database tables.

    This is the backtesting data loader — it reads from the existing DB tables
    (worlds, prices, probabilities, fundamentals) and computes all features.
    """
    conn = await connect()
    try:
        world_rows = await conn.fetch(f"""
            SELECT w.market_id, w.event_id, w.universe_name,
                   a.symbol, a.connection_strength,
                   (SELECT COUNT(*) FROM {SCHEMA}.historical_asset_world_assets
                    WHERE world_id = w.world_id) AS world_size
            FROM {SCHEMA}.historical_asset_worlds w
            JOIN {SCHEMA}.historical_asset_world_assets a ON a.world_id = w.world_id
            WHERE a.connection_strength IS NOT NULL
              AND a.connection_strength >= $1
        """, relevance_floor)
    finally:
        await conn.close()

    if not world_rows:
        print("[data_loader] no candidates found in DB")
        return pd.DataFrame()

    candidates = []
    for r in world_rows:
        candidates.append({
            "market_id": r["market_id"],
            "event_id": r["event_id"],
            "symbol": r["symbol"],
            "archetype": r["universe_name"],
            "relevance": float(r["connection_strength"]),
            "world_size": int(r["world_size"]),
        })
    df_cands = pd.DataFrame(candidates).drop_duplicates(subset=["market_id", "symbol"])

    # Load market dates from probability coverage + market decisions
    conn = await connect()
    try:
        date_rows = await conn.fetch(f"""
            SELECT pc.market_id, pc.requested_start, pc.requested_end,
                   md.market_question AS question
            FROM {SCHEMA}.historical_probability_coverage pc
            LEFT JOIN {SCHEMA}.historical_market_decisions md
                ON md.market_id = pc.market_id
            WHERE pc.market_id = ANY($1::text[])
        """, df_cands["market_id"].unique().tolist())
    finally:
        await conn.close()
    dates = {r["market_id"]: r for r in date_rows}

    symbols = df_cands["symbol"].unique().tolist()
    market_ids = df_cands["market_id"].unique().tolist()

    prices, probs, meta = await asyncio.gather(
        load_prices_from_db(symbols + ["SPY"]),
        load_probs_from_db(market_ids),
        load_metadata(symbols),
    )

    spy_prices = prices.get("SPY", [])

    records = []
    for _, row in df_cands.iterrows():
        mid = row["market_id"]
        sym = row["symbol"]
        date_info = dates.get(mid)
        if not date_info:
            continue

        t0 = pd.Timestamp(date_info["requested_start"]).tz_convert("UTC")
        t_e = pd.Timestamp(date_info["requested_end"]).tz_convert("UTC")
        question = date_info.get("question", "")

        mkt_probs = probs.get(mid, [])
        t_theta = find_t_theta(mkt_probs)
        if t_theta is None:
            continue

        sym_meta = meta.get(sym, {})
        sector = sym_meta.get("sector", "Unknown")
        sector_etf = sym_meta.get("sector_etf") or SECTOR_ETFS.get(sector, "SPY")
        sector_prices = prices.get(sector_etf, spy_prices)

        rec = compute_features(
            market_id=mid, event_id=row["event_id"], symbol=sym,
            question=question, archetype=row["archetype"],
            relevance=row["relevance"], world_size=row["world_size"],
            t0=t0, t_e=t_e, t_theta=t_theta,
            prices=prices.get(sym, []), probs=mkt_probs,
            spy_prices=spy_prices, sector_etf_prices=sector_prices,
            sector=sector,
        )
        if rec is None:
            continue
        records.append(rec)

    df = pd.DataFrame(records)
    if not df.empty:
        df = add_rank_features(df)
        df = assign_chronological_splits(df)
    print(f"[data_loader] built {len(df)} candidates from DB")

    if output_path and not df.empty:
        df.to_parquet(output_path, engine="pyarrow", compression="snappy")
        print(f"[data_loader] saved to {output_path}")
    return df

async def load_price_prob_paths(df: pd.DataFrame):
    """Load daily bars and probability paths from DB."""
    c = await connect()
    try:
        syms = sorted(df["symbol"].unique())
        mkts = sorted(df["market_id"].unique())
        bars = await c.fetch(
            f"SELECT symbol, ts, high, low, close FROM {SCHEMA}.historical_price_bars "
            f"WHERE resolution='1d' AND symbol=ANY($1::text[]) ORDER BY symbol, ts",
            syms,
        )
        prob_rows = await c.fetch(
            f"""SELECT DISTINCT ON (market_id, (hour_ts AT TIME ZONE 'UTC')::date)
                market_id, (hour_ts AT TIME ZONE 'UTC')::date AS d, probability
                FROM {SCHEMA}.historical_probability_points
                WHERE market_id=ANY($1::text[])
                  AND EXTRACT(HOUR FROM hour_ts AT TIME ZONE 'UTC') <= 20
                ORDER BY market_id, (hour_ts AT TIME ZONE 'UTC')::date, hour_ts DESC""",
            mkts,
        )
    finally:
        await c.close()

    prices: dict[str, list[tuple]] = {}
    for b in bars:
        prices.setdefault(b["symbol"], []).append((
            pd.Timestamp(b["ts"]).tz_convert("UTC").normalize(),
            float(b["high"]), float(b["low"]), float(b["close"]),
        ))

    probs: dict[str, list[tuple]] = {}
    for p in prob_rows:
        probs.setdefault(p["market_id"], []).append((
            pd.Timestamp(p["d"]).tz_localize("UTC"),
            float(p["probability"]),
        ))

    for d in (prices, probs):
        for k in d:
            d[k].sort()
    return prices, probs
