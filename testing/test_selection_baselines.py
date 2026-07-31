import pandas as pd

from selection.baselines import PairwiseLogisticRanker, _apply_selection


def test_pairwise_ranker_orients_right_candidate_as_the_positive_direction():
    features = ("feat_connection_strength", "entry_prob")
    pairs = pd.DataFrame(
        {
            "analysis_split": ["train", "train", "train", "train"],
            "diff_feat_connection_strength": [1.0, -1.0, 0.0, 0.0],
            "diff_entry_prob": [0.0, 0.0, 1.0, -1.0],
            "right_beats_left_active": [1, 0, 1, 0],
        }
    )
    ranker = PairwiseLogisticRanker(features=features).fit(pairs)
    candidates = pd.DataFrame(
        {
            "feat_connection_strength": [1.0, 0.0],
            "entry_prob": [0.0, 0.0],
        }
    )
    scores = ranker.score(candidates)
    assert scores.iloc[0] > scores.iloc[1]


def test_selection_respects_known_daily_capacity():
    candidates = pd.DataFrame(
        {
            "benchmark": ["SPY", "SPY", "SPY"],
            "analysis_split": ["train"] * 3,
            "entry_date": pd.to_datetime(["2025-01-02"] * 3, utc=True),
            "symbol": ["A", "B", "C"],
            "entry_prob": [0.8, 0.9, 0.9],
            "capacity_known": [True] * 3,
            "capacity_slots": [1] * 3,
            "score": [0.1, 0.9, 0.9],
        }
    )
    _apply_selection(candidates, "score", "test")
    chosen = candidates.loc[candidates["selected_test"], "symbol"].tolist()
    assert chosen == ["B"]


def test_missing_capacity_selects_no_candidate():
    candidates = pd.DataFrame(
        {
            "benchmark": ["SPY"],
            "analysis_split": ["train"],
            "entry_date": pd.to_datetime(["2025-01-02"], utc=True),
            "symbol": ["A"],
            "entry_prob": [0.8],
            "capacity_known": [False],
            "capacity_slots": [0],
            "score": [1.0],
        }
    )
    _apply_selection(candidates, "score", "test")
    assert not bool(candidates.loc[0, "selected_test"])
