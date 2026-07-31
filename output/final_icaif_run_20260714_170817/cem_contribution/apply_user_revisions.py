from pathlib import Path

HERE = Path(__file__).resolve().parent
p = HERE / "paper_final_cem_contribution.tex"
s = p.read_text(encoding="utf-8")

subs = [
# ── Rename: frozen CEM baseline -> standard CEM ──────────────────────────────
("a non-optimized reference, a frozen CEM policy, selection-versus-execution decomposition",
 "a non-optimized reference, a standard (single-fit, frozen) CEM policy, selection-versus-execution decomposition"),
(r"\subsection{Non-optimized reference versus frozen CEM}",
 r"\subsection{Non-optimized reference versus standard CEM}"),
("(2) The \\emph{frozen CEM baseline} and the treatment ladder",
 "(2) The \\emph{standard CEM} policy (one fit on completed pre-2026 data, frozen through the test) and the treatment ladder"),
("(3) A \\emph{selection/execution decomposition} crosses the CEM baseline's five selection parameters",
 "(3) A \\emph{selection/execution decomposition} crosses the standard CEM policy's five selection parameters"),
("The frozen CEM baseline improved on it in",
 "The standard CEM policy improved on it in"),
("For the frozen CEM baseline (seed 42):",
 "For the standard CEM policy (seed 42):"),
("Figure~\\ref{fig:contribution}A decomposes the frozen policy.",
 "Figure~\\ref{fig:contribution}A decomposes the standard CEM policy."),
("End to end, the all-treatment arm underperforms the frozen baseline by median",
 "End to end, the all-treatment arm underperforms standard CEM by median"),
("switching the frozen fit to walk-forward (T2-only $-$ Baseline)",
 "switching the frozen fit to walk-forward (T2-only $-$ standard CEM)"),
("and one frozen low-dimensional CEM fit reliably improved on a pre-specified non-optimized reference.",
 "and one standard low-dimensional CEM fit reliably improved on the fixed reference."),
("One frozen, low-dimensional Cross-Entropy Method (CEM) fit improved on that reference",
 "One standard, low-dimensional Cross-Entropy Method (CEM) fit (a single frozen pre-2026 fit) improved on that reference"),
("a low-dimensional CEM policy concentrated capital in a tighter subset",
 "a standard low-dimensional CEM policy concentrated capital in a tighter subset"),

# ── Reference-policy provenance (author-disclosed) ───────────────────────────
("(1) A \\emph{fixed default policy} --- the repository's pre-specified rule set documented before these experiments (entry 0.75/0.70, one confirmation day, surge cap 0.40, run-up cap 0.10, ATR 3.65, lock 3\\%, $\\theta_{out}$ 0.55, 10\\% size, 10 slots, FIFO) --- provides the non-optimized reference; a \\emph{fully permissive} variant trades every eligible candidate at its first $0.55$ cross.",
 "(1) A \\emph{fixed default policy} --- the repository's default rule set (entry 0.75/0.70, one confirmation day, surge cap 0.40, run-up cap 0.10, ATR 3.65, lock 3\\%, $\\theta_{out}$ 0.55, 10\\% size, 10 slots, FIFO) --- provides the reference without any parameter search. Its values were fixed in code before these experiments, but they were informally chosen by the authors with knowledge of earlier fitted policies, and they resemble typical CEM optima. The reference is therefore fixed but not hindsight-free: it is a \\emph{conservative} comparator that biases the study against finding a CEM improvement, while its own absolute performance overstates what an uninformed operator would have achieved. A \\emph{fully permissive} variant trades every eligible candidate at its first $0.55$ cross (its execution rules share the same informal provenance)."),
("Table~\\ref{tab:cemmain} is the main comparison. The fixed default policy --- with no optimization at all --- historically outperformed both benchmarks: excess $+13.21$ points on SPY and $+5.22$ on QQQ.",
 "Table~\\ref{tab:cemmain} is the main comparison. The fixed default policy --- with no parameter search --- historically outperformed both benchmarks: excess $+13.21$ points on SPY and $+5.22$ on QQQ (recalling its informal provenance, this is not evidence that an uninformed rule set would have done the same)."),
("Selectivity as such is therefore the largest single contribution; optimization adds a moderate, seed-consistent increment on top of it.",
 "Selectivity as such is therefore the largest single contribution; explicit optimization adds a moderate, seed-consistent increment on top of a reference that was already informally informed. Given these results we treat standard CEM as the paper's primary configuration --- on protocol grounds (the simplest leakage-free deployment), not because it won this short test, which by itself cannot justify selecting it."),

# ── Fold comparison: all-treatment arm shows the same profile ────────────────
("Walk-forward evaluation still contains a training history and a subsequent evaluation block; it does not eliminate the training period.",
 "Walk-forward evaluation still contains a training history and a subsequent evaluation block; it does not eliminate the training period. The all-treatment arm shows nearly the same fold profile (fold-excess correlation $0.86$ across the ten fold--benchmark cells; SPY folds 4--5: $+9.49$ and $+12.63$; QQQ fold 4: $-3.26$), so the late-fold strength reflects the 2026 candidate stream and regime, not any treatment."),

# ── RL: author-disclosed exploratory experiments, artifacts not retained ─────
("We did not evaluate higher-capacity sequential policies in this study; with roughly five hundred label-complete training candidates, a low-capacity interpretable policy class is the design-appropriate choice, and whether reinforcement-learning agents become viable as the universe of relevant, label-complete questions grows is a testable future research question, not an established fact.",
 "Exploratory PPO reinforcement-learning experiments conducted during development did not learn a stable replacement for the low-dimensional CEM policy: across the runs attempted, the agent failed to produce consistently reliable entry, exit, stop-loss, sizing, and portfolio decisions. The limited number of high-quality, label-complete event candidates (roughly five hundred for training) appears insufficient for estimating a high-capacity sequential policy, although those experiments cannot prove that sample size is the sole cause. Their artifacts were not retained in the cleaned repository, so we report this as preliminary negative evidence rather than a formal benchmark; it favors a low-capacity, interpretable policy for the present dataset, not a general claim that reinforcement learning is unsuitable for prediction-market trading. Whether such agents become viable as the universe of relevant, label-complete questions grows is a testable future research question."),

# ── Limitations: add reference provenance ────────────────────────────────────
("The random-selection test bundles gate timing with selection and does not equalize executed trade counts.",
 "The random-selection test bundles gate timing with selection and does not equalize executed trade counts. The fixed reference policy is pre-specified but informally informed by earlier fitted policies, and the exploratory RL evidence is unverifiable because its artifacts were not retained."),
]
for old, new in subs:
    assert old in s, "NOT FOUND: " + old[:80]
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("applied", len(subs), "revisions")

# Table renames.
t = (HERE / "table_cem_main.tex").read_text(encoding="utf-8")
t = t.replace("Frozen CEM baseline", "Standard CEM (frozen fit)")
(HERE / "table_cem_main.tex").write_text(t, encoding="utf-8")

b = (HERE / "table_budget.tex").read_text(encoding="utf-8")
b = b.replace("Flagship T1+T2+T3+T4 distribution", "All-treatment arm (T1+T2+T3+T4) distribution")
(HERE / "table_budget.tex").write_text(b, encoding="utf-8")
print("tables renamed")
