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
| Passed T1+T2+T3+T4 entry rules | 9 |
| Valid for T-1 primary test | 7 |
| Invalid: missing ex-ante T | 0 |
| Invalid: entry >= T-1 | 2 |
| Invalid: missing prices | 0 |
| Invalid: bad price | 0 |
| Rejected: no clean signal side | 0 |
| Rejected: below entry threshold | 1 |
| Rejected: prob surge exceeded | 3 |
| Rejected: price runup exceeded | 0 |
| Rejected: no probability data | 0 |
| Rejected: no policy available | 0 |

- Earliest entry date: 2026-01-20
- Latest exit date: 2026-01-30

## Primary: Candidate-Level Results

| Metric | Value |
| --- | --- |
| N trades | 7 |
| Mean gross return | 2.4406% |
| Median gross return | 2.6717% |
| Mean net return | 2.3230% |
| Median net return | 2.5529% |
| Win rate (net > 0) | 100.0000% |
| Total net PnL ($10k each) | $1,621.21 |
| Total gross PnL ($10k each) | $1,703.31 |

## Symbol-Day Collapsed Results

| Metric | Value |
| --- | --- |
| N trades | 7 |
| Mean gross return | 2.4406% |
| Median gross return | 2.6717% |
| Mean net return | 2.3230% |
| Median net return | 2.5529% |
| Win rate (net > 0) | 100.0000% |
| Total net PnL ($10k each) | $1,621.21 |

## Event-Level Results (equal-weighted by market)

| Metric | Value |
| --- | --- |
| N events | 7 |
| Mean event-avg net return | 2.3230% |
| Median event-avg net return | 2.5529% |
| Mean event-avg gross return | 2.4406% |
| Median event-avg gross return | 2.6717% |
| Win rate (event mean net > 0) | 100.0000% |

## Monthly Results

| Month | N | Mean Net Ret | Median Net Ret | Win Rate | Net PnL ($10k) |
| --- | --- | --- | --- | --- | --- |
| 2026-01 | 7 | 2.3230% | 2.5529% | 100.00% | $1,621 |

## Robustness

### candidate_level

| Metric | Value |
| --- | --- |
| n_trades | 7 |
| mean_gross_return | 0.024406 |
| median_gross_return | 0.026717 |
| mean_net_return | 0.023230 |
| median_net_return | 0.025529 |
| total_net_pnl_at_10k_each | 1,621.21 |
| win_rate_net_return_gt_0 | 1.000000 |
| binomial_p_value_greater_than_50pct | 0.007812 |
| one_sample_ttest_p_value_mean_net_return_gt_0 | 0.000166 |
| bootstrap_p_value_mean_net_return_gt_0 | 0.000000 |
| event_cluster_bootstrap_p_value | 0.00 |
| mean_net_return_after_removing_top_1pct | 0.021320 |
| mean_net_return_after_removing_top_5pct | 0.021320 |
| mean_net_return_after_removing_top_10pct | 0.021320 |
| median_net_return_after_removing_top_5pct | 0.021146 |
| share_of_total_pnl_from_top_1pct | 0.213800 |
| share_of_total_pnl_from_top_5pct | 0.213800 |
| share_of_total_pnl_from_top_10pct | 0.213800 |

### symbol_day_collapsed

| Metric | Value |
| --- | --- |
| n_trades | 7 |
| mean_gross_return | 0.024406 |
| median_gross_return | 0.026717 |
| mean_net_return | 0.023230 |
| median_net_return | 0.025529 |
| total_net_pnl_at_10k_each | 1,621.21 |
| win_rate_net_return_gt_0 | 1.000000 |
| binomial_p_value_greater_than_50pct | 0.007812 |
| one_sample_ttest_p_value_mean_net_return_gt_0 | 0.000166 |
| bootstrap_p_value_mean_net_return_gt_0 | 0.000000 |
| event_cluster_bootstrap_p_value | 0.00 |
| mean_net_return_after_removing_top_1pct | 0.021320 |
| mean_net_return_after_removing_top_5pct | 0.021320 |
| mean_net_return_after_removing_top_10pct | 0.021320 |
| median_net_return_after_removing_top_5pct | 0.021146 |
| share_of_total_pnl_from_top_1pct | 0.213800 |
| share_of_total_pnl_from_top_5pct | 0.213800 |
| share_of_total_pnl_from_top_10pct | 0.213800 |

## Top 20 Winners (by net return)

| Rank | Symbol | Entry | Exit | Net Ret | Net PnL | Question |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | EWD | 2026-01-20 | 2026-01-30 | 3.4685% | $347 | Will Trump’s Greenland Tariffs go into effect for Sweden by ... |
| 2 | EWU | 2026-01-20 | 2026-01-30 | 3.1461% | $314 | Will Trump’s Greenland Tariffs go into effect for The United... |
| 3 | EWN | 2026-01-20 | 2026-01-30 | 2.5723% | $256 | Will Trump’s Greenland Tariffs go into effect for Norway by ... |
| 4 | EFNL | 2026-01-20 | 2026-01-30 | 2.5529% | $254 | Will Trump’s Greenland Tariffs go into effect for Finland by... |
| 5 | EWQ | 2026-01-20 | 2026-01-30 | 1.6763% | $167 | Will Trump’s Greenland Tariffs go into effect for France by ... |
| 6 | EDEN | 2026-01-20 | 2026-01-30 | 1.6246% | $162 | Will Trump’s Greenland Tariffs go into effect for Denmark by... |
| 7 | EWG | 2026-01-20 | 2026-01-30 | 1.2200% | $122 | Will Trump’s Greenland Tariffs go into effect for Germany by... |

## Top 20 Losers (by net return)

| Rank | Symbol | Entry | Exit | Net Ret | Net PnL | Question |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | EWG | 2026-01-20 | 2026-01-30 | 1.2200% | $122 | Will Trump’s Greenland Tariffs go into effect for Germany by... |
| 2 | EDEN | 2026-01-20 | 2026-01-30 | 1.6246% | $162 | Will Trump’s Greenland Tariffs go into effect for Denmark by... |
| 3 | EWQ | 2026-01-20 | 2026-01-30 | 1.6763% | $167 | Will Trump’s Greenland Tariffs go into effect for France by ... |
| 4 | EFNL | 2026-01-20 | 2026-01-30 | 2.5529% | $254 | Will Trump’s Greenland Tariffs go into effect for Finland by... |
| 5 | EWN | 2026-01-20 | 2026-01-30 | 2.5723% | $256 | Will Trump’s Greenland Tariffs go into effect for Norway by ... |
| 6 | EWU | 2026-01-20 | 2026-01-30 | 3.1461% | $314 | Will Trump’s Greenland Tariffs go into effect for The United... |
| 7 | EWD | 2026-01-20 | 2026-01-30 | 3.4685% | $347 | Will Trump’s Greenland Tariffs go into effect for Sweden by ... |

## Top 20 Events by Average Net Return

| Rank | N | Mean Net Ret | Win Rate | Net PnL | Question |
| --- | --- | --- | --- | --- | --- |
| 1 | 1 | 3.4685% | 100% | $347 | Will Trump’s Greenland Tariffs go into effect for Sweden by ... |
| 2 | 1 | 3.1461% | 100% | $314 | Will Trump’s Greenland Tariffs go into effect for The United... |
| 3 | 1 | 2.5723% | 100% | $256 | Will Trump’s Greenland Tariffs go into effect for Norway by ... |
| 4 | 1 | 2.5529% | 100% | $254 | Will Trump’s Greenland Tariffs go into effect for Finland by... |
| 5 | 1 | 1.6763% | 100% | $167 | Will Trump’s Greenland Tariffs go into effect for France by ... |
| 6 | 1 | 1.6246% | 100% | $162 | Will Trump’s Greenland Tariffs go into effect for Denmark by... |
| 7 | 1 | 1.2200% | 100% | $122 | Will Trump’s Greenland Tariffs go into effect for Germany by... |

## Top 20 Events by Negative Average Net Return

| Rank | N | Mean Net Ret | Win Rate | Net PnL | Question |
| --- | --- | --- | --- | --- | --- |
| 1 | 1 | 1.2200% | 100% | $122 | Will Trump’s Greenland Tariffs go into effect for Germany by... |
| 2 | 1 | 1.6246% | 100% | $162 | Will Trump’s Greenland Tariffs go into effect for Denmark by... |
| 3 | 1 | 1.6763% | 100% | $167 | Will Trump’s Greenland Tariffs go into effect for France by ... |
| 4 | 1 | 2.5529% | 100% | $254 | Will Trump’s Greenland Tariffs go into effect for Finland by... |
| 5 | 1 | 2.5723% | 100% | $256 | Will Trump’s Greenland Tariffs go into effect for Norway by ... |
| 6 | 1 | 3.1461% | 100% | $314 | Will Trump’s Greenland Tariffs go into effect for The United... |
| 7 | 1 | 3.4685% | 100% | $347 | Will Trump’s Greenland Tariffs go into effect for Sweden by ... |

## Warnings and Assumptions

- 2 candidates had entry >= T-1 exit
- Fold policies are from the SPY benchmark arm of T1+T2+T3+T4
- Candidates before the first fold window (2025-04-29) use fold 1 policy
- Cost model uses only 2 legs (asset buy + asset sell), no benchmark rotation
- Whole shares only (actual notional may be slightly below $10,000)

## Interpretation

- Mean net return is **positive** (2.3230%)
- Median net return is **positive** (2.5529%)
- Win rate is **above** 50% (100.00%)
- t-test p-value: 0.0002 (passes 0.05)
- Binomial p-value: 0.0078 (passes 0.05)
- Bootstrap p-value: 0.0000
- Event-cluster bootstrap p-value: 0.0000
- Results survive removing top 5% winners (mean after removal: 2.1320%)
- Results survive removing top 10% winners (mean after removal: 2.1320%)
