"""Build the timestamp-safe Stage 2B feature audit table.

This is intentionally separate from the frozen compact ranking models.  It
adds the E4/E5-inspired continuous variables and legally-known arrival
pressure without silently promoting unavailable supporting-market data into a
model.  All price lookbacks use observations strictly before entry_date.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATES = PROJECT / "data" / "selection_stage1" / "decision_candidates.csv"
DEFAULT_PRICES = PROJECT / "assistant_generated_all_artifacts" / "assistant_generated_research_large_supplements" / "prices_open_merged.pkl"
DEFAULT_PROBS = PROJECT / "data" / "probs.pkl"
DEFAULT_OUTPUT = PROJECT / "data" / "selection_stage2b" / "feature_table"


SUPPORTING_COLUMNS = (
    "supporting_market_count",
    "supporting_question_count",
    "probability_mean",
    "probability_median",
    "probability_max",
    "probability_min",
    "probability_dispersion",
    "probability_standard_deviation",
    "number_of_distinct_deadlines",
    "earliest_signal_time",
    "latest_signal_time",
    "signal_age",
    "cross_market_directional_agreement",
    "event_novelty",
    "deadline_extension_only_flag",
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_candidates(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for column in ("entry_date", "t_e", "te1_exit_date"):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce", utc=True).dt.normalize()
    for column in ("same_day_candidate_count", "same_day_earnings_count", "same_day_geo_count", "recent_5d_candidate_count"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["feat_sector"] = frame.get("feat_sector", "unknown").fillna("unknown").astype(str)
    frame["event_family"] = frame.get("event_family", "other").fillna("other").astype(str).str.lower()
    return frame


def _price_frame(prices: dict[str, list[tuple[Any, ...]]]) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for symbol, rows in prices.items():
        values = []
        for row in rows:
            if len(row) < 5:
                continue
            values.append((pd.to_datetime(row[0], utc=True).normalize(), float(row[4])))
        if values:
            result[str(symbol)] = pd.DataFrame(values, columns=["date", "close"]).drop_duplicates("date").sort_values("date")
    return result


def _lookback(series: pd.DataFrame | None, entry_date: pd.Timestamp, periods: int) -> float:
    if series is None or series.empty:
        return np.nan
    prior = series.loc[series["date"] < entry_date, "close"].to_numpy(dtype=float)
    if len(prior) <= periods:
        return np.nan
    base = prior[-periods - 1]
    last = prior[-1]
    return float(last / base - 1.0) if np.isfinite(base) and abs(base) > 1e-12 else np.nan


def _returns_by_symbol(frame: pd.DataFrame, prices: dict[str, pd.DataFrame]) -> pd.DataFrame:
    out = frame.copy()
    out["stock_return_20d"] = [
        _lookback(prices.get(str(symbol)), date, 20)
        for symbol, date in zip(out["symbol"], out["entry_date"])
    ]
    out["stock_return_5d"] = [
        _lookback(prices.get(str(symbol)), date, 5)
        for symbol, date in zip(out["symbol"], out["entry_date"])
    ]
    # Sector return is a historical cross-sectional proxy.  Only peer prices
    # strictly before the row's entry date are used; no post-entry outcome or
    # future probability observation enters this calculation.
    sector_map = out[["symbol", "feat_sector"]].drop_duplicates("symbol").set_index("symbol")["feat_sector"].to_dict()
    sector_cache: dict[tuple[str, pd.Timestamp, int], float] = {}
    for period in (20, 5):
        values = []
        for sector, date in zip(out["feat_sector"], out["entry_date"]):
            key = (str(sector), pd.Timestamp(date), period)
            if key not in sector_cache:
                peer_returns = [
                    _lookback(prices.get(symbol), date, period)
                    for symbol, peer_sector in sector_map.items()
                    if str(peer_sector) == str(sector)
                ]
                finite = np.asarray([x for x in peer_returns if np.isfinite(x)], dtype=float)
                sector_cache[key] = float(np.median(finite)) if len(finite) else np.nan
            values.append(sector_cache[key])
        out[f"sector_return_{period}d"] = values
    out["stock_minus_sector_20d"] = out["stock_return_20d"] - out["sector_return_20d"]
    out["stock_minus_sector_return_5d"] = out["stock_return_5d"] - out["sector_return_5d"]
    group = out.groupby(["benchmark", "entry_date"], sort=False)
    out["same_day_stock_minus_sector_rank"] = group["stock_minus_sector_20d"].rank(pct=True, method="average")
    mean = group["stock_minus_sector_20d"].transform("mean")
    std = group["stock_minus_sector_20d"].transform("std").replace(0.0, np.nan)
    out["same_day_stock_minus_sector_zscore"] = (out["stock_minus_sector_20d"] - mean) / std
    return out


def _arrival_pressure(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["candidates_seen_previous_5_trading_days"] = np.nan
    out["event_candidates_seen_previous_5_days"] = np.nan
    out["earnings_candidate_count_previous_5_days"] = np.nan
    out["geopolitical_candidate_count_previous_5_days"] = np.nan
    for benchmark, bench in out.groupby("benchmark", sort=False):
        dates = sorted(pd.to_datetime(bench["entry_date"], utc=True).dropna().unique())
        for index, row in bench.iterrows():
            date = pd.Timestamp(row["entry_date"])
            prior_dates = [value for value in dates if value < date][-5:]
            prior = bench[bench["entry_date"].isin(prior_dates)]
            out.loc[index, "candidates_seen_previous_5_trading_days"] = float(len(prior))
            event_id = str(row.get("economic_event_group_clean", ""))
            out.loc[index, "event_candidates_seen_previous_5_days"] = float(
                (prior.get("economic_event_group_clean", pd.Series(index=prior.index, dtype=object)).astype(str) == event_id).sum()
            )
            families = prior["event_family"].astype(str)
            out.loc[index, "earnings_candidate_count_previous_5_days"] = float(families.eq("earnings").sum())
            out.loc[index, "geopolitical_candidate_count_previous_5_days"] = float(
                families.isin({"geopolitical", "geopolitics"}).sum()
            )
    out["current_earnings_season_intensity"] = pd.to_numeric(
        out.get("same_day_earnings_count", np.nan), errors="coerce"
    ) / np.maximum(pd.to_numeric(out.get("same_day_candidate_count", np.nan), errors="coerce"), 1.0)
    # Existing candidate arrival count is retained as an explicit alias.
    if "recent_5d_candidate_count" in out:
        out["candidates_seen_previous_5_trading_days_source"] = out["recent_5d_candidate_count"]
    return out


def build_feature_table(
    candidates_path: Path | str = DEFAULT_CANDIDATES,
    prices_path: Path | str = DEFAULT_PRICES,
    probs_path: Path | str = DEFAULT_PROBS,
    output_dir: Path | str = DEFAULT_OUTPUT,
) -> dict[str, Path]:
    candidates_path, prices_path, probs_path, output_dir = map(Path, (candidates_path, prices_path, probs_path, output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    frame = _read_candidates(candidates_path)
    prices = _price_frame(pickle.loads(prices_path.read_bytes()))
    features = _arrival_pressure(_returns_by_symbol(frame, prices))
    for column in SUPPORTING_COLUMNS:
        features[column] = np.nan
    # Effective probability history cannot be joined safely: probs.pkl uses
    # hashed market keys while the legally-collapsed candidate table contains
    # numeric source market IDs, and no verified ID map is present.
    for column in (
        "effective_probability_change_5d",
        "probability_change_x_stock_response",
        "probability_change_x_relative_stock_response",
    ):
        features[column] = np.nan
    features["expected_slot_days"] = pd.to_numeric(
        features.get("feat_time_to_resolution_days", features.get("slot_days", np.nan)), errors="coerce"
    )
    features["te1_horizon_assertion"] = pd.to_datetime(features["te1_exit_date"], utc=True, errors="coerce") < pd.to_datetime(
        features["t_e"], utc=True, errors="coerce"
    )
    selected_columns = [
        "benchmark", "analysis_split", "entry_date", "symbol", "event_family", "feat_sector",
        "stock_return_20d", "sector_return_20d", "stock_minus_sector_20d",
        "same_day_stock_minus_sector_rank", "same_day_stock_minus_sector_zscore",
        "effective_probability_change_5d", "stock_return_5d", "sector_return_5d",
        "stock_minus_sector_return_5d", "probability_change_x_stock_response",
        "probability_change_x_relative_stock_response", "expected_slot_days",
        "candidates_seen_previous_5_trading_days", "event_candidates_seen_previous_5_days",
        "earnings_candidate_count_previous_5_days", "geopolitical_candidate_count_previous_5_days",
        "current_earnings_season_intensity", "candidates_seen_previous_5_trading_days_source",
        *SUPPORTING_COLUMNS, "te1_exit_date", "t_e", "te1_horizon_assertion",
    ]
    selected_columns = [column for column in selected_columns if column in features.columns]
    table_path = output_dir / "timestamp_safe_feature_table.csv"
    features[selected_columns].to_csv(table_path, index=False)
    supporting_audit = {
        "supporting_market_relationship_available": False,
        "reason": "probs.pkl keys are hashed and candidate market IDs are numeric; no verified event-to-supporting-market map exists",
        "omitted_columns": list(SUPPORTING_COLUMNS),
        "probability_history_omitted_columns": [
            "effective_probability_change_5d",
            "probability_change_x_stock_response",
            "probability_change_x_relative_stock_response",
        ],
        "no_post_entry_observations_used": True,
        "entry_cutoff_rule": "price timestamps must be strictly earlier than entry_date",
    }
    audit_path = output_dir / "supporting_market_snapshot_audit.json"
    audit_path.write_text(json.dumps(supporting_audit, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "label": "timestamp_safe_stage2b_feature_table",
        "candidate_sha256": _hash(candidates_path),
        "prices_sha256": _hash(prices_path),
        "probs_sha256": _hash(probs_path),
        "derived_features": [
            "stock_return_20d", "sector_return_20d", "stock_minus_sector_20d",
            "same_day_stock_minus_sector_rank", "same_day_stock_minus_sector_zscore",
            "stock_return_5d", "sector_return_5d", "stock_minus_sector_return_5d",
            "expected_slot_days", "candidate_arrival_pressure",
        ],
        "supporting_market_features": "not_available_without_verified_id_map",
        "timestamp_rule": "strictly prior observations only",
        "te_is_never_exit": True,
        "terminal_horizon_assertion": bool(features["te1_horizon_assertion"].all()),
        "outputs": {"feature_table": str(table_path), "supporting_market_audit": str(audit_path)},
    }
    manifest_path = output_dir / "feature_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return {"feature_table": table_path, "audit": audit_path, "manifest": manifest_path}


if __name__ == "__main__":
    for name, path in build_feature_table().items():
        print(f"{name}: {path}")
