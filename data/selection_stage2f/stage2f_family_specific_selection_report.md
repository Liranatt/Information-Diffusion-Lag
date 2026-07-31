# Stage 2F — Family-Specific Trade Opportunity Selection

## 1. Scope and oracle labels

Stage 2F used 580 development candidates and opened zero test/lockbox rows. Every label uses legal closes only through `T_e-1`; `T_e` is never an exit. Standardized net-active labels subtract asset buy/sell and benchmark sell/rebuy costs and slippage at a $10,000 reference notional.

These full-path labels are ex-post research targets only and are explicitly prohibited as live features.

| event_family | candidates | independent_events | reaches_2pct_rate | never_profitable_rate | persistent_loser_rate | severe_adverse_rate | mean_best_legal_net_active_pct |
| --- | --- | --- | --- | --- | --- | --- | --- |
| earnings | 492.0000 | 273.0000 | 0.3984 | 0.3232 | 0.3557 | 0.2927 | 2.2280 |
| geo | 61.0000 | 14.0000 | 0.4098 | 0.3770 | 0.4098 | 0.3934 | 1.9450 |
| other | 27.0000 | 15.0000 | 0.7037 | 0.0000 | 0.1111 | 0.3333 | 14.5986 |

## 2. Nested chronological prediction results

All preprocessing, ridge penalties from `(1.0, 5.0, 20.0)`, and binary thresholds from `(0.4, 0.5, 0.6)` were selected inside inner chronological folds. Outer folds keep event episodes intact and train only on events completed before validation starts. No RL, deep model, or large search was used.

| scope | family | task | observations | independent_events | auc | balanced_accuracy | mae | spearman |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pooled | all | opportunity_probability | 415.0000 | 231.0000 | 0.4915 | 0.4710 |  |  |
| pooled | all | expected_best_legal_return | 415.0000 | 231.0000 |  |  | 2.6017 | -0.1041 |
| pooled | all | never_profitable_probability | 415.0000 | 231.0000 | 0.4295 | 0.3743 |  |  |
| pooled | all | persistent_loss_probability | 415.0000 | 231.0000 | 0.3925 | 0.4639 |  |  |
| pooled | all | severe_adverse_probability | 415.0000 | 231.0000 | 0.3945 | 0.4740 |  |  |
| pooled_within_family | earnings | opportunity_probability | 415.0000 | 231.0000 | 0.4915 | 0.4710 |  |  |
| pooled_within_family | earnings | expected_best_legal_return | 415.0000 | 231.0000 |  |  | 2.6017 | -0.1041 |
| pooled_within_family | earnings | never_profitable_probability | 415.0000 | 231.0000 | 0.4295 | 0.3743 |  |  |
| pooled_within_family | earnings | persistent_loss_probability | 415.0000 | 231.0000 | 0.3925 | 0.4639 |  |  |
| pooled_within_family | earnings | severe_adverse_probability | 415.0000 | 231.0000 | 0.3945 | 0.4740 |  |  |
| family_specific | earnings | opportunity_probability | 415.0000 | 231.0000 | 0.4390 | 0.4924 |  |  |
| family_specific | earnings | expected_best_legal_return | 415.0000 | 231.0000 |  |  | 2.6674 | -0.1512 |
| family_specific | earnings | never_profitable_probability | 415.0000 | 231.0000 | 0.4179 | 0.3940 |  |  |
| family_specific | earnings | persistent_loss_probability | 415.0000 | 231.0000 | 0.3498 | 0.4266 |  |  |
| family_specific | earnings | severe_adverse_probability | 415.0000 | 231.0000 | 0.3819 | 0.4845 |  |  |

Outer-fold date ranges and validation-family composition:

| outer_fold | training_end | validation_start | validation_end | training_rows | validation_rows | training_events | validation_events | validation_family_composition | family_model_status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.0000 | 2025-10-06 00:00:00+00:00 | 2025-10-07 00:00:00+00:00 | 2025-10-20 00:00:00+00:00 | 159.0000 | 82.0000 | 68.0000 | 48.0000 | {"earnings": 82} | {"earnings": "fitted"} |
| 1.0000 | 2025-10-20 00:00:00+00:00 | 2025-10-21 00:00:00+00:00 | 2025-10-30 00:00:00+00:00 | 247.0000 | 96.0000 | 119.0000 | 56.0000 | {"earnings": 96} | {"earnings": "fitted"} |
| 2.0000 | 2025-10-28 00:00:00+00:00 | 2025-10-29 00:00:00+00:00 | 2025-11-17 00:00:00+00:00 | 335.0000 | 157.0000 | 171.0000 | 85.0000 | {"earnings": 157} | {"earnings": "fitted"} |
| 3.0000 | 2025-11-11 00:00:00+00:00 | 2025-11-12 00:00:00+00:00 | 2025-12-04 00:00:00+00:00 | 482.0000 | 58.0000 | 251.0000 | 30.0000 | {"earnings": 58} | {"earnings": "fitted"} |
| 4.0000 | 2025-11-28 00:00:00+00:00 | 2025-12-01 00:00:00+00:00 | 2025-12-11 00:00:00+00:00 | 552.0000 | 22.0000 | 287.0000 | 12.0000 | {"earnings": 22} | {"earnings": "fitted"} |

Earnings has enough independent events for fitted family models in supported outer folds. Geo (14 events) and other (15 events) remain exploratory and use transparent semantic/ordering fallbacks.

The concatenated OOF validation composition is `{"earnings": 415}`. Because every OOF validation row is earnings, the chronological evidence validates only the earnings family. Geo and other occurred too early and/or are too sparse to obtain honest global-chronology outer validation; they are not claimed as predictive models.

## 3. Features by family

Most stable fitted earnings coefficients:

| task | feature | folds | median_standardized_coefficient | coefficient_sign_consistency |
| --- | --- | --- | --- | --- |
| expected_best_legal_return | stock_minus_sector_20d | 5.0000 | -0.3206 | 1.0000 |
| expected_best_legal_return | feat_beta | 5.0000 | 0.2043 | 0.8000 |
| expected_best_legal_return | feat_sector_1m_trend | 5.0000 | -0.2022 | 0.6000 |
| expected_best_legal_return | feat_runup_since_t0 | 5.0000 | -0.1939 | 1.0000 |
| expected_best_legal_return | expected_slot_days | 5.0000 | 0.1691 | 0.8000 |
| expected_time_to_opportunity | feat_prob_at_trigger | 5.0000 | 0.2134 | 0.8000 |
| expected_time_to_opportunity | feat_prob_surge_since_t0 | 5.0000 | -0.1600 | 0.8000 |
| expected_time_to_opportunity | stock_minus_sector_20d | 5.0000 | 0.1555 | 1.0000 |
| expected_time_to_opportunity | feat_spy_2w_trend | 5.0000 | 0.1472 | 0.8000 |
| expected_time_to_opportunity | expected_slot_days | 5.0000 | -0.1323 | 0.8000 |
| never_profitable_probability | feat_prob_at_trigger | 5.0000 | 0.2162 | 1.0000 |
| never_profitable_probability | feat_prob_volatility | 5.0000 | -0.1888 | 0.8000 |
| never_profitable_probability | feat_beta | 5.0000 | -0.1427 | 0.6000 |
| never_profitable_probability | feat_log_market_cap | 5.0000 | 0.1417 | 0.6000 |
| never_profitable_probability | feat_runup_since_t0 | 5.0000 | 0.1339 | 1.0000 |
| opportunity_probability | expected_slot_days | 5.0000 | 0.2236 | 1.0000 |
| opportunity_probability | stock_minus_sector_20d | 5.0000 | -0.1899 | 1.0000 |
| opportunity_probability | feat_spy_2w_trend | 5.0000 | -0.1378 | 0.6000 |
| opportunity_probability | feat_asset_2w_trend | 5.0000 | -0.1179 | 0.8000 |
| opportunity_probability | feat_beta | 5.0000 | 0.1042 | 0.8000 |
| persistent_loss_probability | feat_prob_volatility | 5.0000 | -0.2023 | 0.8000 |
| persistent_loss_probability | feat_log_market_cap | 5.0000 | 0.1180 | 0.6000 |
| persistent_loss_probability | feat_sector_1m_trend | 5.0000 | 0.1009 | 0.8000 |
| persistent_loss_probability | feat_spy_2w_trend | 5.0000 | 0.0995 | 0.6000 |
| persistent_loss_probability | feat_beta | 5.0000 | -0.0984 | 1.0000 |

Geo and other descriptive correlations—diagnostic only, never selected as fitted evidence:

| family | target | feature | observations | independent_events | spearman | diagnostic_role |
| --- | --- | --- | --- | --- | --- | --- |
| geo | best_legal_net_active_return_pct | event_candidates_seen_previous_5_days | 60.0000 | 14.0000 | -0.4300 | descriptive_only |
| geo | best_legal_net_active_return_pct | feat_sector_1m_trend | 61.0000 | 14.0000 | -0.2267 | descriptive_only |
| geo | best_legal_net_active_return_pct | impact_materiality | 61.0000 | 14.0000 | -0.1644 | descriptive_only |
| geo | never_profitable_after_costs | event_candidates_seen_previous_5_days | 60.0000 | 14.0000 | 0.4434 | descriptive_only |
| geo | never_profitable_after_costs | feat_sector_1m_trend | 61.0000 | 14.0000 | 0.2387 | descriptive_only |
| geo | never_profitable_after_costs | stock_minus_sector_20d | 60.0000 | 14.0000 | 0.2338 | descriptive_only |
| geo | persistent_loser | event_candidates_seen_previous_5_days | 60.0000 | 14.0000 | 0.4089 | descriptive_only |
| geo | persistent_loser | feat_sector_1m_trend | 61.0000 | 14.0000 | 0.2409 | descriptive_only |
| geo | persistent_loser | stock_minus_sector_20d | 60.0000 | 14.0000 | 0.2375 | descriptive_only |
| geo | severe_adverse_before_meaningful_gain | stock_minus_sector_20d | 60.0000 | 14.0000 | 0.4838 | descriptive_only |
| geo | severe_adverse_before_meaningful_gain | feat_asset_2w_trend | 61.0000 | 14.0000 | 0.3139 | descriptive_only |
| geo | severe_adverse_before_meaningful_gain | feat_runup_since_t0 | 61.0000 | 14.0000 | 0.2481 | descriptive_only |
| other | best_legal_net_active_return_pct | stock_minus_sector_20d | 27.0000 | 15.0000 | 0.3196 | descriptive_only |
| other | best_legal_net_active_return_pct | feat_beta | 27.0000 | 15.0000 | 0.3079 | descriptive_only |
| other | best_legal_net_active_return_pct | feat_asset_2w_trend | 27.0000 | 15.0000 | -0.2920 | descriptive_only |
| other | never_profitable_after_costs | expected_slot_days | 27.0000 | 15.0000 |  | descriptive_only |
| other | never_profitable_after_costs | stock_minus_sector_20d | 27.0000 | 15.0000 |  | descriptive_only |
| other | never_profitable_after_costs | feat_prob_at_trigger | 27.0000 | 15.0000 |  | descriptive_only |
| other | persistent_loser | feat_prob_at_trigger | 27.0000 | 15.0000 | 0.3127 | descriptive_only |
| other | persistent_loser | feat_beta | 27.0000 | 15.0000 | -0.2570 | descriptive_only |
| other | persistent_loser | stock_minus_sector_20d | 27.0000 | 15.0000 | -0.2272 | descriptive_only |
| other | severe_adverse_before_meaningful_gain | feat_sector_1m_trend | 27.0000 | 15.0000 | -0.4967 | descriptive_only |
| other | severe_adverse_before_meaningful_gain | feat_runup_since_t0 | 27.0000 | 15.0000 | -0.4750 | descriptive_only |
| other | severe_adverse_before_meaningful_gain | feat_prob_at_trigger | 27.0000 | 15.0000 | 0.4576 | descriptive_only |

Coefficient magnitude is not causal importance. A feature is credible only when its sign is reasonably stable across outer folds and the corresponding OOF task has useful discrimination.

## 4. Same-day opportunity selection

The oracle picks the candidate with the highest ex-post best legal net active return on each benchmark/date and is diagnostic only. Regret charges an abstention as a zero-return choice.

| method_family | benchmark | same_day_decisions | admission_rate | mean_selected_best_legal_net_active_pct | selected_never_profitable_rate | selected_persistent_loser_rate | selected_severe_adverse_rate | mean_same_day_oracle_regret_pct | oracle_exact_hit_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A_target_b_baseline | QQQ | 25.0000 | 0.6400 | 2.1985 | 0.2400 | 0.2000 | 0.1600 | 4.2381 | 0.2000 |
| A_target_b_baseline | SPY | 24.0000 | 0.6667 | 2.0265 | 0.1667 | 0.1667 | 0.2083 | 4.4838 | 0.1250 |
| B_opportunity_ranking_only | QQQ | 25.0000 | 1.0000 | 2.5813 | 0.2000 | 0.2400 | 0.2400 | 3.8553 | 0.2400 |
| B_opportunity_ranking_only | SPY | 24.0000 | 1.0000 | 3.6574 | 0.1667 | 0.2083 | 0.2083 | 2.8529 | 0.3333 |
| C_failure_filter_only | QQQ | 25.0000 | 0.4400 | 1.2295 | 0.2400 | 0.2400 | 0.1600 | 5.2071 | 0.0800 |
| C_failure_filter_only | SPY | 24.0000 | 0.5000 | 0.9785 | 0.1667 | 0.0833 | 0.0833 | 5.5318 | 0.0833 |
| D_failure_then_opportunity | QQQ | 25.0000 | 0.4400 | 1.1703 | 0.2000 | 0.2000 | 0.2000 | 5.2662 | 0.1200 |
| D_failure_then_opportunity | SPY | 24.0000 | 0.5000 | 1.2331 | 0.1667 | 0.1250 | 0.1250 | 5.2772 | 0.1250 |
| E_family_specific_shared_allocator | QQQ | 25.0000 | 0.6000 | 1.1892 | 0.2400 | 0.2000 | 0.1600 | 5.2474 | 0.2400 |
| E_family_specific_shared_allocator | SPY | 24.0000 | 0.7083 | 2.1101 | 0.2083 | 0.2083 | 0.2083 | 4.4003 | 0.2500 |
| expected_slot_days | QQQ | 25.0000 | 1.0000 | 2.7333 | 0.3200 | 0.3600 | 0.2800 | 3.7033 | 0.2400 |
| expected_slot_days | SPY | 24.0000 | 1.0000 | 1.8529 | 0.2083 | 0.1667 | 0.1667 | 4.6574 | 0.1250 |
| random_legal | QQQ | 500.0000 | 1.0000 | 2.8924 | 0.2940 | 0.3100 | 0.2660 | 3.5442 | 0.2620 |
| random_legal | SPY | 480.0000 | 1.0000 | 2.5534 | 0.2437 | 0.2458 | 0.2146 | 3.9569 | 0.1875 |
| source_order | QQQ | 25.0000 | 1.0000 | 2.1925 | 0.4400 | 0.5600 | 0.4800 | 4.2441 | 0.2000 |
| source_order | SPY | 24.0000 | 1.0000 | 2.2193 | 0.2917 | 0.2917 | 0.2917 | 4.2911 | 0.2500 |

## 5. Exact selector × exit-policy replay

Every one of the five selector policies was replayed independently under all eight Stage 2E exits for SPY and QQQ. Each of the 80 combined runs and every outer-fold run rebuilt capital, capacity, later admissions, benchmark rotation, costs, and slippage.

| selector_policy | robust_rank_score | mean_excess_return | median_excess_return | mean_active_ir | mean_active_drawdown_pct | mean_event_cluster_concentration_pct | max_event_cluster_concentration_pct | robust_order |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A_target_b_baseline | 1.0000 | 2.3179 | 2.8461 | 1.2423 | -2.3147 | 9.3302 | 11.7731 | 1.0000 |
| C_failure_filter_only | 2.3750 | -0.4990 | -0.3774 | -0.8314 | -3.3943 | 11.4850 | 13.8883 | 2.0000 |
| D_failure_then_opportunity | 3.4167 | -1.0838 | -0.9862 | -1.2646 | -3.8529 | 12.1031 | 16.9789 | 3.0000 |
| E_family_specific_shared_allocator | 4.3333 | -1.6878 | -1.4148 | -1.3629 | -5.0527 | 18.2789 | 27.3534 | 4.0000 |
| B_opportunity_ranking_only | 4.4167 | -1.2166 | -1.1370 | -0.4813 | -6.8677 | 7.2966 | 9.5216 | 5.0000 |

SPY and QQQ separately (mean across the eight predeclared exits):

| selector_policy | benchmark | mean_excess_return | mean_active_ir | mean_active_drawdown_pct | mean_trade_count | mean_slot_usage_pct |
| --- | --- | --- | --- | --- | --- | --- |
| A_target_b_baseline | QQQ | 3.2490 | 1.5892 | -2.4825 | 50.6250 | 47.5455 |
| A_target_b_baseline | SPY | 1.3868 | 0.8953 | -2.1469 | 44.8750 | 41.0227 |
| B_opportunity_ranking_only | QQQ | -1.1578 | -0.4430 | -7.3386 | 88.6250 | 76.7955 |
| B_opportunity_ranking_only | SPY | -1.2754 | -0.5195 | -6.3968 | 97.6250 | 80.6818 |
| C_failure_filter_only | QQQ | -0.9237 | -1.2332 | -3.8491 | 32.7500 | 28.0682 |
| C_failure_filter_only | SPY | -0.0743 | -0.4295 | -2.9394 | 33.1250 | 24.6818 |
| D_failure_then_opportunity | QQQ | -1.2016 | -1.3716 | -4.1123 | 29.7500 | 27.2273 |
| D_failure_then_opportunity | SPY | -0.9659 | -1.1576 | -3.5934 | 28.3750 | 23.0000 |
| E_family_specific_shared_allocator | QQQ | -1.6078 | -1.2609 | -5.3779 | 37.6250 | 33.8409 |
| E_family_specific_shared_allocator | SPY | -1.7677 | -1.4650 | -4.7275 | 41.6250 | 34.9318 |

Chronological fold stability:

| selector_policy | mean_fold_excess | median_fold_excess | mean_positive_fold_share |
| --- | --- | --- | --- |
| A_target_b_baseline | 0.4746 | 0.0780 | 0.5500 |
| B_opportunity_ranking_only | -0.2369 | -0.2817 | 0.4750 |
| C_failure_filter_only | -0.0815 | 0.0000 | 0.4000 |
| D_failure_then_opportunity | -0.1971 | 0.0000 | 0.4000 |
| E_family_specific_shared_allocator | -0.3058 | -0.1035 | 0.3875 |

Robustness after excluding the small `other` catalyst family:

| selector_policy | robust_rank_score | mean_excess_return | mean_active_ir | mean_active_drawdown_pct | robust_order |
| --- | --- | --- | --- | --- | --- |
| A_target_b_baseline | 1.0000 | 2.3179 | 1.2423 | -2.3147 | 1.0000 |
| C_failure_filter_only | 2.3750 | -0.4990 | -0.8314 | -3.3943 | 2.0000 |
| D_failure_then_opportunity | 3.4167 | -1.0838 | -1.2646 | -3.8529 | 3.0000 |
| E_family_specific_shared_allocator | 4.3333 | -1.6878 | -1.3629 | -5.0527 | 4.0000 |
| B_opportunity_ranking_only | 4.4167 | -1.2166 | -0.4813 | -6.8677 | 5.0000 |

This table is identical to the full OOF table by construction because the OOF stream contains earnings only. It confirms that `other` did not drive the Stage 2F selector comparison; it is not an independent robustness test and does not validate geo/other models.

## 6. Freeze decision

| selector_policy | opportunity_auc | opportunity_identified | failure_auc_never_profitable | failure_auc_persistent | failure_auc_severe_adverse | failures_identified | same_day_best_improved | same_day_bad_trade_rates_improved | exact_robust_rank_improved | exit_policies_not_worse_than_target_b | fold_stability_gate | credible_without_other | event_cluster_concentration_gate | all_freeze_gates_passed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B_opportunity_ranking_only | 0.4915 | False | 0.4295 | 0.3925 | 0.3945 | False | True | False | False | 0.0000 | False | False | True | False |
| C_failure_filter_only | 0.4915 | False | 0.4295 | 0.3925 | 0.3945 | False | False | True | False | 0.0000 | False | False | True | False |
| D_failure_then_opportunity | 0.4915 | False | 0.4295 | 0.3925 | 0.3945 | False | False | True | False | 0.0000 | False | False | True | False |
| E_family_specific_shared_allocator | 0.4390 | False | 0.4179 | 0.3498 | 0.3819 | False | False | False | False | 0.0000 | False | False | True | False |

Selected policy: `A_target_b_baseline`. No learned stage 2f selector passed every predeclared gate; retain the simplest valid stage 2e target b baseline.

No exit model was trained and no exit policy was frozen. The 184- and 138-trade Stage 3 samples remain audit-only. The later lockbox remains sealed.

## 7. Required answers

1. **Can opportunity be identified in advance?** No reliable evidence: pooled OOF AUC is 0.491 and earnings AUC is 0.439.
2. **Can bad trades be identified more reliably than exact winners?** Not reliably: fewer than two pooled failure tasks cleared OOF AUC 0.55.
3. **Which features work by family?** No feature set is established as working. Some earnings coefficient signs are stable enough to remain hypotheses, but their associated OOF tasks did not achieve useful discrimination. Geo and other correlations are descriptive only.
4. **Does family-specific selection improve the real portfolio decision without overfitting?** No learned family-specific selector passed every gate; Target B is retained as the simplest valid baseline.
