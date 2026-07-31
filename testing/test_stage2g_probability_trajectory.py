from __future__ import annotations

import pandas as pd

from selection.stage2g_probability_trajectory import (
    _candidate_coverage,
    _coverage_decision,
    _fold_summary,
    _observation_audit,
)


def _row(entry: str = "2025-01-03") -> pd.Series:
    return pd.Series(
        {
            "stage2e_candidate_id": "candidate-1",
            "market_id": "market-1",
            "symbol": "XYZ",
            "benchmark": "SPY",
            "economic_event_id": "event-1",
            "event_family": "earnings",
            "entry_date": entry,
            "t0": "2025-01-01T16:00:00Z",
            "t_theta": "2025-01-02",
            "outer_fold": 0,
        }
    )


def test_candidate_audit_excludes_same_day_and_later_points() -> None:
    path = [
        (pd.Timestamp("2025-01-01", tz="UTC"), 0.50),
        (pd.Timestamp("2025-01-02", tz="UTC"), 0.60),
        (pd.Timestamp("2025-01-03", tz="UTC"), 0.70),
        (pd.Timestamp("2025-01-04", tz="UTC"), 0.80),
    ]

    audit = _candidate_coverage(_row(), path)

    assert audit["strict_pre_entry_observations"] == 2
    assert audit["same_entry_day_ambiguous_observations"] == 1
    assert audit["post_entry_day_observations_excluded"] == 1
    assert audit["post_entry_observations_used"] == 0
    assert not audit["has_slope_acceleration_point_count"]


def test_date_collapsed_source_and_entry_never_claim_exact_ordering() -> None:
    path = [(pd.Timestamp("2025-01-02", tz="UTC"), 0.60)]

    audit = _candidate_coverage(_row(), path)

    assert audit["all_source_times_date_normalized"]
    assert audit["entry_decision_time_is_date_normalized"]
    assert not audit["exact_source_availability_preserved"]
    assert not audit["exact_entry_decision_timestamp_preserved"]
    assert not audit["exact_pre_entry_ordering_verifiable"]


def test_every_observation_is_classified_and_none_is_used_after_gate_failure() -> None:
    path = [
        (pd.Timestamp("2025-01-02", tz="UTC"), 0.60),
        (pd.Timestamp("2025-01-03", tz="UTC"), 0.70),
        (pd.Timestamp("2025-01-04", tz="UTC"), 0.80),
    ]

    audit = _observation_audit(_row(), path)

    assert [point["temporal_class"] for point in audit] == [
        "strictly_before_normalized_entry_date",
        "same_entry_day_ambiguous_excluded",
        "post_entry_day_excluded",
    ]
    assert not any(point["used_in_trajectory_features"] for point in audit)


def test_coverage_gate_fails_when_exact_timestamps_are_unavailable() -> None:
    rows = []
    path = [
        (pd.Timestamp("2024-12-29", tz="UTC"), 0.40),
        (pd.Timestamp("2024-12-30", tz="UTC"), 0.50),
        (pd.Timestamp("2024-12-31", tz="UTC"), 0.60),
        (pd.Timestamp("2025-01-01", tz="UTC"), 0.70),
        (pd.Timestamp("2025-01-02", tz="UTC"), 0.80),
    ]
    for index in range(415):
        row = _row()
        row["stage2e_candidate_id"] = f"candidate-{index}"
        row["economic_event_id"] = f"event-{index}"
        row["outer_fold"] = index % 5
        rows.append(_candidate_coverage(row, path))
    audit = pd.DataFrame(rows)
    folds = _fold_summary(audit)

    gates, decision = _coverage_decision(audit, folds)

    exact_gate = gates.loc[gates["coverage_gate"].eq("exact_entry_and_source_ordering_verifiable")].iloc[0]
    assert not bool(exact_gate["passed"])
    assert decision["decision"] == "insufficient_data"
    assert not decision["models_fitted"]
    assert decision["post_entry_observations_used"] == 0
