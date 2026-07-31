# Stage 2G — Polymarket Probability-Trajectory Rerun

## 1. Coverage and leakage audit

Fresh public CLOB histories were downloaded at one-minute fidelity. Features use only `source_ts < scheduled NYSE entry close`; the 2025-11-28 early close is handled at 13:00 America/New_York.

| outer_fold | candidates | min_observations | median_observations | median_span_hours | max_latest_age_seconds | post_entry_observations_used |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 82 | 108 | 1517.0000 | 25.2667 | 58.0000 | 0 |
| 1 | 96 | 131 | 1306.5000 | 21.7585 | 57.0000 | 0 |
| 2 | 157 | 61 | 1471.0000 | 24.5003 | 56.0000 | 0 |
| 3 | 58 | 1272 | 2900.0000 | 48.3167 | 55.0000 | 0 |
| 4 | 22 | 355 | 12363.0000 | 206.0332 | 54.0000 | 0 |

All 415 OOF candidates have at least 61 observations; median coverage is 1519 points. Post-entry observations used: 0.

## 2. OOF opportunity and failure prediction

| model | task | observations | auc | balanced_accuracy | brier |
| --- | --- | --- | --- | --- | --- |
| A_target_b_baseline | opportunity_probability | 415 | 0.5658 | 0.5773 | 0.2585 |
| A_target_b_baseline | never_profitable_probability | 415 | 0.5420 | 0.5291 | 0.2554 |
| A_target_b_baseline | persistent_loss_probability | 415 | 0.4034 | 0.3936 | 0.2759 |
| A_target_b_baseline | severe_adverse_probability | 415 | 0.4293 | 0.4432 | 0.2835 |
| B_target_b_plus_trajectory | opportunity_probability | 415 | 0.5454 | 0.5426 | 0.2599 |
| B_target_b_plus_trajectory | never_profitable_probability | 415 | 0.5014 | 0.5109 | 0.2666 |
| B_target_b_plus_trajectory | persistent_loss_probability | 415 | 0.4406 | 0.4182 | 0.2864 |
| B_target_b_plus_trajectory | severe_adverse_probability | 415 | 0.4743 | 0.4857 | 0.2737 |
| C_probability_trajectory_only_diagnostic | opportunity_probability | 415 | 0.5074 | 0.4880 | 0.2703 |
| C_probability_trajectory_only_diagnostic | never_profitable_probability | 415 | 0.5260 | 0.5439 | 0.2598 |
| C_probability_trajectory_only_diagnostic | persistent_loss_probability | 415 | 0.5181 | 0.5132 | 0.2633 |
| C_probability_trajectory_only_diagnostic | severe_adverse_probability | 415 | 0.5502 | 0.5034 | 0.2548 |

## 3. Same-day selection

| model | benchmark | same_day_decisions | admission_rate | mean_selected_best_legal_net_active_pct | selected_never_profitable_rate | selected_persistent_loser_rate | mean_same_day_oracle_regret_pct |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A_target_b_baseline | QQQ | 25 | 0.6400 | 2.1985 | 0.2400 | 0.2000 | 4.2381 |
| A_target_b_baseline | SPY | 24 | 0.6667 | 2.0265 | 0.1667 | 0.1667 | 4.4838 |
| B_target_b_plus_trajectory | QQQ | 25 | 0.3600 | 1.0559 | 0.1200 | 0.0800 | 5.3807 |
| B_target_b_plus_trajectory | SPY | 24 | 0.3750 | 0.8077 | 0.1667 | 0.1667 | 5.7026 |
| C_probability_trajectory_only_diagnostic | QQQ | 25 | 0.5600 | 1.7880 | 0.2000 | 0.2000 | 4.6486 |
| C_probability_trajectory_only_diagnostic | SPY | 24 | 0.7500 | 1.9635 | 0.2500 | 0.2917 | 4.5468 |

## 4. Selector × exit exact replay

| selector_policy | benchmark | mean_excess_return | mean_active_information_ratio | mean_active_drawdown_pct | mean_trades |
| --- | --- | --- | --- | --- | --- |
| A_target_b_baseline | QQQ | 3.2490 | 1.5892 | -2.4825 | 50.6250 |
| A_target_b_baseline | SPY | 1.3868 | 0.8953 | -2.1469 | 44.8750 |
| B_target_b_plus_trajectory | QQQ | 0.7293 | 0.1468 | -2.3782 | 22.8750 |
| B_target_b_plus_trajectory | SPY | 0.6599 | 0.9445 | -1.3858 | 21.8750 |
| C_probability_trajectory_only_diagnostic | QQQ | -2.3024 | -1.6086 | -5.3384 | 34.7500 |
| C_probability_trajectory_only_diagnostic | SPY | -1.7344 | -1.1963 | -4.8150 | 50.1250 |

Paired B minus A results by exit and benchmark:

| benchmark | exit_policy | paired_excess_improvement | paired_drawdown_change_pct |
| --- | --- | --- | --- |
| QQQ | corrected_reference_exit | -3.5689 | -0.0767 |
| QQQ | fixed_2_day | -2.7879 | -0.1251 |
| QQQ | fixed_4_day | -2.0870 | -0.2883 |
| QQQ | fixed_8_day | -1.9896 | 0.3781 |
| QQQ | hold_to_te1 | -2.5459 | 0.1747 |
| QQQ | time_underwater_exit | -2.1735 | 0.3545 |
| QQQ | trailing_profit_giveback_exit | -2.5459 | 0.1747 |
| QQQ | volatility_scaled_stop | -2.4590 | 0.2430 |
| SPY | corrected_reference_exit | -1.5946 | -0.1150 |
| SPY | fixed_2_day | -0.8692 | 0.2793 |
| SPY | fixed_4_day | -0.0751 | 0.2540 |
| SPY | fixed_8_day | 1.2470 | 1.3873 |
| SPY | hold_to_te1 | -1.2685 | 1.0873 |
| SPY | time_underwater_exit | -1.1127 | 0.9370 |
| SPY | trailing_profit_giveback_exit | -1.3341 | 1.0879 |
| SPY | volatility_scaled_stop | -0.8078 | 1.1708 |

## 5. Paired chronological folds

| outer_fold | mean_paired_excess_improvement | median_paired_excess_improvement | mean_paired_drawdown_change_pct |
| --- | --- | --- | --- |
| 0 | -0.1584 | -0.1055 | 0.3307 |
| 1 | -0.0886 | -0.0347 | -0.1283 |
| 2 | 0.1887 | 0.4961 | 1.3588 |
| 3 | -1.3386 | -1.3584 | 0.7616 |
| 4 | -0.2525 | -0.2113 | 0.0121 |

## 6. Promotion decision

| promotion_gate | passed | observed |
| --- | --- | --- |
| same_day_opportunity_ranking_or_bad_trade_rejection | True | {'regret': False, 'best': False, 'never': True, 'persistent': True} |
| positive_paired_median_fold_improvement | False | -0.11919999999999997 |
| portfolio_improves_spy_and_qqq | False | {'QQQ': -2.5197125000000002, 'SPY': -0.7268749999999999} |
| not_worse_in_more_than_three_exits | False | {'worse_exit_policies': 8, 'limit': 3} |
| not_driven_by_one_fold | False | {'positive_folds': 1, 'leave_one_fold_out_medians': [-0.1280000000000001, -0.21130000000000004, -0.2102, -0.07020000000000004, -0.08084999999999998]} |
| drawdown_not_materially_worse | True | {'mean_change_by_benchmark': {'QQQ': 0.10435920214240946, 'SPY': 0.761068946236855}, 'tolerance_pct': 1.0} |

**Final decision: close_earnings_selection_and_proceed_to_exits.**

Operational selector: `A_target_b_baseline`. The lockbox remained sealed; geo and other catalysts were excluded.
