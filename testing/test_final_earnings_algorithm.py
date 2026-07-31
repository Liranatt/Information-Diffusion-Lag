from __future__ import annotations

import numpy as np
import pandas as pd

from selection.final_earnings_algorithm import BENCHMARK, ENTRY_THRESHOLD, score_and_rank
from selection.stage2c_research import QUALITY_FEATURES


def test_final_algorithm_is_qqq_only_and_ranks_positive_scores() -> None:
    model = {
        "intercept": 0.1,
        "coefficients": [0.0] * len(QUALITY_FEATURES),
        "medians": [0.0] * len(QUALITY_FEATURES),
        "means": [0.0] * len(QUALITY_FEATURES),
        "scales": [1.0] * len(QUALITY_FEATURES),
    }
    base = {feature: 0.0 for feature in QUALITY_FEATURES}
    frame = pd.DataFrame([
        {**base, "symbol": "GOOD", "benchmark": BENCHMARK, "mapping_type": "direct_issuer", "mapping_valid": True, "effective_probability": ENTRY_THRESHOLD, "feat_runup_since_t0": 0.05, "expected_slot_days": 4.0, "source_order": 2},
        {**base, "symbol": "SPY_ONLY", "benchmark": "SPY", "mapping_type": "direct_issuer", "mapping_valid": True, "effective_probability": ENTRY_THRESHOLD, "feat_runup_since_t0": 0.05, "expected_slot_days": 1.0, "source_order": 1},
    ])
    ranked = score_and_rank(frame, model)
    assert ranked.iloc[0]["symbol"] == "GOOD"
    assert bool(ranked.iloc[0]["admit"])
    assert not bool(ranked.loc[ranked["symbol"].eq("SPY_ONLY"), "admit"].iloc[0])
