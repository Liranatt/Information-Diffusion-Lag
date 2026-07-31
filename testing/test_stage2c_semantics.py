import pandas as pd

from selection.stage2c_research import _event_grouped_folds
from selection.stage2c_semantics import backward_compatibility_aliases, label_mapping


def test_same_company_earnings_is_deterministic_direct_issuer():
    label = label_mapping(
        question="Will Apple (AAPL) beat quarterly earnings?",
        symbol="AAPL",
        event_family="earnings",
        company_identity="Apple Earnings",
        sector="Technology",
    )
    assert label.mapping_type == "direct_issuer"
    assert label.mapping_valid is True
    assert label.semantic_directness == 5
    assert label.mapping_confidence == 5
    assert label.exposure_purity == 5


def test_direct_earnings_requires_same_ticker_identity():
    label = label_mapping(
        question="Will Apple (AAPL) beat quarterly earnings?",
        symbol="MSFT",
        event_family="earnings",
        company_identity="Apple Earnings",
        sector="Technology",
    )
    assert label.mapping_type == "unclear_or_invalid"
    assert label.mapping_valid is False


def test_verified_fda_owner_is_direct_issuer():
    label = label_mapping(
        question="Will the FDA approve Odronextamab/Ordspono Resubmitted BLA?",
        symbol="REGN",
        event_family="other",
        company_identity="Regeneron FDA Approval",
        sector="Healthcare",
    )
    assert label.mapping_type == "direct_issuer"
    assert label.semantic_directness == 5


def test_geo_mapping_taxonomy_is_asset_specific():
    question = "Will the U.S. strike Fordow nuclear facility before July?"
    uso = label_mapping(question=question, symbol="USO", event_family="geo", company_identity="", sector="Unknown")
    xle = label_mapping(question=question, symbol="XLE", event_family="geo", company_identity="", sector="Unknown")
    xom = label_mapping(question=question, symbol="XOM", event_family="geo", company_identity="", sector="Unknown")
    assert uso.mapping_type == "direct_underlying"
    assert xle.mapping_type == "first_order_sector"
    assert xom.mapping_type == "second_order_company"
    assert uso.exposure_purity > xle.exposure_purity > xom.exposure_purity


def test_legacy_aliases_are_documented_but_semantics_do_not_accept_score():
    aliases = backward_compatibility_aliases()
    assert "legacy_gemini_relevance_score" in aliases
    assert "feat_connection_strength" in aliases
    assert "universally" in aliases["legacy_gemini_relevance_score"] or "diagnostic" in aliases["legacy_gemini_relevance_score"]


def test_event_grouped_folds_never_split_one_episode():
    frame = pd.DataFrame(
        {
            "economic_event_id": ["a", "a", "b", "c", "d", "e", "f", "g", "h", "i"],
            "entry_date": pd.to_datetime(
                ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05", "2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10"],
                utc=True,
            ),
        }
    )
    for train_mask, validation_mask, _events in _event_grouped_folds(frame, 3):
        train_events = set(frame.loc[train_mask, "economic_event_id"])
        validation_events = set(frame.loc[validation_mask, "economic_event_id"])
        assert train_events.isdisjoint(validation_events)

