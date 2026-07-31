"""Stage 3B: one falsification test of a relative-follow-through exit.

Hypothesis
----------
The valid earnings evidence does *not* say that a high Polymarket probability
predicts a large pre-event move.  It says that, after a Target-B candidate has
entered, a stock that fails to outperform its benchmark during the first full
post-entry session is a qualitatively different trade.  It should be released
back to the benchmark at the next executable opening rather than allowed to
consume the remaining event window.

This is deliberately a single fixed rule--not a CEM parameter search:

    At the close of the first full session after entry, require stock active
    return versus the selected benchmark to be at least +1%.  If it is below
    +1%, sell at the following regular-session open.

The threshold was chosen from the timestamp-safe Stage 2F path-state audit
and is independently checked (only as a transfer diagnostic) on corrected
2026 CEM OOS earnings paths.  This module supplies the decisive test: frozen
Target-B entries, daily-next-open fills, exact capacity replay, all five
chronological folds, and both SPY and QQQ.  A good-looking result is *not*
promoted unless it beats each benchmark and clears the paired-fold gates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from selection.stage3a_execution_safe import (
    ARM_A,
    HISTORY,
    PRICES,
    _active_metrics,
    _day,
    _effective_asof,
    _entry_and_exit_plans,
    _exit_fill_on_stop,
    _hash,
    _json,
    _load_prices,
    _markdown_table,
    _rank_actual_entry_days,
    _replay,
    _session_open,
    _simulation_prices,
    _simulation_probs,
)
from selection.stage3a_execution_safe import (
    INVALIDATION_THRESHOLD,
    PROFIT_LOCK_ACTIVATION,
    TRAILING_ATR_MULTIPLE,
)


PROJECT = Path(__file__).resolve().parents[1]
STAGE2F_OOF = PROJECT / "data" / "selection_stage2f" / "nested_oof_models" / "stage2f_oof_predictions.csv"
OUTPUT = PROJECT / "data" / "selection_stage3b_relative_confirmation"

ARM_C = "C_relative_follow_through_confirmation"
CONFIRMATION_ACTIVE_RETURN_PCT = 1.0


def _benchmark_open_and_prior_close(
    benchmark_bars: list[dict[str, float | pd.Timestamp]],
    entry_day: pd.Timestamp,
    prior_day: pd.Timestamp,
) -> tuple[float, float] | None:
    by_day = {pd.Timestamp(bar["date"]): bar for bar in benchmark_bars}
    entry = by_day.get(entry_day)
    prior = by_day.get(prior_day)
    if entry is None or prior is None:
        return None
    opening, prior_close = float(entry["open"]), float(prior["close"])
    if opening <= 0.0 or prior_close <= 0.0:
        return None
    return opening, prior_close


def _confirmation_exit(
    path: list[dict[str, float | pd.Timestamp]],
    benchmark_bars: list[dict[str, float | pd.Timestamp]],
    entry_price: float,
    atr_pct: float,
    probability_path: pd.DataFrame,
    polarity: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Daily-open executable reference exit plus one first-full-day rule.

    ``path[0]`` is the partial entry session.  At the beginning of
    ``path[2]`` we know the close of ``path[1]``, which is the first full
    post-entry session, but nothing from the current session.  That makes the
    confirmation decision available strictly before the exit fill.
    """
    peak = 0.0
    entry_day = pd.Timestamp(path[0]["date"])
    audit: dict[str, Any] = {
        "confirmation_observed": False,
        "confirmation_active_return_pct": np.nan,
        "confirmation_passed": np.nan,
        "confirmation_decision_timestamp_utc": pd.NaT,
        "post_confirmation_observations_used": 0,
    }
    for index, bar in enumerate(path):
        day = pd.Timestamp(bar["date"])
        if index == 0:
            peak = max(peak, float(bar["high"]) / entry_price - 1.0)
            continue

        # Evaluate the first complete post-entry session only at the *next*
        # opening.  A missing benchmark price is conservatively a no-exit,
        # not an invented confirmation.
        if index == 2:
            prior = path[index - 1]
            benchmark_values = _benchmark_open_and_prior_close(
                benchmark_bars, entry_day, pd.Timestamp(prior["date"])
            )
            if benchmark_values is not None:
                benchmark_entry_open, benchmark_prior_close = benchmark_values
                active_return_pct = 100.0 * (
                    (float(prior["close"]) / entry_price - 1.0)
                    - (benchmark_prior_close / benchmark_entry_open - 1.0)
                )
                passed = active_return_pct >= CONFIRMATION_ACTIVE_RETURN_PCT
                audit.update({
                    "confirmation_observed": True,
                    "confirmation_active_return_pct": active_return_pct,
                    "confirmation_passed": passed,
                    "confirmation_decision_timestamp_utc": _session_open(day),
                })
                if not passed:
                    return ({
                        "exit_date": day,
                        "exit_price": float(bar["open"]),
                        "exit_reason": "relative_follow_through_fail_next_open",
                    }, audit)

        probability = _effective_asof(probability_path, polarity, _session_open(day))
        if np.isfinite(probability) and probability < INVALIDATION_THRESHOLD:
            return ({"exit_date": day, "exit_price": float(bar["open"]), "exit_reason": "poly_preopen<0.55"}, audit)
        stop = entry_price * (1.0 + peak - TRAILING_ATR_MULTIPLE * atr_pct)
        if peak >= PROFIT_LOCK_ACTIVATION:
            stop = max(stop, entry_price * (1.0 + int(peak * 100.0) / 100.0))
        if float(bar["low"]) <= stop:
            reason = (
                f"profit_lock_{int(peak * 100.0)}%"
                if peak >= PROFIT_LOCK_ACTIVATION and stop >= entry_price * (1.0 + int(peak * 100.0) / 100.0)
                else "trailing_3.65ATR"
            )
            return ({"exit_date": day, "exit_price": _exit_fill_on_stop(bar, stop), "exit_reason": reason}, audit)
        if index == len(path) - 1:
            return ({"exit_date": day, "exit_price": float(bar["close"]), "exit_reason": "resolution-1d_close"}, audit)
        peak = max(peak, float(bar["high"]) / entry_price - 1.0)
    raise AssertionError("A legal path must have an exit")


def _confirmation_plans(
    oof: pd.DataFrame,
    audit: pd.DataFrame,
    histories: dict[str, pd.DataFrame],
    bars_by_symbol: dict[str, list[dict[str, float | pd.Timestamp]]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    usable = audit.loc[audit["status"].eq("usable")].set_index("stage2e_candidate_id")
    plans: list[dict[str, Any]] = []
    state_rows: list[dict[str, Any]] = []
    for row in oof.itertuples(index=False):
        candidate_id = str(row.stage2e_candidate_id)
        if candidate_id not in usable.index:
            continue
        entry = usable.loc[candidate_id]
        market_id, symbol, benchmark = str(row.market_id), str(row.symbol), str(row.benchmark)
        probability_path = histories[market_id]
        stock_bars = bars_by_symbol.get(symbol, [])
        benchmark_bars = bars_by_symbol.get(benchmark, [])
        entry_day, legal_te = _day(entry.entry_date), _day(row.t_e)
        stock_path = [bar for bar in stock_bars if entry_day <= pd.Timestamp(bar["date"]) < legal_te]
        if len(stock_path) < 2:
            continue
        exit_plan, state = _confirmation_exit(
            stock_path,
            benchmark_bars,
            float(entry.entry_price),
            float(entry.pre_entry_atr20_pct) / 100.0,
            probability_path,
            int(entry.polarity),
        )
        exit_day = _day(exit_plan["exit_date"])
        if not entry_day <= exit_day < legal_te:
            raise AssertionError(f"Illegal confirmation exit for {candidate_id}")
        plans.append({
            "stage2e_candidate_id": candidate_id,
            "_stage3a_exit_date": exit_day,
            "_stage3a_exit_price": float(exit_plan["exit_price"]),
            "_stage3a_exit_reason": str(exit_plan["exit_reason"]),
            "exit_strictly_before_legal_te": True,
        })
        state_rows.append({
            "stage2e_candidate_id": candidate_id,
            "benchmark": benchmark,
            "outer_fold": int(row.outer_fold),
            "symbol": symbol,
            "entry_date": entry_day,
            "legal_t_e": legal_te,
            "planned_exit_date": exit_day,
            "planned_exit_reason": str(exit_plan["exit_reason"]),
            **state,
        })
    return pd.DataFrame(plans), pd.DataFrame(state_rows)


def _paired(folds: pd.DataFrame) -> pd.DataFrame:
    pivot = folds.pivot(index=["outer_fold", "benchmark"], columns="arm", values=["excess_return", "active_max_drawdown_pct"]).reset_index()
    pivot.columns = ["_".join(str(part) for part in column if part) if isinstance(column, tuple) else str(column) for column in pivot.columns]
    paired = pivot.rename(columns={
        f"excess_return_{ARM_A}": "excess_return_a_pct",
        f"excess_return_{ARM_C}": "excess_return_c_pct",
        f"active_max_drawdown_pct_{ARM_A}": "active_max_drawdown_a_pct",
        f"active_max_drawdown_pct_{ARM_C}": "active_max_drawdown_c_pct",
    })
    paired["excess_return_delta_c_minus_a_pct"] = paired["excess_return_c_pct"] - paired["excess_return_a_pct"]
    paired["active_drawdown_delta_c_minus_a_pct"] = paired["active_max_drawdown_c_pct"] - paired["active_max_drawdown_a_pct"]
    return paired


def _decision(combined: pd.DataFrame, paired: pd.DataFrame) -> tuple[str, dict[str, Any]]:
    gates: dict[str, Any] = {}
    promoted = True
    for benchmark in ("SPY", "QQQ"):
        challenger = combined[(combined["benchmark"] == benchmark) & (combined["arm"] == ARM_C)].iloc[0]
        pair = paired[paired["benchmark"] == benchmark]
        absolute_excess = float(challenger["excess_return"])
        median_delta = float(pair["excess_return_delta_c_minus_a_pct"].median())
        positive_folds = int((pair["excess_return_delta_c_minus_a_pct"] > 0.0).sum())
        median_dd_delta = float(pair["active_drawdown_delta_c_minus_a_pct"].median())
        passed = absolute_excess > 0.0 and median_delta > 0.0 and positive_folds >= 3 and median_dd_delta >= -1.0
        gates[benchmark] = {
            "combined_absolute_excess_pct": absolute_excess,
            "paired_median_excess_delta_c_minus_a_pct": median_delta,
            "positive_improvement_folds": positive_folds,
            "paired_median_active_drawdown_delta_c_minus_a_pct": median_dd_delta,
            "passed": passed,
        }
        promoted = promoted and passed
    return (ARM_C if promoted else "NO_VALIDATED_EARNINGS_ALGORITHM"), gates


def _report(output: Path, audit: pd.DataFrame, states: pd.DataFrame, combined: pd.DataFrame, paired: pd.DataFrame, decision: str, gates: dict[str, Any]) -> None:
    view = combined[["arm", "benchmark", "total_return", "benchmark_return", "excess_return", "active_max_drawdown_pct", "n_trades", "win_rate"]]
    confirmation = states.groupby(["benchmark", "planned_exit_reason"], as_index=False).size().rename(columns={"size": "candidates"})
    lines = [
        "# Stage 3B — relative-follow-through falsification test",
        "",
        "Target-B selection remains frozen.  The new rule has exactly one decision: after the first complete post-entry session, keep the position only if its stock return minus benchmark return is at least +1%; otherwise exit at the following regular-session open.  The decision uses only the prior close and is timestamped before the fill.  The trailing/profit-lock/Polymarket-invalidating reference machinery remains unchanged after confirmation.",
        "",
        "## Confirmation-state coverage",
        "",
        _markdown_table(confirmation),
        "",
        "## Exact timestamp-safe replay",
        "",
        _markdown_table(view),
        "",
        "## Paired chronological folds (C minus corrected reference A)",
        "",
        _markdown_table(paired),
        "",
        "## Decision",
        "",
        f"**{decision}**",
        "",
        "Promotion requires positive total active excess, positive paired median improvement, at least three improving folds, and no material paired drawdown deterioration in both SPY and QQQ.  A pass only means this specific, timestamp-safe replay passed; it is not a live-capital authorization.",
        "",
        "```json",
        json.dumps(gates, indent=2),
        "```",
    ]
    (output / "stage3b_relative_confirmation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(output: Path = OUTPUT) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    oof = pd.read_csv(STAGE2F_OOF, dtype={"market_id": str})
    histories_raw = pd.read_csv(HISTORY, dtype={"market_id": str}, usecols=["market_id", "source_ts_utc", "probability_yes"], parse_dates=["source_ts_utc"])
    histories = {market: group.sort_values("source_ts_utc").reset_index(drop=True) for market, group in histories_raw.groupby("market_id", sort=False)}
    raw_prices, bars_by_symbol = _load_prices()
    audit, base_plans = _entry_and_exit_plans(oof, histories, bars_by_symbol)
    usable = audit[audit["status"].eq("usable")]
    if len(usable) < 400:
        raise AssertionError("Timestamp-safe entry coverage is inadequate")
    confirmation_plans, states = _confirmation_plans(oof, audit, histories, bars_by_symbol)
    if len(confirmation_plans) != len(usable):
        raise AssertionError("Every usable candidate needs a confirmation exit plan")
    if not confirmation_plans["exit_strictly_before_legal_te"].all():
        raise AssertionError("A confirmation plan crossed legal T_e")

    usable_fields = usable[["stage2e_candidate_id", "entry_date", "entry_price", "legal_t_e", "polarity"]].copy()
    frame = oof.merge(usable_fields, on="stage2e_candidate_id", how="inner", validate="one_to_one", suffixes=("", "_stage3a"))
    frame["_stage3a_legal_t_e"] = pd.to_datetime(frame["legal_t_e"], utc=True)
    frame["_stage3a_polarity"] = frame["polarity"].astype(int)
    frame["t_theta"] = pd.to_datetime(frame["entry_date_stage3a"], utc=True)
    frame["t_e"] = frame["_stage3a_legal_t_e"] + pd.Timedelta(days=1)
    frame["entry_date"] = frame["t_theta"]
    frame = _rank_actual_entry_days(frame)
    sim_prices = _simulation_prices(raw_prices, audit)
    sim_probs = _simulation_probs(frame)

    plan_by_arm = {
        ARM_A: base_plans.loc[base_plans["arm"].eq(ARM_A), ["stage2e_candidate_id", "planned_exit_date", "planned_exit_price", "planned_exit_reason"]].rename(columns={
            "planned_exit_date": "_stage3a_exit_date", "planned_exit_price": "_stage3a_exit_price", "planned_exit_reason": "_stage3a_exit_reason",
        }),
        ARM_C: confirmation_plans.drop(columns=["exit_strictly_before_legal_te"]),
    }
    combined_rows: list[dict[str, Any]] = []
    fold_rows: list[dict[str, Any]] = []
    all_trades: list[pd.DataFrame] = []
    for arm, plans in plan_by_arm.items():
        planned = frame.merge(plans, on="stage2e_candidate_id", how="inner", validate="one_to_one")
        for benchmark in ("SPY", "QQQ"):
            result, trades = _replay(planned, sim_prices, sim_probs, benchmark, arm, output / "exact_replay" / "combined" / arm, {"evaluation_scope": "combined_stage3b_relative_confirmation"})
            combined_rows.append(result)
            if not trades.empty:
                detail = trades.copy()
                detail["arm"], detail["benchmark"], detail["outer_fold"] = arm, benchmark, np.nan
                all_trades.append(detail)
        for fold, fold_frame in planned.groupby("outer_fold", sort=True):
            for benchmark in ("SPY", "QQQ"):
                result, trades = _replay(fold_frame, sim_prices, sim_probs, benchmark, arm, None, {"evaluation_scope": "outer_fold_stage3b_relative_confirmation", "outer_fold": int(fold)})
                fold_rows.append(result)
                if not trades.empty:
                    detail = trades.copy()
                    detail["arm"], detail["benchmark"], detail["outer_fold"] = arm, benchmark, int(fold)
                    all_trades.append(detail)
    combined, folds = pd.DataFrame(combined_rows), pd.DataFrame(fold_rows)
    paired = _paired(folds)
    decision, gates = _decision(combined, paired)

    audit.to_csv(output / "entry_coverage_and_leakage_audit.csv", index=False)
    base_plans.to_csv(output / "reference_candidate_exit_plans.csv", index=False)
    confirmation_plans.to_csv(output / "confirmation_candidate_exit_plans.csv", index=False)
    states.to_csv(output / "confirmation_state_audit.csv", index=False)
    combined.to_csv(output / "combined_exact_results.csv", index=False)
    folds.to_csv(output / "outer_fold_exact_results.csv", index=False)
    paired.to_csv(output / "paired_outer_fold_comparison.csv", index=False)
    (pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()).to_csv(output / "trade_detail.csv", index=False)
    _report(output, audit, states, combined, paired, decision, gates)
    manifest = {
        "experiment": "stage3b_relative_confirmation",
        "hypothesis": "first-full-session relative confirmation identifies whether a Target-B earnings trade should continue",
        "entry_selector": "frozen_stage2f_target_b",
        "entry_execution": "first daily regular-session open strictly after raw probability signal",
        "confirmation_threshold_active_return_pct": CONFIRMATION_ACTIVE_RETURN_PCT,
        "confirmation_fill": "following daily regular-session open",
        "post_entry_observations_used_for_entry": 0,
        "arms": [ARM_A, ARM_C],
        "decision": decision,
        "gates": gates,
        "source_hashes": {"stage2f_oof": _hash(STAGE2F_OOF), "polymarket_history": _hash(HISTORY), "daily_prices": _hash(PRICES)},
    }
    _json(output / "manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
