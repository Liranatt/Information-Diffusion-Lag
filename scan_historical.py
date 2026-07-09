"""Scan, evaluate, and download data for Polymarket markets Jul 2024 - May 27 2026.

Steps:
  1. Fetch markets from Polymarket in monthly chunks (avoids offset cap)
  2a. Regex pre-filter (cheap, instant)
  2b. Gemini CATALYST/NOISE classification (50/batch, 1 API call each)
  2c. Gemini asset mapping (20/batch, only for catalysts)
  3. Download DAILY probability data (fidelity=1440)
  4. Download daily stock prices from yfinance
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from database.db_connection import connect, create_pool
from database.backtesting.schema import SCHEMA
from database.backtesting.market_data import YFinanceClient
from pipeline.scanner import fetch_markets_in_range, ScannedMarket
from pipeline.evaluator import evaluate as gemini_evaluate
from run_claude_pipeline import evaluate_pass1, PROMPT_VERSION
from LLM.gemini_client import GeminiClient
from LLM.build_world import (
    QUESTION_RELEVANCE_FLOOR,
    CatalystBatch,
    GEMINI_CATALYST_PROMPT,
)

SCAN_START = datetime(2024, 7, 1, tzinfo=timezone.utc)
SCAN_END = datetime(2026, 5, 27, tzinfo=timezone.utc)
CACHE_PATH = Path(__file__).resolve().parent / "data" / "markets_cache_historical.json"
CONCURRENCY = 8
CLOB_PRICE_HISTORY_URL = "https://clob.polymarket.com/prices-history"


async def step1_scan() -> list[dict]:
    """Fetch markets in monthly chunks, cache to JSON."""
    if CACHE_PATH.exists():
        markets = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        print(f"[Step 1] Loaded {len(markets)} cached markets from {CACHE_PATH}")
        return markets

    print(f"[Step 1] Scanning Polymarket {SCAN_START.date()} to {SCAN_END.date()} ...")
    all_markets: dict[str, ScannedMarket] = {}

    async with httpx.AsyncClient(timeout=httpx.Timeout(60)) as client:
        cursor = SCAN_START
        while cursor < SCAN_END:
            chunk_end = min(cursor + timedelta(days=31), SCAN_END)
            batch = await fetch_markets_in_range(client, start=cursor, end=chunk_end)
            for m in batch:
                all_markets[m.market_id] = m
            print(f"  {cursor.date()} - {chunk_end.date()}: {len(batch)} markets "
                  f"(total unique: {len(all_markets)})", flush=True)
            cursor = chunk_end

    records = []
    for m in all_markets.values():
        records.append({
            "event_id": m.event_id,
            "market_id": m.market_id,
            "question": m.question,
            "event_title": m.event_title,
            "tags": m.tags,
            "created_at": m.created_at.isoformat(),
            "end_at": m.end_at.isoformat(),
            "yes_token_id": m.yes_token_id,
            "condition_id": m.condition_id,
        })

    CACHE_PATH.parent.mkdir(exist_ok=True)
    CACHE_PATH.write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(f"  Cached {len(records)} markets to {CACHE_PATH}")
    return records


def _dict_to_scanned(m: dict) -> ScannedMarket:
    """Convert a cache dict to a ScannedMarket for the Gemini evaluator."""
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


CATALYST_BATCH_SIZE = 50
MAPPING_BATCH_SIZE = 20
ROOT = Path(__file__).resolve().parent
PROGRESS_PATH = ROOT / "data" / "gemini_eval_progress.json"
CATALYST_RESULTS_PATH = ROOT / "data" / "catalyst_results.json"


async def step2a_regex_filter(markets: list[dict]) -> list[dict]:
    """Cheap regex pre-filter."""
    print(f"\n[Step 2a] Regex pre-filter ({len(markets)} markets, floor={QUESTION_RELEVANCE_FLOOR}) ...")
    regex_passed = []
    for m in markets:
        p1 = evaluate_pass1(m)
        if p1.question_relevance >= QUESTION_RELEVANCE_FLOOR and p1.positive_sentiment:
            regex_passed.append(m)
    print(f"  {len(markets)} → {len(regex_passed)}")
    return regex_passed


async def step2b_catalyst_filter(regex_passed: list[dict]) -> list[dict]:
    """Gemini CATALYST/NOISE classification. 50 per batch, 1 API call each."""
    print(f"\n[Step 2b] Gemini catalyst filter ({len(regex_passed)} markets, batch={CATALYST_BATCH_SIZE}) ...")

    # Load existing results for resumability
    existing: dict[str, dict] = {}
    if CATALYST_RESULTS_PATH.exists():
        for r in json.loads(CATALYST_RESULTS_PATH.read_text(encoding="utf-8")):
            existing[r["market_id"]] = r
        print(f"  Resuming: {len(existing)} already classified")

    remaining = [m for m in regex_passed if m["market_id"] not in existing]
    total_batches = (len(remaining) + CATALYST_BATCH_SIZE - 1) // CATALYST_BATCH_SIZE
    print(f"  Remaining: {len(remaining)} → {total_batches} API calls")

    if remaining:
        gemini = GeminiClient()
        done_batches = 0
        failed_batches = 0
        t0 = time.time()

        for i in range(0, len(remaining), CATALYST_BATCH_SIZE):
            batch = remaining[i : i + CATALYST_BATCH_SIZE]

            lines = []
            for j, m in enumerate(batch, 1):
                created = m["created_at"][:10]
                lines.append(f"{j}. [{created}] \"{m['question']}\"")
            payload_text = "\n".join(lines)

            try:
                response = await gemini.structured(
                    system_prompt=GEMINI_CATALYST_PROMPT,
                    payload={"questions": payload_text},
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
            except Exception as e:
                failed_batches += 1
                print(f"  BATCH {done_batches+1} FAILED: {e}")
                for m in batch:
                    existing[m["market_id"]] = {
                        "market_id": m["market_id"],
                        "verdict": "ERROR",
                        "positive_sentiment": True,
                        "reason": str(e)[:200],
                    }

            done_batches += 1
            if done_batches % 10 == 0 or done_batches == total_batches:
                n_cat = sum(1 for r in existing.values() if r["verdict"] == "CATALYST")
                n_noise = sum(1 for r in existing.values() if r["verdict"] == "NOISE")
                elapsed = time.time() - t0
                print(f"  [{done_batches}/{total_batches}] "
                      f"CATALYST={n_cat} NOISE={n_noise} failed={failed_batches} "
                      f"({elapsed:.0f}s)", flush=True)

            if done_batches % 20 == 0:
                CATALYST_RESULTS_PATH.write_text(
                    json.dumps(list(existing.values()), indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

        await gemini.close()

    # Save final results
    CATALYST_RESULTS_PATH.write_text(
        json.dumps(list(existing.values()), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Filter to catalysts with positive sentiment
    catalyst_ids = {
        r["market_id"] for r in existing.values()
        if r["verdict"] == "CATALYST" and r["positive_sentiment"]
    }
    catalysts = [m for m in regex_passed if m["market_id"] in catalyst_ids]

    n_cat = sum(1 for r in existing.values() if r["verdict"] == "CATALYST")
    n_noise = sum(1 for r in existing.values() if r["verdict"] == "NOISE")
    n_neg = sum(1 for r in existing.values() if r["verdict"] == "CATALYST" and not r["positive_sentiment"])
    print(f"\n  === Catalyst filter results ===")
    print(f"  CATALYST: {n_cat}  (positive: {n_cat - n_neg}, negative: {n_neg})")
    print(f"  NOISE:    {n_noise}")
    print(f"  Passing to asset mapping: {len(catalysts)}")

    return catalysts


async def step2c_asset_mapping(catalysts: list[dict]) -> list[dict]:
    """Gemini asset mapping via existing evaluator. 20 per batch."""
    print(f"\n[Step 2c] Gemini asset mapping ({len(catalysts)} catalysts, batch={MAPPING_BATCH_SIZE}) ...")

    # Check which are already mapped in DB
    conn = await connect()
    try:
        already = await conn.fetch(f"""
            SELECT DISTINCT w.market_id
            FROM {SCHEMA}.historical_asset_worlds w
            JOIN {SCHEMA}.historical_asset_world_assets a ON a.world_id = w.world_id
            WHERE w.market_id = ANY($1::text[])
              AND w.prompt_version = 'pipeline-v1'
        """, [m["market_id"] for m in catalysts])
    finally:
        await conn.close()

    already_ids = {r["market_id"] for r in already}
    remaining = [m for m in catalysts if m["market_id"] not in already_ids]
    total_batches = (len(remaining) + MAPPING_BATCH_SIZE - 1) // MAPPING_BATCH_SIZE
    print(f"  Already mapped: {len(already_ids)}  Remaining: {len(remaining)} → {total_batches} API calls")

    done_batches = 0
    failed_batches = 0
    total_with_assets = len(already_ids)

    for i in range(0, len(remaining), MAPPING_BATCH_SIZE):
        batch_dicts = remaining[i : i + MAPPING_BATCH_SIZE]
        batch_scanned = [_dict_to_scanned(m) for m in batch_dicts]

        try:
            results = await gemini_evaluate(batch_scanned)
            relevant = [r for r in results if r.question_relevance >= QUESTION_RELEVANCE_FLOOR and r.assets]
            total_with_assets += len(relevant)
        except Exception as e:
            failed_batches += 1
            print(f"  BATCH {done_batches+1} FAILED: {e}")

        done_batches += 1
        if done_batches % 5 == 0 or done_batches == total_batches:
            print(f"  [{done_batches}/{total_batches}] "
                  f"with_assets={total_with_assets} failed={failed_batches}", flush=True)

    # Query DB for final set of markets with assets
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

    print(f"\n  === Asset mapping complete ===")
    print(f"  Catalysts in:       {len(catalysts)}")
    print(f"  With mapped assets: {len(passed)}")
    print(f"  Failed batches:     {failed_batches}")

    return passed


async def step2_evaluate(markets: list[dict]) -> list[dict]:
    """Full 3-stage evaluation: regex → catalyst filter → asset mapping."""
    regex_passed = await step2a_regex_filter(markets)
    catalysts = await step2b_catalyst_filter(regex_passed)
    passed = await step2c_asset_mapping(catalysts)
    return passed


async def step3_download_probs(passed_markets: list[dict]):
    """Download DAILY probability data (fidelity=1440)."""
    conn = await connect()
    try:
        already = await conn.fetch(
            f"""SELECT market_id FROM {SCHEMA}.historical_probability_coverage
                WHERE market_id = ANY($1::text[])""",
            [m["market_id"] for m in passed_markets]
        )
    finally:
        await conn.close()

    already_set = {r["market_id"] for r in already}
    need = [m for m in passed_markets if m["market_id"] not in already_set]

    print(f"\n[Step 3] Download daily probabilities")
    print(f"  Total passed: {len(passed_markets)}  Already have: {len(already_set)}  Need: {len(need)}")

    if not need:
        print("  Nothing to download!")
        return

    pool = await create_pool(min_size=2, max_size=6)
    sem = asyncio.Semaphore(CONCURRENCY)
    client = httpx.AsyncClient(timeout=httpx.Timeout(30))
    done = 0
    failed = 0
    total = len(need)

    async def download_one(m: dict):
        nonlocal done, failed
        created = datetime.fromisoformat(m["created_at"])
        end = datetime.fromisoformat(m["end_at"])

        async with sem:
            try:
                rows = []
                cursor = created
                while cursor < end:
                    chunk_end = min(cursor + timedelta(days=30), end)
                    resp = await client.get(
                        CLOB_PRICE_HISTORY_URL,
                        params={
                            "market": m["yes_token_id"],
                            "startTs": int(cursor.timestamp()),
                            "endTs": int(chunk_end.timestamp()),
                            "fidelity": 1440,
                        },
                    )
                    resp.raise_for_status()
                    for item in resp.json().get("history") or []:
                        ts = datetime.fromtimestamp(float(item["t"]), tz=timezone.utc)
                        prob = min(max(float(item["p"]), 0.0), 1.0)
                        rows.append((ts, prob))
                    cursor = chunk_end
            except Exception as e:
                failed += 1
                done += 1
                if done % 100 == 0 or done == total:
                    print(f"  [{done}/{total}] FAILED {m['market_id'][:12]}: {e}")
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
                m["market_id"], m["yes_token_id"],
                created, end, first_ts, last_ts, len(rows),
            )

        done += 1
        if done % 200 == 0 or done == total:
            print(f"  [{done}/{total}] {len(rows)} pts (failed={failed})", flush=True)

    await asyncio.gather(*[download_one(m) for m in need])
    await client.aclose()
    await pool.close()
    print(f"  Done: {done - failed} ok, {failed} failed")


async def step4_download_prices(passed_markets: list[dict]):
    """Download daily stock prices for all symbols in passed markets."""
    conn = await connect()
    try:
        syms = await conn.fetch(
            f"""SELECT DISTINCT a.symbol
                FROM {SCHEMA}.historical_asset_worlds w
                JOIN {SCHEMA}.historical_asset_world_assets a ON w.world_id = a.world_id
                WHERE w.prompt_version = ANY($1::text[])
                ORDER BY a.symbol""",
            [PROMPT_VERSION, "pipeline-v1", "catalyst-v1"],
        )
        sym_list = sorted(set(r["symbol"] for r in syms) | {"SPY", "QQQ"})

        coverage = await conn.fetch(
            f"""SELECT symbol, last_ts
                FROM {SCHEMA}.historical_price_coverage
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

    print(f"\n[Step 4] Download daily stock prices")
    print(f"  Symbols: {len(sym_list)}  Need update: {len(needs_update)}")

    if not needs_update:
        print("  All price data current!")
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

        done += 1
        if done % 10 == 0 or done == total:
            print(f"  [{done}/{total}] {sym:8s} {len(bars)} bars", flush=True)


async def main():
    markets = await step1_scan()
    passed = await step2_evaluate(markets)
    await step3_download_probs(passed)
    await step4_download_prices(passed)

    print("\n=== DONE ===")
    print(f"  Scanned: {len(markets)} markets")
    print(f"  Passed evaluation: {len(passed)}")


if __name__ == "__main__":
    asyncio.run(main())
