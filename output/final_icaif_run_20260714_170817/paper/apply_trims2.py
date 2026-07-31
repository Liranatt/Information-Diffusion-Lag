from pathlib import Path

p = Path(__file__).resolve().parent / "paper_final_revised.tex"
s = p.read_text(encoding="utf-8")

subs = [
# Inline the H1 hypothesis display (saves ~4 lines).
(r"""The primary hypothesis is
\[
H_1:\quad
\mu_{\mathcal O}=\mathbb{E}\!\left[r^{net}_i\mid i\in\mathcal O_{clean}\right] > 0.
\]
The expectation is reported at three levels.""",
 r"""The primary hypothesis is $H_1:\ \mu_{\mathcal O}=\mathbb{E}[r^{net}_i\mid i\in\mathcal O_{clean}]>0$. The expectation is reported at three levels."""),
# Trim inference paragraph tail.
("We also report medians and positive-return frequencies because the mean can be positive in a right-skewed distribution even when close to half of observations win.",
 "Medians and positive-return frequencies are reported because a right-skewed mean can be positive when barely half of observations win."),
# Trim Discussion paragraph 1 (redundant with Section 5.4).
("The primary empirical result is a positive observed conditional mean in the cleaned Polymarket-identified opportunity set. The candidate estimate is economically large relative to modeled cost and remains positive after event-cluster resampling. The same data do not establish a positive population expectation under every reasonable dependence model: symbol, week, and month clustering cross zero, the equal-event month-block interval crosses zero, and the event estimate is highly sensitive to removal of the upper tail. Most importantly, no benchmark-relative interval excludes zero, so the market-adjusted component of the headline is not statistically established. The appropriate conclusion is a positive historical conditional expectation with limited effective sample size and substantial concentration, not a universal return law and not demonstrated market-relative excess.",
 "The primary empirical result is a positive observed conditional mean in the cleaned opportunity set, economically large relative to modeled cost and stable under event-cluster resampling. It is not established under every reasonable dependence model (symbol, week, and month clustering cross zero, as does the equal-event month block), it is upper-tail dependent, and no benchmark-relative interval excludes zero. The appropriate conclusion is a positive historical conditional expectation with limited effective sample size and substantial concentration, not a universal return law and not demonstrated market-relative excess."),
# Trim polarity/cleaning paragraph tail.
("Polarity and cleaning serve different scientific roles. Polarity makes the signal economically interpretable; its value cannot be judged only by whether the historical mean rises. Cleaning defines the estimand by removing duplicates and invalid mappings. The matched CEM comparison is directionally favorable to cleaning but statistically inconclusive. Future ingestion should apply the same rules prospectively, with immutable candidate versions and explicit event identifiers.",
 "Polarity and cleaning serve different scientific roles: polarity makes the signal economically interpretable (its value is not judged by whether the historical mean rises), while cleaning defines the estimand. The matched CEM comparison is directionally favorable to cleaning but statistically inconclusive."),
# Small bibliography.
(r"\begin{thebibliography}{9}",
 "{\\small\n\\begin{thebibliography}{9}"),
(r"\end{thebibliography}",
 "\\end{thebibliography}\n}"),
]
for old, new in subs:
    assert old in s, "NOT FOUND: " + old[:70]
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("applied", len(subs), "trims")
