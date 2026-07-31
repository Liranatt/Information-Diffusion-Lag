# Manuscript revision log — paper_final_cem_contribution.tex → paper_final_t4_test.tex

Date: 2026-07-14. All new numbers trace to CSVs in this directory
(`t4_allocator/`); the experiment design, verification, and full results are in
`t4_allocator_experiment_report.md`.

## New experiment integrated

Matched allocator experiment isolating T4: per seed 42–51 and benchmark, the
fitted T1+T2+T3+T4 stack (schedule, frozen Kelly history, eligibility, exits,
sizing, capacity, costs, test dates) is held fixed and only the ordering rule
for contemporaneously competing candidates varies — exact T4, FIFO, and 1,000
seeded random orderings (20,000 full chronological simulations). Seeds 43–51
schedules were refit deterministically and verified equal to the preserved grid
aggregates (18/18 comparisons); the harness reproduces the reference T4 run to
1e-4 and the patched FIFO arm equals a true FIFO simulation to 1e-4.

## Manuscript changes

1. **New subsection 7.5 "Does event-priority allocation add value?"** —
   contested-decision volume (~43 contested dates, ~90 identity changes per
   run; preemption fired 0/20 runs), T4 vs FIFO (median +2.10 SPY 9/10, +2.82
   QQQ 8/10, better MaxDD 17/20), T4 vs random (median percentile 60.3/58.0,
   0/20 cells above the random 95th percentile, median p≈0.40; FIFO below the
   random median at 30.7/39.5), and the retrospective contested-decision
   diagnostics (no per-decision selection skill).
2. **Corrected scope of the earlier random-selection test** (§7.2): explicitly
   stated it evaluates the CEM entry gates within the cleaned pool and says
   nothing about T4.
3. **Experimental-design list** extended with item (6), the allocator
   experiment.
4. **Abstract** updated: treatments sentence now separates end-to-end
   attribution from the matched allocator result (beats FIFO 17/20; not
   distinguishable from random ordering).
5. **Conclusion** now keeps three portfolio conclusions explicitly separate:
   (i) full stack vs SPY/QQQ (ten-seed result); (ii) CEM entry gates vs matched
   random candidate selection (not distinguishable); (iii) T4 vs FIFO/random
   allocation (beats FIFO; not shown to beat random).
6. **Discussion and limitations** updated (allocator finding; off-policy caveat:
   the frozen schedules were fitted with T4 in the loop, so FIFO/random arms
   are slightly off-policy).
7. **Space recovered** to stay at 8 pages: budget-robustness Table 6 replaced
   by its per-budget medians in text (all numbers preserved), fold table set in
   scriptsize, figure widths reduced, bibliography in footnotesize, and prose
   tightened in the discussion/conclusion. No H1 content, benchmark-relative
   result, or concentration diagnostic was removed.

## Final state

paper_final_t4_test.pdf: **8 pages including references**, anonymous ACM
sigconf, no overfull hboxes (one transient 1.2pt vbox warning resolved in the
final compile), pages 7–8 and the new subsection visually inspected.

## Verdict carried in the paper

T4: **suggestive but inconclusive** — consistently better than FIFO under
matched frozen-stack conditions (mainly portfolio-path and drawdown effects),
not shown to be better than random ordering of the same competing candidates,
and with no retrospective per-decision selection skill. The paper does NOT
claim that T1+T2+T3+T4 beats matched random allocation.
