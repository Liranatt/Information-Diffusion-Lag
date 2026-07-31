"""Describe CEDE's fixed structural gate against existing earnings OOF labels.

This is intentionally a *diagnostic*, not a performance backtest.  The labels
are the old Stage 2F full-path research targets and use SPY/QQQ active-return
definitions, whereas CEDE's executable earnings hedge is the sector ETF and
has a different exit state machine.  The script makes that distinction
explicit so a promising small cohort cannot be mistaken for deployed alpha.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
RUN = PROJECT / "data" / "cede" / "run_20260717_v2"
EVENTS = RUN / "event_candidates_and_allocations.csv"
OOF = PROJECT / "data" / "selection_stage2f" / "nested_oof_models" / "stage2f_oof_predictions.csv"


def _structural_gate(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["coverage_sufficient"].fillna(False)
        & frame["calibration_available"].fillna(False)
        & frame["timestamp_safe"].fillna(False)
        & frame["event_probability"].ge(0.60)
        & frame["event_agreement"].ge(0.75)
        & frame["business_days_to_event_end"].between(2, 20)
        & frame["probability_update_ok"].fillna(False)
        & frame["dislocation_ok"].fillna(False)
        & frame["price_not_fully_repriced"].fillna(False)
    )


def _pre_dislocation_gate(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["coverage_sufficient"].fillna(False)
        & frame["calibration_available"].fillna(False)
        & frame["timestamp_safe"].fillna(False)
        & frame["event_probability"].ge(0.60)
        & frame["event_agreement"].ge(0.75)
        & frame["business_days_to_event_end"].between(2, 20)
    )


def _summary(frame: pd.DataFrame, cohort: str) -> dict[str, float | int | str]:
    return {
        "cohort": cohort,
        "rows": int(len(frame)),
        "economic_events": int(frame["trade_event_id"].nunique()),
        "profitable_share": float(frame["ever_profitable_after_costs"].mean()),
        "never_profitable_share": float(frame["never_profitable_after_costs"].mean()),
        "persistent_loser_share": float(frame["persistent_loser"].mean()),
        "mean_best_legal_active_pct": float(frame["best_legal_net_active_return_pct"].mean()),
        "median_best_legal_active_pct": float(frame["best_legal_net_active_return_pct"].median()),
        "mean_terminal_active_pct": float(frame["terminal_net_active_return_pct"].mean()),
        "median_terminal_active_pct": float(frame["terminal_net_active_return_pct"].median()),
        "mean_active_mae_pct": float(frame["active_mae_net_pct"].mean()),
    }


def run() -> dict[str, int]:
    events = pd.read_csv(EVENTS, parse_dates=["decision_ts_utc"])
    earnings = events[events["family"].eq("earnings")].copy()
    earnings["pre_dislocation_gate"] = _pre_dislocation_gate(earnings)
    earnings["cede_structural_gate"] = _structural_gate(earnings)

    oof = pd.read_csv(OOF, parse_dates=["entry_date"])
    oof = oof[oof["event_family"].eq("earnings")].copy()
    oof["trade_event_id"] = oof["economic_event_id"].astype(str) + "@" + oof["entry_date"].dt.date.astype(str)
    labels = oof[[
        "trade_event_id", "benchmark", "symbol", "outer_fold", "ever_profitable_after_costs",
        "never_profitable_after_costs", "persistent_loser", "best_legal_net_active_return_pct",
        "terminal_net_active_return_pct", "active_mae_net_pct",
    ]]
    joined = earnings.merge(labels, on="trade_event_id", how="inner", validate="one_to_many")
    joined["cohort"] = np.select(
        [joined["cede_structural_gate"], joined["pre_dislocation_gate"]],
        ["CEDE_structural_gate", "pre_dislocation_comparator"],
        default="canonical_covered_calibrated_population",
    )

    summaries = []
    for benchmark, group in joined.groupby("benchmark", sort=True):
        for cohort, subset in group.groupby("cohort", sort=True):
            summaries.append({"benchmark": benchmark, **_summary(subset, str(cohort))})
    summary = pd.DataFrame(summaries)
    folds = (
        joined[joined["cohort"].eq("CEDE_structural_gate")]
        .groupby(["benchmark", "outer_fold"], as_index=False)
        .agg(
            rows=("trade_event_id", "size"), economic_events=("trade_event_id", "nunique"),
            profitable_share=("ever_profitable_after_costs", "mean"),
            persistent_loser_share=("persistent_loser", "mean"),
            mean_terminal_active_pct=("terminal_net_active_return_pct", "mean"),
            median_terminal_active_pct=("terminal_net_active_return_pct", "median"),
        )
    )
    selected = joined[joined["cohort"].eq("CEDE_structural_gate")].copy()
    selected.to_csv(RUN / "structural_oof_label_selected_events.csv", index=False)
    summary.to_csv(RUN / "structural_oof_label_diagnostic_summary.csv", index=False)
    folds.to_csv(RUN / "structural_oof_label_fold_diagnostic.csv", index=False)

    lines = [
        "# CEDE structural-gate OOF label diagnostic",
        "",
        "This is **not a CEDE performance replay**. It joins the fixed, timestamp-safe CEDE structural gate to the existing Stage 2F full-path research labels. Those labels use legacy SPY/QQQ active returns and legal-oracle outcomes, not CEDE's sector-hedged entry/exit execution.",
        "",
        "## What it can say",
        "",
        "- Whether the new probability-price dislocation gate is descriptively enriched for old legal opportunities.",
        "- Whether the few selected events are concentrated in one chronological fold.",
        "",
        "## What it cannot say",
        "",
        "- That CEDE has a validated live expected-return model.",
        "- That the selected group has a profitable CEDE portfolio replay or beats SPY/QQQ after the new exits.",
        "",
        "## Summary",
        "",
        "```csv\n" + summary.to_csv(index=False).strip() + "\n```",
        "",
        "## Selected-cohort fold results",
        "",
        "```csv\n" + folds.to_csv(index=False).strip() + "\n```",
    ]
    (RUN / "STRUCTURAL_OOF_LABEL_DIAGNOSTIC.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"joined_label_rows": len(joined), "selected_label_rows": len(selected), "selected_events": int(selected["trade_event_id"].nunique())}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
