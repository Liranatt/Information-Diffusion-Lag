# H1 Expectation-First Protocol Report

Protocol: `protocol:27d2e1dfd3cb6939`  
Dataset: `dataset:a9d4121be5263b65`  
Mapping: `mapping:7f71faa0818da83a`  
Polarity: `polarity:fcaa9411dd18eb28`  
Policy: `policy:3ddd58c18cde2b21`  
Implementation: `implementation:5d1b9c9c5322df6c`  
Run: `h1run:956974494894ee86`

## Primary hypothesis

> **E[observed net equity return from T_theta to the last eligible pre-event close | frozen Polymarket-identified stock-event observation] > 0**

The primary estimand is the empirical conditional mean of observed net returns. It is not alpha, and H1 does not evaluate a trading strategy. Candidate observations are mapped stock-event intervals identified through Polymarket; equal-weight economic events and clustered/block bootstraps evaluate uncertainty in that same observational claim.

## Primary results

- Candidate observations: N=887, mean +1.208%, median +0.188%, positive-return frequency 51.97%, one-sided mean p=0.0002.
- True economic events: N=750, mean +0.631%, median +0.013%, positive-return frequency 50.27%, one-sided mean p=0.0402.

### Candidate-observation mean with dependence-aware uncertainty

| cluster_type | n | n_clusters | mean | ci | p_cluster |
| --- | --- | --- | --- | --- | --- |
| economic_event_id | 887 | 750.0 | +1.208% | [+0.263%, +2.200%] | 0.0087 |
| symbol | 887 | 387.0 | +1.208% | [-0.184%, +2.776%] | 0.0652 |
| entry_week_block | 887 | 46.0 | +1.208% | [-0.185%, +2.834%] | 0.0678 |
| entry_month_block | 887 | 16.0 | +1.208% | [-0.630%, +3.651%] | 0.1427 |

### Equal-weight economic-event expectation

| cluster_type | n | n_clusters | mean | ci | p_cluster |
| --- | --- | --- | --- | --- | --- |
| entry_week_block | 750 | 45.0 | +0.631% | [-0.465%, +2.004%] | 0.1657 |
| entry_month_block | 750 | 16.0 | +0.631% | [-0.417%, +2.407%] | 0.1935 |

## What the expectation tests actually establish

- The sample estimate of the conditional raw net-return expectation is positive at both levels: +1.208% per candidate observation and +0.631% per equal-weight economic event.
- The evidence is not uniform through time. At the stricter economic-event level, the month-block interval is [-0.417%, +2.407%] with p=0.1935; this does not establish a positive population expectation under month-level dependence.
- The event-level mean is upper-tail dependent: after removing only the best 1% of events, the mean becomes +0.029% with p=0.4523.
- Candidate-level cost robustness is materially better: even at three times the modeled transaction costs, the estimated mean is +0.954%. But removing the best 5% of candidate returns changes the mean to -0.260%.
- These results support a promising positive conditional-mean estimate, not a completed proof that the expectation is positive across new independent events and future periods. A positive expectation does not require more than half of observations to be positive; the median and positive-return frequency are distributional diagnostics, not alternative definitions of H1.

## Tail and concentration sensitivity

| level | variant | n | mean | median | positive_frequency | p_t |
| --- | --- | --- | --- | --- | --- | --- |
| candidate_observation | drop_top_1pct | 878 | +0.662% | +0.145% | 51.48% | 0.0047 |
| candidate_observation | symmetric_trim_1pct | 869 | +0.909% | +0.188% | 52.01% | <0.0001 |
| candidate_observation | drop_top_5pct | 842 | -0.260% | -0.118% | 49.41% | 0.8895 |
| candidate_observation | symmetric_trim_5pct | 797 | +0.584% | +0.188% | 52.20% | 0.0006 |
| candidate_observation | drop_top_10pct | 798 | -0.950% | -0.458% | 46.62% | 1.0000 |
| candidate_observation | symmetric_trim_10pct | 709 | +0.400% | +0.188% | 52.47% | 0.0031 |
| economic_event | drop_top_1pct | 742 | +0.029% | -0.049% | 49.73% | 0.4523 |
| economic_event | symmetric_trim_1pct | 734 | +0.271% | +0.013% | 50.27% | 0.1179 |
| economic_event | drop_top_5pct | 712 | -0.715% | -0.364% | 47.61% | 0.9997 |
| economic_event | symmetric_trim_5pct | 674 | +0.070% | +0.013% | 50.30% | 0.3396 |
| economic_event | drop_top_10pct | 675 | -1.276% | -0.551% | 44.74% | 1.0000 |
| economic_event | symmetric_trim_10pct | 600 | -0.022% | +0.013% | 50.33% | 0.5614 |

Contribution shares above 100% mean that the remaining observations collectively lost money and the upper tail more than accounted for the full positive total.

| level | variant | n | share_of_total_return |
| --- | --- | --- | --- |
| candidate_observation | top_1pct_share_of_total_return | 9 | 45.7% |
| candidate_observation | top_5pct_share_of_total_return | 45 | 120.4% |
| candidate_observation | top_10pct_share_of_total_return | 89 | 170.8% |
| economic_event | top_1pct_share_of_total_return | 8 | 95.5% |
| economic_event | top_5pct_share_of_total_return | 38 | 207.6% |
| economic_event | top_10pct_share_of_total_return | 75 | 282.1% |

## Net-return sensitivity to transaction-cost assumptions

| variant | n | mean | median | positive_frequency | p_t |
| --- | --- | --- | --- | --- | --- |
| modeled_cost_x_0 | 887 | +1.334% | +0.324% | 52.42% | <0.0001 |
| modeled_cost_x_0.5 | 887 | +1.271% | +0.256% | 52.31% | 0.0001 |
| modeled_cost_x_1 | 887 | +1.208% | +0.188% | 51.97% | 0.0002 |
| modeled_cost_x_2 | 887 | +1.081% | +0.062% | 50.73% | 0.0008 |
| modeled_cost_x_3 | 887 | +0.954% | -0.082% | 49.49% | 0.0026 |

## Event-family and calendar sensitivity

| variant | n | mean | median | positive_frequency | p_t |
| --- | --- | --- | --- | --- | --- |
| earnings | 692 | +0.020% | -0.125% | 49.42% | 0.4687 |
| geo | 172 | +4.794% | +2.443% | 61.63% | <0.0001 |
| other | 23 | +10.118% | +1.295% | 56.52% | 0.0969 |

Candidate-level monthly mean return is positive in 8 of 16 observed entry months. The three weakest and three strongest months are:

| variant | n | mean | median | positive_frequency |
| --- | --- | --- | --- | --- |
| 2026-06 | 11 | -8.880% | -8.356% | 18.18% |
| 2024-10 | 2 | -2.553% | -2.553% | 0.00% |
| 2025-06 | 54 | -2.241% | -1.589% | 46.30% |
| 2025-07 | 14 | +17.120% | +2.898% | 71.43% |
| 2026-03 | 116 | +7.867% | +6.593% | 69.83% |
| 2026-05 | 46 | +4.741% | +3.200% | 60.87% |

## Executable timing

Exact same-session ordering is verified for 0 of 887 rows. Current probability and equity artifacts are date-normalized, so the historical same-day close cannot be declared executable from the stored evidence.
Recomputing the observation interval from the next stored equity close gives a candidate-observation mean of +1.229% across N=847, median -0.153%, positive-return frequency 47.11%, p=<0.0001.
At true economic-event level under that timing convention: mean +0.550%, median -0.350%, positive-return frequency 45.58%, p=0.0550.

## End-date and event-uncertainty provenance

- Versioned scheduled-end snapshot available at signal: False.
- Actual public outcome timestamp available: False.
- Until those fields exist, `T_e-1` observations are provisional for confirmatory H1 because the project cannot prove the event remained unresolved at the measured interval endpoint.

## Research-stage separation

| level | research_stage | n | mean | median | positive_frequency | p_t | stage_is_genuinely_untouched |
| --- | --- | --- | --- | --- | --- | --- | --- |
| candidate_observation | model_selection | 292 | +0.352% | -0.215% | 48.97% | 0.3020 | False |
| candidate_observation | validation | 347 | +2.171% | +0.739% | 55.04% | <0.0001 | False |
| candidate_observation | discovery | 67 | -1.604% | -0.745% | 47.76% | 0.9923 | False |
| candidate_observation | retrospective_holdout | 181 | +1.782% | +0.384% | 52.49% | 0.0036 | False |
| economic_event | discovery | 21 | -0.355% | +0.305% | 52.38% | 0.6457 | False |
| economic_event | model_selection | 292 | +0.352% | -0.215% | 48.97% | 0.3020 | False |
| economic_event | validation | 273 | +0.022% | -0.362% | 49.08% | 0.4838 | False |
| economic_event | retrospective_holdout | 164 | +2.266% | +0.509% | 54.27% | 0.0002 | False |

No historical stage is labeled genuinely untouched. The next prospective, version-frozen sample is the confirmatory holdout.

## Most influential leave-one-out exclusions

| level | group_type | group_value | n_removed | leave_out_mean | change |
| --- | --- | --- | --- | --- | --- |
| candidate_observation | entry_month | 2026-03 | 116 | +0.206% | -1.002% |
| candidate_observation | symbol | USO | 104 | +0.313% | -0.895% |
| economic_event | entry_month | 2026-05 | 44 | +0.322% | -0.308% |
| economic_event | entry_month | 2025-07 | 12 | +0.323% | -0.307% |
| economic_event | event_family | other | 21 | +0.337% | -0.294% |
| economic_event | event_family | geo | 37 | +0.338% | -0.292% |
| candidate_observation | event_family | geo | 172 | +0.345% | -0.863% |
| economic_event | entry_month | 2026-03 | 43 | +0.435% | -0.196% |
| economic_event | entry_month | 2026-04 | 118 | +0.526% | -0.105% |
| economic_event | entry_month | 2025-12 | 16 | +0.604% | -0.027% |

## Secondary benchmark diagnostics — after-cost stock vs benchmark

These controls compare the after-cost dollar PnL of each stock trade against an equal-notional benchmark ETF trade over the same window. The sector control only uses trades with a real sector mapping; unknown-sector rows are excluded from the sector ETF comparison instead of being silently treated as SPY.

| level | benchmark | n | n_stock_better | n_benchmark_better | share_stock_better | mean_stock_net_pnl | mean_benchmark_net_pnl | mean_excess_net_pnl | p_t |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| candidate_observation | sector_etf | 680 | 339 | 341 | 49.85% | $-4.84 | $-24.52 | $19.68 | 0.2759 |
| economic_event | sector_etf | 678 | 338 | 340 | 49.85% | $-5.63 | $-24.49 | $18.86 | 0.2847 |
| candidate_observation | SPY | 887 | 442 | 445 | 49.83% | $120.62 | $27.69 | $92.93 | 0.0032 |
| economic_event | SPY | 750 | 358 | 392 | 47.73% | $63.76 | $19.19 | $44.58 | 0.1044 |

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
