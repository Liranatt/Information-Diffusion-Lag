from __future__ import annotations

import pandas as pd
import pytest

from selection.stage2f_family_selection import (
    _build_oracle_labels,
    _candidate_net_path,
    _chronological_event_folds,
    _family_supported,
    _oracle_from_path,
)


def _summary(candidate_id: str = "candidate-1") -> pd.Series:
    summary = pd.Series({
        "benchmark": "SPY",
        "symbol": "AAA",
        "event_family": "earnings",
        "mapping_type": "direct_issuer",
        "actual_entry_price": 100.0,
    })
    summary.name = candidate_id
    return summary


def test_full_path_oracle_selects_best_legal_day_after_costs() -> None:
    dates = pd.date_range("2025-01-02", periods=4, freq="B", tz="UTC")
    path = pd.DataFrame({
        "legal_holding_day": [1, 2, 3],
        "path_date": dates[:3],
        "candidate_t_e": dates[3],
        "stock_close": [100.0, 104.0, 101.0],
        "active_return_pct": [0.0, 4.0, 1.0],
        "benchmark_close": [100.0, 100.0, 100.0],
        "benchmark_return_pct": [0.0, 0.0, 0.0],
    })
    net_path = _candidate_net_path(path, _summary())
    oracle = _oracle_from_path(net_path, _summary())

    assert (net_path["path_date"] < net_path["candidate_t_e"]).all()
    assert oracle["day_of_best_legal_return"] == 2
    assert oracle["reaches_2pct_active_net"] is True
    assert oracle["oracle_label_role"] == "ex_post_research_only_never_live_feature"


def test_oracle_builder_rejects_te_as_a_path_date(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import selection.stage2f_family_selection as stage2f

    t_e = pd.Timestamp("2025-01-06", tz="UTC")
    paths = pd.DataFrame({
        "stage2e_candidate_id": ["candidate-1"],
        "entry_date": [pd.Timestamp("2025-01-02", tz="UTC")],
        "candidate_t_e": [t_e],
        "path_date": [t_e],
        "legal_holding_day": [1],
        "stock_close": [101.0],
        "active_return_pct": [1.0],
        "benchmark_close": [100.0],
        "benchmark_return_pct": [0.0],
    })
    summaries = pd.DataFrame({
        "stage2e_candidate_id": ["candidate-1"],
        "benchmark": ["SPY"],
        "symbol": ["AAA"],
        "event_family": ["earnings"],
        "mapping_type": ["direct_issuer"],
        "actual_entry_price": [100.0],
    })
    path_file = tmp_path / "paths.csv"
    summary_file = tmp_path / "summaries.csv"
    paths.to_csv(path_file, index=False)
    summaries.to_csv(summary_file, index=False)
    monkeypatch.setattr(stage2f, "PATH_TABLE", path_file)
    monkeypatch.setattr(stage2f, "PATH_SUMMARY", summary_file)

    with pytest.raises(AssertionError, match="T_e is never an exit"):
        _build_oracle_labels(tmp_path / "output")


def test_chronological_folds_keep_event_episodes_together() -> None:
    rows = []
    dates = pd.date_range("2025-01-02", periods=18, freq="B", tz="UTC")
    for event_number, date in enumerate(dates):
        for symbol_number in range(2):
            rows.append({
                "economic_event_id": f"event-{event_number}",
                "entry_date": date,
                "event_family": "earnings" if event_number >= 5 else "geo",
                "symbol": f"S{event_number}-{symbol_number}",
            })
    frame = pd.DataFrame(rows)
    folds = _chronological_event_folds(frame, n_splits=3, min_train_fraction=0.20)

    assert len(folds) == 3
    validation_events: set[str] = set()
    for train_mask, validation_mask, _ in folds:
        train = frame.loc[train_mask]
        validation = frame.loc[validation_mask]
        train_events = set(train["economic_event_id"])
        fold_validation_events = set(validation["economic_event_id"])
        assert train_events.isdisjoint(fold_validation_events)
        assert validation_events.isdisjoint(fold_validation_events)
        assert train["entry_date"].max() < validation["entry_date"].min()
        assert validation.groupby("economic_event_id").size().eq(2).all()
        validation_events.update(fold_validation_events)


def test_sparse_families_are_not_fitted() -> None:
    sparse = pd.DataFrame({
        "economic_event_id": [f"event-{index}" for index in range(10)],
        "reaches_2pct_active_net": [index % 2 for index in range(10)],
        "never_profitable_after_costs": [(index + 1) % 2 for index in range(10)],
        "persistent_loser": [index % 2 for index in range(10)],
        "severe_adverse_before_meaningful_gain": [(index + 1) % 2 for index in range(10)],
    })
    supported, support = _family_supported(sparse)

    assert supported is False
    assert support["independent_events"] == 10
