# 1. Required corrections

Stage 2B was not overwritten. Its selector is preserved as
`raw_global_connection_baseline`, and its raw value is exposed unchanged as
`legacy_gemini_relevance_score`. The old names `feat_connection_strength`,
`connection_strength`, and `relevance` remain documented compatibility
aliases only. The score is no longer described as universally calibrated
economic connection strength.

Semantic labels use only question text, asset/company identity, sector, and
known economic relationships. No return, post-entry price, selected outcome,
or portfolio result enters labeling. Ordinary same-company earnings and
verified company-owned FDA events are deterministic `direct_issuer`
mappings with maximal semantic directness.

| mapping_type | unique_question_symbol_mappings |
| --- | --- |
| direct_issuer | 288 |
| direct_underlying | 16 |
| first_order_sector | 8 |
| second_order_company | 5 |
| broad_macro_proxy | 0 |

All Stage 2C inputs are development rows (`analysis_split=train`). Test rows
read: 0. The later lockbox remains closed.

# 2. Raw score semantic audit

The audit contains 492 benchmark-specific
direct-earnings rows. 0 exact question-symbol pairs received
multiple legacy scores. No verified model/prompt batch ID exists, so batch
tables use the explicitly named UTC `t0`-date inferred generation batch.

Adjusted `score = 1.00` versus `< 1.00` results:

| benchmark | adjusted_coefficient_target_a_pct | cluster_bootstrap_ci_low | cluster_bootstrap_ci_high | observations |
| --- | --- | --- | --- | --- |
| QQQ | -0.6415 | -2.8110 | 0.8589 | 221.0000 |
| SPY | -0.1653 | -1.6375 | 2.2818 | 271.0000 |

The predeclared stability test requires a positive clustered 95% interval and
positive adjusted coefficients in at least four of five outer folds for both
SPY and QQQ. Result: `False`.
Any surviving association is labeled an LLM confidence/data-quality proxy,
not economic directness. Temporal calibration drift flag:
`False`.

# 3. Direct-event ablation

Full nested chronological exact replay reused the five Stage 2B earnings
outer folds. Inner folds alone selected the D-lane quality rule. No current
2026 exploratory test row was used.

| variant | benchmark | mean_excess_return | mean_active_ir | mean_active_drawdown_pct | mean_trade_count | mean_slot_usage_pct |
| --- | --- | --- | --- | --- | --- | --- |
| A_raw_global_connection_baseline | QQQ | 0.5026 | 0.4050 | -1.4096 | 16.2000 | 31.0828 |
| A_raw_global_connection_baseline | SPY | 0.7050 | 1.5147 | -1.1621 | 19.4000 | 34.2327 |
| B_deterministic_direct_always_fill | QQQ | 0.3137 | 0.2180 | -2.1220 | 23.8000 | 45.2763 |
| B_deterministic_direct_always_fill | SPY | -0.3715 | -0.2051 | -2.1387 | 25.2000 | 44.8131 |
| C_deterministic_direct_legacy_proxy_1.00 | QQQ | 0.5024 | 0.4050 | -1.4098 | 16.2000 | 31.0828 |
| C_deterministic_direct_legacy_proxy_1.00 | SPY | 0.7050 | 1.5147 | -1.1621 | 19.4000 | 34.2327 |
| D_deterministic_direct_trade_quality | QQQ | 0.1914 | 1.3553 | -1.1848 | 13.2000 | 29.0706 |
| D_deterministic_direct_trade_quality | SPY | 0.3149 | 1.7580 | -0.7685 | 13.0000 | 24.3563 |

The family-level predeclared direct quality rule is
`predicted_target_a_positive`. For interpretation, A→C isolates raw-score ranking
while holding the 1.00 proxy admission fixed; B→C isolates that admission
bucket under deterministic directness; B→D measures non-connection
trade-quality admission. SPY excess A/B/C/D is
0.7050/-0.3715/0.7050/0.3149;
QQQ is 0.5026/0.3137/0.5024/0.1914.

Probability-price disagreement was unavailable because no verified market-ID
map exists. Portfolio capacity state was enforced dynamically in every exact
admission decision; Target A is benchmark-active value and therefore embeds
benchmark opportunity cost.

# 4. Indirect-event mapping audit

The pre-lockbox sample contains 61
geo/macro candidate rows, 14
economic event episodes, and 7 assets. All rows
from one episode stay in one chronological fold. Results are exploratory and
event-cluster-aware; no grid of geopolitical thresholds was optimized.

| selector | benchmark | mean_excess_return | mean_active_ir | mean_active_drawdown_pct | mean_trade_count | mean_slot_usage_pct | mean_top_event_cluster_abs_pnl_share_pct |
| --- | --- | --- | --- | --- | --- | --- | --- |
| raw_global_connection_baseline | QQQ | -0.2551 | -0.9366 | -0.4209 | 0.6667 | 1.9091 | 66.6667 |
| raw_global_connection_baseline | SPY | -0.2581 | -0.3162 | -0.4127 | 0.6667 | 0.5798 | 66.6667 |
| semantic_mapping_selector | QQQ | -0.1577 | -4.3551 | -0.4953 | 1.6667 | 8.4848 | 82.3701 |
| semantic_mapping_selector | SPY | -0.4916 | -4.3495 | -0.9628 | 2.0000 | 9.1036 | 82.2295 |

Raw absolute score, event-relative raw rank, mapping-type priority, exposure
purity, direction confidence, source order, semantic full rank, and random
legal selection are exported trade by trade. USO, BNO, XLE, and individual
company results are reported separately. The key semantic test is whether a
defensible and purer within-event exposure is selected, not whether a raw
score predicts returns.

# 5. Family-aware exact replay

Direct and indirect candidates pass separate semantic lanes and then enter
one shared corrected capacity-constrained portfolio allocator. There is no
geopolitical quota and no unconditional geo priority. Direct events use no
raw connection threshold in the deterministic family selector. Indirect
events require mapping confidence ≥3 and are ranked within event by mapping
type, exposure purity, materiality, direction confidence, and slot duration.

| selector | benchmark | mean_excess_return | mean_active_ir | mean_active_drawdown_pct | mean_trade_count | mean_slot_usage_pct | mean_top_winner_concentration_pct | mean_top_event_cluster_abs_pnl_share_pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| raw_global_connection_baseline | SPY | 4.6088 | 1.3380 | -1.9313 | 98.0000 | 27.3529 | 6.1372 | 4.2352 |
| raw_global_connection_baseline | QQQ | 2.8767 | 0.7150 | -3.7034 | 82.0000 | 25.0370 | 7.9977 | 5.0463 |
| family_aware_selector | SPY | -1.0852 | -0.3261 | -3.8284 | 71.0000 | 21.0294 | 7.6779 | 5.3927 |
| family_aware_selector | QQQ | 0.6230 | 0.2211 | -2.6488 | 67.0000 | 18.9630 | 7.8737 | 4.3613 |
| hybrid_legacy_confidence_selector | SPY | 3.7947 | 1.0275 | -3.2931 | 102.0000 | 30.6618 | 5.9409 | 3.8388 |
| hybrid_legacy_confidence_selector | QQQ | 3.3876 | 0.8207 | -3.7288 | 84.0000 | 25.7778 | 7.7454 | 4.4009 |

Random legal allocator comparison:

| benchmark | family_aware_mean_excess_return | random_legal_exact_percentile | random_seeds |
| --- | --- | --- | --- |
| SPY | -1.0852 | 0.0000 | 20.0000 |
| QQQ | 0.6230 | 20.0000 | 20.0000 |

Exact added/removed trades, mapping-type trade counts and P&L, event-cluster
contribution, slot usage, and winner concentration are exported beside this
report.

# 6. Decision after results

Decision: **Case C**. Frozen selector:
`family_aware_selector`.

The raw score advantage was not stable after deterministic directness normalization and controls; it is removed from direct-event admission. Direct performance preserved within the
predeclared 0.25 percentage-point tolerance:
`False`. Global performance preserved:
`False`. Valid geo/macro eligibility
restored: `True`.

The prior `connection >= 1.00` improvement is classified as:

- genuine mapping quality: rejected for ordinary earnings; every mapping is already direct by construction
- LLM confidence/data-quality proxy: not validated; adjusted 95% intervals cross zero and fold signs are unstable for SPY and QQQ
- raw-score ranking: no material contribution; deterministic C minus raw-ranked A excess was +0.0000 for SPY and -0.0002 for QQQ
- admission bucket: the observed effect came from refusing score<1 rows; C minus always-fill B was +1.0765 for SPY and +0.1888 for QQQ
- duration/trade-quality effects: the non-connection D rule improved active IR and drawdown for both benchmarks but retained less excess return than A/C
- temporal calibration drift: not detected by the predeclared mean-drift flag
- sample-specific selection noise: the best-supported interpretation of the legacy 1.00 bucket because its return difference did not survive adjusted or fold-stability tests

The frozen selector cannot change during exit research. The legacy score's
new role is `diagnostic_only`.

This is not a Case A performance win: the corrected selector did not preserve
the raw baseline's SPY or global return within tolerance. Case C freezes the
semantic correction because the legacy bucket failed its predeclared
stability test, while retaining the performance shortfall as an explicit
research risk for Stage 3 and the final sealed evaluation.

# 7. Stage 3 handoff status

The original Stage 3 manifest and 184 trades are preserved under the Stage
2C baseline directory. No exit policy was trained or selected from that
sample during Stage 2C.

Stage 3 was rebuilt from the selected global Stage 2C outer-fold exact
replays: 138 development trades across
11 folds. Terminal Te−1 label coverage
is 100.00%; legal Te−1 date coverage is
100.00%.

All observed exits satisfy `exit_date < T_e`; `T_e` is never an exit and
`T_e - 1` remains the latest legal horizon. The later lockbox remains sealed
for one final evaluation of the complete frozen modular pipeline.
