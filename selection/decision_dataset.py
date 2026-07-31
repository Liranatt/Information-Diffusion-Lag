"""Build the first-stage candidate and competition datasets.

The dataset is intentionally selection-only.  Sizing and learned exits remain
outside this module.  ``t_e`` is the scheduled event timestamp; it is never an
exit timestamp.  The terminal horizon is the final legal trading session
strictly before ``t_e`` (the stored ``te1_exit_date_dt`` column).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    PROJECT
    / "assistant_generated_all_artifacts"
    / "assistant_generated_research_core"
    / "trade_opportunity_research"
    / "symbol_day_current_priority.csv"
)
DEFAULT_OUTPUT = PROJECT / "data" / "selection_stage1"

# These are known at the decision date and deliberately exclude raw IDs,
# outcome columns, and the legacy split/selection labels.
EX_ANTE_FEATURES = (
    "feat_connection_strength",
    "entry_prob",
    "feat_prob_at_trigger",
    "feat_prob_slope_24h",
    "feat_prob_volatility",
    "feat_prob_surge_since_t0",
    "feat_time_to_resolution_days",
    "feat_crossing_latency_days",
    "feat_pre_entry_volume_log",
    "feat_runup_since_t0",
    "feat_asset_2w_trend",
    "feat_sector_1m_trend",
    "feat_spy_2w_trend",
    "feat_ytd_change",
    "feat_beta",
    "feat_profit_margin",
    "feat_log_market_cap",
)

CATEGORICAL_FEATURES = ("benchmark", "feat_archetype", "feat_sector", "event_family")
METADATA_COLUMNS = (
    "benchmark",
    "analysis_split",
    "entry_date",
    "symbol",
    "event_family",
    "economic_event_group_clean",
    "economic_event_id",
    "source_row_number",
    "feat_sector",
    "feat_archetype",
    "connection_strength",
    "candidate_rows",
    "market_count",
    "timestamp_resolution",
    "decision_time_known",
    "legacy_selected",
)
TARGET_COLUMNS = (
    "te1_exit_date",
    "te1_net_return_pct",
    "te1_active_net_return_pct",
    "te1_active_gross_return_pct",
    "slot_days",
    "active_return_per_slot_day_pct",
)

OUTCOME_COLUMNS = {
    "return_pct",
    "active_close_return_at_exit_pct",
    "hardcap_return_pct",
    "hardcap_active_close_return_at_exit_pct",
    "selected_pnl",
    "selected_pnl_pct",
    "selected_gross_pnl",
    "stock_te1_gross_return_pct",
    "stock_te1_net_return_pct",
    "spy_net_return_pct",
    "qqq_net_return_pct",
    "te1_active_vs_spy_gross_pct",
    "te1_active_vs_spy_net_twin_pct",
    "te1_active_vs_qqq_gross_pct",
    "te1_active_vs_qqq_net_twin_pct",
}


def _numeric(df: pd.DataFrame, columns: list[str]) -> None:
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")


def _parse_dates(df: pd.DataFrame) -> None:
    for col in ("entry_date", "t_e", "te1_exit_date_dt", "hardcap_exit_date", "t_theta_dt"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True).dt.normalize()


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _rank_pct(series: pd.Series, ascending: bool) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    if values.notna().sum() <= 1:
        return pd.Series(0.5, index=series.index, dtype=float)
    return values.rank(method="average", pct=True, ascending=ascending).fillna(0.0)


def _active_te1_column(benchmark: str) -> str:
    key = str(benchmark).lower()
    if key not in {"spy", "qqq"}:
        raise ValueError(f"Unsupported benchmark for active T_e-1 target: {benchmark}")
    return f"te1_active_vs_{key}_net_twin_pct"


def _prepare_candidates(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    df = raw.copy()
    _parse_dates(df)
    _numeric(
        df,
        list(EX_ANTE_FEATURES)
        + [
            "stock_te1_gross_return_pct",
            "stock_te1_net_return_pct",
            "te1_active_vs_spy_gross_pct",
            "te1_active_vs_spy_net_twin_pct",
            "te1_active_vs_qqq_gross_pct",
            "te1_active_vs_qqq_net_twin_pct",
            "candidate_rows",
            "market_count",
        ],
    )

    # The table is already collapsed to one symbol-day.  Keep a defensive
    # duplicate check so future inputs cannot silently reintroduce duplicates.
    duplicate_key = ["benchmark", "entry_date", "symbol"]
    duplicate_rows = int(df.duplicated(duplicate_key, keep=False).sum())
    if duplicate_rows:
        raise ValueError(f"Input is not symbol-day collapsed: {duplicate_rows} duplicate rows")

    df["timestamp_resolution"] = "daily"
    df["decision_time_known"] = False
    if "selected_by_portfolio" in df.columns:
        df["legacy_selected"] = df["selected_by_portfolio"].map(_as_bool)
    else:
        df["legacy_selected"] = False
    df["te1_exit_date"] = df["te1_exit_date_dt"]
    df["valid_te1_window"] = (
        df["entry_date"].notna()
        & df["te1_exit_date"].notna()
        & df["t_e"].notna()
        & (df["entry_date"] < df["te1_exit_date"])
        & (df["te1_exit_date"] < df["t_e"])
    )
    df["valid_hardcap_window"] = (
        df["hardcap_exit_date"].isna()
        | (
            df["hardcap_exit_date"].notna()
            & df["hardcap_exit_date"].lt(df["t_e"])
        )
    )
    df["invalid_te1_reason"] = np.select(
        [
            df["entry_date"].isna(),
            df["te1_exit_date"].isna(),
            df["t_e"].isna(),
            df["entry_date"] >= df["te1_exit_date"],
            df["te1_exit_date"] >= df["t_e"],
        ],
        [
            "missing_entry_date",
            "missing_te1_exit",
            "missing_scheduled_te",
            "entry_not_before_te1",
            "te1_not_strictly_before_te",
        ],
        default="",
    )

    # The terminal horizon is always the stored final legal session before
    # t_e.  There is intentionally no fallback to t_e or to hardcap exit.
    df["te1_net_return_pct"] = df["stock_te1_net_return_pct"]
    df["te1_active_net_return_pct"] = np.nan
    df["te1_active_gross_return_pct"] = np.nan
    for benchmark in df["benchmark"].dropna().unique():
        mask = df["benchmark"].eq(benchmark)
        active_net_col = _active_te1_column(str(benchmark))
        active_gross_col = f"te1_active_vs_{str(benchmark).lower()}_gross_pct"
        df.loc[mask, "te1_active_net_return_pct"] = df.loc[mask, active_net_col]
        df.loc[mask, "te1_active_gross_return_pct"] = df.loc[mask, active_gross_col]
    df["slot_days"] = (df["te1_exit_date"] - df["entry_date"]).dt.days
    df["active_return_per_slot_day_pct"] = (
        df["te1_active_net_return_pct"] / df["slot_days"].replace(0, np.nan)
    )

    # Cross-sectional information is computed only within the observed daily
    # opportunity set.  It is marked as daily-resolution research because the
    # historical artifacts do not prove exact same-session ordering.
    groups = ["benchmark", "entry_date"]
    df["same_day_candidate_count"] = df.groupby(groups)["symbol"].transform("size")
    df["same_day_earnings_count"] = df.groupby(groups)["event_family"].transform(
        lambda s: s.astype(str).str.lower().eq("earnings").sum()
    )
    df["same_day_geo_count"] = df.groupby(groups)["event_family"].transform(
        lambda s: s.astype(str).str.lower().eq("geo").sum()
    )
    df["connection_rank_pct"] = df.groupby(groups, group_keys=False)["feat_connection_strength"].transform(
        lambda s: _rank_pct(s, ascending=False)
    )
    df["entry_prob_rank_pct"] = df.groupby(groups, group_keys=False)["entry_prob"].transform(
        lambda s: _rank_pct(s, ascending=False)
    )
    df["runup_rank_pct"] = df.groupby(groups, group_keys=False)["feat_runup_since_t0"].transform(
        lambda s: _rank_pct(s, ascending=True)
    )
    df["recent_5d_candidate_count"] = 0
    for _, group in df.groupby("benchmark", sort=False):
        dates = group["entry_date"]
        counts = [
            int(((dates < date) & (dates >= date - pd.Timedelta(days=5))).sum())
            for date in dates
        ]
        df.loc[group.index, "recent_5d_candidate_count"] = counts

    valid = df[df["valid_te1_window"]].copy()
    counts = {
        "raw_rows": int(len(raw)),
        "valid_te1_rows": int(len(valid)),
        "invalid_te1_rows": int((~df["valid_te1_window"]).sum()),
        "invalid_hardcap_rows": int((~df["valid_hardcap_window"]).sum()),
        "duplicate_rows_checked": duplicate_rows,
    }
    return valid, counts


def _build_competition_pairs(
    candidates: pd.DataFrame,
    capacity_summary: pd.DataFrame | None,
) -> pd.DataFrame:
    df = candidates.copy()
    if "selected_by_portfolio" in df.columns:
        df["selected"] = df["selected_by_portfolio"].map(_as_bool)
    else:
        df["selected"] = False
    if capacity_summary is not None and not capacity_summary.empty:
        cap = capacity_summary.copy()
        _parse_dates(cap)
        cap = cap.rename(columns={"split": "analysis_split"})
        key = ["benchmark", "analysis_split", "entry_date"]
        keep = key + [
            "eligible",
            "selected",
            "free_slots_before",
            "contested",
            "same_day_choice_exists",
        ]
        keep = [c for c in keep if c in cap.columns]
        cap = cap[keep].drop_duplicates(key)
        df = df.merge(cap, on=["benchmark", "analysis_split", "entry_date"], how="left", suffixes=("", "_capacity"))
    else:
        df["same_day_choice_exists"] = False

    df["genuine_same_day_competition"] = (
        df["same_day_choice_exists"].fillna(False).astype(bool)
        & df.groupby(["benchmark", "analysis_split", "entry_date"])["selected"].transform("any")
        & (~df["selected"])
    )
    pairs: list[dict[str, Any]] = []
    group_cols = ["benchmark", "analysis_split", "entry_date"]
    for group_key, day in df.groupby(group_cols, sort=True):
        if not bool(day["same_day_choice_exists"].fillna(False).any()):
            continue
        selected = day[day["selected"]]
        missed = day[~day["selected"]]
        if selected.empty or missed.empty:
            continue
        for _, left in selected.iterrows():
            for _, right in missed.iterrows():
                row: dict[str, Any] = dict(zip(group_cols, group_key))
                row.update(
                    {
                        "competition_reason": "same_day_capacity_conflict",
                        "left_symbol": left["symbol"],
                        "right_symbol": right["symbol"],
                        "left_selected": True,
                        "right_selected": False,
                        "left_connection_strength": left["feat_connection_strength"],
                        "right_connection_strength": right["feat_connection_strength"],
                        "left_entry_prob": left["entry_prob"],
                        "right_entry_prob": right["entry_prob"],
                        "left_slot_days": left["slot_days"],
                        "right_slot_days": right["slot_days"],
                        "left_te1_active_net_pct": left["te1_active_net_return_pct"],
                        "right_te1_active_net_pct": right["te1_active_net_return_pct"],
                        "left_active_efficiency_pct": left["active_return_per_slot_day_pct"],
                        "right_active_efficiency_pct": right["active_return_per_slot_day_pct"],
                        "same_day_candidate_count": left["same_day_candidate_count"],
                        "connection_diff": left["feat_connection_strength"] - right["feat_connection_strength"],
                        "entry_prob_diff": left["entry_prob"] - right["entry_prob"],
                        "slot_days_diff": left["slot_days"] - right["slot_days"],
                        "active_return_diff_pct": left["te1_active_net_return_pct"] - right["te1_active_net_return_pct"],
                        "efficiency_diff_pct": left["active_return_per_slot_day_pct"] - right["active_return_per_slot_day_pct"],
                    }
                )
                for feature in EX_ANTE_FEATURES:
                    left_value = pd.to_numeric(left.get(feature), errors="coerce")
                    right_value = pd.to_numeric(right.get(feature), errors="coerce")
                    row[f"left_{feature}"] = left_value
                    row[f"right_{feature}"] = right_value
                    # Orient the difference in the same direction as the
                    # target: positive means the right candidate has more of
                    # the feature than the left candidate.
                    row[f"diff_{feature}"] = right_value - left_value
                for feature in ("connection_rank_pct", "entry_prob_rank_pct", "runup_rank_pct"):
                    left_value = pd.to_numeric(left.get(feature), errors="coerce")
                    right_value = pd.to_numeric(right.get(feature), errors="coerce")
                    row[f"left_{feature}"] = left_value
                    row[f"right_{feature}"] = right_value
                    row[f"diff_{feature}"] = right_value - left_value
                for feature in ("event_family", "feat_sector", "t_e"):
                    row[f"left_{feature}"] = left.get(feature)
                    row[f"right_{feature}"] = right.get(feature)
                row["right_beats_left_active"] = int(
                    right["te1_active_net_return_pct"] > left["te1_active_net_return_pct"]
                )
                row["right_beats_left_efficiency"] = int(
                    right["active_return_per_slot_day_pct"] > left["active_return_per_slot_day_pct"]
                )
                pairs.append(row)
    return pd.DataFrame(pairs)


def build_stage1_datasets(
    input_path: Path | str = DEFAULT_INPUT,
    output_dir: Path | str = DEFAULT_OUTPUT,
    capacity_summary_path: Path | str | None = None,
) -> dict[str, Path]:
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw = pd.read_csv(input_path)
    candidates, counts = _prepare_candidates(raw)

    capacity_summary = None
    if capacity_summary_path is None:
        candidate_path = input_path.parent / "same_day_capacity_summary.csv"
        if candidate_path.exists():
            capacity_summary_path = candidate_path
    if capacity_summary_path is not None and Path(capacity_summary_path).exists():
        capacity_summary = pd.read_csv(capacity_summary_path)
    pairs = _build_competition_pairs(candidates, capacity_summary)

    forbidden = set(EX_ANTE_FEATURES) & OUTCOME_COLUMNS
    if forbidden:
        raise AssertionError(f"Outcome columns leaked into ex-ante features: {sorted(forbidden)}")

    candidate_columns = []
    for col in (
        list(METADATA_COLUMNS)
        + list(EX_ANTE_FEATURES)
        + list(CATEGORICAL_FEATURES)
        + [
            "same_day_candidate_count",
            "same_day_earnings_count",
            "same_day_geo_count",
            "recent_5d_candidate_count",
            "connection_rank_pct",
            "entry_prob_rank_pct",
            "runup_rank_pct",
            "t_e",
            "te1_exit_date",
            "valid_te1_window",
            "valid_hardcap_window",
        ]
        + list(TARGET_COLUMNS)
    ):
        if col in candidates.columns and col not in candidate_columns:
            candidate_columns.append(col)
    candidate_path = output_dir / "decision_candidates.csv"
    all_path = output_dir / "decision_candidates_all.csv"
    pairs_path = output_dir / "competition_pairs.csv"
    candidates[candidate_columns].to_csv(candidate_path, index=False)
    # Keep the audit fields and invalid rows separate from the training table.
    all_audit = raw.copy()
    _parse_dates(all_audit)
    all_audit.to_csv(all_path, index=False)
    pairs.to_csv(pairs_path, index=False)

    manifest = {
        "input_path": str(input_path),
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "output_dir": str(output_dir),
        "terminal_horizon": "te1_exit_date_dt, strictly before t_e",
        "te_is_never_exit": True,
        "decision_time_known": False,
        "timestamp_resolution": "daily",
        "supporting_market_features_included": False,
        "raw_feature_columns": list(EX_ANTE_FEATURES),
        "categorical_features": list(CATEGORICAL_FEATURES),
        "outcome_columns": list(TARGET_COLUMNS),
        "counts": counts,
        "competition_pair_rows": int(len(pairs)),
        "competition_source": "same_day_capacity_summary when same_day_choice_exists is true",
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return {
        "candidates": candidate_path,
        "all_audit": all_path,
        "pairs": pairs_path,
        "manifest": manifest_path,
    }


if __name__ == "__main__":
    paths = build_stage1_datasets()
    for name, path in paths.items():
        print(f"{name}: {path}")
