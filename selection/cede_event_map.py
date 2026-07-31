"""Outcome-blind canonical mapping for CEDE event exposures.

The raw research tables contain many rows per underlying economic event: one
Polymarket question may be attached to several possible assets, and a tariff
episode may be attached to several country ETFs.  Those are alternative
expressions of one causal hypothesis, not independent trades.  This module
turns them into one audited exposure (a stock, commodity basket, or
predeclared macro basket) before any probability or price feature is built.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = PROJECT / "data" / "cede" / "canonical_event_policy.json"

FAMILY_ALIASES = {
    "earnings": "earnings",
    "geo": "geopolitics",
    "geopolitics": "geopolitics",
    "macro": "macro",
}

REQUIRED_COLUMNS = {
    "economic_event_id", "event_family", "market_id", "symbol", "question",
    "t0", "t_e", "decision_ts_utc", "polarity", "mapping_valid",
    "mapping_confidence",
}


def load_policy(path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    """Load the human-reviewed mapping policy rather than hiding choices in code."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "earnings" not in payload or "geopolitics" not in payload or "macro" not in payload:
        raise ValueError(f"Incomplete CEDE canonical-event policy: {path}")
    return payload


def _utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _confidence(value: Any) -> float:
    """Accept legacy 1--5 semantic scores or normalized confidence values."""
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if not np.isfinite(number):
        return np.nan
    return float(number / 5.0 if number > 1.0 else number)


def _event_key(root_event_id: str, decision_ts: pd.Timestamp) -> str:
    return f"{root_event_id}@{_utc(decision_ts).date().isoformat()}"


def _empty_issues() -> pd.DataFrame:
    return pd.DataFrame(columns=["root_event_id", "trade_event_id", "family", "reason", "symbols", "market_ids"])


def _issue(group: pd.DataFrame, family: str, reason: str) -> dict[str, str]:
    decision = _utc(group["decision_ts_utc"].iloc[0])
    return {
        "root_event_id": str(group["economic_event_id"].iloc[0]),
        "trade_event_id": _event_key(str(group["economic_event_id"].iloc[0]), decision),
        "family": family,
        "reason": reason,
        "symbols": "|".join(sorted(group["symbol"].astype(str).str.upper().unique())),
        "market_ids": "|".join(sorted(group["market_id"].astype(str).unique())),
    }


def _family(group: pd.DataFrame) -> str | None:
    families = group["event_family"].astype(str).str.lower().map(FAMILY_ALIASES).dropna().unique()
    return str(families[0]) if len(families) == 1 else None


def _common_direction(group: pd.DataFrame) -> int | None:
    values = pd.to_numeric(group["polarity"], errors="coerce").dropna().astype(int).unique()
    return int(values[0]) if len(values) == 1 and int(values[0]) in {-1, 1} else None


def _map_components(group: pd.DataFrame, family: str, policy: dict[str, Any]) -> tuple[list[str] | None, str | None, str | None]:
    """Return components, hedge and a rejection reason, without outcome inputs."""
    symbols = group["symbol"].astype(str).str.upper()
    if family == "earnings":
        allowed = set(policy["earnings"]["eligible_mapping_types"])
        eligible = group[
            group["mapping_valid"].astype(bool)
            & group.get("mapping_type", pd.Series("", index=group.index)).astype(str).isin(allowed)
        ]
        components = sorted(eligible["symbol"].astype(str).str.upper().unique())
        if len(components) != 1:
            return None, None, "earnings_requires_exactly_one_direct_issuer"
        sector = str(eligible.get("sector_etf", eligible.get("hedge", pd.Series("SPY", index=eligible.index))).iloc[0]).upper()
        if not sector or sector == "NAN":
            sector = "SPY"
        return components, sector, None

    if family == "geopolitics":
        allowed = set(policy["geopolitics"]["eligible_symbols"])
        eligible = group[group["mapping_valid"].astype(bool) & symbols.isin(allowed)]
        components = sorted(eligible["symbol"].astype(str).str.upper().unique())
        if not components:
            return None, None, "geo_requires_direct_commodity_etp"
        return components, str(policy["geopolitics"]["hedge"]), None

    if family == "macro":
        root = str(group["economic_event_id"].iloc[0])
        expected = [str(value).upper() for value in policy["macro"]["event_components"].get(root, [])]
        actual = sorted(symbols.unique())
        if not expected:
            return None, None, "macro_event_not_predeclared"
        if sorted(expected) != actual:
            return None, None, "macro_components_do_not_match_predeclared_policy"
        return expected, str(policy["macro"]["hedge"]), None

    return None, None, "unsupported_family"


def build_canonical_event_map(
    candidates: pd.DataFrame,
    policy: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Make one canonical event/basket and one probability leg per market.

    ``trade_event_id`` is the root economic-event ID plus its decision session.
    It prevents a later, separate market question from being treated as the
    exact same order, while portfolio allocation can still enforce one open
    position per ``root_event_id``.
    """
    missing = REQUIRED_COLUMNS.difference(candidates.columns)
    if missing:
        raise ValueError(f"Canonical CEDE map missing columns: {sorted(missing)}")
    policy = policy or load_policy()
    frame = candidates.copy()
    frame["event_family"] = frame["event_family"].astype(str).str.lower().map(FAMILY_ALIASES)
    frame = frame[frame["event_family"].notna()].copy()
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    frame["market_id"] = frame["market_id"].astype(str)
    frame["decision_ts_utc"] = frame["decision_ts_utc"].map(_utc)
    frame["t0"] = pd.to_datetime(frame["t0"], errors="coerce", utc=True)
    frame["t_e"] = pd.to_datetime(frame["t_e"], errors="coerce", utc=True)
    frame["mapping_confidence"] = frame["mapping_confidence"].map(_confidence)
    frame["trade_event_id"] = [
        _event_key(str(root), decision)
        for root, decision in zip(frame["economic_event_id"], frame["decision_ts_utc"], strict=True)
    ]

    map_rows: list[dict[str, Any]] = []
    leg_rows: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    for trade_event_id, group in frame.groupby("trade_event_id", sort=True):
        family = _family(group)
        if family is None:
            issues.append(_issue(group, "unknown", "mixed_or_unknown_family"))
            continue
        direction = _common_direction(group)
        if direction is None:
            issues.append(_issue(group, family, "mixed_or_unresolved_probability_polarity"))
            continue
        components, hedge, reason = _map_components(group, family, policy)
        if reason:
            issues.append(_issue(group, family, reason))
            continue
        minimum = float(policy[family]["minimum_mapping_confidence"])
        confidence = float(group["mapping_confidence"].min())
        if not np.isfinite(confidence) or confidence < minimum:
            issues.append(_issue(group, family, "mapping_confidence_below_policy_minimum"))
            continue
        root_event_id = str(group["economic_event_id"].iloc[0])
        asset = components[0] if len(components) == 1 else f"BASKET:{trade_event_id}"
        record = {
            "trade_event_id": trade_event_id,
            "root_event_id": root_event_id,
            "family": family,
            "asset": asset,
            "hedge": hedge,
            "components_json": json.dumps(components),
            "component_count": len(components),
            "expected_direction": direction,
            "mapping_confidence": confidence,
            "decision_ts_utc": group["decision_ts_utc"].iloc[0],
            "event_start_utc": group["t0"].min(),
            "event_end_utc": group["t_e"].min(),
            "market_count": group["market_id"].nunique(),
            "mapping_policy_version": policy.get("policy_version", "unknown"),
        }
        map_rows.append(record)
        # One market is one vote regardless of how many proxy rows were in the
        # source table.  The basket is only the execution exposure.
        for market_id, market_group in group.groupby("market_id", sort=True):
            representative = market_group.sort_values(["t0", "symbol"], kind="mergesort").iloc[0]
            leg_rows.append({
                **record,
                "market_id": str(market_id),
                "question": str(representative["question"]),
                "t0": representative["t0"],
                "market_event_end_utc": market_group["t_e"].min(),
                "weight": 1.0,
            })

    map_columns = [
        "trade_event_id", "root_event_id", "family", "asset", "hedge", "components_json",
        "component_count", "expected_direction", "mapping_confidence", "decision_ts_utc",
        "event_start_utc", "event_end_utc", "market_count", "mapping_policy_version",
    ]
    leg_columns = [*map_columns, "market_id", "question", "t0", "market_event_end_utc", "weight"]
    legs = pd.DataFrame(leg_rows, columns=leg_columns)
    event_map = pd.DataFrame(map_rows, columns=map_columns)
    issues_frame = pd.DataFrame(issues) if issues else _empty_issues()
    return event_map.sort_values("decision_ts_utc").reset_index(drop=True), legs.sort_values(
        ["decision_ts_utc", "trade_event_id", "market_id"]
    ).reset_index(drop=True), issues_frame.sort_values(["family", "trade_event_id"]).reset_index(drop=True)
