# Stage 2E — Path-Aware Selection–Exit Interaction

## Scope and legal horizon

Stage 2E used development data only: 580 semantic candidates and 496 chronological OOF evaluation candidates. Test/lockbox rows read: 0. Stage 2C semantic mapping conclusions remain frozen, while the Stage 2C performance-selector freeze based on `predicted_target_a_positive` is superseded.

Every path and replay enforces `exit_date < T_e`. `T_e` is never an exit; `T_e - 1` is the latest legal horizon. The 184-trade Stage 2B and 138-trade Stage 2C Stage 3 samples remain unchanged and audit-only. No exit model was trained.

## Legal paths and descriptive classes

The long path table reports stock, benchmark, and active return on every legal holding day, running active MFE/MAE and their timing, peak giveback, underwater duration, trough recovery, positive-active-day fraction, realized volatility, and overnight gaps. Classes are fixed diagnostics and were not selected on a lockbox.

| path_class | candidates |
| --- | --- |
| early_winner_with_giveback | 183 |
| persistent_loser | 100 |
| early_loser_with_recovery | 97 |
| unclassified_mixed | 97 |
| immediate_winner | 50 |
| volatile_oscillation | 28 |
| delayed_winner | 23 |
| flat_nonresponsive | 2 |

Early-vs-terminal sign reversals: 308. Candidates with first-four-day active MFE >=2% that finish negative at `T_e-1`: 155. Terminal winners that suffered active MAE <=-5%: 10.

## Exact selector × exit-policy matrix

Each of the 40 selector/exit combinations was replayed independently for SPY and QQQ. Every replay rebuilt its own capacity, capital, benchmark rotation, turnover, and later admissions. The same matrix was also replayed separately on every OOF panel for fold and event-family reporting.

| selector | exit_policy | benchmark | excess_return | active_information_ratio | active_max_drawdown_pct | turnover_x_average_equity | n_trades | slot_usage_pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| direct_always_fill | hold_to_te1 | SPY | 0.9365 | 0.1979 | -9.2916 | 17.8350 | 98.0000 | 51.9118 |
| direct_always_fill | hold_to_te1 | QQQ | 3.3425 | 0.5331 | -8.3096 | 16.5294 | 90.0000 | 49.5556 |
| direct_always_fill | fixed_2_day | SPY | -4.3755 | -1.1218 | -7.7867 | 34.9104 | 192.0000 | 28.3824 |
| direct_always_fill | fixed_2_day | QQQ | -5.7435 | -1.5490 | -8.7953 | 30.2235 | 165.0000 | 24.5185 |
| direct_always_fill | fixed_4_day | SPY | 0.9821 | 0.2513 | -4.1619 | 22.5453 | 123.0000 | 35.2941 |
| direct_always_fill | fixed_4_day | QQQ | -4.1250 | -0.8775 | -6.8618 | 21.2377 | 116.0000 | 33.9259 |
| direct_always_fill | fixed_8_day | SPY | -2.3185 | -0.3843 | -8.3334 | 18.3323 | 101.0000 | 42.7206 |
| direct_always_fill | fixed_8_day | QQQ | -2.5132 | -0.3933 | -8.5647 | 16.9867 | 93.0000 | 41.7778 |
| direct_always_fill | corrected_reference_exit | SPY | -0.3589 | -0.0375 | -6.2323 | 23.2727 | 127.0000 | 40.0000 |
| direct_always_fill | corrected_reference_exit | QQQ | -0.7205 | -0.0984 | -7.4667 | 21.2427 | 116.0000 | 36.7407 |
| direct_always_fill | volatility_scaled_stop | SPY | -3.0120 | -0.5443 | -9.7612 | 18.6144 | 103.0000 | 45.7353 |
| direct_always_fill | volatility_scaled_stop | QQQ | -3.1739 | -0.5619 | -10.4958 | 17.0478 | 94.0000 | 45.0370 |
| direct_always_fill | time_underwater_exit | SPY | 1.5880 | 0.3153 | -7.5113 | 18.7669 | 103.0000 | 46.2500 |
| direct_always_fill | time_underwater_exit | QQQ | 0.4080 | 0.1224 | -7.5455 | 17.5911 | 96.0000 | 42.6667 |
| direct_always_fill | trailing_profit_giveback_exit | SPY | -2.8928 | -0.4528 | -9.8193 | 18.2250 | 101.0000 | 49.1912 |
| direct_always_fill | trailing_profit_giveback_exit | QQQ | -0.6951 | -0.0540 | -8.3741 | 16.7501 | 92.0000 | 46.2222 |
| predicted_target_a_positive | hold_to_te1 | SPY | 1.8774 | 0.3810 | -4.3429 | 10.6075 | 58.0000 | 31.4706 |
| predicted_target_a_positive | hold_to_te1 | QQQ | 4.1614 | 0.7792 | -3.6539 | 10.9001 | 59.0000 | 30.8148 |
| predicted_target_a_positive | fixed_2_day | SPY | -0.8871 | -0.4749 | -2.6402 | 15.8166 | 87.0000 | 12.8676 |
| predicted_target_a_positive | fixed_2_day | QQQ | -0.9907 | -0.4574 | -3.0962 | 14.3163 | 78.0000 | 11.5556 |
| predicted_target_a_positive | fixed_4_day | SPY | 0.4871 | 0.1810 | -3.0661 | 11.8273 | 65.0000 | 18.1618 |
| predicted_target_a_positive | fixed_4_day | QQQ | 1.4785 | 0.4390 | -2.7062 | 12.4700 | 68.0000 | 19.4074 |
| predicted_target_a_positive | fixed_8_day | SPY | -1.4295 | -0.3037 | -4.2369 | 10.5405 | 58.0000 | 23.4559 |
| predicted_target_a_positive | fixed_8_day | QQQ | -0.1800 | 0.0013 | -3.6622 | 10.8204 | 59.0000 | 24.6667 |
| predicted_target_a_positive | corrected_reference_exit | SPY | -1.0852 | -0.3261 | -3.8284 | 12.8954 | 71.0000 | 21.0294 |
| predicted_target_a_positive | corrected_reference_exit | QQQ | 0.6230 | 0.2211 | -2.6488 | 12.2890 | 67.0000 | 18.9630 |
| predicted_target_a_positive | volatility_scaled_stop | SPY | -2.8241 | -0.7740 | -4.3001 | 10.6827 | 59.0000 | 24.9265 |
| predicted_target_a_positive | volatility_scaled_stop | QQQ | -1.4656 | -0.3550 | -4.3322 | 11.1489 | 61.0000 | 26.3704 |
| predicted_target_a_positive | time_underwater_exit | SPY | 0.9608 | 0.2401 | -4.0598 | 10.5713 | 58.0000 | 25.5147 |
| predicted_target_a_positive | time_underwater_exit | QQQ | 1.4431 | 0.3383 | -3.5092 | 11.2149 | 61.0000 | 27.1111 |
| predicted_target_a_positive | trailing_profit_giveback_exit | SPY | -2.3737 | -0.4841 | -4.4354 | 10.8845 | 60.0000 | 28.3824 |
| predicted_target_a_positive | trailing_profit_giveback_exit | QQQ | -0.4396 | -0.0480 | -3.7589 | 10.9638 | 60.0000 | 27.2593 |
| target_b_per_slot_day | hold_to_te1 | SPY | 6.4865 | 1.0016 | -4.3429 | 16.1731 | 88.0000 | 49.0441 |
| target_b_per_slot_day | hold_to_te1 | QQQ | 8.0383 | 1.2382 | -3.6539 | 13.7236 | 74.0000 | 42.8889 |
| target_b_per_slot_day | fixed_2_day | SPY | 0.7246 | 0.3083 | -3.6893 | 23.2209 | 127.0000 | 18.7500 |
| target_b_per_slot_day | fixed_2_day | QQQ | -0.7439 | -0.2977 | -3.3186 | 18.6212 | 101.0000 | 14.9630 |
| target_b_per_slot_day | fixed_4_day | SPY | -0.2573 | -0.0313 | -3.2077 | 18.1260 | 99.0000 | 28.0147 |
| target_b_per_slot_day | fixed_4_day | QQQ | 1.2349 | 0.3617 | -2.7062 | 15.6780 | 85.0000 | 24.5926 |
| target_b_per_slot_day | fixed_8_day | SPY | 0.9021 | 0.2140 | -4.2369 | 16.2757 | 89.0000 | 38.3088 |
| target_b_per_slot_day | fixed_8_day | QQQ | 1.2156 | 0.2950 | -3.6011 | 13.8388 | 75.0000 | 33.6296 |
| target_b_per_slot_day | corrected_reference_exit | SPY | -0.5988 | -0.1175 | -4.7639 | 18.6679 | 102.0000 | 32.8676 |
| target_b_per_slot_day | corrected_reference_exit | QQQ | 2.3723 | 0.6339 | -4.4471 | 15.6209 | 85.0000 | 27.5556 |
| target_b_per_slot_day | volatility_scaled_stop | SPY | 1.0330 | 0.2481 | -5.9293 | 16.1953 | 89.0000 | 41.6176 |
| target_b_per_slot_day | volatility_scaled_stop | QQQ | 2.6846 | 0.5837 | -4.2705 | 13.9603 | 76.0000 | 38.5185 |
| target_b_per_slot_day | time_underwater_exit | SPY | 4.7947 | 0.8588 | -4.5724 | 16.3197 | 89.0000 | 41.9118 |
| target_b_per_slot_day | time_underwater_exit | QQQ | 3.5995 | 0.6956 | -3.5092 | 14.2381 | 77.0000 | 36.8889 |
| target_b_per_slot_day | trailing_profit_giveback_exit | SPY | 2.5499 | 0.4858 | -5.1044 | 16.4024 | 90.0000 | 44.9265 |
| target_b_per_slot_day | trailing_profit_giveback_exit | QQQ | 3.0533 | 0.5827 | -4.5955 | 13.7505 | 75.0000 | 38.6667 |
| expected_slot_days_ranking | hold_to_te1 | SPY | 7.4136 | 1.2073 | -5.1105 | 18.6205 | 101.0000 | 50.9559 |
| expected_slot_days_ranking | hold_to_te1 | QQQ | 7.4538 | 1.0931 | -5.6156 | 17.2630 | 93.0000 | 49.8519 |
| expected_slot_days_ranking | fixed_2_day | SPY | -3.7185 | -0.9051 | -7.3110 | 34.9001 | 192.0000 | 28.3824 |
| expected_slot_days_ranking | fixed_2_day | QQQ | -4.5408 | -1.1246 | -8.3156 | 30.2208 | 165.0000 | 24.5185 |
| expected_slot_days_ranking | fixed_4_day | SPY | 0.7613 | 0.2074 | -5.4609 | 22.9260 | 125.0000 | 35.5882 |
| expected_slot_days_ranking | fixed_4_day | QQQ | -0.2512 | -0.0008 | -5.1101 | 21.3428 | 116.0000 | 33.7778 |
| expected_slot_days_ranking | fixed_8_day | SPY | 4.1043 | 0.8099 | -4.4853 | 19.1515 | 104.0000 | 42.1324 |
| expected_slot_days_ranking | fixed_8_day | QQQ | 1.3115 | 0.2656 | -5.6250 | 17.7143 | 96.0000 | 42.3704 |
| expected_slot_days_ranking | corrected_reference_exit | SPY | -0.2123 | -0.0060 | -5.8711 | 23.8948 | 130.0000 | 39.6324 |
| expected_slot_days_ranking | corrected_reference_exit | QQQ | 2.1006 | 0.4435 | -6.4605 | 22.0971 | 120.0000 | 36.6667 |
| expected_slot_days_ranking | volatility_scaled_stop | SPY | 3.4075 | 0.7320 | -5.5743 | 19.4170 | 106.0000 | 45.4412 |
| expected_slot_days_ranking | volatility_scaled_stop | QQQ | -0.8500 | -0.0947 | -8.8877 | 17.5127 | 96.0000 | 45.7778 |
| expected_slot_days_ranking | time_underwater_exit | SPY | 7.8502 | 1.3745 | -4.0598 | 19.5597 | 106.0000 | 45.8824 |
| expected_slot_days_ranking | time_underwater_exit | QQQ | 2.3358 | 0.4288 | -6.3439 | 18.0476 | 98.0000 | 43.7778 |
| expected_slot_days_ranking | trailing_profit_giveback_exit | SPY | 3.0546 | 0.5770 | -5.8699 | 19.1793 | 105.0000 | 48.1618 |
| expected_slot_days_ranking | trailing_profit_giveback_exit | QQQ | 2.5324 | 0.4409 | -6.2828 | 17.4409 | 95.0000 | 46.7407 |
| corrected_reference_selector | hold_to_te1 | SPY | 0.7082 | 0.1939 | -6.4651 | 14.8213 | 81.0000 | 36.6912 |
| corrected_reference_selector | hold_to_te1 | QQQ | 1.6169 | 0.3664 | -5.0816 | 12.5690 | 68.0000 | 33.1111 |
| corrected_reference_selector | fixed_2_day | SPY | 1.5582 | 0.5373 | -2.5866 | 25.0703 | 136.0000 | 20.1471 |
| corrected_reference_selector | fixed_2_day | QQQ | -0.5319 | -0.1316 | -4.0734 | 20.5776 | 111.0000 | 16.5185 |
| corrected_reference_selector | fixed_4_day | SPY | 1.7525 | 0.4965 | -2.8047 | 16.7656 | 91.0000 | 25.6618 |
| corrected_reference_selector | fixed_4_day | QQQ | 1.1010 | 0.3123 | -3.2346 | 14.4912 | 78.0000 | 22.8148 |
| corrected_reference_selector | fixed_8_day | SPY | 0.7532 | 0.2121 | -5.3367 | 15.4383 | 84.0000 | 32.9412 |
| corrected_reference_selector | fixed_8_day | QQQ | 1.6112 | 0.3993 | -3.8949 | 13.3495 | 72.0000 | 30.8148 |
| corrected_reference_selector | corrected_reference_exit | SPY | 3.7947 | 1.0275 | -3.2931 | 18.8299 | 102.0000 | 30.6618 |
| corrected_reference_selector | corrected_reference_exit | QQQ | 3.3876 | 0.8207 | -3.7288 | 15.5866 | 84.0000 | 25.7778 |
| corrected_reference_selector | volatility_scaled_stop | SPY | 2.1736 | 0.5373 | -5.2546 | 15.6063 | 85.0000 | 33.6029 |
| corrected_reference_selector | volatility_scaled_stop | QQQ | 2.0089 | 0.4700 | -4.7546 | 13.8200 | 75.0000 | 32.2963 |
| corrected_reference_selector | time_underwater_exit | SPY | 1.7605 | 0.4387 | -5.2958 | 15.8203 | 86.0000 | 33.0882 |
| corrected_reference_selector | time_underwater_exit | QQQ | -1.3176 | -0.2274 | -5.6962 | 13.2676 | 72.0000 | 30.8148 |
| corrected_reference_selector | trailing_profit_giveback_exit | SPY | 1.7701 | 0.4456 | -5.6662 | 15.3972 | 84.0000 | 35.8824 |
| corrected_reference_selector | trailing_profit_giveback_exit | QQQ | 3.0032 | 0.6746 | -4.4491 | 13.5043 | 73.0000 | 32.1481 |

The volatility stop uses only pre-entry ATR20 to scale its fixed threshold. The time-underwater and trailing-giveback exits are deterministic sequential rules; no threshold grid was searched.

## Fold and event-family stability

For the research-frozen selector, the exact outer-panel results are:

| exit_policy | benchmark | panels | mean_panel_excess | median_panel_excess | positive_panel_share | mean_panel_active_ir | mean_panel_active_drawdown_pct |
| --- | --- | --- | --- | --- | --- | --- | --- |
| corrected_reference_exit | QQQ | 11.0000 | 0.1752 | 0.2809 | 0.5455 | -0.3298 | -0.9520 |
| corrected_reference_exit | SPY | 11.0000 | 0.0173 | 0.2851 | 0.5455 | -0.5020 | -1.0899 |
| fixed_2_day | QQQ | 11.0000 | -0.0498 | -0.3147 | 0.4545 | -1.3819 | -0.5773 |
| fixed_2_day | SPY | 11.0000 | 0.0562 | -0.3290 | 0.4545 | -0.7653 | -0.5752 |
| fixed_4_day | QQQ | 11.0000 | 0.0396 | -0.1524 | 0.4545 | -0.6666 | -0.8557 |
| fixed_4_day | SPY | 11.0000 | -0.0898 | 0.0304 | 0.5455 | -0.7163 | -1.0346 |
| fixed_8_day | QQQ | 11.0000 | 0.0418 | 0.1493 | 0.5455 | -1.0858 | -1.0627 |
| fixed_8_day | SPY | 11.0000 | -0.0650 | -0.3345 | 0.3636 | -0.4169 | -1.1987 |
| hold_to_te1 | QQQ | 11.0000 | 0.6081 | 0.3863 | 0.6364 | -0.5148 | -1.2580 |
| hold_to_te1 | SPY | 11.0000 | 0.4805 | -0.4574 | 0.4545 | 0.4250 | -1.4237 |
| time_underwater_exit | QQQ | 11.0000 | 0.2382 | -0.0827 | 0.4545 | -0.9120 | -1.1552 |
| time_underwater_exit | SPY | 11.0000 | 0.2608 | -0.7025 | 0.3636 | -0.0922 | -1.2990 |
| trailing_profit_giveback_exit | QQQ | 11.0000 | 0.2866 | 0.2684 | 0.6364 | -0.7020 | -1.2233 |
| trailing_profit_giveback_exit | SPY | 11.0000 | 0.2298 | -0.2095 | 0.3636 | 0.1867 | -1.3473 |
| volatility_scaled_stop | QQQ | 11.0000 | 0.2053 | 0.0895 | 0.5455 | -1.0768 | -1.1479 |
| volatility_scaled_stop | SPY | 11.0000 | -0.0111 | -0.7537 | 0.3636 | -0.4610 | -1.3147 |

SPY panel medians remain weaker than QQQ for several exits, so the combined-stream gains should not be interpreted as uniform temporal dominance. The complete 880-row file reports every selector, exit, benchmark, and OOF panel separately.

Event-family contribution for the frozen selector, averaged across the eight exit policies:

| benchmark | event_family | exit_policies | mean_trade_count | mean_net_pnl_across_exits | mean_trade_pnl_pct_across_exits | mean_win_rate_across_exits |
| --- | --- | --- | --- | --- | --- | --- |
| QQQ | earnings | 8.0000 | 69.2500 | 1321.0312 | 0.1961 | 0.5590 |
| QQQ | geo | 8.0000 | 3.7500 | -423.8675 | -1.4095 | 0.4188 |
| QQQ | other | 8.0000 | 8.0000 | 1232.4075 | 1.6636 | 0.5781 |
| SPY | earnings | 8.0000 | 82.6250 | 1191.3275 | 0.1648 | 0.5394 |
| SPY | geo | 8.0000 | 5.0000 | -702.9237 | -1.7181 | 0.3735 |
| SPY | other | 8.0000 | 9.0000 | 1346.2875 | 1.6217 | 0.5833 |

Geo remains the weak lane across both benchmarks. The strong long-horizon combined return is concentrated partly in the small `other` catalyst family; this concentration is retained as an explicit Stage 3 risk rather than used to alter the frozen Stage 2C semantic rules.

## Selector robustness across exits

| selector | robust_rank_score | median_composite_rank | q75_composite_rank | mean_excess_return | mean_active_ir | mean_active_drawdown_pct | robust_selector_order |
| --- | --- | --- | --- | --- | --- | --- | --- |
| target_b_per_slot_day | 2.1667 | 2.0000 | 2.3333 | 2.3181 | 0.4413 | -4.1218 | 1.0000 |
| corrected_reference_selector | 2.7500 | 2.1667 | 3.3333 | 1.5719 | 0.4108 | -4.4760 | 2.0000 |
| expected_slot_days_ranking | 3.1250 | 2.8333 | 3.4167 | 2.0471 | 0.3406 | -6.0240 | 3.0000 |
| predicted_target_a_positive | 3.2083 | 3.0833 | 3.3333 | -0.0403 | -0.0401 | -3.6423 | 4.0000 |
| direct_always_fill | 4.9167 | 4.8333 | 5.0000 | -1.4170 | -0.2909 | -8.0819 | 5.0000 |

The best selector changed materially across exit policies: `True`. Dominant exit-policy winner share: 50.0%. Median pairwise Spearman correlation of selector ranks across exits: 0.616.

The robust rule gives equal weight to excess return, active IR, and less-negative active drawdown within each benchmark/exit cell, then minimizes 50% median plus 50% 75th-percentile rank across all cells. It therefore does not freeze a selector merely for winning hold-to-`T_e-1`.

Development-only alternating diagnostic:

| iteration | frozen_side | selected_selector | selected_exit_policy |
| --- | --- | --- | --- |
| 0 | all_predeclared_exit_baselines | target_b_per_slot_day | hold_to_te1 |
| 1 | exit_then_selector | target_b_per_slot_day | hold_to_te1 |
| 2 | exit_then_selector | target_b_per_slot_day | hold_to_te1 |

## Stage 2E freeze and Stage 3 status

Research-frozen performance selector: `target_b_per_slot_day`. Direct ranking: OOF predicted active return per slot-day descending then expected slot days. Direct admission: OOF predicted Target B per slot-day > 0.

Target B itself remains a terminal-derived label (`T_e-1` active return divided by slot days). Stage 2E freezes the selector because its OOF decisions were comparatively robust across the predeclared exit matrix—not because Target B has become a path-aware or causal label.

The indirect semantic lane remains unchanged. No exit policy is frozen by this branch. Learned binary hold/close research may use the Stage 2E selector only after this interaction report is accepted; training remains paused in the generated Stage 3 status manifest. The later lockbox remains sealed for one final full-pipeline evaluation.
