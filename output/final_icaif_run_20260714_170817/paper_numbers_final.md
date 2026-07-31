# paper_numbers_final.md — every manuscript number and its source

Run root: `C:\Users\Liran\PycharmProjects\cem_clean_repo\output\final_icaif_run_20260714_170817`
(all relative paths below are under this root unless absolute). Bootstraps: 20,000
replications, seed 42 (+ documented offsets). All H1 returns are fractions in the CSVs and
percentages in the paper (×100).

## 1. Cleaning reconciliation (paper Table 1)

Source: `C:\Users\Liran\PycharmProjects\cem_clean_repo\data\candidate_cleaning_summary.json`

| Number | JSON key |
|---|---|
| 1,293 raw rows | `input_rows` |
| 36 duplicate excess removed | `exact_duplicate_excess_rows_removed` |
| 1,257 after collapse | `annotated_rows_after_exact_deduplication` |
| 109 audited pairs | `audited_rows_after_deduplication` |
| 54 quarantined | `treatment_action_counts.quarantine` |
| 11 secondary | `treatment_action_counts.secondary` |
| 10 excluded (9 "Exclude from primary" + 1 "Incorrect endpoint") | `audited_verdict_counts` |
| 1,182 primary output | `primary_output_rows` |
| 1,148 unreviewed passthrough | `unreviewed_passthrough_rows` |

Polarity split (1,071 / 99 / 12) — `data/polarity_labels.json` via
`core/polarity.resolve_polarity` over the 1,182 primary rows; H1 selected split (844 / 43):
`h1/raw_expectation_tminus1_final/raw_expectation_trades_candidate_level.csv`, column
`polarity` value counts.

## 2. H1 central result (paper §5.1, Table 2, Fig 2)

Counts — `h1/raw_expectation_tminus1_final/summary_report.md` "Filtering Stages":
1,182 loaded; 934 passed entry rules; 887 valid; 47 `entry_not_before_T_minus_1`;
121 `below_entry_threshold`; 108 `prob_surge_exceeded`; 12 `no_clean_signal_side`;
7 `price_runup_exceeded`; 0 missing prices / missing t_e / no prob data.

Sample structure — `h1/raw_expectation_tminus1_final/h1_raw_expectation_cluster_inference.csv`
(`n`, `n_clusters` per row) and the manifest: 750 economic events, 387 symbols, 46 entry
weeks, 16 entry months; 819 unique market_ids
(`h1_expectation_manifest.json` → `row_counts`).

Central and dependence rows — `h1_raw_expectation_cluster_inference.csv`, columns
`mean_raw_net_return`, `median_raw_net_return`, `win_rate`, `standard_deviation`,
`cluster_ci_lo/hi`, `cluster_bootstrap_p`, `p_t_one_sided`:

| Paper number | Row filter |
|---|---|
| mean +1.208%, median +0.188%, positive 51.97%, std 10.156% | level=candidate_observation, cluster_type=iid_reference |
| CI [+0.263,+2.200]%, p=0.0087 | candidate_observation / economic_event_id |
| CI [−0.184,+2.776]%, p=0.0652 | candidate_observation / symbol |
| CI [−0.185,+2.834]%, p=0.0678 | candidate_observation / entry_week_block |
| CI [−0.630,+3.651]%, p=0.1427 | candidate_observation / entry_month_block |
| event mean +0.631%, median +0.013%, positive 50.27% | economic_event / iid_reference |
| event CI [−0.417,+2.407]%, p=0.1935 | economic_event / entry_month_block |
| event week CI [−0.465,+2.004]%, p=0.1657 | economic_event / entry_week_block |

Symbol-day row (786, +0.568%, −0.057%, 49.62%, p=0.0611) —
`h1/raw_expectation_tminus1_final/raw_expectation_robustness.csv`, row
`version=symbol_day_collapsed`, columns `n_trades`, `mean_net_return`,
`median_net_return`, `win_rate_net_return_gt_0`, `event_cluster_bootstrap_p_value`.

2026 subsample (N=528, mean +2.038%, median +0.557%, positive 54.17%; event-cluster
p=0.0006, CI [+0.658,+3.304]%; month-block p=0.1083, CI [−0.733,+5.425]%) —
`stats/final_statistical_results.csv`, rows `estimand="H1 mean net return (2026 subsample,
entries >= 2026-01-01)"`, columns `estimate/median/positive_frequency/ci_lo/ci_hi/p_value`.

## 3. H1 sensitivities (paper §5.2)

Source: `h1/raw_expectation_tminus1_final/h1_sensitivity.csv`, columns
`mean_raw_net_return`, `p_t_one_sided`, filtered by `level`/`family`/`variant`:

| Paper number | Row |
|---|---|
| drop top 1% candidates → +0.662% (p=0.0047) | candidate_observation / tail / drop_top_1pct |
| drop top 5% candidates → −0.260% | candidate_observation / tail / drop_top_5pct |
| symmetric 5% trim candidates → +0.584% | candidate_observation / tail / symmetric_trim_5pct |
| event drop top 1% → +0.029%, p=0.4523 | economic_event / tail / drop_top_1pct |
| event drop top 5% → −0.715% | economic_event / tail / drop_top_5pct |
| event symmetric 5% → +0.070% | economic_event / tail / symmetric_trim_5pct |
| 3× costs → +0.954%, p=0.0026, median −0.082% | candidate_observation / cost / modeled_cost_x_3 |
| next-close candidate mean +1.229% | candidate_observation / entry_timing / next_stored_trading_close |
| next-close event mean +0.550%, p=0.0550 | economic_event / entry_timing / next_stored_trading_close |
| top-5% share of total return ≈120% | candidate_observation / tail_concentration / top_5pct_share_of_total_return (1.2043) |

## 4. Event-family heterogeneity (paper §5.3, Fig 2B)

Means/medians/N — `h1_sensitivity.csv` family=event_family rows (earnings +0.020%/N=692;
geo +4.794%/median +2.443%/win 61.63%/N=172; other +10.118%/N=23).
Event-clustered CIs — `stats/final_statistical_results.csv`, rows
`estimand="H1 mean net return (event family: …)"`:
earnings CI [−0.476,+0.521]% (p=0.4645); geo CI [+0.936,+8.485]% (p=0.0053, 37 clusters);
other CI [+0.248,+27.850]% (p=0.0983, 21 clusters).

Polarity ablation (882 obs, mean +1.272%, median +0.229%) —
`h1/raw_expectation_tminus1_final_raw_yes/raw_expectation_trades_candidate_level.csv`,
column `net_return` (mean/median). NO-bullish subgroup (43, −2.100%, −2.864%) — primary
trades CSV filtered `polarity == -1`.

## 5. Benchmark-relative inference (paper §5.4, Table 3, Fig 3)

Source: `benchmark_excess/benchmark_excess_inference.csv`, columns
`observed_mean_excess_return`, `ci_lo`, `ci_hi`, `p_boot_null_centered`, `n`:

| Paper number | Row (benchmark / level / scheme) |
|---|---|
| SPY candidate +0.914%, CI [−0.116,+2.013]%, p=0.0508, N=887 | SPY / candidate_observation / economic_event_cluster |
| SPY candidate month block CI [−0.899,+3.404]%, p=0.2036 | SPY / candidate_observation / entry_month_block |
| SPY symbol-day +0.223%, CI [−0.453,+0.975]%, p=0.2641 | SPY / symbol_day_collapsed / economic_event_cluster |
| SPY equal-event +0.430%, CI [−0.218,+1.174]%, p=0.1138, N=750 | SPY / economic_event / ordinary_event_bootstrap |
| Sector candidate +0.191%, CI [−0.393,+0.904]%, p=0.2644, N=680 | sector_etf / candidate_observation / economic_event_cluster |
| Sector equal-event +0.183%, p=0.2727, N=678 | sector_etf / economic_event / ordinary_event_bootstrap |
| Sector exclusions: 184 rows have no sector mapping; of the 703 sector-eligible rows, 680 match ETF prices at both dates | `benchmark_excess/benchmark_excess_summary.md` "Exclusions" |

"No 95% interval excludes zero" — verified over all 20 rows of the inference CSV
(`ci_lo > 0` or `ci_hi < 0` matches zero rows).

## 6. CEM configuration matrix (paper Table 4)

Source: `cem_matrix/cem_matrix_final.csv` (mirrors
`C:\Users\Liran\PycharmProjects\cem_clean_repo\runs\final_icaif_matrix_seed42_6x20\experiment_results_clean.csv`),
columns `test_return_pct`, `test_excess_return_pct`, `test_max_dd_pct`, `test_sharpe`,
`test_trades` per `experiment` × `benchmark`:

| Config | SPY Return/Excess/MaxDD/Sharpe/Trades | QQQ same |
|---|---|---|
| Baseline | 30.27 / 21.75 / −6.67 / 3.30 / 225 | 27.36 / 9.77 / −6.36 / 2.93 / 223 |
| T1+T2 | 30.87 / 22.35 / −9.52 / 3.41 / 191 | 24.63 / 7.03 / −7.51 / 2.52 / 189 |
| T1+T2+T3 | 25.50 / 16.98 / −7.76 / 2.99 / 197 | 22.57 / 4.98 / −11.43 / 2.10 / 181 |
| T4 GeoPriority | 27.27 / 18.75 / −8.33 / 2.89 / 197 | 23.58 / 5.99 / −6.74 / 2.54 / 211 |
| T1+T2+T3+T4 | 28.49 / 19.97 / −8.72 / 3.22 / 184 | 24.61 / 7.02 / −10.38 / 2.20 / 185 |

Benchmark buy-and-hold +8.52 (SPY) / +17.59 (QQQ): `test_benchmark_return_pct`.
Equity-log recomputation check (all 10 rows match): `cem_matrix/cem_matrix_verification.md`.
Universe/chronology counts (1,148 relevance-filtered; 423 label-complete train; 721 test;
OOS 2026-01-01→2026-06-13; portfolio dates 2026-01-02→2026-06-12): `cem_matrix/matrix_run.log`.

Seed-42 flagship block inference (SPY block-bootstrap p=0.0317, HAC p=0.0239; QQQ
p=0.3102): `cem_matrix/cem_oos_block_inference_final.csv`, rows universe=audit_clean,
columns `boot_p_one_sided_null_centered`, `hac_p_one_sided`, `n_daily_returns`=111.

Cleaning delta (SPY 25.53→28.49, +2.96, p=0.3371; QQQ 22.29→24.61, +2.32, p=0.3884):
`cem_matrix/cem_cleaning_delta_final.csv`, columns
`clean_minus_original_terminal_return_pct`, `boot_p_one_sided_null_centered`; original
universe legs from `C:\Users\Liran\PycharmProjects\cem_clean_repo\runs\paper_legacy_key_arms`.

## 7. CEM seed/budget robustness (paper Table 5, Fig 5)

Source: `robustness/icaif_robustness_budget_summary.csv`, columns
`median_test_excess_pct`, `q25/q75_test_excess_pct`, `min/max_test_excess_pct`,
`positive_excess_seed_count`, `median_test_return_pct`, `median_test_sharpe`,
`median_test_max_dd_pct`, `median_test_trades`, `median_test_trade_txn_cost`:

| Budget | SPY median [Q25,Q75] / pos | QQQ median [Q25,Q75] / pos |
|---|---|---|
| 6×20 | +8.26 [+4.31,+9.63] / 10 of 10 | +3.89 [+2.21,+7.25] / 8 of 10 |
| 6×30 | +6.69 [+6.15,+8.37] / 10 of 10 | +5.65 [+3.11,+7.69] / 9 of 10 |
| 10×20 | +7.76 [+7.35,+10.52] / 10 of 10 | +4.65 [+1.33,+5.96] / 8 of 10 |
| 10×30 | +6.97 [+5.77,+9.06] / 10 of 10 | +6.44 [+4.15,+7.23] / 9 of 10 |

Run-level rows (with equity-log recomputation match flags; 0 mismatches):
`robustness/icaif_robustness_run_level.csv`. Underlying run dirs:
`C:\Users\Liran\PycharmProjects\cem_clean_repo\runs\icaif_grid_{6x20,6x30,10x20,10x30}_seed{42..51}`.

## 8. Baseline versus All Treatments, paired at 6×20 (paper Table 6)

Source: `robustness/icaif_paired_summary.csv` (per-seed detail:
`robustness/icaif_paired_baseline_vs_all_6x20.csv`), columns
`median_delta_excess_pct`, `q25/q75_delta_excess_pct`, `median_delta_sharpe`,
`median_delta_max_dd_pct`, `median_delta_trade_txn_cost`, `pct_seeds_all_beats_baseline`:

- SPY: ΔExcess −7.43 [−12.39,−3.92], ΔSharpe −0.92, ΔMaxDD −0.16, ΔCost −$2,032, wins 0%.
- QQQ: ΔExcess −3.44 [−7.87,+1.06], ΔSharpe −0.55, ΔMaxDD −1.00, ΔCost −$1,620, wins 30%.
- Both: median ΔExcess −6.15, wins 15% (3/20 pairs).

Baseline run dirs: `C:\Users\Liran\PycharmProjects\cem_clean_repo\runs\icaif_base_6x20_seed{42..51}`.

## 9. Consolidated machine-readable index

`stats/final_statistical_results.csv` — 82 rows; every row carries estimand, sample,
observation_level, dependence_unit, N, estimate, CI, p-value, p-convention and
source_file. `stats/final_statistical_report.md` is the narrative version.
