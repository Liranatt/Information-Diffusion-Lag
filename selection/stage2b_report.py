"""Write the versioned Stage 2B report and final decision manifest."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / "data" / "selection_stage2b"


def _mean_excess(frame: pd.DataFrame, selector: str, admission: str) -> float:
    rows = frame[(frame["selector"].eq(selector)) & (frame["admission_policy"].eq(admission))]
    return float(rows["excess_return"].mean()) if not rows.empty else float("nan")


def write_stage2b_report(output_dir: Path | str = OUTPUT) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = pd.read_csv(output_dir / "target_diagnostics" / "target_ab_comparison.csv")
    v1v2 = target[target["selector"].isin(["pairwise_v1", "pairwise_v2"])].copy()
    v1v2["implementation_label"] = v1v2["selector"].map({"pairwise_v1": "pairwise_v1_older_implementation", "pairwise_v2": "pairwise_v2_corrected_negative_baseline"})
    v1v2["test_is_exploratory"] = v1v2["analysis_split"].eq("test")
    v1v2_path = output_dir / "pairwise_v1_v2_comparison.csv"
    v1v2.to_csv(v1v2_path, index=False)
    exact_oof = pd.read_csv(output_dir / "dynamic_replay_oof" / "exact_oof_replay_summary.csv")
    exact = pd.read_csv(output_dir / "dynamic_replay" / "exact_replay_summary.csv")
    admission_choices = json.loads((output_dir / "admission" / "admission_choices.json").read_text(encoding="utf-8"))
    selected_choice = admission_choices["selected_for_initial_dynamic_replay"]
    connection_oof_mean = _mean_excess(exact_oof, "connection_oof_tiebreaker", "always_fill")
    admitted_oof_mean = _mean_excess(exact_oof, "connection_oof_tiebreaker", str(selected_choice["policy"]))
    mono_oof_mean = _mean_excess(exact_oof, "monotonic_additive", "always_fill")
    pooled_oof_mean = _mean_excess(exact_oof, "pooled_set", "always_fill")
    test_connection = exact[(exact["selector"].eq("connection_oof_tiebreaker")) & (exact["analysis_split"].eq("test"))]
    test_admission = exact[(exact["selector"].eq("connection_oof_tiebreaker")) & (exact["admission_policy"].eq(str(selected_choice["policy"]))) & (exact["analysis_split"].eq("test"))]
    target_c = pd.read_csv(output_dir / "target_c_counterfactuals" / "target_c_summary.csv")
    tie = pd.read_csv(output_dir / "connection_tiebreakers" / "connection_tiebreaker_oof_summary.csv")
    v1_test = v1v2[(v1v2["selector"].eq("pairwise_v1")) & (v1v2["analysis_split"].eq("test"))]
    v2_test = v1v2[(v1v2["selector"].eq("pairwise_v2")) & (v1v2["analysis_split"].eq("test"))]
    v1_mean = float(v1_test["mean_target_a_pct"].iloc[0]) if not v1_test.empty else float("nan")
    v2_mean = float(v2_test["mean_target_a_pct"].iloc[0]) if not v2_test.empty else float("nan")
    report_path = output_dir / "stage2b_report.md"
    report = f"""# Stage 2B Selection Research

## Required corrections

- Terminal semantics are enforced throughout the corrected simulator and generated artifacts: `T_e` is a scheduled event/resolution timestamp, never an exit; every checked outcome satisfies `te1_exit_date < t_e` and every replay trade asserts `exit_date < candidate_t_e`.
- The prior ranking diagnostic is explicitly labeled `frozen_capacity_same_day_ranking_evaluation`. It is preserved as a diagnostic and is not presented as an exact dynamic portfolio backtest.
- Pairwise reporting is separated. The older result is `pairwise_v1`; the corrected result is `pairwise_v2`. In the exploratory 2026 frozen-capacity comparison, V1 selected mean Target A is {v1_mean:.6f}% and V2 is {v2_mean:.6f}%; V2 is preserved as a negative baseline and its score is not reversed.
- Pairwise V2 orientation tests, preprocessing exports, fold coefficients, fold calibration, score deciles, extremes, and contribution exports are preserved. The connection selector uses descending connection strength, an explicit tie-breaker, and a deterministic final key independent of source row order.
- The connection tie-breaker choice is made from chronological training OOF: `expected_slot_days`. The 1,000-seed random distribution is retained as a diagnostic; the current 2026 block is exploratory only and did not choose any feature, model, threshold, tie-breaker, or architecture.
- Admission score propagation and portfolio-state context were added to the exact engine. The context now exposes free slots, gross/benchmark exposure, sector/event exposure, expected remaining slot-days, recent pressure, and current drawdown without changing the frozen execution policy.

## New experiments

- Small trained models were completed: a monotonic additive ranker with only economically justified hard constraints, and a small pooled-set MLP with candidate, same-day mean/max, and compact state inputs. No raw symbol IDs, raw event IDs, attention, or large embeddings were used.
- Exact dynamic replay was run per selector with separate portfolio paths. A leakage-controlled replay was also run on the intersection of chronological training OOF validation groups. Mean excess return across SPY/QQQ in that OOF replay was {connection_oof_mean:.4f}% for connection + OOF tie-breaker with always-fill, {admitted_oof_mean:.4f}% with the frozen OOF admission choice, {mono_oof_mean:.4f}% for the monotonic additive model, and {pooled_oof_mean:.4f}% for the pooled model.
- Sequential admission was implemented as separate accept/reject/stop-capable policy callbacks. Transparent train-OOF families included always-fill, minimum connection strength, minimum predicted Target A, minimum predicted Target B per slot-day, and minimum predicted Target B per square-root slot-day. The initial selected OOF choice is `{selected_choice['policy']}` at threshold `{selected_choice.get('threshold')}`.
- Target A remains same-horizon active return. Target B was reported using both predeclared capital-time variants. Target C was built from genuine `max_concurrent` blocks by exact force-and-continue replay under connection and legacy continuation policies; it is labeled `policy_conditioned_counterfactual_value`, not absolute ground truth. Its sign is not stable across benchmark/continuation combinations.
- A timestamp-safe feature table was created for stock/sector 20-day and 5-day returns, relative extension ranks/z-scores, expected slot-days, and historical candidate-arrival pressure. Supporting-market snapshots and five-day probability changes remain null and are explicitly audited as unavailable because no verified map joins hashed probability keys to the collapsed numeric market IDs.
- Capacity robustness was evaluated at capacities 8, 10, and 12, random legal tie allocation was evaluated across 20 seeds, largest-winner removal was exported, and staged state ablations were run on OOF data. Free-slot and exposure fields are not silently fabricated when absent from a source table.

## Decision after results

- The currently strongest defensible modular selector is connection strength descending + the OOF-selected `expected_slot_days` tie-breaker + the OOF-selected `{selected_choice['policy']}` admission threshold. This is an OOF-based provisional decision, not a choice made from the 2026 test block.
- The decision is not yet a final production freeze: a genuinely later lockbox period is still missing, and the target-C continuation diagnostic is not uniformly positive. The final selector decision is therefore recorded as `provisional_oof_winner_pending_later_lockbox`.
- Stage 2B is not complete for production promotion. The exact corrected replay, admission layer, feature audit, OOF model replay, and robustness artifacts are complete; final lockbox validation and any remaining subgroup/stress expansion remain before a definitive freeze.
- Exit research and joint RL should not begin yet. Once the later lockbox is opened, no new features, thresholds, architectures, tie-breakers, or score polarity changes should be made; and `T_e` must remain excluded as an exit date forever.

Key outputs: `pairwise_v1_v2_comparison.csv`, `dynamic_replay/exact_replay_summary.csv`, `dynamic_replay_oof/exact_oof_replay_summary.csv`, `admission/admission_choices.json`, `target_diagnostics/target_ab_comparison.csv`, `target_c_counterfactuals/target_c_policy_conditioned_counterfactuals.csv`, `feature_table/feature_manifest.json`, and `robustness/capacity_robustness.csv`.
"""
    report_path.write_text(report, encoding="utf-8")
    decision = {
        "label": "provisional_oof_winner_pending_later_lockbox",
        "ranker": "connection_strength_descending",
        "tie_breaker": "expected_slot_days",
        "admission_policy": selected_choice["policy"],
        "admission_threshold": selected_choice.get("threshold"),
        "selection_source": "chronological_training_oof_exact_replay",
        "current_2026_test_used_for_choice": False,
        "stage2b_complete": False,
        "selector_frozen_for_production": False,
        "exit_research_may_begin": False,
        "oof_mean_excess": {
            "connection_always_fill": connection_oof_mean,
            "connection_with_admission": admitted_oof_mean,
            "monotonic_additive": mono_oof_mean,
            "pooled_set": pooled_oof_mean,
        },
        "te_is_never_exit": True,
    }
    decision_path = output_dir / "final_selector_decision.json"
    decision_path.write_text(json.dumps(decision, indent=2) + "\n", encoding="utf-8")
    return {"report": report_path, "comparison": v1v2_path, "decision": decision_path}


if __name__ == "__main__":
    for name, path in write_stage2b_report().items():
        print(f"{name}: {path}")
