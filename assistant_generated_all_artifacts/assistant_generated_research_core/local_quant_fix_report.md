# Local corrected-execution optimization study

This study uses only the files uploaded to `/mnt/data`. No repository code was changed.

## Changes tested

1. Removed the probability-surge veto from policy search and execution.
2. Recomputed price run-up at the actual equity entry close.
3. Reparameterized sizing as gross event exposure divided by maximum concurrent positions.
4. Capped gross event exposure at 95% and rejected positions receiving less than 90% of their target allocation.
5. Replaced the absolute-return CEM reward with a benchmark-relative objective based on active daily log returns, chronological-block Information Ratios, active drawdown, and terminal relative return.
6. Tested corrected baseline exits, hard maximum-loss caps, no-follow-through exits, and both rules together.
7. Compared equal-budget Sobol and CEM searches across four independent runs, with all policy and exit selection performed on training data only.

## Four-run robustness results

Mean OOS excess return across four independent training-selected policies:

| Benchmark | Baseline | Hard cap | No follow | Combined |
|---|---:|---:|---:|---:|
| SPY | -1.41 pp | **+0.72 pp** | -1.52 pp | -0.53 pp |
| QQQ | -8.46 pp | **-5.86 pp** | -8.46 pp | -5.86 pp |

The hard cap improved SPY in three of four runs and QQQ in all four runs. No-follow-through was inactive or immaterial in nearly every OOS replay.

## Consensus-policy validation

A componentwise median policy was constructed from the four independently selected training policies. Exit parameters were then selected on training data only and replayed once OOS.

| Benchmark | Exit rule | Strategy | Benchmark | Excess | Active IR | Active max DD | Portfolio max DD |
|---|---|---:|---:|---:|---:|---:|---:|
| SPY | Corrected baseline | 8.24% | 8.52% | -0.28 pp | -0.04 | -8.31% | -11.33% |
| SPY | **6% hard cap** | **10.66%** | 8.52% | **+2.14 pp** | **0.33** | **-6.36%** | **-8.70%** |
| QQQ | Corrected baseline | 8.17% | 17.59% | -9.42 pp | -1.41 | -13.57% | -14.46% |
| QQQ | **8% hard cap** | **11.19%** | 17.59% | **-6.40 pp** | **-0.96** | -13.54% | **-12.27%** |

The no-follow and combined variants reproduced the baseline and hard-cap results respectively because the no-follow condition almost never became binding.

## Exit diagnosis

The hard cap is not profitable by itself. Its exits are losses. It improves the portfolio by truncating losses before the endpoint, freeing capacity, and changing the later set of executed trades.

For the SPY consensus policy, resolution-minus-one-day losses fell from approximately -$16.23k to -$3.80k. For QQQ, they fell from approximately -$13.74k to -$6.22k. More positions subsequently reached profitable lock exits. This was sufficient for SPY to beat its benchmark, but not for QQQ.

The no-follow rule selected ten trading days and a 1-3% MFE threshold. It triggered only about one OOS position in the consensus replays, so it did not address the losing endpoint bucket. It should not be retained in the current specification.

## Optimizer comparison

Equal-budget comparison across four runs:

| Benchmark | Method | Mean OOS excess | Mean OOS active IR | OOS-score dispersion |
|---|---|---:|---:|---:|
| SPY | Sobol | -2.25 pp | -0.32 | lower |
| SPY | CEM | -2.31 pp | -0.35 | higher |
| QQQ | Sobol | -8.89 pp | -1.27 | lower |
| QQQ | CEM | -10.44 pp | -1.46 | higher |

CEM was not a consistent winner. On QQQ it was less stable and worse on average. The search surface is discontinuous because policy changes alter entries, capacity conflicts, stop events, and whole-share allocations. CEM remains useful as a local distributional refiner, but it should not be the sole global optimizer.

## Recommended final search design

1. Keep probability surge removed.
2. Keep the 95% gross-exposure constraint and minimum 90% target-fill rule.
3. Keep the benchmark-relative objective, but simplify it before the final run to avoid tuning the objective itself excessively. Use median chronological-fold active IR, a worst-fold penalty, and active drawdown.
4. Fix `hold_days=1`; every robustly selected policy chose one day. This reduces search dimensionality.
5. Use a global scrambled Sobol screen of 256-512 feasible policies per benchmark.
6. Refine the top 10-15% with CEM using population 64, four or five iterations, three independent seeds, and a minimum sampling standard deviation.
7. Select a consensus policy using median fold rank or median parameters across seeds; do not select the single best training seed.
8. Retain only the hard-loss cap as the new exit component: approximately 6% for SPY and 8% for QQQ are the training-selected consensus values. Treat these values as provisional until confirmed on future unseen data.
9. Drop no-follow-through for now.

## Final conclusion

The structural changes were correct and materially improved the honesty and stability of the backtest. The strongest new finding is the hard-loss cap. It converts the corrected SPY strategy from approximately benchmark-flat to +2.14 percentage points of OOS excess in the consensus replay and reduces drawdown. It improves QQQ consistently but does not repair it; QQQ still materially underperforms its benchmark.

More CEM iterations alone are not the solution. The appropriate optimizer is a Sobol-plus-CEM hybrid with multiple seeds and consensus selection. The existing OOS period has now been used for exploratory model comparison and should be treated as validation rather than a pristine final holdout.
