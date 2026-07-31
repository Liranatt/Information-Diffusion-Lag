"""Timestamp and event-cluster audit for CEDE's geo/macro inputs."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from analysis.download_stage2g_polymarket_history import decision_timestamp


PROJECT = Path(__file__).resolve().parents[1]
SEMANTIC = PROJECT / "data" / "selection_stage2c" / "semantics" / "semantic_development_candidates.csv"
TARIFF = PROJECT / "data" / "tariff_run" / "tariff_candidates.parquet"
HISTORY = PROJECT / "data" / "multifamily_probability_download" / "polymarket_probability_history.csv"
OUTPUT = PROJECT / "data" / "multifamily_probability_download" / "cede_coverage"


def _candidates() -> pd.DataFrame:
    semantic = pd.read_csv(
        SEMANTIC,
        usecols=["economic_event_id", "market_id", "symbol", "entry_date", "event_family"],
        dtype={"market_id": str},
    )
    geo = semantic[semantic["event_family"].eq("geo")].copy()
    geo["family"] = "geopolitics"
    geo["economic_event_id"] = geo["economic_event_id"].where(
        geo["economic_event_id"].notna(),
        pd.Series("source:" + geo.index.astype(str), index=geo.index),
    )
    tariff = pd.read_parquet(TARIFF)[["event_id", "market_id", "symbol", "t_theta"]].copy()
    tariff = tariff.rename(columns={"event_id": "economic_event_id", "t_theta": "entry_date"})
    tariff["market_id"] = tariff["market_id"].astype(str)
    tariff["economic_event_id"] = "tariff:" + tariff["economic_event_id"].astype(str)
    tariff["family"] = "macro"
    frame = pd.concat([geo[["economic_event_id", "market_id", "symbol", "entry_date", "family"]], tariff], ignore_index=True)
    frame["decision_ts_utc"] = frame["entry_date"].map(decision_timestamp)
    return frame


def run(output: Path = OUTPUT) -> dict[str, int]:
    output.mkdir(parents=True, exist_ok=True)
    candidates = _candidates()
    history = pd.read_csv(HISTORY, dtype={"market_id": str}, parse_dates=["source_ts_utc"])
    history["source_ts_utc"] = pd.to_datetime(history["source_ts_utc"], utc=True)
    rows = []
    for candidate in candidates.itertuples(index=False):
        points = history[(history["market_id"] == candidate.market_id) & (history["source_ts_utc"] < candidate.decision_ts_utc)]
        first = points["source_ts_utc"].min() if len(points) else pd.NaT
        last = points["source_ts_utc"].max() if len(points) else pd.NaT
        span = (last - first).total_seconds() / 3600.0 if len(points) else 0.0
        rows.append({
            **candidate._asdict(),
            "strict_pre_entry_observations": int(len(points)),
            "strict_pre_entry_span_hours": span,
            "latest_observation_age_seconds": (candidate.decision_ts_utc - last).total_seconds() if len(points) else None,
            "has_minimum_6h_update_history": bool(len(points) >= 30 and span >= 6.0),
        })
    detail = pd.DataFrame(rows)
    detail.to_csv(output / "candidate_probability_coverage.csv", index=False)
    coverage = (
        detail.groupby("family", as_index=False)
        .agg(
            candidate_rows=("market_id", "size"),
            markets=("market_id", "nunique"),
            economic_events=("economic_event_id", "nunique"),
            assets=("symbol", "nunique"),
            min_observations=("strict_pre_entry_observations", "min"),
            median_observations=("strict_pre_entry_observations", "median"),
            minimum_history_share=("has_minimum_6h_update_history", "mean"),
        )
    )
    coverage.to_csv(output / "family_coverage_summary.csv", index=False)
    clusters = (
        detail.groupby(["family", "economic_event_id"], as_index=False)
        .agg(markets=("market_id", "nunique"), asset_exposures=("symbol", "nunique"), symbols=("symbol", lambda values: "|".join(sorted(set(values)))))
    )
    clusters.to_csv(output / "economic_event_cluster_audit.csv", index=False)
    return {"candidate_rows": int(len(detail)), "families": int(detail["family"].nunique())}


if __name__ == "__main__":
    print(run())
