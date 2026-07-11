from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


@dataclass(frozen=True)
class SourceMarket:
    market_id: str
    event_id: str
    event_title: str
    question: str
    created_at: datetime
    end_at: datetime
    tags: list[str]
    raw_market: dict[str, Any]
    yes_token_id: str
    condition_id: str | None
    final_outcome: str | None


@dataclass(frozen=True)
class Asset:
    symbol: str
    asset_name: str
    asset_class: str
    reason: str
    connection_strength: float | None = None


@dataclass(frozen=True)
class IBTradableAsset:
    symbol: str
    asset_name: str
    asset_class: Literal["stock", "etf"]
    primary_exchange: str
    stock_type: str
    industry: str | None = None
    category: str | None = None
    subcategory: str | None = None


class AssetCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    symbol: str = Field(min_length=1, max_length=20)
    asset_name: str = Field(min_length=1, max_length=260)
    asset_class: Literal["stock", "etf"]
    relationship_type: Literal[
        "direct_company",
        "customer",
        "supplier",
        "distributor",
        "partner",
        "competitor",
        "substitute",
        "complement",
        "creditor",
        "investor",
        "landlord_tenant",
        "sector_etf",
        "country_etf",
        "commodity_proxy",
        "other_specific",
    ]
    reason: str = Field(
        min_length=20,
        max_length=500,
        description=(
            "Specific causal economic relationship to the exact question. Generic claims "
            "that an asset may be affected are insufficient."
        ),
    )
    connection_strength: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Mechanical exposure strength in [0, 1]; legacy one-pass worlds default to 1.0.",
    )

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, value: str) -> str:
        return value.upper()


class AssetWorld(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    universe_name: str = Field(min_length=1, max_length=200)
    universe_reason: str = Field(min_length=20, max_length=700)
    assets: list[AssetCandidate] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_unique_symbols(self) -> AssetWorld:
        symbols = [
            asset.symbol.strip().upper().replace(".", " ").replace("$", " ")
            for asset in self.assets
        ]
        if len(symbols) != len(set(symbols)):
            raise ValueError("Asset world contains duplicate symbols")
        return self


class BatchedAssetWorld(AssetWorld):
    request_id: str
    # Pass-1 score for the market question itself. Multiplied by each asset's connection_strength
    # to form the final relevance. Defaults to 1.0 for legacy one-pass worlds (unscored).
    question_relevance: float = 1.0


class CompactAssetWorld(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str
    symbols: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        return [value.upper() for value in values]


class CompactAssetWorlds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worlds: list[CompactAssetWorld]


# Below this question-relevance score the YES outcome has no real mechanical channel to US
# equities; we skip the (expensive) stock-mapping pass and emit an empty world.
QUESTION_RELEVANCE_FLOOR = 0.60


class RelevanceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str
    question_relevance: float = Field(
        ge=0.0,
        le=1.0,
        description="How mechanically a YES outcome reprices US-listed equities, in [0, 1].",
    )
    positive_sentiment: bool = Field(
        description=(
            "True if the event described is a positive/favorable development (growth, "
            "easing, approval, a beat, a success); false if it describes a negative/adverse "
            "one (a crisis, failure, decline, rejection, a miss, a crash). Judge the tone of "
            "the event itself -- not whether YES or NO is more likely, and not which "
            "direction any asset's price would move."
        ),
    )
    reason: str = Field(min_length=20, max_length=500)


class RelevanceGateBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decisions: list[RelevanceDecision]


class CatalystDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: int = Field(description="The question number from the input (1-indexed)")
    verdict: str = Field(description="CATALYST or NOISE")
    positive_sentiment: bool = Field(
        description="True if the event is a positive/favorable development, false if negative/adverse.",
    )
    reason: str = Field(max_length=200)


class CatalystBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    classifications: list[CatalystDecision]


GEMINI_CATALYST_PROMPT = """
For each numbered prediction-market question, classify as CATALYST or NOISE.

CATALYST: A discrete event that would be a genuine surprise and mechanically reprice a
US-listed stock or ETF. Examples: earnings beat/miss, FDA approval/rejection, Fed rate
decision, major M&A, IPO, trade policy change, sanctions, geopolitical escalation,
major product launch, significant legal ruling, vehicle delivery report, major contract win.

NOISE: An arbitrary price-level target, a routine threshold, or a question with no clear
surprise catalyst behind it. Examples: "Will AAPL close above $232.50?", "Will Bitcoin
reach $100k?", "Will S&P 500 close above 6000?", word-count bets, celebrity questions,
app rankings, podcast mentions, net worth questions.

KEY RULE: Stock price targets ("Will X close above/below $Y?") are NOISE unless the price
level implies an extraordinary move (e.g. doubling or halving) that could only happen from
a fundamental catalyst. A $5 or $10 move in a $200 stock is NOT extraordinary.

Also judge positive_sentiment: does the event ITSELF describe a positive/favorable
development (growth, easing, approval, a beat, a success) or a negative/adverse one
(a crisis, failure, decline, rejection, a miss, a crash)? Use financial judgment --
"the Fed cuts rates" is positive (accommodative), "a company misses earnings" is negative.

Each question includes its creation date in brackets. Evaluate using ONLY knowledge
available AS OF that date -- do NOT use hindsight or any information about what happened
after the question was created.

Return your classifications as a JSON array matching the schema provided.
""".strip()


class TightAssetWorld(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str
    universe_name: str = Field(min_length=1, max_length=200)
    universe_reason: str = Field(min_length=20, max_length=700)
    assets: list[AssetCandidate] = Field(default_factory=list, max_length=20)


class TightAssetWorlds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worlds: list[TightAssetWorld]


GEMINI_RELEVANCE_GATE_PROMPT = """
For each prediction-market question, rate question_relevance in [0, 1]: how mechanically a YES
outcome would move US-listed equities or ETFs. Judge the QUESTION itself, not any single stock.

CRITICAL: Each question includes a market_created_at date. You MUST evaluate the question using
ONLY knowledge available AS OF that date. Do NOT use hindsight or any information about what
happened after the question was created. Pretend you are reading this question on its creation day.

Weigh four things:
1. Directness -- does YES hit a US company's cash flow/value directly (a US name's earnings,
   FDA, M&A), a US-traded commodity or rate (oil, Treasuries), or only a foreign/indirect channel?
2. Breadth x magnitude -- does it move one stock, a sector, or the whole US market?
3. Surprise -- is YES a genuine market-moving surprise, or a low bar already priced in?
4. US proximity -- US government / Federal Reserve action > a major US ally's action > a distant
   regional actor with no US transmission. Use your world knowledge of whether this TYPE of event
   has historically repriced US stocks.

Calibration anchors (generalize the principle from the magnitude of impact; do NOT pattern-match
the wording, and do not pre-decide which ticker is involved):
  ~1.0  a US company's own earnings / FDA / M&A; a Federal Reserve rate decision or emergency action
  ~0.8  direct US action that moves a commodity or a named sector; a major global commodity-supply shock
  ~0.5  an ally/regional event that reaches US markets only through a commodity, with no US actor
  ~0.3  a routine macro print near consensus; a modest, expected policy move
  ~0.2  foreign/regional events US markets routinely shrug off
  ~0.0  no mechanical US-equity channel at all: speech-word counts, celebrity, sports, pure narrative

Also judge positive_sentiment: does the event ITSELF describe a positive/favorable development
(growth, easing, approval, a beat, a success) rather than a negative/adverse one (a crisis,
failure, decline, rejection, a miss, a crash)? Use real-world financial judgment, not the literal
words -- e.g. "the Fed cuts rates" is a positive, accommodative development even though "cuts"
sounds negative in everyday language, while "a company misses earnings" or "a stock crashes" is
negative. This is a judgment about the tone of the event, not a prediction of whether YES or NO
will happen, and not a prediction of which direction any asset would move.

Do not choose assets in this pass. Do not predict the outcome or trading direction. Return only
request_id, question_relevance, positive_sentiment, and a one-line reason that names the channel
(or its absence).
""".strip()


GEMINI_TIGHT_MAPPING_PROMPT = """
For each relevant request, REASON from the event to the assets in three steps, then return the
world. Do not jump straight to a ticker or rely on which name is famous -- reason about cause and
effect, so this works on events you have never seen.

1. CHANNEL: What does a YES outcome mechanically change? Name the single most direct transmission
   channel -- one company's cash flow, a specific commodity's price, interest-rate expectations,
   a regulatory/tariff cost, a currency, etc.
2. INSTRUMENTS: Which liquid US-listed equities/ETFs are MOST DIRECTLY driven by THAT channel?
   Prefer the most direct instrument over things that merely correlate with it. If the channel is a
   commodity's price, decide whether the outcome reprices the commodity itself or the equities that
   produce it -- the producers also carry broad-market and company risk and can move the other way,
   so pick what the channel actually drives. If the channel is interest rates, think about what
   rates mechanically reprice (long-duration instruments, heavy borrowers, rate-sensitive sectors).
   List only what the channel moves -- never a name merely because it is topically related.
3. STRENGTH: connection_strength in [0,1] = how directly the channel drives that asset. 1.0 = the
   outcome is the asset's defining driver; lower = more steps removed; exclude anything tied only by
   narrative or risk-off sentiment.

Hard constraints:
- Earnings / merger / named-company: ONLY the named company, and ONLY if the outcome is material to
  its value -- weigh a single product or segment against the company's total size; where it IS the
  company, strength is high; where it is immaterial to a diversified large-cap, strength is low or
  return empty. Never add peers, competitors, suppliers, or customers.
- Keep worlds small (prefer 1-4 names). If nothing is directly exposed, return an empty assets list.

This is asset selection only. Do not predict Yes/No, direction, sizing, or expected return. Return
request_id, universe_name, universe_reason (state the channel you identified), and assets per the schema.
""".strip()


# Backward-compatible import name used by older stage metadata. The implementation below
# is now two-pass: relevance gate, then tight mapping.
GEMINI_ONE_CALL_PROMPT = GEMINI_TIGHT_MAPPING_PROMPT


CATALOG_TOKEN_RE = re.compile(r"[A-Z0-9]+")
CATALOG_STOP_WORDS = {
    "A",
    "AN",
    "AND",
    "ARE",
    "CLASS",
    "CO",
    "COMMON",
    "COMPANY",
    "CORP",
    "CORPORATION",
    "ETF",
    "FUND",
    "HOLDINGS",
    "INC",
    "INCORPORATED",
    "LTD",
    "OF",
    "ORDINARY",
    "SHARE",
    "SHARES",
    "STOCK",
    "THE",
}


class IBAssetCatalogIndex:
    def __init__(self, assets: list[IBTradableAsset]) -> None:
        self.assets = tuple(assets)
        self.by_symbol = {ib_symbol_key(asset.symbol): asset for asset in assets}
        self.name_text: dict[str, str] = {}
        self.name_tokens: dict[str, set[str]] = {}
        self.metadata_tokens: dict[str, set[str]] = {}
        self.name_token_symbols: dict[str, set[str]] = {}
        self.metadata_token_symbols: dict[str, set[str]] = {}
        for asset in assets:
            key = ib_symbol_key(asset.symbol)
            self.name_text[key] = normalized_catalog_text(asset.asset_name)
            name_tokens = catalog_tokens(asset.symbol, asset.asset_name)
            metadata_tokens = catalog_tokens(
                asset.industry,
                asset.category,
                asset.subcategory,
            )
            self.name_tokens[key] = name_tokens
            self.metadata_tokens[key] = metadata_tokens
            for token in name_tokens:
                self.name_token_symbols.setdefault(token, set()).add(key)
            for token in metadata_tokens:
                self.metadata_token_symbols.setdefault(token, set()).add(key)


def ib_asset_catalog_index(
    assets: list[IBTradableAsset] | IBAssetCatalogIndex,
) -> IBAssetCatalogIndex:
    if isinstance(assets, IBAssetCatalogIndex):
        return assets
    return IBAssetCatalogIndex(assets)


def gemini_world_payload(
    requests: list[tuple[str, SourceMarket, datetime]],
) -> dict[str, list[dict[str, object]]]:
    return {
        "requests": [
            {
                "request_id": request_id,
                "event_title": market.event_title,
                "market_question": market.question,
                "tags": market.tags,
                "market_created_at": market.created_at,
                "market_end_at": market.end_at,
                "historical_as_of": as_of,
            }
            for request_id, market, as_of in requests
        ]
    }


def canonicalize_compact_gemini_world(
    compact_world: CompactAssetWorld,
    market: SourceMarket,
    catalog: IBAssetCatalogIndex,
) -> BatchedAssetWorld:
    selected: list[tuple[IBTradableAsset, str]] = []
    seen: set[str] = set()
    for symbol in compact_world.symbols:
        key = ib_symbol_key(symbol)
        asset = catalog.by_symbol.get(key)
        if asset is None or key in seen:
            continue
        seen.add(key)
        selected.append((asset, "gemini"))
    if len(selected) < 4:
        # No fallback. Gemini must return at least four IB-tradable symbols; if it
        # does not, crash loudly instead of padding with hardcoded local symbols.
        raise ValueError(
            "Gemini returned fewer than four IB-tradable symbols for request "
            f"{compact_world.request_id}: {list(compact_world.symbols)}"
        )
    assets = [
        AssetCandidate(
            symbol=asset.symbol,
            asset_name=asset.asset_name,
            asset_class=asset.asset_class,
            relationship_type="sector_etf" if asset.asset_class == "etf" else "other_specific",
            reason=(
                f"Gemini 3.5 Flash selected {asset.asset_name} as economically related "
                "to the supplied prediction-market question."
            ),
        )
        for asset, _source in selected[:20]
    ]
    universe_name = (market.event_title or market.question or "Gemini asset world")[:200]
    return BatchedAssetWorld(
        request_id=compact_world.request_id,
        universe_name=universe_name,
        universe_reason=(
            "Gemini 3.5 Flash selected this locally IB-verified cross-sectional "
            "research world."
        ),
        assets=assets,
    )


def _single_named_entity_market(market: SourceMarket) -> bool:
    text = " ".join(
        [
            market.event_title or "",
            market.question or "",
            " ".join(market.tags or []),
        ]
    ).lower()
    needles = (
        "earnings",
        "eps",
        "revenue",
        "fda",
        "pdufa",
        "drug approval",
        "approval",
        "merger",
        "acquisition",
        "takeover",
    )
    return any(needle in text for needle in needles)


def _reason(value: str | None, fallback: str) -> str:
    text = (value or "").strip()
    if len(text) >= 20:
        return text[:700]
    return fallback


def empty_world(
    request_id: str,
    market: SourceMarket,
    reason: str | None = None,
    *,
    question_relevance: float = 0.0,
) -> BatchedAssetWorld:
    return BatchedAssetWorld(
        request_id=request_id,
        universe_name=(market.event_title or market.question or "No liquid equity world")[:200],
        universe_reason=_reason(
            reason,
            "Relevance gate scored this question below the floor for mechanical US-equity repricing.",
        ),
        assets=[],
        question_relevance=question_relevance,
    )


def canonicalize_tight_gemini_world(
    tight_world: TightAssetWorld,
    market: SourceMarket,
    catalog: IBAssetCatalogIndex,
    *,
    question_relevance: float = 1.0,
) -> BatchedAssetWorld:
    selected: list[AssetCandidate] = []
    seen: set[str] = set()
    for item in tight_world.assets:
        key = ib_symbol_key(item.symbol)
        asset = catalog.by_symbol.get(key)
        if asset is None or key in seen:
            continue
        seen.add(key)
        selected.append(
            AssetCandidate(
                symbol=asset.symbol,
                asset_name=asset.asset_name,
                asset_class=asset.asset_class,
                relationship_type=item.relationship_type,
                reason=item.reason,
                connection_strength=item.connection_strength,
            )
        )
    if _single_named_entity_market(market) and len(selected) > 1:
        selected = selected[:1]
    if tight_world.assets and not selected:
        # The LLM named only assets we cannot trade on IB (e.g. a small-cap biotech not in the
        # catalog). Emit an empty world for this market rather than crashing the whole run --
        # we simply have no tradeable exposure here. No hardcoded fallback symbols.
        print(
            f"[world] no IB-tradable symbol for {tight_world.request_id}: "
            f"{[asset.symbol for asset in tight_world.assets]} -> empty world"
        )
        return empty_world(
            tight_world.request_id,
            market,
            f"Mapped names are not IB-tradable: {[asset.symbol for asset in tight_world.assets]}",
            question_relevance=question_relevance,
        )
    return BatchedAssetWorld(
        request_id=tight_world.request_id,
        universe_name=(tight_world.universe_name or market.event_title or market.question)[:200],
        universe_reason=_reason(
            tight_world.universe_reason,
            "Tight mapping selected assets with concrete mechanical exposure to the prediction-market YES outcome.",
        ),
        assets=selected,
        question_relevance=question_relevance,
    )


async def build_gemini_asset_worlds(
    gemini: object,
    requests: list[tuple[str, SourceMarket, datetime]],
    *,
    tradable_assets: list[IBTradableAsset] | IBAssetCatalogIndex,
) -> list[BatchedAssetWorld]:
    if not requests:
        return []
    gate_response = await gemini.structured(  # type: ignore[attr-defined]
        system_prompt=GEMINI_RELEVANCE_GATE_PROMPT,
        payload=gemini_world_payload(requests),
        response_model=RelevanceGateBatch,
        max_tokens=max(800, len(requests) * 80),
        prefer_prompt_schema=True,
    )
    catalog = ib_asset_catalog_index(tradable_assets)
    expected = {request_id for request_id, _, _ in requests}
    decisions = {
        decision.request_id: decision
        for decision in gate_response.decisions
        if decision.request_id in expected
    }
    missing_gate = expected - set(decisions)
    if missing_gate:
        raise ValueError(f"Gemini relevance gate omitted requests: {sorted(missing_gate)}")

    worlds: dict[str, BatchedAssetWorld] = {}
    relevant_requests: list[tuple[str, SourceMarket, datetime]] = []
    relevance_by_request: dict[str, float] = {}
    for request_id, market, _ in requests:
        decision = decisions[request_id]
        relevance_by_request[request_id] = decision.question_relevance
        if decision.question_relevance >= QUESTION_RELEVANCE_FLOOR and decision.positive_sentiment:
            relevant_requests.append((request_id, market, _))
        else:
            reason = decision.reason
            if decision.question_relevance >= QUESTION_RELEVANCE_FLOOR and not decision.positive_sentiment:
                # This is a long-only strategy: skip events whose tone is negative/adverse,
                # even when they are mechanically relevant to US equities.
                reason = f"Negative-sentiment event, skipped for long-only strategy: {decision.reason}"
            worlds[request_id] = empty_world(
                request_id,
                market,
                reason,
                question_relevance=decision.question_relevance,
            )

    if relevant_requests:
        mapping_response = await gemini.structured(  # type: ignore[attr-defined]
            system_prompt=GEMINI_TIGHT_MAPPING_PROMPT,
            payload=gemini_world_payload(relevant_requests),
            response_model=TightAssetWorlds,
            max_tokens=max(1200, len(relevant_requests) * 220),
            prefer_prompt_schema=True,
        )
        expected_mapping = {request_id for request_id, _, _ in relevant_requests}
        mapped_by_request = {
            world.request_id: world
            for world in mapping_response.worlds
            if world.request_id in expected_mapping
        }
        missing_mapping = expected_mapping - set(mapped_by_request)
        if missing_mapping:
            raise ValueError(f"Gemini tight mapping omitted requests: {sorted(missing_mapping)}")
        for request_id, market, _ in relevant_requests:
            worlds[request_id] = canonicalize_tight_gemini_world(
                mapped_by_request[request_id],
                market,
                catalog,
                question_relevance=relevance_by_request[request_id],
            )
    return [worlds[request_id] for request_id, _, _ in requests]


def ib_symbol_key(symbol: str) -> str:
    return symbol.strip().upper().replace(".", " ").replace("$", " ")


def normalized_catalog_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(CATALOG_TOKEN_RE.findall(value.upper()))


def catalog_tokens(*values: str | None) -> set[str]:
    return {
        token
        for value in values
        for token in CATALOG_TOKEN_RE.findall((value or "").upper())
        if len(token) > 1 and token not in CATALOG_STOP_WORDS
    }


def assets_from_world(world: AssetWorld) -> list[Asset]:
    # Persist the FINAL relevance = question_relevance (pass 1) x connection_strength (pass 2),
    # per Liran's design. Raw question_relevance is also kept in the world's llm_output JSON.
    question_relevance = getattr(world, "question_relevance", 1.0)
    return [
        Asset(
            symbol=item.symbol,
            asset_name=item.asset_name,
            asset_class=item.asset_class,
            reason=f"[{item.relationship_type}] {item.reason}",
            connection_strength=(
                item.connection_strength * question_relevance
                if item.connection_strength is not None
                else question_relevance
            ),
        )
        for item in world.assets
    ]


