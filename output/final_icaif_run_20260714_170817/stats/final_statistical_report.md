# Final Statistical Report — ICAIF paper (2026-07-14 run)

All bootstraps: 20,000 replications, fixed seeds (42 + documented offsets).
Two p-value conventions appear and are never mixed: (a) null-centered one-sided
bootstrap p = (#null-draws >= observed + 1)/(B + 1); (b) one-sided t-tests as an
iid reference only. Every CI is an ordinary uncentered percentile bootstrap
interval of the mean unless marked as an IQR.

## H1

**Is the raw candidate mean positive?** Yes: +1.208% mean, +0.188% median, 51.97% positive over N=887 candidates (750 economic events, 387 symbols, 46 entry weeks, 16 entry months).

**Under which dependence assumptions does its interval exclude zero?** Only under
economic-event clustering: CI [+0.263%, +2.200%], p=0.0087.
Symbol clustering (CI [-0.184%, +2.776%], p=0.0652),
entry-week blocks (p=0.0678) and
entry-month blocks (CI [-0.630%, +3.651%], p=0.1427) all cross zero.

**Does the equal-event estimate remain positive?** The point estimate is positive but small:
mean +0.631%, median +0.013% over N=750 events; its month-block interval
[-0.417%, +2.407%] crosses zero (p=0.1935).

**How dependent is the result on the upper tail?** Strongly. Dropping the top 1% of
candidates lowers the mean to +0.662%; dropping the top 5% turns it negative (-0.260%).
At event level the top 1% removal leaves +0.029% (p=0.452). Symmetric trims are milder
(candidate 5%: +0.584%; event 5%: +0.070%). The top 5% of candidates carry ~120% of the
total return (the remainder loses money in aggregate).

**Which event families drive the result?** Geopolitical: mean +4.794%, median +2.443%,
61.6% positive (N=172), event-clustered CI excludes zero. Earnings are centered at zero
(mean +0.020%, N=692, CI crosses zero). 'Other' is +10.118% on N=23 with a very wide CI.

**Does the result survive matched SPY and sector controls?** Not at the 95% level.
The SPY candidate/event-cluster excess is +0.914% with CI crossing zero (p=0.0508);
all other SPY and sector-ETF schemes have p >= 0.11. The sector-ETF candidate excess
is +0.191% (p=0.264). NO 95% interval excludes zero in the benchmark-relative analysis.
The sector comparison covers the 680 sector-mapped (single-stock) rows; ETF-mapped
geo/macro candidates have no sector assignment and are excluded rather than proxied.

**2026 subsample (point-in-time eligibility, online refits):** N=528 entries in
Jan-Jun 2026, mean +2.038%, median +0.557%; event-cluster p=0.0006, month-block p=0.1083.
Eligibility parameters for these observations were fitted only on outcomes completed
before each observation's walk-forward fold started, but folds 4-5 were refit inside
the test period, and the period has been repeatedly inspected during research —
descriptive evidence, not a confirmatory test.

**Provenance caveat (applies to every full-sample H1 row):** the eligibility
parameters are CEM-derived (T1+T2+T3+T4/SPY walk-forward schedule) and pre-fold-1
candidates use a policy fitted on later data. The full-sample estimate is therefore
descriptive, not an independent confirmatory hypothesis test.

## CEM

**Are test returns and excess returns stable across seeds?**

budget benchmark  n_seeds  median_test_excess_pct  q25_test_excess_pct  q75_test_excess_pct  min_test_excess_pct  max_test_excess_pct  positive_excess_seed_count  median_test_return_pct  median_test_sharpe  median_test_max_dd_pct  median_test_trades  median_test_trade_txn_cost
 10x20       QQQ       10                    4.65                 1.33                 5.96                -3.33                12.08                           8                   22.24                2.12                   -9.39               182.5                     4050.77
 10x20       SPY       10                    7.76                 7.35                10.52                 3.56                13.54                          10                   16.28                1.93                   -8.62               193.5                     3884.66
 10x30       QQQ       10                    6.44                 4.15                 7.23                -0.86                12.11                           9                   24.03                2.17                   -9.54               178.0                     4051.58
 10x30       SPY       10                    6.97                 5.77                 9.06                 2.30                15.44                          10                   15.49                1.87                   -8.47               189.5                     3698.20
  6x20       QQQ       10                    3.89                 2.21                 7.25                -0.89                12.47                           8                   21.48                2.17                   -9.26               186.5                     4158.35
  6x20       SPY       10                    8.26                 4.31                 9.63                 3.30                19.97                          10                   16.78                1.88                   -8.74               197.0                     3891.22
  6x30       QQQ       10                    5.65                 3.11                 7.69                -0.22                 9.64                           9                   23.24                2.17                   -9.48               179.5                     4221.65
  6x30       SPY       10                    6.69                 6.15                 8.37                 3.39                16.05                          10                   15.21                1.86                   -8.73               189.0                     3628.59

**Budget sensitivity:** medians move by a few points across the four budgets and
IQRs overlap heavily; no budget is selected or ranked by test performance.

**Does All Treatments consistently improve on Baseline at equal seed/budget (6x20)?**

benchmark  n_pairs  median_delta_excess_pct  q25_delta_excess_pct  q75_delta_excess_pct  min_delta_excess_pct  max_delta_excess_pct  median_delta_sharpe  median_delta_max_dd_pct  median_delta_trade_txn_cost  pct_seeds_all_beats_baseline
      QQQ       10                    -3.44                 -7.87                  1.06                -14.95                  5.64                -0.55                    -1.00                     -1620.14                          30.0
      SPY       10                    -7.43                -12.39                 -3.92                -14.59                 -1.64                -0.92                    -0.16                     -2032.00                           0.0
     BOTH       20                    -6.15                -10.49                 -1.75                -14.95                  5.64                -0.66                    -0.65                     -1844.60                          15.0

**Is the evidence sufficient for a profitability claim?** No. It supports historical
robustness only. The seed-42 flagship daily excess is nominally positive on SPY
(block-bootstrap p=0.0317, HAC p=0.0239)
but not on QQQ (p=0.3102); the test window is a single,
short (111 trading days), repeatedly inspected period; and the walk-forward arms refit
policies inside that window. The cleaning delta (audit-clean vs original universe) is
directionally positive but not significant (SPY p=0.3371, QQQ p=0.3884).
No portfolio-level statistical-significance claim is made in the paper.

## Sources

Every row of final_statistical_results.csv names its source CSV; computed rows name
the trade file and the computation (20k null-centered cluster bootstrap, seeds 142-143,
242-243 for families and the 2026 subsample as recorded in build_final_stats.py).