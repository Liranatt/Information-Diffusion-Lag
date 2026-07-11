"""Summarize live tracking tables without touching IB."""
from __future__ import annotations

import asyncio
import json

from database.backtesting.schema import SCHEMA
from database.db_connection import create_pool


async def main() -> None:
    pool = await create_pool(min_size=1, max_size=1)
    try:
        async with pool.acquire() as conn:
            markets = await conn.fetchrow(
                f"""SELECT COUNT(*) AS markets,
                          COUNT(*) FILTER (WHERE t0_prob IS NOT NULL) AS with_t0
                    FROM {SCHEMA}.live_tracked_markets
                    WHERE status='tracking'"""
            )
            rows = await conn.fetch(
                f"""SELECT assets FROM {SCHEMA}.live_tracked_markets
                    WHERE status='tracking'"""
            )
            probs = await conn.fetchrow(
                f"""SELECT COUNT(DISTINCT p.market_id) AS markets_with_probs,
                          COUNT(*) AS points
                    FROM {SCHEMA}.historical_probability_points p
                    JOIN {SCHEMA}.live_tracked_markets m
                      ON m.market_id = p.market_id
                    WHERE m.status='tracking'"""
            )
            positions = await conn.fetchrow(
                f"""SELECT COUNT(*) FILTER (WHERE status='open') AS open_positions,
                          COUNT(*) FILTER (WHERE status='closed') AS closed_positions
                    FROM {SCHEMA}.live_positions"""
            )
            orders = await conn.fetchrow(
                f"SELECT COUNT(*) AS orders FROM {SCHEMA}.live_orders"
            )

        symbols = sorted({
            asset["symbol"]
            for row in rows
            for asset in (
                json.loads(row["assets"])
                if isinstance(row["assets"], str)
                else row["assets"]
            )
        })
        print(
            f"tracking_markets={markets['markets']} "
            f"with_t0={markets['with_t0']} distinct_symbols={len(symbols)}"
        )
        print(
            f"markets_with_probs={probs['markets_with_probs']} "
            f"probability_points={probs['points']}"
        )
        print(
            f"open_positions={positions['open_positions']} "
            f"closed_positions={positions['closed_positions']} "
            f"orders={orders['orders']}"
        )
        print("symbols=" + ",".join(symbols))
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
