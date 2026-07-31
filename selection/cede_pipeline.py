"""Build a timestamp-safe CEDE research or paper-trading run.

This command deliberately separates three things that were conflated in the
old CEM experiments:

* canonical event mapping (one event exposure, even when many rows exist),
* features that were genuinely observable before the decision session, and
* admission/risk sizing, which is disabled until supplied with chronological
  OOF or live meta predictions.

It writes a complete audit even when no trades are admitted.  That behaviour
is intentional: a convenient-looking paper order must never substitute for a
validated expected-return and loss model.
"""

from __future__ import annotations

import argparse
import json
import pickle
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from core.features import SECTOR_ETFS
from core.polarity import resolve_polarity
from selection.causal_event_dislocation import CEDEConfig, cluster_event_legs, risk_size, score_and_admit
from selection.cede_event_map import build_canonical_event_map, load_policy


PROJECT = Path(__file__).resolve().parents[1]
SEMANTIC = PROJECT / "data" / "selection_stage2c" / "semantics" / "semantic_development_candidates.csv"
TARIFF = PROJECT / "data" / "tariff_run" / "tariff_candidates.parquet"
EARNINGS_HISTORY = PROJECT / "data" / "selection_stage2g" / "polymarket_download" / "polymarket_probability_history.csv"
MULTIFAMILY_HISTORY = PROJECT / "data" / "multifamily_probability_download" / "polymarket_probability_history.csv"
PRICES = PROJECT / "assistant_generated_all_artifacts" / "assistant_generated_research_large_supplements" / "prices_open_merged.pkl"
DEFAULT_OUTPUT = PROJECT / "data" / "cede" / "latest_run"

NYSE_EARLY_CLOSES = {pd.Timestamp("2025-11-28").date(): time(13, 0)}
META_REQUIRED = {
    "probability_positive", "expected_positive_return", "probability_loss",
    "expected_shortfall", "all_in_rotation_cost", "family_edge_score_q80",
}


@dataclass(frozen=True)
class PipelineConfig:
    """Pre-entry data-quality and calibration constraints, fixed before a run."""

    min_pre_entry_observations: int = 30
    min_history_hours: float = 24.0
    max_latest_age_minutes: float = 180.0
    min_prior_family_events: int = 20
    capital: float = 100_000.0


def _utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return False
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def _session_close(day: Any) -> pd.Timestamp:
    """NYSE close in UTC; price features intentionally stop before this session."""
    date = _utc(day).date()
    close = NYSE_EARLY_CLOSES.get(date, time(16, 0))
    return pd.Timestamp.combine(date, close).tz_localize("America/New_York").tz_convert("UTC")


def _business_days_to(event_end: Any, decision_ts: Any) -> int:
    end = _utc(event_end).date()
    start = _utc(decision_ts).date()
    return int(np.busday_count(start, end))


def _logit(values: np.ndarray) -> np.ndarray:
    return np.log(np.clip(values, 1e-5, 1.0 - 1e-5) / np.clip(1.0 - values, 1e-5, 1.0))


def _asof(times: pd.Series, values: np.ndarray, target: pd.Timestamp) -> float:
    position = int(times.searchsorted(target, side="right")) - 1
    return float(values[position]) if position >= 0 else np.nan


def _semantic_candidates() -> pd.DataFrame:
    """Normalize earnings/geo source rows and remove SPY-vs-QQQ duplicates."""
    columns = [
        "economic_event_id", "event_family", "market_id", "symbol", "question", "t0", "t_e",
        "entry_date", "mapping_type", "mapping_valid", "mapping_confidence", "feat_sector",
    ]
    data = pd.read_csv(SEMANTIC, usecols=columns, dtype={"market_id": str})
    data = data[data["event_family"].astype(str).str.lower().isin(["earnings", "geo"])].copy()
    data["entry_date"] = pd.to_datetime(data["entry_date"], errors="coerce", utc=True)
    data["t0"] = pd.to_datetime(data["t0"], errors="coerce", utc=True)
    data["t_e"] = pd.to_datetime(data["t_e"], errors="coerce", utc=True)
    data = data.dropna(subset=["economic_event_id", "market_id", "symbol", "question", "t0", "t_e", "entry_date"])
    # The legacy table has alternate benchmark rows for the same underlying
    # candidate.  Choosing the earliest scheduled decision is outcome-blind.
    keys = ["economic_event_id", "event_family", "market_id", "symbol", "question", "t0", "t_e"]
    data = data.sort_values("entry_date", kind="mergesort").groupby(keys, as_index=False, dropna=False).first()
    data["decision_ts_utc"] = data["entry_date"].map(_session_close)
    data["polarity"] = [resolve_polarity(str(q), str(s))[0] for q, s in zip(data["question"], data["symbol"], strict=True)]
    data["mapping_valid"] = data["mapping_valid"].map(_bool)
    data["sector_etf"] = data["feat_sector"].astype(str).map(SECTOR_ETFS).fillna("SPY")
    data["source"] = "semantic_development"
    return data


def _tariff_candidates() -> pd.DataFrame:
    """Add the audited tariff candidates as macro rows with explicit polarity."""
    data = pd.read_parquet(TARIFF).copy()
    data["economic_event_id"] = "tariff:" + data["event_id"].astype(str)
    data["event_family"] = "macro"
    data["t0"] = pd.to_datetime(data["t0"], errors="coerce", utc=True)
    data["t_e"] = pd.to_datetime(data["t_e"], errors="coerce", utc=True)
    data["decision_ts_utc"] = pd.to_datetime(data["t_theta"], errors="coerce", utc=True).map(_session_close)
    data["mapping_valid"] = True
    data["mapping_confidence"] = pd.to_numeric(data["feat_connection_strength"], errors="coerce")
    data["mapping_type"] = "predeclared_macro_basket_component"
    data["sector_etf"] = "SPY"
    data["source"] = "tariff_run"
    columns = [
        "economic_event_id", "event_family", "market_id", "symbol", "question", "t0", "t_e",
        "decision_ts_utc", "polarity", "mapping_valid", "mapping_confidence", "mapping_type",
        "sector_etf", "source",
    ]
    return data[columns].dropna(subset=["t0", "t_e", "decision_ts_utc"])


def load_raw_candidates() -> pd.DataFrame:
    """Load all in-scope family rows without using realized returns or labels."""
    data = pd.concat([_semantic_candidates(), _tariff_candidates()], ignore_index=True, sort=False)
    data["market_id"] = data["market_id"].astype(str)
    data["symbol"] = data["symbol"].astype(str).str.upper()
    if data[["decision_ts_utc", "t0", "t_e"]].isna().any().any():
        raise ValueError("CEDE source candidates contain an unusable timestamp")
    return data.sort_values(["decision_ts_utc", "economic_event_id", "market_id", "symbol"]).reset_index(drop=True)


class PriceBook:
    """Daily OHLC view with an explicit *strictly prior session* convention."""

    def __init__(self, raw_prices: dict[str, list[tuple[Any, ...]]]):
        self._bars: dict[str, list[dict[str, float | pd.Timestamp]]] = {}
        for symbol, records in raw_prices.items():
            bars: list[dict[str, float | pd.Timestamp]] = []
            for record in records:
                if len(record) < 5:
                    continue
                values = [float(value) for value in record[1:5]]
                if not np.isfinite(values).all():
                    continue
                bars.append({
                    "date": _utc(record[0]).normalize(), "open": values[0], "high": values[1],
                    "low": values[2], "close": values[3],
                })
            self._bars[str(symbol).upper()] = sorted(bars, key=lambda row: pd.Timestamp(row["date"]))

    @classmethod
    def from_default_file(cls) -> "PriceBook":
        return cls(pickle.loads(PRICES.read_bytes()))

    def _prior_bars(self, symbol: str, decision_ts: pd.Timestamp) -> list[dict[str, float | pd.Timestamp]]:
        cutoff_day = _utc(decision_ts).normalize()
        return [row for row in self._bars.get(str(symbol).upper(), []) if pd.Timestamp(row["date"]) < cutoff_day]

    def _returns(self, symbol: str, decision_ts: pd.Timestamp) -> pd.Series:
        bars = self._prior_bars(symbol, decision_ts)
        if len(bars) < 2:
            return pd.Series(dtype=float)
        closes = pd.Series(
            [float(row["close"]) for row in bars],
            index=pd.DatetimeIndex([pd.Timestamp(row["date"]) for row in bars]), dtype=float,
        )
        return closes.pct_change().dropna()

    def _basket_returns(self, components: list[str], decision_ts: pd.Timestamp) -> pd.Series:
        series = [self._returns(symbol, decision_ts).rename(symbol) for symbol in components]
        if not series or any(value.empty for value in series):
            return pd.Series(dtype=float)
        combined = pd.concat(series, axis=1, sort=False).dropna(how="any")
        return combined.mean(axis=1) if not combined.empty else pd.Series(dtype=float)

    def _basket_return(self, components: list[str], decision_ts: pd.Timestamp, sessions: int) -> float:
        values = self._basket_returns(components, decision_ts)
        if len(values) < sessions:
            return np.nan
        return float(np.prod(1.0 + values.iloc[-sessions:].to_numpy(dtype=float)) - 1.0)

    def _atr_pct(self, components: list[str], decision_ts: pd.Timestamp) -> float:
        values: list[float] = []
        for symbol in components:
            bars = self._prior_bars(symbol, decision_ts)[-21:]
            if len(bars) < 3:
                return np.nan
            ranges = []
            for previous, current in zip(bars[:-1], bars[1:]):
                high, low, close = float(current["high"]), float(current["low"]), float(previous["close"])
                ranges.append(max(high - low, abs(high - close), abs(low - close)))
            latest_close = float(bars[-1]["close"])
            values.append(float(np.mean(ranges) / latest_close) if latest_close > 0 else np.nan)
        return float(np.mean(values)) if values and np.isfinite(values).all() else np.nan

    def features(self, components: list[str], hedge: str, decision_ts: pd.Timestamp) -> dict[str, float]:
        asset_returns = self._basket_returns(components, decision_ts)
        hedge_returns = self._returns(hedge, decision_ts)
        aligned = pd.concat([asset_returns.rename("asset"), hedge_returns.rename("hedge")], axis=1, sort=False).dropna()
        beta = np.nan
        if len(aligned) >= 20 and float(aligned["hedge"].var()) > 1e-12:
            beta = float(aligned["asset"].tail(60).cov(aligned["hedge"].tail(60)) / aligned["hedge"].tail(60).var())
        return {
            "asset_return_1d": self._basket_return(components, decision_ts, 1),
            "asset_return_2d": self._basket_return(components, decision_ts, 2),
            "hedge_return_1d": self._basket_return([hedge], decision_ts, 1),
            "hedge_return_2d": self._basket_return([hedge], decision_ts, 2),
            "beta_60": beta,
            "rv20_pct": float(asset_returns.tail(20).std(ddof=0)) if len(asset_returns) >= 20 else np.nan,
            "atr20_pct": self._atr_pct(components, decision_ts),
        }

    def reference_close(self, symbol: str, decision_ts: pd.Timestamp) -> float:
        bars = self._prior_bars(symbol, decision_ts)
        return float(bars[-1]["close"]) if bars else np.nan


def _load_probability_paths(
    earnings_history: Path = EARNINGS_HISTORY,
    multifamily_history: Path = MULTIFAMILY_HISTORY,
) -> dict[str, pd.DataFrame]:
    paths = []
    for path in (earnings_history, multifamily_history):
        frame = pd.read_csv(path, dtype={"market_id": str}, parse_dates=["source_ts_utc", "available_at_utc"])
        frame["market_id"] = frame["market_id"].astype(str)
        paths.append(frame[["market_id", "source_ts_utc", "available_at_utc", "probability_yes"]])
    data = pd.concat(paths, ignore_index=True).drop_duplicates(["market_id", "available_at_utc"], keep="last")
    data["source_ts_utc"] = pd.to_datetime(data["source_ts_utc"], utc=True)
    data["available_at_utc"] = pd.to_datetime(data["available_at_utc"], utc=True)
    data["probability_yes"] = pd.to_numeric(data["probability_yes"], errors="coerce")
    return {market: group.sort_values("available_at_utc").reset_index(drop=True) for market, group in data.groupby("market_id", sort=False)}


def _probability_features(leg: pd.Series, path: pd.DataFrame | None) -> dict[str, Any]:
    """Compute only observations with public timestamps strictly before decision."""
    decision = _utc(leg["decision_ts_utc"])
    if path is None:
        return {"coverage_status": "missing_market_history"}
    # ``t0`` is when the legacy candidate builder first noticed the market,
    # not the creation time of the Polymarket contract.  Earlier public quotes
    # are legitimate pre-decision history and are required to measure a 24h
    # update; only the decision cutoff is a leakage boundary.
    selected = path[path["available_at_utc"] < decision].copy()
    selected = selected.dropna(subset=["probability_yes"]).sort_values("available_at_utc")
    if selected.empty:
        return {"coverage_status": "no_strict_pre_entry_observation"}
    if not selected["available_at_utc"].lt(decision).all():
        raise AssertionError("CEDE probability feature attempted to use a post-decision observation")
    times = selected["available_at_utc"].reset_index(drop=True)
    raw = selected["probability_yes"].to_numpy(dtype=float)
    values = raw if int(leg["expected_direction"]) == 1 else 1.0 - raw
    logits = _logit(values)
    latest = float(values[-1])
    prior_24 = _asof(times, values, decision - pd.Timedelta(hours=24))
    delta = float(logits[-1] - _logit(np.asarray([prior_24]))[0]) if np.isfinite(prior_24) else np.nan

    # Vectorized 24-hour as-of pairing.  The former row-by-row implementation
    # was correct but quadratic on minute histories; this remains strictly
    # pre-decision and makes a full 1.1m-observation audit practical.
    # Pandas 3 may store timezone-aware values at microsecond resolution;
    # normalize to nanoseconds before subtracting the nanosecond Timedelta.
    time_ns = times.dt.as_unit("ns").astype("int64").to_numpy(dtype=np.int64)
    prior_index = np.searchsorted(time_ns, time_ns - int(pd.Timedelta(hours=24).value), side="right") - 1
    valid_pairs = prior_index >= 0
    rolling_deltas = logits[valid_pairs] - logits[prior_index[valid_pairs]]
    scale = np.nan
    if len(rolling_deltas) >= 5:
        centre = float(np.median(rolling_deltas))
        scale = float(1.4826 * np.median(np.abs(rolling_deltas - centre)))
    if not np.isfinite(scale):
        scale = np.nan
    span_hours = float((times.iloc[-1] - times.iloc[0]).total_seconds() / 3600.0)
    age_minutes = float((decision - times.iloc[-1]).total_seconds() / 60.0)
    return {
        "aligned_probability": latest,
        "delta_logit": delta,
        "delta_logit_mad_24h": max(scale, 0.02) if np.isfinite(scale) else np.nan,
        "strict_pre_entry_observations": int(len(selected)),
        "strict_pre_entry_span_hours": span_hours,
        "latest_observation_age_minutes": age_minutes,
        "path_first_available_at_utc": times.iloc[0],
        "path_last_available_at_utc": times.iloc[-1],
        "post_decision_observations_used": 0,
        "coverage_status": "ok",
    }


def _attach_expanding_thresholds(candidates: pd.DataFrame, minimum_events: int) -> pd.DataFrame:
    """Attach family thresholds using only strictly earlier decision sessions."""
    frame = candidates.sort_values(["family", "decision_ts_utc", "trade_event_id"], kind="mergesort").copy()
    columns = ["family_delta_logit_q80", "family_dislocation_q80", "family_signed_ar2_q60", "prior_family_events"]
    for column in columns:
        frame[column] = np.nan
    for family, family_rows in frame.groupby("family", sort=False):
        seen: list[pd.Series] = []
        for timestamp, batch in family_rows.groupby("decision_ts_utc", sort=True):
            history = pd.DataFrame(seen)
            if len(history) >= minimum_events:
                delta = pd.to_numeric(history["event_delta_logit"], errors="coerce").abs().dropna()
                dislocation = pd.to_numeric(history["dislocation"], errors="coerce").dropna()
                signed_ar2 = pd.to_numeric(history["expected_direction"], errors="coerce") * pd.to_numeric(history["abnormal_return_2d"], errors="coerce")
                if len(delta) >= minimum_events and len(dislocation) >= minimum_events and signed_ar2.notna().sum() >= minimum_events:
                    frame.loc[batch.index, "family_delta_logit_q80"] = float(delta.quantile(0.80))
                    frame.loc[batch.index, "family_dislocation_q80"] = float(dislocation.quantile(0.80))
                    frame.loc[batch.index, "family_signed_ar2_q60"] = float(signed_ar2.quantile(0.60))
                    frame.loc[batch.index, "prior_family_events"] = int(history["trade_event_id"].nunique())
            seen.extend(batch.to_dict("records"))
    frame["probability_update_ok"] = frame["event_delta_logit"].abs().ge(frame["family_delta_logit_q80"])
    frame["dislocation_ok"] = frame["dislocation"].ge(frame["family_dislocation_q80"])
    frame["price_not_fully_repriced"] = (
        frame["expected_direction"] * frame["abnormal_return_2d"]
    ).lt(frame["family_signed_ar2_q60"])
    return frame


def _coverage_summary(legs: pd.DataFrame, config: PipelineConfig) -> pd.DataFrame:
    frame = legs.copy()
    frame["coverage_sufficient"] = (
        frame["strict_pre_entry_observations"].ge(config.min_pre_entry_observations)
        & frame["strict_pre_entry_span_hours"].ge(config.min_history_hours)
        & frame["latest_observation_age_minutes"].le(config.max_latest_age_minutes)
    )
    return frame


def _admission_status(row: pd.Series, meta_present: bool) -> str:
    if not bool(row.get("coverage_sufficient", False)):
        return "blocked_probability_coverage"
    if not bool(row.get("calibration_available", False)):
        return "blocked_insufficient_prior_family_events"
    if not meta_present:
        return "blocked_missing_chronological_meta_predictions"
    if not bool(row.get("entry_eligible", False)):
        return "rejected_by_cede_gate"
    return "admitted"


def _read_meta_predictions(path: Path) -> pd.DataFrame:
    data = pd.read_csv(path)
    key = "trade_event_id" if "trade_event_id" in data.columns else "economic_event_id"
    data = data.rename(columns={key: "trade_event_id"})
    missing = META_REQUIRED.difference(data.columns)
    if missing:
        raise ValueError(f"CEDE meta-prediction file is missing: {sorted(missing)}")
    if data["trade_event_id"].duplicated().any():
        raise ValueError("CEDE meta-prediction file must contain one row per trade_event_id")
    return data[["trade_event_id", *sorted(META_REQUIRED)]].copy()


def allocate_admitted(candidates: pd.DataFrame, cede: CEDEConfig = CEDEConfig()) -> pd.DataFrame:
    """Apply event/family/gross/proxy caps to planned holdings, chronologically.

    A planned end is deliberately conservative: capacity is not recycled for a
    possible early exit that was unknowable at admission time.
    """
    output = candidates.copy()
    output["allocated_weight"] = 0.0
    output["allocation_status"] = np.where(output.get("entry_eligible", False), "unallocated", "not_admitted")
    active: list[dict[str, Any]] = []
    next_id = 1
    for index, row in output.sort_values(["decision_ts_utc", "edge_score", "trade_event_id"], ascending=[True, False, True]).iterrows():
        if not bool(row.get("entry_eligible", False)):
            continue
        start = _utc(row["decision_ts_utc"])
        active = [position for position in active if position["planned_end"] >= start]
        desired = risk_size(row, cede)
        components = json.loads(row["components_json"])
        planned_end = _utc(row["event_end_utc"]) - pd.offsets.BDay(1)
        family = str(row["family"])
        family_used = sum(position["weight"] for position in active if position["family"] == family)
        gross_used = sum(position["weight"] for position in active)
        root_open = any(position["root_event_id"] == str(row["root_event_id"]) for position in active)
        component_used = {
            component: sum(position["weight"] / len(position["components"]) for position in active if component in position["components"])
            for component in components
        }
        cap = cede.family_cap_map.get(family, 0.0)
        if desired <= 0:
            status = "rejected_zero_risk_size"
        elif root_open:
            status = "rejected_root_event_already_open"
        elif gross_used + desired > cede.gross_event_cap + 1e-12:
            status = "rejected_gross_capacity"
        elif family_used + desired > cap + 1e-12:
            status = "rejected_family_capacity"
        elif any(used + desired / len(components) > cede.proxy_cap + 1e-12 for used in component_used.values()):
            status = "rejected_proxy_capacity"
        else:
            status = "allocated"
            output.at[index, "allocated_weight"] = desired
            output.at[index, "position_id"] = f"CEDE-{next_id:04d}"
            next_id += 1
            active.append({
                "root_event_id": str(row["root_event_id"]), "family": family, "weight": desired,
                "components": components, "planned_end": planned_end,
            })
        output.at[index, "allocation_status"] = status
    return output


def build_order_blotter(allocations: pd.DataFrame, prices: PriceBook, capital: float, cede: CEDEConfig = CEDEConfig()) -> pd.DataFrame:
    """Create paper orders and an explicit conservative exit/watchlist instruction."""
    rows: list[dict[str, Any]] = []
    for _, event in allocations[allocations["allocation_status"].eq("allocated")].iterrows():
        components = json.loads(event["components_json"])
        component_weight = float(event["allocated_weight"]) / len(components)
        for component in components:
            reference = prices.reference_close(component, _utc(event["decision_ts_utc"]))
            rows.append({
                "position_id": event["position_id"],
                "trade_event_id": event["trade_event_id"],
                "family": event["family"],
                "component": component,
                "component_weight": component_weight,
                "paper_notional": capital * component_weight,
                "decision_ts_utc": event["decision_ts_utc"],
                "entry_instruction": "enter_at_next_regular_session_open",
                "reference_close_before_decision": reference,
                "standing_catastrophe_stop": (
                    reference * (1.0 - cede.catastrophe_atr_multiple * float(event["atr20_pct"]))
                    if np.isfinite(reference) and np.isfinite(event["atr20_pct"]) else np.nan
                ),
                "preopen_exit_rule": "exit if aligned_probability < 0.55 or 24h logit update reverses by >= 1 MAD",
                "after_two_sessions_rule": "exit next open only when dislocation <= 0 and active abnormal return <= 0",
                "scheduled_exit_rule": "exit before event resolution / on final tradable session",
                "event_end_utc": event["event_end_utc"],
            })
    return pd.DataFrame(rows)


def _write_report(output: Path, *, raw: pd.DataFrame, event_map: pd.DataFrame, issues: pd.DataFrame, legs: pd.DataFrame, candidates: pd.DataFrame, orders: pd.DataFrame, meta_path: Path | None, config: PipelineConfig) -> None:
    coverage = legs.get("coverage_sufficient", pd.Series(False, index=legs.index)).mean() if len(legs) else 0.0
    admitted = int(candidates.get("entry_eligible", pd.Series(False, index=candidates.index)).sum())
    lines = [
        "# CEDE run report",
        "",
        "## Status",
        "",
        ("Paper-order eligible only; do not treat the output as live validation." if meta_path else "Research-only: no chronological OOF/live meta-prediction file was supplied, so no orders are admitted."),
        "",
        "## Audit counts",
        "",
        f"- Raw source rows: {len(raw)}",
        f"- Canonical event exposures: {len(event_map)}",
        f"- Canonical probability legs: {len(legs)}",
        f"- Rejected mapping episodes: {len(issues)}",
        f"- Probability-leg coverage passing policy: {coverage:.1%}",
        f"- Event candidates after aggregation: {len(candidates)}",
        f"- CEDE admissions: {admitted}",
        f"- Allocated paper components: {len(orders)}",
        "",
        "## Safety invariants",
        "",
        "- Every probability observation is strictly earlier than its decision timestamp.",
        "- Price features use sessions strictly before the decision session.",
        "- Probability questions aggregate to one event; component rows are execution legs, not independent votes.",
        "- Family thresholds are expanding and exclude the current simultaneous decision session.",
        "- Admission requires a separately supplied chronological meta-prediction file; the engine never creates expected return from realized outcomes.",
        "",
        "## Fixed policy",
        "",
        f"- Min pre-entry history: {config.min_pre_entry_observations} observations, {config.min_history_hours:g} hours, latest update <= {config.max_latest_age_minutes:g} minutes old.",
        f"- Family calibration: at least {config.min_prior_family_events} prior event decisions.",
        "- Stop: 2.5 ATR catastrophe stop; no take-profit target. Exit decisions use probability invalidation/reversal, failed two-session follow-through, or final event session.",
    ]
    (output / "CEDE_RUN_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(output: Path, meta_path: Path | None = None, config: PipelineConfig = PipelineConfig()) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    raw = load_raw_candidates()
    policy = load_policy()
    event_map, canonical_legs, mapping_issues = build_canonical_event_map(raw, policy)
    prices = PriceBook.from_default_file()
    paths = _load_probability_paths()

    price_rows = []
    for _, event in event_map.iterrows():
        components = json.loads(event["components_json"])
        price_rows.append({"trade_event_id": event["trade_event_id"], **prices.features(components, str(event["hedge"]), _utc(event["decision_ts_utc"]))})
    price_features = pd.DataFrame(price_rows)
    legs = canonical_legs.merge(price_features, on="trade_event_id", how="left", validate="many_to_one")
    probability_rows = [_probability_features(row, paths.get(str(row["market_id"]))) for _, row in legs.iterrows()]
    legs = pd.concat([legs.reset_index(drop=True), pd.DataFrame(probability_rows)], axis=1)
    legs["business_days_to_event_end"] = [
        _business_days_to(end, decision) for end, decision in zip(legs["market_event_end_utc"], legs["decision_ts_utc"], strict=True)
    ]
    legs = _coverage_summary(legs, config)
    legs["available_at_utc"] = pd.to_datetime(legs.get("path_last_available_at_utc"), errors="coerce", utc=True)
    # Placeholders are overwritten after one-event aggregation by expanding,
    # prior-only family calibration.  They are required by the pure CEDE core.
    legs["family_delta_logit_q80"] = np.nan
    legs["family_dislocation_q80"] = np.nan
    legs["family_signed_ar2_q60"] = np.nan
    engine_legs = legs.rename(columns={"trade_event_id": "economic_event_id"}).copy()
    events = cluster_event_legs(engine_legs, CEDEConfig()).rename(columns={"economic_event_id": "trade_event_id"})
    events = events.merge(event_map, on=["trade_event_id", "family", "asset", "hedge", "expected_direction", "mapping_confidence", "decision_ts_utc"], how="left", validate="one_to_one")
    audit = legs.groupby("trade_event_id", as_index=False).agg(
        event_coverage_sufficient=("coverage_sufficient", "all"),
        min_pre_entry_observations=("strict_pre_entry_observations", "min"),
        min_pre_entry_span_hours=("strict_pre_entry_span_hours", "min"),
        max_latest_observation_age_minutes=("latest_observation_age_minutes", "max"),
        any_post_decision_observations=("post_decision_observations_used", "sum"),
    )
    events = events.merge(audit, on="trade_event_id", how="left", validate="one_to_one")
    events["timestamp_safe"] = events["timestamp_safe"] & events["event_coverage_sufficient"].fillna(False)
    events = _attach_expanding_thresholds(events, config.min_prior_family_events)
    events["calibration_available"] = events["prior_family_events"].ge(config.min_prior_family_events)
    events["coverage_sufficient"] = events["event_coverage_sufficient"].fillna(False)

    meta_present = meta_path is not None
    if meta_path:
        meta = _read_meta_predictions(meta_path)
        model_input = events.rename(columns={"trade_event_id": "economic_event_id"})
        admitted = score_and_admit(model_input, meta.rename(columns={"trade_event_id": "economic_event_id"}), CEDEConfig())
        events = admitted.rename(columns={"economic_event_id": "trade_event_id"})
    else:
        for column in META_REQUIRED | {"edge_score"}:
            events[column] = np.nan
        events["entry_eligible"] = False
    events["admission_status"] = events.apply(_admission_status, axis=1, meta_present=meta_present)
    allocations = allocate_admitted(events, CEDEConfig())
    orders = build_order_blotter(allocations, prices, config.capital, CEDEConfig())

    raw.to_csv(output / "raw_in_scope_candidates.csv", index=False)
    event_map.to_csv(output / "canonical_event_map.csv", index=False)
    mapping_issues.to_csv(output / "canonical_mapping_rejections.csv", index=False)
    legs.to_csv(output / "pre_entry_probability_and_price_legs.csv", index=False)
    allocations.to_csv(output / "event_candidates_and_allocations.csv", index=False)
    orders.to_csv(output / "paper_order_blotter.csv", index=False)
    _write_report(output, raw=raw, event_map=event_map, issues=mapping_issues, legs=legs, candidates=allocations, orders=orders, meta_path=meta_path, config=config)
    manifest = {
        "mode": "paper_candidate_generation" if meta_path else "research_only_no_meta_predictions",
        "policy_version": policy.get("policy_version"),
        "raw_candidates": len(raw), "canonical_events": len(event_map), "canonical_legs": len(legs),
        "mapping_rejections": len(mapping_issues), "event_candidates": len(allocations),
        "admissions": int(allocations["entry_eligible"].sum()), "allocated_components": len(orders),
        "meta_predictions": str(meta_path) if meta_path else None,
        "data_sources": {"semantic": str(SEMANTIC), "tariff": str(TARIFF), "earnings_history": str(EARNINGS_HISTORY), "multifamily_history": str(MULTIFAMILY_HISTORY), "prices": str(PRICES)},
    }
    (output / "run_manifest.json").write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a timestamp-safe CEDE research/paper run.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--meta-predictions", type=Path, default=None, help="Chronological OOF/live CEDE meta predictions keyed by trade_event_id.")
    parser.add_argument("--capital", type=float, default=PipelineConfig.capital)
    parser.add_argument("--min-prior-family-events", type=int, default=PipelineConfig.min_prior_family_events)
    args = parser.parse_args()
    config = PipelineConfig(capital=float(args.capital), min_prior_family_events=int(args.min_prior_family_events))
    manifest = run(args.output, args.meta_predictions, config)
    print(json.dumps(manifest, indent=2, default=str))


if __name__ == "__main__":
    main()
