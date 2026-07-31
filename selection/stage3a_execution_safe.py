"""Stage 3A: frozen Target B under conservative timestamp-safe execution.

This is intentionally a *single* final exit test.  It does not alter Target B,
fit new selector features, or search a stop grid.  Raw Polymarket observations
must precede the decision; because the available equity history is daily OHLC,
an eligible signal enters at the next executable daily open.  Standing stops
use conservative daily gap/touch fills.

Arms:
    A. corrected reference: 3.65 ATR trailing stop, 3% profit lock and
       pre-open Polymarket invalidation below 0.55;
    B. the previously defined volatility-scaled loss stop from session two:
       min(max(2%, 2 * ATR20), 8%).

The test reuses the fixed Stage 2F Target B OOF scores and the same five
chronological folds.  It exists to test execution semantics, not to discover
new parameters.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtesting.optimize_cem import ALLOCATION_FIFO, INITIAL_CAPITAL, PORT_DEFAULT, sim_opp_cost
from core.kernel import clear_kernel_caches
from core.polarity import resolve_polarity
from selection.stage2f_family_selection import (
    _active_metrics,
    _admission_callback,
    _as_bool,
    _slot_usage,
    _trade_concentration,
    _turnover,
)


PROJECT = Path(__file__).resolve().parents[1]
STAGE2F_OOF = PROJECT / "data" / "selection_stage2f" / "nested_oof_models" / "stage2f_oof_predictions.csv"
HISTORY = PROJECT / "data" / "selection_stage2g" / "polymarket_download" / "polymarket_probability_history.csv"
PRICES = PROJECT / "assistant_generated_all_artifacts" / "assistant_generated_research_large_supplements" / "prices_open_merged.pkl"
OUTPUT = PROJECT / "data" / "selection_stage3a_execution_safe"

ENTRY_THRESHOLD = 0.70
INVALIDATION_THRESHOLD = 0.55
TRAILING_ATR_MULTIPLE = 3.65
PROFIT_LOCK_ACTIVATION = 0.03

ARM_A = "A_corrected_reference_execution_safe"
ARM_B = "B_volatility_scaled_stop_execution_safe"
ARMS = (ARM_A, ARM_B)


def _utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return stamp


def _day(value: Any) -> pd.Timestamp:
    return _utc(value).normalize()


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def _load_prices() -> tuple[dict[str, list[tuple]], dict[str, list[dict[str, float | pd.Timestamp]]]]:
    raw = pickle.loads(PRICES.read_bytes())
    indexed: dict[str, list[dict[str, float | pd.Timestamp]]] = {}
    for symbol, records in raw.items():
        bars: list[dict[str, float | pd.Timestamp]] = []
        for record in records:
            if len(record) < 5:
                continue
            date = _day(record[0])
            o, h, l, c = (float(record[1]), float(record[2]), float(record[3]), float(record[4]))
            if not np.isfinite([o, h, l, c]).all():
                continue
            bars.append({"date": date, "open": o, "high": h, "low": l, "close": c})
        indexed[symbol] = sorted(bars, key=lambda bar: pd.Timestamp(bar["date"]))
    return raw, indexed


def _session_open(day: pd.Timestamp) -> pd.Timestamp:
    # Price bars are labelled with the exchange *date* at UTC midnight.  That
    # label is not an actual midnight instant in New York, so converting it to
    # New York first would incorrectly move the session to the prior day.
    session_date = _day(day).date()
    local = pd.Timestamp(session_date).tz_localize("America/New_York")
    return (local + pd.Timedelta(hours=9, minutes=30)).tz_convert("UTC")


def _effective_asof(path: pd.DataFrame, polarity: int, timestamp: pd.Timestamp) -> float:
    if path.empty:
        return np.nan
    position = int(path["source_ts_utc"].searchsorted(timestamp, side="right")) - 1
    if position < 0:
        return np.nan
    raw = float(path.iloc[position]["probability_yes"])
    return raw if polarity == 1 else 1.0 - raw


def _next_open_after_signal(
    bars: list[dict[str, float | pd.Timestamp]], signal: pd.Timestamp
) -> dict[str, float | pd.Timestamp] | None:
    local_signal = signal.tz_convert("America/New_York")
    local_open = local_signal.normalize() + pd.Timedelta(hours=9, minutes=30)
    desired = pd.Timestamp(local_signal.date(), tz="UTC")
    # A signal exactly at 09:30 is not known before the opening auction/fill.
    if local_signal >= local_open:
        desired += pd.Timedelta(days=1)
    return next((bar for bar in bars if pd.Timestamp(bar["date"]) >= desired), None)


def _atr_pct(bars: list[dict[str, float | pd.Timestamp]], entry_day: pd.Timestamp, entry_price: float) -> float:
    history = [bar for bar in bars if pd.Timestamp(bar["date"]) <= entry_day][-16:]
    if len(history) < 2 or entry_price <= 0:
        return np.nan
    true_ranges = []
    for previous, current in zip(history[:-1], history[1:]):
        high, low, prev_close = float(current["high"]), float(current["low"]), float(previous["close"])
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return float(np.mean(true_ranges) / entry_price) if true_ranges else np.nan


def _exit_fill_on_stop(bar: dict[str, float | pd.Timestamp], stop: float) -> float:
    opening = float(bar["open"])
    return opening if opening <= stop else stop


def _reference_exit(
    path: list[dict[str, float | pd.Timestamp]],
    entry_price: float,
    atr_pct: float,
    probability_path: pd.DataFrame,
    polarity: int,
) -> dict[str, Any]:
    """Standing-stop, pre-open-information implementation of Arm A."""
    peak = 0.0
    for index, bar in enumerate(path):
        day = pd.Timestamp(bar["date"])
        if index == 0:
            peak = max(peak, float(bar["high"]) / entry_price - 1.0)
            continue
        probability = _effective_asof(probability_path, polarity, _session_open(day))
        if np.isfinite(probability) and probability < INVALIDATION_THRESHOLD:
            return {"exit_date": day, "exit_price": float(bar["open"]), "exit_reason": "poly_preopen<0.55"}
        stop = entry_price * (1.0 + peak - TRAILING_ATR_MULTIPLE * atr_pct)
        if peak >= PROFIT_LOCK_ACTIVATION:
            locked = int(peak * 100.0) / 100.0
            stop = max(stop, entry_price * (1.0 + locked))
        if float(bar["low"]) <= stop:
            reason = f"profit_lock_{int(peak * 100.0)}%" if peak >= PROFIT_LOCK_ACTIVATION and stop >= entry_price * (1.0 + int(peak * 100.0) / 100.0) else "trailing_3.65ATR"
            return {"exit_date": day, "exit_price": _exit_fill_on_stop(bar, stop), "exit_reason": reason}
        if index == len(path) - 1:
            return {"exit_date": day, "exit_price": float(bar["close"]), "exit_reason": "resolution-1d_close"}
        peak = max(peak, float(bar["high"]) / entry_price - 1.0)
    raise AssertionError("A legal path must have an exit")


def _volatility_stop_exit(
    path: list[dict[str, float | pd.Timestamp]], entry_price: float, atr_pct: float
) -> dict[str, Any]:
    """The pre-registered Stage 2E volatility-stop challenger with daily fills."""
    stop_pct = min(max(0.02, 2.0 * atr_pct), 0.08)
    stop = entry_price * (1.0 - stop_pct)
    for index, bar in enumerate(path):
        if index == 0:
            continue
        if float(bar["low"]) <= stop:
            return {
                "exit_date": pd.Timestamp(bar["date"]),
                "exit_price": _exit_fill_on_stop(bar, stop),
                "exit_reason": f"volatility_stop_{stop_pct * 100.0:.2f}%",
            }
        if index == len(path) - 1:
            return {"exit_date": pd.Timestamp(bar["date"]), "exit_price": float(bar["close"]), "exit_reason": "resolution-1d_close"}
    raise AssertionError("A legal path must have an exit")


def _entry_and_exit_plans(
    oof: pd.DataFrame,
    histories: dict[str, pd.DataFrame],
    bars_by_symbol: dict[str, list[dict[str, float | pd.Timestamp]]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    audit_rows: list[dict[str, Any]] = []
    plan_rows: list[dict[str, Any]] = []
    for _, row in oof.iterrows():
        candidate_id = str(row["stage2e_candidate_id"])
        market_id = str(row["market_id"])
        symbol = str(row["symbol"])
        path = histories.get(market_id)
        bars = bars_by_symbol.get(symbol, [])
        theta, legal_te = _day(row["t_theta"]), _day(row["t_e"])
        polarity, polarity_source = resolve_polarity(str(row["question"]), symbol)
        base = {
            "stage2e_candidate_id": candidate_id,
            "market_id": market_id,
            "symbol": symbol,
            "benchmark": row["benchmark"],
            "outer_fold": int(row["outer_fold"]),
            "original_t_theta": theta,
            "legal_t_e": legal_te,
            "polarity": polarity,
            "polarity_source": polarity_source,
        }
        if path is None or path.empty or polarity == 0:
            audit_rows.append({**base, "status": "missing_probability_history_or_polarity"})
            continue
        eligible = path[path["source_ts_utc"] >= theta].copy()
        effective = eligible["probability_yes"] if polarity == 1 else 1.0 - eligible["probability_yes"]
        hits = eligible[effective >= ENTRY_THRESHOLD]
        if hits.empty:
            audit_rows.append({**base, "status": "never_reached_entry_threshold"})
            continue
        hit = hits.iloc[0]
        signal_ts = _utc(hit["source_ts_utc"])
        entry_bar = _next_open_after_signal(bars, signal_ts)
        if entry_bar is None:
            audit_rows.append({**base, "status": "no_subsequent_open", "signal_ts_utc": signal_ts})
            continue
        entry_day = pd.Timestamp(entry_bar["date"])
        legal_path = [bar for bar in bars if entry_day <= pd.Timestamp(bar["date"]) < legal_te]
        if len(legal_path) < 2:
            audit_rows.append({**base, "status": "fewer_than_two_legal_sessions", "signal_ts_utc": signal_ts, "entry_date": entry_day})
            continue
        entry_open = _session_open(entry_day)
        if not signal_ts < entry_open:
            raise AssertionError(f"Signal is not strictly before the planned fill for {candidate_id}")
        entry_price = float(entry_bar["open"])
        atr_pct = _atr_pct(bars, entry_day, entry_price)
        if not np.isfinite(atr_pct) or atr_pct <= 0:
            audit_rows.append({**base, "status": "missing_pre_entry_atr", "signal_ts_utc": signal_ts, "entry_date": entry_day})
            continue
        audit_rows.append({
            **base,
            "status": "usable",
            "signal_ts_utc": signal_ts,
            "signal_effective_probability": float(hit["probability_yes"] if polarity == 1 else 1.0 - hit["probability_yes"]),
            "entry_date": entry_day,
            "entry_open_timestamp_utc": entry_open,
            "entry_price": entry_price,
            "pre_entry_atr20_pct": atr_pct * 100.0,
            "signal_to_open_hours": (entry_open - signal_ts).total_seconds() / 3600.0,
            "post_entry_observations_used_for_entry": 0,
        })
        for arm, exit_plan in (
            (ARM_A, _reference_exit(legal_path, entry_price, atr_pct, path, polarity)),
            (ARM_B, _volatility_stop_exit(legal_path, entry_price, atr_pct)),
        ):
            if not entry_day <= _day(exit_plan["exit_date"]) < legal_te:
                raise AssertionError(f"Illegal Stage 3A exit for {candidate_id}/{arm}")
            plan_rows.append({
                **base,
                "arm": arm,
                "signal_ts_utc": signal_ts,
                "entry_date": entry_day,
                "entry_price": entry_price,
                "pre_entry_atr20_pct": atr_pct * 100.0,
                "planned_exit_date": _day(exit_plan["exit_date"]),
                "planned_exit_price": float(exit_plan["exit_price"]),
                "planned_exit_reason": str(exit_plan["exit_reason"]),
                "exit_strictly_before_legal_te": True,
            })
    return pd.DataFrame(audit_rows), pd.DataFrame(plan_rows)


def _simulation_prices(raw_prices: dict[str, list[tuple]], audit: pd.DataFrame) -> dict[str, list[tuple]]:
    """Set only each entry-day close to its already-known open.

    ``simulate_one`` uses the first bar's close as its entry price.  Replacing
    that one value makes the established exact allocator open the trade at the
    next daily open without exposing a future close.  High/low remain intact
    for subsequent stop fill calculations, which are overridden by the tested
    precomputed plans.
    """
    overrides = {
        (str(row.symbol), _day(row.entry_date)): float(row.entry_price)
        for row in audit.loc[audit["status"].eq("usable")].itertuples(index=False)
    }
    output: dict[str, list[tuple]] = {}
    for symbol, records in raw_prices.items():
        adjusted: list[tuple] = []
        for record in records:
            if len(record) < 5:
                adjusted.append(record)
                continue
            key = (str(symbol), _day(record[0]))
            if key in overrides:
                adjusted.append((record[0], record[1], record[2], record[3], overrides[key]))
            else:
                adjusted.append(record)
        output[str(symbol)] = adjusted
    return output


def _simulation_probs(frame: pd.DataFrame) -> dict[str, list[tuple]]:
    """Supply a single pre-open threshold observation for the frozen kernel.

    Exit decisions do not use this synthetic path; both arms use their audited
    precomputed exit plans.  The raw history remains the sole source of every
    entry signal and Arm A probability invalidation.
    """
    output: dict[str, list[tuple]] = {}
    for _, row in frame.iterrows():
        market_id = str(row["market_id"])
        raw_probability = ENTRY_THRESHOLD if int(row["_stage3a_polarity"]) == 1 else 1.0 - ENTRY_THRESHOLD
        output[market_id] = [(_day(row["t_theta"]), raw_probability)]
    return output


def _rank_actual_entry_days(frame: pd.DataFrame) -> pd.DataFrame:
    ranked = frame.copy()
    ranked["_selector_rank"] = 0
    for _, group in ranked.groupby(["benchmark", "t_theta"], sort=False):
        order = group.sort_values(
            ["oof_predicted_target_b_slot", "expected_slot_days", "source_order", "symbol"],
            ascending=[False, True, True, True],
            kind="mergesort",
        ).index
        ranked.loc[order, "_selector_rank"] = np.arange(len(order), dtype=int)
    ranked["_admission_score"] = pd.to_numeric(ranked["oof_predicted_target_b_slot"], errors="coerce")
    ranked["stage2f_policy_accept"] = ranked["_admission_score"].gt(0.0)
    ranked["stage2f_policy"] = "A_target_b_baseline"
    return ranked


def _replay(
    frame: pd.DataFrame,
    sim_prices: dict[str, list[tuple]],
    sim_probs: dict[str, list[tuple]],
    benchmark: str,
    arm: str,
    output_dir: Path | None,
    metadata: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    subset = frame[frame["benchmark"].eq(benchmark)].copy()
    if subset.empty:
        return {**metadata, "benchmark": benchmark, "arm": arm, "n_trades": 0}, pd.DataFrame()
    start = _day(subset["t_theta"].min())
    # Keep the evaluation span identical across arms, while the kernel has a
    # one-day internal buffer only to reproduce the historical legal T_e - 1.
    end = _day(subset["_stage3a_legal_t_e"].max())
    clear_kernel_caches()
    trades, equity, stats, _frozen, allocation, disposition = sim_opp_cost(
        subset,
        sim_prices,
        sim_probs,
        dict(PORT_DEFAULT),
        bench_sym=benchmark,
        initial=INITIAL_CAPITAL,
        start_date=start,
        end_date=end,
        allocation_mode=ALLOCATION_FIFO,
        collect_allocation_log=True,
        admission_policy=_admission_callback,
        exit_plan_columns=("_stage3a_exit_date", "_stage3a_exit_price", "_stage3a_exit_reason"),
    )
    if not trades.empty:
        plan_lookup = subset.set_index("stage2e_candidate_id")["_stage3a_legal_t_e"]
        trades["legal_t_e"] = trades["stage2e_candidate_id"].map(plan_lookup)
        exits = pd.to_datetime(trades["exit_date"], utc=True).dt.normalize()
        legal_tes = pd.to_datetime(trades["legal_t_e"], utc=True).dt.normalize()
        if not (exits < legal_tes).all():
            raise AssertionError("Stage 3A replay exited at or after its legal T_e")
    turnover_notional, turnover_x = _turnover(trades, equity)
    result = {
        **metadata,
        "benchmark": benchmark,
        "arm": arm,
        **stats,
        **_active_metrics(equity),
        **_trade_concentration(trades),
        "turnover_notional": turnover_notional,
        "turnover_x_average_equity": turnover_x,
        "slot_usage_pct": _slot_usage(trades, start, end),
        "n_trades": int(len(trades)),
        "selected_decisions": int(allocation.get("decision", pd.Series(dtype=object)).eq("selected").sum()) if not allocation.empty else 0,
        "admission_rejected": int(allocation.get("skip_reason", pd.Series(dtype=object)).eq("admission_reject").sum()) if not allocation.empty else 0,
        "blocked_by_capacity": int(allocation.get("skip_reason", pd.Series(dtype=object)).eq("max_concurrent").sum()) if not allocation.empty else 0,
    }
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        trades.to_csv(output_dir / f"trades_{benchmark.lower()}.csv", index=False)
        equity.to_csv(output_dir / f"equity_{benchmark.lower()}.csv", index=False)
        allocation.to_csv(output_dir / f"allocation_{benchmark.lower()}.csv", index=False)
        disposition.to_csv(output_dir / f"disposition_{benchmark.lower()}.csv", index=False)
    return result, trades


def _decision(paired: pd.DataFrame) -> tuple[str, dict[str, Any]]:
    gates: dict[str, Any] = {}
    promote = True
    for benchmark in ("SPY", "QQQ"):
        subset = paired[paired["benchmark"].eq(benchmark)]
        median_delta = float(subset["excess_return_delta_b_minus_a"].median())
        positive_folds = int((subset["excess_return_delta_b_minus_a"] > 0.0).sum())
        median_dd_delta = float(subset["active_drawdown_delta_b_minus_a"].median())
        passed = median_delta > 0.0 and positive_folds >= 3 and median_dd_delta >= -1.0
        gates[benchmark] = {
            "paired_median_excess_delta_b_minus_a_pct": median_delta,
            "positive_improvement_folds": positive_folds,
            "paired_median_active_drawdown_delta_b_minus_a_pct": median_dd_delta,
            "passed": passed,
        }
        promote = promote and passed
    return (ARM_B if promote else ARM_A), gates


def _markdown_table(frame: pd.DataFrame) -> str:
    """Render a compact markdown table without requiring optional tabulate."""
    columns = list(frame.columns)
    head = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    rows = []
    for _, row in frame.iterrows():
        values = []
        for value in row:
            if isinstance(value, (float, np.floating)):
                values.append(f"{float(value):.4f}")
            else:
                values.append(str(value))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join([head, divider, *rows])


def _report(
    output: Path,
    audit: pd.DataFrame,
    combined: pd.DataFrame,
    paired: pd.DataFrame,
    final_arm: str,
    gates: dict[str, Any],
) -> None:
    usable = audit[audit["status"].eq("usable")]
    coverage = audit["status"].value_counts().to_dict()
    combined_view = combined[["arm", "benchmark", "total_return", "benchmark_return", "excess_return", "active_max_drawdown_pct", "n_trades", "win_rate"]].copy()
    lines = [
        "# Stage 3A — final timestamp-safe Target B exit test",
        "",
        "## Design",
        "",
        "Target B scores and admissions are frozen from Stage 2F.  A raw Polymarket observation had to be at or above 0.70 after `t_theta` and strictly before the next executable daily opening price.  This is a conservative daily-open test; it does not claim five-minute fill precision.  No raw observation after entry was used to create an entry.",
        "",
        "Arm A is the corrected 3.65 ATR trailing/profit-lock exit with a Polymarket invalidation below 0.55 known before the session opens.  Arm B is the pre-registered 2 ATR (2%–8%) volatility loss stop from session two.  There was no parameter search.",
        "",
        "## Coverage",
        "",
        f"- Candidate observations: {len(audit)}",
        f"- Usable timestamp-safe next-open entries: {len(usable)}",
        f"- Entry coverage by status: `{json.dumps(coverage, sort_keys=True)}`",
        f"- Median signal-to-open delay: {usable['signal_to_open_hours'].median():.2f} hours",
        "",
        "## Exact replay",
        "",
        _markdown_table(combined_view),
        "",
        "## Paired chronological folds (B minus A)",
        "",
        _markdown_table(paired),
        "",
        "## Decision",
        "",
        f"**Final exit algorithm: `{final_arm}`.**",
        "",
        "The challenger could replace the control only if it had positive paired median active-excess improvement, at least three improving folds, and no material median active-drawdown deterioration in both benchmarks.  Gates:",
        "",
        "```json",
        json.dumps(gates, indent=2),
        "```",
        "",
        "The entry algorithm remains frozen Target B.  This outcome closes selector research and the exit comparison; no further threshold search is justified by this sample.",
    ]
    (output / "stage3a_execution_safe_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(output: Path = OUTPUT) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    oof = pd.read_csv(STAGE2F_OOF, dtype={"market_id": str})
    histories_raw = pd.read_csv(
        HISTORY,
        dtype={"market_id": str},
        usecols=["market_id", "source_ts_utc", "probability_yes"],
        parse_dates=["source_ts_utc"],
    )
    histories = {market_id: group.sort_values("source_ts_utc").reset_index(drop=True) for market_id, group in histories_raw.groupby("market_id", sort=False)}
    raw_prices, bars_by_symbol = _load_prices()
    audit, plans = _entry_and_exit_plans(oof, histories, bars_by_symbol)
    if audit["status"].eq("usable").sum() < 400:
        raise AssertionError("Stage 3A next-open coverage is inadequate")
    if not plans["exit_strictly_before_legal_te"].all():
        raise AssertionError("Stage 3A constructed an illegal exit")
    audit.to_csv(output / "entry_coverage_and_leakage_audit.csv", index=False)
    plans.to_csv(output / "candidate_exit_plans.csv", index=False)

    usable = audit[audit["status"].eq("usable")][
        ["stage2e_candidate_id", "entry_date", "entry_price", "legal_t_e", "polarity"]
    ].copy()
    frame = oof.merge(usable, on="stage2e_candidate_id", how="inner", validate="one_to_one", suffixes=("", "_stage3a"))
    frame["_stage3a_legal_t_e"] = pd.to_datetime(frame["legal_t_e"], utc=True)
    frame["_stage3a_polarity"] = frame["polarity"].astype(int)
    frame["t_theta"] = pd.to_datetime(frame["entry_date_stage3a"], utc=True)
    # Current kernel's legacy resolution bound is one daily bar earlier than
    # the externally audited `T_e - 1`; plans and final validation retain the
    # true legal boundary, while this internal buffer only admits the fill.
    frame["t_e"] = frame["_stage3a_legal_t_e"] + pd.Timedelta(days=1)
    frame["entry_date"] = frame["t_theta"]
    frame = _rank_actual_entry_days(frame)
    sim_prices = _simulation_prices(raw_prices, audit)
    sim_probs = _simulation_probs(frame)

    combined_rows: list[dict[str, Any]] = []
    fold_rows: list[dict[str, Any]] = []
    all_trades: list[pd.DataFrame] = []
    for arm in ARMS:
        arm_plans = plans[plans["arm"].eq(arm)][
            ["stage2e_candidate_id", "planned_exit_date", "planned_exit_price", "planned_exit_reason"]
        ].rename(columns={
            "planned_exit_date": "_stage3a_exit_date",
            "planned_exit_price": "_stage3a_exit_price",
            "planned_exit_reason": "_stage3a_exit_reason",
        })
        planned = frame.merge(arm_plans, on="stage2e_candidate_id", how="inner", validate="one_to_one")
        for benchmark in ("SPY", "QQQ"):
            result, trades = _replay(
                planned,
                sim_prices,
                sim_probs,
                benchmark,
                arm,
                output / "exact_replay" / "combined" / arm,
                {"evaluation_scope": "combined_stage3a_execution_safe"},
            )
            combined_rows.append(result)
            if not trades.empty:
                detail = trades.copy()
                detail["arm"] = arm
                detail["benchmark"] = benchmark
                detail["outer_fold"] = np.nan
                all_trades.append(detail)
        for fold, fold_frame in planned.groupby("outer_fold", sort=True):
            for benchmark in ("SPY", "QQQ"):
                result, trades = _replay(
                    fold_frame,
                    sim_prices,
                    sim_probs,
                    benchmark,
                    arm,
                    None,
                    {"evaluation_scope": "outer_fold_stage3a_execution_safe", "outer_fold": int(fold)},
                )
                fold_rows.append(result)
                if not trades.empty:
                    detail = trades.copy()
                    detail["arm"] = arm
                    detail["benchmark"] = benchmark
                    detail["outer_fold"] = int(fold)
                    all_trades.append(detail)
    combined = pd.DataFrame(combined_rows)
    folds = pd.DataFrame(fold_rows)
    trade_detail = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    combined.to_csv(output / "combined_exact_results.csv", index=False)
    folds.to_csv(output / "outer_fold_exact_results.csv", index=False)
    trade_detail.to_csv(output / "trade_detail.csv", index=False)

    pivot = folds.pivot(index=["outer_fold", "benchmark"], columns="arm", values=["excess_return", "active_max_drawdown_pct"]).reset_index()
    pivot.columns = ["_".join(str(part) for part in column if part) if isinstance(column, tuple) else str(column) for column in pivot.columns]
    paired = pivot.rename(columns={
        f"excess_return_{ARM_A}": "excess_return_a_pct",
        f"excess_return_{ARM_B}": "excess_return_b_pct",
        f"active_max_drawdown_pct_{ARM_A}": "active_max_drawdown_a_pct",
        f"active_max_drawdown_pct_{ARM_B}": "active_max_drawdown_b_pct",
    })
    paired["excess_return_delta_b_minus_a"] = paired["excess_return_b_pct"] - paired["excess_return_a_pct"]
    paired["active_drawdown_delta_b_minus_a"] = paired["active_max_drawdown_b_pct"] - paired["active_max_drawdown_a_pct"]
    paired.to_csv(output / "paired_outer_fold_comparison.csv", index=False)
    final_arm, gates = _decision(paired)
    _report(output, audit, combined, paired, final_arm, gates)
    manifest = {
        "experiment": "stage3a_execution_safe",
        "oof_rows": int(len(oof)),
        "usable_entries": int(audit["status"].eq("usable").sum()),
        "entry_threshold": ENTRY_THRESHOLD,
        "entry_execution": "first daily regular-session open strictly after raw signal",
        "post_entry_observations_used_for_entry": 0,
        "arms": list(ARMS),
        "final_exit_algorithm": final_arm,
        "gates": gates,
        "source_hashes": {"stage2f_oof": _hash(STAGE2F_OOF), "polymarket_history": _hash(HISTORY), "daily_prices": _hash(PRICES)},
    }
    _json(output / "manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
