# Manuscript change log — paper_final.tex vs main_short_technical_revised.tex

Base: `output/pdf/paper_revision/main_short_technical_revised.tex` (7 pages).
Result: `paper_final.tex` (8 pages, sigconf anonymous, compiles clean under MiKTeX pdflatex).

## Removed (false validation/final split)

- Abstract sentence "the validation-selected budget remains positive in the final
  April–June 2026 window" — replaced with full-test robustness + the paired-baseline finding.
- §5.3 (old): "January–March is the configuration-validation window. April–June … final
  window. Selection uses median excess…" — replaced with a statement that **no validation
  split exists anywhere in the pipeline** and January–June 2026 is one test period.
- §6.3 (old): budget-selection narrative ("The 10×30 budget is selected because …"),
  validation/final medians, "85% of its seed-benchmark cells are positive" — replaced with
  full-test medians/IQRs for all four budgets, no selection, no ranking.
- Old Table 4 (validation/final split, legacy-universe grid) — replaced by new Table 5
  (full Jan–Jun test, audit-clean-universe grid, median [IQR], positive-seed counts, MaxDD).
- Old Figure "budget robustness" (val/final heatmap, gold-outlined selected budget) —
  replaced by a full-test dot-whisker figure with no highlighted budget.
- Discussion/conclusion references to the validation-selected 10×30 budget.

## H1 methods and eligibility provenance

- §3.1: "the frozen eligibility policy accepts the candidate" — corrected. New text
  separates the fixed 0.55 screening cross (T_theta) from policy acceptance, and a new
  §4.2 "Eligibility provenance" states that the entry parameters are the CEM-fitted
  walk-forward schedule of the all-treatment SPY arm (online refits; retrospective reuse of
  fold-1 policy before the first fold), and that the full-sample H1 estimate is therefore
  descriptive, not an independent confirmatory test.
- §4.1: return formula anchored at the entry close (P_entry) rather than P_{T_theta}.
- §4.3: bootstrap replications raised from 10,000 to 20,000; both p-value conventions
  stated (null-centered one-sided (k+1)/(B+1); uncentered percentile CIs); t-tests
  labelled reference-only.
- §5.1: added the full exclusion breakdown (121 below threshold, 108 surge, 12 no clean
  side, 7 run-up, 47 late-entry) and the counts of unique events/symbols/weeks/months.
- §5.1: added the entry-week block interval and the January–June 2026 subsample
  (N=528, +2.038% mean, event-cluster p=0.0006, month-block p=0.1083) with its caveats.

## H1 numbers updated (10k -> 20k bootstrap; identical trades, means unchanged)

- Event-cluster CI [+0.251,+2.203]/p=0.0088 -> [+0.263,+2.200]/p=0.0087.
- Symbol CI [-0.175,+2.757]/p=0.0647 -> [-0.184,+2.776]/p=0.0652.
- Month CI [-0.652,+3.645]/p=0.1414 -> [-0.630,+3.651]/p=0.1427.
- Symbol-day p 0.0602 -> 0.0611.
- Equal-event month CI [-0.429,+2.358]/p=0.1915 -> [-0.417,+2.407]/p=0.1935.
- Family CIs: earnings [-0.468,+0.519] -> [-0.476,+0.521]; geo [+0.969,+8.485] ->
  [+0.936,+8.485]; geo event count (37) added.
- All other H1 values (means, medians, tail/cost/timing sensitivities, ablation) verified
  identical and retained.

## New content

- §5.4 "Benchmark-relative inference" + Table 3 + Figure 3: matched equal-notional SPY and
  sector-ETF trades over identical dates, costs on both legs; headline: **no 95% interval
  excludes zero** (SPY candidate +0.914%, p=0.0508; sector +0.191%, p=0.264).
- §7.4 "Do the treatments beat the baseline?" + Table 6: seed-paired T1+T2+T3+T4 minus
  Baseline at 6×20 (median ΔExcess −7.43 SPY / −3.44 QQQ; wins 0%/30%); honest conclusion
  that the treatments do not improve on the baseline in this sample.
- §6.2 (T2): explicit statement that walk-forward arms are online-refit schedules that
  continue refitting inside the 2026 test window (label-complete), while Baseline/T4 are
  frozen; train-fit N=423 and test N=721 (post-relevance-filter) stated.
- §7.3: seed-42 flagship 5-day moving-block bootstrap of daily excess return reported with
  caveats (SPY p=0.0317, QQQ p=0.3102, "descriptive rather than confirmatory").
- Abstract, Discussion, Conclusion rewritten to carry the negative/inconclusive results
  (benchmark-relative nulls, treatments-vs-baseline underperformance, no profitability
  claim, 2026 not a pristine holdout).

## CEM numbers updated

- Configuration matrix Table 4: identical values to the previous manuscript (reproduced
  exactly by the fresh audit-clean rerun; recomputation from equity logs verified).
- Cleaning-delta block p-values: 0.3445/0.3847 -> 0.3371/0.3884 (fresh 20k run, seed 42).
- Robustness table/figure: replaced entirely (legacy-universe grid -> new audit-clean grid,
  runs/icaif_grid_*; full test period only).

## Unchanged

- Related Work section: verbatim (per instruction, no new literature search).
- Cleaning reconciliation Table 1 (matches data/candidate_cleaning_summary.json).
- Signal-window and architecture figures (verified accurate; kept).
- Double-blind sigconf setup.
