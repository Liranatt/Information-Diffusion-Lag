"""Family-aware Causal Event–Dislocation Engine (CEDE).

This is a state-machine policy, not a CEM optimiser.  It converts already
timestamped and semantically mapped Polymarket event legs into *one* tradable
candidate per economic event, ranks candidates by probability-price
dislocation, and supplies conservative entry/exit decisions.

It intentionally does not infer causal mappings from an LLM at trade time.
Callers must provide an audited event-to-asset map and a liquid hedge.  That is
how it avoids counting seven paraphrased Iran or tariff questions as seven
independent opportunities.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


FAMILIES = frozenset({"earnings", "geopolitics", "macro"})


@dataclass(frozen=True)
class CEDEConfig:
    """Fixed operational risk constraints; scoring cutoffs arrive as OOF data."""

    min_probability: float = 0.60
    min_agreement: float = 0.75
    min_mapping_confidence: float = 0.80
    min_days_to_end: int = 2
    max_days_to_end: int = 20
    gross_event_cap: float = 0.35
    family_caps: tuple[tuple[str, float], ...] = (
        ("earnings", 0.10),
        ("geopolitics", 0.15),
        ("macro", 0.10),
    )
    event_cap: float = 0.08
    proxy_cap: float = 0.10
    daily_risk_budget_pct: float = 0.004
    catastrophe_atr_multiple: float = 2.5
    invalidation_probability: float = 0.55
    reversal_mad: float = 1.0

    @property
    def family_cap_map(self) -> dict[str, float]:
        return dict(self.family_caps)


REQUIRED_LEG_COLUMNS = frozenset({
    "economic_event_id", "family", "asset", "hedge", "expected_direction",
    "aligned_probability", "delta_logit", "weight", "mapping_confidence",
    "business_days_to_event_end", "asset_return_1d", "asset_return_2d",
    "hedge_return_1d", "hedge_return_2d", "beta_60", "rv20_pct",
    "delta_logit_mad_24h", "family_delta_logit_q80", "family_dislocation_q80",
    "family_signed_ar2_q60", "available_at_utc", "decision_ts_utc",
})


def _weighted_median(values: pd.Series, weights: pd.Series) -> float:
    valid = values.notna() & weights.notna() & weights.gt(0.0)
    if not valid.any():
        return np.nan
    ordered = pd.DataFrame({"value": values[valid], "weight": weights[valid]}).sort_values("value")
    midpoint = ordered["weight"].sum() / 2.0
    return float(ordered.loc[ordered["weight"].cumsum().ge(midpoint), "value"].iloc[0])


def _weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    valid = values.notna() & weights.notna() & weights.gt(0.0)
    if not valid.any():
        return np.nan
    return float(np.average(values[valid].astype(float), weights=weights[valid].astype(float)))


def _assert_leg_schema(legs: pd.DataFrame) -> None:
    missing = REQUIRED_LEG_COLUMNS.difference(legs.columns)
    if missing:
        raise ValueError(f"CEDE legs missing required columns: {sorted(missing)}")


def cluster_event_legs(legs: pd.DataFrame, config: CEDEConfig = CEDEConfig()) -> pd.DataFrame:
    """Collapse Polymarket questions into one timestamp-safe event candidate.

    The caller supplies only the latest observation for each market at the
    decision time.  Any leg made available at or after the decision is dropped
    before aggregation, rather than silently used as a same-session feature.
    """
    _assert_leg_schema(legs)
    frame = legs.copy()
    frame["available_at_utc"] = pd.to_datetime(frame["available_at_utc"], utc=True)
    frame["decision_ts_utc"] = pd.to_datetime(frame["decision_ts_utc"], utc=True)
    frame = frame[frame["available_at_utc"] < frame["decision_ts_utc"]].copy()
    frame = frame[frame["family"].isin(FAMILIES)].copy()
    if frame.empty:
        return pd.DataFrame()

    records: list[dict[str, object]] = []
    for event_id, group in frame.groupby("economic_event_id", sort=False):
        # A cluster must have one family, one signed causal exposure and one
        # benchmark.  Ambiguity is a mapping failure, not a vote.
        if group["family"].nunique() != 1 or group["asset"].nunique() != 1 or group["hedge"].nunique() != 1 or group["expected_direction"].nunique() != 1:
            continue
        weights = group["weight"].clip(lower=0.0)
        posterior = _weighted_median(group["aligned_probability"], weights)
        delta = _weighted_mean(group["delta_logit"], weights)
        sign = float(np.sign(delta)) if np.isfinite(delta) else 0.0
        agreement = (
            float(weights[np.sign(group["delta_logit"].fillna(0.0)).eq(sign)].sum() / weights.sum())
            if sign != 0.0 and weights.sum() > 0.0 else 0.0
        )
        asset_return_1d = _weighted_mean(group["asset_return_1d"], weights)
        hedge_return_1d = _weighted_mean(group["hedge_return_1d"], weights)
        asset_return_2d = _weighted_mean(group["asset_return_2d"], weights)
        hedge_return_2d = _weighted_mean(group["hedge_return_2d"], weights)
        beta = _weighted_mean(group["beta_60"], weights)
        rv = _weighted_mean(group["rv20_pct"], weights)
        atr = _weighted_mean(group.get("atr20_pct", pd.Series(np.nan, index=group.index)), weights)
        direction = float(group["expected_direction"].iloc[0])
        abnormal_1d = asset_return_1d - beta * hedge_return_1d
        abnormal_2d = asset_return_2d - beta * hedge_return_2d
        mad = _weighted_mean(group["delta_logit_mad_24h"], weights)
        probability_z = delta / mad if np.isfinite(mad) and mad > 0.0 else np.nan
        price_z = abnormal_1d / rv if np.isfinite(rv) and rv > 0.0 else np.nan
        dislocation = direction * probability_z - direction * price_z
        records.append({
            "economic_event_id": event_id,
            "family": group["family"].iloc[0],
            "asset": group["asset"].iloc[0],
            "hedge": group["hedge"].iloc[0],
            "expected_direction": direction,
            "decision_ts_utc": group["decision_ts_utc"].max(),
            "event_leg_count": int(len(group)),
            "event_probability": posterior,
            "event_delta_logit": delta,
            "event_agreement": agreement,
            "mapping_confidence": _weighted_median(group["mapping_confidence"], weights),
            "business_days_to_event_end": _weighted_median(group["business_days_to_event_end"], weights),
            "abnormal_return_1d": abnormal_1d,
            "abnormal_return_2d": abnormal_2d,
            "rv20_pct": rv,
            "atr20_pct": atr,
            "delta_logit_mad_24h": mad,
            "probability_z": probability_z,
            "dislocation": dislocation,
            "family_delta_logit_q80": _weighted_median(group["family_delta_logit_q80"], weights),
            "family_dislocation_q80": _weighted_median(group["family_dislocation_q80"], weights),
            "family_signed_ar2_q60": _weighted_median(group["family_signed_ar2_q60"], weights),
        })
    candidates = pd.DataFrame(records)
    if candidates.empty:
        return candidates
    return candidates.assign(
        probability_update_ok=lambda x: x["event_delta_logit"].abs().ge(x["family_delta_logit_q80"]),
        dislocation_ok=lambda x: x["dislocation"].ge(x["family_dislocation_q80"]),
        price_not_fully_repriced=lambda x: (x["expected_direction"] * x["abnormal_return_2d"]).lt(x["family_signed_ar2_q60"]),
        timestamp_safe=True,
    )


def score_and_admit(
    candidates: pd.DataFrame,
    meta_predictions: pd.DataFrame,
    config: CEDEConfig = CEDEConfig(),
) -> pd.DataFrame:
    """Apply the family-aware meta-label and produce at most one row/event.

    ``meta_predictions`` must be chronological OOF/live predictions with
    positive expected abnormal return, loss probability, expected shortfall,
    all-in rotation cost and a precomputed family score percentile threshold.
    """
    required = {
        "economic_event_id", "probability_positive", "expected_positive_return",
        "probability_loss", "expected_shortfall", "all_in_rotation_cost",
        "family_edge_score_q80",
    }
    missing = required.difference(meta_predictions.columns)
    if missing:
        raise ValueError(f"CEDE meta predictions missing required columns: {sorted(missing)}")
    frame = candidates.merge(meta_predictions, on="economic_event_id", how="left", validate="one_to_one")
    frame["edge_score"] = (
        frame["probability_positive"] * frame["expected_positive_return"]
        - frame["probability_loss"] * frame["expected_shortfall"]
        - frame["all_in_rotation_cost"]
    )
    frame["entry_eligible"] = (
        frame["timestamp_safe"]
        & frame["event_probability"].ge(config.min_probability)
        & frame["event_agreement"].ge(config.min_agreement)
        & frame["mapping_confidence"].ge(config.min_mapping_confidence)
        & frame["business_days_to_event_end"].between(config.min_days_to_end, config.max_days_to_end)
        & frame["probability_update_ok"]
        & frame["dislocation_ok"]
        & frame["price_not_fully_repriced"]
        & frame["edge_score"].gt(0.0)
        & frame["edge_score"].ge(frame["family_edge_score_q80"])
    )
    return frame.sort_values(["decision_ts_utc", "edge_score", "economic_event_id"], ascending=[True, False, True]).reset_index(drop=True)


def risk_size(candidate: pd.Series, config: CEDEConfig = CEDEConfig()) -> float:
    """Volatility size an already-admitted event, before portfolio caps."""
    rv = float(candidate["rv20_pct"])
    threshold = float(candidate["family_edge_score_q80"])
    score = float(candidate["edge_score"])
    if not np.isfinite(rv) or rv <= 0.0 or not np.isfinite(score) or score <= 0.0:
        return 0.0
    conviction = min(1.0, score / max(threshold, 1e-9))
    volatility_size = config.daily_risk_budget_pct / max(rv, 0.01)
    return float(min(config.event_cap, volatility_size) * conviction)


def exit_decision(state: pd.Series, config: CEDEConfig = CEDEConfig()) -> str | None:
    """Return the next-open exit reason, or ``None`` to remain in the trade.

    Standing intraday catastrophe stops are checked by the execution layer
    from OHLC, using conservative gap/touch fills.  This function handles the
    pre-open information and two-complete-session dislocation decisions.
    """
    if bool(state.get("catastrophe_stop_touched", False)):
        return "catastrophe_atr_stop"
    probability = float(state["event_probability"])
    entry_update = float(state["entry_delta_logit"])
    current_update = float(state["event_delta_logit"])
    mad = float(state["delta_logit_mad_24h"])
    if probability < config.invalidation_probability:
        return "event_probability_invalidation"
    if np.isfinite(mad) and mad > 0.0 and current_update <= entry_update - config.reversal_mad * mad:
        return "event_probability_reversal"
    if int(state["complete_sessions_after_entry"]) >= 2 and float(state["dislocation"]) <= 0.0 and float(state["active_abnormal_return"]) <= 0.0:
        return "dislocation_closed_without_follow_through"
    if bool(state.get("final_tradable_session", False)):
        return "scheduled_event_end"
    return None
