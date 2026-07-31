# H1 Expectation-First Protocol Report

Protocol: `protocol:27d2e1dfd3cb6939`  
Dataset: `dataset:a9d4121be5263b65`  
Mapping: `mapping:7f71faa0818da83a`  
Polarity: `polarity:fcaa9411dd18eb28`  
Policy: `policy:3ddd58c18cde2b21`  
Implementation: `implementation:5d1b9c9c5322df6c`  
Run: `h1run:094a5e0fd28181b2`

## Primary hypothesis

> **E[observed net equity return from T_theta to the last eligible pre-event close | frozen Polymarket-identified stock-event observation] > 0**

The primary estimand is the empirical conditional mean of observed net returns. It is not alpha, and H1 does not evaluate a trading strategy. Candidate observations are mapped stock-event intervals identified through Polymarket; equal-weight economic events and clustered/block bootstraps evaluate uncertainty in that same observational claim.

## Primary results

- Candidate observations: N=882, mean +1.272%, median +0.229%, positive-return frequency 52.27%, one-sided mean p=0.0001.
- True economic events: N=747, mean +0.670%, median +0.015%, positive-return frequency 50.33%, one-sided mean p=0.0331.

### Candidate-observation mean with dependence-aware uncertainty

| cluster_type | n | n_clusters | mean | ci | p_cluster |
| --- | --- | --- | --- | --- | --- |
| economic_event_id | 882 | 747.0 | +1.272% | [+0.300%, +2.265%] | 0.0069 |
| symbol | 882 | 385.0 | +1.272% | [-0.151%, +2.921%] | 0.0645 |
| entry_week_block | 882 | 46.0 | +1.272% | [-0.112%, +2.880%] | 0.0570 |
| entry_month_block | 882 | 14.0 | +1.272% | [-0.458%, +3.679%] | 0.1270 |

### Equal-weight economic-event expectation

| cluster_type | n | n_clusters | mean | ci | p_cluster |
| --- | --- | --- | --- | --- | --- |
| entry_week_block | 747 | 46.0 | +0.670% | [-0.419%, +2.042%] | 0.1521 |
| entry_month_block | 747 | 14.0 | +0.670% | [-0.396%, +2.471%] | 0.1822 |

## What the expectation tests actually establish

- The sample estimate of the conditional raw net-return expectation is positive at both levels: +1.272% per candidate observation and +0.670% per equal-weight economic event.
- The evidence is not uniform through time. At the stricter economic-event level, the month-block interval is [-0.396%, +2.471%] with p=0.1822; this does not establish a positive population expectation under month-level dependence.
- The event-level mean is upper-tail dependent: after removing only the best 1% of events, the mean becomes +0.050% with p=0.4181.
- Candidate-level cost robustness is materially better: even at three times the modeled transaction costs, the estimated mean is +1.018%. But removing the best 5% of candidate returns changes the mean to -0.211%.
- These results support a promising positive conditional-mean estimate, not a completed proof that the expectation is positive across new independent events and future periods. A positive expectation does not require more than half of observations to be positive; the median and positive-return frequency are distributional diagnostics, not alternative definitions of H1.

## Tail and concentration sensitivity

| level | variant | n | mean | median | positive_frequency | p_t |
| --- | --- | --- | --- | --- | --- | --- |
| candidate_observation | drop_top_1pct | 873 | +0.710% | +0.154% | 51.78% | 0.0026 |
| candidate_observation | symmetric_trim_1pct | 864 | +0.946% | +0.229% | 52.31% | <0.0001 |
| candidate_observation | drop_top_5pct | 837 | -0.211% | -0.053% | 49.70% | 0.8397 |
| candidate_observation | symmetric_trim_5pct | 792 | +0.621% | +0.229% | 52.53% | 0.0003 |
| candidate_observation | drop_top_10pct | 793 | -0.907% | -0.452% | 46.91% | 1.0000 |
| candidate_observation | symmetric_trim_10pct | 704 | +0.439% | +0.229% | 52.84% | 0.0015 |
| economic_event | drop_top_1pct | 739 | +0.050% | -0.046% | 49.80% | 0.4181 |
| economic_event | symmetric_trim_1pct | 731 | +0.293% | +0.015% | 50.34% | 0.0986 |
| economic_event | drop_top_5pct | 709 | -0.681% | -0.362% | 47.67% | 0.9995 |
| economic_event | symmetric_trim_5pct | 671 | +0.093% | +0.015% | 50.37% | 0.2947 |
| economic_event | drop_top_10pct | 672 | -1.246% | -0.535% | 44.79% | 1.0000 |
| economic_event | symmetric_trim_10pct | 597 | -0.002% | +0.015% | 50.42% | 0.5048 |

Contribution shares above 100% mean that the remaining observations collectively lost money and the upper tail more than accounted for the full positive total.

| level | variant | n | share_of_total_return |
| --- | --- | --- | --- |
| candidate_observation | top_1pct_share_of_total_return | 9 | 44.7% |
| candidate_observation | top_5pct_share_of_total_return | 45 | 115.7% |
| candidate_observation | top_10pct_share_of_total_return | 89 | 164.1% |
| economic_event | top_1pct_share_of_total_return | 8 | 92.7% |
| economic_event | top_5pct_share_of_total_return | 38 | 196.6% |
| economic_event | top_10pct_share_of_total_return | 75 | 267.5% |

## Net-return sensitivity to transaction-cost assumptions

| variant | n | mean | median | positive_frequency | p_t |
| --- | --- | --- | --- | --- | --- |
| modeled_cost_x_0 | 882 | +1.398% | +0.341% | 52.61% | <0.0001 |
| modeled_cost_x_0.5 | 882 | +1.335% | +0.285% | 52.61% | <0.0001 |
| modeled_cost_x_1 | 882 | +1.272% | +0.229% | 52.27% | 0.0001 |
| modeled_cost_x_2 | 882 | +1.145% | +0.065% | 50.91% | 0.0005 |
| modeled_cost_x_3 | 882 | +1.018% | -0.054% | 49.66% | 0.0016 |

## Event-family and calendar sensitivity

| variant | n | mean | median | positive_frequency | p_t |
| --- | --- | --- | --- | --- | --- |
| earnings | 692 | +0.020% | -0.125% | 49.42% | 0.4687 |
| geo | 168 | +5.237% | +3.484% | 63.69% | <0.0001 |
| other | 22 | +10.362% | +1.187% | 54.55% | 0.1018 |

Candidate-level monthly mean return is positive in 7 of 14 observed entry months. The three weakest and three strongest months are:

| variant | n | mean | median | positive_frequency |
| --- | --- | --- | --- | --- |
| 2026-06 | 7 | -5.402% | -3.660% | 14.29% |
| 2024-10 | 4 | -2.920% | -2.553% | 0.00% |
| 2025-06 | 43 | -2.290% | +0.154% | 53.49% |
| 2025-07 | 14 | +17.120% | +2.898% | 71.43% |
| 2026-03 | 114 | +7.767% | +6.593% | 69.30% |
| 2026-05 | 50 | +3.907% | +2.597% | 58.00% |

## Executable timing

Exact same-session ordering is verified for 0 of 882 rows. Current probability and equity artifacts are date-normalized, so the historical same-day close cannot be declared executable from the stored evidence.
Recomputing the observation interval from the next stored equity close gives a candidate-observation mean of +1.323% across N=846, median -0.089%, positive-return frequency 48.23%, p=<0.0001.
At true economic-event level under that timing convention: mean +0.633%, median -0.309%, positive-return frequency 46.27%, p=0.0358.

## End-date and event-uncertainty provenance

- Versioned scheduled-end snapshot available at signal: False.
- Actual public outcome timestamp available: False.
- Until those fields exist, `T_e-1` observations are provisional for confirmatory H1 because the project cannot prove the event remained unresolved at the measured interval endpoint.

## Research-stage separation

| level | research_stage | n | mean | median | positive_frequency | p_t | stage_is_genuinely_untouched |
| --- | --- | --- | --- | --- | --- | --- | --- |
| candidate_observation | model_selection | 292 | +0.352% | -0.215% | 48.97% | 0.3020 | False |
| candidate_observation | validation | 345 | +2.197% | +0.809% | 55.07% | <0.0001 | False |
| candidate_observation | discovery | 52 | -1.834% | -0.207% | 50.00% | 0.9926 | False |
| candidate_observation | retrospective_holdout | 193 | +1.845% | +0.406% | 52.85% | 0.0015 | False |
| economic_event | discovery | 16 | -0.375% | +0.288% | 56.25% | 0.6528 | False |
| economic_event | model_selection | 292 | +0.352% | -0.215% | 48.97% | 0.3020 | False |
| economic_event | validation | 271 | +0.003% | -0.377% | 48.71% | 0.4979 | False |
| economic_event | retrospective_holdout | 168 | +2.396% | +0.558% | 54.76% | <0.0001 | False |

No historical stage is labeled genuinely untouched. The next prospective, version-frozen sample is the confirmatory holdout.

## Most influential leave-one-out exclusions

| level | group_type | group_value | n_removed | leave_out_mean | change |
| --- | --- | --- | --- | --- | --- |
| candidate_observation | entry_month | 2026-03 | 114 | +0.308% | -0.964% |
| economic_event | event_family | geo | 35 | +0.332% | -0.337% |
| candidate_observation | symbol | USO | 104 | +0.337% | -0.935% |
| candidate_observation | event_family | geo | 168 | +0.339% | -0.933% |
| economic_event | entry_month | 2026-05 | 43 | +0.343% | -0.327% |
| economic_event | entry_month | 2025-07 | 12 | +0.362% | -0.308% |
| economic_event | event_family | other | 20 | +0.382% | -0.288% |
| economic_event | entry_month | 2026-03 | 41 | +0.513% | -0.157% |
| economic_event | entry_month | 2026-04 | 122 | +0.552% | -0.117% |
| economic_event | entry_month | 2025-12 | 16 | +0.643% | -0.026% |

## Secondary benchmark diagnostics — after-cost stock vs benchmark

These controls compare the after-cost dollar PnL of each stock trade against an equal-notional benchmark ETF trade over the same window. The sector control only uses trades with a real sector mapping; unknown-sector rows are excluded from the sector ETF comparison instead of being silently treated as SPY.

| level | benchmark | n | n_stock_better | n_benchmark_better | share_stock_better | mean_stock_net_pnl | mean_benchmark_net_pnl | mean_excess_net_pnl | p_t |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| candidate_observation | sector_etf | 680 | 339 | 341 | 49.85% | $-4.84 | $-24.52 | $19.68 | 0.2759 |
| economic_event | sector_etf | 678 | 338 | 340 | 49.85% | $-5.63 | $-24.49 | $18.86 | 0.2847 |
| candidate_observation | SPY | 882 | 432 | 450 | 48.98% | $126.97 | $33.43 | $93.54 | 0.0033 |
| economic_event | SPY | 747 | 355 | 392 | 47.52% | $67.55 | $20.88 | $46.66 | 0.0965 |

## Confirmatory status

The protocol and version identifiers are now reproducible, and the raw expectation is reported at candidate and economic-event levels with event/symbol/time dependence controls. Confirmatory status remains blocked by missing point-in-time end-date history, missing actual outcome timestamps, and unverifiable same-session execution in the historical daily artifacts. The prospective hourly logger should populate those fields without moving to minute-level decisions.

## Output files

- `h1_expectation_manifest.json` — immutable IDs, hashes, protocol, split and limitation declarations.
- `raw_expectation_true_event_level.csv` — real `event_id` aggregation.
- `h1_raw_expectation_cluster_inference.csv` — event, symbol, week and month bootstrap inference.
- `h1_timing_audit.csv` — same-session verification and next-session repricing.
- `h1_end_date_uncertainty_audit.csv` — scheduled-end and outcome-timestamp provenance.
- `h1_leave_one_out.csv` — event, family, ticker and month exclusions.
- `h1_sensitivity.csv` — tails, families, months, timing and cost multipliers.
- `h1_research_stage_summary.csv` — discovery, model selection, validation and retrospective holdout.
- `h1_secondary_benchmark_diagnostics.csv` — explicitly non-primary market-adjusted controls.
