# H1 Expectation-First Protocol Report

Protocol: `protocol:27d2e1dfd3cb6939`  
Dataset: `dataset:c2f24dd859e9265f`  
Mapping: `mapping:4c5f242a558d750e`  
Polarity: `polarity:fcaa9411dd18eb28`  
Policy: `policy:43edcc4e3291b49c`  
Implementation: `implementation:9855b005845f9e5c`  
Run: `h1run:f6dea116d90d21f6`

## Primary hypothesis

> **E[observed net equity return from T_theta to the last eligible pre-event close | frozen Polymarket-identified stock-event observation] > 0**

The primary estimand is the empirical conditional mean of observed net returns. It is not alpha, and H1 does not evaluate a trading strategy. Candidate observations are mapped stock-event intervals identified through Polymarket; equal-weight economic events and clustered/block bootstraps evaluate uncertainty in that same observational claim.

## Primary results

- Candidate observations: N=1, mean -0.638%, median -0.638%, positive-return frequency 0.00%, one-sided mean p=—.
- True economic events: N=1, mean -0.638%, median -0.638%, positive-return frequency 0.00%, one-sided mean p=—.

### Candidate-observation mean with dependence-aware uncertainty

| cluster_type | n | n_clusters | mean | ci | p_cluster |
| --- | --- | --- | --- | --- | --- |
| economic_event_id | 1 | 1.0 | -0.638% | [—, —] | — |
| symbol | 1 | 1.0 | -0.638% | [—, —] | — |
| entry_week_block | 1 | 1.0 | -0.638% | [—, —] | — |
| entry_month_block | 1 | 1.0 | -0.638% | [—, —] | — |

### Equal-weight economic-event expectation

| cluster_type | n | n_clusters | mean | ci | p_cluster |
| --- | --- | --- | --- | --- | --- |
| entry_week_block | 1 | 1.0 | -0.638% | [—, —] | — |
| entry_month_block | 1 | 1.0 | -0.638% | [—, —] | — |

## What the expectation tests actually establish

- The sample estimate of the conditional raw net-return expectation is positive at both levels: -0.638% per candidate observation and -0.638% per equal-weight economic event.
- The evidence is not uniform through time. At the stricter economic-event level, the month-block interval is [—, —] with p=—; this does not establish a positive population expectation under month-level dependence.
- The event-level mean is upper-tail dependent: after removing only the best 1% of events, the mean becomes — with p=—.
- Candidate-level cost robustness is materially better: even at three times the modeled transaction costs, the estimated mean is -0.863%. But removing the best 5% of candidate returns changes the mean to —.
- These results support a promising positive conditional-mean estimate, not a completed proof that the expectation is positive across new independent events and future periods. A positive expectation does not require more than half of observations to be positive; the median and positive-return frequency are distributional diagnostics, not alternative definitions of H1.

## Tail and concentration sensitivity

No rows.

Contribution shares above 100% mean that the remaining observations collectively lost money and the upper tail more than accounted for the full positive total.

| level | variant | n | share_of_total_return |
| --- | --- | --- | --- |
| candidate_observation | top_1pct_share_of_total_return | 1 | 100.0% |
| candidate_observation | top_5pct_share_of_total_return | 1 | 100.0% |
| candidate_observation | top_10pct_share_of_total_return | 1 | 100.0% |
| economic_event | top_1pct_share_of_total_return | 1 | 100.0% |
| economic_event | top_5pct_share_of_total_return | 1 | 100.0% |
| economic_event | top_10pct_share_of_total_return | 1 | 100.0% |

## Net-return sensitivity to transaction-cost assumptions

| variant | n | mean | median | positive_frequency | p_t |
| --- | --- | --- | --- | --- | --- |
| modeled_cost_x_0 | 1 | -0.525% | -0.525% | 0.00% | — |
| modeled_cost_x_0.5 | 1 | -0.581% | -0.581% | 0.00% | — |
| modeled_cost_x_1 | 1 | -0.638% | -0.638% | 0.00% | — |
| modeled_cost_x_2 | 1 | -0.751% | -0.751% | 0.00% | — |
| modeled_cost_x_3 | 1 | -0.863% | -0.863% | 0.00% | — |

## Event-family and calendar sensitivity

| variant | n | mean | median | positive_frequency | p_t |
| --- | --- | --- | --- | --- | --- |
| other | 1 | -0.638% | -0.638% | 0.00% | — |

Candidate-level monthly mean return is positive in 0 of 1 observed entry months. The three weakest and three strongest months are:

| variant | n | mean | median | positive_frequency |
| --- | --- | --- | --- | --- |
| 2025-03 | 1 | -0.638% | -0.638% | 0.00% |

## Executable timing

Exact same-session ordering is verified for 0 of 1 rows. Current probability and equity artifacts are date-normalized, so the historical same-day close cannot be declared executable from the stored evidence.

## End-date and event-uncertainty provenance

- Versioned scheduled-end snapshot available at signal: False.
- Actual public outcome timestamp available: False.
- Until those fields exist, `T_e-1` observations are provisional for confirmatory H1 because the project cannot prove the event remained unresolved at the measured interval endpoint.

## Research-stage separation

| level | research_stage | n | mean | median | positive_frequency | p_t | stage_is_genuinely_untouched |
| --- | --- | --- | --- | --- | --- | --- | --- |
| candidate_observation | discovery | 1 | -0.638% | -0.638% | 0.00% | — | False |
| economic_event | discovery | 1 | -0.638% | -0.638% | 0.00% | — | False |

No historical stage is labeled genuinely untouched. The next prospective, version-frozen sample is the confirmatory holdout.

## Most influential leave-one-out exclusions

No rows.

## Secondary benchmark diagnostics — after-cost stock vs benchmark

These controls compare the after-cost dollar PnL of each stock trade against an equal-notional benchmark ETF trade over the same window. The sector control only uses trades with a real sector mapping; unknown-sector rows are excluded from the sector ETF comparison instead of being silently treated as SPY.

| level | benchmark | n | n_stock_better | n_benchmark_better | share_stock_better | mean_stock_net_pnl | mean_benchmark_net_pnl | mean_excess_net_pnl | p_t |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| candidate_observation | sector_etf | 0 | 0 | 0 | nan% | $nan | $nan | $nan | — |
| economic_event | sector_etf | 0 | 0 | 0 | nan% | $nan | $nan | $nan | — |
| candidate_observation | SPY | 1 | 0 | 1 | 0.00% | $-63.41 | $16.37 | $-79.78 | — |
| economic_event | SPY | 1 | 0 | 1 | 0.00% | $-63.41 | $16.37 | $-79.78 | — |

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
