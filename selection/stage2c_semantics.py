"""Timestamp-independent mapping semantics for Stage 2C.

The functions in this module deliberately accept only semantic inputs:
question text, asset identity, company/archetype identity, event family, and
sector.  Returns, prices, selected outcomes, and portfolio results are not
inputs to the labeler.

``feat_connection_strength`` is retained as a backward-compatible source
field elsewhere in the pipeline.  Stage 2C exposes the same unmodified value
as ``legacy_gemini_relevance_score`` and does not treat it as a universally
calibrated measure of economic connection.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


MAPPING_TYPE_PRIORITY = {
    "direct_issuer": 0,
    "direct_underlying": 1,
    "first_order_sector": 2,
    "second_order_company": 3,
    "broad_macro_proxy": 4,
    "unclear_or_invalid": 5,
}


RUBRICS = {
    "mapping_confidence": {
        1: "economic path is absent or cannot be defended",
        2: "plausible but materially ambiguous or weakly documented path",
        3: "defensible indirect path with meaningful basis risk",
        4: "clear economic relationship with limited mapping ambiguity",
        5: "direct issuer/underlying relationship established by construction",
    },
    "impact_materiality": {
        1: "unlikely to matter to the asset's economic value",
        2: "small or highly diluted exposure",
        3: "meaningful but not dominant exposure",
        4: "major earnings, cash-flow, or valuation catalyst",
        5: "the event directly defines the tracked economic variable or issuer outcome",
    },
    "direction_confidence": {
        1: "direction cannot be defended",
        2: "several competing channels make the sign fragile",
        3: "expected sign is plausible but basis risk is material",
        4: "strong expected sign with ordinary market-expectation uncertainty",
        5: "mechanically directional exposure",
    },
    "exposure_purity": {
        1: "broad/noisy proxy dominated by unrelated exposures",
        2: "individual company with a material second-order channel",
        3: "sector basket with direct industry exposure",
        4: "commodity or thematic vehicle closely tracking the affected variable",
        5: "same issuer or uniquely direct underlying exposure",
    },
}


FDA_COMPANY_TOKENS = {
    "ASND": ("ascendis", "transcon"),
    "BIIB": ("biogen", "leqembi"),
    "INSM": ("insmed", "brensocatib"),
    "IONS": ("ionis", "donidalorsen"),
    "LENZ": ("lenz therapeutics", "lnz100"),
    "MRK": ("merck", "clesrovimab", "mk-1654", "mk‑1654"),
    "PGEN": ("precigen", "prgn-2012"),
    "PTCT": ("ptc therapeutics", "sepiapterin"),
    "REGN": ("regeneron", "odronextamab", "ordspono", "eylea"),
    "SNY": ("sanofi", "rilzabrutinib"),
    "TLX": ("telix", "tlx250"),
    "TNXP": ("tonix", "tnx-102"),
    "TVTX": ("travere", "filspari"),
    "UNCY": ("unicycive", "oxylanthanum", "olc"),
}


DIRECT_UNDERLYING = {
    "USO": "United States Oil Fund crude-oil exposure",
    "BNO": "Brent crude-oil exposure",
    "UNG": "United States Natural Gas Fund exposure",
    "WEAT": "wheat-futures exposure",
}

FIRST_ORDER_SECTOR = {
    "XLE": "energy-sector equity exposure",
}

SECOND_ORDER_COMPANY = {
    "CVX": "Chevron oil-and-gas cash-flow exposure",
    "XOM": "Exxon Mobil oil-and-gas cash-flow exposure",
}


@dataclass(frozen=True)
class SemanticLabel:
    mapping_type: str
    mapping_valid: bool
    semantic_directness: int
    mapping_confidence: int
    impact_materiality: int
    direction_confidence: int
    exposure_purity: int
    event_description: str
    transmission_channel: str
    asset_exposure: str
    expected_direction: str
    economic_path_explanation: str
    semantic_rule: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _expected_direction(question: str, asset: str) -> str:
    text = question.casefold()
    if "earnings" in text or "quarterly eps" in text:
        return "YES / earnings beat -> positive issuer surprise, subject to expectations"
    if "fda" in text or "approve" in text or "approval" in text:
        return "YES / approval -> positive issuer value, subject to expectations and product materiality"
    deescalation = any(token in text for token in ("ceasefire", "conflict ends", "ends before", "visit the white house"))
    if asset in {"USO", "BNO", "UNG", "WEAT", "XLE", "CVX", "XOM"}:
        if deescalation:
            return "YES / de-escalation -> lower supply-risk premium -> negative exposure return"
        return "YES / escalation or disruption -> higher supply-risk premium -> positive exposure return"
    return "direction unresolved"


def _label(
    mapping_type: str,
    valid: bool,
    directness: int,
    mapping_confidence: int,
    materiality: int,
    direction_confidence: int,
    purity: int,
    question: str,
    channel: str,
    exposure: str,
    asset: str,
    rule: str,
) -> SemanticLabel:
    direction = _expected_direction(question, asset)
    path = f"{question} -> {channel} -> {exposure} -> {direction}"
    return SemanticLabel(
        mapping_type=mapping_type,
        mapping_valid=valid,
        semantic_directness=directness,
        mapping_confidence=mapping_confidence,
        impact_materiality=materiality,
        direction_confidence=direction_confidence,
        exposure_purity=purity,
        event_description=question,
        transmission_channel=channel,
        asset_exposure=exposure,
        expected_direction=direction,
        economic_path_explanation=path,
        semantic_rule=rule,
    )


def label_mapping(
    *,
    question: Any,
    symbol: Any,
    event_family: Any = "",
    company_identity: Any = "",
    sector: Any = "",
) -> SemanticLabel:
    """Return a deterministic semantic label without looking at outcomes.

    The observed development taxonomy is intentionally small.  Unrecognized
    mappings are sent to ``unclear_or_invalid`` rather than guessed.
    """

    q = _clean(question)
    q_lower = q.casefold()
    asset = _clean(symbol).upper()
    family = _clean(event_family).casefold()
    company = _clean(company_identity).casefold()
    sector_text = _clean(sector)

    ticker_pattern = re.compile(rf"\({re.escape(asset)}\)", flags=re.IGNORECASE) if asset else None
    ordinary_same_company_earnings = (
        family == "earnings"
        and ("earnings" in q_lower or "quarterly eps" in q_lower)
        and ticker_pattern is not None
        and ticker_pattern.search(q) is not None
    )
    if ordinary_same_company_earnings:
        return _label(
            "direct_issuer", True, 5, 5, 4, 4, 5, q,
            "reported earnings directly change the same issuer's expected cash flows and valuation",
            f"{asset} common equity",
            asset,
            "ordinary_same_company_earnings",
        )

    fda_tokens = FDA_COMPANY_TOKENS.get(asset, ())
    fda_context = any(token in q_lower or token in company for token in ("fda", "approval", "approve", "regulatory"))
    owns_named_product = any(token.casefold() in q_lower or token.casefold() in company for token in fda_tokens)
    if fda_context and owns_named_product:
        return _label(
            "direct_issuer", True, 5, 5, 4, 4, 5, q,
            "the mapped issuer owns or economically participates in the named regulatory asset",
            f"{asset} common equity",
            asset,
            "verified_observed_fda_issuer_relationship",
        )

    if asset in DIRECT_UNDERLYING and family in {"geo", "geopolitical", "macro", "other"}:
        channel = "geopolitical escalation/de-escalation changes expected commodity supply and risk premium"
        return _label(
            "direct_underlying", True, 4, 4, 4 if asset in {"USO", "BNO"} else 3, 3, 4,
            q, channel, DIRECT_UNDERLYING[asset], asset, "recognized_direct_commodity_underlying",
        )

    if asset in FIRST_ORDER_SECTOR and family in {"geo", "geopolitical", "macro", "other"}:
        return _label(
            "first_order_sector", True, 3, 4, 3, 3, 3, q,
            "commodity supply-risk changes sector revenue and margins",
            FIRST_ORDER_SECTOR[asset], asset, "recognized_first_order_sector_proxy",
        )

    if asset in SECOND_ORDER_COMPANY and family in {"geo", "geopolitical", "macro", "other"}:
        return _label(
            "second_order_company", True, 2, 3, 2, 2, 2, q,
            "commodity price and regional-risk changes flow through company revenue, costs, and risk premium",
            SECOND_ORDER_COMPANY[asset], asset, "recognized_second_order_energy_company",
        )

    broad_tokens = ("SPY", "QQQ", "DIA", "IWM", "ACWI", "EEM", "TLT", "UUP")
    if asset in broad_tokens and family in {"geo", "geopolitical", "macro", "other"}:
        return _label(
            "broad_macro_proxy", True, 1, 2, 2, 2, 1, q,
            "the event changes broad risk appetite, rates, growth, or inflation expectations",
            f"{asset} broad macro/market exposure", asset, "recognized_broad_macro_proxy",
        )

    return _label(
        "unclear_or_invalid", False, 1, 1, 1, 1, 1, q,
        "no defensible timestamp-independent economic transmission channel was established",
        f"{asset or 'unknown asset'} exposure", asset, "unrecognized_or_ambiguous_mapping",
    )


def backward_compatibility_aliases() -> dict[str, str]:
    return {
        "legacy_gemini_relevance_score": "canonical Stage 2C diagnostic name; original value is unchanged",
        "feat_connection_strength": "backward-compatible engine/input alias",
        "connection_strength": "backward-compatible decision-table alias",
        "relevance": "backward-compatible simulator/source alias",
    }
