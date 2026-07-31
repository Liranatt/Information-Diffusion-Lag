"""Unit tests for the timestamp ordering of Stage 3B's one new decision."""

from __future__ import annotations

import unittest

import pandas as pd

from selection.stage3b_relative_confirmation import _confirmation_exit


def _bar(day: str, opening: float, high: float, low: float, close: float) -> dict:
    return {"date": pd.Timestamp(day, tz="UTC"), "open": opening, "high": high, "low": low, "close": close}


class RelativeConfirmationTimingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.probabilities = pd.DataFrame({
            "source_ts_utc": pd.to_datetime(["2025-01-01 00:00:00+00:00"]),
            "probability_yes": [0.80],
        })
        self.benchmark = [
            _bar("2025-01-02", 100.0, 101.0, 99.0, 100.0),
            _bar("2025-01-03", 100.0, 101.0, 99.0, 100.0),
            _bar("2025-01-06", 100.0, 101.0, 99.0, 100.0),
        ]

    def test_failure_uses_only_prior_close_and_exits_next_open(self) -> None:
        path = [
            _bar("2025-01-02", 100.0, 101.0, 99.0, 100.0),
            _bar("2025-01-03", 99.0, 100.0, 98.0, 99.0),
            _bar("2025-01-06", 97.0, 98.0, 96.0, 97.0),
        ]
        plan, audit = _confirmation_exit(path, self.benchmark, 100.0, 0.10, self.probabilities, 1)
        self.assertEqual(plan["exit_reason"], "relative_follow_through_fail_next_open")
        self.assertEqual(plan["exit_date"], pd.Timestamp("2025-01-06", tz="UTC"))
        self.assertEqual(plan["exit_price"], 97.0)
        self.assertTrue(audit["confirmation_observed"])
        self.assertFalse(audit["confirmation_passed"])
        self.assertEqual(audit["post_confirmation_observations_used"], 0)
        self.assertEqual(audit["confirmation_decision_timestamp_utc"], pd.Timestamp("2025-01-06 14:30:00+00:00"))

    def test_pass_does_not_exit_on_confirmation_rule(self) -> None:
        path = [
            _bar("2025-01-02", 100.0, 101.0, 99.0, 100.0),
            _bar("2025-01-03", 101.0, 103.0, 100.0, 102.0),
            _bar("2025-01-06", 102.0, 103.0, 101.0, 102.0),
        ]
        plan, audit = _confirmation_exit(path, self.benchmark, 100.0, 0.10, self.probabilities, 1)
        self.assertNotEqual(plan["exit_reason"], "relative_follow_through_fail_next_open")
        self.assertTrue(audit["confirmation_observed"])
        self.assertTrue(audit["confirmation_passed"])


if __name__ == "__main__":
    unittest.main()
