from pathlib import Path

HERE = Path(__file__).resolve().parent

# Fold table: smaller font.
t = (HERE / "table_folds.tex").read_text(encoding="utf-8")
t = t.replace("\\footnotesize", "\\scriptsize").replace("{3.2pt}", "{2.6pt}")
(HERE / "table_folds.tex").write_text(t, encoding="utf-8")

p = HERE / "paper_final_t4_test.tex"
s = p.read_text(encoding="utf-8")

subs = [
# Discussion P2: tighten.
("The experiments also bound the claim. The optimizer's improvement is modest ($\\approx+3$ points median) relative to seed dispersion; its specific candidate choices are not statistically distinguishable from arbitrary choices within the same screened pool (86th percentile SPY, 37th QQQ); optimized selection and optimized execution are substitutes rather than complements; fold-level results are regime-dependent with unstable fitted parameters; none of the four treatments adds terminal value across seeds end to end --- half-Kelly sizing consistently subtracts on SPY; and the matched allocator experiment shows T4's priority ranking beats FIFO without demonstrably beating random ordering of the same candidates. Not all Polymarket questions carry useful equity information: earnings-linked candidates are close to chance on average, and the exploitable component concentrates in particular geopolitical, oil-related, and calendar regimes. The relevant research object is the continuous crowd-implied probability stream --- level, direction, revisions, deadline proximity --- rather than the final binary contract outcome.",
 "The experiments also bound the claim. The optimizer's improvement is modest ($\\approx+3$ points median) relative to seed dispersion; its candidate choices are not distinguishable from arbitrary choices within the same screened pool (86th percentile SPY, 37th QQQ); optimized selection and execution are substitutes; fold-level results are regime-dependent with unstable fitted parameters; no treatment adds terminal value end to end (half-Kelly consistently subtracts on SPY); and T4's ranking beats FIFO without demonstrably beating random ordering. Not all Polymarket questions carry useful equity information: earnings-linked candidates are near chance, and the exploitable component concentrates in geopolitical, oil-related, and calendar regimes. The relevant research object is the continuous crowd-implied probability stream --- level, direction, revisions, deadline proximity --- rather than the final binary outcome."),
# T4 subsection: tighten two clauses.
(r"A matched allocator experiment isolates T4 itself: per seed, the fitted all-treatment schedule, frozen Kelly history, eligibility, exits, sizing, capacity, and costs are held fixed, and only the ordering of contemporaneously competing candidates varies (T4, FIFO, or 1,000 seeded random orderings, each a full chronological simulation).",
 r"A matched allocator experiment isolates T4 itself: per seed, the fitted all-treatment schedule, frozen Kelly history, eligibility, exits, sizing, capacity, and costs are held fixed; only the ordering of contemporaneously competing candidates varies (T4, FIFO, or 1,000 seeded random orderings, each a full chronological simulation)."),
(r"Retrospective contested-decision diagnostics agree: T4-selected candidates are no better than FIFO-selected or rejected alternatives (differences $\approx-0.1$ to $-0.2\%$ per contested date, date- and event-clustered intervals crossing zero), and T4 holds a mid-pack mean rank among ${\sim}7$ contemporaneous alternatives.",
 r"Retrospective contested-decision diagnostics agree: T4-selected candidates are no better than FIFO-selected or rejected alternatives (date- and event-clustered intervals crossing zero) and hold a mid-pack mean rank among ${\sim}7$ contemporaneous alternatives."),
]
for old, new in subs:
    assert old in s, "NOT FOUND: " + old[:70]
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("applied", len(subs))
