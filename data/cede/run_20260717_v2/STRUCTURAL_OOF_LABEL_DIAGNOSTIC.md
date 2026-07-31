# CEDE structural-gate OOF label diagnostic

This is **not a CEDE performance replay**. It joins the fixed, timestamp-safe CEDE structural gate to the existing Stage 2F full-path research labels. Those labels use legacy SPY/QQQ active returns and legal-oracle outcomes, not CEDE's sector-hedged entry/exit execution.

## What it can say

- Whether the new probability-price dislocation gate is descriptively enriched for old legal opportunities.
- Whether the few selected events are concentrated in one chronological fold.

## What it cannot say

- That CEDE has a validated live expected-return model.
- That the selected group has a profitable CEDE portfolio replay or beats SPY/QQQ after the new exits.

## Summary

```csv
benchmark,cohort,rows,economic_events,profitable_share,never_profitable_share,persistent_loser_share,mean_best_legal_active_pct,median_best_legal_active_pct,mean_terminal_active_pct,median_terminal_active_pct,mean_active_mae_pct
QQQ,CEDE_structural_gate,7,7,1.0,0.0,0.14285714285714285,3.3661012181719605,3.4688782260883326,0.8567545516963214,0.884336708597161,-1.4064192649075693
QQQ,canonical_covered_calibrated_population,67,67,0.6119402985074627,0.3880597014925373,0.3582089552238806,1.9118034027909483,1.1003095615367915,-0.6803371058797315,-1.0140774115175497,-2.7483528705524938
QQQ,pre_dislocation_comparator,49,49,0.8163265306122449,0.1836734693877551,0.22448979591836735,3.193834748486625,2.3517285299790767,1.3385657541116576,1.8822187278652405,-1.7097967967662024
SPY,CEDE_structural_gate,12,12,0.9166666666666666,0.08333333333333333,0.16666666666666666,2.0906325235043344,2.0774644539911074,0.34585127433579066,0.603116098637078,-1.3904302646759206
SPY,canonical_covered_calibrated_population,132,132,0.6287878787878788,0.3712121212121212,0.4090909090909091,1.6226043802973629,0.9024569691241635,-1.0493068239478827,-1.0860754046276693,-2.766089852391925
SPY,pre_dislocation_comparator,86,86,0.7558139534883721,0.2441860465116279,0.2441860465116279,2.8569937552149898,1.7428752846749778,1.1550664272677866,0.868601100153353,-1.7955885373937692
```

## Selected-cohort fold results

```csv
benchmark,outer_fold,rows,economic_events,profitable_share,persistent_loser_share,mean_terminal_active_pct,median_terminal_active_pct
QQQ,0,3,3,1.0,0.3333333333333333,-2.477982215276444,-1.382989841797826
QQQ,2,3,3,1.0,0.0,2.3532212366118737,2.8057730477183527
QQQ,4,1,1,1.0,0.0,6.371564797867963,6.371564797867963
SPY,0,3,3,1.0,0.3333333333333333,-1.4536472061673067,-0.1135834806560142
SPY,1,1,1,1.0,0.0,2.535814071105418,2.535814071105418
SPY,2,7,7,0.8571428571428571,0.14285714285714285,0.18892347407038804,0.4050250858782013
SPY,4,1,1,1.0,0.0,4.652878520933274,4.652878520933274
```
