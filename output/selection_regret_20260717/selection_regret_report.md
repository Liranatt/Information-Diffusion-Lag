# Trade-by-trade selection regret and H1 interpretation

Generated 2026-07-17 from the collapsed symbol-day opportunity universe. All opportunity dollars below use a standardized $10,000 slot. This prevents accidental claims that every missed trade could have been funded at once.

## Executive conclusion

H1 gives us a valuable but narrower result than ‘the strategy will make money’: the historical conditional stock-event return is positive before capacity selection, especially in geo events. The strategy failed to monetize it because the opportunity is concentrated, duplicated, regime-dependent, and benchmark-sensitive. The largest fix is not finding more signals; it is selecting the right exposure on crowded entry days and measuring active return against the benchmark.

## What H1 proves

- Candidate-observation level: 887 observations, mean net return +1.208%, median +0.188%, 51.97% positive, ordinary one-sided mean p=0.0002.
- Dependence-aware candidate bootstrap: economic-event clustering CI [+0.263%, +2.200%], p=0.0087; symbol and week clustering intervals include zero.
- Equal-weight true economic events: 750 events, mean +0.631%, positive-event frequency 50.27%, one-sided mean p=0.0402; month-block bootstrap p=0.1935.
- After removing the best 1% of economic events, mean falls to +0.029% with p=0.4523. After removing the best 5% of candidate observations, mean is -0.260%.
- Event family split: earnings mean +0.020% (n=692) versus geo +4.794% (n=172). Geo drives much of H1, and geo was negative in train but strongly positive in test.
- Timing audit: exact same-session ordering is verified for 0/887 historical rows; the next-stored-close convention gives candidate mean +1.229% but median -0.153%, and event mean +0.550% with p=0.055.

H1 therefore establishes a promising conditional expectation in this historical universe. It does not establish a deployable portfolio rule, future stationarity, or that the current selector can capture the mean.

## Money left on the table

The first table is the full trade-level accounting by benchmark and split. ‘Positive active’ means a missed symbol-day would have beaten the benchmark under the hard-cap counterfactual. ‘Selected active losses’ are selected trades that underperformed the benchmark under the same standardized $10,000 slot.

| benchmark | split | eligible | selected | missed | missed_positive_active_count | money_left_on_table_positive_active_10k | money_left_on_table_all_missed_active_10k | selected_active_losses_10k | selected_actual_losses | selected_actual_pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| QQQ | test | 387 | 179 | 208 | 96 | 32713.62 | -15824.58 | -34834.03 | -29441.40 | 6014.23 |
| QQQ | train | 258 | 132 | 126 | 59 | 15376.87 | -3973.36 | -16321.44 | -19103.29 | 6690.74 |
| SPY | test | 479 | 216 | 263 | 116 | 41436.60 | -13238.46 | -35354.88 | -35763.33 | 10321.56 |
| SPY | train | 312 | 137 | 175 | 88 | 21523.16 | -2649.41 | -18217.36 | -23851.57 | 6987.08 |

Interpretation: the positive missed-opportunity total is an upper bound on capital that could have been earned if each missed trade received an independent $10,000 slot. The actual portfolio could not take all of them, so the economically credible number is the same-day replacement regret below.

## Same-day trade competition

On each contested date, the existing analysis compares the worst selected trade with the best missed trade. This is a one-swap oracle: it tells us how much one better choice would have improved a $10,000 slot, but it uses future realized returns and is not itself tradable.

| benchmark | split | choice_days | positive_swap_days | mean_swap_pct | median_swap_pct | total_swap_dollars_10k | p90_swap_pct |
| --- | --- | --- | --- | --- | --- | --- | --- |
| QQQ | test | 36 | 31 | 5.53 | 4.95 | 19922.60 | 13.74 |
| QQQ | train | 17 | 16 | 4.22 | 2.55 | 7181.26 | 9.80 |
| SPY | test | 37 | 30 | 6.95 | 6.51 | 25726.11 | 14.91 |
| SPY | train | 19 | 15 | 6.18 | 6.19 | 11733.60 | 13.31 |

The test-period oracle improvement was positive on 30/37 SPY choice days and 31/36 QQQ choice days. The mean one-swap improvement was 6.953 percentage points for SPY and 5.534 points for QQQ, or approximately $695 and $553 per $10,000 replacement respectively. This is strong evidence that capacity/ranking is economically important; it is not proof that a model can realize the full amount.

## Trade-by-trade diagnosis

Every collapsed symbol-day is retained in `trade_by_trade_collapsed.csv`, with selection status, connection strength, family, entry latency, hard-cap return, active return, T-1 return, and standardized dollars. The two most actionable slices are:

- `top_missed_active_opportunities.csv`: missed CRCL, NET, QCOM, USO and other trades show that the selector often left profitable capacity unused.
- `top_selected_active_losses.csv`: selected XLE geo trades and several earnings names such as AS, ARM, RBLX, RDDT, CMG, HD and COF are examples where capital was allocated to weak active outcomes.

The recurring pattern is not simply ‘winners were missed’. Selected trades are already better than the missed average in most cells, so the current selector contains information. The failure is ranking precision under crowded days: it chooses some good trades, but also spends scarce slots on much worse alternatives.

## What to improve

1. Collapse first: one symbol-day equals one exposure. Keep multiple Polymarket questions as supporting evidence, not multiple independent positions.
2. Rank, do not hard-filter: connection strength is the most robust simple ranking signal. A hard connection=1 gate overfits and removes useful trades.
3. Optimize active return: score each candidate against SPY/QQQ and the sector ETF, not nominal stock return alone. Earnings exposure is materially sector beta.
4. Separate families: use hard-cap/profit-lock for earnings/other; investigate T_e-1 only for the SPY geo 2–3 day latency subgroup. Do not transfer that rule to QQQ without new evidence.
5. Penalize concentration: cap symbol, sector, and event-family exposure. In geo, do not treat XLE as an automatic substitute for USO/BNO.
6. Treat latency as a feature: geo entries 4+ days after T_theta were sharply negative in both periods; 2–3 days was the most stable positive subgroup.
7. Use conservative allocation: keep the corrected hard-cap policy, target-fill diagnostics, gap-aware execution, and benchmark rebuys in every backtest.

## Quant plan

### Phase 1 — freeze the estimand and data

- Freeze point-in-time t_e, outcome timestamps, signal timestamp, and executable hourly prices.
- Log every eligible candidate, every rejected candidate, every same-day rank, requested allocation, realized allocation, and benchmark counterfactual.
- Pre-register the primary metric as portfolio excess return versus benchmark, with event-cluster bootstrap intervals; nominal return is secondary.

### Phase 2 — build a transparent selector

- Baseline: current selector after symbol-day collapse.
- Candidate score: connection rank, entry probability, latency, run-up, sector exposure, and family; use only features available at entry.
- Compare simple weighted ranking, within-day percentile ranking, and a monotone/shrinkage model. Avoid unrestricted tree/pairwise models until a new holdout proves generalization.
- Portfolio decision: select the top K subject to symbol/sector/family caps and a minimum expected active-return threshold estimated only from training data.

### Phase 3 — pre-registered walk-forward test

- Use a genuinely untouched future window; do not tune on the current test again.
- Run SPY and QQQ separately, plus sector-ETF controls.
- Compare current, connection rank, oracle upper bound, random same-day selection, and trade-everything symbol-day baselines.
- Require positive excess in at least 3/4 temporal blocks, positive median block excess, no single month contributing more than 40% of total active PnL, and a block-bootstrap lower bound above zero before considering deployment.

### Phase 4 — live shadow mode

- Shadow-log decisions for 8–12 weeks with no capital or very small capped capital.
- Review same-day regret, missed positive active trades, selected active losses, slippage, target-fill rate, and benchmark rebalancing every week.
- Promote only if live shadow results preserve the pre-registered ranking edge after costs and the decision log is complete.

## Output files

- `trade_selection_regret_totals.csv` — money-left and selected-loss totals.
- `trade_selection_regret_summary.csv` — family-level decomposition.
- `same_day_regret_summary.csv` and `same_day_regret_detail.csv` — contested-day replacement analysis.
- `trade_by_trade_collapsed.csv` — complete trade-by-trade audit.
- `top_missed_active_opportunities.csv` and `top_selected_active_losses.csv` — actionable examples.
- `h1_primary_excerpt.csv`, `h1_tail_sensitivity_excerpt.csv`, `h1_family_excerpt.csv` — machine-readable H1 extracts.
