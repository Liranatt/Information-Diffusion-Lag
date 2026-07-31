from pathlib import Path

HERE = Path(__file__).resolve().parent
p = HERE / "paper_final_t4_test.tex"
s = p.read_text(encoding="utf-8")

subs = [
# Abstract: nuance the treatments sentence with the allocator result.
("Additional treatments --- friction-aware fitness, walk-forward refitting, half-Kelly sizing, event-priority allocation --- do not add terminal value across seeds.",
 "Additional treatments --- friction-aware fitness, walk-forward refitting, half-Kelly sizing, event-priority allocation --- do not add terminal value across seeds end to end; in a matched allocator experiment on a frozen stack, event-priority allocation consistently beats FIFO ordering (17 of 20 seed--benchmark cells) but is statistically indistinguishable from random ordering of the same competing candidates (median percentile 58--60, never above the random 95th percentile)."),

# Design list: add experiment (6).
("(5) Fold-level walk-forward results are reported individually.",
 "(5) Fold-level walk-forward results are reported individually. (6) An \\emph{allocator experiment} freezes the fitted all-treatment stack per seed --- schedule, Kelly history, eligibility, exits, sizing, capacity, costs --- and varies only the rule that orders contemporaneously competing candidates: the exact T4 allocator, FIFO, and 1,000 seeded random orderings per seed and benchmark, each a full chronological simulation."),

# 7.2: clarify what the candidate random-selection test does NOT evaluate.
("Selective question choice is therefore \\emph{not statistically established} as the source of value --- what the data support is that trading this pool selectively, under disciplined rules, historically outperformed trading all of it.",
 "Selective question choice is therefore \\emph{not statistically established} as the source of value --- what the data support is that trading this pool selectively, under disciplined rules, historically outperformed trading all of it. This test evaluates the CEM \\emph{entry gates} within the cleaned pool; it says nothing about the T4 allocator, which is tested separately below."),

# New subsection after treatment attribution.
("\\subsection{Seed and budget robustness; statistical evidence}",
 "\\subsection{Does event-priority allocation add value?}\n\\label{sec:t4alloc}\n\nThe ladder step above compares end-to-end pipelines, in which adding T4 also changes the fitted optimum. A matched allocator experiment isolates T4 itself: per seed, the fitted all-treatment schedule, frozen Kelly history, eligibility, exits, sizing, capacity, and costs are held fixed, and only the ordering of contemporaneously competing candidates varies (T4, FIFO, or 1,000 seeded random orderings, each a full chronological simulation). The allocator has real scope: a median of ${\\sim}43$ contested dates and ${\\sim}90$ positions per run whose identity differs between T4 and FIFO (roughly half of all trades), driven by priority ranking and same-symbol collapse --- the preemption rule never fired in any of the 20 runs. Against FIFO, T4 wins consistently: median paired excess difference $+2.10$ points on SPY (9/10 seeds) and $+2.82$ on QQQ (8/10), with better maximum drawdown in 17/20 cells and similar trade counts. Against matched random ordering it is indistinguishable: median percentile 60.3 (SPY) and 58.0 (QQQ), no cell above the random 95th percentile, median empirical one-sided $p\\approx0.40$; FIFO itself sits \\emph{below} the random median (percentile 30.7 and 39.5), so much of T4's FIFO edge is escaping a below-random ordering rather than superior ranking. Retrospective contested-decision diagnostics agree: T4-selected candidates are no better than FIFO-selected or rejected alternatives (differences $\\approx-0.1$ to $-0.2\\%$ per contested date, date- and event-clustered intervals crossing zero), and T4 holds a mid-pack mean rank among ${\\sim}7$ contemporaneous alternatives. One caveat: the frozen schedules were fitted with T4 in the loop, so the FIFO and random arms are evaluated slightly off-policy. The supported statement is therefore: T4 improves on FIFO under capacity constraints --- through portfolio-path and drawdown effects, not through picking retrospectively better candidates --- and is not shown to beat random allocation.\n\n\\subsection{Seed and budget robustness; statistical evidence}"),

# Conclusion: three separated statements.
("The value came from disciplined selectivity as such: optimized selection and optimized execution proved substitutable, the optimizer's specific candidate choices were not statistically distinguishable from arbitrary choices within the same pool, and the additional treatments improved operational realism but not terminal return.",
 "Three portfolio conclusions must be kept separate. First, the full T1+T2+T3+T4 stack historically outperformed SPY and QQQ across all ten optimizer seeds. Second, the CEM entry gates were not statistically distinguishable from matched random candidate selection within the same cleaned pool: the value came from disciplined selectivity as such, and optimized selection and optimized execution proved substitutable. Third, under matched frozen-stack conditions the T4 event-priority allocator consistently beat FIFO (17 of 20 seed--benchmark cells, mainly via portfolio-path and drawdown effects) but was not shown to beat random ordering of the same competing candidates; the additional treatments improved operational realism, not terminal return."),
]
for old, new in subs:
    assert old in s, "NOT FOUND: " + old[:80]
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("applied", len(subs), "edits")
