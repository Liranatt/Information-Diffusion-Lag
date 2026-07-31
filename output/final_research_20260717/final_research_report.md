# Final quantitative-research handoff

Generated 2026-07-17 from the local chat-artifact manifest and the corrected repository run.

## Completion status

- Repository implementation is complete and the full test suite passes: **69 passed**.
- The corrected execution engine uses OHLC bars, fills overnight stop gaps at the Open, fills intraday stop touches at the standing stop, evaluates protective stops before probability exits, and removes the invalid probability-surge hard gate.
- The corrected full experiment was started with the Open-aware artifact and completed 16 log files across variants baseline, t1_frictionpenalty, t2_trainwindows, t3_kelly, benchmarks QQQ, SPY, and splits test, train. Its final all-arm summary was not reached, so missing arms are not presented as results.
- The completed chat-generated tables below remain useful for exploratory diagnosis, but they are not treated as proof of deployable alpha.

## Main conclusion

The originally reported alpha was largely an execution artifact. After correcting close-only execution, overnight gaps, benchmark rebuys, and the probability-surge gate, the frozen strategy lost its apparent edge. A hard loss cap improved SPY materially, but QQQ remained below benchmark; the connection-strength ranker is promising for SPY and not yet universal. The practical recommendation is a hybrid Sobol+CEM optimizer with hard-cap-only selection, no-follow disabled unless independently revalidated, and strict out-of-sample monitoring.

## Execution correction evidence

| engine | benchmark | return_pct | benchmark_return_pct | excess_return_pct | sharpe | max_dd_pct | n_trades | gap_open_benchmark_rebuys | intraday_stop_close_proxy_rebuys |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| original_flawed_frozen | SPY | 23.648 | 8.520 | 15.128 | 2.833 | -7.104 | 226 | n/a | n/a |
| original_flawed_frozen | QQQ | 29.503 | 17.591 | 11.912 | 2.961 | -7.082 | 210 | n/a | n/a |
| gap_fix_only_frozen | SPY | -1.589 | 8.520 | -10.108 | -0.112 | -12.391 | 225 | n/a | n/a |
| gap_fix_only_frozen | QQQ | 3.760 | 17.591 | -13.831 | 0.515 | -12.916 | 212 | n/a | n/a |
| proper_frozen_close_rebuy | SPY | -1.127 | 8.520 | -9.647 | -0.051 | -12.011 | 221 | 0.000 | 31.000 |
| proper_frozen_close_rebuy | QQQ | 4.455 | 17.591 | -13.136 | 0.588 | -12.556 | 210 | 0.000 | 25.000 |
| proper_frozen_gap_open_rebuy | SPY | 0.233 | 8.520 | -8.287 | 0.125 | -12.381 | 223 | 109.000 | 31.000 |
| proper_frozen_gap_open_rebuy | QQQ | 6.632 | 17.591 | -10.960 | 0.796 | -12.547 | 212 | 108.000 | 25.000 |
| proper_retrained_gap_open_rebuy | SPY | 6.191 | 8.520 | -2.329 | 0.839 | -17.031 | 167 | 53.000 | 24.000 |
| proper_retrained_gap_open_rebuy | QQQ | 2.926 | 17.591 | -14.665 | 0.419 | -15.792 | 153 | 61.000 | 18.000 |

The corrected frozen gap-open-rebuy rows are the relevant comparison: SPY excess was -8.287 percentage points and QQQ excess was -10.960 points, versus the flawed frozen excesses of +15.128 and +11.912 points.

## Policy and optimizer evidence

| benchmark | variant | oos_total_return | oos_benchmark_return | oos_excess_return | oos_overall_ir | oos_active_max_dd_pct | oos_max_dd | oos_n_trades | hard_loss_cap | no_follow_days |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SPY | baseline | 8.244 | 8.520 | -0.276 | -0.040 | -8.311 | -11.326 | 195 | 0.500 | 30 |
| SPY | hard_cap | 10.661 | 8.520 | 2.141 | 0.331 | -6.362 | -8.701 | 216 | 0.060 | 30 |
| SPY | no_follow | 8.244 | 8.520 | -0.276 | -0.040 | -8.311 | -11.326 | 195 | 0.500 | 10 |
| SPY | combined | 10.661 | 8.520 | 2.141 | 0.331 | -6.362 | -8.701 | 216 | 0.060 | 10 |
| QQQ | baseline | 8.167 | 17.591 | -9.424 | -1.408 | -13.571 | -14.457 | 169 | 0.500 | 30 |
| QQQ | hard_cap | 11.194 | 17.591 | -6.398 | -0.958 | -13.540 | -12.272 | 179 | 0.080 | 30 |
| QQQ | no_follow | 8.167 | 17.591 | -9.424 | -1.408 | -13.571 | -14.457 | 169 | 0.500 | 10 |
| QQQ | combined | 11.194 | 17.591 | -6.398 | -0.958 | -13.540 | -12.272 | 179 | 0.080 | 10 |

| benchmark | method | n | train_score_mean | train_score_std | oos_score_mean | oos_score_std | oos_excess_mean | oos_excess_std | oos_ir_mean | oos_active_dd_mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| QQQ | local_sobol | 4 | 0.4153 | 0.0337 | -3.2708 | 0.5493 | -8.8925 | 2.4662 | -1.2705 | -14.5649 |
| QQQ | pure_cem | 4 | 0.3501 | 0.1881 | -3.5465 | 0.7497 | -10.4426 | 3.9506 | -1.4629 | -15.6059 |
| SPY | local_sobol | 4 | 0.3295 | 0.1020 | -2.2838 | 0.2654 | -2.2521 | 0.9993 | -0.3187 | -10.1155 |
| SPY | pure_cem | 4 | 0.3615 | 0.0784 | -2.3062 | 0.5071 | -2.3082 | 2.1005 | -0.3519 | -9.8420 |

| benchmark | variant | runs | train_excess_mean | train_score_mean | oos_excess_mean | oos_excess_median | oos_excess_min | oos_excess_max | oos_ir_mean | oos_active_dd_mean | oos_return_mean | oos_sharpe_mean | positive_excess_runs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| QQQ | baseline | 4 | 6.3539 | 0.4601 | -8.4650 | -9.1026 | -10.1852 | -5.4695 | -1.2970 | -13.5183 | 9.1263 | 1.0408 | 0 |
| QQQ | combined | 4 | 2.6006 | -0.0771 | -5.8643 | -6.0320 | -6.3420 | -5.0509 | -0.8980 | -13.3228 | 11.7270 | 1.3415 | 0 |
| QQQ | hard_cap | 4 | 4.1418 | 0.1472 | -5.8643 | -6.0320 | -6.3420 | -5.0509 | -0.8980 | -13.3228 | 11.7270 | 1.3415 | 0 |
| QQQ | no_follow | 4 | 6.3539 | 0.4601 | -8.4650 | -9.1026 | -10.1852 | -5.4695 | -1.2970 | -13.5183 | 9.1263 | 1.0408 | 0 |
| SPY | baseline | 4 | 6.8771 | 0.4196 | -1.4117 | -1.3056 | -3.4748 | 0.4392 | -0.2140 | -8.9794 | 7.1082 | 0.9502 | 1 |
| SPY | combined | 4 | 3.0067 | -0.0953 | -0.5273 | -0.2125 | -4.7061 | 3.0219 | -0.0898 | -7.6197 | 7.9926 | 1.1329 | 1 |
| SPY | hard_cap | 4 | 4.1918 | 0.0441 | 0.7227 | 2.1036 | -5.5173 | 4.2010 | 0.0635 | -7.5999 | 9.2426 | 1.2465 | 3 |
| SPY | no_follow | 4 | 6.8016 | 0.4034 | -1.5152 | -1.3056 | -3.8887 | 0.4392 | -0.2288 | -9.0663 | 7.0048 | 0.9376 | 1 |

Interpretation: the SPY hard-cap variant had +2.141 percentage points OOS excess in the completed local table and was positive in 3/4 robustness runs; QQQ improved from -9.424 to -6.398 points but still did not beat benchmark. No-follow was inactive or non-incremental in these results.

## Selection and opportunity evidence

| benchmark | analysis_split | event_family | n | selected_n | selected_rate | selected_te1_net_mean | missed_te1_net_mean | selected_te1_win | missed_te1_win | selected_hc_mean | missed_hc_mean | selected_hc_win | missed_hc_win | missed_profitable_te1_n | missed_profitable_hc_n | missed_te1_total_net_10k |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| QQQ | test | earnings | 352.0000 | 164.0000 | 0.4659 | 1.3678 | 0.3611 | 0.5305 | 0.5106 | 0.5441 | 0.2483 | 0.5610 | 0.5532 | 96.0000 | 104.0000 | 6789.2115 |
| QQQ | test | geo | 83.0000 | 10.0000 | 0.1205 | 6.4998 | 7.9981 | 0.8000 | 0.7397 | 1.3394 | -0.3955 | 0.6000 | 0.4521 | 54.0000 | 33.0000 | 58386.1449 |
| QQQ | test | other | 9.0000 | 5.0000 | 0.5556 | -0.1036 | -0.8792 | 0.4000 | 0.0000 | -0.0980 | 0.2827 | 0.6000 | 0.5000 | 0.0000 | 2.0000 | -351.6927 |
| QQQ | train | earnings | 221.0000 | 102.0000 | 0.4615 | 0.3487 | -0.8306 | 0.5588 | 0.4034 | 0.6707 | -0.8200 | 0.6373 | 0.4622 | 48.0000 | 55.0000 | -9884.7238 |
| QQQ | train | geo | 63.0000 | 18.0000 | 0.2857 | -0.3802 | -2.4869 | 0.4444 | 0.3778 | 0.8279 | -0.7931 | 0.6667 | 0.4667 | 17.0000 | 21.0000 | -11190.9505 |
| QQQ | train | other | 12.0000 | 12.0000 | 1.0000 | 5.8215 | n/a | 0.6667 | n/a | 0.2499 | n/a | 0.5833 | n/a | 0.0000 | 0.0000 | 0.0000 |
| SPY | test | earnings | 450.0000 | 207.0000 | 0.4600 | 1.0243 | -0.0636 | 0.5362 | 0.4815 | 0.5769 | -0.2779 | 0.5604 | 0.4897 | 117.0000 | 119.0000 | -1544.8290 |
| SPY | test | geo | 88.0000 | 8.0000 | 0.0909 | 10.1386 | 7.7222 | 0.7500 | 0.7625 | 3.9748 | 0.2025 | 0.6250 | 0.4875 | 61.0000 | 39.0000 | 61777.5125 |
| SPY | test | other | 9.0000 | 1.0000 | 0.1111 | -2.2488 | 0.2166 | 0.0000 | 0.2500 | 1.0021 | 1.1697 | 1.0000 | 0.6250 | 2.0000 | 5.0000 | 173.3068 |
| SPY | train | earnings | 271.0000 | 104.0000 | 0.3838 | 0.5020 | -0.7239 | 0.5769 | 0.4491 | 0.7905 | -0.1454 | 0.6346 | 0.5569 | 75.0000 | 93.0000 | -12088.8646 |
| SPY | train | geo | 60.0000 | 20.0000 | 0.3333 | -1.2492 | -2.6639 | 0.4000 | 0.4500 | 0.2308 | -0.8419 | 0.5500 | 0.5000 | 18.0000 | 20.0000 | -10655.6283 |
| SPY | train | other | 15.0000 | 13.0000 | 0.8667 | 18.0365 | 2.8985 | 0.7692 | 1.0000 | 0.1930 | 3.1638 | 0.6154 | 0.5000 | 2.0000 | 1.0000 | 579.6978 |

| benchmark | ranker | split | total_return | benchmark_return | excess_return | overall_ir | active_max_dd_pct | n_trades |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SPY | current | train | 29.3324 | 25.5179 | 3.8145 | 0.3702 | -4.7789 | 137 |
| SPY | current | test | 10.6613 | 8.5199 | 2.1413 | 0.3313 | -6.3623 | 216 |
| SPY | connection | train | 30.4463 | 25.5179 | 4.9284 | 0.4900 | -4.7791 | 138 |
| SPY | connection | test | 14.2766 | 8.5199 | 5.7567 | 0.8689 | -4.4709 | 213 |
| SPY | geo_first | train | 29.3324 | 25.5179 | 3.8145 | 0.3702 | -4.7789 | 137 |
| SPY | geo_first | test | 9.4650 | 8.5199 | 0.9451 | 0.1490 | -6.1316 | 215 |
| SPY | family_sector_train | train | 32.4600 | 25.5179 | 6.9421 | 0.6637 | -4.7788 | 134 |
| SPY | family_sector_train | test | 10.0098 | 8.5199 | 1.4899 | 0.2230 | -6.3286 | 210 |
| QQQ | current | train | 38.0867 | 33.5766 | 4.5101 | 0.4152 | -5.2216 | 132 |
| QQQ | current | test | 11.1936 | 17.5912 | -6.3976 | -0.9579 | -13.5398 | 179 |
| QQQ | connection | train | 34.9062 | 33.5766 | 1.3296 | 0.1217 | -6.3393 | 131 |
| QQQ | connection | test | 14.4811 | 17.5912 | -3.1101 | -0.4588 | -10.8182 | 181 |
| QQQ | geo_first | train | 38.0867 | 33.5766 | 4.5101 | 0.4152 | -5.2216 | 132 |
| QQQ | geo_first | test | 8.4967 | 17.5912 | -9.0946 | -1.3603 | -15.6338 | 182 |
| QQQ | family_sector_train | train | 39.7010 | 33.5766 | 6.1244 | 0.4787 | -5.7956 | 131 |
| QQQ | family_sector_train | test | 8.1527 | 17.5912 | -9.4385 | -1.3900 | -14.5952 | 181 |

| benchmark | ranker | variant | split | total_return | benchmark_return | excess_return | overall_ir | active_max_dd_pct | n_trades |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SPY | current | current | train | 29.3324 | 25.5179 | 3.8145 | 0.3702 | -4.7789 | 137 |
| SPY | current | current | test | 10.6613 | 8.5199 | 2.1413 | 0.3313 | -6.3623 | 216 |
| SPY | connection | current | train | 30.4463 | 25.5179 | 4.9284 | 0.4900 | -4.7791 | 138 |
| SPY | connection | current | test | 14.2766 | 8.5199 | 5.7567 | 0.8689 | -4.4709 | 213 |
| SPY | connection | geo_latency_2_3_te1 | train | 30.3992 | 25.5179 | 4.8813 | 0.4851 | -4.8189 | 138 |
| SPY | connection | geo_latency_2_3_te1 | test | 16.8931 | 8.5199 | 8.3732 | 1.2029 | -4.0481 | 211 |
| SPY | connection | earnings_te1 | train | 34.2758 | 25.5179 | 8.7579 | 0.7630 | -4.7791 | 118 |
| SPY | connection | earnings_te1 | test | 5.5847 | 8.5199 | -2.9353 | -0.3832 | -16.2908 | 144 |
| QQQ | current | current | train | 38.0867 | 33.5766 | 4.5101 | 0.4152 | -5.2216 | 132 |
| QQQ | current | current | test | 11.1936 | 17.5912 | -6.3976 | -0.9579 | -13.5398 | 179 |
| QQQ | connection | current | train | 34.9062 | 33.5766 | 1.3296 | 0.1217 | -6.3393 | 131 |
| QQQ | connection | current | test | 14.4811 | 17.5912 | -3.1101 | -0.4588 | -10.8182 | 181 |
| QQQ | connection | geo_latency_2_3_te1 | train | 34.8484 | 33.5766 | 1.2717 | 0.1164 | -6.3450 | 131 |
| QQQ | connection | geo_latency_2_3_te1 | test | 14.2634 | 17.5912 | -3.3278 | -0.4863 | -10.9911 | 178 |
| QQQ | connection | earnings_te1 | train | 34.9127 | 33.5766 | 1.3360 | 0.1204 | -7.7274 | 109 |
| QQQ | connection | earnings_te1 | test | 21.1517 | 17.5912 | 3.5604 | 0.4416 | -8.6921 | 134 |

The same-day oracle comparison shows large avoidable selection regret, but it is an upper bound rather than a tradable strategy. Rank-based selection is therefore a hypothesis worth validating, not a production rule.

## Event-specific diagnostics

| split | symbol | n | mean_te1_net_pct | median_te1_net_pct | win_rate |
| --- | --- | --- | --- | --- | --- |
| test | BNO | 6 | 11.2651 | 10.8792 | 1.0000 |
| train | BNO | 5 | 1.9802 | 4.4731 | 0.8000 |
| train | CVX | 3 | -1.0829 | -1.6430 | 0.3333 |
| train | UNG | 1 | -2.9787 | -2.9787 | 0.0000 |
| test | USO | 71 | 12.7748 | 10.5100 | 0.8592 |
| train | USO | 33 | -2.4489 | 0.2710 | 0.5455 |
| train | WEAT | 1 | -0.7446 | -0.7446 | 0.0000 |
| test | XLE | 33 | -0.9426 | -1.3767 | 0.3030 |
| train | XLE | 17 | -1.9667 | -3.0708 | 0.3529 |
| train | XOM | 2 | -3.8049 | -3.8049 | 0.0000 |

| benchmark | split | connection_bin | n | te1_mean_pct | te1_win_rate | hardcap_mean_pct | hardcap_win_rate | active_hardcap_mean_pct | selected_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| QQQ | test | 0.90-<1.00 | 112 | -0.2887 | 0.4464 | -0.0849 | 0.5446 | -1.1419 | 0.3839 |
| QQQ | test | 1.00 | 238 | 1.3688 | 0.5588 | 0.6237 | 0.5672 | -0.0545 | 0.5084 |
| QQQ | test | <0.90 | 2 | -0.6227 | 0.0000 | -1.5090 | 0.0000 | -4.2046 | 0.0000 |
| QQQ | train | 0.90-<1.00 | 82 | -1.6256 | 0.3537 | -0.7280 | 0.4878 | 0.3605 | 0.4634 |
| QQQ | train | 1.00 | 139 | 0.5037 | 0.5468 | 0.2196 | 0.5755 | 0.0468 | 0.4604 |
| SPY | test | 0.90-<1.00 | 138 | -0.2837 | 0.4493 | -0.1316 | 0.5000 | -0.6787 | 0.4058 |
| SPY | test | 1.00 | 308 | 0.7816 | 0.5357 | 0.2377 | 0.5357 | -0.0635 | 0.4838 |
| SPY | test | <0.90 | 4 | -1.2477 | 0.2500 | -0.7903 | 0.2500 | -1.3281 | 0.5000 |
| SPY | train | 0.90-<1.00 | 93 | -2.0608 | 0.3656 | -0.7919 | 0.4946 | -0.1997 | 0.4301 |
| SPY | train | 1.00 | 178 | 0.6908 | 0.5674 | 0.7393 | 0.6348 | 0.4378 | 0.3596 |

The strongest recurring diagnostic is connection strength: full-strength rows were directionally better than 0.90–<1.00 rows in both benchmarks, but the active return remains mixed. Geopolitical results are concentrated in a few symbols and have small samples, so they need symbol-level shrinkage and more history.

## Corrected rerun snapshot

The following table is generated directly from the completed corrected-run equity and trade logs. It is intentionally labeled partial because the run stopped after the first four variants.

| benchmark | variant | split | strategy_return_pct | benchmark_return_pct | excess_return_pct | active_sharpe | max_dd_pct | n_trades | win_rate_pct | mean_pnl_pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| QQQ | baseline | test | -4.999 | 17.651 | -22.650 | -2.983 | -20.844 | 218 | 50.917 | -0.309 |
| QQQ | baseline | train | 39.085 | 33.644 | 5.440 | 0.411 | -22.774 | 143 | 60.839 | 0.558 |
| QQQ | t1_frictionpenalty | test | -6.014 | 17.651 | -23.664 | -3.249 | -20.339 | 182 | 50.549 | -0.354 |
| QQQ | t1_frictionpenalty | train | 43.641 | 33.644 | 9.997 | 0.754 | -22.763 | 128 | 65.625 | 0.782 |
| QQQ | t2_trainwindows | test | 4.308 | 17.651 | -13.343 | -1.894 | -11.410 | 196 | 53.571 | 0.283 |
| QQQ | t2_trainwindows | train | 22.874 | 33.644 | -10.771 | -0.819 | -22.451 | 137 | 56.934 | -0.518 |
| QQQ | t3_kelly | test | 8.039 | 17.651 | -9.612 | -1.663 | -15.357 | 213 | 52.582 | 0.019 |
| QQQ | t3_kelly | train | 38.795 | 33.644 | 5.150 | 0.543 | -22.775 | 145 | 60.690 | 0.555 |
| SPY | baseline | test | -6.099 | 8.575 | -14.674 | -2.969 | -18.278 | 183 | 49.727 | -0.496 |
| SPY | baseline | train | 34.479 | 25.581 | 8.897 | 1.056 | -19.014 | 129 | 64.341 | 0.808 |
| SPY | t1_frictionpenalty | test | -9.550 | 8.575 | -18.125 | -2.938 | -21.188 | 220 | 50.909 | -0.402 |
| SPY | t1_frictionpenalty | train | 34.110 | 25.581 | 8.528 | 0.833 | -19.015 | 146 | 65.068 | 0.654 |
| SPY | t2_trainwindows | test | 2.457 | 8.575 | -6.118 | -0.918 | -12.858 | 179 | 48.603 | 0.184 |
| SPY | t2_trainwindows | train | 16.209 | 25.581 | -9.372 | -0.862 | -18.726 | 153 | 54.248 | -0.441 |
| SPY | t3_kelly | test | -1.926 | 8.575 | -10.501 | -2.392 | -16.165 | 208 | 50.000 | -0.372 |
| SPY | t3_kelly | train | 30.869 | 25.581 | 5.288 | 0.693 | -19.015 | 143 | 62.937 | 0.637 |

## Recommended next research gate

1. Finish the corrected all-arm matrix or deliberately rerun only the pre-registered P0-P3/P4-P9 arms with a saved manifest and deterministic seed list.
2. Freeze a selection rule before looking at the final test rows; compare current rank, connection rank, and a shrinkage family/symbol model.
3. Report confidence intervals and bootstrap-by-event results, not only aggregate trade means.
4. Keep the benchmark rebalancing and target-fill diagnostics in every future run.

## Artifact index

| category | status | path |
| --- | --- | --- |
| execution semantics | completed | C:\Users\Liran\PycharmProjects\cem_clean_repo\assistant_generated_all_artifacts\assistant_generated_research_core\proper_execution_rerun\proper_execution_summary_full.csv |
| local policy fix | completed | C:\Users\Liran\PycharmProjects\cem_clean_repo\assistant_generated_all_artifacts\assistant_generated_research_core\local_quant_fix_summary.csv |
| optimizer comparison | completed | C:\Users\Liran\PycharmProjects\cem_clean_repo\assistant_generated_all_artifacts\assistant_generated_research_core\local_quant_fix_optimizer_aggregate.csv |
| seed robustness | completed | C:\Users\Liran\PycharmProjects\cem_clean_repo\assistant_generated_all_artifacts\assistant_generated_research_core\local_quant_fix_variant_seed_aggregate.csv |
| selected vs missed | completed | C:\Users\Liran\PycharmProjects\cem_clean_repo\assistant_generated_all_artifacts\assistant_generated_research_core\trade_opportunity_research\selected_vs_missed_summary.csv |
| same-day ranker | completed | C:\Users\Liran\PycharmProjects\cem_clean_repo\assistant_generated_all_artifacts\assistant_generated_research_core\trade_opportunity_research\key_same_day_ranker_results.csv |
| family exit | completed | C:\Users\Liran\PycharmProjects\cem_clean_repo\assistant_generated_all_artifacts\assistant_generated_research_core\trade_opportunity_research\key_family_exit_results.csv |
| geopolitical by symbol | completed | C:\Users\Liran\PycharmProjects\cem_clean_repo\assistant_generated_all_artifacts\assistant_generated_research_core\trade_opportunity_research\geo_results_by_symbol.csv |
| earnings connection bins | completed | C:\Users\Liran\PycharmProjects\cem_clean_repo\assistant_generated_all_artifacts\assistant_generated_research_core\trade_opportunity_research\earnings_connection_strength_bins.csv |
| event-collapse bundle | available; train-only bundle | C:\Users\Liran\PycharmProjects\cem_clean_repo\assistant_generated_all_artifacts\event_collapse_selection_research_bundle |
| corrected rerun | partial; P0-P3 only | C:\Users\Liran\PycharmProjects\cem_clean_repo\runs\corrected_full_20260716 |
| corrected prices | used | C:\Users\Liran\PycharmProjects\cem_clean_repo\assistant_generated_all_artifacts\assistant_generated_research_large_supplements\prices_open_merged.pkl |

The machine-readable index is `output_index.csv`; the corrected partial-run table is `corrected_partial_ablation_table.csv` in this output directory.
