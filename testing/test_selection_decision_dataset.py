from __future__ import annotations

import pandas as pd

from selection.decision_dataset import EX_ANTE_FEATURES, _build_competition_pairs, _prepare_candidates


def _row(**overrides):
    row = {
        "benchmark": "SPY",
        "analysis_split": "test",
        "entry_date": "2026-01-05",
        "t_e": "2026-01-10T12:00:00Z",
        "te1_exit_date_dt": "2026-01-09",
        "hardcap_exit_date": "2026-01-08",
        "symbol": "AAA",
        "feat_archetype": "earnings",
        "feat_sector": "Technology",
        "event_family": "earnings",
        "feat_connection_strength": 1.0,
        "connection_strength": 1.0,
        "entry_prob": 0.85,
        "stock_te1_gross_return_pct": 3.0,
        "stock_te1_net_return_pct": 2.8,
        "te1_active_vs_spy_gross_pct": 1.0,
        "te1_active_vs_spy_net_twin_pct": 0.8,
        "candidate_rows": 1,
        "market_count": 1,
    }
    for col in EX_ANTE_FEATURES:
        row.setdefault(col, 0.0)
    row.update(overrides)
    return row


def test_te_is_never_used_as_exit_and_te1_is_strictly_before_te():
    raw = pd.DataFrame([_row(), _row(symbol="BBB", te1_exit_date_dt="2026-01-10")])
    prepared, counts = _prepare_candidates(raw)
    assert len(prepared) == 1
    assert counts["invalid_te1_rows"] == 1
    row = prepared.iloc[0]
    assert row["te1_exit_date"] < row["t_e"]
    assert row["te1_exit_date"] != row["t_e"]


def test_ex_ante_feature_list_contains_no_outcome_or_exit_columns():
    forbidden = {"t_e", "te1_exit_date", "stock_te1_net_return_pct", "hardcap_exit_date", "selected_pnl"}
    assert not (set(EX_ANTE_FEATURES) & forbidden)


def test_competition_pairs_only_come_from_declared_capacity_conflicts():
    raw = pd.DataFrame(
        [
            _row(symbol="AAA"),
            _row(symbol="BBB", feat_connection_strength=0.8, entry_prob=0.8, te1_active_vs_spy_net_twin_pct=-1.0),
        ]
    )
    prepared, _ = _prepare_candidates(raw)
    prepared["selected_by_portfolio"] = [True, False]
    cap = pd.DataFrame(
        [
            {
                "benchmark": "SPY",
                "split": "test",
                "entry_date": "2026-01-05",
                "eligible": 2,
                "selected": 1,
                "free_slots_before": 0,
                "contested": True,
                "same_day_choice_exists": True,
            }
        ]
    )
    pairs = _build_competition_pairs(prepared, cap)
    assert len(pairs) == 1
    assert pairs.iloc[0]["competition_reason"] == "same_day_capacity_conflict"
    assert pairs.iloc[0]["left_symbol"] == "AAA"
    assert pairs.iloc[0]["right_symbol"] == "BBB"
