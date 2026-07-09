# Forensic Attribution: QQQ T1+T2+T3 vs T1+T2+T3+T4

## 1. Period Metrics

     config   period  return_pct  benchmark_return_pct  excess_pct  max_dd_pct  sharpe  n_trades  win_rate_pct  total_pnl  avg_pnl
   T1+T2+T3 full_oos       23.88                 17.65        6.23      -10.48    2.23       183          67.2   22045.39   120.47
   T1+T2+T3    early       10.56                  8.91        1.65      -10.48    1.57       148          66.2   10421.89    70.42
   T1+T2+T3     late       11.60                  7.00        4.60       -7.03    3.59        35          71.4   11623.50   332.10
T1+T2+T3+T4 full_oos       27.28                 17.65        9.63       -8.94    2.35       190          64.2   22804.72   120.02
T1+T2+T3+T4    early        5.13                  8.91       -3.78       -8.94    0.85       158          61.4    4424.27    28.00
T1+T2+T3+T4     late       19.37                  7.00       12.37       -7.03    4.93        32          78.1   18380.45   574.39


## 2. Policy Parameter Comparison

### T1+T2+T3 (FIFO)

 fold_id  atr_mult  lock_activate  theta_out  enter_strong  enter_floor  hold_days  max_prob_surge  max_price_runup  position_size_pct  max_concurrent
       1  4.000000       0.034468   0.563599      0.791721     0.791721          1        0.290293         0.099919           0.110059               8
       2  3.880107       0.035394   0.555914      0.800000     0.800000          1        0.344063         0.097048           0.113491               9
       3  4.000000       0.020000   0.571804      0.795912     0.792394          1        0.284721         0.141236           0.097444              10
       4  3.678569       0.020000   0.450000      0.796010     0.792098          1        0.204556         0.173124           0.104172              10
       5  4.000000       0.020000   0.553305      0.790735     0.790735          1        0.215657         0.102675           0.105561               9

### T1+T2+T3+T4 (Event Priority)

 fold_id  atr_mult  lock_activate  theta_out  enter_strong  enter_floor  hold_days  max_prob_surge  max_price_runup  position_size_pct  max_concurrent
       1  4.000000       0.034468   0.563599      0.791721     0.791721          1        0.290293         0.099919           0.110059               8
       2  3.818430       0.056097   0.539591      0.792550     0.728486          3        0.369179         0.096113           0.106601              10
       3  3.671941       0.020000   0.554857      0.753763     0.707883          2        0.467800         0.197919           0.104828              10
       4  4.000000       0.020000   0.568370      0.792941     0.792941          1        0.200000         0.086526           0.091410              10
       5  4.000000       0.024578   0.548125      0.787670     0.787670          1        0.200000         0.133121           0.109785               9


## 3. Trade Overlap

- Selected by both: 136 candidates

- Selected only by T1+T2+T3: 47 candidates

- Selected only by T1+T2+T3+T4: 54 candidates



## 4. Top 10 Trades Selected Only by T1+T2+T3 (Early Period)

symbol entry_date  exit_date     pnl  pnl_pct                                                              question
   CVS 2026-04-27 2026-04-30 1461.28   6.7812                        Will CVS Health (CVS) beat quarterly earnings?
   NET 2026-01-30 2026-02-03  750.90   3.7803                        Will Cloudflare (NET) beat quarterly earnings?
  BBBY 2026-04-21 2026-04-23  599.96   2.7204                Will Bed Bath & Beyond (BBBY) beat quarterly earnings?
   USO 2026-03-31 2026-04-06  484.36   9.7600 Will Iran take military action against a Gulf State on April 4, 2026?
  NXPI 2026-04-21 2026-04-24  452.25   8.7586               Will NXP Semiconductors (NXPI) beat quarterly earnings?
  BURL 2026-02-24 2026-02-27  379.47   1.7852                Will Burlington Stores (BURL) beat quarterly earnings?
    ON 2026-04-28 2026-04-30  361.62   7.7519                   Will ON Semiconductor (ON) beat quarterly earnings?
   MTB 2026-01-05 2026-01-09  359.18   1.7863                          Will M&T Bank (MTB) beat quarterly earnings?
   PNC 2026-01-05 2026-01-09  358.68   1.7872            Will PNC Financial Services (PNC) beat quarterly earnings?
   COF 2026-01-12 2026-01-16  357.69   1.7835                       Will Capital One (COF) beat quarterly earnings?


## 5. Top 10 Trades Selected Only by T1+T2+T3+T4 (Early Period)

symbol entry_date  exit_date    pnl  pnl_pct                                                              question
   TSM 2026-01-05 2026-01-07 556.49   2.7853              Will Taiwan Semiconductor (TSM) beat quarterly earnings?
   USO 2026-03-31 2026-04-06 459.42   9.7578 Will Iran take military action against a Gulf State on April 1, 2026?
   WIX 2026-04-30 2026-05-04 440.94   7.7679                           Will Wix.com (WIX) beat quarterly earnings?
  MSFT 2026-04-20 2026-04-22 380.68   1.7854                        Will Microsoft (MSFT) beat quarterly earnings?
   APP 2026-04-27 2026-05-04 378.24   1.7864                          Will Applovin (APP) beat quarterly earnings?
  SCHW 2026-01-07 2026-01-20 354.45   1.7833                   Will Charles Schwab (SCHW) beat quarterly earnings?
   TFC 2026-01-13 2026-01-16 350.35   1.7671                  Will Truist Financial (TFC) beat quarterly earnings?
 GOOGL 2026-04-20 2026-04-27 252.67   1.7829                        Will Alphabet (GOOGL) beat quarterly earnings?
  CRWD 2026-02-23 2026-02-25 243.09   2.7756             Will CrowdStrike Holdings (CRWD) beat quarterly earnings?
  ADSK 2026-02-12 2026-02-17 235.74   3.7673                         Will Autodesk (ADSK) beat quarterly earnings?


## 6. Top 10 Skipped-by-T4 but Selected-by-T3 (by Counterfactual PnL)

      date symbol               skip_reason  counterfactual_pnl_of_skipped_candidate  counterfactual_net_car_of_skipped_candidate                    positions_blocking_capital
2026-03-31    USO same_day_symbol_collapsed                                   9.1866                                     0.072254                GS;DAL;WFC;USO;BNO;XLE;JPM;JNJ
2026-04-21   NXPI      insufficient_capital                                   8.7038                                     0.056696             INTC;MSFT;IVZ;GOOGL;META;SOFI;DLB
2026-05-12   WDAY            max_concurrent                                   8.6495                                     0.088418             AS;AZN;ADI;NVDA;BJ;INTU;LOW;DE;RL
2026-05-20    WSM      insufficient_capital                                   6.7961                                     0.061805          HPE;HPQ;OKTA;CRWD;CXM;DELL;ADSK;AVGO
2026-05-26     DG      insufficient_capital                                   6.7561                                     0.056565             DELL;ADSK;AVGO;LULU;IOT;RBRK;DOCU
2026-04-27    CVS      insufficient_capital                                   6.2915                                     0.057630               META;SOFI;OXY;DKNG;DLB;PZZA;APP
2026-01-26   MCHP            max_concurrent                                   6.1104                                     0.054757 NYT;MSFT;META;PFE;GOOGL;PEP;DIS;FOXA;CMG;QCOM
2026-05-20   PANW      insufficient_capital                                   5.6434                                     0.050278          HPE;HPQ;OKTA;CRWD;CXM;DELL;ADSK;AVGO
2026-01-23    XOM            max_concurrent                                   4.1046                                     0.030271 NYT;MSFT;META;PFE;GOOGL;PEP;DIS;FOXA;CMG;QCOM
2026-02-23   SHAK      insufficient_capital                                   2.8696                                     0.003305          NVDA;ZM;FTDR;RKLB;CRWD;REAL;MDB;ASAN


## 7. Top 10 T4 Positions that Blocked Capital (Early Period)

candidate_id symbol  avg_unrealized_pnl_pct  days_open  max_capital_locked
 1705685_BNO    BNO               -9.564444         10             4865.63
1339470_WING   WING               -8.627500          3             4933.16
 1280193_FIS    FIS               -6.798000          4             5103.28
 1399646_MDB    MDB               -6.312000          6            20329.04
1435762_PLBY   PLBY               -6.074000         13            20276.56
 1339377_MCO    MCO               -5.570000          4             4944.17
 1508634_ACN    ACN               -4.255714          8              209.36
1555597_CHWY   CHWY               -4.160000         12            12618.70
1555598_CTAS   CTAS               -3.844286         10             4857.00
1494817_LULU   LULU               -3.496667          7             3572.73


## 8. Measured Attribution: Where Did the Early Gap Come From?



### A. Trade Choice (early period)

- Shared trades in T3: 109, total PnL: $16059.80

- Shared trades in T4: 109, total PnL: $9212.01

- PnL gap on SHARED trades (T3 - T4): $6847.79

- T3-only trades: 39, total PnL: $-5637.91

- T4-only trades: 49, total PnL: $-4787.74

- PnL gap from DIVERGENT trade selection (T3-only minus T4-only): $-850.17



### B. Position Sizing (early period)

- Avg position size T3 (shared trades): 14.65%

- Avg position size T4 (shared trades): 10.61%



### C. Capital Blocking (early period)

- Candidates skipped by T4 but selected by T3 (early): 35

- Total counterfactual PnL% lost by T4 skipping: -36.15%



### D. Exit Timing (shared trades, early period)

- Avg holding days T3: 5.3

- Avg holding days T4: 5.3



### E. CEM Parameter Differences

See Section 2 above. Key differences per fold determine enter_floor/enter_strong thresholds and position sizing.



### F. T4 Event Priority Reranking Impact (early period)

- T4 allocation_mode = event_priority vs T3 allocation_mode = fifo

- T4 reranks candidates by: geo > macro > earnings > other, then by entry_prob, then by runup

- In the early period, T4 selected 49 unique trades that T3 did not, and missed 39 trades that T3 selected.



## 9. Missing Source Fields

- `prob_at_trigger (not captured in allocation logs)`

- `prob_slope_24h (not captured in allocation logs)`

- `prob_surge_since_t0 (not captured in allocation logs — only feat_runup_since_t0 available)`

- `trailing_stop_level (internal to sim_kernel, not exposed)`

- `hard_floor_stop_level (internal to sim_kernel, not exposed)`

- `exit_signal_state (internal to sim_kernel, not exposed)`

- `peak_price_since_entry (would require bar-by-bar replay)`

- `gross_exposure columns in daily_gap_decomposition (require bar-level mark-to-market)`

- `unrealized_pnl columns in daily_equity and daily_gap_decomposition (require bar-level replay)`

- `cash_available_before_decision in candidate_disposition (not logged per-candidate)`

- `capital_required in candidate_disposition (not logged per-candidate)`

- `cem_seed (randomized per run, not persisted)`

- `sharpe/maxdd/return in fit (only aggregate cem_score persisted per fold)`
