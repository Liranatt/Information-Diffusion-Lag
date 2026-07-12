"""Cheap keyword/regex pre-filter — the first, free stage of ingestion.

`regex_prefilter` scores a market question's US-equity relevance and sentiment
without any API call, so obviously-irrelevant markets (podcasts, word-counts,
app rankings) are culled before the paid Gemini stages. Markets clearing the
relevance floor with positive sentiment go on to the Gemini catalyst/relevance
gate and asset mapping.

This is a pure `re` cascade — there is no LLM here. It was formerly the
misleadingly named `run_claude_pipeline.evaluate_pass1` (no Claude/Anthropic API
was ever involved).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

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


# Cost pre-cull threshold — NOT a relevance judgment.
#
# The regex prefilter no longer decides which CATEGORIES of event are relevant
# (that was a hindsight-tuned gate: Iran 0.62 in, macro 0.40 / general-geo 0.45
# out, hand-drawn around a 0.60 floor). Relevance and sentiment are now judged
# only by the Gemini catalyst gate + relevance gate (ingest/world.py), which
# grade every surviving market with calibrated, as-of-date reasoning and
# generalize instead of pattern-matching the wording.
#
# This floor exists solely to avoid paying Gemini to classify questions with no
# mechanical US-equity channel at all — podcasts, earnings-call word counts, raw
# crypto price targets (scored 0.02–0.12). Everything with a plausible channel
# (macro, geopolitics, diplomacy, IPOs, country ETFs, single names) passes
# through to Gemini to judge. Keep this LOW; it must never encode which
# catalysts are expected to be profitable.
NOISE_FLOOR = 0.15


# ── Structural noise removal (data cleaning, NOT relevance scoring) ───────────
#
# These patterns drop question FORMATS that have no discrete tradable US-equity
# catalyst at all, regardless of subject: model leaderboards, market-cap
# rankings (an outcome mechanically equal to the stock price, not a catalyst),
# search-popularity charts, app-store positions, keynote word-bingo, CEO-naming
# lotteries, arbitrary price-level ladders, home-value brackets, personal-wealth
# trivia. This is data cleaning — removing things that are not financial-market
# catalysts — and it must NEVER encode which real catalysts are relevant or
# profitable. That judgment (is a Fed print / a foreign election / an Iran strike
# worth trading?) belongs entirely to the Gemini relevance gate. Every pattern
# must be defensible as "this FORMAT can never map to a US-listed equity
# catalyst", independent of any backtest outcome. Validated against a full
# 2024-07→2026-05 Gamma scan: removes ~3,000 junk questions, zero of which were
# in the pre-existing candidate universe.
_STRUCTURAL_NOISE: list[tuple[str, re.Pattern]] = [
    ("ai_model_leaderboard", re.compile(r"\bAI model\b", re.I)),
    ("market_cap_ranking", re.compile(
        r"\b(largest|second[- ]largest|third[- ]largest|most valuable|second most valuable)"
        r"\b[\w\s'-]{0,40}\b(company|market cap)\b|\bby market cap\b", re.I)),
    ("search_popularity", re.compile(
        r"\bYear in Search\b|\bmost searched\b|\bsearched\b[\w\s]{0,25}\bon Google\b"
        r"|#\s*\d+\s+searched\b", re.I)),
    ("app_store_rank", re.compile(
        r"#\s*\d+\s+(Free|Paid|Grossing|utility|finance)\s+App\b|\btop\s+\d+\s+app\b"
        r"|\bbe\s+#1\b[\w\s]{0,30}\bApp\b|\b#1\s+(Free|Paid|Grossing)\b", re.I)),
    ("speech_wordcount", re.compile(
        r'\bsay\s+["“].+?["”]|\b(keynote|product showcase)\b|\btweet\s+\d', re.I)),
    ("ceo_naming_lottery", re.compile(
        r"\bbe\s+announced\s+as\s+the\s+next\s+CEO\b|\bbe\s+the\s+next\s+CEO\b", re.I)),
    ("price_level_ladder", re.compile(r"\b(reach|dip to)\s+\$?[\d,.]+", re.I)),
    ("home_value_bracket", re.compile(r"\bmedian home value\b", re.I)),
    ("misc_non_catalyst", re.compile(
        r"\bnext Pope\b|\bNOF1\.ai\b|\bokbet\b|\brichest person\b|\bnet worth be\b"
        r"|\bto its[\w\s,]{0,20}13F\b", re.I)),
    # IPO / pre-listing: no US-listed instrument exists at signal time, so the
    # strategy has nothing to trade. (M&A about a public acquirer -- "Apple
    # acquires X" -- does NOT match and is left for Gemini.)
    ("ipo_prelisting", re.compile(r"\bIPO\b", re.I)),
    # Foreign parliamentary seat-counts and election horse-race placement -- no
    # mechanical US-equity channel. (Bare "meet with"/"talk to" diplomacy and
    # foreign GDP prints are deliberately NOT here: a summit or a major-economy
    # print can be a real signal, so that judgment stays with the Gemini gate.)
    ("election_horserace", re.compile(
        r"\bwin the (most|second most|third most|fewest) seats\b"
        r"|\bcome in (first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\b[\w\s]{0,15}\bround\b"
        r"|\bqualify for the (second|next|runoff|1st|first) round\b"
        r"|\b(Legislative Assembly|National People'?s Assembly|House of Councillors|House of Representatives)\b[\w\s]{0,10}\belection\b"
        r"|\bwin the \d{4}[\w\s]{0,30}\bpresidential election\b", re.I)),
    # Awards / sports / entertainment outcomes.
    ("awards_sports", re.compile(
        r"\bNobel\b|\bOscar(s)?\b|\bGrammy(s)?\b|\bSuper Bowl\b|\bWorld Cup\b|\bbox office\b"
        r"|\bPerson of the Year\b|\bBallon d'?Or\b|\bGolden Globe|\bchampionship\b|\bthe Masters\b", re.I)),
    # Tweet / social-post occurrence bets.
    ("tweet_occurrence", re.compile(r"\btweet", re.I)),
    # Crypto listing / token launch (not a US-listed equity catalyst).
    ("crypto_listing", re.compile(
        r"\bnext coin listed\b|\bcoin listed on Robinhood\b|\blaunch a token\b|\btoken launch\b", re.I)),
    # Celebrity divorce.
    ("divorce", re.compile(r"\bdivorce\b", re.I)),
    # Service-uptime / outage counting.
    ("service_uptime", re.compile(
        r"\bgo(es)? down\s+\d|\b(full|partial)\s+outage\b|\bgo down\b[\w\s]{0,10}\btimes\b", re.I)),
]


# Cryptocurrency / token subject matter. The strategy trades US-listed equities,
# not crypto, so a question ABOUT a coin/token (price, listing, launch) is out of
# scope. Public crypto-EXPOSED companies are kept: they carry a parenthetical US
# ticker ("Coinbase (COIN) beat earnings", "Bitcoin Depot (BTM)…"), which the
# ticker check below preserves. Deliberately excludes bare "airdrop" — that
# matches humanitarian aid airdrops, not only crypto airdrops.
_CRYPTO_TERMS = re.compile(
    r"\b(bitcoin|btc|ethereum|\beth\b|xrp|ripple|solana|dogecoin|doge|cardano|litecoin|ltc|"
    r"polkadot|chainlink|avalanche|avax|toncoin|shiba|pepe|monero|tron|worldcoin|"
    r"memecoin|meme\s*coin|altcoin|stablecoin|hyperliquid|fartcoin|\bbnb\b|"
    r"crypto|\bdefi\b|\bnft\b|web3|on-?chain|\bcoin\b|\btoken\b)\b", re.I)


# Raw asset price-level targets ("Will X hit $Y", "close above $Y", "(HIGH)/(LOW)"
# markers, price brackets, all-time-high, best/worst-performing rankings). These
# are not information catalysts -- just the market restating a price -- and carry
# no diffusion edge. The fundamental guard prevents collisions: a real earnings/
# revenue/sales question is never a price target, and the guard also saves tickers
# that happen to spell HIGH/LOW (e.g. Lowe's "(LOW)").
_PRICE_TARGET = re.compile(
    r"\((HIGH|LOW)\)|\bclose(s|d)?\s+(above|below|at)\s+\$?[\d,]"
    r"|\bfinish(es)?\s+[\w\s]{0,20}?\b(above|below)\s+\$?[\d,]"
    r"|\bhit(s)?\s+\$?[\d,][\d,.]*|\breach(es|ed)?\s+\$[\d,]"
    r"|\ball[-\s]?time high\b|\b(best|worst)\s+performing\b"
    r"|\$[\d,]+\s*[-–]\s*\$?[\d,]", re.I)
_FUNDAMENTAL_GUARD = re.compile(
    r"\b(earnings|revenue|sales|\beps\b|deliver(ies|y)?|customers|subscribers|bookings"
    r"|margin|\busers\b|trips|nights|comparable|same[-\s](restaurant|store)|beat|guidance|forecast)\b",
    re.I)


def structural_noise_rule(question: str) -> str | None:
    """Return the name of the first structural-noise pattern the question hits,
    or None. See `_STRUCTURAL_NOISE`."""
    q = question or ""
    for name, rx in _STRUCTURAL_NOISE:
        if rx.search(q):
            return name
    # Cryptocurrency/token question with no US-listed equity ticker → out of scope.
    if _CRYPTO_TERMS.search(q) and _extract_ticker(q) is None:
        return "crypto_asset"
    # Raw price-level target (not a fundamental catalyst) → out of scope.
    if _PRICE_TARGET.search(q) and not _FUNDAMENTAL_GUARD.search(q):
        return "stock_price_target"
    return None


def regex_prefilter(m: dict) -> Pass1Result:
    """Score relevance and sentiment for a single market question (no API)."""
    q = m["question"]
    tags = m.get("tags", [])
    q_lower = q.lower()

    # ── Structural noise (question format has no tradable US-equity catalyst) ──
    rule = structural_noise_rule(q)
    if rule is not None:
        return Pass1Result(m["market_id"], 0.0, True,
                           f"Structural noise ({rule}): question format has no tradable US-equity catalyst.")

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
