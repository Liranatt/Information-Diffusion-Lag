# Stage 3B — relative-follow-through falsification test

Target-B selection remains frozen.  The new rule has exactly one decision: after the first complete post-entry session, keep the position only if its stock return minus benchmark return is at least +1%; otherwise exit at the following regular-session open.  The decision uses only the prior close and is timestamped before the fill.  The trailing/profit-lock/Polymarket-invalidating reference machinery remains unchanged after confirmation.

## Confirmation-state coverage

| benchmark | planned_exit_reason | candidates |
| --- | --- | --- |
| QQQ | poly_preopen<0.55 | 4 |
| QQQ | profit_lock_3% | 20 |
| QQQ | profit_lock_4% | 12 |
| QQQ | profit_lock_5% | 4 |
| QQQ | profit_lock_6% | 3 |
| QQQ | profit_lock_7% | 2 |
| QQQ | profit_lock_9% | 1 |
| QQQ | relative_follow_through_fail_next_open | 110 |
| QQQ | resolution-1d_close | 25 |
| QQQ | trailing_3.65ATR | 4 |
| SPY | poly_preopen<0.55 | 7 |
| SPY | profit_lock_3% | 24 |
| SPY | profit_lock_4% | 13 |
| SPY | profit_lock_5% | 7 |
| SPY | profit_lock_6% | 3 |
| SPY | profit_lock_7% | 3 |
| SPY | profit_lock_8% | 2 |
| SPY | profit_lock_9% | 1 |
| SPY | relative_follow_through_fail_next_open | 134 |
| SPY | resolution-1d_close | 32 |
| SPY | trailing_3.65ATR | 3 |

## Exact timestamp-safe replay

| arm | benchmark | total_return | benchmark_return | excess_return | active_max_drawdown_pct | n_trades | win_rate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A_corrected_reference_execution_safe | SPY | 0.2057 | 1.2859 | -1.0802 | -4.2997 | 50 | 64.0000 |
| A_corrected_reference_execution_safe | QQQ | 2.3837 | 1.4856 | 0.8981 | -4.3518 | 56 | 66.0714 |
| C_relative_follow_through_confirmation | SPY | -1.2213 | 1.2859 | -2.5072 | -4.1712 | 53 | 45.2830 |
| C_relative_follow_through_confirmation | QQQ | 2.1555 | 1.4856 | 0.6699 | -3.5523 | 58 | 58.6207 |

## Paired chronological folds (C minus corrected reference A)

| outer_fold | benchmark | excess_return_a_pct | excess_return_c_pct | active_max_drawdown_a_pct | active_max_drawdown_c_pct | excess_return_delta_c_minus_a_pct | active_drawdown_delta_c_minus_a_pct |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | QQQ | 0.5660 | 0.1573 | -0.1411 | -0.2583 | -0.4087 | -0.1172 |
| 0 | SPY | 0.1857 | -0.7383 | -0.4451 | -0.7300 | -0.9240 | -0.2849 |
| 1 | QQQ | -3.3519 | -3.2558 | -4.2352 | -3.2560 | 0.0961 | 0.9792 |
| 1 | SPY | -1.4843 | -2.1349 | -2.4911 | -2.3133 | -0.6506 | 0.1778 |
| 2 | QQQ | -0.4283 | 1.9009 | -1.5424 | -0.2119 | 2.3292 | 1.3305 |
| 2 | SPY | -1.8062 | -0.5188 | -1.8219 | -1.3910 | 1.2874 | 0.4309 |
| 3 | QQQ | 1.7480 | 1.7618 | -1.6677 | -0.8937 | 0.0138 | 0.7740 |
| 3 | SPY | 0.1982 | 0.3954 | -0.6475 | -0.2348 | 0.1972 | 0.4127 |
| 4 | QQQ | 2.4940 | 0.1243 | -0.8256 | -0.7292 | -2.3697 | 0.0965 |
| 4 | SPY | 1.8985 | 0.4910 | -1.0342 | -0.9466 | -1.4075 | 0.0876 |

## Decision

**NO_VALIDATED_EARNINGS_ALGORITHM**

Promotion requires positive total active excess, positive paired median improvement, at least three improving folds, and no material paired drawdown deterioration in both SPY and QQQ.  A pass only means this specific, timestamp-safe replay passed; it is not a live-capital authorization.

```json
{
  "SPY": {
    "combined_absolute_excess_pct": -2.5072,
    "paired_median_excess_delta_c_minus_a_pct": -0.6506000000000001,
    "positive_improvement_folds": 2,
    "paired_median_active_drawdown_delta_c_minus_a_pct": 0.17783738691561357,
    "passed": false
  },
  "QQQ": {
    "combined_absolute_excess_pct": 0.6699,
    "paired_median_excess_delta_c_minus_a_pct": 0.013800000000000034,
    "positive_improvement_folds": 3,
    "paired_median_active_drawdown_delta_c_minus_a_pct": 0.7740446167450621,
    "passed": true
  }
}
```
