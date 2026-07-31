from __future__ import annotations

import pandas as pd
import pytest

from core.candidate_cleaning import clean_candidates


def _candidate(market_id: str, symbol: str, question: str, event_id: str) -> dict:
    return {
        "market_id": market_id,
        "symbol": symbol,
        "question": question,
        "event_id": event_id,
        "t_theta": pd.Timestamp("2026-01-01", tz="UTC"),
        "t_e": pd.Timestamp("2026-01-10", tz="UTC"),
        "split": "train",
        "feat_connection_strength": 0.9,
    }


def _audit_row(
    market_id: str,
    symbol: str,
    question: str,
    verdict: str,
    action: str,
    event: str,
) -> dict:
    return {
        "market_id": market_id,
        "asset_symbol": symbol,
        "question": question,
        "treatment_verdict": verdict,
        "treatment_action": action,
        "question_structure": "test structure",
        "economic_event_group": event,
        "audit_group": "Test",
    }


def test_cleaning_deduplicates_gates_and_event_weights() -> None:
    first = _candidate("M1", "AAA", "primary one", "E1")
    candidates = pd.DataFrame(
        [
            first,
            first.copy(),
            _candidate("M2", "BBB", "invalid derivative", "E2"),
            _candidate("M3", "CCC", "process only", "E3"),
            _candidate("M4", "DDD", "needs transform", "E4"),
            _candidate("M5", "EEE", "unreviewed", "E5"),
            _candidate("M6", "FFF", "controlled primary", "E6"),
        ]
    )
    audit = pd.DataFrame(
        [
            _audit_row("M1", "AAA", "primary one", "Mostly correct", "primary", "Event alpha"),
            _audit_row(
                "M2", "BBB", "invalid derivative", "Exclude from primary", "exclude", "Event beta"
            ),
            _audit_row("M3", "CCC", "process only", "Secondary only", "secondary", "Event gamma"),
            _audit_row("M4", "DDD", "needs transform", "Needs redesign", "quarantine", "Event delta"),
            _audit_row(
                "M6",
                "FFF",
                "controlled primary",
                "Mostly correct after controls",
                "primary_controlled",
                "Event alpha",
            ),
        ]
    )

    result = clean_candidates(candidates, audit)

    assert result.summary["input_rows"] == 7
    assert result.summary["exact_duplicate_excess_rows_removed"] == 1
    assert len(result.annotated) == 6
    assert set(result.primary["market_id"]) == {"M1", "M5", "M6"}

    annotated = result.annotated.set_index("market_id")
    assert annotated.loc["M1", "source_duplicate_count"] == 2
    assert annotated.loc["M2", "cleaning_disposition"] == "exclude_from_primary"
    assert annotated.loc["M3", "secondary_signal_only"]
    assert annotated.loc["M4", "requires_semantic_redesign"]
    assert annotated.loc["M5", "treatment_action"] == "unreviewed_passthrough"

    alpha = result.annotated[result.annotated["market_id"].isin(["M1", "M6"])]
    assert alpha["economic_event_id"].nunique() == 1
    assert alpha["economic_event_weight"].sum() == pytest.approx(1.0)
    assert set(alpha["economic_event_weight"]) == {0.5}


def test_cleaning_rejects_nonidentical_duplicate_market_asset_keys() -> None:
    first = _candidate("M1", "AAA", "same key", "E1")
    second = first.copy()
    second["t_theta"] = pd.Timestamp("2026-01-02", tz="UTC")
    candidates = pd.DataFrame([first, second])
    audit = pd.DataFrame(
        [_audit_row("M1", "AAA", "same key", "Mostly correct", "primary", "Event alpha")]
    )

    with pytest.raises(ValueError, match="Non-identical candidate rows"):
        clean_candidates(candidates, audit)


def test_cleaning_rejects_question_drift_for_audited_key() -> None:
    candidates = pd.DataFrame([_candidate("M1", "AAA", "changed title", "E1")])
    audit = pd.DataFrame(
        [_audit_row("M1", "AAA", "frozen title", "Mostly correct", "primary", "Event alpha")]
    )

    with pytest.raises(ValueError, match="question text disagrees"):
        clean_candidates(candidates, audit)
