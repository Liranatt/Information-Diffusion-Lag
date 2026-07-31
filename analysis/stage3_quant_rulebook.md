# Earnings trading: evidence-led rulebook and next test

**Research date:** 2026-07-17  
**Status:** Target B is frozen as the earnings entry selector.  This is a
research specification, not a claim that the historical backtest is a live
trading edge.

## The objective

The objective is not to identify the stock with the largest hypothetical
upside.  A trade is successful when its **net active return** is positive:

```
stock return over the actual holding interval
- return of the chosen benchmark over that same interval
- all incremental trading costs
```

SPY and QQQ are separate mandates/sensitivities.  A live portfolio must choose
one benchmark before trading; it must not switch benchmark after seeing a
trade's result.

## What the completed evidence supports

### H1 is a warning, not a parameter source

H1's 887 raw candidates had mean net return +1.21%, median +0.19%, and a
51.97% win rate.  But the largest 1% of trades contributed 45.68% of total
P&L; removing the largest 5% changed mean return to -0.26%.  It therefore
does **not** justify a take-profit target, a winner-ranking rule, or a broad
entry rule.  It does justify treating concentration and loss control as first
class risks.

### Target B is a rejection/risk selector, not a maximum-upside selector

The frozen chronological OOF universe has 415 direct-issuer earnings
candidates.  Target B admitted 118 (28.4%).  On the same-day decisions it
reduced bad-trade incidence relative to random legal selection:

| Benchmark | Target B never-profitable | Random legal | Target B persistent loser | Random legal |
| --- | ---: | ---: | ---: | ---: |
| SPY | 16.7% | 24.4% | 16.7% | 24.6% |
| QQQ | 24.0% | 29.4% | 20.0% | 31.0% |

It was not the best ranking for the hindsight legal oracle.  That is expected:
we are selecting candidates likely to survive and beat the benchmark, rather
than attempting to capture the ex-post largest peak.

The fresh, minute-level Polymarket history covered all 415 OOF candidates.
Trajectory slopes, crossings, acceleration, volatility and relative repricing
did not improve Target B in paired chronological folds.  They are therefore
not an entry veto or ranking feature.

### Exit evidence says to trail, not to take a fixed profit

With the frozen Target B selector, the corrected reference exit produced:

| Benchmark | Total return | Benchmark return | Active excess | Active max drawdown | OOF fold median excess | Positive folds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| SPY | 2.33% | 1.29% | +1.05% | -2.24% | +0.39% | 3 / 5 |
| QQQ | 4.25% | 1.49% | +2.77% | -3.18% | +1.14% | 4 / 5 |

Fixed two- and four-day exits were weak.  Holding to the legal event exit and
the active-profit-giveback rule had attractive aggregate returns, but less
reliable SPY fold results.  The corrected reference exit is the most balanced
current baseline.  The pre-defined volatility-scaled stop is the only Stage 2E
challenger worth carrying into Stage 3: it had better aggregate return and
drawdown, but failed the paired median-fold test (especially for SPY).  It is
not promoted.

The 105 selected trade instances under the reference exit also show why an
early, tight close-based stop is unsafe.  There were 24 early-loser/recovery
instances.  After a day-two active loss of at least 2%, all two QQQ instances
and two of three SPY instances ultimately remained negative, but this is far
too small and post-hoc to set a new threshold.  A threshold must be tested
out-of-sample, not fitted to those five observations.

## Operational entry rules now

These are the frozen rules for a small live/paper deployment.  They are also
the control arm for the next test.

1. **Universe:** a valid, direct-issuer Polymarket question about that
   company's earnings only.  Exclude geo, macro, indirect mappings, ambiguous
   polarity, missing market data, and an event with fewer than two tradable
   sessions before its resolution cut-off.
2. **Signal clock:** save the raw Polymarket observation timestamp, polarity
   and probability.  A usable signal is at least 0.70 after polarity
   normalisation.  The current `enter_strong=0.75` setting adds no effective
   confirmation because `enter_floor=0.70` and `hold_days=1`; operationally the
   threshold is 0.70.
3. **Executable fill:** place the order only at the first tradable price *after*
   that timestamp.  Do not backfill a same-day closing price that was not known
   when the probability observation arrived.
4. **Price extension guard:** do not enter if the stock has already risen more
   than 10% from the event's `t0` reference price to the decision price.
5. **Target B admission:** calculate the frozen regularised Target B score
   (predicted active return per expected slot day).  Enter only if the score is
   positive.
6. **Same-day capacity:** rank eligible candidates by Target B score descending,
   then shorter expected slot days, then the frozen deterministic tie breakers.
   Cap the portfolio at ten concurrent positions, 9% maximum allocation per
   position and 90% gross event exposure.  There were no historical capacity
   collisions, so this is a risk limit rather than a discovered alpha source.

### What may be known before entry

Use only the timestamped market probability, its polarity and metadata; event
resolution window; current and pre-entry price/volume history; current sector
and benchmark prices; and the frozen Target B inputs.  The model's useful
pre-entry information is deliberately short horizon: expected slot days,
20-trading-day stock-minus-sector repricing, probability at trigger,
probability change since `t0`, two-week asset trend, and recent candidate
congestion.

Do **not** add YTD, 3-month, 6-month or one-year trend filters today.  They
were available in development but were not promoted by the OOF selector.  The
20-day relative move and the 10% event-window run-up guard already address the
question we actually need to answer: has this specific earnings belief already
been repriced into this stock?

## Operational exit rules now

There is no fixed take-profit.  A fixed 2- or 4-day exit was weaker in both
benchmark replays.  The exit should preserve a valid event trade while
protecting a realised gain:

1. **Initial protection:** from the first post-entry bar, maintain a standing
   trailing stop `3.65 × pre-entry ATR(20)` below the best stock price achieved
   since entry.  This is volatility-scaled; it is not a universal 3% or 5%
   stock stop.
2. **Profit lock:** once the stock's realised peak return reaches 3%, raise the
   stop to lock the integer percentage of that peak.  For example, a 3.8% peak
   locks at +3%; a 6.2% peak locks at +6%.  This is a trailing lock, not an
   instruction to sell at +3%.
3. **Information invalidation:** exit if the timestamped, polarity-normalised
   Polymarket probability falls below 0.55.
4. **Time exit:** otherwise exit on the last legally tradable session before
   resolution (`T_e - 1`).  Never hold through the earnings/resolution event.
5. **No additional early-loss rule:** do not add a day-1/day-2 close stop or a
   four-days-underwater rule.  The latter was already tested and did not beat
   the reference exit reliably; early recoveries are common enough to make a
   new tight threshold unjustified.

The current median pre-entry ATR was about 2.9%, so `3.65 × ATR` is roughly a
10.6% *price* distance for a median candidate.  That is not a promise of a
10.6% maximum loss—overnight gaps can fill worse.  Risk should therefore be
controlled primarily by position size.  At the 9% maximum allocation, that
median stop distance is roughly 0.95% of portfolio equity before gap risk.

## The next test: Stage 3A, execution-safe exit replay

Do not hunt a new profit percentage.  The immediate test is whether the
existing, best-balanced rule remains viable after the decision clock is made
fully executable.

**Data required:** archived five-minute (or finer) OHLCV for every H1/OOF
selected stock and its fixed benchmark, with regular-session timestamps, plus
the already-downloaded raw Polymarket history.  Daily OHLC cannot establish the
price actually obtainable after an intraday probability signal or a stop fill.

**Frozen entry:** the six rules above, with an entry at the first subsequent
five-minute bar.  No same-day-close substitution and no observation at or
after the entry timestamp.

**Only two pre-registered exit arms:**

* **A — control:** corrected reference exit, exactly as described above.
* **B — challenger:** the already-defined volatility-scaled loss exit: from
  holding day two, exit after a stock loss of
  `min(max(2%, 2 × ATR(20)), 8%)`, using an executable stop/gap fill.  It has
  no independent take-profit; the legal `T_e - 1` exit remains.

Use the identical five chronological outer folds, fees, capacity and entry
events.  Report paired fold active return, active drawdown, win rate, turnover,
and exact fills for SPY and QQQ separately.  A challenger may replace A only
if it has positive paired median fold improvement in both benchmarks, does not
rely on one fold, and does not materially worsen drawdown.  Otherwise retain A
and stop changing the entry selector.

This gives a practical path now: run Target B only in timestamp-logged paper
or very small size using the control exit, collect real fills, and let Stage 3A
answer the one unresolved question—whether a faster, volatility-scaled failure
exit improves active return after real execution—without re-optimising the
whole system.
