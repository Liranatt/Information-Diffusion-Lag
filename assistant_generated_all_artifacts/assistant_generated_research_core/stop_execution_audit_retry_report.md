# Independent Stop-Execution Audit — Retry Using `prices_1.pkl`

## Concise technical verdict

This rerun used the newly uploaded `prices_1.pkl` directly as the Open-price supplement. It contains five-field bars:

```text
(timestamp, Open, High, Low, Close)
```

The rerun **exactly reproduced the original frozen-policy backtest before applying any correction**:

- SPY: 23.6478% return and 226 realized trades.
- QQQ: 29.5029% return and 210 realized trades.
- Trade keys, exit prices and P&L matched the supplied original CSVs exactly.

The code verdict is unchanged:

1. **Overnight-gap stop fill:** confirmed bug.
2. **Same-bar High/Low ordering:** not a bug.
3. **Profit-lock gap fill:** confirmed bug.
4. **Minimal correction:** use Open only to distinguish a gap-through from a normal intraday crossing.
5. **No CEM retraining was performed.**

The corrected frozen-policy replay materially changes the economic result:

| Benchmark   |   Original return % |   Open-aware return % |   Change pp |   Benchmark return % |   Original Sharpe |   Open-aware Sharpe |   Original max DD % |   Open-aware max DD % |   Original trades |   Open-aware trades |
|:------------|--------------------:|----------------------:|------------:|---------------------:|------------------:|--------------------:|--------------------:|----------------------:|------------------:|--------------------:|
| SPY         |             23.6478 |               -1.5885 |    -25.2363 |               8.5199 |            2.8326 |             -0.1116 |             -7.1042 |              -12.3907 |               226 |                 225 |
| QQQ         |             29.5029 |                3.7601 |    -25.7428 |              17.5912 |            2.9611 |              0.5154 |             -7.0821 |              -12.9156 |               210 |                 212 |

## What changed relative to the prior audit response

The prior response placed several common-factor corporate-action rows in a separate adjustment category. The new `prices_1.pkl` permits those Opens to be aligned directly to the frozen HLC scale.

Consequently, the cleaner attribution is:

- SPY: **107** overnight gaps and **30** valid intraday crossings.
- QQQ: **103** overnight gaps and **24** valid intraday crossings.
- Missing or inconsistent Open on realized stop exits: **zero**.
- Missing or inconsistent Open on all candidate-level stop exits: **zero**.

The headline frozen-policy portfolio results are unchanged.

## 1. Original execution semantics

The original notebook implements the following chronological sequence:

1. Resolve signal polarity for each question-symbol pair.
2. Scan stored probability points beginning at normalized `T_theta`.
3. Accept immediately above `enter_strong`, or after `hold_days` consecutive observations above `enter_floor`.
4. Reject candidates exceeding the probability-surge or equity-run-up caps.
5. Select the first equity bar whose timestamp is at or after the accepted probability timestamp.
6. Enter at that bar's **Close**.
7. Calculate ATR from up to 15 True Range transitions ending on the entry bar.
8. Disable all exits on the entry bar.
9. On later bars, test exits in this priority:
   - probability exit;
   - trailing ATR stop;
   - profit lock;
   - final eligible pre-resolution bar.
10. Update the running peak only **after** all exit checks.

### Trailing-stop code

```python
elif ret_l <= peak - stop_dist:
    reason = 1
    cand = entry_price * (1.0 + peak - stop_dist)
    cc = ll if ll > cand else cand
```

This is equivalent to:

```python
exit_price = max(day_low, active_stop)
```

### Profit-lock code

```python
elif peak >= lock_activate:
    hard_floor_pct = int(peak * 100.0)
    hard_floor = hard_floor_pct / 100.0
    if ret_l < hard_floor:
        reason = 2
        cand = entry_price * (1.0 + hard_floor)
        cc = ll if ll > cand else cand
```

The hard floor is the prior peak truncated to an integer percentage return.

### Peak update

```python
if i_rel == 0:
    peak = 0.0
elif ret_h > peak:
    peak = ret_h
```

Because this occurs after exit checks, the High of day `d` can affect only day `d+1`.

## 2. Verdict on each suspected bug

| Suspected issue | Verdict | Evidence |
|---|---|---|
| Stop fill during an overnight gap | **Confirmed bug** | The daily Low triggers the stop, then the code records `max(Low, stop)`. |
| Fill above the daily High | **Confirmed** | Exact pre-rounding engine fills exceeded High in 54 SPY and 50 QQQ stop exits. |
| Fill below the daily Low | **Not present** | `max(Low, stop)` cannot produce a value below Low. |
| Same-bar High raises and triggers the stop | **Not a bug** | Peak is updated only after exit checks. |
| Profit-lock trigger uses current High | **Not a bug** | It uses the prior running peak. |
| Profit-lock overnight-gap fill | **Confirmed bug** | It shares the same `max(Low, floor)` formula. |
| Python/Numba semantic drift | **Not present in the notebook** | Numba decorates the same `_scan` body; the transparent rerun used the pure-Python body. |

The exact engine generated 54 SPY and 50 QQQ fills above High. The exported trade records round prices to two decimals, leaving 53 and 49 visibly above High after rounding; one CXM case rounds down to the exact daily High.

## 3. Open validation using the newly uploaded file

The frozen HLC dataset contains 211,435 rows. `prices_1.pkl` contains 444 symbols, versus 505 in the original frozen file.

| classification         |   rows |       pct |
|:-----------------------|-------:|----------:|
| common_factor_rescaled |   1486 |  0.702816 |
| exact_hlc              | 182020 | 86.087923 |
| inconsistent_hlc       |     95 |  0.044931 |
| missing_open           |  27834 | 13.164329 |

Among dates and symbols present in both files:

- exact HLC match: **99.1389%**;
- common-factor rescaling: **0.8094%**;
- inconsistent HLC: **0.0517%**.

The new file does not contain 61 of the original 505 symbols, but this does **not** prevent the counterfactual:

- every one of the 599 candidate-level trailing/profit-lock exits had a usable exact or common-factor-rescaled Open;
- every one of the 264 realized stop exits had a usable Open;
- no corrected stop exit silently fell back to the original stop fill.

## 4. Frozen-policy attribution

This table holds the original realized position quantities and allocation fixed, changing only the exit fill and the resulting asset-sale and benchmark-rebuy costs:

| Benchmark   | Exit category                   |   Trades |   Original P&L |   Corrected P&L |   Difference |
|:------------|:--------------------------------|---------:|---------------:|----------------:|-------------:|
| SPY         | valid intraday crossing         |       30 |        6947.26 |         6947.26 |         0.00 |
| SPY         | overnight gap through stop      |      107 |       38799.51 |        14596.79 |    -24202.72 |
| SPY         | missing Open                    |        0 |           0.00 |            0.00 |         0.00 |
| SPY         | inconsistent OHLC or adjustment |        0 |           0.00 |            0.00 |         0.00 |
| SPY         | other                           |        0 |           0.00 |            0.00 |         0.00 |
| QQQ         | valid intraday crossing         |       24 |        8878.25 |         8878.25 |         0.00 |
| QQQ         | overnight gap through stop      |      103 |       39505.13 |        15898.20 |    -23606.93 |
| QQQ         | missing Open                    |        0 |           0.00 |            0.00 |         0.00 |
| QQQ         | inconsistent OHLC or adjustment |        0 |           0.00 |            0.00 |         0.00 |
| QQQ         | other                           |        0 |           0.00 |            0.00 |         0.00 |

Direct fill-only correction:

- SPY: **-$24,202.72**.
- QQQ: **-$23,606.93**.

The full portfolio replay differs slightly from the fixed-allocation attribution because changed proceeds alter subsequent benchmark holdings, position quantities, compounding and capacity.

## 5. Stop-exit diagnostics

| Benchmark   |   Stop exits |   Gap exits |   Gap exits % |   Exact fills above High |   Rounded records above High |   Recovered to stop |   Never reached stop |   Earnings gap exits |   Earnings gap % |   Top 10 correction share % |   Top 20 correction share % |
|:------------|-------------:|------------:|--------------:|-------------------------:|-----------------------------:|--------------------:|---------------------:|---------------------:|-----------------:|----------------------------:|----------------------------:|
| SPY         |          137 |         107 |         78.10 |                       54 |                           53 |                  53 |                   54 |                   97 |            90.65 |                       36.74 |                       54.60 |
| QQQ         |          127 |         103 |         81.10 |                       50 |                           49 |                  53 |                   50 |                   94 |            91.26 |                       37.87 |                       56.63 |

The gap effect is concentrated in earnings events, but not in one or two isolated trades. The largest ten corrections explain about 36.7% of the SPY change and 37.9% of the QQQ change.

## 6. Largest individual corrections

| Benchmark   | Symbol   |   Market | Exit date   | Reason         |   Active stop |   Open |   Original fill |   Corrected fill |   Original P&L |   Corrected P&L |   Difference |
|:------------|:---------|---------:|:------------|:---------------|--------------:|-------:|----------------:|-----------------:|---------------:|----------------:|-------------:|
| SPY         | USO      |  1705690 | 2026-04-08  | profit_lock_3% |        143.11 | 119.06 |          143.11 |           119.06 |         328.40 |        -1713.75 |     -2042.15 |
| SPY         | SMTC     |  2245146 | 2026-05-19  | profit_lock_8% |        148.65 | 128.34 |          148.65 |           128.34 |         973.66 |         -872.65 |     -1846.31 |
| SPY         | GETY     |  1454725 | 2026-03-06  | profit_lock_2% |          0.92 |   0.86 |            0.92 |             0.86 |         149.15 |         -657.86 |      -807.01 |
| SPY         | IONQ     |  1369348 | 2026-02-19  | profit_lock_2% |         34.79 |  32.72 |           34.79 |            32.72 |         199.87 |         -488.73 |      -688.60 |
| SPY         | CXM      |  2312808 | 2026-05-26  | profit_lock_4% |          5.48 |   5.20 |            5.48 |             5.20 |         471.54 |         -216.56 |      -688.10 |
| SPY         | BBBY     |  1360923 | 2026-02-12  | profit_lock_3% |          5.55 |   5.26 |            5.55 |             5.26 |         303.51 |         -317.90 |      -621.41 |
| SPY         | URBN     |  1387000 | 2026-02-23  | profit_lock_3% |         71.04 |  67.65 |           71.04 |            67.65 |         319.78 |         -245.77 |      -565.55 |
| SPY         | AMPL     |  1339376 | 2026-02-09  | profit_lock_4% |          7.60 |   7.24 |            7.60 |             7.24 |         417.14 |         -144.24 |      -561.38 |
| SPY         | USO      |  1359830 | 2026-03-04  | profit_lock_8% |         94.17 |  90.22 |           94.17 |            90.22 |         929.06 |          388.46 |      -540.60 |
| SPY         | LULU     |  2335553 | 2026-05-28  | profit_lock_6% |        134.99 | 129.97 |          134.99 |           129.97 |         779.77 |          248.20 |      -531.57 |
| QQQ         | USO      |  1705690 | 2026-04-08  | profit_lock_3% |        143.11 | 119.06 |          143.11 |           119.06 |         316.80 |        -1653.28 |     -1970.08 |
| QQQ         | SMTC     |  2245146 | 2026-05-19  | profit_lock_8% |        148.65 | 128.34 |          148.65 |           128.34 |         995.17 |         -891.72 |     -1886.89 |
| QQQ         | GETY     |  1454725 | 2026-03-06  | profit_lock_2% |          0.92 |   0.86 |            0.92 |             0.86 |         144.56 |         -637.76 |      -782.32 |
| QQQ         | CXM      |  2312808 | 2026-05-26  | profit_lock_4% |          5.48 |   5.20 |            5.48 |             5.20 |         477.37 |         -219.11 |      -696.48 |
| QQQ         | IONQ     |  1369348 | 2026-02-19  | profit_lock_2% |         34.79 |  32.72 |           34.79 |            32.72 |         193.21 |         -472.65 |      -665.86 |
| QQQ         | GTLB     |  1399738 | 2026-02-27  | profit_lock_7% |         28.24 |  26.74 |           28.24 |            26.74 |         755.18 |          121.33 |      -633.85 |
| QQQ         | CBRL     |  2335609 | 2026-05-28  | profit_lock_8% |         35.97 |  34.46 |           35.97 |            34.46 |        1042.38 |          432.97 |      -609.41 |
| QQQ         | BBBY     |  1360923 | 2026-02-12  | profit_lock_3% |          5.55 |   5.26 |            5.55 |             5.26 |         294.30 |         -308.28 |      -602.58 |
| QQQ         | URBN     |  1387000 | 2026-02-23  | profit_lock_3% |         71.04 |  67.65 |           71.04 |            67.65 |         310.19 |         -238.42 |      -548.61 |
| QQQ         | AMPL     |  1339376 | 2026-02-09  | profit_lock_4% |          7.60 |   7.24 |            7.60 |             7.24 |         404.29 |         -139.83 |      -544.12 |

## 7. Synthetic tests

| case                                                 |   active_stop |   open |   high |   low |   close |   original_fill |   open_aware_fill | stop_exit   |
|:-----------------------------------------------------|--------------:|-------:|-------:|------:|--------:|----------------:|------------------:|:------------|
| normal intraday stop crossing                        |           100 |    104 |    108 |    98 |     102 |        100.0000 |          100.0000 | True        |
| overnight gap through stop                           |           100 |     94 |     98 |    90 |      96 |        100.0000 |           94.0000 | True        |
| no stop crossing                                     |           100 |    104 |    110 |   102 |     108 |        nan      |          nan      | False       |
| same-bar ordering: prior stop 90, current H=115 L=95 |            90 |    100 |    115 |    95 |     108 |        nan      |          nan      | False       |
| gap below stop followed by recovery                  |           100 |     90 |    105 |    85 |     102 |        100.0000 |           90.0000 | True        |

For the potential same-bar ordering case, the prior stop is 90 and the current Low is 95. No stop occurs. The current High of 115 is incorporated only after the checks and may raise the following day's stop.

## 8. Is adding Open sufficient?

### For the confirmed stop-fill defect

**Yes.** For a stop fixed before the trading day:

```python
if open_price <= active_stop:
    exit_price = open_price
elif low_price <= active_stop:
    exit_price = active_stop
else:
    no_stop_exit
```

Daily Open is sufficient to distinguish:

- an overnight gap through the stop;
- a normal intraday crossing;
- no crossing.

Intraday data are not required for that distinction because the original stop level does not use the current bar's High.

### Conditions that remain necessary

- Open must be on the same date and adjustment scale as the frozen HLC data.
- Missing or inconsistent Open must fail closed or be explicitly classified.
- The original entry-day stop suppression remains a design choice.
- The existing 5 bp slippage remains a separate cash cost.
- A worse-than-Open gap-fill stress model would be an additional conservatism test, not the minimal repair.

### Probability exits

The code gives probability exits priority over protective stops and fills them at the day's Close. The retry reconstructed all realized probability exits:

| benchmark   |   probability_exits |   same_day_trailing_crosses |   same_day_profit_lock_crosses |
|:------------|--------------------:|----------------------------:|-------------------------------:|
| QQQ         |                  26 |                           0 |                              3 |
| SPY         |                  22 |                           0 |                              4 |

Four of 22 SPY probability exits and three of 26 QQQ probability exits also crossed an active profit-lock floor that day. There were no same-day trailing-stop crossings. Because all stored probability points and all `T_theta` timestamps are at 00:00 UTC, this is not evidence of post-close look-ahead, but the production execution specification should state whether the probability exit is submitted at Open or whether the stop remains active until a Close exit.

## Final answers

1. **Was the original stop trigger logic itself valid?** Yes.
2. **Was the original stop fill logic valid?** No.
3. **Was there a same-bar High/Low ordering bug?** No.
4. **Was there an overnight-gap fill bug?** Yes, in both trailing stops and profit locks.
5. **Does adding Open fully fix the stop problem?** Yes, with date/scale validation and fail-closed handling.
6. **Are intraday data required?** No for the original fixed-before-day stop semantics.
7. **Minimal patch or broader rewrite?** The stop engine can be repaired with a minimal patch. Probability-exit timing should be specified separately.
8. **Does the original headline result survive?** No. Under the frozen policies, SPY becomes -1.5885% and QQQ becomes +3.7601%.
