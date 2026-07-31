# Code Methodology Report — Final ICAIF Run (2026-07-14)

Everything below was verified by reading the current working-tree code (not from memory or
prior documentation). File references use `path::function` plus line numbers in the current
working tree. Run directory: `C:\Users\Liran\PycharmProjects\cem_clean_repo\output\final_icaif_run_20260714_170817`.

---

## A. H1 eligibility

### A.1 Which script creates the H1 candidate-level trade file?

`diagnostics/run_raw_expectation_test_tminus1.py` (`main()` → `process_all_candidates()` →
`process_candidate()`, lines 126–318). It reads:

- candidates: `data/candidates_audit_clean.parquet` (default `CANDIDATES_PATH`, line 43;
  overridable via `--candidates-path`)
- prices: `data/prices.pkl`; probabilities: `data/probs.pkl`
- polarity: `core/polarity.py::resolve_polarity` (override > llm(`data/polarity_labels.json`) > regex)
- eligibility policies: `data/experiment_walkforward_folds_clean.csv`, filtered to
  `experiment == "T1+T2+T3+T4"` and `benchmark == "SPY"` (`load_fold_policies()`, lines 79–94,
  constants `FOLD_EXPERIMENT`/`FOLD_BENCHMARK`, lines 53–54).

It writes `raw_expectation_trades_candidate_level.csv` plus symbol-day-collapsed, event-level,
monthly, invalid-candidate and robustness CSVs, then calls
`analysis/h1_expectation_protocol.py::run_expectation_protocol` for the full inference suite.

### A.2 What exactly defines T_theta and the entry rule?

Two separate objects, which the manuscript previously conflated:

1. **`t_theta` (parquet column)** — the first time the market's probability path crosses the
   fixed 0.55 screening threshold. This is set upstream at candidate construction and is NOT
   a CEM-optimized quantity.
2. **Entry acceptance** — `core/kernel.py::entry_day` (lines 536–556), scanning probability
   points from `t_theta.normalize()` onward: enter at the first point with
   `p >= enter_strong`, or after `hold_days` consecutive points with `p >= enter_floor`.
   `enter_strong`, `enter_floor`, `hold_days` come from the fold policy (see A.3). Two more
   policy gates apply after acceptance: `feat_prob_surge_since_t0` (polarity-adjusted) must
   not exceed `max_prob_surge`, and `feat_runup_since_t0` must not exceed `max_price_runup`
   (`process_candidate`, lines 183–201).

### A.3 Are H1 eligibility parameters fixed independently, or CEM-derived?

**They are CEM-derived.** The five entry-relevant parameters (`enter_strong`, `enter_floor`,
`hold_days`, `max_prob_surge`, `max_price_runup`) are read per-candidate from the
walk-forward fold schedule of the **T1+T2+T3+T4 / SPY** arm of the CEM experiment matrix
(`eval_policy_json` column of `experiment_walkforward_folds_clean.csv`). `policy_for_day()`
(lines 97–109) selects the fold whose evaluation window contains the candidate's `t_theta`;
candidates before the first fold window use the fold-1 policy; candidates after the last
window use the last fold's policy.

### A.4 On which dates/observations were those parameters fitted? Frozen before 2026?

On the audit-clean universe (this run's matrix, `runs/final_icaif_matrix_seed42_6x20`), the
schedule has 5 folds (`create_expanding_wf_folds`, `backtesting/optimize_cem.py` lines
777–847). Fit eligibility is strict label completion: a candidate may enter a fold's CEM fit
only if `t_e < fold.eval_start` (`rows_completed_before`, lines 742–752, enforced fail-closed
by `assert_rows_completed_before`, lines 755–774):

| Fold | Fit rows (t_e <) | Eval window (by t_theta) |
|---|---|---|
| 1 | 14 (< 2025-05-01) | 2025-05-01 — 2025-07-31 |
| 2 | 81 (< 2025-08-01) | 2025-08-01 — 2025-10-31 |
| 3 | 247 (< 2025-11-01) | 2025-11-01 — 2026-01-31 |
| 4 | 477 (< 2026-02-01) | 2026-02-01 — 2026-04-30 |
| 5 | 981 (< 2026-05-01) | 2026-05-01 — 2026-06-13 |

**Consequences (must be stated in the paper):**

- For every 2026 observation, the eligibility parameters were fitted only on candidates whose
  outcomes had completed **before that observation's fold began** — point-in-time legal, no
  label lookahead.
- But the parameters are **NOT frozen before 2026**. Fold 4 fits on outcomes completed
  through 2026-01-31 and fold 5 through 2026-04-30 — i.e., 2026 observations completed in
  earlier test blocks update the policies used later in the test period (online refit).
  Only fold 3's policy (fit on t_e < 2025-11-01) is fitted purely on pre-2026 information,
  and it governs the January-2026 observations.
- Candidates **before** 2025-05-01 use the fold-1 policy, which was fitted on outcomes that
  completed after many of those candidates' signal times. For pre-fold-1 (2024-08 — 2025-04)
  observations the eligibility rule is therefore retrospective. This affects only the
  descriptive full-sample estimate, not the 2026 observations.

**Verdict on confirmatory status:** the full-sample H1 estimate is a *descriptive*
conditional mean under a selection rule that was tuned (by CEM, on the SPY portfolio
objective) using data overlapping the sample itself. It is not an independent hypothesis
test. The 2026 subsample is point-in-time legal under the expanding refit schedule but the
2026 period has been repeatedly inspected during research; the paper must not call it a
pristine holdout. Both statements are carried into `final_statistical_report.md` and the
manuscript.

### A.5 Entry close convention

Primary specification is **same-session close**: the entry bar is the first stored daily bar
with `bar.date >= entry_day date` (`process_candidate` lines 210–222; identical in the kernel,
`_scan` entry `gi = bisect_left(bar_value, entry_ts_value)`). If the signal day is a
non-trading day the entry rolls forward to the next stored close. The
**next-stored-trading-close** variant is computed as a sensitivity
(`h1_expectation_protocol.py::build_timing_audit`, lines 368–440) because same-session
signal-to-close ordering is unverifiable from date-normalized artifacts.

### A.6 Exit convention, sizing, costs

- Exit: last stored close strictly **before** `t_e` (T−1; lines 224–235). Candidates whose
  entry lands on/after that bar are excluded (`entry_not_before_T_minus_1`).
- Fixed $10,000 target notional, whole shares (`shares = int(10000 // entry_price)`).
- Costs: `ib_cost` = max($0.35, min($0.0035/share, 1% of value)) + SEC fee 0.0000278 on
  sells + 5 bp slippage per leg; two legs (asset buy + asset sell). No benchmark rotation
  legs in H1.

### A.7 Does H1 use T3 Kelly sizing or T4 capacity allocation?

**No.** Each candidate is an independent fixed-notional trade; there is no portfolio capital,
no concurrency cap, no Kelly history, no preemption (`process_candidate` has none of these
code paths). The **only** T4-derived element is the *symbol-day collapse* diagnostic
(`collapse_symbol_day`, lines 333–349), which reuses the T4 ranking key
(`EVENT_PRIORITY_ORDER`, entry probability, clipped run-up) to pick one representative trade
per (symbol, entry-date) as a secondary observation level. It does not alter the
candidate-level file.

### A.8 Inference conventions in the protocol

`analysis/h1_expectation_protocol.py`:

- `_cluster_bootstrap` (lines 258–302): cluster/block bootstrap resampling whole clusters
  with replacement. Reports (a) the **ordinary uncentered percentile CI** of the mean
  (2.5/97.5 quantiles of raw resampled means) and (b) a **null-centered one-sided bootstrap
  p-value**, `p = (#{null mean >= observed} + 1)/(B + 1)`, where null draws are recentered to
  mean zero. These are the two conventions the paper must distinguish.
- Candidate-level dependence units: economic event, symbol, entry-week block, entry-month
  block. Event-level (equal weight per `economic_event_id`): iid reference + entry-week and
  entry-month blocks.
- One-sided t-tests and binomial tests are reference-only.
- `benchmark_excess_h1.py::_bootstrap_grouped` additionally reports `p_legacy_raw_tail`
  (share of raw resampled means ≥ observed — a near-0.5-by-construction legacy diagnostic,
  NOT a test) alongside `p_null_centered`. Only the null-centered p is a hypothesis test.
- Equal-weight event construction (`build_true_event_frame`, lines 166–224): candidate rows →
  unique (event, symbol, entry, exit) opportunities (mean within duplicates) → equal-weight
  mean per `economic_event_id` (fallback `market-fallback:<market_id>` when event_id empty).

---

## B. CEM chronology

Verified in `backtesting/optimize_cem.py::main` (lines 2319–2688) and `cem_search`
(lines 1792–1937).

- **Universe**: `data/candidates_audit_clean.parquet` (default), filtered to
  `cem_eligible == True` (all 1,182 rows) and `feat_connection_strength > 0.5`
  → **1,148 candidates** enter the experiment (34 dropped by the relevance gate).
- **Train/optimization period**: the initial (non-WF) fit uses
  `rows_completed_before(df, 2026-01-01)` = **423 candidates with t_e < 2026-01-01**
  (label-complete, outcome finished before the OOS boundary). `oos_start` is hard-coded
  `2026-01-01` (line 2396).
- **Test period**: candidates with `t_theta in [2026-01-01, oos_end]` where
  `oos_end = (max t_theta) + 1 day − 1 day` = **2026-06-13** on this artifact → **721 test
  candidates**; the portfolio simulation runs 2026-01-02 — 2026-06-12 (first/last benchmark
  trading closes). **January–June 2026 is one single test period.**
- **Validation dataset: none is used.** `split_train_val_test` exists (lines 1976–2022) but
  `main()` never calls it; the `val` split label is folded into `test`
  (lines 2371–2374, comment: "val is not used for any CEM/model selection"). No
  configuration, seed, or budget selection reads a validation window in this pipeline.
- **T2 expanding walk-forward**: `create_expanding_wf_folds` builds 3-month eval blocks
  stepping 3 months over the **entire candidate range** (including 2026). Fold i fits by CEM
  on all candidates with `t_e < fold_i.eval_start` (strict; completion ON the fold start is
  not "before") and requires ≥8 fit and ≥8 eval candidates. **Yes — test-period policies are
  updated using outcomes completed in earlier test blocks** (folds 4–5 above). This is
  labelled `policy_scope = online_refit_schedule` in the results CSV. Non-WF arms
  (Baseline, T4 GeoPriority) fit once on the 423 label-complete train rows and are
  `frozen_policy` for the whole test period.
- **Path truncation**: every fit simulation is truncated at `fit_eval_end`
  (fold start − 1 day) and every fold evaluation at its fold's `eval_end` via
  `truncate_paths` (lines 336–357), which clips both price and probability paths before
  trade generation; `sim_opp_cost` raises if any generated trade exits after its horizon
  (lines 972–978). The final test simulation is truncated at `oos_end`.
- **Which data determine the policy used on each date**: for WF arms,
  `DynamicPolicySchedule.__call__` (lines 111–124) returns the policy of the fold whose eval
  window contains the date (dates past the last window keep the last fold's policy; dates
  before the first window use fold 1's policy). For non-WF arms, the single frozen policy.

## C. Portfolio mechanics

All in `backtesting/optimize_cem.py` unless noted.

- **T1 (realized-friction fitness)**: `cem_reward` (lines 1658–1676) subtracts
  `2.0 × share of fitted trades with gross_pnl < 3 × txn_cost` from the CEM score
  (`HURDLE_MULT=3.0`, `HURDLE_PENALTY=2.0`). Fitness-only; no live entry gate.
- **T2**: expanding label-complete walk-forward (section B).
- **T3 (half-Kelly)**: `kelly_size` (lines 644–663) — needs ≥10 completed net trades
  (`KELLY_MIN_N`), uses last 30 (`KELLY_LOOKBACK_N`), half-Kelly clipped to [0.05, 0.20].
  Test simulation seeds history with train-period trades whose `candidate_t_e < 2026-01-01`
  (`completed_trade_history_before`, lines 2092–2117); trades force-liquidated at a horizon
  (`evaluation_end_liquidation`) never enter Kelly history.
- **T4 (event-priority allocation)**: same-day candidates ranked by
  `(event_priority, −entry_prob, −clip(runup, ±0.20), candidate_order)` with
  `EVENT_PRIORITY_ORDER = {geo:0, macro:1, earnings:2, other:3}` (`_allocation_rank_tuple`);
  same-day duplicate symbols collapsed to one position with supporting-signal metadata
  (`_prepare_event_priority_batch`); when the roster is full, a geo/macro candidate may
  preempt the worst open **earnings** position only if ALL open positions are earnings and
  the worst is below +3.0% net (`maybe_preempt_earnings_slot`,
  `PREEMPT_NET_PROFIT_HURDLE_PCT = 3.0`). Event family is regex-classified from question +
  archetype text only (`event_family_from_text`, lines 404–413).
- **Ten optimized parameters and bounds** (`PORTFOLIO_BOUNDS`, lines 181–192):
  `atr_mult` [1.5, 4.0]; `lock_activate` [0.02, 0.10]; `theta_out` [0.45, 0.60];
  `enter_strong` [0.60, 0.85]; `enter_floor` [0.55, 0.80] (enter_strong is raised to
  enter_floor if sampled below it); `hold_days` [1, 5] (rounded int);
  `max_prob_surge` [0.20, 0.80]; `max_price_runup` [0.02, 0.20];
  `position_size_pct` [0.06, 0.12]; `max_concurrent` [8, 12] (rounded int).
- **CEM search**: Gaussian CEM, default 6 iterations × population 20, elite fraction 25%
  (elite count 5), initial mean = `PORT_DEFAULT` (`core/policy.py::DEFAULT_POLICY` +
  position_size 0.10, max_concurrent 10), initial std = bound-range/4, std floor 1e-4.
  Seed = `--seed` + benchmark offset (SPY +0, QQQ +10,000) so ablations share the identical
  initial population per benchmark.
- **CEM objective**: `J = Sharpe_daily − 0.30 × |MaxDD_pct|` (− T1 penalty when active).
  **MaxDD enters in percentage points** (e.g. 7.4, not 0.074): `_calc_max_dd` returns
  `min(equity/cummax − 1) × 100`, and `cem_reward` takes `abs()` of that. Invalid score
  (−1e9) when < 3 trades or < 20 daily returns.
- **Daily-equity Sharpe annualization**: `mean(daily pct_change)/std × sqrt(252)`
  (`daily_equity_sharpe` uses ddof=1; `_calc_advanced_metrics` uses pandas default ddof=1).
- **Transaction-cost formula** (`ib_cost`, lines 290–297):
  `max(0.35, min(0.0035×shares, 1%×value)) + (sell ? 0.0000278×value : 0) + 0.0005×value`.
- **Benchmark rotation costs**: opening an event position funds from idle cash first, then
  sells benchmark shares (benchmark sell cost); closing sells the asset (sell cost) and
  re-buys the benchmark (buy cost). Per-trade net `pnl` is gross P&L minus **all four legs**:
  benchmark sell + asset buy + asset sell + benchmark rebuy (`close_position`,
  lines 1022–1060).
- **Whole-share event positions; fractional benchmark**: event positions use
  `_affordable_buy_qty` (whole shares net of buy cost); benchmark legs use fractional shares
  (`FRACTIONAL_BENCHMARK = True`, `_bench_buy_qty` fixed-point iteration).
- **Fully-invested sweep**: idle cash ≥ $100 (`MIN_SWEEP_CASH`) is swept daily into
  fractional benchmark shares (`sweep_idle_cash`).
- **Sizing and concurrency**: target allocation = `current_equity × position_size_pct`
  (Kelly-adjusted when T3); concurrency capped at `max_concurrent`; duplicate open symbols
  skipped; insufficient-capital skips logged. FIFO arms admit same-day candidates in
  parquet/`t_theta` order (stable sort — row order is the tiebreak); T4 arms rank as above.
- **Passive benchmark**: same initial execution model (initial buy cost included), tracked as
  `initial_bench_shares × close + initial_cash`.
- **Portfolio metrics** (`stats`, lines 1595–1633): `total_return = (final/initial − 1)×100`;
  `benchmark_return` analogous on the passive leg; `excess_return =
  (final_equity − final_passive_equity)/initial × 100` (identical to the return difference);
  `max_dd` from close-to-close equity with the final row restated to the post-liquidation
  value; Sharpe/Sortino from daily equity.
- **End-of-horizon liquidation**: any open positions are closed at the last day's close with
  reason `evaluation_end_liquidation`.

---

## D. Material mismatches: current manuscript vs current code/artifacts

1. **Robustness table and figure are from the wrong universe.** The manuscript's Table 4 /
   Figure "budget robustness" numbers come from `runs/robustness_grid_*` which were run on
   the **legacy** `data/candidates.parquet` universe (verified: those runs match
   `runs/paper_legacy_key_arms` byte-for-byte and do NOT match the audit-clean matrix that
   Table 3 uses). The final paper must use the new audit-clean grid
   (`runs/icaif_grid_*`, this run).
2. **Jan–Mar "validation" / Apr–Jun "final" split** appears in the abstract, §6.3, §5.3,
   Table 4 and the budget figure, and in `analysis/build_robustness_reports.py`
   (`VAL_END = 2026-03-31`, `FINAL_START = 2026-04-01`, budget "selection"). No such split
   exists in the pipeline (no validation set is consulted anywhere; section B). Removed
   throughout; January–June 2026 is reported as one test period and no budget is selected.
3. **"Frozen eligibility policy" (§3.1)** — wrong for two reasons: the H1 eligibility
   parameters are CEM-derived (A.3) and follow the online-refit walk-forward schedule
   (A.4). Reworded to state provenance exactly.
4. **T_theta conflation** — parquet `t_theta` (0.55 screening cross) vs policy acceptance
   time (entry_day). The paper's formula anchors entry at the acceptance close, which is
   correct in code; wording clarified.
5. **Manuscript's H1/CEM matrix numbers** — the seed-42 6×20 audit-clean matrix reproduced
   exactly in this rerun (Table 3 values confirmed). H1 central values confirmed against the
   fresh 20,000-replication rerun (small p-value differences from the 10k→20k change are
   expected and updated).
6. **`data/experiment_*` in-place artifacts currently hold a legacy-universe run**
   (regenerated 2026-07-14 15:50 as the "original universe" control). They do not match the
   audit-clean paper numbers; all paper numbers now cite isolated `runs/…` and
   `output/final_icaif_run_20260714_170817/…` artifacts instead.
7. **Cleaning table** (Table 1) matches `data/candidate_cleaning_summary.json` exactly
   (1,293 → 36 dup rows removed → 1,257; 109 audited; 54 quarantined; 11 secondary;
   10 excluded; 1,182 primary). No change needed.
8. **CEM fitness formula in §5.1** matches code (Sharpe − 0.30·|MDD%|; T1 −2·f_fail).
   Bounds listed in §5.1 match `PORTFOLIO_BOUNDS`. No change needed.

No code was changed to match manuscript wording. The only new code written for this run is
(a) a read-only reporting script for the robustness grid (`robustness/build_icaif_robustness_report.py`)
replacing the legacy split logic, and (b) thin driver scripts that call existing entry points
with explicit paths/seeds; both live in the run directory.
