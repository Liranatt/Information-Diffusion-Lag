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
| Passed T1+T2+T3+T4 entry rules | 957 |
| Valid for T-1 primary test | 884 |
| Invalid: missing ex-ante T | 0 |
| Invalid: entry >= T-1 | 73 |
| Invalid: missing prices | 0 |
| Invalid: bad price | 0 |
| Rejected: no clean signal side | 12 |
| Rejected: below entry threshold | 199 |
| Rejected: prob surge exceeded | 22 |
| Rejected: price runup exceeded | 2 |
| Rejected: no probability data | 0 |
| Rejected: no policy available | 0 |

- Earliest entry date: 2024-09-25
- Latest exit date: 2026-06-29

## Primary: Candidate-Level Results

| Metric | Value |
| --- | --- |
| N trades | 884 |
| Mean gross return | 1.5816% |
| Median gross return | 0.3846% |
| Mean net return | 1.4570% |
| Median net return | 0.2722% |
| Win rate (net > 0) | 52.7149% |
| Total net PnL ($10k each) | $128,400.61 |
| Total gross PnL ($10k each) | $139,338.31 |

## Symbol-Day Collapsed Results

| Metric | Value |
| --- | --- |
| N trades | 761 |
| Mean gross return | 0.8092% |
| Median gross return | 0.1442% |
| Mean net return | 0.6837% |
| Median net return | 0.0148% |
| Win rate (net > 0) | 50.1971% |
| Total net PnL ($10k each) | $51,994.21 |

## Event-Level Results (equal-weighted by market)

| Metric | Value |
| --- | --- |
| N events | 805 |
| Mean event-avg net return | 1.3295% |
| Median event-avg net return | 0.3589% |
| Mean event-avg gross return | 1.4545% |
| Median event-avg gross return | 0.4796% |
| Win rate (event mean net > 0) | 53.4161% |

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
| 2026-04 | 180 | 1.6033% | 0.4739% | 51.67% | $28,767 |
| 2026-05 | 56 | 4.6128% | 2.8948% | 64.29% | $25,670 |
| 2026-06 | 10 | -7.6510% | -5.2227% | 20.00% | $-7,584 |

## Robustness

### candidate_level

| Metric | Value |
| --- | --- |
| n_trades | 884 |
| mean_gross_return | 0.015816 |
| median_gross_return | 0.003846 |
| mean_net_return | 0.014570 |
| median_net_return | 0.002723 |
| total_net_pnl_at_10k_each | 128,400.61 |
| win_rate_net_return_gt_0 | 0.527100 |
| binomial_p_value_greater_than_50pct | 0.056939 |
| one_sample_ttest_p_value_mean_net_return_gt_0 | 0.000000 |
| bootstrap_p_value_mean_net_return_gt_0 | 0.000000 |
| event_cluster_bootstrap_p_value | 0.00 |
| mean_net_return_after_removing_top_1pct | 0.010434 |
| mean_net_return_after_removing_top_5pct | 0.000895 |
| mean_net_return_after_removing_top_10pct | -0.006670 |
| median_net_return_after_removing_top_5pct | 0.000148 |
| share_of_total_pnl_from_top_1pct | 0.291100 |
| share_of_total_pnl_from_top_5pct | 0.940100 |
| share_of_total_pnl_from_top_10pct | 1.409600 |

### symbol_day_collapsed

| Metric | Value |
| --- | --- |
| n_trades | 761 |
| mean_gross_return | 0.008092 |
| median_gross_return | 0.001442 |
| mean_net_return | 0.006837 |
| median_net_return | 0.000148 |
| total_net_pnl_at_10k_each | 51,994.21 |
| win_rate_net_return_gt_0 | 0.502000 |
| binomial_p_value_greater_than_50pct | 0.471105 |
| one_sample_ttest_p_value_mean_net_return_gt_0 | 0.006110 |
| bootstrap_p_value_mean_net_return_gt_0 | 0.007700 |
| event_cluster_bootstrap_p_value | 0.01 |
| mean_net_return_after_removing_top_1pct | 0.002927 |
| mean_net_return_after_removing_top_5pct | -0.004944 |
| mean_net_return_after_removing_top_10pct | -0.010600 |
| median_net_return_after_removing_top_5pct | -0.003392 |
| share_of_total_pnl_from_top_1pct | 0.574900 |
| share_of_total_pnl_from_top_5pct | 1.678900 |
| share_of_total_pnl_from_top_10pct | 2.382000 |

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
| 3 | RDDT | 2026-01-27 | 2026-02-04 | -22.3142% | $-2,190 | Will Reddit (RDDT) beat quarterly earnings? |
| 4 | USO | 2026-06-08 | 2026-06-29 | -20.3731% | $-2,025 | Will Steve Witkoff have a diplomatic meeting with Iran by Ju... |
| 5 | GETY | 2026-03-04 | 2026-03-13 | -20.0037% | $-2,000 | Will Getty Images Holdings (GETY) beat quarterly earnings? |
| 6 | M | 2026-02-24 | 2026-03-17 | -18.8975% | $-1,889 | Will Macy's (M) beat quarterly earnings? |
| 7 | EFX | 2026-01-26 | 2026-02-03 | -18.4880% | $-1,824 | Will Equifax Inc (EFX) beat quarterly earnings? |
| 8 | SPGI | 2026-01-29 | 2026-02-09 | -16.0751% | $-1,530 | Will S&P Global (SPGI) beat quarterly earnings? |
| 9 | PINS | 2026-01-29 | 2026-02-11 | -14.7125% | $-1,470 | Will Pinterest (PINS) beat quarterly earnings? |
| 10 | PZZA | 2025-11-03 | 2025-11-05 | -14.6675% | $-1,466 | Will Papa John’s International (PZZA) beat quarterly earning... |
| 11 | HOOD | 2026-01-29 | 2026-02-09 | -14.6024% | $-1,449 | Will Robinhood Markets (HOOD) beat quarterly earnings? |
| 12 | EXPE | 2026-01-29 | 2026-02-11 | -14.4624% | $-1,420 | Will Expedia Group (EXPE) beat quarterly earnings? |
| 13 | CRL | 2026-02-05 | 2026-02-17 | -13.8043% | $-1,369 | Will Charles River Laboratories International (CRL) beat qua... |
| 14 | AMPL | 2026-02-05 | 2026-02-17 | -13.4610% | $-1,345 | Will Amplitude (AMPL) beat quarterly earnings? |
| 15 | MRNA | 2025-10-31 | 2025-11-05 | -13.3763% | $-1,337 | Will Moderna (MRNA) beat quarterly earnings? |
| 16 | USO | 2026-05-04 | 2026-05-29 | -12.6498% | $-1,251 | Will Donald Trump announce that the United States blockade o... |
| 17 | POWL | 2025-11-11 | 2025-11-17 | -12.6053% | $-1,251 | Will Powell Industries (POWL) beat quarterly earnings? |
| 18 | PLBY | 2026-02-27 | 2026-03-13 | -12.5048% | $-1,250 | Will Playboy (PLBY) beat quarterly earnings? |
| 19 | ARM | 2026-01-22 | 2026-02-03 | -12.3936% | $-1,226 | Will Arm Holdings (ARM) beat quarterly earnings? |
| 20 | USO | 2026-04-30 | 2026-05-29 | -12.3408% | $-1,216 | Strait of Hormuz traffic returns to normal by end of May? |

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
| 7 | 1 | -16.0751% | 0% | $-1,530 | Will S&P Global (SPGI) beat quarterly earnings? |
| 8 | 1 | -14.7125% | 0% | $-1,470 | Will Pinterest (PINS) beat quarterly earnings? |
| 9 | 1 | -14.6675% | 0% | $-1,466 | Will Papa John’s International (PZZA) beat quarterly earning... |
| 10 | 1 | -14.6024% | 0% | $-1,449 | Will Robinhood Markets (HOOD) beat quarterly earnings? |
| 11 | 1 | -14.4624% | 0% | $-1,420 | Will Expedia Group (EXPE) beat quarterly earnings? |
| 12 | 2 | -14.3645% | 0% | $-2,859 | Will Steve Witkoff have a diplomatic meeting with Iran by Ju... |
| 13 | 1 | -13.8043% | 0% | $-1,369 | Will Charles River Laboratories International (CRL) beat qua... |
| 14 | 1 | -13.4610% | 0% | $-1,345 | Will Amplitude (AMPL) beat quarterly earnings? |
| 15 | 1 | -13.3763% | 0% | $-1,337 | Will Moderna (MRNA) beat quarterly earnings? |
| 16 | 1 | -12.6053% | 0% | $-1,251 | Will Powell Industries (POWL) beat quarterly earnings? |
| 17 | 1 | -12.5048% | 0% | $-1,250 | Will Playboy (PLBY) beat quarterly earnings? |
| 18 | 1 | -12.3936% | 0% | $-1,226 | Will Arm Holdings (ARM) beat quarterly earnings? |
| 19 | 1 | -12.2225% | 0% | $-1,222 | Will Newsmax (NMAX) beat quarterly earnings? |
| 20 | 1 | -11.9010% | 0% | $-1,189 | Will Caesars Entertainment (CZR) beat quarterly earnings? |

## Warnings and Assumptions

- 73 candidates had entry >= T-1 exit
- Fold policies are from the SPY benchmark arm of T1+T2+T3+T4
- Candidates before the first fold window (2025-04-29) use fold 1 policy
- Cost model uses only 2 legs (asset buy + asset sell), no benchmark rotation
- Whole shares only (actual notional may be slightly below $10,000)

## Interpretation

- Mean net return is **positive** (1.4570%)
- Median net return is **positive** (0.2722%)
- Win rate is **above** 50% (52.71%)
- t-test p-value: 0.0000 (passes 0.05)
- Binomial p-value: 0.0569 (passes 0.10)
- Bootstrap p-value: 0.0000
- Event-cluster bootstrap p-value: 0.0000
- Results survive removing top 5% winners (mean after removal: 0.0895%)
- Results do not survive removing top 10% winners (mean after removal: -0.6670%)
