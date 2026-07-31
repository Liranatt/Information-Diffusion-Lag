# Execution report — final ICAIF run, 2026-07-14

Run root: `C:\Users\Liran\PycharmProjects\cem_clean_repo\output\final_icaif_run_20260714_170817`
Repo: `C:\Users\Liran\PycharmProjects\cem_clean_repo`, branch main, HEAD `80c4408`
(working tree carries uncommitted CLI additions to `backtesting/optimize_cem.py` and the
untracked `data/candidates_audit_clean.parquet` / `core/candidate_cleaning.py` — the same
state every artifact in this run was produced with).

Fixed inputs used, never regenerated: `data/candidates_audit_clean.parquet`,
`data/prices.pkl`, `data/probs.pkl`, `data/polarity_labels.json`,
`data/candidate_cleaning_summary.json`. No ingestion, DB, Gemini, mapping, polarity
labeling, or audit step was run.

## What was run (chronological)

1. **Code verification** (read-only) → `code_methodology_report.md`.
2. **CEM configuration matrix** — `backtesting.optimize_cem`, seed 42, 6×20, five active
   configurations × SPY/QQQ, full logs → `runs/final_icaif_matrix_seed42_6x20`
   (11.8 min). Verification: recomputed return/Sharpe/MaxDD from equity logs match the
   result CSV on all 10 rows (`cem_matrix/cem_matrix_verification.md`). The matrix
   reproduces the previous manuscript's Table values exactly.
3. **Robustness grid** — 50 isolated runs (T1+T2+T3+T4 × {6×20, 6×30, 10×20, 10×30} ×
   seeds 42–51, plus Baseline × 6×20 × seeds 42–51), driver
   `robustness/run_icaif_grid.py`, 6 parallel workers, 0 failures. Determinism check: an
   independently launched duplicate of the 6×30/seed-47 cell
   (`runs/verify_icaif_6x30_seed47`) is byte-identical to the grid's cell.
4. **H1** — driver `h1/run_h1_final.py`: canonical
   `diagnostics/run_raw_expectation_test_tminus1.py` + full
   `analysis/h1_expectation_protocol.py` at 20,000 bootstraps, seed 42, on the audit-clean
   artifacts; plus the raw-YES polarity ablation. The 887 candidate trades are
   byte-identical to the pre-existing 2026-07-14 morning run.
5. **Benchmark-relative** — `analysis.benchmark_excess_h1`, 20,000 bootstraps, seed 42,
   on the fresh trade file (PDF forest-plot variant added).
6. **Statistics** — `cem_matrix/build_matrix_report.py` (matrix verification + HAC/5-day
   moving-block bootstrap + cleaning delta vs `runs/paper_legacy_key_arms`),
   `robustness/build_icaif_robustness_report.py` (full-test aggregation + paired
   comparison), `stats/build_final_stats.py` (82-row consolidated results + report).
7. **Figures/tables** — `figures/build_final_figures.py`, `tables/build_final_tables.py`
   (all values read from final CSVs).
8. **Paper** — `paper/paper_final.tex` compiled with MiKTeX pdflatex ×2 → 8 pages,
   no errors.

## Key findings surfaced by this run

- **The pre-existing 40-run robustness grid (`runs/robustness_grid_*`) was produced on the
  legacy `data/candidates.parquet` universe**, not the audit-clean one (its cells match
  `runs/paper_legacy_key_arms` exactly). The previous manuscript therefore mixed an
  audit-clean configuration matrix with a legacy-universe robustness table. The grid was
  rerun in full on the audit-clean universe (`runs/icaif_grid_*`).
- **The in-place `data/experiment_*` artifacts held a legacy-universe run** (regenerated
  2026-07-14 15:50 as the "original universe" control). The in-place walk-forward fold CSV
  was therefore the legacy schedule; before H1, this run installed the fresh audit-clean
  fold CSV (byte-identical to the schedule used by the morning H1 run, sha256
  `0387179e…`) after backing up the legacy occupant to
  `h1/backup_legacy_experiment_walkforward_folds_clean.csv`.
- **Paired result**: at equal seed/budget (6×20), T1+T2+T3+T4 underperforms Baseline
  (median ΔExcess −7.43 SPY / −3.44 QQQ; wins 0%/30%). Reported honestly in the paper.
- **Benchmark-relative H1**: no 95% interval excludes zero (SPY event-cluster p=0.0508).
  Added to the paper as a primary qualification.
- The earlier (2026-07-14 morning) H1 protocol run had used 10,000 bootstraps and a
  mismatched manifest candidates path (`candidates.parquet` for hashing while trades used
  the audit-clean file); this run uses 20,000 and audit-clean paths throughout.

## Deviations / notes

- `analysis/build_robustness_reports.py` (legacy Jan–Mar validation / Apr–Jun final split
  and budget ranking) was NOT used; the replacement reporting layer lives in
  `robustness/build_icaif_robustness_report.py`. No repo code was modified.
- The equity-log verification initially flagged a constant ~0.065-pt return gap: the
  official `total_return` divides by the $100k initial capital (pre-initial-cost) while
  equity logs start post-cost. Recomputation against the true initial matches exactly;
  Sharpe and MaxDD matched to 1e-6 throughout.
- `data/experiment_walkforward_folds_clean.csv` now contains the audit-clean schedule
  (canonical paper state); the displaced legacy version is preserved byte-for-byte in this
  run directory and in `runs/paper_legacy_key_arms/experiment_walkforward_folds_clean.csv`.

## Failed or unresolved

None. All 50 grid cells, the matrix, both H1 runs, the benchmark analysis, and the paper
compile completed successfully; every recomputation check passed.
