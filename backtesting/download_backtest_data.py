"""Download Polymarket probability data and stock prices for the claude pipeline worlds."""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from database.db_connection import connect
from database.backtesting.schema import SCHEMA
from database.backtesting.polymarket import PolymarketHistoryClient, SourceMarket
from database.backtesting.market_data import YFinanceClient

CONCURRENCY = 8
PRICE_START = datetime(2026, 4, 1, tzinfo=timezone.utc)
PRICE_END = datetime(2026, 7, 5, tzinfo=timezone.utc)


async def download_probabilities():
    conn = await connect()
    try:
        rows = await conn.fetch(
            f"""SELECT w.market_id, w.event_id, w.universe_name,
                       m.question, m.created_at, m.end_at, m.yes_token_id, m.condition_id
                FROM (
                    SELECT DISTINCT market_id, event_id, universe_name
                    FROM {SCHEMA}.historical_asset_worlds
                    WHERE prompt_version = 'claude-pipeline-v1'
                ) w
                CROSS JOIN LATERAL (
                    SELECT question, created_at, end_at, yes_token_id, condition_id
                    FROM (VALUES (NULL::text, NULL::timestamptz, NULL::timestamptz, NULL::text, NULL::text)) AS dummy(question, created_at, end_at, yes_token_id, condition_id)
                ) m
            """
        )
    finally:
        await conn.close()

    print(f"Dummy query returned {len(rows)} rows - need to get market info from cache")

    # Load from JSON cache instead since DB only has world data, not raw market data
    import json
    cache = json.loads(Path("data/markets_cache.json").read_text(encoding="utf-8"))
    market_by_id = {m["market_id"]: m for m in cache}

    conn = await connect()
    try:
        world_ids = await conn.fetch(
            f"""SELECT DISTINCT market_id
                FROM {SCHEMA}.historical_asset_worlds
                WHERE prompt_version = 'claude-pipeline-v1'"""
        )
        already_have = await conn.fetch(
            f"""SELECT market_id FROM {SCHEMA}.historical_probability_coverage
                WHERE market_id = ANY($1::text[])""",
            [r["market_id"] for r in world_ids]
        )
    finally:
        await conn.close()

    world_market_ids = {r["market_id"] for r in world_ids}
    already_set = {r["market_id"] for r in already_have}
    need_ids = world_market_ids - already_set

    markets_to_download = []
    for mid in need_ids:
        m = market_by_id.get(mid)
        if m:
            markets_to_download.append(m)

    print(f"Markets in pipeline:     {len(world_market_ids)}")
    print(f"Already have probs:      {len(already_set)}")
    print(f"Need to download:        {len(markets_to_download)}")

    if not markets_to_download:
        print("Nothing to download!")
        return

    from database.db_connection import create_pool
    pool = await create_pool(min_size=2, max_size=6)
    sem = asyncio.Semaphore(CONCURRENCY)
    client = PolymarketHistoryClient(chunk_days=10)
    done = 0
    failed = 0
    total = len(markets_to_download)

    async def download_one(m: dict):
        nonlocal done, failed
        created = datetime.fromisoformat(m["created_at"])
        end = datetime.fromisoformat(m["end_at"])
        source = SourceMarket(
            market_id=m["market_id"],
            event_id=m["event_id"],
            event_title=m.get("event_title", ""),
            question=m.get("question", ""),
            created_at=created,
            end_at=end,
            tags=m.get("tags", []),
            raw_market={},
            yes_token_id=m["yes_token_id"],
            condition_id=m.get("condition_id"),
            final_outcome=None,
        )

        async with sem:
            try:
                points = await client.hourly_probabilities(
                    source, start=created, end=end,
                )
            except Exception as e:
                failed += 1
                done += 1
                if done % 100 == 0 or done == total:
                    print(f"  [{done}/{total}] FAILED {m['market_id'][:12]}: {e}")
                return

        async with pool.acquire() as conn2:
            for pt in points:
                await conn2.execute(
                    f"""INSERT INTO {SCHEMA}.historical_probability_points
                        (market_id, yes_token_id, hour_ts, source_ts, available_at, probability, volume_usdc)
                        VALUES ($1,$2,$3,$4,$5,$6,$7)
                        ON CONFLICT (market_id, hour_ts) DO NOTHING""",
                    m["market_id"],
                    m["yes_token_id"],
                    pt.timestamp,
                    pt.source_timestamp or pt.timestamp,
                    pt.available_at or pt.timestamp,
                    pt.probability,
                    pt.volume_usdc,
                )
            first_ts = points[0].timestamp if points else None
            last_ts = points[-1].timestamp if points else None
            await conn2.execute(
                f"""INSERT INTO {SCHEMA}.historical_probability_coverage
                    (market_id, yes_token_id, requested_start, requested_end,
                     first_hour, last_hour, row_count, volume_status, volume_error)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                    ON CONFLICT (market_id) DO UPDATE SET
                        row_count = EXCLUDED.row_count,
                        first_hour = EXCLUDED.first_hour,
                        last_hour = EXCLUDED.last_hour,
                        volume_status = EXCLUDED.volume_status,
                        completed_at = NOW()""",
                m["market_id"],
                m["yes_token_id"],
                created,
                end,
                first_ts,
                last_ts,
                len(points),
                client.volume_status,
                client.volume_error,
            )

        done += 1
        if done % 50 == 0 or done == total:
            print(f"  [{done}/{total}] {m['market_id'][:12]}... {len(points)} points "
                  f"(failed={failed})")

    tasks = [download_one(m) for m in markets_to_download]
    await asyncio.gather(*tasks)
    await client.close()
    await pool.close()

    print(f"\nProbability download complete: {done - failed} ok, {failed} failed")


async def download_prices():
    conn = await connect()
    try:
        syms = await conn.fetch(
            f"""SELECT DISTINCT a.symbol
                FROM {SCHEMA}.historical_asset_worlds w
                JOIN {SCHEMA}.historical_asset_world_assets a ON w.world_id = a.world_id
                WHERE w.prompt_version = 'claude-pipeline-v1'
                ORDER BY a.symbol"""
        )
        sym_list = [r["symbol"] for r in syms]

        coverage = await conn.fetch(
            f"""SELECT symbol, last_ts, resolution
                FROM {SCHEMA}.historical_price_coverage
                WHERE symbol = ANY($1::text[]) AND resolution = '1d'""",
            sym_list
        )
    finally:
        await conn.close()

    covered = {r["symbol"]: r["last_ts"] for r in coverage}

    needs_update = []
    for sym in sym_list:
        last = covered.get(sym)
        if last is None:
            needs_update.append((sym, PRICE_START, PRICE_END))
        elif last < PRICE_END - timedelta(days=3):
            needs_update.append((sym, last - timedelta(days=1), PRICE_END))

    print(f"\nPrice data: {len(sym_list)} symbols, {len(needs_update)} need updates")
    if not needs_update:
        print("All price data is current!")
        return

    yf = YFinanceClient(concurrency=4)
    done = 0
    total = len(needs_update)

    for sym, start, end in needs_update:
        try:
            bars = await yf.bars(sym, start=start, end=end, resolution="1d")
        except Exception as e:
            print(f"  FAILED {sym}: {e}")
            done += 1
            continue

        if bars:
            conn2 = await connect()
            try:
                for bar in bars:
                    await conn2.execute(
                        f"""INSERT INTO {SCHEMA}.historical_price_bars
                            (symbol, resolution, ts, open, high, low, close, volume)
                            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                            ON CONFLICT (symbol, resolution, ts) DO NOTHING""",
                        sym, "1d", bar.timestamp,
                        bar.open, bar.high, bar.low, bar.close, bar.volume,
                    )
                first_ts = bars[0].timestamp
                last_ts = bars[-1].timestamp
                existing = covered.get(sym)
                if existing:
                    await conn2.execute(
                        f"""UPDATE {SCHEMA}.historical_price_coverage
                            SET requested_end = $1, last_ts = $2,
                                row_count = row_count + $3, completed_at = NOW()
                            WHERE symbol = $4 AND resolution = '1d'""",
                        end, max(last_ts, existing), len(bars), sym,
                    )
                else:
                    await conn2.execute(
                        f"""INSERT INTO {SCHEMA}.historical_price_coverage
                            (symbol, resolution, requested_start, requested_end,
                             first_ts, last_ts, row_count)
                            VALUES ($1,$2,$3,$4,$5,$6,$7)
                            ON CONFLICT (symbol, resolution) DO UPDATE SET
                                last_ts = GREATEST({SCHEMA}.historical_price_coverage.last_ts, EXCLUDED.last_ts),
                                row_count = {SCHEMA}.historical_price_coverage.row_count + EXCLUDED.row_count,
                                completed_at = NOW()""",
                        sym, "1d", start, end, first_ts, last_ts, len(bars),
                    )
            finally:
                await conn2.close()

        done += 1
        print(f"  [{done}/{total}] {sym:8s} {len(bars)} new bars")


async def main():
    print("=== Step 1: Download Polymarket probabilities ===")
    await download_probabilities()

    print("\n=== Step 2: Download/extend stock prices ===")
    await download_prices()

    # Final stats
    conn = await connect()
    try:
        prob_ok = await conn.fetchval(
            f"""SELECT COUNT(*)
                FROM {SCHEMA}.historical_probability_coverage c
                WHERE c.market_id IN (
                    SELECT DISTINCT market_id FROM {SCHEMA}.historical_asset_worlds
                    WHERE prompt_version = 'claude-pipeline-v1'
                ) AND c.row_count > 0"""
        )
        prob_empty = await conn.fetchval(
            f"""SELECT COUNT(*)
                FROM {SCHEMA}.historical_probability_coverage c
                WHERE c.market_id IN (
                    SELECT DISTINCT market_id FROM {SCHEMA}.historical_asset_worlds
                    WHERE prompt_version = 'claude-pipeline-v1'
                ) AND c.row_count = 0"""
        )
        total_points = await conn.fetchval(
            f"""SELECT COUNT(*)
                FROM {SCHEMA}.historical_probability_points
                WHERE market_id IN (
                    SELECT DISTINCT market_id FROM {SCHEMA}.historical_asset_worlds
                    WHERE prompt_version = 'claude-pipeline-v1'
                )"""
        )
        print(f"\n=== Final Stats ===")
        print(f"Markets with probability data:  {prob_ok}")
        print(f"Markets with empty probs:       {prob_empty}")
        print(f"Total probability points:       {total_points:,}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
