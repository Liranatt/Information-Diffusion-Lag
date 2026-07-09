# Raw Expectation Test: T-1 Exit

## Configuration

- **Input parquet**: `C:\Users\Liran\PycharmProjects\cem_clean_repo\data\candidates.parquet`
- **Scheduled T column**: `t_e` (Polymarket `end_at`/`requested_end`, set at market creation)
- **Why t_e is ex ante**: `t_e` is the scheduled resolution date published by Polymarket when the market is created. It is publicly known before any candidate entry occurs.
- **Fold policies**: T1+T2+T3+T4, benchmark=SPY, 5 folds
- **Notional per trade**: $10,000
- **Exit rule**: close on last trading day before t_e (T-1)
- **Cost model**: IB commission + SEC fee (sell) + 5bp slippage (both legs)
- **Bootstrap replications**: 10,000
- **Random seed**: 42

## Fold Policies Used

| Fold | Eval Start | Eval End Excl | enter_strong | enter_floor | hold_days | max_prob_surge | max_price_runup |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 2025-04-29 | 2025-07-29 | 0.7465 | 0.7391 | 1 | 0.3828 | 0.0830 |
| 2 | 2025-07-29 | 2025-10-29 | 0.7950 | 0.7557 | 2 | 0.6163 | 0.0567 |
| 3 | 2025-10-29 | 2026-01-29 | 0.7441 | 0.7441 | 2 | 0.4476 | 0.1005 |
| 4 | 2026-01-29 | 2026-04-29 | 0.7194 | 0.7194 | 2 | 0.4673 | 0.1747 |
| 5 | 2026-04-29 | 2026-06-14 | 0.7104 | 0.7104 | 1 | 0.4700 | 0.1423 |

## Filtering Stages

| Stage | Count |
| --- | --- |
| Total candidates loaded | 1192 |
| Passed threshold (exist in parquet, >= 0.55) | 1192 |
| Passed T1+T2+T3+T4 entry rules | 963 |
| Valid for T-1 primary test | 890 |
| Invalid: missing ex-ante T | 0 |
| Invalid: entry >= T-1 | 73 |
| Invalid: missing prices | 0 |
| Invalid: bad price | 0 |
| Rejected: below entry threshold | 205 |
| Rejected: prob surge exceeded | 22 |
| Rejected: price runup exceeded | 2 |
| Rejected: no probability data | 0 |
| Rejected: no policy available | 0 |

- Earliest entry date: 2024-09-25
- Latest exit date: 2026-06-29

## Primary: Candidate-Level Results

| Metric | Value |
| --- | --- |
| N trades | 890 |
| Mean gross return | 1.4741% |
| Median gross return | 0.3836% |
| Mean net return | 1.3497% |
| Median net return | 0.2710% |
| Win rate (net > 0) | 52.3596% |
| Total net PnL ($10k each) | $119,788.27 |
| Total gross PnL ($10k each) | $130,788.48 |

## Symbol-Day Collapsed Results

| Metric | Value |
| --- | --- |
| N trades | 763 |
| Mean gross return | 0.7744% |
| Median gross return | 0.1246% |
| Mean net return | 0.6490% |
| Median net return | 0.0103% |
| Win rate (net > 0) | 50.0655% |
| Total net PnL ($10k each) | $49,498.41 |

## Event-Level Results (equal-weighted by market)

| Metric | Value |
| --- | --- |
| N events | 808 |
| Mean event-avg net return | 1.2709% |
| Median event-avg net return | 0.3364% |
| Mean event-avg gross return | 1.3958% |
| Median event-avg gross return | 0.4532% |
| Win rate (event mean net > 0) | 53.2178% |

## Monthly Results

| Month | N | Mean Net Ret | Median Net Ret | Win Rate | Net PnL ($10k) |
| --- | --- | --- | --- | --- | --- |
| 2024-09 | 1 | 8.9150% | 8.9150% | 100.00% | $889 |
| 2024-10 | 2 | -2.5532% | -2.5532% | 0.00% | $-509 |
| 2025-06 | 52 | -2.9786% | -0.6910% | 50.00% | $-15,444 |
| 2025-07 | 16 | 7.3643% | 1.4853% | 75.00% | $11,754 |
| 2025-08 | 3 | 29.1586% | 44.5334% | 66.67% | $8,747 |
| 2025-09 | 21 | -0.3688% | 0.0103% | 52.38% | $-763 |
| 2025-10 | 123 | -0.6593% | -0.3594% | 43.09% | $-8,002 |
| 2025-11 | 83 | 0.0582% | 0.0808% | 53.01% | $501 |
| 2025-12 | 17 | 1.7317% | 1.7816% | 76.47% | $2,934 |
| 2026-01 | 92 | -1.3319% | -0.4991% | 43.48% | $-12,073 |
| 2026-02 | 104 | -0.8082% | -0.4781% | 46.15% | $-8,372 |
| 2026-03 | 124 | 8.2624% | 6.5933% | 68.55% | $101,886 |
| 2026-04 | 182 | 1.4477% | 0.4326% | 51.10% | $26,271 |
| 2026-05 | 56 | 4.6128% | 2.8948% | 64.29% | $25,670 |
| 2026-06 | 14 | -9.8689% | -8.6020% | 14.29% | $-13,701 |

## Robustness

### candidate_level

| Metric | Value |
| --- | --- |
| n_trades | 890 |
| mean_gross_return | 0.014741 |
| median_gross_return | 0.003836 |
| mean_net_return | 0.013497 |
| median_net_return | 0.002710 |
| total_net_pnl_at_10k_each | 119,788.27 |
| win_rate_net_return_gt_0 | 0.523600 |
| binomial_p_value_greater_than_50pct | 0.084653 |
| one_sample_ttest_p_value_mean_net_return_gt_0 | 0.000002 |
| bootstrap_p_value_mean_net_return_gt_0 | 0.000000 |
| event_cluster_bootstrap_p_value | 0.00 |
| mean_net_return_after_removing_top_1pct | 0.009378 |
| mean_net_return_after_removing_top_5pct | -0.000139 |
| mean_net_return_after_removing_top_10pct | -0.007704 |
| median_net_return_after_removing_top_5pct | -0.000462 |
| share_of_total_pnl_from_top_1pct | 0.312100 |
| share_of_total_pnl_from_top_5pct | 1.007700 |
| share_of_total_pnl_from_top_10pct | 1.510900 |

### symbol_day_collapsed

| Metric | Value |
| --- | --- |
| n_trades | 763 |
| mean_gross_return | 0.007744 |
| median_gross_return | 0.001246 |
| mean_net_return | 0.006490 |
| median_net_return | 0.000103 |
| total_net_pnl_at_10k_each | 49,498.41 |
| win_rate_net_return_gt_0 | 0.500700 |
| binomial_p_value_greater_than_50pct | 0.500000 |
| one_sample_ttest_p_value_mean_net_return_gt_0 | 0.008819 |
| bootstrap_p_value_mean_net_return_gt_0 | 0.012700 |
| event_cluster_bootstrap_p_value | 0.01 |
| mean_net_return_after_removing_top_1pct | 0.002587 |
| mean_net_return_after_removing_top_5pct | -0.005278 |
| mean_net_return_after_removing_top_10pct | -0.010935 |
| median_net_return_after_removing_top_5pct | -0.003448 |
| share_of_total_pnl_from_top_1pct | 0.603900 |
| share_of_total_pnl_from_top_5pct | 1.763500 |
| share_of_total_pnl_from_top_10pct | 2.502100 |

## Top 20 Winners (by net return)

| Rank | Symbol | Entry | Exit | Net Ret | Net PnL | Question |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | USO | 2026-03-02 | 2026-03-30 | 48.7681% | $4,847 | Will the US not strike Iran by March 31, 2026? |
| 2 | USO | 2026-03-02 | 2026-03-30 | 48.7681% | $4,847 | Will Iran strike gulf oil facilities by March 31? |
| 3 | PGEN | 2025-08-15 | 2025-08-26 | 44.5334% | $4,453 | FDA approves Precigen’s PRGN-2012 for recurrent respiratory ... |
| 4 | PGEN | 2025-08-15 | 2025-08-26 | 44.5334% | $4,453 | FDA approves Precigen’s PRGN-2012 for recurrent respiratory ... |
| 5 | USO | 2026-03-03 | 2026-03-30 | 43.8020% | $4,346 | Will another country strike Iran by March 31? |
| 6 | TNXP | 2025-07-29 | 2025-08-14 | 39.6205% | $3,952 | FDA approves Tonix Pharmaceuticals’ TNX-102 SL for fibromyal... |
| 7 | TNXP | 2025-07-29 | 2025-08-14 | 39.6205% | $3,952 | FDA approves Tonix Pharmaceuticals’ TNX-102 SL for fibromyal... |
| 8 | USO | 2026-03-25 | 2026-04-29 | 32.7153% | $3,264 | Will Iran conduct a military action against Israel on April ... |
| 9 | USO | 2026-03-25 | 2026-04-29 | 32.7153% | $3,264 | Will Iran strike UAE by April 30, 2026? |
| 10 | USO | 2026-03-25 | 2026-04-29 | 32.7153% | $3,264 | Will Iran strike Saudi Arabia by April 30, 2026? |
| 11 | USO | 2026-03-25 | 2026-04-29 | 32.7153% | $3,264 | Will Iran strike Kuwait by April 30, 2026? |
| 12 | HPE | 2026-05-19 | 2026-05-29 | 31.8025% | $3,174 | Will Hewlett Packard Enterprise (HPE) beat quarterly earning... |
| 13 | USO | 2026-03-24 | 2026-04-29 | 31.3822% | $3,127 | Will Iran strike Israel by April 30, 2026? |
| 14 | GTLB | 2026-05-27 | 2026-06-01 | 27.2696% | $2,726 | Will GitLab (GTLB) beat quarterly earnings? |
| 15 | AKAM | 2026-04-27 | 2026-05-06 | 27.0412% | $2,698 | Will Akamai Technologies (AKAM) beat quarterly earnings? |
| 16 | DELL | 2026-05-20 | 2026-05-27 | 25.5589% | $2,546 | Will Dell Technologies (DELL) beat quarterly earnings? |
| 17 | CRCL | 2026-04-30 | 2026-05-08 | 24.9533% | $2,495 | Will Circle Internet (CRCL) beat quarterly earnings? |
| 18 | USO | 2026-03-02 | 2026-03-06 | 24.6267% | $2,448 | Will Iran strike Israel in March? |
| 19 | USO | 2026-03-02 | 2026-03-06 | 24.6267% | $2,448 | Will Iran strike Saudi Arabia in March? |
| 20 | USO | 2026-03-02 | 2026-03-06 | 24.6267% | $2,448 | Will Iran strike Bahrain in March? |

## Top 20 Losers (by net return)

| Rank | Symbol | Entry | Exit | Net Ret | Net PnL | Question |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | USO | 2026-05-18 | 2026-06-29 | -28.3688% | $-2,795 | Strait of Hormuz traffic returns to normal by end of June? |
| 2 | USO | 2026-06-03 | 2026-06-29 | -24.0785% | $-2,374 | Will Jared Kushner have a diplomatic meeting with Iran by Ju... |
| 3 | USO | 2026-06-03 | 2026-06-29 | -24.0785% | $-2,374 | Will Jared Kushner attend the next US x Iran diplomatic meet... |
| 4 | RDDT | 2026-01-27 | 2026-02-04 | -22.3142% | $-2,190 | Will Reddit (RDDT) beat quarterly earnings? |
| 5 | USO | 2026-06-08 | 2026-06-29 | -20.3731% | $-2,025 | Will Steve Witkoff have a diplomatic meeting with Iran by Ju... |
| 6 | USO | 2026-06-08 | 2026-06-29 | -20.3731% | $-2,025 | Will Steve Witkoff attend the next US x Iran diplomatic meet... |
| 7 | GETY | 2026-03-04 | 2026-03-13 | -20.0037% | $-2,000 | Will Getty Images Holdings (GETY) beat quarterly earnings? |
| 8 | USO | 2026-04-24 | 2026-06-29 | -19.2236% | $-1,909 | Will J.D. Vance attend the next US x Iran diplomatic meeting... |
| 9 | M | 2026-02-24 | 2026-03-17 | -18.8975% | $-1,889 | Will Macy's (M) beat quarterly earnings? |
| 10 | EFX | 2026-01-26 | 2026-02-03 | -18.4880% | $-1,824 | Will Equifax Inc (EFX) beat quarterly earnings? |
| 11 | SPGI | 2026-01-29 | 2026-02-09 | -16.0751% | $-1,530 | Will S&P Global (SPGI) beat quarterly earnings? |
| 12 | PINS | 2026-01-29 | 2026-02-11 | -14.7125% | $-1,470 | Will Pinterest (PINS) beat quarterly earnings? |
| 13 | PZZA | 2025-11-03 | 2025-11-05 | -14.6675% | $-1,466 | Will Papa John’s International (PZZA) beat quarterly earning... |
| 14 | HOOD | 2026-01-29 | 2026-02-09 | -14.6024% | $-1,449 | Will Robinhood Markets (HOOD) beat quarterly earnings? |
| 15 | EXPE | 2026-01-29 | 2026-02-11 | -14.4624% | $-1,420 | Will Expedia Group (EXPE) beat quarterly earnings? |
| 16 | CRL | 2026-02-05 | 2026-02-17 | -13.8043% | $-1,369 | Will Charles River Laboratories International (CRL) beat qua... |
| 17 | AMPL | 2026-02-05 | 2026-02-17 | -13.4610% | $-1,345 | Will Amplitude (AMPL) beat quarterly earnings? |
| 18 | MRNA | 2025-10-31 | 2025-11-05 | -13.3763% | $-1,337 | Will Moderna (MRNA) beat quarterly earnings? |
| 19 | USO | 2026-05-04 | 2026-05-29 | -12.6498% | $-1,251 | Will Donald Trump announce that the United States blockade o... |
| 20 | POWL | 2025-11-11 | 2025-11-17 | -12.6053% | $-1,251 | Will Powell Industries (POWL) beat quarterly earnings? |

## Top 20 Events by Average Net Return

| Rank | N | Mean Net Ret | Win Rate | Net PnL | Question |
| --- | --- | --- | --- | --- | --- |
| 1 | 1 | 48.7681% | 100% | $4,847 | Will the US not strike Iran by March 31, 2026? |
| 2 | 1 | 48.7681% | 100% | $4,847 | Will Iran strike gulf oil facilities by March 31? |
| 3 | 2 | 44.5334% | 100% | $8,906 | FDA approves Precigen’s PRGN-2012 for recurrent respiratory ... |
| 4 | 1 | 43.8020% | 100% | $4,346 | Will another country strike Iran by March 31? |
| 5 | 2 | 39.6205% | 100% | $7,905 | FDA approves Tonix Pharmaceuticals’ TNX-102 SL for fibromyal... |
| 6 | 1 | 32.7153% | 100% | $3,264 | Will Iran conduct a military action against Israel on April ... |
| 7 | 1 | 32.7153% | 100% | $3,264 | Will Iran strike UAE by April 30, 2026? |
| 8 | 1 | 31.8025% | 100% | $3,174 | Will Hewlett Packard Enterprise (HPE) beat quarterly earning... |
| 9 | 1 | 31.3822% | 100% | $3,127 | Will Iran strike Israel by April 30, 2026? |
| 10 | 1 | 27.2696% | 100% | $2,726 | Will GitLab (GTLB) beat quarterly earnings? |
| 11 | 1 | 27.0412% | 100% | $2,698 | Will Akamai Technologies (AKAM) beat quarterly earnings? |
| 12 | 2 | 26.5868% | 100% | $5,310 | Will Iran strike Kuwait by April 30, 2026? |
| 13 | 1 | 25.5589% | 100% | $2,546 | Will Dell Technologies (DELL) beat quarterly earnings? |
| 14 | 1 | 24.9533% | 100% | $2,495 | Will Circle Internet (CRCL) beat quarterly earnings? |
| 15 | 1 | 24.6267% | 100% | $2,448 | Will Iran strike Iraq in March? |
| 16 | 1 | 24.6267% | 100% | $2,448 | Will Iran strike Kuwait in March? |
| 17 | 1 | 24.4092% | 100% | $2,434 | Will Rubrik (RBRK) beat quarterly earnings? |
| 18 | 1 | 23.5785% | 100% | $2,351 | Will Samsara (IOT) beat quarterly earnings? |
| 19 | 1 | 21.6981% | 100% | $2,141 | Will Palo Alto Networks (PANW) beat quarterly earnings? |
| 20 | 1 | 21.2666% | 100% | $2,111 | Will Iran take military action against a Gulf State on April... |

## Top 20 Events by Negative Average Net Return

| Rank | N | Mean Net Ret | Win Rate | Net PnL | Question |
| --- | --- | --- | --- | --- | --- |
| 1 | 1 | -22.3142% | 0% | $-2,190 | Will Reddit (RDDT) beat quarterly earnings? |
| 2 | 2 | -20.0160% | 0% | $-3,961 | Strait of Hormuz traffic returns to normal by end of June? |
| 3 | 1 | -20.0037% | 0% | $-2,000 | Will Getty Images Holdings (GETY) beat quarterly earnings? |
| 4 | 1 | -18.8975% | 0% | $-1,889 | Will Macy's (M) beat quarterly earnings? |
| 5 | 1 | -18.4880% | 0% | $-1,824 | Will Equifax Inc (EFX) beat quarterly earnings? |
| 6 | 2 | -16.4633% | 0% | $-3,257 | Will Jared Kushner have a diplomatic meeting with Iran by Ju... |
| 7 | 2 | -16.4633% | 0% | $-3,257 | Will Jared Kushner attend the next US x Iran diplomatic meet... |
| 8 | 1 | -16.0751% | 0% | $-1,530 | Will S&P Global (SPGI) beat quarterly earnings? |
| 9 | 1 | -14.7125% | 0% | $-1,470 | Will Pinterest (PINS) beat quarterly earnings? |
| 10 | 1 | -14.6675% | 0% | $-1,466 | Will Papa John’s International (PZZA) beat quarterly earning... |
| 11 | 1 | -14.6024% | 0% | $-1,449 | Will Robinhood Markets (HOOD) beat quarterly earnings? |
| 12 | 1 | -14.4624% | 0% | $-1,420 | Will Expedia Group (EXPE) beat quarterly earnings? |
| 13 | 2 | -14.3645% | 0% | $-2,859 | Will Steve Witkoff have a diplomatic meeting with Iran by Ju... |
| 14 | 2 | -14.3645% | 0% | $-2,859 | Will Steve Witkoff attend the next US x Iran diplomatic meet... |
| 15 | 1 | -13.8043% | 0% | $-1,369 | Will Charles River Laboratories International (CRL) beat qua... |
| 16 | 1 | -13.4610% | 0% | $-1,345 | Will Amplitude (AMPL) beat quarterly earnings? |
| 17 | 1 | -13.3763% | 0% | $-1,337 | Will Moderna (MRNA) beat quarterly earnings? |
| 18 | 1 | -12.6053% | 0% | $-1,251 | Will Powell Industries (POWL) beat quarterly earnings? |
| 19 | 2 | -12.5604% | 0% | $-2,496 | Will J.D. Vance attend the next US x Iran diplomatic meeting... |
| 20 | 1 | -12.5048% | 0% | $-1,250 | Will Playboy (PLBY) beat quarterly earnings? |

## Warnings and Assumptions

- 73 candidates had entry >= T-1 exit
- Fold policies are from the SPY benchmark arm of T1+T2+T3+T4
- Candidates before the first fold window (2025-04-29) use fold 1 policy
- Cost model uses only 2 legs (asset buy + asset sell), no benchmark rotation
- Whole shares only (actual notional may be slightly below $10,000)

## Interpretation

- Mean net return is **positive** (1.3497%)
- Median net return is **positive** (0.2710%)
- Win rate is **above** 50% (52.36%)
- t-test p-value: 0.0000 (passes 0.05)
- Binomial p-value: 0.0847 (passes 0.10)
- Bootstrap p-value: 0.0000
- Event-cluster bootstrap p-value: 0.0000
- Results do not survive removing top 5% winners (mean after removal: -0.0139%)
- Results do not survive removing top 10% winners (mean after removal: -0.7704%)
