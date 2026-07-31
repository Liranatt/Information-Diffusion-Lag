"""Write the final nested Stage 2B validation report."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT / "data" / "selection_stage2b" / "nested_final_validation"
STAGE3_OUTPUT = PROJECT / "data" / "stage3_exit_research"


def _fmt(value: object, digits: int = 3) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return ""


def _markdown_table(frame: pd.DataFrame, columns: list[str], headers: list[str] | None = None) -> str:
    headers = headers or columns
    rows = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for _, row in frame[columns].iterrows():
        rows.append("| " + " | ".join(str(row[column]) for column in columns) + " |")
    return "\n".join(rows)


def write_final_validation_report(output_dir: Path | str = DEFAULT_OUTPUT) -> dict[str, Path]:
    output_dir = Path(output_dir)
    folds = pd.read_csv(output_dir / "nested_outer_fold_manifest.csv")
    choices = pd.read_csv(output_dir / "nested_outer_choices.csv")
    factorial = pd.read_csv(output_dir / "factorial_ablation_summary.csv")
    stability = pd.read_csv(output_dir / "threshold_stability_summary.csv")
    frozen = json.loads((output_dir / "research_frozen_selector.json").read_text(encoding="utf-8"))
    feature_status = json.loads((output_dir / "feature_hypothesis_status.json").read_text(encoding="utf-8"))
    target_c_path = PROJECT / "data" / "selection_stage2b" / "target_c_counterfactuals" / "target_c_summary.csv"
    target_c = pd.read_csv(target_c_path) if target_c_path.exists() else pd.DataFrame()
    stage3_manifest = json.loads((STAGE3_OUTPUT / "stage3_exit_manifest.json").read_text(encoding="utf-8")) if (STAGE3_OUTPUT / "stage3_exit_manifest.json").exists() else {}
    fold_table = folds.copy()
    fold_table["validation_range"] = fold_table["outer_validation_start"].str[:10] + " to " + fold_table["outer_validation_end"].str[:10]
    fold_table["event_families"] = fold_table["outer_validation_event_family_composition"].str.replace('"', "'", regex=False)
    fold_table["validation_rows"] = fold_table["outer_validation_rows"].astype(int)
    choice_table = choices[["outer_fold", "tie_breaker", "admission_threshold_label", "inner_fold_mean_group_target_a_pct"]].copy()
    choice_table["inner_mean_target_a"] = choice_table["inner_fold_mean_group_target_a_pct"].map(lambda x: _fmt(x, 3))
    choice_table = choice_table.rename(columns={"admission_threshold_label": "threshold"})
    factorial_view = factorial.copy()
    for column in ("mean_excess_return", "mean_trade_count", "mean_active_information_ratio", "mean_active_max_drawdown_pct"):
        factorial_view[column] = factorial_view[column].map(lambda x: _fmt(x, 3))
    threshold_view = stability.copy()
    for column in ("mean_excess_return", "mean_trade_count", "mean_active_information_ratio", "mean_active_max_drawdown_pct"):
        threshold_view[column] = threshold_view[column].map(lambda x: _fmt(x, 3))
    threshold_view["threshold"] = threshold_view["admission_threshold_label"]
    target_c_note = "Target C output was not used for primary training or selector freezing; its policy-conditioned sign remains continuation-policy dependent."
    if not target_c.empty:
        target_c_note += f" The retained diagnostic contains {int(target_c['n_candidates'].sum())} continuation-policy/benchmark aggregates."
    report = f"""# Stage 2B Final Validation and Stage 3 Handoff

## Required corrections

- The previous intersection-based OOF replay has been replaced by full nested chronological outer evaluation on development/training rows. Inner folds select the tie-breaker and admission threshold; outer folds evaluate frozen choices.
- The later lockbox was not opened. Every nested artifact records `lockbox_opened=false` and `lockbox_rows_evaluated=0`.
- The factorial cells isolate ranking from admission: A/B compare source-order versus expected-slot-days with always-fill; C/D compare the same ranking choices with the 1.00 minimum connection threshold.
- Threshold stability is reported for exactly: no threshold, 0.90, 0.95, 0.98, and 1.00. SPY and QQQ are reported separately.
- Target C remains a diagnostic only. {target_c_note}
- The monotonic and pooled models were evaluated without verified probability-price disagreement or supporting-market snapshot features. Their underperformance does not reject those feature hypotheses; the hypotheses remain untested in this feature-complete form.
- All exact replay exits remain strictly before `T_e`; `T_e` is never an exit.

## Nested validation

Outer-fold date ranges and event-family composition:

{_markdown_table(fold_table, ["outer_fold", "validation_range", "validation_rows", "event_families"], ["Fold", "Validation range", "Rows", "Event-family composition"])}

Inner-fold choices:

{_markdown_table(choice_table, ["outer_fold", "tie_breaker", "threshold", "inner_mean_target_a"], ["Outer fold", "Tie-breaker", "Threshold", "Inner mean Target A"])}

Factorial ranking × admission results:

{_markdown_table(factorial_view, ["factorial_cell", "benchmark", "mean_excess_return", "mean_trade_count", "mean_active_information_ratio", "mean_active_max_drawdown_pct"], ["Cell", "Benchmark", "Mean excess", "Mean trades", "Active IR", "Active drawdown"])}

Threshold stability by benchmark:

{_markdown_table(threshold_view, ["tie_breaker", "threshold", "benchmark", "mean_excess_return", "mean_trade_count", "mean_active_information_ratio", "mean_active_max_drawdown_pct"], ["Tie-breaker", "Threshold", "Benchmark", "Mean excess", "Mean trades", "Active IR", "Active drawdown"])}

## Decision and Stage 3

The research-frozen selector is:

`connection_strength descending → {frozen['tie_breaker']} tie-breaker → minimum connection strength ≥ {frozen['admission_threshold_label']}`

It was chosen using the predeclared rule of maximizing the minimum SPY/QQQ outer-fold mean excess, with mean excess, active IR, and drawdown as tie-breakers. The nested result is SPY mean excess `{_fmt(frozen['spy_mean_excess_return'])}%`, QQQ mean excess `{_fmt(frozen['qqq_mean_excess_return'])}%`, and mean active drawdown `{_fmt(frozen['mean_active_max_drawdown_pct'])}%`.

The selector is now a `research_frozen_selector`: its ranking, tie-breaker, and admission threshold must not change during exit research. The lockbox remains reserved for one final evaluation of the full frozen modular pipeline.

Stage 3 has begun on the five outer development folds only. It contains `{stage3_manifest.get('development_trade_count', 0)}` frozen-selector development trades, has legal `T_e - 1` dates for `{_fmt(100.0 * stage3_manifest.get('legal_te1_date_coverage', 0.0), 1)}%` of them, maintains the `exit_date < T_e` guard, and has not trained an exit model or accessed the lockbox.

Key artifacts:

- `nested_outer_fold_manifest.csv`
- `nested_outer_choices.csv`
- `nested_outer_decisions.csv`
- `nested_outer_exact_replay_summary.csv`
- `factorial_ablation_summary.csv`
- `threshold_stability_summary.csv`
- `research_frozen_selector.json`
- `feature_hypothesis_status.json`
- `../stage3_exit_research/stage3_exit_development_trades.csv`
"""
    report_path = output_dir / "stage2b_final_validation_report.md"
    report_path.write_text(report, encoding="utf-8")
    return {"report": report_path, "frozen_selector": output_dir / "research_frozen_selector.json"}


if __name__ == "__main__":
    for name, path in write_final_validation_report().items():
        print(f"{name}: {path}")
