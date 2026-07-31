# Independent Audit of the Original Standard CEM Stop-Execution Logic

## Technical verdict

The original **stop trigger calculation is valid under daily bars**, because the running peak used on day `d` contains only highs observed through day `d-1`. The suspected same-bar High-before-Low bug is therefore **not present**.

The original **stop fill calculation is invalid for overnight gaps**. Both the trailing stop and profit lock trigger from the day's Low and then record:

```python
exit_price = max(day_low, active_stop)
```

When the market opens and remains below the stop, this can record a fill at a price that never traded. The original fill is never below the daily Low, but it can be above the daily High.

A minimal Open-aware correction repairs this specific execution error without changing the strategy:

```python
if open_price <= active_stop:
    exit_price = open_price
elif low_price <= active_stop:
    exit_price = active_stop
else:
    no_stop_exit
```

The Open must first be verified to use the same price scale as the frozen High/Low/Close series. Current Yahoo raw data matched 209,854 of 211,435 original rows (99.252252%). Another 1,482 rows (0.700925%) required a common-factor rescaling, mainly because of corporate actions. Blindly appending current raw Yahoo Opens would therefore be incorrect.

No CEM retraining or policy optimization was performed in this audit.

## Frozen-policy counterfactual

| benchmark   |   original_return_pct |   open_aware_return_pct |   return_change_pp |   benchmark_return_pct |   original_sharpe |   open_aware_sharpe |   original_max_dd_pct |   open_aware_max_dd_pct |   original_n_trades |   open_aware_n_trades |
|:------------|----------------------:|------------------------:|-------------------:|-----------------------:|------------------:|--------------------:|----------------------:|------------------------:|--------------------:|----------------------:|
| SPY         |               23.6478 |                 -1.5885 |           -25.2363 |                 8.5199 |            2.8326 |             -0.1116 |               -7.1042 |                -12.3907 |                 226 |                   225 |
| QQQ         |               29.5029 |                  3.7601 |           -25.7428 |                17.5912 |            2.9611 |              0.5154 |               -7.0821 |                -12.9156 |                 210 |                   212 |

The candidate-level trade generation was unchanged: SPY produced 499 candidate trades under both engines and QQQ produced 464 under both. Entry dates, exit dates, and exit reasons were identical. The small change in realized portfolio trade counts is a downstream capital/allocation effect caused by changed sale proceeds.

The direct fill-only correction, holding the original realized quantities and allocations fixed, reduced stop-exit P&L by **$24,202.72 for SPY** and **$23,606.93 for QQQ**. The full portfolio replay additionally incorporates altered compounding, position quantities, benchmark rotations, and capacity interactions.

## 1. Original execution semantics

1. **Polarity and eligibility.** The candidate's probability path is used as raw `P(YES)` for polarity `+1`, flipped to `1-P(YES)` for polarity `-1`, and skipped for polarity `0`.
2. **Probability entry.** Starting at normalized `T_theta`, the engine scans probability points in stored order. It enters immediately at `enter_strong`, or after `hold_days` consecutive points at or above `enter_floor`.
3. **Entry vetoes.** The trade is rejected when the point-in-time probability surge or pre-entry equity run-up exceeds its frozen-policy cap.
4. **Equity entry.** The first stored equity bar whose timestamp is at or after the accepted probability timestamp is selected. Entry price is that bar's **Close**.
5. **ATR.** ATR is the arithmetic mean of True Range over up to 15 transitions ending on the entry bar. It uses High, Low, previous Close.
6. **Entry day.** No probability, trailing-stop, profit-lock, or resolution exit is checked on the entry bar.
7. **Daily exit priority after entry.**
   - probability collapse below `theta_out`;
   - trailing ATR stop;
   - profit lock;
   - final eligible pre-resolution bar.
8. **Trailing stop.** `active_stop = entry_price * (1 + prior_peak_return - atr_mult * ATR/entry_price)`. Trigger: `day_low <= active_stop`.
9. **Profit lock.** It arms when prior peak return is at least `lock_activate`. The locked return is `int(peak * 100) / 100`, i.e. truncation to an integer percentage point. Trigger: `day_low < floor_price`.
10. **Original stop fill.** Both stop branches use `max(day_low, stop_price)`.
11. **Peak update.** Only after all exit tests, the engine sets `peak = max(peak, current_day_high_return)`. Thus today's High affects tomorrow's stop, not today's.
12. **Resolution.** The holding path contains bars strictly earlier than `T_e - 1 day`; the last such bar exits at Close if no earlier rule triggered.
13. **Costs.** Portfolio P&L charges benchmark sell, asset buy, asset sell, and benchmark rebuy. Each leg includes the IB-style commission rule, SEC fee on sells, and 5 bp slippage as a cash cost.

## 2. Verdict by suspected issue

| Issue | Verdict | Basis |
|---|---|---|
| Overnight gap stop fill | **Confirmed bug** | Low triggers the stop, but `max(low, stop)` assigns the stop even when Open and High are below it. |
| Same-bar High/Low ordering | **Not a bug** | Peak is updated after exit checks; day `d` uses only the peak through day `d-1`. |
| Profit-lock trigger | **Valid under the stated daily-bar semantics** | It uses prior peak and an explicitly truncated integer-percent floor. |
| Profit-lock gap fill | **Confirmed bug** | It uses the same `max(low, floor)` fill formula as the trailing stop. |
| Python versus Numba drift | **Not present in the notebook** | Numba decorates the same `_scan` function; the fallback executes the same body. |
| Fill outside feasible daily range | **Confirmed above High; impossible below Low** | 54 SPY and 50 QQQ original stop fills were above the day's High; none were below Low. |

## 3. Synthetic tests

| case                                                 |   active_stop |   open |   high |   low |   close |   original_fill |   open_aware_fill | stop_exit   |
|:-----------------------------------------------------|--------------:|-------:|-------:|------:|--------:|----------------:|------------------:|:------------|
| normal intraday stop crossing                        |           100 |    104 |    108 |    98 |     102 |        100.0000 |          100.0000 | True        |
| overnight gap through stop                           |           100 |     94 |     98 |    90 |      96 |        100.0000 |           94.0000 | True        |
| no stop crossing                                     |           100 |    104 |    110 |   102 |     108 |        nan      |          nan      | False       |
| same-bar ordering: prior stop 90, current H=115 L=95 |            90 |    100 |    115 |    95 |     108 |        nan      |          nan      | False       |
| gap below stop followed by recovery                  |           100 |     90 |    105 |    85 |     102 |        100.0000 |           90.0000 | True        |

For the same-bar case, the previous peak implies a stop of 90. The current bar Low is 95, so no stop occurs. The High of 115 is incorporated only after the checks and can create a higher stop for the next bar.

## 4. Stop-exit attribution

| benchmark   | exit_category                   |   number_of_trades |   original_pnl |   corrected_pnl |   difference |
|:------------|:--------------------------------|-------------------:|---------------:|----------------:|-------------:|
| QQQ         | valid intraday crossing         |                 23 |        9626.42 |         9626.42 |         0.00 |
| QQQ         | overnight gap through stop      |                102 |       38872.47 |        15487.96 |    -23384.51 |
| QQQ         | missing Open                    |                  0 |           0.00 |            0.00 |         0.00 |
| QQQ         | inconsistent OHLC or adjustment |                  2 |        -115.51 |         -337.93 |      -222.42 |
| QQQ         | other                           |                  0 |           0.00 |            0.00 |         0.00 |
| SPY         | valid intraday crossing         |                 29 |        7656.27 |         7656.27 |         0.00 |
| SPY         | overnight gap through stop      |                105 |       37907.78 |        14269.36 |    -23638.42 |
| SPY         | missing Open                    |                  0 |           0.00 |            0.00 |         0.00 |
| SPY         | inconsistent OHLC or adjustment |                  3 |         182.72 |         -381.58 |      -564.30 |
| SPY         | other                           |                  0 |           0.00 |            0.00 |         0.00 |

The category “inconsistent OHLC or adjustment” reserves rows whose current Yahoo HLC required scale handling, even when the economic fill behavior was a gap. Including those rows by fill behavior, **107 of 137 SPY stop exits (78.10%)** and **103 of 127 QQQ stop exits (81.10%)** opened below the active stop.

Additional diagnostics:

| benchmark   |   stop_exits |   gap_affected_exits |   gap_affected_pct |   original_fills_above_daily_high |   original_fills_below_daily_low |   gap_then_recovered_to_stop |   gap_never_reached_stop |   earnings_gap_exits |   earnings_gap_pct |   earnings_share_of_gap_correction_pct |   top_10_share_of_correction_pct |   top_20_share_of_correction_pct |
|:------------|-------------:|---------------------:|-------------------:|----------------------------------:|---------------------------------:|-----------------------------:|-------------------------:|---------------------:|-------------------:|---------------------------------------:|---------------------------------:|---------------------------------:|
| SPY         |          137 |                  107 |              78.10 |                                54 |                                0 |                           53 |                       54 |                   97 |              90.65 |                                  83.68 |                            36.74 |                            54.60 |
| QQQ         |          127 |                  103 |              81.10 |                                50 |                                0 |                           53 |                       50 |                   94 |              91.26 |                                  85.49 |                            37.87 |                            56.63 |

The effect is strongly associated with earnings-related exits, but it is not explained by one or two trades. The largest 10 corrections account for 36.74% of SPY's direct correction and 37.87% of QQQ's; the largest 20 account for 54.60% and 56.63%.

## 5. Largest individual corrections

### SPY

| symbol   |   market_id | exit_date   | exit_reason    |   active_stop |   open_price |   original_exit_price |   corrected_exit_price |   original_pnl |   corrected_pnl |   pnl_difference |
|:---------|------------:|:------------|:---------------|--------------:|-------------:|----------------------:|-----------------------:|---------------:|----------------:|-----------------:|
| USO      |     1705690 | 2026-04-08  | profit_lock_3% |        143.11 |       119.06 |                143.11 |                 119.06 |         328.40 |        -1713.75 |         -2042.15 |
| SMTC     |     2245146 | 2026-05-19  | profit_lock_8% |        148.65 |       128.34 |                148.65 |                 128.34 |         973.66 |         -872.65 |         -1846.31 |
| GETY     |     1454725 | 2026-03-06  | profit_lock_2% |          0.92 |         0.86 |                  0.92 |                   0.86 |         149.15 |         -657.86 |          -807.01 |
| IONQ     |     1369348 | 2026-02-19  | profit_lock_2% |         34.79 |        32.72 |                 34.79 |                  32.72 |         199.87 |         -488.73 |          -688.60 |
| CXM      |     2312808 | 2026-05-26  | profit_lock_4% |          5.48 |         5.20 |                  5.48 |                   5.20 |         471.54 |         -216.56 |          -688.10 |
| BBBY     |     1360923 | 2026-02-12  | profit_lock_3% |          5.55 |         5.26 |                  5.55 |                   5.26 |         303.51 |         -317.90 |          -621.41 |
| URBN     |     1387000 | 2026-02-23  | profit_lock_3% |         71.04 |        67.65 |                 71.04 |                  67.65 |         319.78 |         -245.77 |          -565.55 |
| AMPL     |     1339376 | 2026-02-09  | profit_lock_4% |          7.60 |         7.24 |                  7.60 |                   7.24 |         417.14 |         -144.24 |          -561.38 |
| USO      |     1359830 | 2026-03-04  | profit_lock_8% |         94.17 |        90.22 |                 94.17 |                  90.22 |         929.06 |          388.46 |          -540.60 |
| LULU     |     2335553 | 2026-05-28  | profit_lock_6% |        134.99 |       129.97 |                134.99 |                 129.97 |         779.77 |          248.20 |          -531.57 |

### QQQ

| symbol   |   market_id | exit_date   | exit_reason    |   active_stop |   open_price |   original_exit_price |   corrected_exit_price |   original_pnl |   corrected_pnl |   pnl_difference |
|:---------|------------:|:------------|:---------------|--------------:|-------------:|----------------------:|-----------------------:|---------------:|----------------:|-----------------:|
| USO      |     1705690 | 2026-04-08  | profit_lock_3% |        143.11 |       119.06 |                143.11 |                 119.06 |         316.80 |        -1653.28 |         -1970.08 |
| SMTC     |     2245146 | 2026-05-19  | profit_lock_8% |        148.65 |       128.34 |                148.65 |                 128.34 |         995.17 |         -891.72 |         -1886.89 |
| GETY     |     1454725 | 2026-03-06  | profit_lock_2% |          0.92 |         0.86 |                  0.92 |                   0.86 |         144.56 |         -637.76 |          -782.32 |
| CXM      |     2312808 | 2026-05-26  | profit_lock_4% |          5.48 |         5.20 |                  5.48 |                   5.20 |         477.37 |         -219.11 |          -696.48 |
| IONQ     |     1369348 | 2026-02-19  | profit_lock_2% |         34.79 |        32.72 |                 34.79 |                  32.72 |         193.21 |         -472.65 |          -665.86 |
| GTLB     |     1399738 | 2026-02-27  | profit_lock_7% |         28.24 |        26.74 |                 28.24 |                  26.74 |         755.18 |          121.33 |          -633.85 |
| CBRL     |     2335609 | 2026-05-28  | profit_lock_8% |         35.97 |        34.46 |                 35.97 |                  34.46 |        1042.38 |          432.97 |          -609.41 |
| BBBY     |     1360923 | 2026-02-12  | profit_lock_3% |          5.55 |         5.26 |                  5.55 |                   5.26 |         294.30 |         -308.28 |          -602.58 |
| URBN     |     1387000 | 2026-02-23  | profit_lock_3% |         71.04 |        67.65 |                 71.04 |                  67.65 |         310.19 |         -238.42 |          -548.61 |
| AMPL     |     1339376 | 2026-02-09  | profit_lock_4% |          7.60 |         7.24 |                  7.60 |                   7.24 |         404.29 |         -139.83 |          -544.12 |

## 6. Is adding Open enough?

### For the confirmed stop-fill defect: yes, conditionally

Daily Open is sufficient to distinguish the two cases that the original HLC-only data cannot distinguish:

- Open at or below the stop: gap-through fill at Open.
- Open above the stop and Low through the stop: intraday fill at the stop.

Intraday bars are not required for this distinction because the stop level is fixed before the day begins. They would be required only for a more granular execution model—auction liquidity, halt behavior, exact slippage after a large gap—or if the strategy were changed to let the current day's High modify the current day's stop.

### Conditions that must accompany the patch

- **Scale consistency:** attach an Open only after proving it matches the frozen HLC scale. This audit rescaled common-factor corporate-action differences.
- **OHLC integrity:** fail or classify rows where `low <= min(open, close) <= max(...) <= high` relationships or source matching fail.
- **Timestamp discipline:** keep probability observations and equity sessions on explicit timestamps. In the frozen data, all 20,547 probability points and all 1,161 `T_theta` values are at 00:00 UTC, so the same-day Close entry and probability-exit decisions do not use post-close probabilities. The code would need stronger session-time gating if intraday probability timestamps were introduced later.
- **Probability-exit execution remains a separate convention:** the code checks a probability exit before either stop and fills it at that day's Close. Among the realized portfolio trades, 4 of 22 SPY probability exits and 3 of 26 QQQ probability exits also breached the active profit-lock floor on that day. Because the probability observations were available at 00:00 UTC, a production specification should say whether the probability exit is submitted at the Open or whether the protective stop remains active until a Close order executes. Adding Open does not decide that policy by itself.
- **Entry day:** the original engine deliberately disables stops on the entry day. The patch preserves this; it is a design choice, not part of the gap-fill correction.
- **Slippage:** the existing 5 bp cash cost remains unchanged. Filling a gap at official Open is a defensible minimal stop-market model; a worse-than-Open stress model is optional and should be reported separately.
- **Accounting:** corrected proceeds must flow through asset-sale cost, benchmark rebuy, later position sizing, and capacity. The full portfolio replay in this audit does so.

## Final answers

1. **Was the original stop trigger logic itself valid?** Yes, for a stop fixed from information through the prior bar.
2. **Was the original stop fill logic valid?** No.
3. **Was there a same-bar High/Low ordering bug?** No.
4. **Was there an overnight-gap fill bug?** Yes, in both trailing-stop and profit-lock exits.
5. **Does Open fully fix the original problem?** It fully fixes the confirmed stop-gap fill defect when Open is aligned to the same source, date, and adjustment scale and missing/inconsistent rows fail closed. It does not, by itself, specify how a pre-open probability exit should interact with an already-active stop.
6. **Are intraday data required?** Not for the original strategy's fixed-before-day stop semantics. They are required only for finer execution realism or a redesigned same-bar trailing rule. The probability-exit ambiguity can instead be removed by defining an explicit Open-versus-Close order policy.
7. **Minimal patch or broader rewrite?** A minimal stop-execution patch is sufficient for the confirmed defect; no strategy redesign is required. A small additional execution-policy clarification is advisable for probability exits. The original headline backtest results do not survive the corrected frozen-policy replay.
