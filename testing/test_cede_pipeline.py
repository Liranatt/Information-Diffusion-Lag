from __future__ import annotations

import numpy as np
import pandas as pd

from selection.cede_event_map import build_canonical_event_map, load_policy
from selection.cede_pipeline import _attach_expanding_thresholds, _probability_features, allocate_admitted


def _row(**overrides):
    value = {
        "economic_event_id": "evt-1",
        "event_family": "geo",
        "market_id": "market-1",
        "symbol": "USO",
        "question": "Will disruption occur?",
        "t0": "2025-01-01T00:00:00Z",
        "t_e": "2025-01-10T00:00:00Z",
        "decision_ts_utc": "2025-01-05T21:00:00Z",
        "polarity": 1,
        "mapping_valid": True,
        "mapping_confidence": 4,
        "mapping_type": "direct_underlying",
        "sector_etf": "SPY",
    }
    value.update(overrides)
    return value


def test_geo_components_become_one_event_exposure_and_one_market_vote():
    source = pd.DataFrame([
        _row(symbol="USO"),
        _row(symbol="XLE"),  # Explicitly not a second trade or basket component.
    ])
    event_map, legs, issues = build_canonical_event_map(source, load_policy())

    assert issues.empty
    assert len(event_map) == 1
    assert event_map.iloc[0]["component_count"] == 1
    assert event_map.iloc[0]["components_json"] == '["USO"]'
    assert len(legs) == 1
    assert legs.iloc[0]["weight"] == 1.0


def test_unknown_macro_event_is_rejected_until_predeclared():
    source = pd.DataFrame([
        _row(
            economic_event_id="tariff:new-event", event_family="macro", symbol="EWJ",
            mapping_confidence=1.0, mapping_type="predeclared_macro_basket_component",
        )
    ])
    event_map, legs, issues = build_canonical_event_map(source, load_policy())

    assert event_map.empty
    assert legs.empty
    assert issues.iloc[0]["reason"] == "macro_event_not_predeclared"


def test_probability_features_exclude_decision_and_later_observations():
    decision = pd.Timestamp("2025-01-05T21:00:00Z")
    timestamps = pd.date_range("2025-01-02T00:00:00Z", periods=80, freq="h")
    path = pd.DataFrame({
        "available_at_utc": list(timestamps) + [decision, decision + pd.Timedelta(hours=1)],
        "source_ts_utc": list(timestamps) + [decision, decision + pd.Timedelta(hours=1)],
        "probability_yes": np.linspace(0.45, 0.80, 82),
    })
    feature = _probability_features(pd.Series({
        "decision_ts_utc": decision, "t0": timestamps[0], "expected_direction": 1,
    }), path)

    assert feature["post_decision_observations_used"] == 0
    assert feature["path_last_available_at_utc"] < decision
    assert feature["strict_pre_entry_observations"] == 80
    assert np.isfinite(feature["delta_logit_mad_24h"])


def test_expanding_thresholds_do_not_use_simultaneous_events():
    rows = []
    for index, day in enumerate([1, 2, 3, 3]):
        rows.append({
            "trade_event_id": f"event-{index}", "family": "earnings",
            "decision_ts_utc": pd.Timestamp(f"2025-01-0{day}T21:00:00Z"),
            "event_delta_logit": float(index + 1), "dislocation": float(index + 1),
            "expected_direction": 1, "abnormal_return_2d": 0.01 * (index + 1),
        })
    result = _attach_expanding_thresholds(pd.DataFrame(rows), minimum_events=2)

    assert result.loc[result["trade_event_id"].eq("event-0"), "prior_family_events"].isna().all()
    assert result.loc[result["trade_event_id"].eq("event-1"), "prior_family_events"].isna().all()
    same_day = result[result["decision_ts_utc"].eq(pd.Timestamp("2025-01-03T21:00:00Z"))]
    assert same_day["prior_family_events"].eq(2).all()


def test_allocator_enforces_one_open_position_per_root_event():
    decisions = pd.DataFrame([
        {
            "trade_event_id": "root@2025-01-02", "root_event_id": "root", "family": "earnings",
            "decision_ts_utc": pd.Timestamp("2025-01-02T21:00:00Z"),
            "event_end_utc": pd.Timestamp("2025-01-10T21:00:00Z"), "entry_eligible": True,
            "rv20_pct": 0.02, "edge_score": 0.02, "family_edge_score_q80": 0.01,
            "components_json": '["AAA"]',
        },
        {
            "trade_event_id": "root@2025-01-03", "root_event_id": "root", "family": "earnings",
            "decision_ts_utc": pd.Timestamp("2025-01-03T21:00:00Z"),
            "event_end_utc": pd.Timestamp("2025-01-10T21:00:00Z"), "entry_eligible": True,
            "rv20_pct": 0.02, "edge_score": 0.02, "family_edge_score_q80": 0.01,
            "components_json": '["AAA"]',
        },
    ])
    allocated = allocate_admitted(decisions)

    assert allocated.iloc[0]["allocation_status"] == "allocated"
    assert allocated.iloc[1]["allocation_status"] == "rejected_root_event_already_open"
