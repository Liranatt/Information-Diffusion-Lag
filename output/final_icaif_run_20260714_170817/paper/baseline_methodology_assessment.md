# Baseline methodology assessment (2026-07-14, final revision)

## Code paths inspected

- `backtesting/optimize_cem.py::main` (lines 2319–2688): split definition, `oos_start = 2026-01-01`
  (hard-coded), test simulation call.
- `backtesting/optimize_cem.py::cem_search` (lines 1792–1937): the `use_wf=False` branch
  (Baseline, T4-alone) and the `use_wf=True` branch (all T2 arms).
- `backtesting/optimize_cem.py::rows_completed_before` / `assert_rows_completed_before`
  (lines 742–774): label-completion eligibility, strict `t_e < cutoff`.
- `backtesting/optimize_cem.py::create_expanding_wf_folds` (lines 777–847): fold construction.
- `backtesting/optimize_cem.py::truncate_paths` (lines 336–357) and the horizon assertion in
  `sim_opp_cost` (lines 972–978): path truncation at every fit/eval horizon.

## What information each configuration can use

**A. No-T2 configuration ("Baseline"):** one batch CEM fit (6×20, seed 42+offset) on
`rows_completed_before(df, 2026-01-01)` = the 423 candidates whose outcomes (`t_e`) completed
strictly before 2026-01-01. The fitting simulation is truncated at 2025-12-31; the single
argmax policy is then **frozen** and evaluated once on the January–June 2026 test candidates.
FIFO allocation, no Kelly, ordinary objective.

**B. T2-only ("T2 TrainWindows"):** identical objective/allocation/sizing, but the policy is
an expanding walk-forward schedule. Fold *i* fits only on candidates with
`t_e < fold_i.eval_start` (fail-closed assertion) and governs the next three-month block; the
schedule keeps refitting **inside** the 2026 test period (folds starting 2026-02-01 and
2026-05-01 use outcomes completed in earlier test months). `policy_scope =
online_refit_schedule`.

## The four distinctions the revision required

1. **Hindsight inside the reported training path — YES for the no-T2 arm.** Its policy is
   selected by maximizing the objective over the full pre-2026 history and its "train"
   metrics re-run that same policy over that same history. The training path is therefore
   retrospectively optimized (ordinary in-sample optimization). The manuscript's tables report
   only test-period metrics, and the revised text now says explicitly that the baseline's
   training path is in-sample and is not reported as performance.
2. **Legitimate fitting on completed pre-2026 data — YES.** The batch fit uses only outcomes
   completed before 2026-01-01 and price/probability paths truncated at 2025-12-31. This is
   exactly the information a live operator would possess on 2026-01-01.
3. **Leakage into the January–June 2026 test — NO, for both configurations.** Neither fit can
   see 2026 outcomes at fitting time (label-completion is strict), every simulation is
   truncated at its own horizon, and generated trades exiting past a horizon raise an error.
4. **Policy refitting during 2026 — NO for the no-T2 arm (frozen); YES for every T2 arm**
   (label-complete online refits at 2026-02-01 and 2026-05-01 on this artifact).

## Determination

T2 is **an optional chronological-protocol variant, not the minimum protocol required for a
valid 2026 test**. The no-T2 configuration is a chronologically legitimate, implementable
frozen-policy deployment for the January–June 2026 comparison: at the test boundary it uses
strictly less future information than the T2 arms use over the test period (it never refits).
T2 *is* the stricter protocol for producing meaningful pre-2026 out-of-fold performance
paths, but the paper reports no such paths.

The genuine methodological defect in the previous presentation was different: calling the
paired comparison "the four treatments versus Baseline" presented the bundle as a clean
treatment ablation when switching Baseline → T1+T2+T3+T4 changes **two things at once** —
the fitting protocol (frozen batch → online walk-forward) *and* the added techniques
(T1 fitness penalty, T3 sizing, T4 allocation).

## What was run to support the correction

One minimal missing configuration (authorized by the brief): **T2-only**, audit-clean
universe, seed 42, 6×20, SPY+QQQ, identical costs and test period →
`runs/icaif_t2_only_seed42_6x20/experiment_results_clean.csv`:

| Arm | SPY excess (pts) | QQQ excess (pts) |
|---|---|---|
| Baseline (frozen batch fit) | +21.75 | +9.77 |
| T2 only (walk-forward, FIFO) | +20.53 | −0.67 |
| T1+T2 | +22.35 | +7.03 |
| T1+T2+T3 | +16.98 | +4.98 |
| T1+T2+T3+T4 | +19.97 | +7.02 |

Single-seed decomposition (descriptive only): the protocol switch itself costs −1.22 (SPY) /
−10.44 (QQQ) points; given T2, T1 adds +1.82 / +7.70; T3 subtracts −5.37 / −2.05; T4 adds
+2.99 / +2.04. The ten-seed paired deficit of the full bundle versus the frozen baseline
(median −7.43 SPY, 0/10 wins; −3.44 QQQ, 3/10 wins) therefore cannot be attributed to
T1/T3/T4 alone; a material part is the online-refit protocol itself on this window.

## Changes made in the manuscript

- The baseline is now described precisely as the **frozen batch-fit baseline** (one
  label-complete pre-2026 CEM fit, frozen through the test), with an explicit statement that
  its training path is in-sample and not reported as performance, and that the 2026 test
  contains no leakage for either protocol.
- A **T2-only row** was added to the configuration matrix so the protocol effect and the
  treatment effects are visible separately.
- The paired-comparison section and abstract no longer say "the four treatments underperform
  the baseline" as a treatment-only claim; they say the treated configuration (walk-forward
  protocol plus T1/T3/T4) underperforms the frozen batch-fit baseline, with the seed-42
  decomposition reported as descriptive context.
- No configuration is called "Oracle", "best possible outcome", or "hindsight reference";
  the inspected code supports none of those terms for any arm.
