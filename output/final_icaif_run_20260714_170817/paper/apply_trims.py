from pathlib import Path

p = Path(__file__).resolve().parent / "paper_final_revised.tex"
s = p.read_text(encoding="utf-8")

subs = [
(r"\includegraphics[width=0.86\textwidth]{fig_signal_window_v2.pdf}",
 r"\includegraphics[width=0.80\textwidth]{fig_signal_window_v2.pdf}"),
(r"\includegraphics[width=0.90\textwidth]{fig1_h1_evidence.pdf}",
 r"\includegraphics[width=0.84\textwidth]{fig1_h1_evidence.pdf}"),
(r"\includegraphics[width=0.90\textwidth]{fig2_benchmark_excess.pdf}",
 r"\includegraphics[width=0.84\textwidth]{fig2_benchmark_excess.pdf}"),
(r"\includegraphics[width=0.88\textwidth]{fig_strategy_architecture.pdf}",
 r"\includegraphics[width=0.80\textwidth]{fig_strategy_architecture.pdf}"),
(r"\includegraphics[width=0.76\textwidth]{fig3_cem_budget_robustness.pdf}",
 r"\includegraphics[width=0.70\textwidth]{fig3_cem_budget_robustness.pdf}"),
("The archive spans August 2024 through June 2026. Daily decisions are used because a complete historical hourly equity panel is not available for every symbol; the live implementation can update hourly when current data exist.",
 "The archive spans August 2024 through June 2026. Decisions are daily because a complete historical hourly equity panel is unavailable for every symbol."),
(r"Table~\ref{tab:cleaning} reconciles the data flow. The audit is targeted rather than universe-complete: 1,148 rows pass through deterministic rules without manual question-level review. This boundary is recorded because semantic validity, not only sample size, determines the credibility of the expectation estimate.",
 r"Table~\ref{tab:cleaning} reconciles the data flow. The audit is targeted rather than universe-complete: 1,148 rows pass through deterministic rules without manual question-level review, and this boundary is recorded explicitly."),
("For live use, the defensible role of Polymarket is a continuous confirmation variable: probability level, direction, revisions, and deadline proximity can inform eligibility and exits without assuming minute-level trading or a diffusion lag. The daily backtest cannot evaluate after-hours or weekend probability changes, and a prospective hourly implementation should preserve the same semantic rules while evaluating only data observable at each decision time.",
 "For live use, the defensible role of Polymarket is a continuous confirmation variable informing eligibility and exits; a prospective hourly implementation should preserve the same semantic rules and evaluate only data observable at each decision time."),
("The research contribution is therefore narrower and more defensible than information diffusion. Polymarket supplies continuous, timestamped crowd-belief variables that can identify event relevance, encode the favorable outcome through polarity, and confirm entry or exit conditions for selected equities. The positive conditional expectation shows that this information source is empirically useful in the observed sample. Establishing why it works, whether it beats simple market exposure, and whether it persists prospectively requires new events rather than additional optimization of the same history.",
 "The research contribution is therefore narrower and more defensible than information diffusion. Polymarket supplies continuous, timestamped crowd-belief variables that can identify event relevance, encode the favorable outcome through polarity, and confirm entry or exit conditions for selected equities. Establishing why the conditional return exists, whether it beats simple market exposure, and whether it persists prospectively requires new events rather than additional optimization of the same history."),
]
for old, new in subs:
    assert old in s, "NOT FOUND: " + old[:70]
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("applied", len(subs), "trims")
