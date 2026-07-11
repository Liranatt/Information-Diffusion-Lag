"""One-off migration: canonicalize daily price bars to one midnight-UTC row per
(symbol, trading day).

Why: the live daemon stamped IB daily bars at midnight UTC while yfinance stamped
them at 09:30 ET (13:30/14:30 UTC), so the same session produced two rows under
the (symbol, resolution, ts) primary key — 1,978 duplicate (symbol, day) pairs.
After both writers were switched to midnight UTC (market_data / data_fetcher) and
the conflict clauses to DO NOTHING, this collapses the existing rows so the
invariant holds retroactively and no future re-write can duplicate a session.

This does NOT change backtest results: every reader already normalizes bar
timestamps to the day, so the rebuilt artifacts are identical. Runs in one
transaction. Idempotent — re-running is a no-op once the table is canonical.
"""
from __future__ import annotations

import asyncio

from database.db_connection import connect
from database.backtesting.schema import SCHEMA as S


async def migrate() -> None:
    conn = await connect()
    try:
        before = await conn.fetchval(
            f"SELECT count(*) FROM {S}.historical_price_bars WHERE resolution='1d'"
        )
        async with conn.transaction():
            # 1. Keep one row per (symbol, day): prefer an existing midnight-UTC
            #    row (the live/IB one), else the earliest, delete the rest.
            deleted = await conn.execute(f"""
                WITH ranked AS (
                    SELECT ctid,
                           row_number() OVER (
                               PARTITION BY symbol, (ts AT TIME ZONE 'UTC')::date
                               ORDER BY (ts = date_trunc('day', ts AT TIME ZONE 'UTC')
                                              AT TIME ZONE 'UTC') DESC, ts
                           ) AS rn
                    FROM {S}.historical_price_bars
                    WHERE resolution='1d'
                )
                DELETE FROM {S}.historical_price_bars b
                USING ranked r
                WHERE b.ctid = r.ctid AND r.rn > 1
            """)
            # 2. Renormalize the surviving rows to midnight UTC (one per day now,
            #    so no primary-key collision).
            updated = await conn.execute(f"""
                UPDATE {S}.historical_price_bars
                SET ts = date_trunc('day', ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
                WHERE resolution='1d'
                  AND ts <> date_trunc('day', ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
            """)
        after = await conn.fetchval(
            f"SELECT count(*) FROM {S}.historical_price_bars WHERE resolution='1d'"
        )
        remaining_dups = await conn.fetchval(f"""
            SELECT count(*) FROM (
                SELECT 1 FROM {S}.historical_price_bars WHERE resolution='1d'
                GROUP BY symbol, (ts AT TIME ZONE 'UTC')::date HAVING count(*)>1) x
        """)
        non_midnight = await conn.fetchval(f"""
            SELECT count(*) FROM {S}.historical_price_bars
            WHERE resolution='1d'
              AND ts <> date_trunc('day', ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
        """)
        print(f"1d rows: {before} -> {after}  ({deleted}, {updated})")
        print(f"remaining (symbol,day) duplicates: {remaining_dups}")
        print(f"remaining non-midnight rows:       {non_midnight}")
        assert remaining_dups == 0 and non_midnight == 0, "migration did not fully canonicalize"
        print("OK: all daily bars are one midnight-UTC row per symbol-day")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
