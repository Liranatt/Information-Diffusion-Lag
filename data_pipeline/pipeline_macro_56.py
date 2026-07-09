"""Connect the macro lane: pipeline the ~56 liquid macro/war markets into
candidates.parquet (catalyst -> asset mapping -> probs -> prices -> features).

Approved spend: Gemini catalyst+mapping for ~56 markets (< $1, gemini default).
Everything else is free downloads and local feature building.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from database.db_connection import connect
from database.backtesting.schema import SCHEMA

CLOB = "https://clob.polymarket.com/prices-history"
FAMS = {"fed_rates", "inflation_cpi", "jobs_employment", "gdp_growth", "war_conflict"}
BELLS = {"russia_ukraine", "china_taiwan"}


def qhash(q):
    return hashlib.sha256(str(q).strip().lower().encode("utf-8")).hexdigest()


async def select_liquid_markets() -> list[dict]:
    conn = await connect()
    try:
        rows = await conn.fetch(
            f"SELECT question_hash, event_family, belligerents, materiality "
            f"FROM {SCHEMA}.question_labels WHERE event_family = ANY($1::text[])",
            list(FAMS))
    finally:
        await conn.close()
    lab = {r["question_hash"]: dict(r) for r in rows}

    cand = pd.read_parquet(ROOT / "data" / "candidates.parquet")
    known = set(cand["market_id"].astype(str))

    pool = {}
    for cache in ["markets_cache_historical.json", "markets_cache.json"]:
        p = ROOT / "data" / cache
        if not p.exists():
            continue
        for m in json.loads(p.read_text(encoding="utf-8")):
            L = lab.get(qhash(m.get("question", "")))
            if not L or L["materiality"] == "low":
                continue
            if L["event_family"] == "war_conflict" and L["belligerents"] not in BELLS:
                continue
            mid = str(m.get("market_id") or "")
            end = str(m.get("end_at", ""))[:10]
            if not mid or mid in known or not m.get("yes_token_id"):
                continue
            if not ("2024-08-01" <= end <= "2026-06-12"):
                continue
            pool[mid] = m
    print(f"[select] labeled macro/war pool (pre-liquidity): {len(pool)}")

    sem = asyncio.Semaphore(8)
    liquid: list[dict] = []
    errors: list[str] = []

    async def check(m, client):
        async with sem:
            try:
                end_ts = int(datetime.fromisoformat(str(m["end_at"])[:19].replace("Z", "")).replace(
                    tzinfo=timezone.utc).timestamp())
                try:
                    start_ts = int(datetime.fromisoformat(str(m["created_at"])[:19].replace("Z", "")).replace(
                        tzinfo=timezone.utc).timestamp())
                except (KeyError, ValueError, TypeError):
                    start_ts = end_ts - 120 * 86400
                # CLOB rejects spans much beyond ~4 months at daily fidelity.
                start_ts = max(start_ts, end_ts - 120 * 86400)
                r = await client.get(CLOB, params={
                    "market": m["yes_token_id"], "fidelity": 1440,
                    "startTs": start_ts, "endTs": end_ts})
                r.raise_for_status()
                if len(r.json().get("history") or []) >= 5:
                    liquid.append(m)
            except Exception as error:  # noqa: BLE001
                errors.append(f"{type(error).__name__}: {str(error)[:80]}")

    async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
        markets = list(pool.values())
        for i in range(0, len(markets), 200):
            await asyncio.gather(*(check(m, client) for m in markets[i:i + 200]))
    print(f"[select] liquid markets: {len(liquid)}   check errors: {len(errors)}")
    if errors:
        print(f"[select] first errors: {errors[:3]}")
    return liquid


async def main() -> None:
    from scan_historical import step2b_catalyst_filter, step2c_asset_mapping, \
        step3_download_probs, step4_download_prices
    from pipeline.data_loader import build_dataset_from_db

    liquid = await select_liquid_markets()
    if not liquid:
        print("nothing to pipeline")
        return
    new_mids = {str(m["market_id"]) for m in liquid}

    catalysts = await step2b_catalyst_filter(liquid)
    passed = await step2c_asset_mapping(catalysts)
    print(f"[gemini] catalysts={len(catalysts)}  with mapped assets={len(passed)}")
    if not passed:
        print("no markets passed asset mapping")
        return

    await step3_download_probs(passed)
    await step4_download_prices(passed)

    # Build feature rows from DB, keep only the new markets.
    full = await build_dataset_from_db(relevance_floor=0.5, output_path=None)
    new_rows = full[full["market_id"].astype(str).isin(new_mids)].copy()
    print(f"[features] new candidate rows built: {len(new_rows)}")
    if new_rows.empty:
        print("no feature rows survived (check prob coverage / t_theta)")
        return

    parquet = ROOT / "data" / "candidates.parquet"
    backup = ROOT / "data" / "candidates_backup_pre_macro.parquet"
    shutil.copy(parquet, backup)
    existing = pd.read_parquet(parquet)

    new_rows["t_theta"] = pd.to_datetime(new_rows["t_theta"], utc=True)
    new_rows["split"] = new_rows["t_theta"].map(
        lambda t: "train" if t < pd.Timestamp("2026-01-01", tz="UTC") else "test")
    aligned = new_rows.reindex(columns=existing.columns)

    merged = pd.concat([existing, aligned], ignore_index=True)
    before = len(merged)
    merged = merged.drop_duplicates(subset=["market_id", "symbol"], keep="first")
    merged.to_parquet(parquet, engine="pyarrow", compression="snappy")
    print(f"[merge] {len(existing)} existing + {len(aligned)} new "
          f"({before - len(merged)} dupes dropped) -> {len(merged)} total")
    print(f"[merge] backup at {backup.name}")
    print("\nnew rows by question (first 30):")
    for _, r in aligned.head(30).iterrows():
        print(f"  [{r['split']}] {r['symbol']:>5}  {str(r['question'])[:70]}")


asyncio.run(main())
