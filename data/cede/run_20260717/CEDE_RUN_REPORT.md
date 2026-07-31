# CEDE run report

## Status

Research-only: no chronological OOF/live meta-prediction file was supplied, so no orders are admitted.

## Audit counts

- Raw source rows: 321
- Canonical event exposures: 292
- Canonical probability legs: 298
- Rejected mapping episodes: 2
- Probability-leg coverage passing policy: 52.3%
- Event candidates after aggregation: 292
- CEDE admissions: 0
- Allocated paper components: 0

## Safety invariants

- Every probability observation is strictly earlier than its decision timestamp.
- Price features use sessions strictly before the decision session.
- Probability questions aggregate to one event; component rows are execution legs, not independent votes.
- Family thresholds are expanding and exclude the current simultaneous decision session.
- Admission requires a separately supplied chronological meta-prediction file; the engine never creates expected return from realized outcomes.

## Fixed policy

- Min pre-entry history: 30 observations, 24 hours, latest update <= 180 minutes old.
- Family calibration: at least 20 prior event decisions.
- Stop: 2.5 ATR catastrophe stop; no take-profit target. Exit decisions use probability invalidation/reversal, failed two-session follow-through, or final event session.
