"""Merge new pipeline-v1 + claude-pipeline-v1 candidates into old parquet.

Takes old parquet as base, adds only NEW questions from DB (pipeline-v1 and
claude-pipeline-v1). Matches by question text to avoid duplicates across
different market_id formats. Rebuilds probs.pkl and prices.pkl from DB.
"""
from __future__ import annotations

import asyncio
import json
import pickle
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from database.db_connection import connect
from database.backtesting.schema import SCHEMA
from pipeline.data_loader import (
    compute_features,
    add_rank_features,
    find_t_theta,
)
from pipeline.scanner import MIN_RESOLUTION_DAYS, MAX_RESOLUTION_DAYS

DATA_DIR = Path("data")
PROMPT_VERSIONS = ["claude-pipeline-v1", "pipeline-v1"]
OOS_START = pd.Timestamp("2026-01-01", tz="UTC")


async def main() -> None:
    # Load old parquet as base
    old_df = pd.read_parquet(DATA_DIR / "candidates_old.parquet")
    print(
        f"Old parquet: {len(old_df)} rows, {old_df['question'].nunique()} questions",
        flush=True,
    )

    # Fix splits: train if t_theta < 2026-01-01, test otherwise
    old_df["t_theta"] = pd.to_datetime(old_df["t_theta"], utc=True)
    old_df["t_e"] = pd.to_datetime(old_df["t_e"], utc=True)
    old_resolution_days = (old_df["t_e"] - old_df["t_theta"]).dt.total_seconds() / 86400
    old_window_mask = (
        (old_resolution_days >= MIN_RESOLUTION_DAYS)
        & (old_resolution_days <= MAX_RESOLUTION_DAYS)
    )
    skipped_old_resolution_window = int((~old_window_mask).sum())
    old_df = old_df.loc[old_window_mask].copy()
    print(
        f"Old parquet after {MIN_RESOLUTION_DAYS}-{MAX_RESOLUTION_DAYS}d gate: "
        f"{len(old_df)} rows (removed {skipped_old_resolution_window})",
        flush=True,
    )
    old_df["split"] = old_df["t_theta"].apply(
        lambda t: "train" if t < OOS_START else "test"
    )
    old_questions = set(old_df["question"].unique())

    # Load market metadata from both caches
    market_meta = {}
    for cache_file in ["markets_cache.json", "markets_cache_historical.json"]:
        p = DATA_DIR / cache_file
        if p.exists():
            for m in json.loads(p.read_text(encoding="utf-8")):
                market_meta.setdefault(m["market_id"], m)

    # Query DB for pipeline-v1 + claude-pipeline-v1 worlds
    conn = await connect()
    try:
        world_rows = await conn.fetch(f"""
            SELECT w.market_id, w.event_id, w.universe_name, w.prompt_version,
                   a.symbol, a.connection_strength,
                   (SELECT COUNT(*) FROM {SCHEMA}.historical_asset_world_assets
                    WHERE world_id = w.world_id) AS world_size
            FROM {SCHEMA}.historical_asset_worlds w
            JOIN {SCHEMA}.historical_asset_world_assets a ON a.world_id = w.world_id
            WHERE w.prompt_version = ANY($1::text[])
        """, PROMPT_VERSIONS)
    finally:
        await conn.close()

    # Filter to only questions NOT in old parquet
    new_candidates = []
    skipped_existing = 0
    skipped_no_meta = 0
    for r in world_rows:
        mid = r["market_id"]
        meta = market_meta.get(mid)
        if not meta:
            skipped_no_meta += 1
            continue
        if meta["question"] in old_questions:
            skipped_existing += 1
            continue
        new_candidates.append({
            "market_id": mid,
            "event_id": r["event_id"],
            "symbol": r["symbol"],
            "question": meta["question"],
            "archetype": r["universe_name"],
            "relevance": float(r["connection_strength"]),
            "world_size": int(r["world_size"]),
            "created_at": meta["created_at"],
            "end_at": meta["end_at"],
            "prompt_version": r["prompt_version"],
        })

    df_new = pd.DataFrame(new_candidates).drop_duplicates(subset=["market_id", "symbol"])
    new_questions = df_new["question"].nunique() if len(df_new) else 0
    print(f"New questions to add: {new_questions} ({len(df_new)} pairs)", flush=True)
    print(f"  Already in old parquet: {skipped_existing}", flush=True)
    print(f"  No metadata: {skipped_no_meta}", flush=True)

    if df_new.empty:
        print("Nothing new to add.")
        return

    # Collect ALL market_ids (old uses different IDs, so collect both)
    all_market_ids = sorted(
        set(old_df["market_id"].astype(str).tolist())
        | set(df_new["market_id"].astype(str).tolist())
    )
    all_symbols = sorted(
        set(old_df["symbol"].astype(str).tolist())
        | set(df_new["symbol"].astype(str).tolist())
        | {"SPY", "QQQ"}
    )

    # Load prob + price data from DB
    conn = await connect()
    try:
        prob_rows = await conn.fetch(f"""
            SELECT DISTINCT ON (market_id, (hour_ts AT TIME ZONE 'UTC')::date)
                   market_id,
                   (hour_ts AT TIME ZONE 'UTC')::date AS d,
                   probability
            FROM {SCHEMA}.historical_probability_points
            WHERE market_id = ANY($1::text[])
              AND EXTRACT(HOUR FROM hour_ts AT TIME ZONE 'UTC') <= 20
            ORDER BY market_id, (hour_ts AT TIME ZONE 'UTC')::date, hour_ts DESC
        """, all_market_ids)

        bar_rows = await conn.fetch(f"""
            SELECT symbol, ts, high, low, close
            FROM {SCHEMA}.historical_price_bars
            WHERE resolution = '1d'
              AND symbol = ANY($1::text[])
            ORDER BY symbol, ts
        """, all_symbols)
    finally:
        await conn.close()

    probs: dict[str, list[tuple]] = {}
    for r in prob_rows:
        probs.setdefault(r["market_id"], []).append((
            pd.Timestamp(r["d"]).tz_localize("UTC"),
            float(r["probability"]),
        ))
    for k in probs:
        probs[k].sort()

    prices_hlc: dict[str, list[tuple]] = {}
    prices_close: dict[str, list[tuple]] = {}
    for b in bar_rows:
        ts = pd.Timestamp(b["ts"]).tz_convert("UTC").normalize()
        prices_hlc.setdefault(b["symbol"], []).append(
            (ts, float(b["high"]), float(b["low"]), float(b["close"]))
        )
        prices_close.setdefault(b["symbol"], []).append((ts, float(b["close"])))
    for d in (prices_hlc, prices_close):
        for k in d:
            d[k].sort()

    print(f"Loaded: {len(probs)} prob series, {len(prices_hlc)} price series", flush=True)

    spy_prices = prices_close.get("SPY", [])

    # Compute features for new candidates
    records = []
    skip_no_prob = 0
    skip_t_theta = 0
    skip_resolution_window = 0
    skip_features = 0
    for _, row in df_new.iterrows():
        mid = row["market_id"]
        sym = row["symbol"]
        t0 = pd.Timestamp(row["created_at"]).tz_convert("UTC")
        t_e = pd.Timestamp(row["end_at"]).tz_convert("UTC")

        mkt_probs = probs.get(mid, [])
        if not mkt_probs:
            skip_no_prob += 1
            continue
        t_theta = find_t_theta(mkt_probs)
        if t_theta is None or t_theta >= t_e:
            skip_t_theta += 1
            continue
        resolution_days = (t_e - t_theta).total_seconds() / 86400
        if not (MIN_RESOLUTION_DAYS <= resolution_days <= MAX_RESOLUTION_DAYS):
            skip_resolution_window += 1
            continue

        rec = compute_features(
            market_id=mid, event_id=row["event_id"], symbol=sym,
            question=row["question"], archetype=row["archetype"],
            relevance=row["relevance"], world_size=row["world_size"],
            t0=t0, t_e=t_e, t_theta=t_theta,
            prices=prices_close.get(sym, []), probs=mkt_probs,
            spy_prices=spy_prices, sector_etf_prices=spy_prices,
            sector="Unknown",
        )
        if rec is None:
            skip_features += 1
            continue

        rec["split"] = "train" if t_theta < OOS_START else "test"
        records.append(rec)

    print(f"\nNew candidates computed: {len(records)}", flush=True)
    print(f"  No prob data:      {skip_no_prob}", flush=True)
    print(f"  No t_theta:        {skip_t_theta}", flush=True)
    print(f"  Outside {MIN_RESOLUTION_DAYS}-{MAX_RESOLUTION_DAYS}d: {skip_resolution_window}", flush=True)
    print(f"  Features failed:   {skip_features}", flush=True)

    # Merge
    if records:
        new_df = pd.DataFrame(records)
        new_df = add_rank_features(new_df)
        for col in old_df.columns:
            if col not in new_df.columns:
                new_df[col] = None
        for col in new_df.columns:
            if col not in old_df.columns:
                old_df[col] = None
        combined = pd.concat([old_df, new_df[old_df.columns]], ignore_index=True)
    else:
        combined = old_df

    combined["t_theta"] = pd.to_datetime(combined["t_theta"], utc=True)
    n_train = len(combined[combined["split"] == "train"])
    n_test = len(combined[combined["split"] == "test"])
    print(f"\n  train: {n_train}   test: {n_test}   total: {len(combined)}", flush=True)

    # Show new questions added
    if records:
        added_qs = sorted(set(r["question"] for r in records))
        print(f"\n  New questions added ({len(added_qs)}):")
        for q in added_qs:
            split = next(r["split"] for r in records if r["question"] == q)
            print(f"    [{split}] {q[:120]}")

    combined.to_parquet(DATA_DIR / "candidates.parquet", engine="pyarrow", compression="snappy")
    print(f"\nSaved candidates.parquet", flush=True)

    with open(DATA_DIR / "probs.pkl", "wb") as f:
        pickle.dump(probs, f)
    print(f"Saved probs.pkl ({len(probs)} markets)", flush=True)

    with open(DATA_DIR / "prices.pkl", "wb") as f:
        pickle.dump(prices_hlc, f)
    print(f"Saved prices.pkl ({len(prices_hlc)} symbols)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
