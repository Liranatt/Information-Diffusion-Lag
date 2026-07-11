"""Gemini-powered relevance scoring and world building.

Takes scanned markets from the scanner, sends them through the two-pass
Gemini pipeline (relevance gate → tight asset mapping), and persists results
to the database.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from database.db_connection import connect
from database.backtesting.schema import SCHEMA
from ingest.gemini_client import GeminiClient
from ingest.world import (
    SourceMarket,
    IBTradableAsset,
    BatchedAssetWorld,
    build_gemini_asset_worlds,
    assets_from_world,
    QUESTION_RELEVANCE_FLOOR,
)
from ingest.scanner import ScannedMarket


@dataclass
class EvaluatedMarket:
    market: ScannedMarket
    question_relevance: float
    assets: list[dict]  # [{symbol, asset_name, asset_class, reason, connection_strength}]


def _scanned_to_source(m: ScannedMarket) -> SourceMarket:
    return SourceMarket(
        market_id=m.market_id,
        event_id=m.event_id,
        event_title=m.event_title,
        question=m.question,
        created_at=m.created_at,
        end_at=m.end_at,
        tags=m.tags,
        raw_market={},
        yes_token_id=m.yes_token_id,
        condition_id=m.condition_id,
        final_outcome=None,
    )


async def load_tradable_catalog() -> list[IBTradableAsset]:
    """Load the IB-tradable security master from the database."""
    conn = await connect()
    try:
        rows = await conn.fetch(
            f"SELECT official_symbol, security_name, is_etf, exchange "
            f"FROM {SCHEMA}.historical_us_security_master"
        )
    finally:
        await conn.close()
    return [
        IBTradableAsset(
            symbol=r["official_symbol"],
            asset_name=r["security_name"],
            asset_class="etf" if r["is_etf"] else "stock",
            primary_exchange=r["exchange"],
            stock_type="ETF" if r["is_etf"] else "COMMON",
        )
        for r in rows
    ]


async def persist_worlds(
    markets: list[ScannedMarket],
    worlds: list[BatchedAssetWorld],
) -> None:
    """Store world results in the database for downstream stages."""
    conn = await connect()
    try:
        for mkt, world in zip(markets, worlds):
            if not world.assets:
                continue
            world_id = uuid.uuid4()
            await conn.execute(
                f"""INSERT INTO {SCHEMA}.historical_asset_worlds
                    (world_id, input_hash, market_id, event_id, pass_number,
                     as_of, model_name, prompt_version, llm_input, llm_output,
                     universe_name, universe_reason)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                    ON CONFLICT (input_hash) DO NOTHING""",
                world_id,
                f"eval:{mkt.market_id}",
                mkt.market_id,
                mkt.event_id,
                1,
                datetime.now(timezone.utc),
                "gemini-3.5-flash",
                "pipeline-v1",
                "{}",
                world.model_dump_json() if hasattr(world, "model_dump_json") else "{}",
                world.universe_name,
                world.universe_reason,
            )
            for asset in world.assets:
                await conn.execute(
                    f"""INSERT INTO {SCHEMA}.historical_asset_world_assets
                        (world_id, symbol, asset_name, asset_class, reason,
                         connection_strength)
                        VALUES ($1,$2,$3,$4,$5,$6)
                        ON CONFLICT DO NOTHING""",
                    world_id,
                    asset.symbol,
                    asset.asset_name,
                    asset.asset_class,
                    asset.reason,
                    asset.connection_strength,
                )
    finally:
        await conn.close()


async def evaluate(markets: list[ScannedMarket]) -> list[EvaluatedMarket]:
    """Run Gemini two-pass evaluation on a batch of scanned markets.

    Returns EvaluatedMarket objects with relevance scores and mapped assets.
    """
    if not markets:
        return []

    gemini = GeminiClient()
    try:
        catalog = await load_tradable_catalog()
        now = datetime.now(timezone.utc)

        requests = [
            (m.market_id, _scanned_to_source(m), now)
            for m in markets
        ]

        worlds = await build_gemini_asset_worlds(
            gemini,
            requests,
            tradable_assets=catalog,
        )

        await persist_worlds(markets, worlds)

        results: list[EvaluatedMarket] = []
        for mkt, world in zip(markets, worlds):
            assets = [
                {
                    "symbol": a.symbol,
                    "asset_name": a.asset_name,
                    "asset_class": a.asset_class,
                    "reason": a.reason,
                    "connection_strength": a.connection_strength,
                }
                for a in world.assets
            ]
            results.append(EvaluatedMarket(
                market=mkt,
                question_relevance=world.question_relevance,
                assets=assets,
            ))

        relevant = [r for r in results if r.question_relevance >= QUESTION_RELEVANCE_FLOOR and r.assets]
        print(f"[evaluator] {len(markets)} markets → {len(relevant)} relevant "
              f"with assets (floor={QUESTION_RELEVANCE_FLOOR})")
        return results

    finally:
        await gemini.close()


async def scan_and_evaluate() -> list[EvaluatedMarket]:
    """Convenience: run scanner + evaluator in sequence."""
    from ingest.scanner import scan
    markets = await scan()
    return await evaluate(markets)


if __name__ == "__main__":
    results = asyncio.run(scan_and_evaluate())
    for r in results:
        if r.assets:
            syms = ", ".join(a["symbol"] for a in r.assets)
            print(f"  rel={r.question_relevance:.2f}  {syms:30s}  {r.market.question[:60]}")
