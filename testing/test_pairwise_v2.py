import numpy as np
import pandas as pd

from selection.connection_tiebreakers import _prepare, _select
from selection.pairwise_v2 import (
    MissingnessAugmenter,
    TrainWinsorizer,
    V2_FEATURES,
    _add_pair_metadata,
    _make_pipeline,
    _pair_features,
    _pair_weights,
)


def test_pairwise_v2_excludes_surge_and_uses_only_compact_features():
    assert "feat_prob_surge_since_t0" not in V2_FEATURES
    assert "symbol" not in V2_FEATURES
    assert "event_family" not in V2_FEATURES


def test_winsorizer_clips_extreme_value_using_fit_rows_only():
    transformer = TrainWinsorizer(lower_quantile=0.0, upper_quantile=0.5)
    transformer.fit(pd.DataFrame({"x": [0.0, 1.0, 2.0]}))
    result = transformer.transform(pd.DataFrame({"x": [100.0]}))
    assert float(result.loc[0, "x"]) == 1.0


def test_missingness_augmenter_adds_indicators_for_every_feature():
    transformer = MissingnessAugmenter().fit(pd.DataFrame({"x": [1.0], "y": [2.0]}))
    result = transformer.transform(pd.DataFrame({"x": [np.nan], "y": [2.0]}))
    assert list(result.columns) == ["x", "y", "missing_x", "missing_y"]
    assert result.loc[0, "missing_x"] == 1.0
    assert result.loc[0, "missing_y"] == 0.0


def test_v2_pair_features_are_right_minus_left_and_probability_is_right_wins():
    pairs = pd.DataFrame(
        {
            "diff_feat_connection_strength": [1.0, -1.0] * 4,
            "diff_entry_prob": [1.0, -1.0] * 4,
            "diff_connection_rank_pct": [1.0, -1.0] * 4,
            "diff_feat_time_to_resolution_days": [0.0] * 8,
            "diff_feat_asset_2w_trend": [0.0] * 8,
            "diff_feat_sector_1m_trend": [0.0] * 8,
            "right_beats_left_active": [1, 0] * 4,
        }
    )
    features = _pair_features(pairs)
    assert features.loc[0, "feat_connection_strength"] == 1.0
    assert features.loc[1, "feat_connection_strength"] == -1.0

    model = _make_pipeline(0.1)
    model.fit(features, pairs["right_beats_left_active"], model__sample_weight=np.ones(len(pairs)))
    positive = pd.DataFrame([{feature: 1.0 for feature in V2_FEATURES}])
    negative = pd.DataFrame([{feature: -1.0 for feature in V2_FEATURES}])
    assert model.predict_proba(positive)[0, 1] > model.predict_proba(negative)[0, 1]
    assert float(model.decision_function(positive)[0]) > float(model.decision_function(negative)[0])


def test_pair_weights_give_each_decision_group_unit_mass():
    pairs = pd.DataFrame(
        {
            "benchmark": ["SPY"] * 4,
            "entry_date": ["2025-01-01"] * 2 + ["2025-01-02"] * 2,
            "analysis_split": ["train"] * 4,
            "left_symbol": ["A", "A", "B", "B"],
            "left_event_family": ["earnings"] * 4,
            "right_event_family": ["earnings"] * 4,
            "same_day_candidate_count": [3] * 4,
            "right_beats_left_active": [1, 0, 1, 0],
        }
    )
    prepared = _add_pair_metadata(pairs)
    prepared["sample_weight"] = _pair_weights(prepared)
    totals = prepared.groupby("decision_group")["sample_weight"].sum()
    assert np.allclose(totals.to_numpy(), 1.0)


def test_connection_tie_breaker_uses_explicit_entry_probability():
    candidates = pd.DataFrame(
        {
            "benchmark": ["SPY", "SPY"],
            "analysis_split": ["test", "test"],
            "entry_date": pd.to_datetime(["2025-01-01"] * 2, utc=True),
            "symbol": ["A", "B"],
            "feat_connection_strength": [1.0, 1.0],
            "entry_prob": [0.7, 0.9],
            "expected_slot_days": [5.0, 5.0],
            "sector_relative_extension": [0.0, 0.0],
            "source_order": [0, 1],
            "symbol_independent_hash": [10, 20],
            "capacity_known": [True, True],
            "capacity_slots": [1, 1],
        }
    )
    selected = _select(candidates, "entry_probability")
    assert candidates.loc[selected, "symbol"].tolist() == ["B"]


def test_connection_selector_is_invariant_to_row_order_and_serialization(tmp_path):
    candidates = pd.DataFrame(
        {
            "benchmark": ["SPY", "SPY", "SPY"],
            "analysis_split": ["test"] * 3,
            "entry_date": pd.to_datetime(["2025-01-01"] * 3, utc=True),
            "symbol": ["Z", "A", "M"],
            "feat_connection_strength": [1.0, 1.0, 1.0],
            "entry_prob": [0.8, 0.8, 0.8],
            "feat_time_to_resolution_days": [5.0, 5.0, 5.0],
            "feat_asset_2w_trend": [0.0, 0.0, 0.0],
            "feat_sector_1m_trend": [0.0, 0.0, 0.0],
            "capacity_known": [True] * 3,
            "capacity_slots": [1] * 3,
        }
    )
    first = _prepare(candidates)
    first_selected = first.loc[_select(first, "entry_probability"), "symbol"].tolist()

    shuffled = candidates.sample(frac=1.0, random_state=17).reset_index(drop=True)
    csv_path = tmp_path / "candidates.csv"
    shuffled.to_csv(csv_path, index=False)
    reloaded = _prepare(pd.read_csv(csv_path))
    reloaded_selected = reloaded.loc[_select(reloaded, "entry_probability"), "symbol"].tolist()

    assert first_selected == reloaded_selected
