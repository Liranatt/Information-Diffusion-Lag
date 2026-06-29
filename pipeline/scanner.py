"""Discover new Polymarket markets via the Gamma API.

Fetches all active markets, filters to resolution windows of 5–60 days,
and returns only markets not already evaluated in the database.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import httpx

from database.db_connection import connect
from database.backtesting.schema import SCHEMA

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
PAGE_LIMIT = 100
MIN_RESOLUTION_DAYS = 5
MAX_RESOLUTION_DAYS = 60


@dataclass(frozen=True)
class ScannedMarket:
    event_id: str
    market_id: str
    question: str
    event_title: str
    tags: list[str]
    created_at: datetime
    end_at: datetime
    yes_token_id: str
    condition_id: str | None


async def fetch_active_markets(client: httpx.AsyncClient) -> list[ScannedMarket]:
    """Fetch all active, unresolved markets from Polymarket Gamma API."""
    markets: list[ScannedMarket] = []
    offset = 0
    now = datetime.now(timezone.utc)
    min_end = now + timedelta(days=MIN_RESOLUTION_DAYS)
    max_end = now + timedelta(days=MAX_RESOLUTION_DAYS)

    while True:
        response = await client.get(
            GAMMA_MARKETS_URL,
            params={
                "active": "true",
                "closed": "false",
                "limit": PAGE_LIMIT,
                "offset": offset,
            },
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break

        for m in batch:
            end_str = m.get("endDate") or m.get("end_date_iso")
            if not end_str:
                continue
            try:
                end_at = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if not (min_end <= end_at <= max_end):
                continue

            tokens = m.get("tokens", [])
            yes_token = next(
                (t for t in tokens if (t.get("outcome") or "").upper() == "YES"),
                None,
            )
            if yes_token is None:
                continue

            created_str = m.get("createdAt") or m.get("created_at")
            try:
                created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                created_at = now

            tags_raw = m.get("tags") or []
            if isinstance(tags_raw, str):
                tags_raw = [t.strip() for t in tags_raw.split(",") if t.strip()]

            markets.append(ScannedMarket(
                event_id=str(m.get("eventSlug") or m.get("event_id", "")),
                market_id=str(m.get("condition_id") or m.get("id", "")),
                question=m.get("question", ""),
                event_title=m.get("groupItemTitle") or m.get("question", ""),
                tags=tags_raw,
                created_at=created_at,
                end_at=end_at,
                yes_token_id=str(yes_token.get("token_id", "")),
                condition_id=m.get("condition_id"),
            ))

        if len(batch) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT

    return markets


async def filter_new_markets(markets: list[ScannedMarket]) -> list[ScannedMarket]:
    """Return only markets not already in the database."""
    if not markets:
        return []
    conn = await connect()
    try:
        existing = await conn.fetch(
            f"SELECT DISTINCT market_id FROM {SCHEMA}.historical_asset_worlds "
            f"WHERE market_id = ANY($1::text[])",
            [m.market_id for m in markets],
        )
        known = {row["market_id"] for row in existing}
    finally:
        await conn.close()
    return [m for m in markets if m.market_id not in known]


async def scan() -> list[ScannedMarket]:
    """Full scan: fetch active markets, filter to new ones."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(60)) as client:
        all_markets = await fetch_active_markets(client)
    new_markets = await filter_new_markets(all_markets)
    print(f"[scanner] found {len(all_markets)} active markets "
          f"({MIN_RESOLUTION_DAYS}–{MAX_RESOLUTION_DAYS}d window), "
          f"{len(new_markets)} new")
    return new_markets


if __name__ == "__main__":
    results = asyncio.run(scan())
    for m in results[:10]:
        days = (m.end_at - datetime.now(timezone.utc)).days
        print(f"  {m.market_id[:12]}… {days:3d}d  {m.question[:80]}")
    if len(results) > 10:
        print(f"  … and {len(results) - 10} more")
