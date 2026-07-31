# Manuscript revision log — paper_final_revised.tex → paper_final_cem_contribution.tex

Date: 2026-07-14. All new numbers are traceable to CSVs in this directory
(`cem_contribution/`) or previously verified bundle artifacts; nothing was
carried from memory.

## New experiments run for this revision

- **Treatment ladder, seeds 42–51 at 6×20** (`runs/icaif_ladder_seed42..51`):
  Baseline, T2-only, T1+T2, T1+T2+T3 (SPY+QQQ). T1+T2+T3+T4 seed rows reused
  from the preserved grid aggregates (`../robustness/icaif_robustness_run_level.csv`).
- **Fixed default reference + permissive arm + selection/execution decomposition**
  (deterministic harness `sim_lib.py`, verified to reproduce the canonical seed-42
  Baseline to 4 decimals) → `cem_decomposition_results.csv`.
- **Matched random-selection test** (500 monthly-matched draws × 2 benchmarks,
  fixed execution, seed 42) → `random_selection_results.csv`, `random_selection_draws.csv`.
- **Fold-level walk-forward report** (per-fold re-simulation, T2-only seed 42;
  parameter-stability across folds/seeds) → `fold_level_results.csv`,
  `fold_policy_stability.csv`.
- **Portfolio block bootstrap** (5-day blocks, 20k reps) for the reference,
  permissive, and frozen-CEM arms → `portfolio_block_bootstrap.csv`.
- NOT rerun: ingestion/cleaning, Gemini/polarity, H1, benchmark-relative H1,
  the 40-run budget grid.

## Structural changes to the paper

1. **Abstract** rewritten: two-question framing; reports the reference-vs-CEM
   result (8/10 seeds, +16.5 vs +13.2 SPY; +8.6 vs +5.2 QQQ), substitutability
   of selection/execution, random-selection percentiles (86th SPY / 37th QQQ),
   treatments adding no terminal value, and no statistically established excess.
   No "beats the market" claim; wording is "historically outperformed."
2. **Introduction**: states the three-object distinction (H1 distribution /
   candidate selection / execution+construction) and that non-significance is
   not treated as absence of signal nor profitability as a predictive law.
3. **CEM section reframed** ("Portfolio Experiments"): research question first;
   CEM described as a stochastic derivative-free optimizer of a ten-parameter
   rule policy — not RL, deterministic once frozen; parameters grouped into
   selection vs execution; new "Experimental design" subsection defining the
   five matched experiments and the decomposition limitation (gate timing
   bundles with selection).
4. **Results reordered** per the prescribed sequence: (7.1) non-optimized
   reference vs frozen CEM (new Table 4, `table_cem_main.tex`); (7.2)
   decomposition + matched random selection (new Figure 4,
   `fig_cem_contribution.pdf`); (7.3) fold table (new Table 5,
   `table_folds.tex`) with the explicit statements that folds do NOT
   monotonically improve and that parameter variation indicates estimation
   instability; (7.4) T1–T4 attribution across seeds (adjacent paired deltas;
   only consistent effect: T3 negative on SPY 1/10); (7.5) budget robustness
   (Table 6 kept) + cleaning effect compressed to one sentence + block-bootstrap
   inference.
5. **Removed** (space + superseded): the seed-42 six-configuration matrix table
   (superseded by the ten-seed ladder table), the old paired
   baseline-vs-flagship table (retained as one sentence), the budget-robustness
   figure (Table 6 keeps all numbers), and the architecture figure. H1 content,
   benchmark-relative results, and concentration diagnostics fully preserved.
6. **RL**: no PPO/RL artifacts exist in the repository
   (`rl_experiment_assessment.md`); the paper contains no RL results — only a
   future-work sentence about higher-capacity sequential policies, framed as a
   testable question. The prescribed negative-result paragraph was NOT inserted
   because there are no artifacts to support it.
7. **Discussion/Conclusion** rewritten to the tested conservative conclusion:
   selective economic value supported; universal effect not established; the
   required statements (concentration, earnings near chance, continuous
   probability stream as the object, short inspected window, material
   benchmark-relative uncertainty, no prospective claim) all present. No
   "buy the news, sell the hype" phrasing; terminology uses
   "probability-triggered, selectively executed equity allocation."

## Final state

- **paper_final_cem_contribution.pdf: 8 pages including references**, anonymous
  ACM sigconf, zero overfull boxes, no unresolved references; all pages
  rendered and visually inspected (`page_1..8.png`).

## Unresolved scientific/computational issues

1. The random-selection test does not equalize executed trade counts (random
   draws execute fewer trades after capacity skips: median 159 vs 213 SPY),
   and its availability pool uses immediate-at-0.55 entry timing; both stated
   in the paper as limitations.
2. The random-selection test uses the seed-42 selection arm only (500 sims ×
   10 seeds was outside the compute budget); the seed-42 arm is close to its
   own cross-seed median (+18.59 vs median +17.67 SPY).
3. QQQ ladder medians are non-monotone in a way that resists a tidy narrative
   (T1+T2 median +0.88 but T1+T2+T3 +7.36); reported faithfully as seed noise.
4. RL comparability remains untestable until RL artifacts are committed.


## Addendum — author-review round (same day)

Applied after author feedback:

1. **RL included as author-disclosed negative evidence.** The author confirmed the
   exploratory PPO experiments were their own work, deleted during repository
   cleaning. The Discussion now carries the qualitative negative-result paragraph
   (no numbers — none are traceable), with the artifact-not-retained disclosure and
   without the forbidden causal/win-rate claims. `rl_experiment_assessment.md` updated.
2. **Reference-policy provenance corrected.** The author disclosed that the repository
   default values were informally chosen with knowledge of earlier fitted policies
   (e.g., 0.70 entry floor, 3% profit lock). Sections 6.3, 7.1, the abstract, and the
   limitations now describe the reference as fixed but not hindsight-free — a
   conservative comparator that biases the study against finding a CEM improvement,
   whose own absolute performance overstates an uninformed operator.
3. **Renamed** "Frozen CEM baseline" to "Standard CEM" throughout (text, Table 4,
   Figure 4 panel A), and "Flagship T1+T2+T3+T4" to "All-treatment arm" (Table 6).
   Standard CEM is presented as the primary configuration on protocol grounds, with
   an explicit statement that its test outcome cannot justify selecting it.
4. **All-treatment fold-level results added** (`run_fold_report_alltreat.py`;
   fold_level_results.csv now covers both arms): fold-excess correlation 0.86 with
   T2-only; the late-fold strength is attributed to the 2026 candidate stream, stated
   in Section 7.3.

Recompiled: 8 pages, no overfull boxes.
