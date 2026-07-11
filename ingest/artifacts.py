"""Rebuild the three committed backtest artifacts from Postgres.

    data/candidates.parquet   (question, symbol) candidates + features + split
    data/prices.pkl           {symbol: [(ts, high, low, close)]}  incl SPY/QQQ
    data/probs.pkl            {market_id: [(ts, probability)]}     daily closes

This is the reproducible source of the artifacts the DB-decoupled backtest reads.
It builds purely from the DB — the old builder seeded from a `candidates_old.parquet`
that no longer exists, which is why the artifacts had become unreproducible.

The candidate features come from the single `core.features` definitions (via
`data_loader.build_dataset_from_db`). The split is the fixed OOS boundary
(t_theta < 2026-01-01 => train, else test); optimize_cem re-derives the actual
train/OOS partition from that same boundary, so the column is a label.

NOTE: legacy fundamentals / LLM columns that were present in the old parquet
(feat_beta, feat_llm_confidence, ...) came from a separate fundamentals pipeline
and the now-deleted RandomForest path; they are not part of the CEM backtest and
are intentionally not reproduced here.
"""
from __future__ import annotations

import asyncio
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from database.db_connection import connect
from database.backtesting.schema import SCHEMA
from backtesting.pipeline.data_loader import build_dataset_from_db

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PARQUET = DATA_DIR / "candidates.parquet"
PRICES_PKL = DATA_DIR / "prices.pkl"
PROBS_PKL = DATA_DIR / "probs.pkl"

OOS_START = pd.Timestamp("2026-01-01", tz="UTC")
RELEVANCE_FLOOR = 0.5


async def _load_hlc_paths(symbols: list[str], market_ids: list[str]) -> tuple[dict, dict]:
    """{symbol: [(ts,h,l,c)]} (incl SPY/QQQ) and {market_id: [(ts,prob)]} daily."""
    conn = await connect()
    try:
        bars = await conn.fetch(
            f"""SELECT symbol, ts, high, low, close FROM {SCHEMA}.historical_price_bars
                WHERE resolution='1d' AND symbol=ANY($1::text[]) ORDER BY symbol, ts""",
            sorted(set(symbols) | {"SPY", "QQQ"}),
        )
        prob_rows = await conn.fetch(
            f"""SELECT DISTINCT ON (market_id, (hour_ts AT TIME ZONE 'UTC')::date)
                market_id, (hour_ts AT TIME ZONE 'UTC')::date AS d, probability
                FROM {SCHEMA}.historical_probability_points
                WHERE market_id=ANY($1::text[])
                  AND EXTRACT(HOUR FROM hour_ts AT TIME ZONE 'UTC') <= 20
                ORDER BY market_id, (hour_ts AT TIME ZONE 'UTC')::date, hour_ts DESC""",
            sorted(set(market_ids)),
        )
    finally:
        await conn.close()

    prices: dict[str, list[tuple]] = {}
    for b in bars:
        prices.setdefault(b["symbol"], []).append((
            pd.Timestamp(b["ts"]).tz_convert("UTC").normalize(),
            float(b["high"]), float(b["low"]), float(b["close"]),
        ))
    probs: dict[str, list[tuple]] = {}
    for p in prob_rows:
        probs.setdefault(p["market_id"], []).append((
            pd.Timestamp(p["d"]).tz_localize("UTC"), float(p["probability"]),
        ))
    for d in (prices, probs):
        for k in d:
            d[k].sort(key=lambda x: x[0])
    return prices, probs


async def rebuild() -> pd.DataFrame:
    """Rebuild candidates.parquet + prices.pkl + probs.pkl from the DB."""
    df = await build_dataset_from_db(relevance_floor=RELEVANCE_FLOOR, output_path=None)
    if df.empty:
        raise RuntimeError("build_dataset_from_db returned no candidates; nothing to write.")

    df["split"] = np.where(
        pd.to_datetime(df["t_theta"], utc=True) < OOS_START, "train", "test"
    )

    DATA_DIR.mkdir(exist_ok=True)
    df.to_parquet(PARQUET, engine="pyarrow", compression="snappy")
    n_train = int((df["split"] == "train").sum())
    n_test = int((df["split"] == "test").sum())
    print(f"[artifacts] candidates.parquet: {len(df)} rows (train={n_train}, test={n_test})")

    prices, probs = await _load_hlc_paths(
        df["symbol"].astype(str).unique().tolist(),
        df["market_id"].astype(str).unique().tolist(),
    )
    with open(PRICES_PKL, "wb") as f:
        pickle.dump(prices, f)
    with open(PROBS_PKL, "wb") as f:
        pickle.dump(probs, f)
    print(f"[artifacts] prices.pkl: {len(prices)} symbols   probs.pkl: {len(probs)} markets")
    return df


if __name__ == "__main__":
    asyncio.run(rebuild())
