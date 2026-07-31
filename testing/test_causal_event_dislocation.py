from __future__ import annotations

import unittest

import pandas as pd

from selection.causal_event_dislocation import CEDEConfig, cluster_event_legs, exit_decision, risk_size, score_and_admit


def _legs() -> pd.DataFrame:
    decision = pd.Timestamp("2026-07-17 13:30:00+00:00")
    return pd.DataFrame([
        {
            "economic_event_id": "iran_oil_2026_07", "family": "geopolitics", "asset": "USO", "hedge": "SPY",
            "expected_direction": 1, "aligned_probability": 0.72, "delta_logit": 0.80, "weight": 1.0,
            "mapping_confidence": 0.95, "business_days_to_event_end": 7, "asset_return_1d": 0.002,
            "asset_return_2d": 0.001, "hedge_return_1d": 0.001, "hedge_return_2d": 0.002,
            "beta_60": 0.5, "rv20_pct": 0.02, "delta_logit_mad_24h": 0.20,
            "family_delta_logit_q80": 0.50, "family_dislocation_q80": 2.0,
            "family_signed_ar2_q60": 0.015, "available_at_utc": decision - pd.Timedelta(minutes=1),
            "decision_ts_utc": decision,
        },
        # A paraphrased market on the same Iran/oil event must reinforce the
        # posterior but never become another position.
        {
            "economic_event_id": "iran_oil_2026_07", "family": "geopolitics", "asset": "USO", "hedge": "SPY",
            "expected_direction": 1, "aligned_probability": 0.74, "delta_logit": 0.70, "weight": 2.0,
            "mapping_confidence": 0.90, "business_days_to_event_end": 7, "asset_return_1d": 0.002,
            "asset_return_2d": 0.001, "hedge_return_1d": 0.001, "hedge_return_2d": 0.002,
            "beta_60": 0.5, "rv20_pct": 0.02, "delta_logit_mad_24h": 0.20,
            "family_delta_logit_q80": 0.50, "family_dislocation_q80": 2.0,
            "family_signed_ar2_q60": 0.015, "available_at_utc": decision - pd.Timedelta(minutes=2),
            "decision_ts_utc": decision,
        },
        # Post-decision data must be discarded, even if it looks attractive.
        {
            "economic_event_id": "late_market", "family": "macro", "asset": "TLT", "hedge": "SPY",
            "expected_direction": 1, "aligned_probability": 0.90, "delta_logit": 2.0, "weight": 1.0,
            "mapping_confidence": 0.99, "business_days_to_event_end": 7, "asset_return_1d": 0.0,
            "asset_return_2d": 0.0, "hedge_return_1d": 0.0, "hedge_return_2d": 0.0,
            "beta_60": 1.0, "rv20_pct": 0.02, "delta_logit_mad_24h": 0.20,
            "family_delta_logit_q80": 0.5, "family_dislocation_q80": 2.0,
            "family_signed_ar2_q60": 0.015, "available_at_utc": decision + pd.Timedelta(seconds=1),
            "decision_ts_utc": decision,
        },
    ])


class CausalEventDislocationTest(unittest.TestCase):
    def test_clustering_deduplicates_event_and_excludes_late_leg(self) -> None:
        candidates = cluster_event_legs(_legs())
        self.assertEqual(len(candidates), 1)
        candidate = candidates.iloc[0]
        self.assertEqual(candidate["economic_event_id"], "iran_oil_2026_07")
        self.assertEqual(candidate["event_leg_count"], 2)
        self.assertTrue(candidate["timestamp_safe"])
        self.assertGreater(candidate["dislocation"], 2.0)

    def test_meta_admission_and_volatility_size(self) -> None:
        candidates = cluster_event_legs(_legs())
        prediction = pd.DataFrame([{
            "economic_event_id": "iran_oil_2026_07", "probability_positive": 0.65,
            "expected_positive_return": 0.05, "probability_loss": 0.35,
            "expected_shortfall": 0.02, "all_in_rotation_cost": 0.001,
            "family_edge_score_q80": 0.02,
        }])
        admitted = score_and_admit(candidates, prediction)
        self.assertTrue(admitted.iloc[0]["entry_eligible"])
        self.assertGreater(risk_size(admitted.iloc[0]), 0.0)
        self.assertLessEqual(risk_size(admitted.iloc[0]), CEDEConfig().event_cap)

    def test_exit_requires_both_closed_dislocation_and_negative_active_return(self) -> None:
        base = pd.Series({
            "event_probability": 0.70, "entry_delta_logit": 0.80,
            "event_delta_logit": 0.70, "delta_logit_mad_24h": 0.20,
            "complete_sessions_after_entry": 2, "dislocation": -0.1,
            "active_abnormal_return": -0.01, "final_tradable_session": False,
        })
        self.assertEqual(exit_decision(base), "dislocation_closed_without_follow_through")
        positive_active = base.copy()
        positive_active["active_abnormal_return"] = 0.01
        self.assertIsNone(exit_decision(positive_active))
        invalidated = base.copy()
        invalidated["event_probability"] = 0.50
        self.assertEqual(exit_decision(invalidated), "event_probability_invalidation")


if __name__ == "__main__":
    unittest.main()
