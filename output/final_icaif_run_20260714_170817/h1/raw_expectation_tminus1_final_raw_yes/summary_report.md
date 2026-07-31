# Raw Expectation Test: T-1 Exit

## Configuration

- **Input parquet**: `C:\Users\Liran\PycharmProjects\cem_clean_repo\data\candidates_audit_clean.parquet`
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
| Total candidates loaded | 1182 |
| Passed threshold (exist in parquet, >= 0.55) | 1182 |
| Passed T1+T2+T3+T4 entry rules | 927 |
| Valid for T-1 primary test | 882 |
| Invalid: missing ex-ante T | 0 |
| Invalid: entry >= T-1 | 45 |
| Invalid: missing prices | 0 |
| Invalid: bad price | 0 |
| Rejected: no clean signal side | 0 |
| Rejected: below entry threshold | 96 |
| Rejected: prob surge exceeded | 152 |
| Rejected: price runup exceeded | 7 |
| Rejected: no probability data | 0 |
| Rejected: no policy available | 0 |

- Earliest entry date: 2024-08-05
- Latest exit date: 2026-06-29

## Primary: Candidate-Level Results

| Metric | Value |
| --- | --- |
| N trades | 882 |
| Mean gross return | 1.3982% |
| Median gross return | 0.3407% |
| Mean net return | 1.2716% |
| Median net return | 0.2288% |
| Win rate (net > 0) | 52.2676% |
| Total net PnL ($10k each) | $111,988.79 |
| Total gross PnL ($10k each) | $123,087.38 |

## Symbol-Day Collapsed Results

| Metric | Value |
| --- | --- |
| N trades | 781 |
| Mean gross return | 0.8714% |
| Median gross return | 0.1442% |
| Mean net return | 0.7433% |
| Median net return | 0.0207% |
| Win rate (net > 0) | 50.4481% |
| Total net PnL ($10k each) | $58,151.06 |

## Event-Level Results (equal-weighted by market)

| Metric | Value |
| --- | --- |
| N events | 818 |
| Mean event-avg net return | 1.1707% |
| Median event-avg net return | 0.3034% |
| Mean event-avg gross return | 1.2982% |
| Median event-avg gross return | 0.4214% |
| Win rate (event mean net > 0) | 53.0562% |

## Monthly Results

| Month | N | Mean Net Ret | Median Net Ret | Win Rate | Net PnL ($10k) |
| --- | --- | --- | --- | --- | --- |
| 2024-08 | 2 | 3.4408% | 3.4408% | 100.00% | $687 |
| 2024-10 | 4 | -2.9198% | -2.5532% | 0.00% | $-1,163 |
| 2025-06 | 43 | -2.2901% | 0.1544% | 53.49% | $-9,818 |
| 2025-07 | 14 | 17.1197% | 2.8985% | 71.43% | $23,942 |
| 2025-09 | 30 | -1.6446% | -1.2737% | 43.33% | $-4,879 |
| 2025-10 | 140 | -0.6494% | -0.4908% | 42.86% | $-8,988 |
| 2025-11 | 95 | -0.1970% | 0.0343% | 50.53% | $-1,863 |
| 2025-12 | 16 | 1.8738% | 1.9838% | 81.25% | $3,074 |
| 2026-01 | 115 | -1.4529% | -0.5319% | 44.35% | $-16,503 |
| 2026-02 | 116 | 0.3429% | 0.1954% | 51.72% | $3,996 |
| 2026-03 | 114 | 7.7665% | 6.5933% | 69.30% | $88,078 |
| 2026-04 | 136 | 1.4599% | 0.4846% | 52.94% | $19,769 |
| 2026-05 | 50 | 3.9073% | 2.5972% | 58.00% | $19,423 |
| 2026-06 | 7 | -5.4022% | -3.6599% | 14.29% | $-3,767 |

## Robustness

### candidate_level

| Metric | Value |
| --- | --- |
| n_trades | 882 |
| mean_gross_return | 0.013982 |
| median_gross_return | 0.003407 |
| mean_net_return | 0.012716 |
| median_net_return | 0.002288 |
| total_net_pnl_at_10k_each | 111,988.79 |
| win_rate_net_return_gt_0 | 0.522700 |
| binomial_p_value_greater_than_50pct | 0.094543 |
| one_sample_ttest_p_value_mean_net_return_gt_0 | 0.000118 |
| bootstrap_p_value_mean_net_return_gt_0 | 0.000150 |
| event_cluster_bootstrap_p_value | 0.00 |
| mean_net_return_after_removing_top_1pct | 0.007101 |
| mean_net_return_after_removing_top_5pct | -0.002107 |
| mean_net_return_after_removing_top_10pct | -0.009068 |
| median_net_return_after_removing_top_5pct | -0.000527 |
| share_of_total_pnl_from_top_1pct | 0.446900 |
| share_of_total_pnl_from_top_5pct | 1.154200 |
| share_of_total_pnl_from_top_10pct | 1.637000 |

### symbol_day_collapsed

| Metric | Value |
| --- | --- |
| n_trades | 781 |
| mean_gross_return | 0.008714 |
| median_gross_return | 0.001442 |
| mean_net_return | 0.007433 |
| median_net_return | 0.000207 |
| total_net_pnl_at_10k_each | 58,151.06 |
| win_rate_net_return_gt_0 | 0.504500 |
| binomial_p_value_greater_than_50pct | 0.415010 |
| one_sample_ttest_p_value_mean_net_return_gt_0 | 0.018239 |
| bootstrap_p_value_mean_net_return_gt_0 | 0.025350 |
| event_cluster_bootstrap_p_value | 0.03 |
| mean_net_return_after_removing_top_1pct | 0.001707 |
| mean_net_return_after_removing_top_5pct | -0.006274 |
| mean_net_return_after_removing_top_10pct | -0.012244 |
| median_net_return_after_removing_top_5pct | -0.003618 |
| share_of_total_pnl_from_top_1pct | 0.769800 |
| share_of_total_pnl_from_top_5pct | 1.791300 |
| share_of_total_pnl_from_top_10pct | 2.466800 |

## Top 20 Winners (by net return)

| Rank | Symbol | Entry | Exit | Net Ret | Net PnL | Question |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | PGEN | 2025-07-29 | 2025-08-26 | 170.6985% | $17,069 | FDA approves Precigen’s PRGN-2012 for recurrent respiratory ... |
| 2 | USO | 2026-02-13 | 2026-03-30 | 70.1868% | $7,008 | Will the US not strike Iran by March 31, 2026? |
| 3 | USO | 2026-03-02 | 2026-03-30 | 48.7681% | $4,847 | Will Iran strike gulf oil facilities by March 31? |
| 4 | USO | 2026-03-03 | 2026-03-30 | 43.8020% | $4,346 | Will another country strike Iran by March 31? |
| 5 | TNXP | 2025-07-29 | 2025-08-14 | 39.6205% | $3,952 | FDA approves Tonix Pharmaceuticals’ TNX-102 SL for fibromyal... |
| 6 | USO | 2026-03-25 | 2026-04-29 | 32.7153% | $3,264 | Will Iran conduct a military action against Israel on April ... |
| 7 | USO | 2026-03-25 | 2026-04-29 | 32.7153% | $3,264 | Will Iran strike Saudi Arabia by April 30, 2026? |
| 8 | HPE | 2026-05-19 | 2026-05-29 | 31.8025% | $3,174 | Will Hewlett Packard Enterprise (HPE) beat quarterly earning... |
| 9 | USO | 2026-03-24 | 2026-04-29 | 31.3822% | $3,127 | Will Iran conduct a military action against Israel on April ... |
| 10 | USO | 2026-03-24 | 2026-04-29 | 31.3822% | $3,127 | Will Iran strike Israel by April 30, 2026? |
| 11 | USO | 2026-03-26 | 2026-04-29 | 28.3333% | $2,824 | Will Iran conduct a military action against Israel on April ... |
| 12 | USO | 2026-03-26 | 2026-04-29 | 28.3333% | $2,824 | Will Iran take military action against a Gulf State on April... |
| 13 | BLSH | 2025-09-17 | 2025-09-23 | 27.7261% | $2,758 | Will Bullish (BLSH) beat its quarterly EPS estimate? |
| 14 | GTLB | 2026-05-27 | 2026-06-01 | 27.2696% | $2,726 | Will GitLab (GTLB) beat quarterly earnings? |
| 15 | DELL | 2026-05-20 | 2026-05-27 | 25.5589% | $2,546 | Will Dell Technologies (DELL) beat quarterly earnings? |
| 16 | CRCL | 2026-04-30 | 2026-05-08 | 24.9533% | $2,495 | Will Circle Internet (CRCL) beat quarterly earnings? |
| 17 | USO | 2026-03-02 | 2026-03-06 | 24.6267% | $2,448 | Will Iran strike Israel in March? |
| 18 | USO | 2026-03-02 | 2026-03-06 | 24.6267% | $2,448 | Will Iran strike Bahrain in March? |
| 19 | USO | 2026-03-02 | 2026-03-06 | 24.6267% | $2,448 | Will Iran strike Qatar in March? |
| 20 | USO | 2026-03-02 | 2026-03-06 | 24.6267% | $2,448 | Will Iran strike UAE in March? |

## Top 20 Losers (by net return)

| Rank | Symbol | Entry | Exit | Net Ret | Net PnL | Question |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | RDDT | 2026-01-26 | 2026-02-04 | -28.6208% | $-2,813 | Will Reddit (RDDT) beat quarterly earnings? |
| 2 | IART | 2025-10-23 | 2025-10-31 | -24.5043% | $-2,448 | Will Integra LifeSciences Holdings (IART) beat quarterly ear... |
| 3 | KMX | 2025-09-18 | 2025-10-01 | -23.3688% | $-2,328 | Will CarMax (KMX) beat its quarterly EPS estimate? |
| 4 | COIN | 2026-01-29 | 2026-02-11 | -23.1823% | $-2,309 | Will Coinbase Global (COIN) beat quarterly earnings? |
| 5 | ETSY | 2026-02-09 | 2026-02-18 | -20.1593% | $-2,011 | Will ETSY (ETSY) beat quarterly earnings? |
| 6 | USO | 2026-05-06 | 2026-06-29 | -20.1590% | $-1,998 | Will Donald Trump announce that the United States blockade o... |
| 7 | GETY | 2026-03-04 | 2026-03-13 | -20.0037% | $-2,000 | Will Getty Images Holdings (GETY) beat quarterly earnings? |
| 8 | M | 2026-02-24 | 2026-03-17 | -18.8975% | $-1,889 | Will Macy's (M) beat quarterly earnings? |
| 9 | EFX | 2026-01-26 | 2026-02-03 | -18.4880% | $-1,824 | Will Equifax Inc (EFX) beat quarterly earnings? |
| 10 | USO | 2026-05-27 | 2026-06-29 | -18.3784% | $-1,830 | Will Jared Kushner have a diplomatic meeting with Iran by Ju... |
| 11 | FDS | 2025-09-16 | 2025-09-24 | -17.2017% | $-1,663 | Will Factset Research (FDS) beat its quarterly EPS estimate? |
| 12 | USO | 2026-05-29 | 2026-06-29 | -17.1509% | $-1,705 | Will Steve Witkoff have a diplomatic meeting with Iran by Ju... |
| 13 | HOOD | 2026-01-28 | 2026-02-09 | -16.3875% | $-1,627 | Will Robinhood Markets (HOOD) beat quarterly earnings? |
| 14 | SPGI | 2026-01-28 | 2026-02-09 | -15.9940% | $-1,520 | Will S&P Global (SPGI) beat quarterly earnings? |
| 15 | PINS | 2026-01-29 | 2026-02-11 | -14.7125% | $-1,470 | Will Pinterest (PINS) beat quarterly earnings? |
| 16 | PZZA | 2025-11-03 | 2025-11-05 | -14.6675% | $-1,466 | Will Papa John’s International (PZZA) beat quarterly earning... |
| 17 | EXPE | 2026-01-29 | 2026-02-11 | -14.4624% | $-1,420 | Will Expedia Group (EXPE) beat quarterly earnings? |
| 18 | CRL | 2026-02-05 | 2026-02-17 | -13.8043% | $-1,369 | Will Charles River Laboratories International (CRL) beat qua... |
| 19 | USO | 2026-04-14 | 2026-06-29 | -13.6433% | $-1,352 | Strait of Hormuz traffic returns to normal by end of June? |
| 20 | AMPL | 2026-02-05 | 2026-02-17 | -13.4610% | $-1,345 | Will Amplitude (AMPL) beat quarterly earnings? |

## Top 20 Events by Average Net Return

| Rank | N | Mean Net Ret | Win Rate | Net PnL | Question |
| --- | --- | --- | --- | --- | --- |
| 1 | 1 | 170.6985% | 100% | $17,069 | FDA approves Precigen’s PRGN-2012 for recurrent respiratory ... |
| 2 | 1 | 70.1868% | 100% | $7,008 | Will the US not strike Iran by March 31, 2026? |
| 3 | 1 | 48.7681% | 100% | $4,847 | Will Iran strike gulf oil facilities by March 31? |
| 4 | 1 | 43.8020% | 100% | $4,346 | Will another country strike Iran by March 31? |
| 5 | 1 | 39.6205% | 100% | $3,952 | FDA approves Tonix Pharmaceuticals’ TNX-102 SL for fibromyal... |
| 6 | 1 | 32.7153% | 100% | $3,264 | Will Iran conduct a military action against Israel on April ... |
| 7 | 1 | 31.8025% | 100% | $3,174 | Will Hewlett Packard Enterprise (HPE) beat quarterly earning... |
| 8 | 1 | 31.3822% | 100% | $3,127 | Will Iran conduct a military action against Israel on April ... |
| 9 | 1 | 31.3822% | 100% | $3,127 | Will Iran strike Israel by April 30, 2026? |
| 10 | 1 | 28.3333% | 100% | $2,824 | Will Iran conduct a military action against Israel on April ... |
| 11 | 1 | 27.7261% | 100% | $2,758 | Will Bullish (BLSH) beat its quarterly EPS estimate? |
| 12 | 1 | 27.2696% | 100% | $2,726 | Will GitLab (GTLB) beat quarterly earnings? |
| 13 | 1 | 25.5589% | 100% | $2,546 | Will Dell Technologies (DELL) beat quarterly earnings? |
| 14 | 1 | 24.9533% | 100% | $2,495 | Will Circle Internet (CRCL) beat quarterly earnings? |
| 15 | 1 | 24.6267% | 100% | $2,448 | Will Iran strike Iraq in March? |
| 16 | 1 | 24.4092% | 100% | $2,434 | Will Rubrik (RBRK) beat quarterly earnings? |
| 17 | 1 | 23.5785% | 100% | $2,351 | Will Samsara (IOT) beat quarterly earnings? |
| 18 | 1 | 21.6981% | 100% | $2,141 | Will Palo Alto Networks (PANW) beat quarterly earnings? |
| 19 | 1 | 20.7894% | 100% | $2,072 | Will Iran strike Saudi Arabia by April 30, 2026? |
| 20 | 1 | 20.5105% | 100% | $2,049 | Will Build-A-Bear Workshop (BBW) beat quarterly earnings? |

## Top 20 Events by Negative Average Net Return

| Rank | N | Mean Net Ret | Win Rate | Net PnL | Question |
| --- | --- | --- | --- | --- | --- |
| 1 | 1 | -28.6208% | 0% | $-2,813 | Will Reddit (RDDT) beat quarterly earnings? |
| 2 | 1 | -24.5043% | 0% | $-2,448 | Will Integra LifeSciences Holdings (IART) beat quarterly ear... |
| 3 | 1 | -23.3688% | 0% | $-2,328 | Will CarMax (KMX) beat its quarterly EPS estimate? |
| 4 | 1 | -23.1823% | 0% | $-2,309 | Will Coinbase Global (COIN) beat quarterly earnings? |
| 5 | 1 | -20.1593% | 0% | $-2,011 | Will ETSY (ETSY) beat quarterly earnings? |
| 6 | 1 | -20.0037% | 0% | $-2,000 | Will Getty Images Holdings (GETY) beat quarterly earnings? |
| 7 | 1 | -18.8975% | 0% | $-1,889 | Will Macy's (M) beat quarterly earnings? |
| 8 | 1 | -18.4880% | 0% | $-1,824 | Will Equifax Inc (EFX) beat quarterly earnings? |
| 9 | 1 | -17.2017% | 0% | $-1,663 | Will Factset Research (FDS) beat its quarterly EPS estimate? |
| 10 | 1 | -16.3875% | 0% | $-1,627 | Will Robinhood Markets (HOOD) beat quarterly earnings? |
| 11 | 1 | -15.9940% | 0% | $-1,520 | Will S&P Global (SPGI) beat quarterly earnings? |
| 12 | 1 | -14.7125% | 0% | $-1,470 | Will Pinterest (PINS) beat quarterly earnings? |
| 13 | 1 | -14.6675% | 0% | $-1,466 | Will Papa John’s International (PZZA) beat quarterly earning... |
| 14 | 1 | -14.4624% | 0% | $-1,420 | Will Expedia Group (EXPE) beat quarterly earnings? |
| 15 | 1 | -13.8043% | 0% | $-1,369 | Will Charles River Laboratories International (CRL) beat qua... |
| 16 | 1 | -13.4610% | 0% | $-1,345 | Will Amplitude (AMPL) beat quarterly earnings? |
| 17 | 1 | -13.2725% | 0% | $-1,327 | Will Concentrix (CNXC) beat its quarterly EPS estimate? |
| 18 | 2 | -13.1354% | 0% | $-2,608 | Will Donald Trump announce that the United States blockade o... |
| 19 | 1 | -12.9483% | 0% | $-1,294 | Will MillerKnoll (MLKN) beat its quarterly EPS estimate? |
| 20 | 1 | -12.6490% | 0% | $-1,265 | Will Kohls (KSS) beat quarterly earnings? |

## Warnings and Assumptions

- 45 candidates had entry >= T-1 exit
- Fold policies are from the SPY benchmark arm of T1+T2+T3+T4
- Candidates before the first fold window (2025-04-29) use fold 1 policy
- Cost model uses only 2 legs (asset buy + asset sell), no benchmark rotation
- Whole shares only (actual notional may be slightly below $10,000)

## Interpretation

- Mean net return is **positive** (1.2716%)
- Median net return is **positive** (0.2288%)
- Win rate is **above** 50% (52.27%)
- t-test p-value: 0.0001 (passes 0.05)
- Binomial p-value: 0.0945 (passes 0.10)
- Bootstrap p-value: 0.0001
- Event-cluster bootstrap p-value: 0.0006
- Results do not survive removing top 5% winners (mean after removal: -0.2107%)
- Results do not survive removing top 10% winners (mean after removal: -0.9068%)
