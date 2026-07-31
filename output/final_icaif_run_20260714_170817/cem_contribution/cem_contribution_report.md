# CEM contribution report (2026-07-14)

Central question: can a weak-but-positive Polymarket-conditioned return signal be converted
into economically useful portfolio performance through selective candidate choice and a
low-dimensional optimized trading policy?

**Verdict: partially supported.** Selectivity itself and a single frozen CEM fit improved on
a fixed, pre-specified, non-optimized reference across optimizer seeds — historically and
consistently, but not statistically established — while the specific CEM candidate choices
are not distinguishable from arbitrary contemporaneous selection, optimized execution adds
nothing beyond optimized selection, and the additional treatments (T1/T3/T4 and walk-forward
refitting) do not add terminal value. Details per question below. All arms share the
identical audit-clean universe, chronology, cost model, benchmark mechanics, and the single
January–June 2026 test period (111 trading days); portfolio returns are terminal-equity
returns, never summed trade PnL.

Source files (this directory unless noted): `cem_decomposition_results.csv`,
`cem_ladder_seed_results.csv`, `cem_ladder_summary.csv`, `cem_ladder_paired_deltas.csv`,
`random_selection_results.csv` (+`random_selection_draws.csv`), `fold_level_results.csv`,
`fold_policy_stability.csv`, `portfolio_block_bootstrap.csv`,
`../robustness/icaif_robustness_run_level.csv` (flagship seeds),
`runs/icaif_ladder_seed42..51`, `runs/icaif_base_vs_all_seed42`.

## 1. Does CEM improve on a simple non-optimized policy?

**Yes — moderately, for the plain frozen fit; historically but not statistically.**
The non-optimized reference is the repository-default policy documented before these
experiments (`core/policy.py::DEFAULT_POLICY` + 10% size, 10 slots, FIFO, no search):
SPY excess **+13.21** (return +21.73%, Sharpe 2.58); QQQ excess **+5.22** (Sharpe 2.43).
The frozen CEM Baseline (6×20, seeds 42–51): SPY median excess **+16.50** [IQR 14.74,
17.84], min +10.49, 10/10 positive, **8/10 seeds beat the simple reference**; QQQ median
**+8.62** [7.05, 11.85], 9/10 positive, **8/10 beat the reference**. Median improvement
≈ +3.3 points on both benchmarks. Caveat: the treated configurations mostly do NOT beat
the simple reference (T1+T2+T3+T4: 1/10 SPY, 4/10 QQQ; T1+T2+T3: 2/10 SPY) — the CEM
contribution survives only in its simplest frozen form.

## 2. Does candidate selection improve on matched random selection?

**Not established; benchmark-dependent.** Holding execution fixed at the default policy,
the seed-42 CEM-selection arm earned +18.59 SPY excess vs a 500-draw matched random-selection
distribution with median +11.57 and [5th, 95th] = [+2.52, +22.21]: **86th percentile,
empirical one-sided p = 0.142**. On QQQ the observed +6.22 sits at the **37th percentile**
(random median +8.11, p = 0.633) — random selection is as good or better.
Design notes: availability pool = every eligible candidate entering at its first ≥0.55
point; draws matched to the observed arm's monthly entered-trade counts; identical fixed
execution and capacity. Limitations documented: (a) selection and confirmation timing are
one mechanism (the entry gates) in this architecture, so "selection" includes gate timing;
(b) random draws execute fewer trades after capacity skips (median 159/154 vs 213/206),
a mechanical asymmetry that, in a positive-mean stream, flatters the observed arm.

## 3. Does optimized execution add value beyond selection?

**No.** Ten-seed medians of the decomposition (excess, SPY/QQQ):
all-eligible + fixed execution **+8.22 / +1.11** (deterministic); fixed default policy
**+13.21 / +5.22**; CEM selection + fixed execution **+17.67 / +9.30**; default selection +
CEM execution **+17.14 / +9.34**; full CEM **+16.50 / +8.62**. Either optimized half alone
matches (slightly exceeds) the fully optimized policy; combining them adds nothing. The
selection and execution channels act as substitutes — consistent with a broadly profitable
candidate stream in which any sane tightening of the policy box captures most of the gain —
and there is no evidence of selection–execution synergy. What clearly matters is
*selectivity per se*: the all-eligible arm is the worst arm on both benchmarks.

## 4. Are the gains consistent across folds?

**No.** T2-only (seed 42) fold-level excess: SPY −0.65, −2.93, +5.76, +11.61, +11.17;
QQQ +0.32, −3.25, +5.63, −3.52, +9.31 (folds 1–5; folds 4–5 lie inside the 2026 test).
The strong results concentrate in the 2026 SPY folds; the two late-2025 folds are flat to
negative on both benchmarks and QQQ fold 4 is negative. Fold-level performance does not
monotonically improve, and where later folds are better this coincides with the richer 2026
candidate stream rather than demonstrable adaptation. Fitted parameters vary substantially
across folds (mean across-fold std ≈ 24% of the bound width for `enter_floor`, ≈ 16% for
`atr_mult`, ≈ 21% for `max_concurrent` and `max_prob_surge`), which we interpret as
estimation instability rather than successful adaptation.

## 5. Which of T1–T4 adds or subtracts value?

Ten-seed paired adjacent-step deltas at 6×20 (median Δ excess, wins/10):

| Step | SPY | QQQ |
|---|---|---|
| T2-only − Baseline (protocol switch) | +2.42 (6/10) | **−6.63 (3/10)** |
| +T1 (friction fitness), given T2 | +1.05 (6/10) | −0.95 (4/10) |
| +T3 (half-Kelly), given T1+T2 | **−6.33 (1/10)** | +4.67 (6/10) |
| +T4 (event priority), given T1+T2+T3 | −2.20 (3/10) | +0.93 (6/10) |

The only near-consistent effect is that **T3 subtracts on SPY** (1/10 wins). Every other
step is inside seed noise and flips sign across benchmarks — including the walk-forward
protocol itself (positive median on SPY, strongly negative on QQQ). The earlier single-seed
decomposition is therefore descriptive only; across seeds, none of T1–T4 reliably improves
terminal excess. Their value, where it exists, is operational (label-complete chronology,
bounded sizing, explainable allocation), not performance.

## 6. Are portfolio excess returns statistically established, or only historically positive?

**Only historically positive.** Five-day moving-block bootstrap (20,000 replications,
seed 42) on daily excess returns, 111 days: frozen CEM seed-42 SPY mean daily excess
1.68e-3, 95% CI [−1.75e-4, +3.88e-3], one-sided p = 0.068; simple default SPY p = 0.166;
all-eligible SPY p = 0.252; every QQQ arm p ≥ 0.266. No arm's excess is statistically
established; the paper wording is "historically outperformed the benchmark during the test
period."

## 7. What can and cannot be concluded from the short test period?

Can: on this single 5.5-month, repeatedly inspected window, selective probability-triggered
allocation converted the candidate stream into portfolios that historically outperformed
both buy-and-hold benchmarks across all optimizer seeds; the ordering
(no selection < fixed selectivity < optimized policy) is stable across seeds; the
improvement over the non-optimized reference is modest (~+3 points median) relative to the
seed spread. Cannot: any population-level alpha claim; that CEM's specific candidate
choices beat arbitrary choice (Q2); that the treatments or walk-forward refitting help;
generalization across regimes (the fold table shows regime dependence, and the H1 evidence
is concentrated in oil-linked assets and March 2026); prospective performance.

## RL

No PPO/RL artifacts exist in the repository; see `rl_experiment_assessment.md`. RL appears
in the paper only as future work, with no claimed experimental result.
