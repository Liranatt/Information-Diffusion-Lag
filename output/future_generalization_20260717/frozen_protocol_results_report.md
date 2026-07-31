# Frozen future-generalization protocol results

Generated 2026-07-17 from the completed frozen simulation. `2026H1_pseudo_future` is the latest available historical block; it is a pseudo-future lockbox, not genuinely future data.

This is a selection/capacity simulation using the saved collapsed trade outcomes and fixed $100,000 slot accounting. It is not yet the final hourly corrected-engine portfolio backtest with live benchmark rotation.

## Protocol

- One position per symbol-day.
- Fixed lexicographic rank: connection strength ↓, entry probability ↓, pre-entry run-up ↑, symbol tie-break.
- No fitted weights, thresholds, family-specific rules, benchmark-specific parameters, CEM, or ML.
- Capacities 5/10/15; exits hardcap and T_e−1; costs 0×/1×/2×/3×.

## Chronological sign stability at primary cost

| benchmark | exit_arm | block | capacity_mean_active_pct | capacity_median_active_pct | positive_capacity_count | capacities |
| --- | --- | --- | --- | --- | --- | --- |
| QQQ | hardcap | 2024H2 | 2.313 | 1.892 | 3 | 5,10,15 |
| QQQ | hardcap | 2025H1 | -2.058 | -1.684 | 0 | 5,10,15 |
| QQQ | hardcap | 2025H2 | 5.278 | 5.212 | 3 | 5,10,15 |
| QQQ | hardcap | 2026H1_pseudo_future | -7.572 | -7.496 | 0 | 5,10,15 |
| QQQ | te1 | 2024H2 | -0.456 | -0.373 | 0 | 5,10,15 |
| QQQ | te1 | 2025H1 | -3.374 | -2.761 | 0 | 5,10,15 |
| QQQ | te1 | 2025H2 | 8.605 | 6.003 | 3 | 5,10,15 |
| QQQ | te1 | 2026H1_pseudo_future | 0.927 | 1.303 | 2 | 5,10,15 |
| SPY | hardcap | 2024H2 | 2.215 | 1.813 | 3 | 5,10,15 |
| SPY | hardcap | 2025H1 | -2.529 | -2.402 | 0 | 5,10,15 |
| SPY | hardcap | 2025H2 | 6.170 | 5.895 | 3 | 5,10,15 |
| SPY | hardcap | 2026H1_pseudo_future | -2.355 | -3.247 | 1 | 5,10,15 |
| SPY | te1 | 2024H2 | -0.354 | -0.290 | 0 | 5,10,15 |
| SPY | te1 | 2025H1 | -3.806 | -3.861 | 0 | 5,10,15 |
| SPY | te1 | 2025H2 | 25.015 | 26.506 | 3 | 5,10,15 |
| SPY | te1 | 2026H1_pseudo_future | 11.250 | 8.269 | 3 | 5,10,15 |

A positive result in the latest block is not enough; the frozen rule must be directionally consistent across blocks and capacities.

## Latest pseudo-future block

| benchmark | exit_arm | capacity | n_trades | strategy_return_pct | active_return_pct | win_rate_pct | median_active_pct | max_dd_pct | top_symbol_abs_active_share_pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| QQQ | hardcap | 5 | 97 | 5.766 | -7.819 | 43.299 | -0.972 | -16.651 | 7.060 |
| QQQ | hardcap | 10 | 174 | 7.001 | -7.402 | 47.701 | -0.378 | -11.735 | 4.147 |
| QQQ | hardcap | 15 | 245 | 6.103 | -7.496 | 48.163 | -0.211 | -9.987 | 3.221 |
| QQQ | te1 | 5 | 68 | 16.546 | -1.222 | 51.471 | 0.087 | -10.618 | 9.336 |
| QQQ | te1 | 10 | 131 | 18.210 | 1.303 | 51.145 | 0.009 | -7.553 | 5.158 |
| QQQ | te1 | 15 | 184 | 19.021 | 2.700 | 50.000 | -0.041 | -4.615 | 3.819 |
| SPY | hardcap | 5 | 105 | 9.799 | 3.223 | 50.476 | 0.195 | -14.300 | 7.598 |
| SPY | hardcap | 10 | 194 | 1.505 | -7.040 | 47.423 | -0.282 | -11.752 | 4.318 |
| SPY | hardcap | 15 | 261 | 4.821 | -3.247 | 48.276 | -0.185 | -9.262 | 3.798 |
| SPY | te1 | 5 | 71 | 26.892 | 18.233 | 54.930 | 0.757 | -6.882 | 9.841 |
| SPY | te1 | 10 | 131 | 17.879 | 7.248 | 51.908 | 0.416 | -2.854 | 6.898 |
| SPY | te1 | 15 | 185 | 18.069 | 8.269 | 50.270 | 0.287 | -7.023 | 4.547 |

## Latest-block cost stress

| benchmark | exit_arm | capacity | cost_multiplier | n_trades | strategy_return_pct | active_return_pct | median_active_pct |
| --- | --- | --- | --- | --- | --- | --- | --- |
| QQQ | hardcap | 5 | 0.000 | 97 | 8.114 | -5.471 | -0.862 |
| QQQ | hardcap | 10 | 0.000 | 174 | 9.137 | -5.266 | -0.248 |
| QQQ | hardcap | 15 | 0.000 | 245 | 8.103 | -5.497 | -0.102 |
| QQQ | hardcap | 5 | 1.000 | 97 | 5.766 | -7.819 | -0.972 |
| QQQ | hardcap | 10 | 1.000 | 174 | 7.001 | -7.402 | -0.378 |
| QQQ | hardcap | 15 | 1.000 | 245 | 6.103 | -7.496 | -0.211 |
| QQQ | hardcap | 5 | 2.000 | 97 | 3.419 | -10.167 | -1.083 |
| QQQ | hardcap | 10 | 2.000 | 174 | 4.866 | -9.537 | -0.498 |
| QQQ | hardcap | 15 | 2.000 | 245 | 4.103 | -9.496 | -0.321 |
| QQQ | hardcap | 5 | 3.000 | 97 | 1.071 | -12.515 | -1.193 |
| QQQ | hardcap | 10 | 3.000 | 174 | 2.730 | -11.673 | -0.609 |
| QQQ | hardcap | 15 | 3.000 | 245 | 2.103 | -11.496 | -0.430 |
| QQQ | te1 | 5 | 0.000 | 68 | 18.253 | 0.486 | 0.198 |
| QQQ | te1 | 10 | 0.000 | 131 | 19.850 | 2.943 | 0.162 |
| QQQ | te1 | 15 | 0.000 | 184 | 20.538 | 4.216 | 0.079 |
| QQQ | te1 | 5 | 1.000 | 68 | 16.546 | -1.222 | 0.087 |
| QQQ | te1 | 10 | 1.000 | 131 | 18.210 | 1.303 | 0.009 |
| QQQ | te1 | 15 | 1.000 | 184 | 19.021 | 2.700 | -0.041 |
| QQQ | te1 | 5 | 2.000 | 68 | 14.838 | -2.929 | -0.024 |
| QQQ | te1 | 10 | 2.000 | 131 | 16.570 | -0.337 | -0.110 |
| QQQ | te1 | 15 | 2.000 | 184 | 17.505 | 1.184 | -0.179 |
| QQQ | te1 | 5 | 3.000 | 68 | 13.131 | -4.637 | -0.134 |
| QQQ | te1 | 10 | 3.000 | 131 | 14.931 | -1.977 | -0.222 |
| QQQ | te1 | 15 | 3.000 | 184 | 15.989 | -0.332 | -0.320 |
| SPY | hardcap | 5 | 0.000 | 105 | 12.412 | 5.836 | 0.307 |
| SPY | hardcap | 10 | 0.000 | 194 | 4.033 | -4.512 | -0.160 |
| SPY | hardcap | 15 | 0.000 | 261 | 7.049 | -1.020 | -0.070 |
| SPY | hardcap | 5 | 1.000 | 105 | 9.799 | 3.223 | 0.195 |
| SPY | hardcap | 10 | 1.000 | 194 | 1.505 | -7.040 | -0.282 |
| SPY | hardcap | 15 | 1.000 | 261 | 4.821 | -3.247 | -0.185 |
| SPY | hardcap | 5 | 2.000 | 105 | 7.186 | 0.610 | 0.082 |
| SPY | hardcap | 10 | 2.000 | 194 | -1.022 | -9.567 | -0.401 |
| SPY | hardcap | 15 | 2.000 | 261 | 2.594 | -5.475 | -0.300 |
| SPY | hardcap | 5 | 3.000 | 105 | 4.574 | -2.003 | -0.032 |
| SPY | hardcap | 10 | 3.000 | 194 | -3.549 | -12.095 | -0.513 |
| SPY | hardcap | 15 | 3.000 | 261 | 0.366 | -7.703 | -0.414 |
| SPY | te1 | 5 | 0.000 | 71 | 28.686 | 20.027 | 0.867 |
| SPY | te1 | 10 | 0.000 | 131 | 19.571 | 8.941 | 0.526 |
| SPY | te1 | 15 | 0.000 | 185 | 19.627 | 9.826 | 0.402 |
| SPY | te1 | 5 | 1.000 | 71 | 26.892 | 18.233 | 0.757 |
| SPY | te1 | 10 | 1.000 | 131 | 17.879 | 7.248 | 0.416 |
| SPY | te1 | 15 | 1.000 | 185 | 18.069 | 8.269 | 0.287 |
| SPY | te1 | 5 | 2.000 | 71 | 25.098 | 16.439 | 0.647 |
| SPY | te1 | 10 | 2.000 | 131 | 16.187 | 5.556 | 0.306 |
| SPY | te1 | 15 | 2.000 | 185 | 16.512 | 6.711 | 0.172 |
| SPY | te1 | 5 | 3.000 | 71 | 23.304 | 14.645 | 0.537 |
| SPY | te1 | 10 | 3.000 | 131 | 14.494 | 3.863 | 0.196 |
| SPY | te1 | 15 | 3.000 | 185 | 14.954 | 5.153 | 0.057 |

## Latest-block controls

| benchmark | selection_mode | exit_arm | capacity | n_trades | active_return_pct | median_active_pct |
| --- | --- | --- | --- | --- | --- | --- |
| QQQ | reverse | hardcap | 5 | 98 | -9.079 | -0.739 |
| QQQ | random | hardcap | 5 | 96 | -5.417 | -0.373 |
| QQQ | reverse | hardcap | 10 | 182 | -11.981 | -0.429 |
| QQQ | random | hardcap | 10 | 173 | -12.548 | -0.671 |
| QQQ | reverse | hardcap | 15 | 246 | -7.982 | -0.264 |
| QQQ | random | hardcap | 15 | 248 | -8.433 | -0.170 |
| QQQ | reverse | te1 | 5 | 67 | -7.011 | -0.644 |
| QQQ | random | te1 | 5 | 64 | -0.463 | 0.402 |
| QQQ | reverse | te1 | 10 | 128 | -5.141 | -0.221 |
| QQQ | random | te1 | 10 | 131 | -5.072 | -0.444 |
| QQQ | reverse | te1 | 15 | 184 | -3.241 | -0.568 |
| QQQ | random | te1 | 15 | 188 | 5.146 | 0.300 |
| SPY | reverse | hardcap | 5 | 106 | 2.244 | -0.214 |
| SPY | random | hardcap | 5 | 103 | 2.691 | -0.496 |
| SPY | reverse | hardcap | 10 | 196 | -5.739 | -0.335 |
| SPY | random | hardcap | 10 | 193 | -8.409 | -0.312 |
| SPY | reverse | hardcap | 15 | 261 | -7.714 | -0.358 |
| SPY | random | hardcap | 15 | 254 | -6.488 | -0.452 |
| SPY | reverse | te1 | 5 | 69 | 7.126 | 0.287 |
| SPY | random | te1 | 5 | 69 | 14.340 | -0.582 |
| SPY | reverse | te1 | 10 | 129 | 5.740 | -0.191 |
| SPY | random | te1 | 10 | 129 | 3.165 | -0.324 |
| SPY | reverse | te1 | 15 | 183 | 5.187 | 0.289 |
| SPY | random | te1 | 15 | 181 | 7.444 | 0.568 |

## Corrected placebo distribution

| benchmark | exit_arm | capacity | ranked_active_return_pct | random_mean_active_return_pct | random_p05_active_return_pct | random_p95_active_return_pct | ranked_minus_random_mean_pp | random_beaten_fraction |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| QQQ | hardcap | 5 | -7.819 | -9.328 | -16.992 | -0.221 | 1.509 | 0.680 |
| QQQ | hardcap | 10 | -7.402 | -10.510 | -16.431 | -4.586 | 3.108 | 0.760 |
| QQQ | hardcap | 15 | -7.496 | -8.170 | -10.776 | -5.890 | 0.673 | 0.660 |
| QQQ | te1 | 5 | -1.222 | -0.709 | -10.627 | 10.164 | -0.513 | 0.440 |
| QQQ | te1 | 10 | 1.303 | -2.573 | -10.306 | 2.418 | 3.876 | 0.860 |
| QQQ | te1 | 15 | 2.700 | 0.340 | -3.646 | 3.519 | 2.360 | 0.900 |
| SPY | hardcap | 5 | 3.223 | -0.585 | -12.018 | 8.924 | 3.808 | 0.680 |
| SPY | hardcap | 10 | -7.040 | -6.560 | -12.996 | -0.637 | -0.480 | 0.440 |
| SPY | hardcap | 15 | -3.247 | -6.193 | -10.678 | -2.829 | 2.946 | 0.900 |
| SPY | te1 | 5 | 18.233 | 8.636 | -5.836 | 19.213 | 9.597 | 0.880 |
| SPY | te1 | 10 | 7.248 | 5.704 | -0.159 | 11.299 | 1.544 | 0.680 |
| SPY | te1 | 15 | 8.269 | 6.012 | 1.999 | 10.330 | 2.257 | 0.740 |

## Reading the result

The primary rule is considered future-compatible only if its sign survives chronological blocks, capacity changes, cost stress, and comparison with random same-day selection. A single positive pseudo-future arm is evidence of possibility, not evidence of deployment readiness.

The next genuine test must be run on observations after the current data end. This protocol is now frozen; the future period must not be used to choose capacity, exit arm, or ranking logic.

## Outputs

- `frozen_protocol_results.csv` — all completed arms.
- `chronological_block_summary.csv` — sign stability by block.
- `pseudo_future_placebo_summary.csv` — corrected random same-day control distribution.
- `frozen_protocol_report.md` — original protocol specification.
