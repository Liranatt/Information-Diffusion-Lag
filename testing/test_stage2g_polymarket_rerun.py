from __future__ import annotations

import pandas as pd

from selection.stage2g_polymarket_rerun import _trajectory_row


def test_trajectory_features_exclude_point_at_entry_cutoff() -> None:
    row = pd.Series(
        {
            "stage2e_candidate_id": "candidate",
            "entry_date": "2025-10-20",
            "t0": "2025-10-20T17:00:00Z",
            "question": "Will Example (XYZ) beat quarterly earnings?",
            "symbol": "XYZ",
            "benchmark": "SPY",
            "feat_sector": "Technology",
        }
    )
    path = pd.DataFrame(
        {
            "source_ts_utc": pd.to_datetime(
                ["2025-10-20T17:00:00Z", "2025-10-20T18:00:00Z", "2025-10-20T19:59:00Z", "2025-10-20T20:00:00Z"],
                utc=True,
            ),
            "probability_yes": [0.60, 0.72, 0.80, 0.10],
        }
    )
    prices = {
        "XYZ": [(pd.Timestamp("2025-10-16", tz="UTC"), 10, 11, 9, 10), (pd.Timestamp("2025-10-17", tz="UTC"), 11, 12, 10, 11)],
        "XLK": [(pd.Timestamp("2025-10-16", tz="UTC"), 10, 11, 9, 10), (pd.Timestamp("2025-10-17", tz="UTC"), 10, 11, 9, 10.5)],
        "SPY": [(pd.Timestamp("2025-10-16", tz="UTC"), 10, 11, 9, 10), (pd.Timestamp("2025-10-17", tz="UTC"), 10, 11, 9, 10.2)],
    }

    features = _trajectory_row(row, path, prices)

    assert features["strict_pre_entry_observations"] == 3
    assert features["traj_probability_latest"] == 0.80
    assert features["path_last_source_ts_utc"] < features["decision_ts_utc"]
    assert features["post_entry_observations_used"] == 0
