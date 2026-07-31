# T4 allocator experiment report (2026-07-14)

**Question:** given exactly the same fitted T1+T2+T3+T4 policy schedule, eligible
candidates, entry/exit rules, Kelly sizing (with the identical frozen completed-trade
history), costs, capacity, and test dates, does the T4 event-priority allocator produce
better portfolio outcomes than FIFO or random allocation among contemporaneously competing
candidates?

**Verdict: B — suggestive but inconclusive.** T4 consistently beats FIFO under matched
conditions (9/10 SPY and 8/10 QQQ seeds, with better drawdowns), but it sits only around the
60th percentile of 1,000 matched random allocations (0/20 seed-benchmark cells above the
95th percentile, median empirical p ≈ 0.40), and the retrospective contested-decision
diagnostics show it does **not** pick better candidates than FIFO or than the rejected
alternatives. T4 produces a positive descriptive allocation advantage over FIFO — much of
which is FIFO being *worse than random* — but the evidence does not establish that T4 is
superior to random allocation.

## Design integrity

- Reference: the exact T1+T2+T3+T4 runs (seed 42 preserved; seeds 43–51 refit
  deterministically and verified equal to the preserved grid aggregates in all 18
  comparisons).
- Harness verification: the "t4" arm reproduces the reference run CSV to 1e-4 on both
  benchmarks, and the patched-FIFO arm equals a true `allocation_mode="fifo"` simulation to
  1e-4.
- The random arm re-runs the **full chronological simulation** per replication (capital,
  Kelly history, capacity, and skips all propagate); 1,000 replications per seed×benchmark
  (20,000 total), seeds stored as `SeedSequence([9000, cem_seed, bench_idx, rep])`.
  Single-candidate days are never shuffled; multi-candidate days are shuffled, which for
  uncontested batches is outcome-neutral up to sub-dollar cost interactions, so this equals
  randomizing only contested competitions. FIFO/random arms have no ranking, no same-symbol
  collapse, no preemption; eligibility, entry dates, exits, sizing, and data are never
  randomized.
- Frozen Kelly seed history: the reference (event-priority) train-simulation history is used
  identically in every arm.

## 1. How often did T4 actually change a portfolio decision?

Often — the experiment is informative. Per seed×benchmark (medians across the 20 runs):
~43 contested dates (of 111 trading days), ~292 candidate competitions, and **71–145
positions whose identity differs between T4 and FIFO** (median ≈ 90, i.e. roughly half of
~190 executed trades). Same-symbol collapses: 53–69 per run. **Preemption never fired: 0
preemptions in all 20 runs** — the fitted policies' concurrency and the candidate mix never
produced the all-slots-earnings condition, so T4's active ingredients in this sample are the
priority ranking and the same-symbol collapse only. Capacity skips: 30–237 per run.
(Source: `t4_contested_summary.csv`.)

## 2. Did T4 beat FIFO?

Yes, consistently (paired, same seed/benchmark; `t4_fifo_paired.csv`):

| | Δexcess median [IQR] | wins | ΔSharpe med. | ΔMaxDD med. | Δcost med. |
|---|---|---|---|---|---|
| SPY | +2.10 [+1.07, +4.55] | 9/10 | +0.24 (8/10) | +1.96 pts better (9/10) | −$164 |
| QQQ | +2.82 [+0.40, +4.88] | 8/10 | +0.15 (7/10) | +1.53 pts better (8/10) | −$348 |

Trade counts are similar (T4 vs FIFO vs random median, e.g. seed-50 QQQ: 188 / 185 / 183),
so the difference is not explained by trade counts.

## 3. Did T4 beat the median random allocator?

Modestly and inconsistently: 6/10 SPY seeds and 8/10 QQQ seeds are above the random median;
median T4 percentile 60.3 (SPY) and 58.0 (QQQ). Notably, **FIFO's median percentile is 30.7
(SPY) and 39.5 (QQQ)** — chronological order is a *below-random* allocator on this sample,
so much of T4's FIFO edge is escaping FIFO's specific weakness rather than superior ranking.

## 4. Was T4 statistically in the upper tail of random allocation?

No. 0 of 20 seed×benchmark cells exceed the random 95th percentile; the median empirical
one-sided p is 0.398 (SPY) and 0.421 (QQQ); the single best cell is p ≈ 0.10 (seed 44 QQQ,
96.6th percentile is not reached anywhere). (Source: `t4_seed_vs_random.csv`.)

## 5. Were results consistent between SPY and QQQ?

Directionally yes: both benchmarks show the same trio of findings (beats FIFO in a clear
majority; ~60th percentile vs random; never upper-tail). Magnitudes vary seed to seed
(Δexcess vs FIFO spans −14.5 to +9.9), and two seeds are strongly negative (seed 43 QQQ
−14.5, seed 49 SPY −5.7), so no per-seed claim is safe.

## 6. Did T4 improve return, risk, or only operational interpretability?

Relative to FIFO: both return (Q2) and risk — MaxDD improves in 17/20 runs and Sharpe in
15/20 — plus lower transaction costs. Relative to random allocation: only a weak return
tendency. Critically, the retrospective contested-decision diagnostics
(`t4_contested_aggregates.csv`, clustered by contested date and by economic event) show no
per-decision selection skill: T4-selected minus FIFO-selected standalone return is −0.14%
(SPY) and −0.19% (QQQ) per contested date with CIs crossing zero; T4-selected minus rejected
is ≈ 0; T4 includes the retrospectively best candidate on 52%/57% of contested dates and its
mean rank among ~7 contemporaneous alternatives is mid-pack. T4's portfolio-level edge over
FIFO therefore flows through path effects (which days capital is deployed, duplicate-symbol
de-concentration, drawdown shape), not through picking retrospectively better candidates.
These diagnostics are retrospective allocator grading, not information available to the live
policy.

## 7. Can the paper claim that T1+T2+T3+T4 beats matched random allocation?

**No.** The paper may claim: (a) the full stack historically outperformed SPY/QQQ across
seeds (existing ten-seed results); (b) T4 consistently outperformed FIFO under matched
frozen-stack conditions; (c) T4 was *not* shown to outperform matched random allocation
(~60th percentile, p ≈ 0.4, never upper-tail), and per-decision diagnostics show no
selection skill. The prior candidate random-selection experiment tests CEM entry-gate
selectivity within the cleaned pool and says nothing about T4.

## Outputs

- `t4_allocator_seed_results.csv` — deterministic T4/FIFO arm rows (all §5 metrics).
- `t4_random_allocator_distribution.csv` — 20,000 random-replication rows (with stored seeds).
- `t4_seed_vs_random.csv`, `t4_fifo_paired.csv` — aggregates.
- `t4_contested_decisions.csv` (per contested date), `t4_contested_summary.csv`,
  `t4_contested_aggregates.csv`.
- `t4_allocator_figure.pdf/.png`.
- Not rerun: RL, H1, Gemini, cleaning, the optimizer-budget grid.
