# Stage 2B Selection Research

## Required corrections

- Terminal semantics are enforced throughout the corrected simulator and generated artifacts: `T_e` is a scheduled event/resolution timestamp, never an exit; every checked outcome satisfies `te1_exit_date < t_e` and every replay trade asserts `exit_date < candidate_t_e`.
- The prior ranking diagnostic is explicitly labeled `frozen_capacity_same_day_ranking_evaluation`. It is preserved as a diagnostic and is not presented as an exact dynamic portfolio backtest.
- Pairwise reporting is separated. The older result is `pairwise_v1`; the corrected result is `pairwise_v2`. In the exploratory 2026 frozen-capacity comparison, V1 selected mean Target A is 0.462055% and V2 is 0.126641%; V2 is preserved as a negative baseline and its score is not reversed.
- Pairwise V2 orientation tests, preprocessing exports, fold coefficients, fold calibration, score deciles, extremes, and contribution exports are preserved. The connection selector uses descending connection strength, an explicit tie-breaker, and a deterministic final key independent of source row order.
- The connection tie-breaker choice is made from chronological training OOF: `expected_slot_days`. The 1,000-seed random distribution is retained as a diagnostic; the current 2026 block is exploratory only and did not choose any feature, model, threshold, tie-breaker, or architecture.
- Admission score propagation and portfolio-state context were added to the exact engine. The context now exposes free slots, gross/benchmark exposure, sector/event exposure, expected remaining slot-days, recent pressure, and current drawdown without changing the frozen execution policy.

## New experiments

- Small trained models were completed: a monotonic additive ranker with only economically justified hard constraints, and a small pooled-set MLP with candidate, same-day mean/max, and compact state inputs. No raw symbol IDs, raw event IDs, attention, or large embeddings were used.
- Exact dynamic replay was run per selector with separate portfolio paths. A leakage-controlled replay was also run on the intersection of chronological training OOF validation groups. Mean excess return across SPY/QQQ in that OOF replay was 2.7149% for connection + OOF tie-breaker with always-fill, 3.0408% with the frozen OOF admission choice, 1.5802% for the monotonic additive model, and 1.1986% for the pooled model.
- Sequential admission was implemented as separate accept/reject/stop-capable policy callbacks. Transparent train-OOF families included always-fill, minimum connection strength, minimum predicted Target A, minimum predicted Target B per slot-day, and minimum predicted Target B per square-root slot-day. The initial selected OOF choice is `min_connection_strength` at threshold `1.0`.
- Target A remains same-horizon active return. Target B was reported using both predeclared capital-time variants. Target C was built from genuine `max_concurrent` blocks by exact force-and-continue replay under connection and legacy continuation policies; it is labeled `policy_conditioned_counterfactual_value`, not absolute ground truth. Its sign is not stable across benchmark/continuation combinations.
- A timestamp-safe feature table was created for stock/sector 20-day and 5-day returns, relative extension ranks/z-scores, expected slot-days, and historical candidate-arrival pressure. Supporting-market snapshots and five-day probability changes remain null and are explicitly audited as unavailable because no verified map joins hashed probability keys to the collapsed numeric market IDs.
- Capacity robustness was evaluated at capacities 8, 10, and 12, random legal tie allocation was evaluated across 20 seeds, largest-winner removal was exported, and staged state ablations were run on OOF data. Free-slot and exposure fields are not silently fabricated when absent from a source table.

## Decision after results

- The currently strongest defensible modular selector is connection strength descending + the OOF-selected `expected_slot_days` tie-breaker + the OOF-selected `min_connection_strength` admission threshold. This is an OOF-based provisional decision, not a choice made from the 2026 test block.
- The decision is not yet a final production freeze: a genuinely later lockbox period is still missing, and the target-C continuation diagnostic is not uniformly positive. The final selector decision is therefore recorded as `provisional_oof_winner_pending_later_lockbox`.
- Stage 2B is not complete for production promotion. The exact corrected replay, admission layer, feature audit, OOF model replay, and robustness artifacts are complete; final lockbox validation and any remaining subgroup/stress expansion remain before a definitive freeze.
- Exit research and joint RL should not begin yet. Once the later lockbox is opened, no new features, thresholds, architectures, tie-breakers, or score polarity changes should be made; and `T_e` must remain excluded as an exit date forever.

Key outputs: `pairwise_v1_v2_comparison.csv`, `dynamic_replay/exact_replay_summary.csv`, `dynamic_replay_oof/exact_oof_replay_summary.csv`, `admission/admission_choices.json`, `target_diagnostics/target_ab_comparison.csv`, `target_c_counterfactuals/target_c_policy_conditioned_counterfactuals.csv`, `feature_table/feature_manifest.json`, and `robustness/capacity_robustness.csv`.
