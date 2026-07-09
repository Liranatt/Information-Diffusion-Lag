"""
Two-pass evaluation pipeline for Polymarket markets.

Pass 1: Relevance scoring + positive-sentiment gating (pattern-based).
Pass 2: Asset mapping to US-listed equities/ETFs (ticker extraction + keyword mapping).

Results are persisted to the database in the same schema as the Gemini pipeline.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx

from database.db_connection import connect
from database.backtesting.schema import SCHEMA
from pipeline.scanner import fetch_markets_in_range, ScannedMarket, ALLOWED_TAG_SLUGS
from LLM.build_world import (
    AssetCandidate,
    BatchedAssetWorld,
    IBTradableAsset,
    IBAssetCatalogIndex,
    ib_asset_catalog_index,
    ib_symbol_key,
    QUESTION_RELEVANCE_FLOOR,
)

MODEL_NAME = "claude-opus-4-20250514"
PROMPT_VERSION = "claude-pipeline-v1"

# ── Ticker extraction ────────────────────────────────────────────────────────
# Matches "(TICKER)" in questions like "Will Apple (AAPL) close above $300?"
# Excludes price-direction markers (HIGH, LOW) and commodity abbreviations (WTI, NG)
_NOT_TICKERS = {"HIGH", "LOW", "WTI", "NG", "CL"}
TICKER_RE = re.compile(r"\(([A-Z]{1,6})\)")


def _extract_ticker(question: str) -> str | None:
    for m in TICKER_RE.finditer(question):
        candidate = m.group(1)
        if candidate not in _NOT_TICKERS:
            return candidate
    return None

# Matches "S&P 500 (SPY)" or "S&P 500 (SPX)"
SP500_RE = re.compile(r"S&P\s*500|SPY|SPX", re.IGNORECASE)

# Price direction indicators
POSITIVE_PRICE_RE = re.compile(
    r"\b(HIGH|above|reach|hit\s+\(HIGH\)|finish\s+.+\s+above|close\s+above|closes\s+above)\b",
    re.IGNORECASE,
)
NEGATIVE_PRICE_RE = re.compile(
    r"\b(LOW|dip\s+to|hit\s+\(LOW\)|below|crash|plunge|tumble|fall\s+below|drop)\b",
    re.IGNORECASE,
)

# Crypto keywords
CRYPTO_RE = re.compile(
    r"\b(Bitcoin|Ethereum|XRP|Solana|Dogecoin|Cardano|BTC|ETH|Hyperliquid|Litecoin|"
    r"Polkadot|Chainlink|Avalanche|BNB|Toncoin|Sui\b|Pepe\b|Shiba)",
    re.IGNORECASE,
)

# Commodity keywords and their ETF mappings
COMMODITY_MAP = {
    "gold": ("GLD", "Gold ETF"),
    "xauusd": ("GLD", "Gold ETF"),
    "silver": ("SLV", "Silver ETF"),
    "xagusd": ("SLV", "Silver ETF"),
    "crude oil": ("USO", "US Oil Fund"),
    "wti": ("USO", "US Oil Fund"),
    "oil": ("USO", "US Oil Fund"),
    "natural gas": ("UNG", "US Natural Gas Fund"),
    "ng": ("UNG", "US Natural Gas Fund"),
    "copper": ("CPER", "Copper ETF"),
}

COMMODITY_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in COMMODITY_MAP) + r")\b",
    re.IGNORECASE,
)

# Earnings / company-specific keywords
EARNINGS_POS_RE = re.compile(
    r"\b(beat|exceed|above|surpass|top|growth|revenue\s+above|EPS\s+above|"
    r"sales\s+above|outperform|upgrade|approve|approval|launch|IPO)\b",
    re.IGNORECASE,
)
EARNINGS_NEG_RE = re.compile(
    r"\b(miss|below|less\s+than|under|decline|bankruptcy|bankrupt|layoff|recall|"
    r"downgrade|reject|fail|lose|loss|default|deficit|shortfall)\b",
    re.IGNORECASE,
)

# Macro economic data keywords
MACRO_RE = re.compile(
    r"\b(GDP|CPI|PPI|payroll|nonfarm|unemployment|jobless|JOLTS|"
    r"consumer\s+sentiment|UMich|PMI|ISM|retail\s+sales|"
    r"durable\s+goods|housing\s+starts|building\s+permits|"
    r"industrial\s+production|trade\s+balance|"
    r"Fed\s+funds?\s+rate|interest\s+rate|FOMC|"
    r"Bank\s+of\s+Canada|ECB|BOJ|BOE)\b",
    re.IGNORECASE,
)

# Podcast / word-count / celebrity (irrelevant noise)
NOISE_RE = re.compile(
    r"\b(said\s+during|podcast|episode|times\s+during\s+earnings\s+call|"
    r"be\s+said|say\s+.+during|App\s+[A-Z]\s+be\s+#|"
    r"#\d+\s+(Free|Paid)\s+App|App\s+Store|"
    r"Polymarket\s+mindshare|net\s+worth\s+be\s+between|"
    r"Shadowrocket|CapCut|Lemonade\s+Stand\s+Podcast|"
    r"All-In\s+Podcast|Glow\b|Football\b|warships|"
    r"SpaceX\s+have\s+exactly\s+\d+\s+launches)\b",
    re.IGNORECASE,
)

# Geopolitics
GEO_POSITIVE_RE = re.compile(
    r"\b(peace|ceasefire|agreement|treaty|normalize|lifted|withdraw|"
    r"returns\s+to\s+normal|sign|memorandum|handshake|speak\s+to|"
    r"diplomatic|talks|negotiate)\b",
    re.IGNORECASE,
)
GEO_NEGATIVE_RE = re.compile(
    r"\b(capture|invade|attack|bomb|strike|sanction|blockade|"
    r"escalat|conflict|war|missile|nuclear|collapse|coup|crisis)\b",
    re.IGNORECASE,
)

# AI model ranking questions
AI_MODEL_RE = re.compile(
    r"\b(best\s+AI\s+model|top\s+AI\s+model|#\d+\s+AI\s+model|"
    r"best\s+Coding\s+AI|best\s+Math\s+AI|best\s+AI\s+Agent|"
    r"second.best|third.best|largest\s+company.*market\s+cap)\b",
    re.IGNORECASE,
)

# Earnings call word-count (say X during earnings call)
EARNINGS_CALL_WORD_RE = re.compile(
    r'say\s+"?\w+"?\s+(during|in)\s+earnings\s+call',
    re.IGNORECASE,
)

# IPO questions
IPO_RE = re.compile(r"\bIPO\b", re.IGNORECASE)

# Price bracket questions ("close at $X-$Y")
PRICE_BRACKET_RE = re.compile(
    r"close\s+at\s+\$[\d,.]+\s*-\s*\$[\d,.]+",
    re.IGNORECASE,
)

# Country ETF pattern
COUNTRY_ETF_RE = re.compile(r"\b([A-Z]+)\s+ETF\s+\(([A-Z]{2,5})\)")

# Known company → ticker mappings for questions that don't include ticker
COMPANY_TICKER_MAP = {
    "apple": "AAPL",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "microsoft": "MSFT",
    "amazon": "AMZN",
    "meta": "META",
    "nvidia": "NVDA",
    "tesla": "TSLA",
    "netflix": "NFLX",
    "alibaba": "BABA",
    "jpmorgan": "JPM",
    "jpmorgan chase": "JPM",
    "salesforce": "CRM",
    "oracle": "ORCL",
    "coinbase": "COIN",
    "palantir": "PLTR",
    "gamestop": "GME",
    "airbnb": "ABNB",
    "opendoor": "OPEN",
    "micron": "MU",
    "rocket lab": "RKLB",
    "uber": "UBER",
    "openai": None,  # private
    "anthropic": None,  # private
    "deepseek": None,  # private
    "spacex": None,  # private
    "bytedance": None,  # private
    "ubisoft": "UBSFY",
    "darden restaurants": "DRI",
    "darden": "DRI",
    "dick's sporting goods": "DKS",
    "innio": None,  # pre-IPO
    "waymo": "GOOGL",
}

# Strait of Hormuz / Iran-related
HORMUZ_RE = re.compile(r"\b(Strait\s+of\s+Hormuz|Hormuz|Iran|Iranian)\b", re.IGNORECASE)

# Home value / real estate
REAL_ESTATE_RE = re.compile(r"\b(median\s+home\s+value|housing|home\s+price)\b", re.IGNORECASE)

# Fed / monetary policy
FED_RE = re.compile(r"\b(Fed\s+cut|rate\s+cut|FOMC|Federal\s+Reserve|easing|tightening)\b", re.IGNORECASE)

# Trump geopolitics
TRUMP_DIPLO_RE = re.compile(r"\bTrump\s+(speak|shake|meet|call|agree)\b", re.IGNORECASE)

# Treasury / blockchain
TREASURY_RE = re.compile(r"\b(Treasury|T-bill|blockchain|stablecoin)\b", re.IGNORECASE)

# "deliver less than" / vehicle deliveries
DELIVERIES_RE = re.compile(r"\bdeliver\s+(less|fewer|more|above|below)\b", re.IGNORECASE)


@dataclass
class Pass1Result:
    market_id: str
    question_relevance: float
    positive_sentiment: bool
    reason: str


@dataclass
class Pass2Result:
    market_id: str
    universe_name: str
    universe_reason: str
    assets: list[AssetCandidate]


def evaluate_pass1(m: dict) -> Pass1Result:
    """Score relevance and sentiment for a single market question."""
    q = m["question"]
    tags = m.get("tags", [])
    q_lower = q.lower()

    # ── Noise filter ──
    if NOISE_RE.search(q):
        return Pass1Result(m["market_id"], 0.02, True, "No mechanical US-equity channel: noise/entertainment category.")

    # ── Earnings call word-count ──
    if EARNINGS_CALL_WORD_RE.search(q):
        ticker = _extract_ticker(q)
        company = ticker if ticker else "company"
        return Pass1Result(m["market_id"], 0.05, True, f"Word count during {company} earnings call has no direct equity channel.")

    # ── Crypto ──
    if CRYPTO_RE.search(q) and not _extract_ticker(q):
        is_positive = bool(POSITIVE_PRICE_RE.search(q)) and not bool(NEGATIVE_PRICE_RE.search(q))
        if NEGATIVE_PRICE_RE.search(q) and not POSITIVE_PRICE_RE.search(q):
            is_positive = False
        elif "dip" in q_lower:
            is_positive = False
        else:
            is_positive = True
        return Pass1Result(
            m["market_id"], 0.12, is_positive,
            "Crypto price target; minimal direct US-equity channel (indirect via COIN/MSTR)."
        )

    # ── S&P 500 / SPY / SPX ──
    if SP500_RE.search(q):
        is_positive = bool(POSITIVE_PRICE_RE.search(q))
        if NEGATIVE_PRICE_RE.search(q) and not is_positive:
            is_positive = False
        return Pass1Result(
            m["market_id"], 0.80, is_positive,
            "S&P 500 price target directly reprices the broad US equity market."
        )

    # ── Country ETF ──
    country_match = COUNTRY_ETF_RE.search(q)
    if country_match:
        is_positive = bool(POSITIVE_PRICE_RE.search(q))
        if NEGATIVE_PRICE_RE.search(q) and not is_positive:
            is_positive = False
        return Pass1Result(
            m["market_id"], 0.55, is_positive,
            f"Country ETF ({country_match.group(2)}) price target; indirect US-equity channel through cross-border flows."
        )

    # ── Individual stock price target ──
    ticker = _extract_ticker(q)
    if ticker and not EARNINGS_CALL_WORD_RE.search(q):

        # Determine sentiment from price direction
        has_high = "(HIGH)" in q or POSITIVE_PRICE_RE.search(q)
        has_low = "(LOW)" in q or NEGATIVE_PRICE_RE.search(q)

        if PRICE_BRACKET_RE.search(q):
            is_positive = True
            reason = f"Stock price bracket for {ticker}; neutral direction, treated as positive (price stability)."
        elif has_low and not has_high:
            is_positive = False
            reason = f"Stock price decline target for {ticker}; negative-sentiment event."
        else:
            is_positive = True
            reason = f"Stock price upside target for {ticker}; directly reprices this US-listed equity."

        # Earnings-specific questions with ticker
        if any(kw in q_lower for kw in ("earnings", "revenue", "eps", "sales", "growth", "deliver")):
            if EARNINGS_NEG_RE.search(q):
                is_positive = False
                reason = f"Negative earnings outcome for {ticker}; adverse event for the stock."
            else:
                is_positive = True
                reason = f"Positive earnings/fundamental metric for {ticker}; directly reprices the stock."
            return Pass1Result(m["market_id"], 0.92, is_positive, reason)

        return Pass1Result(m["market_id"], 0.85, is_positive, reason)

    # ── Commodities ──
    commodity_match = COMMODITY_RE.search(q)
    if commodity_match:
        commodity = commodity_match.group(1).lower()
        is_positive = bool(POSITIVE_PRICE_RE.search(q))
        if NEGATIVE_PRICE_RE.search(q) and not is_positive:
            is_positive = False
        return Pass1Result(
            m["market_id"], 0.68, is_positive,
            f"Commodity ({commodity}) price target; reprices US commodity equities and related ETFs."
        )

    # ── Strait of Hormuz / Iran ──
    if HORMUZ_RE.search(q):
        is_positive = bool(GEO_POSITIVE_RE.search(q))
        if GEO_NEGATIVE_RE.search(q):
            is_positive = False
        if "returns to normal" in q_lower or "lifted" in q_lower or "withdraw" in q_lower:
            is_positive = True
        return Pass1Result(
            m["market_id"], 0.62, is_positive,
            "Strait of Hormuz / Iran event; reprices oil, defense, and shipping equities."
        )

    # ── Macro data ──
    if MACRO_RE.search(q):
        is_positive = True
        if any(kw in q_lower for kw in ("below", "decline", "negative", "under", "less than")):
            is_positive = False
        if "cut" in q_lower and "fed" in q_lower:
            is_positive = True  # rate cuts are positive/accommodative
        if "no change" in q_lower:
            is_positive = True  # status quo = positive/stable
        return Pass1Result(
            m["market_id"], 0.40, is_positive,
            "Macro economic data print; moderate mechanical channel to US equities via rates/sentiment."
        )

    # ── AI model rankings ──
    if AI_MODEL_RE.search(q):
        company_lower = None
        for company, ticker in COMPANY_TICKER_MAP.items():
            if company in q_lower:
                company_lower = company
                break
        is_positive = True
        return Pass1Result(
            m["market_id"], 0.30, is_positive,
            f"AI model ranking for {company_lower or 'company'}; indirect equity channel through competitive positioning."
        )

    # ── IPO questions ──
    if IPO_RE.search(q):
        is_positive = True
        if "not IPO" in q:
            is_positive = False
        return Pass1Result(
            m["market_id"], 0.50, is_positive,
            "IPO event; moderate equity channel for the company and sector."
        )

    # ── Vehicle deliveries ──
    if DELIVERIES_RE.search(q):
        is_positive = "more" in q_lower or "above" in q_lower
        if "less" in q_lower or "fewer" in q_lower or "below" in q_lower:
            is_positive = False
        return Pass1Result(
            m["market_id"], 0.88, is_positive,
            "Vehicle delivery target; directly reprices the manufacturer."
        )

    # ── Trump diplomacy ──
    if TRUMP_DIPLO_RE.search(q):
        is_positive = True
        return Pass1Result(
            m["market_id"], 0.35, is_positive,
            "Diplomatic engagement; positive geopolitical development with modest equity channel."
        )

    # ── Real estate ──
    if REAL_ESTATE_RE.search(q):
        is_positive = True
        if "less than" in q_lower or "below" in q_lower or "decline" in q_lower:
            is_positive = False
        return Pass1Result(
            m["market_id"], 0.30, is_positive,
            "Real estate price metric; indirect US-equity channel via REITs and homebuilders."
        )

    # ── Geopolitics (general) ──
    geo_tags = {"geopolitics", "iran", "us-x-iran", "strait-of-hormuz"}
    if geo_tags.intersection(set(tags)):
        is_positive = bool(GEO_POSITIVE_RE.search(q))
        if GEO_NEGATIVE_RE.search(q):
            is_positive = False
        if not GEO_POSITIVE_RE.search(q) and not GEO_NEGATIVE_RE.search(q):
            is_positive = True  # default: diplomatic questions tend to be about positive outcomes
        return Pass1Result(
            m["market_id"], 0.45, is_positive,
            "Geopolitical event; moderate US-equity channel through risk sentiment and commodities."
        )

    # ── Company-name questions without ticker (e.g. "Will Google have the best AI model?") ──
    for company, ticker in COMPANY_TICKER_MAP.items():
        if company in q_lower and ticker is not None:
            is_positive = True
            if EARNINGS_NEG_RE.search(q):
                is_positive = False
            return Pass1Result(
                m["market_id"], 0.40, is_positive,
                f"Company event for {company}; moderate equity channel through {ticker}."
            )

    # ── Private company events (OpenAI, Anthropic, SpaceX, etc.) ──
    for company, ticker in COMPANY_TICKER_MAP.items():
        if company in q_lower and ticker is None:
            return Pass1Result(
                m["market_id"], 0.20, True,
                f"Private company ({company}) event; no directly tradable US-listed equity."
            )

    # ── Fallback: tag-filtered but unrecognized pattern ──
    is_positive = not bool(NEGATIVE_PRICE_RE.search(q))
    return Pass1Result(
        m["market_id"], 0.25, is_positive,
        "Unrecognized pattern; has allowed tag but unclear mechanical US-equity channel."
    )


def evaluate_pass2(
    m: dict,
    p1: Pass1Result,
    catalog: IBAssetCatalogIndex,
) -> Pass2Result:
    """Map a relevant, positive-sentiment market to specific US-listed assets."""
    q = m["question"]
    q_lower = q.lower()
    assets: list[AssetCandidate] = []

    # ── Extract explicit ticker ──
    sym = _extract_ticker(q)
    if sym:
        key = ib_symbol_key(sym)
        ib_asset = catalog.by_symbol.get(key)
        if ib_asset:
            rel_type = "sector_etf" if ib_asset.asset_class == "etf" else "direct_company"

            if any(kw in q_lower for kw in ("earnings", "revenue", "eps", "deliver", "sales")):
                reason = f"Named company {sym}; earnings/fundamental metric directly reprices this equity."
                strength = 0.95
            elif "(HIGH)" in q or "above" in q_lower:
                reason = f"Price upside target for {sym}; direct company exposure."
                strength = 0.90
            elif PRICE_BRACKET_RE.search(q):
                reason = f"Price bracket target for {sym}; direct company exposure, neutral direction."
                strength = 0.85
            else:
                reason = f"Price target for {sym}; direct company exposure to this US-listed equity."
                strength = 0.85

            assets.append(AssetCandidate(
                symbol=ib_asset.symbol,
                asset_name=ib_asset.asset_name,
                asset_class=ib_asset.asset_class,
                relationship_type=rel_type,
                reason=reason,
                connection_strength=strength,
            ))

    # ── S&P 500 / SPY ──
    if SP500_RE.search(q) and not any(a.symbol == "SPY" for a in assets):
        spy = catalog.by_symbol.get("SPY")
        if spy:
            assets.append(AssetCandidate(
                symbol="SPY",
                asset_name=spy.asset_name,
                asset_class="etf",
                relationship_type="sector_etf",
                reason="S&P 500 price target; direct broad-market ETF exposure.",
                connection_strength=0.90,
            ))

    # ── Commodity mapping ──
    commodity_match = COMMODITY_RE.search(q)
    if commodity_match and not assets:
        commodity_key = commodity_match.group(1).lower()
        for pattern, (etf_sym, etf_name) in COMMODITY_MAP.items():
            if pattern in commodity_key:
                ib_asset = catalog.by_symbol.get(ib_symbol_key(etf_sym))
                if ib_asset:
                    assets.append(AssetCandidate(
                        symbol=ib_asset.symbol,
                        asset_name=ib_asset.asset_name,
                        asset_class="etf",
                        relationship_type="commodity_proxy",
                        reason=f"Commodity price target ({commodity_key}); {etf_sym} provides direct exposure.",
                        connection_strength=0.75,
                    ))
                break

    # ── Crypto → COIN mapping ──
    if CRYPTO_RE.search(q) and not assets:
        coin = catalog.by_symbol.get("COIN")
        if coin:
            assets.append(AssetCandidate(
                symbol="COIN",
                asset_name=coin.asset_name,
                asset_class="stock",
                relationship_type="direct_company",
                reason="Crypto price movement; Coinbase revenue is directly tied to crypto trading volume and prices.",
                connection_strength=0.35,
            ))

    # ── Country ETF ──
    country_match = COUNTRY_ETF_RE.search(q)
    if country_match and not assets:
        etf_sym = country_match.group(2)
        ib_asset = catalog.by_symbol.get(ib_symbol_key(etf_sym))
        if ib_asset:
            assets.append(AssetCandidate(
                symbol=ib_asset.symbol,
                asset_name=ib_asset.asset_name,
                asset_class="etf",
                relationship_type="country_etf",
                reason=f"Country ETF price target; direct exposure via {etf_sym}.",
                connection_strength=0.80,
            ))

    # ── Company name fallback (no ticker in question) ──
    if not assets:
        for company, ticker in COMPANY_TICKER_MAP.items():
            if company in q_lower and ticker:
                ib_asset = catalog.by_symbol.get(ib_symbol_key(ticker))
                if ib_asset:
                    assets.append(AssetCandidate(
                        symbol=ib_asset.symbol,
                        asset_name=ib_asset.asset_name,
                        asset_class=ib_asset.asset_class,
                        relationship_type="direct_company" if ib_asset.asset_class == "stock" else "sector_etf",
                        reason=f"Company event for {company}; {ticker} is the primary listed equity.",
                        connection_strength=0.60,
                    ))
                    break

    # ── Hormuz / Iran → oil + defense ──
    if HORMUZ_RE.search(q) and not assets:
        for sym, name, rel, reason in [
            ("USO", "US Oil Fund", "commodity_proxy",
             "Strait of Hormuz disruption reprices crude oil directly."),
            ("XLE", "Energy Select Sector SPDR Fund", "sector_etf",
             "Oil supply disruption reprices US energy sector equities."),
        ]:
            ib_asset = catalog.by_symbol.get(ib_symbol_key(sym))
            if ib_asset:
                assets.append(AssetCandidate(
                    symbol=ib_asset.symbol,
                    asset_name=ib_asset.asset_name,
                    asset_class=ib_asset.asset_class,
                    relationship_type=rel,
                    reason=reason,
                    connection_strength=0.65,
                ))

    # ── Macro data → SPY fallback ──
    if MACRO_RE.search(q) and not assets:
        spy = catalog.by_symbol.get("SPY")
        if spy:
            assets.append(AssetCandidate(
                symbol="SPY",
                asset_name=spy.asset_name,
                asset_class="etf",
                relationship_type="sector_etf",
                reason="Macro data print reprices broad US equity market expectations.",
                connection_strength=0.40,
            ))

    # ── Fed / rates → TLT + SPY ──
    if FED_RE.search(q) and not assets:
        for sym, reason, strength in [
            ("TLT", "Rate decisions directly reprice long-duration treasuries.", 0.80),
            ("SPY", "Monetary policy reprices broad US equity market.", 0.50),
        ]:
            ib_asset = catalog.by_symbol.get(ib_symbol_key(sym))
            if ib_asset:
                assets.append(AssetCandidate(
                    symbol=ib_asset.symbol,
                    asset_name=ib_asset.asset_name,
                    asset_class=ib_asset.asset_class,
                    relationship_type="sector_etf",
                    reason=reason,
                    connection_strength=strength,
                ))

    universe_name = (m.get("event_title") or m["question"])[:200]
    universe_reason = p1.reason[:700] if len(p1.reason) >= 20 else (
        "Pattern-based asset mapping identified mechanically-exposed US-listed instruments."
    )

    return Pass2Result(
        market_id=m["market_id"],
        universe_name=universe_name,
        universe_reason=universe_reason,
        assets=assets,
    )


def build_batched_world(
    m: dict,
    p1: Pass1Result,
    p2: Pass2Result | None,
) -> BatchedAssetWorld:
    """Build the final BatchedAssetWorld for database persistence."""
    if p2 is None or not p2.assets:
        return BatchedAssetWorld(
            request_id=m["market_id"],
            universe_name=(m.get("event_title") or m["question"])[:200],
            universe_reason=p1.reason[:700] if len(p1.reason) >= 20 else (
                "Relevance gate scored this question below the floor for mechanical US-equity repricing."
            ),
            assets=[],
            question_relevance=p1.question_relevance,
        )
    return BatchedAssetWorld(
        request_id=m["market_id"],
        universe_name=p2.universe_name,
        universe_reason=p2.universe_reason,
        assets=p2.assets,
        question_relevance=p1.question_relevance,
    )


async def load_tradable_catalog() -> IBAssetCatalogIndex:
    conn = await connect()
    try:
        rows = await conn.fetch(
            f"SELECT official_symbol, security_name, is_etf, exchange "
            f"FROM {SCHEMA}.historical_us_security_master"
        )
    finally:
        await conn.close()
    assets = [
        IBTradableAsset(
            symbol=r["official_symbol"],
            asset_name=r["security_name"],
            asset_class="etf" if r["is_etf"] else "stock",
            primary_exchange=r["exchange"],
            stock_type="ETF" if r["is_etf"] else "COMMON",
        )
        for r in rows
    ]
    print(f"[catalog] Loaded {len(assets)} IB-tradable securities")
    return ib_asset_catalog_index(assets)


async def persist_world(conn, mkt: dict, world: BatchedAssetWorld) -> None:
    if not world.assets:
        return
    world_id = uuid.uuid4()
    await conn.execute(
        f"""INSERT INTO {SCHEMA}.historical_asset_worlds
            (world_id, input_hash, market_id, event_id, pass_number,
             as_of, model_name, prompt_version, llm_input, llm_output,
             universe_name, universe_reason)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            ON CONFLICT (input_hash) DO NOTHING""",
        world_id,
        f"eval:{mkt['market_id']}",
        mkt["market_id"],
        mkt["event_id"],
        1,
        datetime.now(timezone.utc),
        MODEL_NAME,
        PROMPT_VERSION,
        "{}",
        world.model_dump_json(),
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
            f"[{asset.relationship_type}] {asset.reason}",
            asset.connection_strength * world.question_relevance,
        )


async def main():
    cache_path = Path("data/markets_cache.json")

    if not cache_path.exists():
        print("Fetching markets from Polymarket...")
        start = datetime(2026, 5, 27, tzinfo=timezone.utc)
        end = datetime(2026, 7, 1, tzinfo=timezone.utc)
        async with httpx.AsyncClient(timeout=httpx.Timeout(60)) as client:
            markets_raw = await fetch_markets_in_range(client, start=start, end=end)
        records = []
        for m in markets_raw:
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
        cache_path.parent.mkdir(exist_ok=True)
        cache_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        print(f"Cached {len(records)} markets to {cache_path}")
    else:
        print(f"Loading cached markets from {cache_path}...")

    markets = json.loads(cache_path.read_text(encoding="utf-8"))
    print(f"Total markets: {len(markets)}")

    # ── Pass 1: Relevance + Sentiment ──
    print("\n=== PASS 1: Relevance + Sentiment Gate ===")
    pass1_results: dict[str, Pass1Result] = {}
    for m in markets:
        p1 = evaluate_pass1(m)
        pass1_results[m["market_id"]] = p1

    # Stats
    total = len(pass1_results)
    above_floor = sum(1 for p in pass1_results.values() if p.question_relevance >= QUESTION_RELEVANCE_FLOOR)
    positive = sum(1 for p in pass1_results.values() if p.positive_sentiment)
    pass_both = sum(
        1 for p in pass1_results.values()
        if p.question_relevance >= QUESTION_RELEVANCE_FLOOR and p.positive_sentiment
    )
    neg_filtered = sum(
        1 for p in pass1_results.values()
        if p.question_relevance >= QUESTION_RELEVANCE_FLOOR and not p.positive_sentiment
    )

    print(f"  Total markets:                {total:,}")
    print(f"  Above relevance floor (>={QUESTION_RELEVANCE_FLOOR}):  {above_floor:,}")
    print(f"  Positive sentiment:           {positive:,}")
    print(f"  Pass BOTH gates:              {pass_both:,}")
    print(f"  Relevant but neg sentiment:   {neg_filtered:,}")
    print(f"  Below relevance floor:        {total - above_floor:,}")

    # Relevance distribution
    buckets = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01]
    print("\n  Relevance distribution:")
    for i in range(len(buckets) - 1):
        lo, hi = buckets[i], buckets[i + 1]
        count = sum(1 for p in pass1_results.values() if lo <= p.question_relevance < hi)
        bar = "#" * (count // 20)
        label = f"  [{lo:.1f}, {hi:.1f})" if hi < 1.01 else f"  [{lo:.1f}, 1.0]"
        print(f"  {label:12s} {count:5d}  {bar}")

    # ── Pass 2: Asset Mapping ──
    print("\n=== PASS 2: Asset Mapping ===")
    catalog = await load_tradable_catalog()

    pass2_results: dict[str, Pass2Result] = {}
    for m in markets:
        p1 = pass1_results[m["market_id"]]
        if p1.question_relevance >= QUESTION_RELEVANCE_FLOOR and p1.positive_sentiment:
            p2 = evaluate_pass2(m, p1, catalog)
            pass2_results[m["market_id"]] = p2

    with_assets = sum(1 for p2 in pass2_results.values() if p2.assets)
    no_assets = sum(1 for p2 in pass2_results.values() if not p2.assets)
    print(f"  Markets reaching Pass 2: {len(pass2_results):,}")
    print(f"  With mapped assets:      {with_assets:,}")
    print(f"  No tradable asset found: {no_assets:,}")

    # Symbol frequency
    sym_counts: dict[str, int] = {}
    for p2 in pass2_results.values():
        for a in p2.assets:
            sym_counts[a.symbol] = sym_counts.get(a.symbol, 0) + 1
    print(f"\n  Top 20 mapped symbols:")
    for sym, count in sorted(sym_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"    {sym:8s} {count:5d}")

    # ── Build worlds and persist ──
    print("\n=== PERSISTING TO DATABASE ===")
    conn = await connect()
    persisted = 0
    skipped = 0
    try:
        for m in markets:
            p1 = pass1_results[m["market_id"]]
            p2 = pass2_results.get(m["market_id"])
            world = build_batched_world(m, p1, p2)
            if world.assets:
                await persist_world(conn, m, world)
                persisted += 1
            else:
                skipped += 1
    finally:
        await conn.close()

    print(f"  Persisted: {persisted:,} worlds with assets")
    print(f"  Skipped:   {skipped:,} (empty worlds / below gates)")

    # ── Examples: what PASSES both gates ──
    import random
    random.seed(42)
    passed_markets = [m for m in markets if pass2_results.get(m["market_id"]) and pass2_results[m["market_id"]].assets]
    random.shuffle(passed_markets)
    print("\n=== EXAMPLES: PASSED both gates (30 random) ===")
    for m in passed_markets[:30]:
        p1 = pass1_results[m["market_id"]]
        p2 = pass2_results[m["market_id"]]
        q = m["question"][:90].encode("ascii", "replace").decode()
        syms = ", ".join(a.symbol for a in p2.assets)
        print(f"  rel={p1.question_relevance:.2f} + [{syms:8s}] {q}")

    # ── Examples: FILTERED by relevance floor ──
    below_floor = [m for m in markets if pass1_results[m["market_id"]].question_relevance < QUESTION_RELEVANCE_FLOOR]
    random.shuffle(below_floor)
    print(f"\n=== EXAMPLES: FILTERED by relevance < {QUESTION_RELEVANCE_FLOOR} (30 random) ===")
    for m in below_floor[:30]:
        p1 = pass1_results[m["market_id"]]
        q = m["question"][:90].encode("ascii", "replace").decode()
        print(f"  rel={p1.question_relevance:.2f}   {q}")
        print(f"         reason: {p1.reason[:100]}")

    # ── Examples: FILTERED by negative sentiment ──
    neg_sentiment = [
        m for m in markets
        if pass1_results[m["market_id"]].question_relevance >= QUESTION_RELEVANCE_FLOOR
        and not pass1_results[m["market_id"]].positive_sentiment
    ]
    random.shuffle(neg_sentiment)
    print(f"\n=== EXAMPLES: FILTERED by negative sentiment (20 random) ===")
    for m in neg_sentiment[:20]:
        p1 = pass1_results[m["market_id"]]
        q = m["question"][:90].encode("ascii", "replace").decode()
        print(f"  rel={p1.question_relevance:.2f} - {q}")
        print(f"         reason: {p1.reason[:100]}")


if __name__ == "__main__":
    asyncio.run(main())
