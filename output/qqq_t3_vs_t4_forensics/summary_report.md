# Forensic Attribution: QQQ T1+T2+T3 vs T1+T2+T3+T4

## 1. Period Metrics

     config   period  return_pct  benchmark_return_pct  excess_pct  max_dd_pct  sharpe  n_trades  win_rate_pct  total_pnl  avg_pnl
   T1+T2+T3 full_oos       22.93                 17.65        5.28      -12.73    2.17       195          63.6   18559.01    95.17
   T1+T2+T3    early        1.32                  8.91       -7.59      -12.73    0.31       166          61.4   -1118.62    -6.74
   T1+T2+T3     late       20.71                  7.00       13.71       -7.03    5.32        29          75.9   19677.63   678.54
T1+T2+T3+T4 full_oos       27.36                 17.65        9.71       -8.47    2.42       192          63.5   21644.24   112.73
T1+T2+T3+T4    early        6.70                  8.91       -2.21       -8.47    1.07       158          61.4    4003.27    25.34
T1+T2+T3+T4     late       18.63                  7.00       11.63       -7.03    5.05        34          73.5   17640.97   518.85


## 2. Policy Parameter Comparison

### T1+T2+T3 (FIFO)

 fold_id  atr_mult  lock_activate  theta_out  enter_strong  enter_floor  hold_days  max_prob_surge  max_price_runup  position_size_pct  max_concurrent
       1  2.844538       0.026269   0.560888      0.800000     0.800000          1             0.2         0.104385           0.094848               9
       2  3.502367       0.027403   0.545304      0.800000     0.800000          2             0.2         0.127719           0.079165               9
       3  4.000000       0.020000   0.545031      0.749149     0.749149          2             0.2         0.121319           0.114561              11
       4  3.517613       0.020000   0.450000      0.781845     0.738136          1             0.2         0.115685           0.091237              10
       5  4.000000       0.020000   0.554756      0.780608     0.780608          1             0.2         0.157390           0.094384               9

### T1+T2+T3+T4 (Event Priority)

 fold_id  atr_mult  lock_activate  theta_out  enter_strong  enter_floor  hold_days  max_prob_surge  max_price_runup  position_size_pct  max_concurrent
       1  2.844538       0.026269   0.560888      0.800000     0.800000          1             0.2         0.104385           0.094848               9
       2  3.199737       0.024474   0.555283      0.800000     0.800000          1             0.2         0.083874           0.091244               9
       3  3.502647       0.020000   0.505429      0.781259     0.743525          1             0.2         0.120810           0.104681               9
       4  4.000000       0.020000   0.537618      0.754621     0.749415          2             0.2         0.130551           0.077318               9
       5  4.000000       0.020000   0.571755      0.788974     0.788974          1             0.2         0.130378           0.101461               9


## 3. Trade Overlap

- Selected by both: 128 candidates

- Selected only by T1+T2+T3: 67 candidates

- Selected only by T1+T2+T3+T4: 64 candidates



## 4. Top 10 Trades Selected Only by T1+T2+T3 (Early Period)

symbol entry_date  exit_date     pnl  pnl_pct                                                   question
   NET 2026-04-27 2026-05-06 1815.54  16.7635             Will Cloudflare (NET) beat quarterly earnings?
  NXPI 2026-04-17 2026-04-24 1682.65  12.7688    Will NXP Semiconductors (NXPI) beat quarterly earnings?
   USO 2026-03-03 2026-03-09 1499.25  11.5427              Will another country strike Iran by March 31?
   CVS 2026-04-27 2026-04-30  732.93   6.7778             Will CVS Health (CVS) beat quarterly earnings?
  REAL 2026-02-20 2026-02-25  644.34   4.7616              Will RealReal (REAL) beat quarterly earnings?
    BP 2026-04-17 2026-04-23  494.75   3.7612                      Will BP (BP) beat quarterly earnings?
   IVZ 2026-04-17 2026-04-22  361.04   2.7405                Will Invesco (IVZ) beat quarterly earnings?
   MTB 2026-01-05 2026-01-09  359.18   1.7863               Will M&T Bank (MTB) beat quarterly earnings?
   PNC 2026-01-05 2026-01-09  358.68   1.7872 Will PNC Financial Services (PNC) beat quarterly earnings?
    KR 2026-02-26 2026-03-02  345.42   1.7730                  Will Kroger (KR) beat quarterly earnings?


## 5. Top 10 Trades Selected Only by T1+T2+T3+T4 (Early Period)

symbol entry_date  exit_date     pnl  pnl_pct                                                              question
  BBBY 2026-04-17 2026-04-21 1874.81   8.7192                Will Bed Bath & Beyond (BBBY) beat quarterly earnings?
   USO 2026-03-02 2026-03-04 1220.89   7.7792                                        Will Iran strike UAE in March?
   USO 2026-04-09 2026-04-14  965.35   4.7821                      Will Iran strike Saudi Arabia by April 30, 2026?
   KFY 2026-02-25 2026-02-27  948.15   4.7757                        Will Korn Ferry (KFY) beat quarterly earnings?
   USO 2026-03-25 2026-03-31  654.65  13.7463                      Will Iran strike Saudi Arabia by April 30, 2026?
  MSFT 2026-01-23 2026-01-27  572.02   2.9229                        Will Microsoft (MSFT) beat quarterly earnings?
   TSM 2026-01-05 2026-01-07  556.49   2.7853              Will Taiwan Semiconductor (TSM) beat quarterly earnings?
  CRWD 2026-02-23 2026-02-25  526.52   2.7832             Will CrowdStrike Holdings (CRWD) beat quarterly earnings?
   USO 2026-03-31 2026-04-06  459.40   9.7573 Will Iran take military action against a Gulf State on April 1, 2026?
  IONQ 2026-04-29 2026-05-01  405.14   7.7589                             Will IONQ (IONQ) beat quarterly earnings?


## 6. Top 10 Skipped-by-T4 but Selected-by-T3 (by Counterfactual PnL)

      date symbol               skip_reason  counterfactual_pnl_of_skipped_candidate  counterfactual_net_car_of_skipped_candidate            positions_blocking_capital
2026-04-27    NET      insufficient_capital                                  17.0606                                     0.123123 META;SOFI;OXY;WING;DDOG;RDDT;DLB;PLTR
2026-03-03    USO          duplicate_symbol                                  15.6652                                     0.146379     PD;USO;GAMB;CXM;XLE;DOCU;DKS;PLBY
2026-04-17   NXPI      insufficient_capital                                  12.9658                                     0.106494             IBM;AXP;MMM;EFX;BBBY;INTC
2026-05-12   WDAY            max_concurrent                                   8.6495                                     0.088418     AS;AZN;ADI;NVDA;BJ;INTU;LOW;DE;RL
2026-02-20   REAL      insufficient_capital                                   8.1908                                     0.068981    TKO;IMAX;NVDA;ZM;SPCE;BOX;MDB;ASAN
2026-03-30    USO same_day_symbol_collapsed                                   7.0169                                     0.016038                       BNO;USO;DAL;XLE
2026-05-26     DG      insufficient_capital                                   6.7561                                     0.056565     DELL;ADSK;AVGO;LULU;IOT;RBRK;DOCU
2026-04-27    CVS      insufficient_capital                                   6.2915                                     0.057630 META;SOFI;OXY;WING;DDOG;RDDT;DLB;PLTR
2026-01-26   MCHP            max_concurrent                                   6.1104                                     0.054757  MSFT;META;PFE;PEP;XOM;DIS;F;FOXA;CMG
2026-04-17     BP      insufficient_capital                                   3.9471                                     0.035510             IBM;AXP;MMM;EFX;BBBY;INTC


## 7. Top 10 T4 Positions that Blocked Capital (Early Period)

candidate_id symbol  avg_unrealized_pnl_pct  days_open  max_capital_locked
 1705685_BNO    BNO               -9.564444         10             4865.63
 1280193_FIS    FIS               -6.798000          4             5103.28
 1399646_MDB    MDB               -6.312000          6            18606.24
1435762_PLBY   PLBY               -6.074000         13            20354.87
2003561_RDDT   RDDT               -5.202857          8              166.28
 1508634_ACN    ACN               -4.255714          8             6490.16
1555597_CHWY   CHWY               -4.160000         12             5037.20
1494817_LULU   LULU               -3.496667          7             6464.94
2003527_SOFI   SOFI               -3.496667          7            21606.00
 1494785_ASO    ASO               -3.365000          9             8152.56


## 8. Measured Attribution: Where Did the Early Gap Come From?



### A. Trade Choice (early period)

- Shared trades in T3: 103, total PnL: $-96.15

- Shared trades in T4: 103, total PnL: $2743.09

- PnL gap on SHARED trades (T3 - T4): $-2839.24

- T3-only trades: 63, total PnL: $-1022.47

- T4-only trades: 55, total PnL: $1260.18

- PnL gap from DIVERGENT trade selection (T3-only minus T4-only): $-2282.65



### B. Position Sizing (early period)

- Avg position size T3 (shared trades): 9.17%

- Avg position size T4 (shared trades): 11.01%



### C. Capital Blocking (early period)

- Candidates skipped by T4 but selected by T3 (early): 58

- Total counterfactual PnL% lost by T4 skipping: -5.42%



### D. Exit Timing (shared trades, early period)

- Avg holding days T3: 5.3

- Avg holding days T4: 5.2



### E. CEM Parameter Differences

See Section 2 above. Key differences per fold determine enter_floor/enter_strong thresholds and position sizing.



### F. T4 Event Priority Reranking Impact (early period)

- T4 allocation_mode = event_priority vs T3 allocation_mode = fifo

- T4 reranks candidates by: geo > macro > earnings > other, then by entry_prob, then by runup

- In the early period, T4 selected 55 unique trades that T3 did not, and missed 63 trades that T3 selected.



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
