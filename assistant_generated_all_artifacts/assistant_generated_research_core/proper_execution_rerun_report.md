# Corrected Execution Logic and Full Rerun

## Final technical conclusion

The original stop trigger was based on information available before the current bar, but the fill model was invalid during overnight gaps. The corrected engine now uses a coherent daily-bar execution specification:

1. Entry remains at the equity Close after probability eligibility.
2. No stop is evaluated on the entry bar because the position is acquired at that Close.
3. The running peak is updated after the current bar's checks, so the current High affects only the next bar.
4. Once the profit lock is armed, the active protective stop is the higher of:
   - the prior-peak ATR trailing stop; and
   - the integer-percentage profit floor.
5. A standing protective stop is evaluated before a probability exit that is executed at the Close.
6. If Open is at or below the active stop, the asset exits at Open.
7. Otherwise, if Low reaches the active stop, the asset exits at the stop level.
8. Prices remain unrounded internally; rounding is only for reporting.
9. For an overnight gap exit, the benchmark rotation uses the benchmark Open. For an intraday stop, the current daily-bar implementation re-enters the benchmark at Close; immediate intraday re-entry would require synchronized intraday benchmark data.
10. A missing Open never falls back to the favorable stop price. The conservative fallback is the daily Low. No realized OOS stop exit required that fallback.

## OOS results

| Engine | SPY return | SPY excess | SPY Sharpe | SPY max DD | QQQ return | QQQ excess | QQQ Sharpe | QQQ max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Original flawed, frozen | 23.6478% | 15.1279 pp | 2.8326 | -7.1042% | 29.5029% | 11.9116 pp | 2.9611 | -7.0821% |
| Gap fix only, frozen | -1.5885% | -10.1084 pp | -0.1116 | -12.3907% | 3.7601% | -13.8312 pp | 0.5154 | -12.9156% |
| Proper logic, frozen | 0.2325% | -8.2874 pp | 0.1252 | -12.3808% | 6.6316% | -10.9597 pp | 0.7961 | -12.5466% |
| Proper logic, retrained | 6.1913% | -2.3286 pp | 0.8387 | -17.0312% | 2.9259% | -14.6653 pp | 0.4186 | -15.7922% |

## What the rerun means

The proper frozen-policy replay is the cleanest estimate of how the original selected policies perform under coherent execution. It produces:

- SPY: **+0.2325%**, versus passive SPY at **+8.5199%**.
- QQQ: **+6.6316%**, versus passive QQQ at **+17.5912%**.

The original policy therefore remains positive in absolute terms under the same-time gap rotation convention, but it does not outperform either benchmark.

Retraining CEM after correcting execution changes the outcome:

- SPY improves to **+6.1913%**, but remains **2.3286 percentage points below SPY**.
- QQQ falls to **+2.9259%**, which is **14.6653 percentage points below QQQ**.

This asymmetry is important. Correct retraining helps SPY but hurts QQQ. The single static CEM fit does not generalize reliably from the pre-2026 training period to the 2026 OOS period.

## Why performance still fails

The exit attribution under proper logic shows a consistent structure:

- Profit-lock exits are strongly profitable.
- ATR trailing-stop exits are large losers.
- Final `resolution-1d` exits are the main aggregate loss bucket.
- Probability exits are weak or negative.

For the retrained SPY policy, profit locks earn approximately **$34.2k**, while resolution exits lose **$20.6k**, trailing stops lose **$5.7k**, and probability exits lose **$3.6k**.

For the retrained QQQ policy, profit locks earn approximately **$20.9k**, while resolution exits lose **$14.2k**, trailing stops lose **$8.3k**, and probability exits lose **$1.1k**.

The core problem after fixing execution is therefore not the stop implementation. It is that too many positions do not achieve favorable follow-through and remain open until the endpoint or reach a wide ATR stop.

## Correct implementation recommendation

The production engine should use the corrected logic in `proper_execution_rerun.py`. The strategy research should then proceed in this order:

1. Treat the corrected engine as immutable infrastructure.
2. Rebuild `prices.pkl` natively as `(timestamp, Open, High, Low, Close)` on one adjustment scale.
3. Rerun every optimizer and ablation from scratch. Policies trained under the flawed fill model are invalid.
4. Use walk-forward fitting rather than a single static CEM fit.
5. Optimize a benchmark-aware objective, because the economic alternative is remaining in SPY or QQQ. A suitable objective is the Sharpe ratio of daily excess returns, with drawdown and transaction-cost penalties.
6. Add a pre-specified no-follow-through or time-decay exit and test it out of sample. This is the most direct target because `resolution-1d` exits dominate losses.
7. Do not change the candidate universe, exit rule, allocator, and objective in one run. Each modification must have a frozen-policy counterfactual and a separate retrained result.
8. Rerun the full T1+T2+T3+T4 strategy under this engine before using any previous portfolio result in the paper.

## Retrained policies

```json
{
  "SPY": {
    "atr_mult": 4.0,
    "lock_activate": 0.03388926465586962,
    "theta_out": 0.5322103152599196,
    "enter_strong": 0.7163470900997602,
    "enter_floor": 0.6907176688717882,
    "hold_days": 1,
    "max_prob_surge": 0.2,
    "max_price_runup": 0.05982453124663895,
    "position_size_pct": 0.11281196082918836,
    "max_concurrent": 9
  },
  "QQQ": {
    "atr_mult": 3.4774372004632568,
    "lock_activate": 0.03138054511517292,
    "theta_out": 0.5740526267205911,
    "enter_strong": 0.7925053945972143,
    "enter_floor": 0.7925053945972143,
    "hold_days": 1,
    "max_prob_surge": 0.2,
    "max_price_runup": 0.09245387391099363,
    "position_size_pct": 0.09983335778274578,
    "max_concurrent": 8
  }
}
```

## Bottom line

The code can be repaired without a complete rewrite. The corrected engine is internally coherent and the rerun is reproducible. However, the original baseline strategy does not retain benchmark-relative alpha after the repair, and retraining the same baseline CEM is insufficient—especially for QQQ.

The next valid experiment is not another stop tweak. It is the full walk-forward, benchmark-aware strategy rerun using the corrected immutable execution engine.
