# Raw Expectation Test: T-1 Exit

## Configuration

- **Input parquet**: `C:\Users\Liran\PycharmProjects\cem_clean_repo\data\tariff_run\tariff_candidates.parquet`
- **Scheduled T column**: `t_e` (Polymarket `end_at`/`requested_end`, set at market creation)
- **Why t_e is ex ante**: `t_e` is the scheduled resolution date published by Polymarket when the market is created. It is publicly known before any candidate entry occurs.
- **Fold policies**: T1+T2+T3+T4, benchmark=SPY, 5 folds
- **Notional per trade**: $10,000
- **Exit rule**: close on last trading day before t_e (T-1)
- **Cost model**: IB commission + SEC fee (sell) + 5bp slippage (both legs)
- **Bootstrap replications**: 20,000
- **Random seed**: 42

## Fold Policies Used

| Fold | Eval Start | Eval End Excl | enter_strong | enter_floor | hold_days | max_prob_surge | max_price_runup |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 2025-05-01 | 2025-08-01 | 0.7361 | 0.6081 | 1 | 0.4470 | 0.1377 |
| 2 | 2025-08-01 | 2025-11-01 | 0.7914 | 0.6452 | 2 | 0.4452 | 0.1446 |
| 3 | 2025-11-01 | 2026-02-01 | 0.7501 | 0.6513 | 1 | 0.3667 | 0.1183 |
| 4 | 2026-02-01 | 2026-05-01 | 0.7000 | 0.6560 | 1 | 0.2260 | 0.1120 |
| 5 | 2026-05-01 | 2026-06-14 | 0.7358 | 0.6568 | 1 | 0.2000 | 0.1533 |

## Filtering Stages

| Stage | Count |
| --- | --- |
| Total candidates loaded | 13 |
| Passed threshold (exist in parquet, >= 0.55) | 13 |
| Passed T1+T2+T3+T4 entry rules | 1 |
| Valid for T-1 primary test | 1 |
| Invalid: missing ex-ante T | 0 |
| Invalid: entry >= T-1 | 0 |
| Invalid: missing prices | 0 |
| Invalid: bad price | 0 |
| Rejected: no clean signal side | 0 |
| Rejected: below entry threshold | 9 |
| Rejected: prob surge exceeded | 3 |
| Rejected: price runup exceeded | 0 |
| Rejected: no probability data | 0 |
| Rejected: no policy available | 0 |

- Earliest entry date: 2025-03-31
- Latest exit date: 2025-04-01

## Primary: Candidate-Level Results

| Metric | Value |
| --- | --- |
| N trades | 1 |
| Mean gross return | -0.5251% |
| Median gross return | -0.5251% |
| Mean net return | -0.6378% |
| Median net return | -0.6378% |
| Win rate (net > 0) | 0.0000% |
| Total net PnL ($10k each) | $-63.41 |
| Total gross PnL ($10k each) | $-52.20 |

## Symbol-Day Collapsed Results

| Metric | Value |
| --- | --- |
| N trades | 1 |
| Mean gross return | -0.5251% |
| Median gross return | -0.5251% |
| Mean net return | -0.6378% |
| Median net return | -0.6378% |
| Win rate (net > 0) | 0.0000% |
| Total net PnL ($10k each) | $-63.41 |

## Event-Level Results (equal-weighted by market)

| Metric | Value |
| --- | --- |
| N events | 1 |
| Mean event-avg net return | -0.6378% |
| Median event-avg net return | -0.6378% |
| Mean event-avg gross return | -0.5251% |
| Median event-avg gross return | -0.5251% |
| Win rate (event mean net > 0) | 0.0000% |

## Monthly Results

| Month | N | Mean Net Ret | Median Net Ret | Win Rate | Net PnL ($10k) |
| --- | --- | --- | --- | --- | --- |
| 2025-03 | 1 | -0.6378% | -0.6378% | 0.00% | $-63 |

## Robustness

### candidate_level

| Metric | Value |
| --- | --- |
| n_trades | 1 |
| mean_gross_return | -0.005251 |
| median_gross_return | -0.005251 |
| mean_net_return | -0.006378 |
| median_net_return | -0.006378 |
| total_net_pnl_at_10k_each | -63.41 |
| win_rate_net_return_gt_0 | 0.000000 |
| binomial_p_value_greater_than_50pct | 1.000000 |
| one_sample_ttest_p_value_mean_net_return_gt_0 | nan |
| bootstrap_p_value_mean_net_return_gt_0 | nan |
| event_cluster_bootstrap_p_value | nan |
| mean_net_return_after_removing_top_1pct | nan |
| mean_net_return_after_removing_top_5pct | nan |
| mean_net_return_after_removing_top_10pct | nan |
| median_net_return_after_removing_top_5pct | nan |
| share_of_total_pnl_from_top_1pct | 1.000000 |
| share_of_total_pnl_from_top_5pct | 1.000000 |
| share_of_total_pnl_from_top_10pct | 1.000000 |

### symbol_day_collapsed

| Metric | Value |
| --- | --- |
| n_trades | 1 |
| mean_gross_return | -0.005251 |
| median_gross_return | -0.005251 |
| mean_net_return | -0.006378 |
| median_net_return | -0.006378 |
| total_net_pnl_at_10k_each | -63.41 |
| win_rate_net_return_gt_0 | 0.000000 |
| binomial_p_value_greater_than_50pct | 1.000000 |
| one_sample_ttest_p_value_mean_net_return_gt_0 | nan |
| bootstrap_p_value_mean_net_return_gt_0 | nan |
| event_cluster_bootstrap_p_value | nan |
| mean_net_return_after_removing_top_1pct | nan |
| mean_net_return_after_removing_top_5pct | nan |
| mean_net_return_after_removing_top_10pct | nan |
| median_net_return_after_removing_top_5pct | nan |
| share_of_total_pnl_from_top_1pct | 1.000000 |
| share_of_total_pnl_from_top_5pct | 1.000000 |
| share_of_total_pnl_from_top_10pct | 1.000000 |

## Top 20 Winners (by net return)

| Rank | Symbol | Entry | Exit | Net Ret | Net PnL | Question |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | EWJ | 2025-03-31 | 2025-04-01 | -0.6378% | $-63 | Will Trump announce tariffs on Japan on April 2? |

## Top 20 Losers (by net return)

| Rank | Symbol | Entry | Exit | Net Ret | Net PnL | Question |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | EWJ | 2025-03-31 | 2025-04-01 | -0.6378% | $-63 | Will Trump announce tariffs on Japan on April 2? |

## Top 20 Events by Average Net Return

| Rank | N | Mean Net Ret | Win Rate | Net PnL | Question |
| --- | --- | --- | --- | --- | --- |
| 1 | 1 | -0.6378% | 0% | $-63 | Will Trump announce tariffs on Japan on April 2? |

## Top 20 Events by Negative Average Net Return

| Rank | N | Mean Net Ret | Win Rate | Net PnL | Question |
| --- | --- | --- | --- | --- | --- |
| 1 | 1 | -0.6378% | 0% | $-63 | Will Trump announce tariffs on Japan on April 2? |

## Warnings and Assumptions

- Fold policies are from the SPY benchmark arm of T1+T2+T3+T4
- Candidates before the first fold window (2025-04-29) use fold 1 policy
- Cost model uses only 2 legs (asset buy + asset sell), no benchmark rotation
- Whole shares only (actual notional may be slightly below $10,000)

## Interpretation

- Mean net return is **negative** (-0.6378%)
- Median net return is **negative** (-0.6378%)
- Win rate is **at or below** 50% (0.00%)
- Binomial p-value: 1.0000 (does not pass 0.10)
