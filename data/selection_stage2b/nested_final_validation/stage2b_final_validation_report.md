# Stage 2B Final Validation and Stage 3 Handoff

## Required corrections

- The previous intersection-based OOF replay has been replaced by full nested chronological outer evaluation on development/training rows. Inner folds select the tie-breaker and admission threshold; outer folds evaluate frozen choices.
- The later lockbox was not opened. Every nested artifact records `lockbox_opened=false` and `lockbox_rows_evaluated=0`.
- The factorial cells isolate ranking from admission: A/B compare source-order versus expected-slot-days with always-fill; C/D compare the same ranking choices with the 1.00 minimum connection threshold.
- Threshold stability is reported for exactly: no threshold, 0.90, 0.95, 0.98, and 1.00. SPY and QQQ are reported separately.
- Target C remains a diagnostic only. Target C output was not used for primary training or selector freezing; its policy-conditioned sign remains continuation-policy dependent. The retained diagnostic contains 428 continuation-policy/benchmark aggregates.
- The monotonic and pooled models were evaluated without verified probability-price disagreement or supporting-market snapshot features. Their underperformance does not reject those feature hypotheses; the hypotheses remain untested in this feature-complete form.
- All exact replay exits remain strictly before `T_e`; `T_e` is never an exit.

## Nested validation

Outer-fold date ranges and event-family composition:

| Fold | Validation range | Rows | Event-family composition |
| --- | --- | --- | --- |
| 0 | 2025-10-01 to 2025-10-13 | 84 | {'earnings': 84} |
| 1 | 2025-10-17 to 2025-10-29 | 125 | {'earnings': 125} |
| 2 | 2025-10-29 to 2025-11-12 | 154 | {'earnings': 154} |
| 3 | 2025-11-12 to 2025-12-01 | 59 | {'earnings': 59} |
| 4 | 2025-12-01 to 2025-12-11 | 24 | {'earnings': 24} |

Inner-fold choices:

| Outer fold | Tie-breaker | Threshold | Inner mean Target A |
| --- | --- | --- | --- |
| 0 | expected_slot_days | 0.9 | 3.450 |
| 1 | expected_slot_days | 0.95 | 1.431 |
| 2 | expected_slot_days | 1.0 | 0.005 |
| 3 | expected_slot_days | 1.0 | 0.175 |
| 4 | expected_slot_days | 1.0 | 0.102 |

Factorial ranking × admission results:

| Cell | Benchmark | Mean excess | Mean trades | Active IR | Active drawdown |
| --- | --- | --- | --- | --- | --- |
| A_connection_source_order_always_fill | QQQ | 0.587 | 24.600 | 0.598 | -2.205 |
| A_connection_source_order_always_fill | SPY | 0.218 | 25.600 | 0.985 | -1.879 |
| B_connection_expected_slot_days_always_fill | QQQ | 1.026 | 25.200 | 0.974 | -1.991 |
| B_connection_expected_slot_days_always_fill | SPY | 0.634 | 25.600 | 1.428 | -1.607 |
| C_connection_source_order_min_connection_1.00 | QQQ | 0.473 | 17.200 | 0.486 | -1.577 |
| C_connection_source_order_min_connection_1.00 | SPY | 0.709 | 19.800 | 1.648 | -1.205 |
| D_connection_expected_slot_days_min_connection_1.00 | QQQ | 0.762 | 17.200 | 0.714 | -1.371 |
| D_connection_expected_slot_days_min_connection_1.00 | SPY | 0.709 | 19.600 | 1.517 | -1.162 |

Threshold stability by benchmark:

| Tie-breaker | Threshold | Benchmark | Mean excess | Mean trades | Active IR | Active drawdown |
| --- | --- | --- | --- | --- | --- | --- |
| expected_slot_days | 0.90 | QQQ | 1.026 | 25.200 | 0.974 | -1.991 |
| expected_slot_days | 0.90 | SPY | 0.634 | 25.600 | 1.428 | -1.607 |
| expected_slot_days | 0.95 | QQQ | 1.382 | 24.800 | 1.570 | -1.821 |
| expected_slot_days | 0.95 | SPY | 0.634 | 25.600 | 1.428 | -1.607 |
| expected_slot_days | 0.98 | QQQ | 0.555 | 18.000 | 0.425 | -1.420 |
| expected_slot_days | 0.98 | SPY | 0.514 | 20.600 | 1.233 | -1.394 |
| expected_slot_days | 1.00 | QQQ | 0.762 | 17.200 | 0.714 | -1.371 |
| expected_slot_days | 1.00 | SPY | 0.709 | 19.600 | 1.517 | -1.162 |
| expected_slot_days | none | QQQ | 1.026 | 25.200 | 0.974 | -1.991 |
| expected_slot_days | none | SPY | 0.634 | 25.600 | 1.428 | -1.607 |
| source_order | 0.90 | QQQ | 0.587 | 24.600 | 0.598 | -2.205 |
| source_order | 0.90 | SPY | 0.218 | 25.600 | 0.985 | -1.879 |
| source_order | 0.95 | QQQ | 0.930 | 24.200 | 1.060 | -2.038 |
| source_order | 0.95 | SPY | 0.218 | 25.600 | 0.985 | -1.879 |
| source_order | 0.98 | QQQ | 0.172 | 18.000 | 0.119 | -1.656 |
| source_order | 0.98 | SPY | 0.478 | 20.600 | 1.358 | -1.324 |
| source_order | 1.00 | QQQ | 0.473 | 17.200 | 0.486 | -1.577 |
| source_order | 1.00 | SPY | 0.709 | 19.800 | 1.648 | -1.205 |
| source_order | none | QQQ | 0.587 | 24.600 | 0.598 | -2.205 |
| source_order | none | SPY | 0.218 | 25.600 | 0.985 | -1.879 |

## Decision and Stage 3

The research-frozen selector is:

`connection_strength descending → expected_slot_days tie-breaker → minimum connection strength ≥ 1.00`

It was chosen using the predeclared rule of maximizing the minimum SPY/QQQ outer-fold mean excess, with mean excess, active IR, and drawdown as tie-breakers. The nested result is SPY mean excess `0.709%`, QQQ mean excess `0.762%`, and mean active drawdown `-1.267%`.

The selector is now a `research_frozen_selector`: its ranking, tie-breaker, and admission threshold must not change during exit research. The lockbox remains reserved for one final evaluation of the full frozen modular pipeline.

Stage 3 has begun on the five outer development folds only. It contains `184` frozen-selector development trades, has legal `T_e - 1` dates for `100.0%` of them, maintains the `exit_date < T_e` guard, and has not trained an exit model or accessed the lockbox.

Key artifacts:

- `nested_outer_fold_manifest.csv`
- `nested_outer_choices.csv`
- `nested_outer_decisions.csv`
- `nested_outer_exact_replay_summary.csv`
- `factorial_ablation_summary.csv`
- `threshold_stability_summary.csv`
- `research_frozen_selector.json`
- `feature_hypothesis_status.json`
- `../stage3_exit_research/stage3_exit_development_trades.csv`
