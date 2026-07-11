"""The consolidated ingestion chain — one place for the cleaning pipeline shared
by historical backfill and live discovery.

    Gamma scan -> bracket dedup -> regex prefilter (free) -> Gemini catalyst gate
    -> Gemini asset mapping -> probability download -> price download

`backfill(start, end)` runs the whole thing over a historical window and writes
to Postgres. `discover_and_clean(client)` runs scan -> dedup -> regex -> catalyst
-> mapping over the live forward window and returns the markets that survived,
for the live control loop to start tracking.

Formerly scattered across backtesting/scan_historical.py (steps 1-4),
backtesting/pipeline/evaluator.py (mapping), data_pipeline/dedup_macro.py
(dedup) and llm_models/run_claude_pipeline.py (the regex prefilter).

NOTE: the Gemini catalyst gate and the relevance gate inside asset mapping ask
overlapping questions; merging them into a single Gemini call is a worthwhile
token saving but needs live batch-size/cost benchmarking before committing, so
the two passes are kept separate here for now.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from database.db_connection import connect, create_pool
from database.backtesting.schema import SCHEMA
from database.backtesting.market_data import YFinanceClient
from ingest.scanner import (
    ScannedMarket,
    fetch_active_markets,
    fetch_markets_in_range,
)
from ingest.prefilter import regex_prefilter
from ingest.dedup import dedup_markets
from ingest.evaluator import evaluate as gemini_evaluate
from ingest.gemini_client import GeminiClient
from ingest.world import (
    QUESTION_RELEVANCE_FLOOR,
    CatalystBatch,
    GEMINI_CATALYST_PROMPT,
)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CATALYST_RESULTS_PATH = DATA_DIR / "catalyst_results.json"

CLOB_PRICE_HISTORY_URL = "https://clob.polymarket.com/prices-history"
CATALYST_BATCH_SIZE = 50
MAPPING_BATCH_SIZE = 20
CONCURRENCY = 8

# Default historical backfill window.
SCAN_START = datetime(2024, 7, 1, tzinfo=timezone.utc)
SCAN_END = datetime(2026, 5, 27, tzinfo=timezone.utc)


def _dict_to_scanned(m: dict) -> ScannedMarket:
    """Convert a market dict to a ScannedMarket for the Gemini evaluator."""
    created = m["created_at"]
    end = m["end_at"]
    if isinstance(created, str):
        created = datetime.fromisoformat(created)
    if isinstance(end, str):
        end = datetime.fromisoformat(end)
    return ScannedMarket(
        event_id=m["event_id"],
        market_id=m["market_id"],
        question=m["question"],
        event_title=m.get("event_title", m["question"][:100]),
        tags=m.get("tags", []),
        created_at=created,
        end_at=end,
        yes_token_id=m["yes_token_id"],
        condition_id=m.get("condition_id", ""),
    )


def _scanned_to_dict(m: ScannedMarket) -> dict:
    return {
        "event_id": m.event_id, "market_id": m.market_id, "question": m.question,
        "event_title": m.event_title, "tags": m.tags,
        "created_at": m.created_at.isoformat(), "end_at": m.end_at.isoformat(),
        "yes_token_id": m.yes_token_id, "condition_id": m.condition_id,
    }


# ── Stage 1: cheap regex prefilter (free) ────────────────────────────────────

async def regex_filter(markets: list[dict]) -> list[dict]:
    """Keep markets clearing the relevance floor with positive sentiment."""
    passed = []
    for m in markets:
        p1 = regex_prefilter(m)
        if p1.question_relevance >= QUESTION_RELEVANCE_FLOOR and p1.positive_sentiment:
            passed.append(m)
    print(f"[regex] {len(markets)} -> {len(passed)}")
    return passed


# ── Stage 2: Gemini CATALYST / NOISE gate ────────────────────────────────────

async def catalyst_filter(regex_passed: list[dict]) -> list[dict]:
    """Gemini CATALYST/NOISE classification. 50 per batch, 1 API call each."""
    existing: dict[str, dict] = {}
    if CATALYST_RESULTS_PATH.exists():
        for r in json.loads(CATALYST_RESULTS_PATH.read_text(encoding="utf-8")):
            existing[r["market_id"]] = r

    remaining = [m for m in regex_passed if m["market_id"] not in existing]
    total_batches = (len(remaining) + CATALYST_BATCH_SIZE - 1) // CATALYST_BATCH_SIZE
    print(f"[catalyst] {len(regex_passed)} in, {len(remaining)} to classify -> {total_batches} calls")

    if remaining:
        gemini = GeminiClient()
        done = 0
        for i in range(0, len(remaining), CATALYST_BATCH_SIZE):
            batch = remaining[i : i + CATALYST_BATCH_SIZE]
            lines = [f"{j}. [{m['created_at'][:10]}] \"{m['question']}\""
                     for j, m in enumerate(batch, 1)]
            try:
                response = await gemini.structured(
                    system_prompt=GEMINI_CATALYST_PROMPT,
                    payload={"questions": "\n".join(lines)},
                    response_model=CatalystBatch,
                    max_tokens=CATALYST_BATCH_SIZE * 40 + 500,
                )
                verdict_map = {c.id: c for c in response.classifications}
                for j, m in enumerate(batch, 1):
                    c = verdict_map.get(j)
                    existing[m["market_id"]] = {
                        "market_id": m["market_id"],
                        "verdict": c.verdict if c else "UNKNOWN",
                        "positive_sentiment": c.positive_sentiment if c else True,
                        "reason": c.reason if c else "Missing from response",
                    }
            except Exception as e:  # noqa: BLE001 - one bad batch must not sink the run
                for m in batch:
                    existing[m["market_id"]] = {
                        "market_id": m["market_id"], "verdict": "ERROR",
                        "positive_sentiment": True, "reason": str(e)[:200],
                    }
            done += 1
            if done % 20 == 0:
                CATALYST_RESULTS_PATH.write_text(
                    json.dumps(list(existing.values()), indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
        await gemini.close()

    CATALYST_RESULTS_PATH.parent.mkdir(exist_ok=True)
    CATALYST_RESULTS_PATH.write_text(
        json.dumps(list(existing.values()), indent=2, ensure_ascii=False), encoding="utf-8",
    )

    catalyst_ids = {
        r["market_id"] for r in existing.values()
        if r["verdict"] == "CATALYST" and r["positive_sentiment"]
    }
    catalysts = [m for m in regex_passed if m["market_id"] in catalyst_ids]
    print(f"[catalyst] passing to asset mapping: {len(catalysts)}")
    return catalysts


# ── Stage 3: Gemini asset mapping (via the evaluator) ────────────────────────

async def asset_mapping(catalysts: list[dict]) -> list[dict]:
    """Gemini asset mapping, 20 per batch; persists worlds to the DB."""
    conn = await connect()
    try:
        already = await conn.fetch(f"""
            SELECT DISTINCT w.market_id
            FROM {SCHEMA}.historical_asset_worlds w
            JOIN {SCHEMA}.historical_asset_world_assets a ON a.world_id = w.world_id
            WHERE w.market_id = ANY($1::text[]) AND w.prompt_version = 'pipeline-v1'
        """, [m["market_id"] for m in catalysts])
    finally:
        await conn.close()

    already_ids = {r["market_id"] for r in already}
    remaining = [m for m in catalysts if m["market_id"] not in already_ids]
    total_batches = (len(remaining) + MAPPING_BATCH_SIZE - 1) // MAPPING_BATCH_SIZE
    print(f"[mapping] {len(already_ids)} already mapped, {len(remaining)} to map -> {total_batches} calls")

    for i in range(0, len(remaining), MAPPING_BATCH_SIZE):
        batch_scanned = [_dict_to_scanned(m) for m in remaining[i : i + MAPPING_BATCH_SIZE]]
        try:
            await gemini_evaluate(batch_scanned)
        except Exception as e:  # noqa: BLE001
            print(f"[mapping] batch failed: {e}")

    conn = await connect()
    try:
        world_rows = await conn.fetch(f"""
            SELECT DISTINCT w.market_id
            FROM {SCHEMA}.historical_asset_worlds w
            JOIN {SCHEMA}.historical_asset_world_assets a ON a.world_id = w.world_id
            WHERE w.market_id = ANY($1::text[])
        """, [m["market_id"] for m in catalysts])
    finally:
        await conn.close()

    passed_ids = {r["market_id"] for r in world_rows}
    passed = [m for m in catalysts if m["market_id"] in passed_ids]
    print(f"[mapping] with mapped assets: {len(passed)}")
    return passed


# ── Stage 4: downloads ───────────────────────────────────────────────────────

async def download_probs(passed_markets: list[dict]) -> None:
    """Download DAILY probability history (fidelity=1440) for markets missing it.

    NOTE: standardizing all stored probability history to hourly (fidelity=60) is
    a follow-up (see the DB-write-safety step); kept daily here for continuity.
    """
    conn = await connect()
    try:
        already = await conn.fetch(
            f"""SELECT market_id FROM {SCHEMA}.historical_probability_coverage
                WHERE market_id = ANY($1::text[])""",
            [m["market_id"] for m in passed_markets],
        )
    finally:
        await conn.close()

    already_set = {r["market_id"] for r in already}
    need = [m for m in passed_markets if m["market_id"] not in already_set]
    print(f"[probs] {len(passed_markets)} passed, {len(already_set)} have coverage, {len(need)} to fetch")
    if not need:
        return

    pool = await create_pool(min_size=2, max_size=6)
    sem = asyncio.Semaphore(CONCURRENCY)
    client = httpx.AsyncClient(timeout=httpx.Timeout(30))

    async def one(m: dict):
        created = datetime.fromisoformat(m["created_at"])
        end = datetime.fromisoformat(m["end_at"])
        async with sem:
            rows = []
            cursor = created
            try:
                while cursor < end:
                    chunk_end = min(cursor + timedelta(days=30), end)
                    resp = await client.get(CLOB_PRICE_HISTORY_URL, params={
                        "market": m["yes_token_id"],
                        "startTs": int(cursor.timestamp()),
                        "endTs": int(chunk_end.timestamp()),
                        "fidelity": 1440,
                    })
                    resp.raise_for_status()
                    for item in resp.json().get("history") or []:
                        ts = datetime.fromtimestamp(float(item["t"]), tz=timezone.utc)
                        rows.append((ts, min(max(float(item["p"]), 0.0), 1.0)))
                    cursor = chunk_end
            except Exception as e:  # noqa: BLE001
                print(f"[probs] FAILED {m['market_id'][:12]}: {e}")
                return
        async with pool.acquire() as conn2:
            for ts, prob in rows:
                await conn2.execute(
                    f"""INSERT INTO {SCHEMA}.historical_probability_points
                        (market_id, yes_token_id, hour_ts, source_ts,
                         available_at, probability, volume_usdc)
                        VALUES ($1,$2,$3,$3,$3,$4,0)
                        ON CONFLICT (market_id, hour_ts) DO NOTHING""",
                    m["market_id"], m["yes_token_id"], ts, prob,
                )
            first_ts = rows[0][0] if rows else None
            last_ts = rows[-1][0] if rows else None
            await conn2.execute(
                f"""INSERT INTO {SCHEMA}.historical_probability_coverage
                    (market_id, yes_token_id, requested_start, requested_end,
                     first_hour, last_hour, row_count, volume_status, volume_error)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,'daily','none')
                    ON CONFLICT (market_id) DO UPDATE SET
                        row_count = EXCLUDED.row_count,
                        first_hour = EXCLUDED.first_hour,
                        last_hour = EXCLUDED.last_hour,
                        completed_at = NOW()""",
                m["market_id"], m["yes_token_id"], created, end, first_ts, last_ts, len(rows),
            )

    await asyncio.gather(*[one(m) for m in need])
    await client.aclose()
    await pool.close()
    print("[probs] done")


async def download_prices(passed_markets: list[dict]) -> None:
    """Download daily stock prices for every symbol mapped to passed markets."""
    conn = await connect()
    try:
        syms = await conn.fetch(
            f"""SELECT DISTINCT a.symbol
                FROM {SCHEMA}.historical_asset_worlds w
                JOIN {SCHEMA}.historical_asset_world_assets a ON w.world_id = a.world_id
                WHERE w.prompt_version = ANY($1::text[])
                ORDER BY a.symbol""",
            ["pipeline-v1", "catalyst-v1"],
        )
        sym_list = sorted(set(r["symbol"] for r in syms) | {"SPY", "QQQ"})
        coverage = await conn.fetch(
            f"""SELECT symbol, last_ts FROM {SCHEMA}.historical_price_coverage
                WHERE symbol = ANY($1::text[]) AND resolution = '1d'""",
            sym_list,
        )
    finally:
        await conn.close()

    covered = {r["symbol"]: r["last_ts"] for r in coverage}
    price_start = SCAN_START - timedelta(days=365)
    price_end = SCAN_END + timedelta(days=7)

    needs_update = []
    for sym in sym_list:
        last = covered.get(sym)
        if last is None:
            needs_update.append((sym, price_start, price_end))
        elif last < price_end - timedelta(days=3):
            needs_update.append((sym, last - timedelta(days=1), price_end))
    print(f"[prices] {len(sym_list)} symbols, {len(needs_update)} need update")
    if not needs_update:
        return

    yf = YFinanceClient(concurrency=4)
    for sym, start, end in needs_update:
        try:
            bars = await yf.bars(sym, start=start, end=end, resolution="1d")
        except Exception as e:  # noqa: BLE001
            print(f"[prices] FAILED {sym}: {e}")
            continue
        if not bars:
            continue
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
            first_ts, last_ts = bars[0].timestamp, bars[-1].timestamp
            existing = covered.get(sym)
            if existing:
                await conn2.execute(
                    f"""UPDATE {SCHEMA}.historical_price_coverage
                        SET requested_end=$1, last_ts=$2,
                            row_count=row_count+$3, completed_at=NOW()
                        WHERE symbol=$4 AND resolution='1d'""",
                    end, max(last_ts, existing), len(bars), sym,
                )
            else:
                await conn2.execute(
                    f"""INSERT INTO {SCHEMA}.historical_price_coverage
                        (symbol, resolution, requested_start, requested_end,
                         first_ts, last_ts, row_count)
                        VALUES ($1,$2,$3,$4,$5,$6,$7)
                        ON CONFLICT (symbol, resolution) DO UPDATE SET
                            last_ts=GREATEST({SCHEMA}.historical_price_coverage.last_ts, EXCLUDED.last_ts),
                            row_count={SCHEMA}.historical_price_coverage.row_count+EXCLUDED.row_count,
                            completed_at=NOW()""",
                    sym, "1d", start, end, first_ts, last_ts, len(bars),
                )
        finally:
            await conn2.close()
    print("[prices] done")


# ── Orchestrators ────────────────────────────────────────────────────────────

async def backfill(start: datetime = SCAN_START, end: datetime = SCAN_END) -> list[dict]:
    """Full historical pipeline over [start, end]: scan -> dedup -> regex ->
    catalyst -> mapping -> download probs -> download prices."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(60)) as client:
        scanned: dict[str, ScannedMarket] = {}
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=31), end)
            for m in await fetch_markets_in_range(client, start=cursor, end=chunk_end):
                scanned[m.market_id] = m
            print(f"  scan {cursor.date()}..{chunk_end.date()}: {len(scanned)} unique")
            cursor = chunk_end
    markets = [_scanned_to_dict(m) for m in scanned.values()]

    markets = dedup_markets(markets)
    print(f"[dedup] -> {len(markets)} unique events")
    regex_passed = await regex_filter(markets)
    catalysts = await catalyst_filter(regex_passed)
    passed = await asset_mapping(catalysts)
    await download_probs(passed)
    await download_prices(passed)
    print(f"=== backfill done: {len(passed)} markets mapped ===")
    return passed


async def discover_and_clean(client: httpx.AsyncClient, *, known: set[str]) -> list[dict]:
    """Live discovery: scan the forward window, drop already-known markets, then
    dedup -> regex -> catalyst -> mapping. Returns the surviving market dicts
    (with mapped worlds persisted to the DB) for the caller to start tracking."""
    scanned = await fetch_active_markets(client)
    fresh = [_scanned_to_dict(m) for m in scanned if m.market_id not in known]
    print(f"[discover] {len(scanned)} scanned, {len(fresh)} new")
    if not fresh:
        return []

    fresh = dedup_markets(fresh)
    regex_passed = await regex_filter(fresh)
    catalysts = await catalyst_filter(regex_passed)
    return await asset_mapping(catalysts)
