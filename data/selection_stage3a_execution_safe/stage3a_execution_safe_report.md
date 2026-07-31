# Stage 3A — final timestamp-safe Target B exit test

## Design

Target B scores and admissions are frozen from Stage 2F.  A raw Polymarket observation had to be at or above 0.70 after `t_theta` and strictly before the next executable daily opening price.  This is a conservative daily-open test; it does not claim five-minute fill precision.  No raw observation after entry was used to create an entry.

Arm A is the corrected 3.65 ATR trailing/profit-lock exit with a Polymarket invalidation below 0.55 known before the session opens.  Arm B is the pre-registered 2 ATR (2%–8%) volatility loss stop from session two.  There was no parameter search.

## Coverage

- Candidate observations: 415
- Usable timestamp-safe next-open entries: 414
- Entry coverage by status: `{"never_reached_entry_threshold": 1, "usable": 414}`
- Median signal-to-open delay: 17.91 hours

## Exact replay

| arm | benchmark | total_return | benchmark_return | excess_return | active_max_drawdown_pct | n_trades | win_rate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A_corrected_reference_execution_safe | SPY | 0.2057 | 1.2859 | -1.0802 | -4.2997 | 50 | 64.0000 |
| A_corrected_reference_execution_safe | QQQ | 2.3837 | 1.4856 | 0.8981 | -4.3518 | 56 | 66.0714 |
| B_volatility_scaled_stop_execution_safe | SPY | 1.1698 | 1.2859 | -0.1162 | -5.2066 | 49 | 57.1429 |
| B_volatility_scaled_stop_execution_safe | QQQ | 4.9580 | 1.4856 | 3.4724 | -4.1266 | 52 | 59.6154 |

## Paired chronological folds (B minus A)

| outer_fold | benchmark | excess_return_a_pct | excess_return_b_pct | active_max_drawdown_a_pct | active_max_drawdown_b_pct | excess_return_delta_b_minus_a | active_drawdown_delta_b_minus_a |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | QQQ | 0.5660 | 0.2108 | -0.1411 | -0.3846 | -0.3552 | -0.2435 |
| 0 | SPY | 0.1857 | -0.3024 | -0.4451 | -0.4467 | -0.4881 | -0.0016 |
| 1 | QQQ | -3.3519 | -2.6182 | -4.2352 | -3.6983 | 0.7337 | 0.5368 |
| 1 | SPY | -1.4843 | -2.2870 | -2.4911 | -3.2900 | -0.8027 | -0.7989 |
| 2 | QQQ | -0.4283 | -0.7463 | -1.5424 | -1.5368 | -0.3180 | 0.0056 |
| 2 | SPY | -1.8062 | -2.8411 | -1.8219 | -2.8657 | -1.0349 | -1.0439 |
| 3 | QQQ | 1.7480 | 3.6369 | -1.6677 | -1.0897 | 1.8889 | 0.5780 |
| 3 | SPY | 0.1982 | 1.3423 | -0.6475 | -0.5641 | 1.1441 | 0.0834 |
| 4 | QQQ | 2.4940 | 3.7276 | -0.8256 | -0.8256 | 1.2336 | 0.0000 |
| 4 | SPY | 1.8985 | 3.4995 | -1.0342 | -0.3697 | 1.6010 | 0.6645 |

## Decision

**Final exit algorithm: `A_corrected_reference_execution_safe`.**

The challenger could replace the control only if it had positive paired median active-excess improvement, at least three improving folds, and no material median active-drawdown deterioration in both benchmarks.  Gates:

```json
{
  "SPY": {
    "paired_median_excess_delta_b_minus_a_pct": -0.4881,
    "positive_improvement_folds": 2,
    "paired_median_active_drawdown_delta_b_minus_a_pct": -0.0016276868593623206,
    "passed": false
  },
  "QQQ": {
    "paired_median_excess_delta_b_minus_a_pct": 0.7337000000000002,
    "positive_improvement_folds": 3,
    "paired_median_active_drawdown_delta_b_minus_a_pct": 0.005555078643437739,
    "passed": true
  }
}
```

The entry algorithm remains frozen Target B.  This outcome closes selector research and the exit comparison; no further threshold search is justified by this sample.
