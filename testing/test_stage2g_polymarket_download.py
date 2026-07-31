from __future__ import annotations

import pandas as pd

from analysis.download_stage2g_polymarket_history import decision_timestamp


def test_decision_timestamp_uses_nyse_dst_close() -> None:
    assert decision_timestamp("2025-10-20") == pd.Timestamp("2025-10-20T20:00:00Z")
    assert decision_timestamp("2025-11-10") == pd.Timestamp("2025-11-10T21:00:00Z")


def test_decision_timestamp_handles_2025_thanksgiving_early_close() -> None:
    assert decision_timestamp("2025-11-28") == pd.Timestamp("2025-11-28T18:00:00Z")
