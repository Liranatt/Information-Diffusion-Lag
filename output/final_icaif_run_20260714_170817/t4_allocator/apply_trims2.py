from pathlib import Path

HERE = Path(__file__).resolve().parent
p = HERE / "paper_final_t4_test.tex"
s = p.read_text(encoding="utf-8")

subs = [
(r"\includegraphics[width=0.97\textwidth]{fig_cem_contribution.pdf}",
 r"\includegraphics[width=0.88\textwidth]{fig_cem_contribution.pdf}"),
(r"\includegraphics[width=0.84\textwidth]{fig1_h1_evidence.pdf}",
 r"\includegraphics[width=0.78\textwidth]{fig1_h1_evidence.pdf}"),
(r"\includegraphics[width=0.84\textwidth]{fig2_benchmark_excess.pdf}",
 r"\includegraphics[width=0.78\textwidth]{fig2_benchmark_excess.pdf}"),
(r"\includegraphics[width=0.80\textwidth]{fig_signal_window_v2.pdf}",
 r"\includegraphics[width=0.74\textwidth]{fig_signal_window_v2.pdf}"),
# Discussion P1: tighten.
("The two halves of the paper answer the central question jointly. The average candidate-level evidence is weak: intervals cross zero under coarse dependence, no benchmark-relative interval excludes zero, and the raw mean is concentrated in oil-linked assets and one month. Yet the matched portfolio experiments show that this weak average distribution still supported economically meaningful historical portfolios: every selective arm outperformed both benchmarks in most or all seeds, the ordering (no selection $<$ fixed selectivity $<$ optimized policy) is stable across seeds, and one standard low-dimensional CEM fit reliably improved on the fixed reference. This is precisely the distinction between a weak average population result and a potentially useful selective trading signal --- probability-triggered, selectively executed equity allocation rather than a universal return law.",
 "The two halves of the paper answer the central question jointly. The average candidate-level evidence is weak: intervals cross zero under coarse dependence, no benchmark-relative interval excludes zero, and the raw mean is concentrated in oil-linked assets and one month. Yet the matched portfolio experiments show this weak average distribution still supported economically meaningful historical portfolios: every selective arm outperformed both benchmarks in most or all seeds, the ordering (no selection $<$ fixed selectivity $<$ optimized policy) is stable across seeds, and one standard CEM fit reliably improved on the fixed reference --- the distinction between a weak average population result and a selectively useful, probability-triggered trading signal."),
# Conclusion final paragraph: tighten.
("These are historical statements about a short, repeatedly inspected window. No portfolio excess return is statistically established, the strongest raw evidence sits in oil-linked and calendar-specific regimes, earnings candidates are near chance, and prospective performance remains untested. What the study distinguishes is a weak average population result from a selectively useful crowd-probability stream --- and it shows that establishing the former is not required for the latter to be economically interesting. Whether the selective value persists, and whether larger universes of label-complete event candidates eventually support higher-capacity policies, are prospective questions that new events, not further optimization of this history, must answer.",
 "These are historical statements about a short, repeatedly inspected window. No portfolio excess return is statistically established, the strongest raw evidence sits in oil-linked and calendar-specific regimes, earnings candidates are near chance, and prospective performance remains untested. Whether the selective value persists, and whether larger universes of label-complete candidates eventually support higher-capacity policies, are prospective questions that new events, not further optimization of this history, must answer."),
]
for old, new in subs:
    assert old in s, "NOT FOUND: " + old[:70]
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("applied", len(subs))
