# Manuscript revision log — paper_final.tex → paper_final_revised.tex (2026-07-14)

## Baseline decision

The no-T2 configuration remains the paper's baseline, renamed precisely as the **frozen
batch-fit Baseline**: one CEM fit on the 423 label-complete pre-2026 candidates (all
simulations truncated at 2025-12-31), then frozen across the January–June 2026 test. Code
inspection (`baseline_methodology_assessment.md`) shows it contains no leakage into the test
and corresponds to an implementable deployment at the test boundary; its retrospectively
optimized *training* path is now explicitly declared in-sample and is not reported as
performance. T2 is classified as an optional chronological-protocol variant, not the minimum
protocol for test validity. Because "treatments vs. Baseline" previously conflated the
protocol switch with T1/T3/T4, a **T2-only arm** (one new minimal run,
`runs/icaif_t2_only_seed42_6x20`, same seed/budget/data/costs/test period) was added to the
configuration matrix, and the paired-comparison conclusion was reworded accordingly.

## Claims corrected or weakened

1. **Holding interval** — `[T_θ, T_e−1]` corrected to `[T_entry, T_e−1]`; §3.1 now defines
   T_θ (fixed 0.55 screening cross, unoptimized) and T_entry (first stored close on/after
   policy acceptance) separately (VERIFIED in code).
2. **47 excluded observations** — previously "enter too late or lack a usable price pair";
   the artifact shows all 47 are late-acceptance exclusions and zero are missing-price
   (CONTRADICTED → corrected).
3. **Same-session timing** — added: daily data do not establish same-session ordering
   between the probability signal and the equity close (0/887 verifiable); next-stored-close
   is the conservative sensitivity.
4. **Endpoint provenance** — T_e now described as "the scheduled contract endpoint stored in
   the research dataset"; limitation added that endpoint revisions and actual resolution
   timestamps are not versioned (PARTIALLY VERIFIED).
5. **Paired finding** — "the four treatments underperform the plain CEM baseline" weakened
   to: the treated configuration underperforms the frozen batch-fit baseline, with the
   difference bundling the protocol switch and T1/T3/T4; single-seed decomposition reported
   as descriptive (T2-only: −1.22 SPY / −10.44 QQQ vs Baseline; then T1 +1.82/+7.70,
   T3 −5.37/−2.05, T4 +2.99/+2.04).
6. **Configuration-matrix text** — "all configurations have positive excess return" replaced
   (T2-only QQQ is −0.67).
7. **Concentration** — added exact leave-one-out values: removing USO (104 rows) lowers the
   candidate mean +1.208% → +0.313%; removing March 2026 (116 rows) → +0.206% (event level
   +0.631% → +0.435%); geopolitical result described as oil-linked and period-specific.
   Carried into abstract, §5.3, Discussion, and Conclusion.
8. **Gemini methodology** — new §3.2 documenting: deterministic prefilter + duplicate
   normalization, two-pass Gemini (question-relevance gate ≥0.60 with positive-tone
   requirement; per-asset connection strength), stored pair relevance R = R^q·C with the
   >0.50 experiment gate, JSON-schema validation, separate polarity labeling
   (override > LLM > regex), 109-pair human audit. No specific model version is named (the
   runtime model is environment-configured); no sealed-model or universe-accuracy claim.

## Limitations added

Endpoint provenance (unversioned scheduled endpoint, no resolution timestamps); eligibility
parameters inherited from the portfolio optimizer (already present, retained); concentration
of the family result.

## Figures and tables changed

- `fig_signal_window_v2.pdf` — rebuilt (matplotlib, `figures/build_signal_window_v2.py`):
  distinguishes T_θ, T_entry, the holding interval [T_entry, T_e−1], T_e−1, and the stored
  scheduled endpoint T_e; labeled schematic.
- `table_benchmark_v2.tex` — rebuilt to fix a 54pt overfull box (footnotesize, shortened
  labels, 2-decimal CIs). Same numbers.
- `table_cem_matrix_v2.tex` — added the T2-only row; caption states frozen vs walk-forward
  protocol per row.
- `table_paired_v2.tex` — caption updated ("frozen batch-fit Baseline"); numbers unchanged.
- Other figures (H1 evidence, benchmark forest, budget robustness, architecture) unchanged
  except width reductions for layout; none contains a validation/final split or a selected
  budget.

## Additional CEM run

One: `python -m backtesting.optimize_cem --experiments t2_trainwindows --benchmarks SPY QQQ
--seed 42 --cem-iters 6 --cem-pop 20 --run-id icaif_t2_only_seed42_6x20` (audit-clean
universe). Nothing else was rerun; H1, benchmark inference, and the 40-run grid are the
existing final-bundle artifacts.

## Final state

- Page count: **8 pages including references** (pdfTeX/MiKTeX, two passes).
- Anonymous ACM sigconf, double-blind preserved.
- LaTeX: zero overfull hboxes/vboxes in the final compile; no unresolved references or
  citations; all eight pages visually inspected (rendered PNGs `page_1..8.png` retained).

## Unresolved scientific issues

1. The T2-only decomposition exists at seed 42 only; the ten-seed paired distribution
   isolates Baseline vs the full bundle, not the protocol switch alone. A ten-seed T2-only
   sweep would decompose the paired deficit distributionally (not run — outside the minimal
   scope authorized).
2. Endpoint point-in-time provenance and actual resolution timestamps remain unavailable in
   the artifacts; stated as a limitation rather than resolved.
3. The architecture figure's "Eligibility at T_θ" box anchors eligibility at the screening
   time; acceptance timing detail lives in Figure 1 and §3.1. Left unchanged (no factual
   error: eligibility evaluation begins at T_θ).
