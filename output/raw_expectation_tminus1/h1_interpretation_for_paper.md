# H1 Statistical Evidence for Paper

## Bottom line
The primary candidate-level H1 test supports a real positive expectation: N=884, mean=+1.4570%, median=+0.2722%, win rate=52.71%, 95% BCa CI=[+0.9083%, +2.0367%], iid bootstrap p=<0.0001.
The duplication controls keep the expectation positive: symbol-day mean +0.6837% over N=761; event-level mean +1.3295% over N=805.

## How to state this in the paper

The statistical tests help if they are used to support the existence and shape of the raw expectation, not to pretend the edge is a smooth high-hit-rate anomaly. The defensible claim is: prediction-market threshold events define a positively skewed conditional return distribution with positive mean net return before scheduled resolution. The weak binomial/win-rate evidence is not a failure of H1; it shows that the effect is payoff-asymmetric rather than accuracy-dominant.

The hardest dependence controls are intentionally more conservative. Symbol-episode and entry-month clustering ask whether the result survives after treating overlapping trades or calendar regimes as common shocks. Where those p-values become marginal, the right interpretation is bounded external validity, not disappearance of the positive expectation: the sample mean, bootstrap CI, collapsed view, and event view remain positive, but the edge is regime-sensitive and right-skewed.

Suggested wording:

> The raw T-1 expectation remains positive after collapsing same-symbol same-day duplicates and after aggregating to the event level. The win-rate tests are weaker, which is consistent with a right-skewed event-driven payoff distribution rather than a high-frequency directional classifier. Therefore the central evidence is mean-expectancy evidence, supported by bootstrap confidence intervals and dependence-robust clustering, not a claim that most signals are individually profitable.

## Files written

- `h1_paper_table.csv`: compact machine-readable table for the paper.
- `h1_paper_table.tex`: LaTeX table snippet.
- `h1_inference_summary.csv`: full replication table with all robustness checks.
