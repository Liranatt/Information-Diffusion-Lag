# Stage 2G — Pre-entry Probability-Trajectory Audit

## 1. Probability-trajectory coverage and leakage audit

The audit covers all **415** honest earnings OOF candidates across **5** chronological folds. The stored probability artifact contains one daily close per market/date, and its original intraday availability timestamp is replaced by `00:00:00 UTC`. Stage 2E also normalizes the entry decision to a calendar date. Therefore an entry-day probability point cannot be ordered against the exact decision, even though it appears at midnight.

The conservative audit admits only observations with stored dates strictly before the entry date. It excludes every same-day point and every later point; **0** post-entry observations were used.
The point-level audit classifies all **4429** candidate-linked stored observations; none entered a feature or model because the coverage gate failed.

- No strictly pre-entry history: **198/415**.
- Fewer than three points (slope plus acceleration unavailable): **319/415**.
- Fewer than five points (full trajectory poorly supported): **385/415**.
- Ambiguous same-entry-day observation: **415/415**.
- Exact pre-entry ordering verifiable: **0/415**.

Coverage by chronological fold:

| outer_fold | candidates | independent_events | strict_pre_entry_observations_median | no_strict_pre_entry_history_count | curvature_point_count_share | full_trajectory_point_count_share | same_day_timestamp_ambiguity_share | exact_pre_entry_ordering_verifiable_share |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 82 | 48 | 1.0000 | 33 | 0.2927 | 0.0122 | 1.0000 | 0.0000 |
| 1 | 96 | 56 | 0.0000 | 68 | 0.1979 | 0.0208 | 1.0000 | 0.0000 |
| 2 | 157 | 85 | 0.0000 | 87 | 0.1720 | 0.0382 | 1.0000 | 0.0000 |
| 3 | 58 | 30 | 1.0000 | 7 | 0.1724 | 0.0862 | 1.0000 | 0.0000 |
| 4 | 22 | 12 | 8.0000 | 3 | 0.7273 | 0.7273 | 1.0000 | 0.0000 |

Outcome-free coverage gates:

| coverage_gate | passed | observed | required |
| --- | --- | --- | --- |
| expected_earnings_oof_candidate_count | True | 415 | 415 |
| earnings_only_honest_oof_scope | True | True | True |
| exact_entry_and_source_ordering_verifiable | False | 0.0 | 1.0 |
| at_least_three_pre_entry_points | False | 0.23132530120481928 | 0.9 |
| at_least_five_pre_entry_points | False | 0.07228915662650602 | 0.8 |
| fold_level_three_point_coverage | False | 0.17197452229299362 | 0.8 |

The point-count gates are minimum engineering requirements, not tuned performance thresholds: three points are needed for slope plus acceleration, while five points provide a minimally useful path for persistence, crossings, peak/drawdown/recovery, volatility, and update-frequency summaries.

## 2. OOF prediction and same-day selection comparison

Not run. Model B and the trajectory-only diagnostic cannot be constructed timestamp-safely for the OOF universe. Target B remains a frozen reference; it was not re-estimated or selectively compared.

## 3. Selector × exit exact replay

Not run. Replaying a selector whose features cannot be constructed honestly would turn the coverage failure into a performance claim. Status rows are emitted for A/B/C × SPY/QQQ × all eight Stage 2E exits.

## 4. Paired fold comparison against Target B

Not run. All five fold rows are retained in the status artifact so the absence of paired estimates is explicit.

## 5. Final decision

**INSUFFICIENT DATA — the full pre-entry probability-trajectory hypothesis cannot be tested reliably.**

This is not evidence that trajectories fail, and it is not a promotion of Target B on new performance evidence. Target B remains the existing operational selector by default. Earnings selection research should not be declared closed on this untestable comparison; Stage 3 exit research may proceed with Target B while new point-in-time probability histories are collected.

Required remediation for a future test: retain each Polymarket observation's original `source_ts` and `available_at`, retain the exact equity decision timestamp, and collect enough pre-decision updates in every chronological fold. The live feature builder must enforce `available_at <= decision_ts`.
