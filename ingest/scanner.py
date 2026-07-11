"""Discover Polymarket markets via the Gamma API's /events endpoint.

Events are fetched (with nested markets and tags in a single response), filtered to
markets tagged with a financial/geopolitical category, and to a resolution-date window.

`fetch_active_markets()` covers the live/forward-looking window (next 5-60 days) used by
the day-to-day `scan` command. `fetch_markets_in_range()` covers an arbitrary historical
[start, end] window, for backfilling past markets into the database.

Positive-sentiment filtering happens downstream, in the Gemini relevance gate (see
LLM/build_world.py) -- it requires financial judgment (e.g. "the Fed cuts rates" is good
news despite the word "cuts") that a local keyword-based sentiment library can't reliably
make.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import httpx

from database.db_connection import connect
from database.backtesting.schema import SCHEMA

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
PAGE_LIMIT = 100
MIN_RESOLUTION_DAYS = 5
MAX_RESOLUTION_DAYS = 60

# Financial, macro, and geopolitical tag slugs we scan for. An event must carry at least
# one of these tags to be considered -- events can carry other tags too. Sourced from
# the same whitelist used by the historical bulk-download script.
ALLOWED_TAG_SLUGS = frozenset({
    "equities",
    "earnings",
    "kpis",
    "economy",
    "macro-indicators",
    "business",
    "monthly",
    "hit-price",
    "finance-updown",
    "pyth-finance",
    "stocks",
    "geopolitics",
    "oil",
    "iran",
    "us-x-iran",
    "strait-of-hormuz",
    "ai",
    "big-tech",
    "tech",
    "privates",
})


def _tag_slugs(tags_raw: object) -> list[str]:
    """Normalize an event's raw `tags` field to lowercase slugs.

    Gamma returns tags as a list of dicts ({"slug"/"label"/"name": ...}), but we accept
    plain strings / a comma-separated string too in case the shape ever varies.
    """
    if isinstance(tags_raw, str):
        items: list[object] = [t.strip() for t in tags_raw.split(",") if t.strip()]
    elif isinstance(tags_raw, list):
        items = tags_raw
    else:
        items = []

    slugs: list[str] = []
    for item in items:
        if isinstance(item, dict):
            value = item.get("slug") or item.get("label") or item.get("name")
        else:
            value = item
        if value:
            slugs.append(str(value).strip().lower())
    return slugs


def _json_list(value: object) -> list[str]:
    """Parse a field that may already be a list or a JSON-encoded array string.

    Gamma encodes `outcomes` / `clobTokenIds` on market objects as JSON strings, e.g.
    '["Yes", "No"]', rather than native arrays.
    """
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
    return []


def _yes_token_id(market: dict) -> str | None:
    outcomes = _json_list(market.get("outcomes"))
    token_ids = _json_list(market.get("clobTokenIds"))
    if len(outcomes) != len(token_ids):
        return None
    for outcome, token_id in zip(outcomes, token_ids):
        if outcome.strip().upper() == "YES":
            return token_id
    return None


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


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


def _scanned_markets_from_event(event: dict) -> list[ScannedMarket]:
    tag_slugs = _tag_slugs(event.get("tags"))
    if not ALLOWED_TAG_SLUGS.intersection(tag_slugs):
        return []

    event_id = str(event.get("id") or "")
    event_title = event.get("title") or ""
    event_created_at = _parse_dt(event.get("createdAt"))

    results: list[ScannedMarket] = []
    for raw_market in event.get("markets") or []:
        yes_token_id = _yes_token_id(raw_market)
        if not yes_token_id:
            continue
        end_at = _parse_dt(raw_market.get("endDate"))
        if end_at is None:
            continue
        market_id = str(raw_market.get("conditionId") or raw_market.get("id") or "")
        if not market_id:
            continue
        created_at = _parse_dt(raw_market.get("createdAt")) or event_created_at
        question = raw_market.get("question") or ""

        results.append(ScannedMarket(
            event_id=event_id,
            market_id=market_id,
            question=question,
            event_title=raw_market.get("groupItemTitle") or event_title or question,
            tags=tag_slugs,
            created_at=created_at or datetime.now(timezone.utc),
            end_at=end_at,
            yes_token_id=yes_token_id,
            condition_id=raw_market.get("conditionId"),
        ))
    return results


async def _fetch_events(client: httpx.AsyncClient, params_base: dict) -> list[dict]:
    events: list[dict] = []
    offset = 0
    while True:
        for attempt in range(5):
            response = await client.get(
                GAMMA_EVENTS_URL,
                params={**params_base, "limit": PAGE_LIMIT, "offset": offset},
            )
            if response.status_code == 429:
                wait = 2 ** attempt + 1
                print(f"[scanner] 429 rate-limited, waiting {wait}s (attempt {attempt+1}/5)")
                await asyncio.sleep(wait)
                continue
            break
        if response.status_code == 422 and "offset too large" in response.text:
            print(f"[scanner] hit Gamma's offset cap at offset={offset}; "
                  f"results may be truncated for params={params_base}")
            break
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        events.extend(batch)
        if len(batch) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
        await asyncio.sleep(0.3)
    return events


async def _fetch_events_by_allowed_tags(
    client: httpx.AsyncClient,
    extra_params: dict,
) -> list[dict]:
    """Query /events once per allowed tag and merge/dedupe by event id.

    Querying `tag_slug` server-side (rather than fetching everything and filtering
    client-side) keeps each request's result set well under Gamma's offset-pagination
    cap -- the unfiltered active/closed catalog alone exceeds it.
    """
    by_event_id: dict[str, dict] = {}
    for tag_slug in sorted(ALLOWED_TAG_SLUGS):
        events = await _fetch_events(client, {**extra_params, "tag_slug": tag_slug})
        for event in events:
            event_id = str(event.get("id") or "")
            if event_id:
                by_event_id[event_id] = event
        await asyncio.sleep(0.5)
    return list(by_event_id.values())


async def fetch_active_markets(client: httpx.AsyncClient) -> list[ScannedMarket]:
    """Fetch markets resolving in the next 5-60 days (the live/forward-looking scan)."""
    now = datetime.now(timezone.utc)
    min_end = now + timedelta(days=MIN_RESOLUTION_DAYS)
    max_end = now + timedelta(days=MAX_RESOLUTION_DAYS)

    events = await _fetch_events_by_allowed_tags(client, {"active": "true", "closed": "false"})
    markets: list[ScannedMarket] = []
    for event in events:
        for market in _scanned_markets_from_event(event):
            if min_end <= market.end_at <= max_end:
                markets.append(market)
    return markets


async def fetch_markets_in_range(
    client: httpx.AsyncClient,
    *,
    start: datetime,
    end: datetime,
) -> list[ScannedMarket]:
    """Fetch markets (any active/closed status) whose resolution date falls in [start, end].

    Used for historical backfills rather than the live day-to-day scan.
    """
    events = await _fetch_events_by_allowed_tags(
        client,
        {
            "end_date_min": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date_max": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    markets: list[ScannedMarket] = []
    for event in events:
        for market in _scanned_markets_from_event(event):
            if start <= market.end_at <= end:
                markets.append(market)
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
          f"({MIN_RESOLUTION_DAYS}–{MAX_RESOLUTION_DAYS}d window, "
          f"{len(ALLOWED_TAG_SLUGS)} allowed tags), "
          f"{len(new_markets)} new")
    return new_markets


async def scan_range(start: datetime, end: datetime) -> list[ScannedMarket]:
    """Historical scan: fetch markets resolving in [start, end], filter to new ones."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(60)) as client:
        all_markets = await fetch_markets_in_range(client, start=start, end=end)
    new_markets = await filter_new_markets(all_markets)
    print(f"[scanner] found {len(all_markets)} markets resolving in "
          f"[{start.date()}, {end.date()}] ({len(ALLOWED_TAG_SLUGS)} allowed tags), "
          f"{len(new_markets)} not yet in the database")
    return new_markets


if __name__ == "__main__":
    results = asyncio.run(scan())
    for m in results[:10]:
        days = (m.end_at - datetime.now(timezone.utc)).days
        print(f"  {m.market_id[:12]}… {days:3d}d  {m.question[:80]}")
    if len(results) > 10:
        print(f"  … and {len(results) - 10} more")
