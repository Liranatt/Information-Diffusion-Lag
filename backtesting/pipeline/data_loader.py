"""Database loaders that feed the candidate feature build.

The pure feature math (`compute_features`, `find_t_theta`, the lookbacks, the
splits) now lives in `core.features` — the single definition shared by every
consumer. This module only knows how to pull rows out of Postgres and hand them
to that math.
"""
from __future__ import annotations

import asyncio

import pandas as pd

from database.db_connection import connect
from database.backtesting.schema import SCHEMA
from core.features import (
    SECTOR_ETFS,
    THETA_THRESHOLD,
    TRAIN_FRACTION,
    VAL_FRACTION,
    add_rank_features,
    assign_chronological_splits,
    compute_features,
    find_t_theta,
)


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
