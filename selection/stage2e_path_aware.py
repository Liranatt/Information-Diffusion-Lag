"""Stage 2E: path-aware selection-exit interaction research.

This branch is development-only.  It reopens the Stage 2C *performance*
selector while preserving the semantic mapping conclusions.  Every candidate
exit is strictly before ``T_e``; the final legal session is ``T_e - 1``.

The selector/exit matrix is deliberately small and predeclared.  No threshold
is selected from a lockbox and no learned exit model is trained here.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from backtesting.optimize_cem import (
    ALLOCATION_FIFO,
    INITIAL_CAPITAL,
    PORT_DEFAULT,
    _close_on,
    as_utc_day,
    sim_opp_cost,
)
from core.kernel import simulate_one
from selection.dynamic_replay import _active_metrics
from selection.stage2c_research import _slot_usage, _trade_concentration


PROJECT = Path(__file__).resolve().parents[1]
STAGE2C = PROJECT / "data" / "selection_stage2c"
STAGE3 = PROJECT / "data" / "stage3_exit_research"
OUTPUT = PROJECT / "data" / "selection_stage2e"
SEMANTIC_CANDIDATES = STAGE2C / "semantics" / "semantic_development_candidates.csv"
OOF_STREAM = STAGE2C / "family_aware_exact_replay" / "combined_oof_candidate_stream.csv"
PRICES = PROJECT / "assistant_generated_all_artifacts" / "assistant_generated_research_large_supplements" / "prices_open_merged.pkl"
PROBS = PROJECT / "data" / "probs.pkl"

SELECTORS = (
    "direct_always_fill",
    "predicted_target_a_positive",
    "target_b_per_slot_day",
    "expected_slot_days_ranking",
    "corrected_reference_selector",
)

EXIT_POLICIES = (
    "hold_to_te1",
    "fixed_2_day",
    "fixed_4_day",
    "fixed_8_day",
    "corrected_reference_exit",
    "volatility_scaled_stop",
    "time_underwater_exit",
    "trailing_profit_giveback_exit",
)

PATH_CLASS_RULES = {
    "early_winner_with_giveback": "first-four-day active MFE >= 2%; terminal active return <= 0 or <= 25% of that early MFE",
    "early_loser_with_recovery": "first-four-day active MAE <= -2%; terminal is positive or recovers at least 75% of the early trough",
    "immediate_winner": "day-2 active return >= 2%, terminal active return > 0, and no early-winner giveback classification",
    "delayed_winner": "terminal active return >= 1%, day-2 active return <= 0, and time to MFE is after day 2",
    "persistent_loser": "terminal active return <= -1% and no more than 25% of legal holding days have positive active return",
    "flat_nonresponsive": "terminal absolute active return < 0.75% and full-path active range < 2%",
    "volatile_oscillation": "full-path active range >= 4% and at least two active-return sign changes",
    "unclassified_mixed": "path does not satisfy any predeclared diagnostic class",
}

EXIT_POLICY_RULES = {
    "hold_to_te1": "close on the final observed session strictly before T_e",
    "fixed_2_day": "close on legal holding day 2, or T_e-1 when fewer than two sessions exist",
    "fixed_4_day": "close on legal holding day 4, or T_e-1 when fewer than four sessions exist",
    "fixed_8_day": "close on legal holding day 8, or T_e-1 when fewer than eight sessions exist",
    "corrected_reference_exit": "preserve the corrected kernel exit exactly",
    "volatility_scaled_stop": "from day 2, close when stock close return breaches max(2%, 2x pre-entry ATR20), capped at 8%; otherwise T_e-1",
    "time_underwater_exit": "from day 4, close after four consecutive sessions with negative active return; otherwise T_e-1",
    "trailing_profit_giveback_exit": "after active close return reaches 3%, close on giveback of max(1.5 points, 50% of peak); otherwise T_e-1",
}


def _json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _as_bool(value: Any) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _candidate_id(row: pd.Series | dict[str, Any]) -> str:
    raw = "|".join(
        str(row.get(column, ""))
        for column in ("benchmark", "event_id", "market_id", "symbol", "t_theta", "t_e")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _parse_dates(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for column in ("entry_date", "t0", "t_theta", "t_e", "te1_exit_date"):
        if column in out:
            out[column] = pd.to_datetime(out[column], errors="coerce", utc=True)
            if column in {"entry_date", "te1_exit_date"}:
                out[column] = out[column].dt.normalize()
    return out


def _load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, dict, dict]:
    all_development = _parse_dates(pd.read_csv(SEMANTIC_CANDIDATES))
    stream = _parse_dates(pd.read_csv(OOF_STREAM))
    for label, frame in (("semantic development", all_development), ("OOF stream", stream)):
        if "analysis_split" in frame and not frame["analysis_split"].astype(str).str.lower().eq("train").all():
            raise AssertionError(f"Stage 2E {label} contains non-development rows")
        frame["stage2e_candidate_id"] = frame.apply(_candidate_id, axis=1)
        if frame["stage2e_candidate_id"].duplicated().any():
            raise AssertionError(f"Stage 2E {label} contains duplicate stable candidate IDs")
    prices = pickle.loads(PRICES.read_bytes())
    probs = pickle.loads(PROBS.read_bytes())
    return all_development, stream, prices, probs


def _kernel_seed(row: pd.Series, prices: dict, probs: dict) -> dict[str, Any] | None:
    trade = simulate_one(row, prices, probs, dict(PORT_DEFAULT))
    return dict(trade) if trade is not None else None


def _pre_entry_atr_pct(prices: dict, symbol: str, entry_date: pd.Timestamp, entry_price: float) -> float:
    bars = [bar for bar in prices.get(symbol, []) if as_utc_day(bar[0]) < entry_date]
    bars = bars[-20:]
    if not bars or entry_price <= 0:
        return 0.02
    true_ranges: list[float] = []
    previous_close: float | None = None
    for _date, _open, high, low, close in bars:
        high, low, close = float(high), float(low), float(close)
        if previous_close is None:
            true_range = high - low
        else:
            true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        true_ranges.append(true_range)
        previous_close = close
    return float(np.mean(true_ranges) / entry_price) if true_ranges else 0.02


def _build_candidate_path(
    row: pd.Series,
    prices: dict,
    seed: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    benchmark = str(row["benchmark"])
    symbol = str(row["symbol"])
    t_e = as_utc_day(row["t_e"])
    if seed is not None:
        entry_date = as_utc_day(seed["entry_date"])
        entry_price = float(seed["entry_price"])
        path_entry_source = "corrected_kernel_entry"
    else:
        entry_date = as_utc_day(row["entry_date"])
        entry_price = float(pd.to_numeric(row.get("entry_price"), errors="coerce"))
        path_entry_source = "candidate_fallback_non_executable"
    benchmark_entry_price = _close_on(prices, benchmark, entry_date)
    if not np.isfinite(entry_price) or entry_price <= 0 or benchmark_entry_price is None or benchmark_entry_price <= 0:
        return [], {
            "stage2e_candidate_id": row["stage2e_candidate_id"],
            "benchmark": benchmark,
            "symbol": symbol,
            "kernel_executable": seed is not None,
            "path_available": False,
            "path_entry_source": path_entry_source,
        }

    stock_bars = sorted(
        [bar for bar in prices.get(symbol, []) if entry_date <= as_utc_day(bar[0]) < t_e],
        key=lambda bar: as_utc_day(bar[0]),
    )
    rows: list[dict[str, Any]] = []
    active_closes: list[float] = []
    active_highs: list[float] = []
    active_lows: list[float] = []
    stock_daily_returns: list[float] = []
    active_daily_returns: list[float] = []
    gaps: list[float] = []
    previous_stock_close = entry_price
    previous_benchmark_close = float(benchmark_entry_price)
    underwater_duration = 0
    below_zero_duration = 0
    peak_close = -np.inf
    peak_close_day = 0

    for day_index, bar in enumerate(stock_bars, start=1):
        date, open_price, high, low, close = bar
        date = as_utc_day(date)
        open_price, high, low, close = map(float, (open_price, high, low, close))
        benchmark_close = _close_on(prices, benchmark, date)
        if benchmark_close is None or benchmark_close <= 0:
            continue
        stock_return = (close / entry_price - 1.0) * 100.0
        benchmark_return = (float(benchmark_close) / float(benchmark_entry_price) - 1.0) * 100.0
        active_close = stock_return - benchmark_return
        active_high = (high / entry_price - 1.0) * 100.0 - benchmark_return
        active_low = (low / entry_price - 1.0) * 100.0 - benchmark_return
        stock_daily = (close / previous_stock_close - 1.0) * 100.0
        benchmark_daily = (float(benchmark_close) / previous_benchmark_close - 1.0) * 100.0
        active_daily = stock_daily - benchmark_daily
        gap = (open_price / previous_stock_close - 1.0) * 100.0
        active_closes.append(active_close)
        active_highs.append(active_high)
        active_lows.append(active_low)
        stock_daily_returns.append(stock_daily)
        active_daily_returns.append(active_daily)
        gaps.append(gap)
        running_mfe = max(active_highs)
        running_mae = min(active_lows)
        time_to_mfe = int(np.argmax(active_highs) + 1)
        time_to_mae = int(np.argmin(active_lows) + 1)
        if active_close > peak_close:
            peak_close = active_close
            peak_close_day = day_index
            underwater_duration = 0
        else:
            underwater_duration += 1
        below_zero_duration = below_zero_duration + 1 if active_close < 0 else 0
        realized_vol = float(np.std(np.asarray(stock_daily_returns) / 100.0, ddof=1) * np.sqrt(252.0) * 100.0) if len(stock_daily_returns) > 1 else 0.0
        active_realized_vol = float(np.std(np.asarray(active_daily_returns) / 100.0, ddof=1) * np.sqrt(252.0) * 100.0) if len(active_daily_returns) > 1 else 0.0
        rows.append({
            "stage2e_candidate_id": row["stage2e_candidate_id"],
            "benchmark": benchmark,
            "symbol": symbol,
            "event_family": row.get("event_family", ""),
            "mapping_type": row.get("mapping_type", ""),
            "stage2c_oof_panel": row.get("stage2c_oof_panel", ""),
            "stage2c_oof_family": row.get("stage2c_oof_family", ""),
            "entry_date": entry_date,
            "candidate_t_e": t_e,
            "legal_holding_day": day_index,
            "path_date": date,
            "stock_open": open_price,
            "stock_high": high,
            "stock_low": low,
            "stock_close": close,
            "benchmark_close": float(benchmark_close),
            "stock_return_pct": stock_return,
            "benchmark_return_pct": benchmark_return,
            "active_return_pct": active_close,
            "active_high_return_pct": active_high,
            "active_low_return_pct": active_low,
            "running_mfe_active_pct": running_mfe,
            "running_mae_active_pct": running_mae,
            "time_to_mfe_days": time_to_mfe,
            "time_to_mae_days": time_to_mae,
            "running_peak_active_close_pct": peak_close,
            "running_peak_close_day": peak_close_day,
            "running_peak_giveback_pct": peak_close - active_close,
            "duration_underwater_days": underwater_duration,
            "duration_below_zero_days": below_zero_duration,
            "recovery_from_observed_trough_pct": active_close - running_mae,
            "fraction_positive_active_days": float(np.mean(np.asarray(active_closes) > 0.0)),
            "stock_realized_vol_annualized_pct": realized_vol,
            "active_realized_vol_annualized_pct": active_realized_vol,
            "overnight_gap_pct": gap,
            "max_abs_gap_pct": float(np.max(np.abs(gaps))),
            "gap_count_abs_ge_2pct": int(np.sum(np.abs(gaps) >= 2.0)),
            "path_entry_source": path_entry_source,
            "kernel_executable": seed is not None,
            "te_is_never_exit": True,
        })
        previous_stock_close = close
        previous_benchmark_close = float(benchmark_close)

    if not rows:
        return [], {
            "stage2e_candidate_id": row["stage2e_candidate_id"],
            "benchmark": benchmark,
            "symbol": symbol,
            "kernel_executable": seed is not None,
            "path_available": False,
            "path_entry_source": path_entry_source,
        }
    summary = _summarize_path(row, rows, seed, prices)
    return rows, summary


def _sign_changes(values: list[float]) -> int:
    signs = np.sign(np.asarray(values, dtype=float))
    signs = signs[signs != 0]
    return int(np.sum(signs[1:] != signs[:-1])) if len(signs) > 1 else 0


def _classify_path(path: pd.DataFrame) -> str:
    if path.empty:
        return "path_unavailable"
    terminal = float(path.iloc[-1]["active_return_pct"])
    day2 = float(path.iloc[min(1, len(path) - 1)]["active_return_pct"])
    first4 = path.iloc[: min(4, len(path))]
    early_mfe = float(first4["running_mfe_active_pct"].max())
    early_mae = float(first4["running_mae_active_pct"].min())
    terminal_mfe = float(path.iloc[-1]["running_mfe_active_pct"])
    terminal_mae = float(path.iloc[-1]["running_mae_active_pct"])
    positive_fraction = float(path.iloc[-1]["fraction_positive_active_days"])
    time_to_mfe = int(path.iloc[-1]["time_to_mfe_days"])
    changes = _sign_changes(path["active_return_pct"].tolist())
    if early_mfe >= 2.0 and (terminal <= 0.0 or terminal <= 0.25 * early_mfe):
        return "early_winner_with_giveback"
    if early_mae <= -2.0 and (terminal > 0.0 or terminal - early_mae >= 0.75 * abs(early_mae)):
        return "early_loser_with_recovery"
    if day2 >= 2.0 and terminal > 0.0:
        return "immediate_winner"
    if terminal >= 1.0 and day2 <= 0.0 and time_to_mfe > 2:
        return "delayed_winner"
    if terminal <= -1.0 and positive_fraction <= 0.25:
        return "persistent_loser"
    if abs(terminal) < 0.75 and terminal_mfe - terminal_mae < 2.0:
        return "flat_nonresponsive"
    if terminal_mfe - terminal_mae >= 4.0 and changes >= 2:
        return "volatile_oscillation"
    return "unclassified_mixed"


def _summarize_path(row: pd.Series, rows: list[dict[str, Any]], seed: dict[str, Any] | None, prices: dict) -> dict[str, Any]:
    path = pd.DataFrame(rows)
    terminal = path.iloc[-1]
    first4 = path.iloc[: min(4, len(path))]
    entry_date = as_utc_day(path.iloc[0]["entry_date"])
    entry_price = float(seed["entry_price"]) if seed is not None else float(row["entry_price"])
    atr_pct = _pre_entry_atr_pct(prices, str(row["symbol"]), entry_date, entry_price) * 100.0
    summary = {
        "stage2e_candidate_id": row["stage2e_candidate_id"],
        "benchmark": row["benchmark"],
        "symbol": row["symbol"],
        "event_family": row.get("event_family", ""),
        "mapping_type": row.get("mapping_type", ""),
        "stage2c_oof_panel": row.get("stage2c_oof_panel", ""),
        "stage2c_oof_family": row.get("stage2c_oof_family", ""),
        "kernel_executable": seed is not None,
        "path_available": True,
        "path_entry_source": terminal["path_entry_source"],
        "actual_entry_date": entry_date,
        "actual_entry_price": entry_price,
        "legal_te1_exit_date": terminal["path_date"],
        "legal_holding_days": int(terminal["legal_holding_day"]),
        "terminal_stock_return_pct": float(terminal["stock_return_pct"]),
        "terminal_benchmark_return_pct": float(terminal["benchmark_return_pct"]),
        "terminal_active_return_pct": float(terminal["active_return_pct"]),
        "terminal_mfe_active_pct": float(terminal["running_mfe_active_pct"]),
        "terminal_mae_active_pct": float(terminal["running_mae_active_pct"]),
        "terminal_peak_giveback_pct": float(terminal["running_peak_giveback_pct"]),
        "time_to_mfe_days": int(terminal["time_to_mfe_days"]),
        "time_to_mae_days": int(terminal["time_to_mae_days"]),
        "terminal_duration_underwater_days": int(terminal["duration_underwater_days"]),
        "terminal_recovery_from_trough_pct": float(terminal["recovery_from_observed_trough_pct"]),
        "fraction_positive_active_days": float(terminal["fraction_positive_active_days"]),
        "stock_realized_vol_annualized_pct": float(terminal["stock_realized_vol_annualized_pct"]),
        "active_realized_vol_annualized_pct": float(terminal["active_realized_vol_annualized_pct"]),
        "max_abs_gap_pct": float(terminal["max_abs_gap_pct"]),
        "gap_count_abs_ge_2pct": int(terminal["gap_count_abs_ge_2pct"]),
        "first4_mfe_active_pct": float(first4["running_mfe_active_pct"].max()),
        "first4_mae_active_pct": float(first4["running_mae_active_pct"].min()),
        "pre_entry_atr20_pct": atr_pct,
        "reference_exit_date": as_utc_day(seed["exit_date"]) if seed is not None else pd.NaT,
        "reference_exit_price": float(seed["exit_price"]) if seed is not None else np.nan,
        "reference_exit_reason": seed.get("exit_reason", "") if seed is not None else "",
        "te_is_never_exit": True,
    }
    summary["path_class"] = _classify_path(path)
    for horizon in (1, 2, 3, 4, 5, 8):
        selected = path[path["legal_holding_day"].eq(horizon)]
        summary[f"day_{horizon}_active_return_pct"] = float(selected.iloc[0]["active_return_pct"]) if not selected.empty else np.nan
    return summary


def _choose_exit(policy: str, path: pd.DataFrame, summary: pd.Series | dict[str, Any]) -> dict[str, Any]:
    if path.empty:
        return {"exit_date": pd.NaT, "exit_price": np.nan, "exit_reason": "path_unavailable"}
    if policy == "corrected_reference_exit":
        return {
            "exit_date": as_utc_day(summary["reference_exit_date"]),
            "exit_price": float(summary["reference_exit_price"]),
            "exit_reason": str(summary["reference_exit_reason"]),
        }
    if policy == "hold_to_te1":
        selected = path.iloc[-1]
    elif policy.startswith("fixed_"):
        horizon = int(policy.split("_")[1])
        selected = path.iloc[min(horizon, len(path)) - 1]
    elif policy == "volatility_scaled_stop":
        stop_pct = min(max(2.0, 2.0 * float(summary["pre_entry_atr20_pct"])), 8.0)
        eligible = path[(path["legal_holding_day"] >= 2) & (path["stock_return_pct"] <= -stop_pct)]
        selected = eligible.iloc[0] if not eligible.empty else path.iloc[-1]
    elif policy == "time_underwater_exit":
        eligible = path[(path["legal_holding_day"] >= 4) & (path["duration_below_zero_days"] >= 4)]
        selected = eligible.iloc[0] if not eligible.empty else path.iloc[-1]
    elif policy == "trailing_profit_giveback_exit":
        activated = path["running_peak_active_close_pct"] >= 3.0
        required_giveback = np.maximum(1.5, 0.5 * path["running_peak_active_close_pct"])
        eligible = path[activated & (path["running_peak_giveback_pct"] >= required_giveback)]
        selected = eligible.iloc[0] if not eligible.empty else path.iloc[-1]
    else:
        raise ValueError(f"Unknown Stage 2E exit policy: {policy}")
    return {
        "exit_date": as_utc_day(selected["path_date"]),
        "exit_price": float(selected["stock_close"]),
        "exit_reason": f"stage2e_{policy}",
    }


def _build_paths_and_exit_plans(
    all_development: pd.DataFrame,
    stream: pd.DataFrame,
    prices: dict,
    probs: dict,
    output_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    path_dir = output_dir / "legal_paths"
    path_dir.mkdir(parents=True, exist_ok=True)
    path_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for _, row in all_development.iterrows():
        seed = _kernel_seed(row, prices, probs)
        rows, summary = _build_candidate_path(row, prices, seed)
        path_rows.extend(rows)
        summaries.append(summary)
    paths = pd.DataFrame(path_rows)
    path_summary = pd.DataFrame(summaries)
    paths.to_csv(path_dir / "candidate_legal_path_table.csv", index=False)
    path_summary.to_csv(path_dir / "candidate_path_summary.csv", index=False)

    stream_summary = stream[["stage2e_candidate_id"]].merge(path_summary, on="stage2e_candidate_id", how="left", suffixes=("", "_path"))
    plans: list[dict[str, Any]] = []
    grouped_paths = {key: group.sort_values("legal_holding_day") for key, group in paths.groupby("stage2e_candidate_id")}
    for _, summary in stream_summary.iterrows():
        candidate_id = summary["stage2e_candidate_id"]
        path = grouped_paths.get(candidate_id, pd.DataFrame())
        if not _as_bool(summary.get("kernel_executable", False)) or path.empty:
            continue
        for policy in EXIT_POLICIES:
            plan = _choose_exit(policy, path, summary)
            candidate_te = as_utc_day(path.iloc[0]["candidate_t_e"])
            if pd.isna(plan["exit_date"]) or as_utc_day(plan["exit_date"]) >= candidate_te:
                raise AssertionError(f"Stage 2E planned an exit at or after T_e for {candidate_id}/{policy}")
            plans.append({
                "stage2e_candidate_id": candidate_id,
                "exit_policy": policy,
                "planned_exit_date": plan["exit_date"],
                "planned_exit_price": plan["exit_price"],
                "planned_exit_reason": plan["exit_reason"],
                "candidate_t_e": candidate_te,
                "exit_strictly_before_te": True,
            })
    exit_plans = pd.DataFrame(plans)
    exit_plans.to_csv(path_dir / "candidate_exit_plans.csv", index=False)
    return paths, path_summary, exit_plans


def _semantic_indirect_accept(trade: dict[str, Any], context: dict[str, Any]) -> bool:
    if int(context.get("free_slots", 0)) <= 0:
        return False
    valid = _as_bool(trade.get("mapping_valid", False)) and float(trade.get("mapping_confidence", 0) or 0) >= 3.0
    top_mapping = int(float(trade.get("semantic_event_rank", 10**9))) == 0
    prior = pd.to_numeric(trade.get("event_candidates_seen_previous_5_days"), errors="coerce")
    extension = pd.to_numeric(trade.get("stock_minus_sector_20d"), errors="coerce")
    novel = bool(np.isfinite(prior) and float(prior) <= 0.0)
    repricing_ok = bool(np.isfinite(extension) and abs(float(extension)) <= 0.10)
    return valid and top_mapping and (novel or repricing_ok)


def _selector_accept(selector: str) -> Callable[[dict, pd.Timestamp, dict[str, Any]], str]:
    def decide(trade: dict, _day: pd.Timestamp, context: dict[str, Any]) -> str:
        if int(context.get("free_slots", 0)) <= 0:
            return "reject"
        if str(trade.get("mapping_type")) != "direct_issuer":
            return "accept" if _semantic_indirect_accept(trade, context) else "reject"
        if not _as_bool(trade.get("mapping_valid", False)):
            return "reject"
        if selector in {"direct_always_fill", "expected_slot_days_ranking"}:
            return "accept"
        if selector == "predicted_target_a_positive":
            value = pd.to_numeric(trade.get("predicted_target_a"), errors="coerce")
            return "accept" if np.isfinite(value) and float(value) > 0 else "reject"
        if selector == "target_b_per_slot_day":
            value = pd.to_numeric(trade.get("predicted_target_b_slot"), errors="coerce")
            return "accept" if np.isfinite(value) and float(value) > 0 else "reject"
        if selector == "corrected_reference_selector":
            value = pd.to_numeric(trade.get("legacy_gemini_relevance_score"), errors="coerce")
            return "accept" if np.isfinite(value) and float(value) >= 1.0 else "reject"
        raise ValueError(selector)
    return decide


def _rank_selector(frame: pd.DataFrame, selector: str) -> pd.DataFrame:
    out = frame.copy()
    out["_selector_rank"] = 10**9
    for _, day in out.groupby(["benchmark", "analysis_split", "entry_date"], sort=False):
        ranked = day.copy()
        direct = ranked["mapping_type"].eq("direct_issuer")
        ranked["_primary"] = 0.0
        ranked["_secondary"] = pd.to_numeric(ranked.get("expected_slot_days"), errors="coerce").fillna(10**6)
        if selector == "direct_always_fill":
            ranked["_secondary"] = pd.to_numeric(ranked.get("source_order"), errors="coerce").fillna(10**9)
        elif selector == "predicted_target_a_positive":
            ranked.loc[direct, "_primary"] = pd.to_numeric(ranked.loc[direct, "predicted_target_a"], errors="coerce").fillna(-10**6)
        elif selector == "target_b_per_slot_day":
            ranked.loc[direct, "_primary"] = pd.to_numeric(ranked.loc[direct, "predicted_target_b_slot"], errors="coerce").fillna(-10**6)
        elif selector == "expected_slot_days_ranking":
            ranked["_primary"] = -ranked["_secondary"]
        elif selector == "corrected_reference_selector":
            ranked.loc[direct, "_primary"] = pd.to_numeric(ranked.loc[direct, "legacy_gemini_relevance_score"], errors="coerce").fillna(-10**6)
        else:
            raise ValueError(selector)
        order = ranked.sort_values(
            ["_primary", "_secondary", "semantic_event_rank", "source_order", "symbol"],
            ascending=[False, True, True, True, True],
            kind="mergesort",
        ).index
        out.loc[order, "_selector_rank"] = np.arange(len(order), dtype=int)
    out["_selector_rank"] = out["_selector_rank"].astype(int)
    out["_admission_score"] = pd.to_numeric(out["legacy_gemini_relevance_score"], errors="coerce")
    out["stage2e_selector"] = selector
    return out


def _turnover(trades: pd.DataFrame, equity: pd.DataFrame) -> tuple[float, float]:
    if trades.empty:
        return 0.0, 0.0
    entry = pd.to_numeric(trades.get("_asset_entry_notional"), errors="coerce").fillna(0.0).sum()
    exit_value = pd.to_numeric(trades.get("exit_value"), errors="coerce").fillna(0.0).sum()
    notional = float(entry + exit_value)
    mean_equity = float(pd.to_numeric(equity.get("equity"), errors="coerce").mean()) if not equity.empty else INITIAL_CAPITAL
    return notional, notional / max(mean_equity, 1e-12)


def _exact_replay(
    frame: pd.DataFrame,
    prices: dict,
    probs: dict,
    benchmark: str,
    selector: str,
    exit_policy: str,
    output_dir: Path | None,
    metadata: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    subset = frame[frame["benchmark"].eq(benchmark)].copy()
    if subset.empty:
        return {**metadata, "benchmark": benchmark, "selector": selector, "exit_policy": exit_policy, "n_trades": 0}, pd.DataFrame()
    subset["stage2e_exit_policy"] = exit_policy
    start = pd.to_datetime(subset["t_theta"], utc=True).min().normalize()
    end = pd.to_datetime(subset["t_e"], utc=True).max().normalize()
    trades, equity, stats, _policy, allocation, disposition = sim_opp_cost(
        subset,
        prices,
        probs,
        dict(PORT_DEFAULT),
        bench_sym=benchmark,
        initial=INITIAL_CAPITAL,
        start_date=start,
        end_date=end,
        allocation_mode=ALLOCATION_FIFO,
        collect_allocation_log=True,
        admission_policy=_selector_accept(selector),
        exit_plan_columns=("_stage2e_exit_date", "_stage2e_exit_price", "_stage2e_exit_reason"),
    )
    if not trades.empty:
        exits = pd.to_datetime(trades["exit_date"], errors="coerce", utc=True).dt.normalize()
        tes = pd.to_datetime(trades["candidate_t_e"], errors="coerce", utc=True).dt.normalize()
        if not (exits < tes).all():
            raise AssertionError("Stage 2E exact replay generated exit_date >= candidate_t_e")
    turnover_notional, turnover_x = _turnover(trades, equity)
    result = {
        **metadata,
        "benchmark": benchmark,
        "selector": selector,
        "exit_policy": exit_policy,
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
        "lockbox_opened": False,
        "te_is_never_exit": True,
    }
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        trades.to_csv(output_dir / f"trades_{benchmark.lower()}.csv", index=False)
        equity.to_csv(output_dir / f"equity_{benchmark.lower()}.csv", index=False)
        allocation.to_csv(output_dir / f"allocation_{benchmark.lower()}.csv", index=False)
        disposition.to_csv(output_dir / f"disposition_{benchmark.lower()}.csv", index=False)
    return result, trades


def _attach_exit_plan(frame: pd.DataFrame, plans: pd.DataFrame, exit_policy: str) -> pd.DataFrame:
    selected = plans[plans["exit_policy"].eq(exit_policy)][
        ["stage2e_candidate_id", "planned_exit_date", "planned_exit_price", "planned_exit_reason"]
    ].copy()
    selected = selected.rename(columns={
        "planned_exit_date": "_stage2e_exit_date",
        "planned_exit_price": "_stage2e_exit_price",
        "planned_exit_reason": "_stage2e_exit_reason",
    })
    out = frame.drop(columns=["_stage2e_exit_date", "_stage2e_exit_price", "_stage2e_exit_reason"], errors="ignore").merge(
        selected, on="stage2e_candidate_id", how="left", validate="one_to_one"
    )
    return out


def _run_matrix(
    stream: pd.DataFrame,
    plans: pd.DataFrame,
    path_summary: pd.DataFrame,
    prices: dict,
    probs: dict,
    output_dir: Path,
) -> dict[str, pd.DataFrame]:
    matrix_dir = output_dir / "selector_exit_matrix"
    matrix_dir.mkdir(parents=True, exist_ok=True)
    class_lookup = path_summary[["stage2e_candidate_id", "path_class"]].drop_duplicates("stage2e_candidate_id")
    combined_rows: list[dict[str, Any]] = []
    combined_trades: list[pd.DataFrame] = []
    fold_rows: list[dict[str, Any]] = []
    fold_trades: list[pd.DataFrame] = []

    for selector in SELECTORS:
        ranked = _rank_selector(stream, selector)
        for exit_policy in EXIT_POLICIES:
            planned = _attach_exit_plan(ranked, plans, exit_policy)
            for benchmark in ("SPY", "QQQ"):
                result, trades = _exact_replay(
                    planned,
                    prices,
                    probs,
                    benchmark,
                    selector,
                    exit_policy,
                    matrix_dir / "combined" / selector / exit_policy,
                    {"evaluation_scope": "combined_all_family_oof_exact_replay"},
                )
                combined_rows.append(result)
                if not trades.empty:
                    detail = trades.merge(class_lookup, on="stage2e_candidate_id", how="left")
                    detail["selector"] = selector
                    detail["exit_policy"] = exit_policy
                    detail["benchmark"] = benchmark
                    combined_trades.append(detail)

            for panel_id, panel in planned.groupby("stage2c_oof_panel", sort=True):
                panel_family = str(panel["stage2c_oof_family"].iloc[0])
                event_composition = json.dumps(panel["event_family"].value_counts().to_dict(), sort_keys=True)
                for benchmark in ("SPY", "QQQ"):
                    result, trades = _exact_replay(
                        panel,
                        prices,
                        probs,
                        benchmark,
                        selector,
                        exit_policy,
                        None,
                        {
                            "evaluation_scope": "outer_panel_exact_replay",
                            "oof_panel": panel_id,
                            "oof_family": panel_family,
                            "event_family_composition": event_composition,
                            "validation_start": panel["entry_date"].min(),
                            "validation_end": panel["entry_date"].max(),
                        },
                    )
                    fold_rows.append(result)
                    if not trades.empty:
                        detail = trades[[column for column in (
                            "stage2e_candidate_id", "symbol", "entry_date", "exit_date", "pnl", "pnl_pct",
                            "event_family", "mapping_type", "stage2c_oof_panel", "stage2c_oof_family"
                        ) if column in trades]].copy()
                        detail["selector"] = selector
                        detail["exit_policy"] = exit_policy
                        detail["benchmark"] = benchmark
                        detail["oof_panel"] = panel_id
                        fold_trades.append(detail)

    combined = pd.DataFrame(combined_rows)
    folds = pd.DataFrame(fold_rows)
    trades = pd.concat(combined_trades, ignore_index=True) if combined_trades else pd.DataFrame()
    panel_trades = pd.concat(fold_trades, ignore_index=True) if fold_trades else pd.DataFrame()
    combined.to_csv(matrix_dir / "selector_exit_combined_exact_results.csv", index=False)
    folds.to_csv(matrix_dir / "selector_exit_fold_exact_results.csv", index=False)
    trades.to_csv(matrix_dir / "selector_exit_combined_trade_detail.csv", index=False)
    panel_trades.to_csv(matrix_dir / "selector_exit_fold_trade_detail.csv", index=False)

    family = (
        trades.groupby(["selector", "exit_policy", "benchmark", "event_family"], dropna=False, as_index=False)
        .agg(
            trade_count=("symbol", "size"),
            net_pnl=("pnl", "sum"),
            mean_trade_pnl_pct=("pnl_pct", "mean"),
            winning_trade_rate=("pnl", lambda values: float((pd.to_numeric(values, errors="coerce") > 0).mean())),
        )
        if not trades.empty else pd.DataFrame()
    )
    family.to_csv(matrix_dir / "selector_exit_results_by_event_family.csv", index=False)
    return {"combined": combined, "folds": folds, "trades": trades, "family": family}


def _rank_stability(combined: pd.DataFrame, output_dir: Path) -> dict[str, Any]:
    ranked = combined.copy()
    group_columns = ["exit_policy", "benchmark"]
    ranked["rank_excess"] = ranked.groupby(group_columns)["excess_return"].rank(method="average", ascending=False)
    ranked["rank_active_ir"] = ranked.groupby(group_columns)["active_information_ratio"].rank(method="average", ascending=False)
    ranked["rank_active_drawdown"] = ranked.groupby(group_columns)["active_max_drawdown_pct"].rank(method="average", ascending=False)
    ranked["composite_selector_rank"] = ranked[["rank_excess", "rank_active_ir", "rank_active_drawdown"]].mean(axis=1)

    aggregate = (
        ranked.groupby("selector", as_index=False)
        .agg(
            median_composite_rank=("composite_selector_rank", "median"),
            q75_composite_rank=("composite_selector_rank", lambda values: float(np.quantile(values, 0.75))),
            worst_composite_rank=("composite_selector_rank", "max"),
            mean_excess_return=("excess_return", "mean"),
            median_excess_return=("excess_return", "median"),
            mean_active_ir=("active_information_ratio", "mean"),
            mean_active_drawdown_pct=("active_max_drawdown_pct", "mean"),
        )
    )
    aggregate["robust_rank_score"] = 0.5 * aggregate["median_composite_rank"] + 0.5 * aggregate["q75_composite_rank"]
    aggregate = aggregate.sort_values(
        ["robust_rank_score", "median_excess_return", "mean_active_ir", "mean_active_drawdown_pct"],
        ascending=[True, False, False, False],
        kind="mergesort",
    ).reset_index(drop=True)
    aggregate["robust_selector_order"] = np.arange(1, len(aggregate) + 1)

    by_policy = (
        ranked.groupby(["exit_policy", "selector"], as_index=False)
        .agg(mean_composite_rank=("composite_selector_rank", "mean"), mean_excess_return=("excess_return", "mean"))
    )
    by_policy["selector_rank_within_exit"] = by_policy.groupby("exit_policy")["mean_composite_rank"].rank(method="average")
    winners = (
        by_policy.sort_values(["exit_policy", "mean_composite_rank", "mean_excess_return"], ascending=[True, True, False], kind="mergesort")
        .groupby("exit_policy", as_index=False).first()
    )
    winner_counts = winners["selector"].value_counts()
    dominant_share = float(winner_counts.max() / len(winners)) if len(winners) else 0.0

    pivot = by_policy.pivot(index="exit_policy", columns="selector", values="mean_composite_rank")
    correlations = pivot.T.corr(method="spearman")
    correlation_rows: list[dict[str, Any]] = []
    for left_index, left in enumerate(correlations.index):
        for right in correlations.index[left_index + 1:]:
            correlation_rows.append({"exit_policy_left": left, "exit_policy_right": right, "spearman_selector_rank": correlations.loc[left, right]})
    correlation_table = pd.DataFrame(correlation_rows)
    finite_correlations = pd.to_numeric(correlation_table.get("spearman_selector_rank"), errors="coerce").dropna()
    median_correlation = float(finite_correlations.median()) if not finite_correlations.empty else 1.0
    material_change = bool(winners["selector"].nunique() > 1 and (dominant_share < 0.75 or median_correlation < 0.60))
    robust_selector = str(aggregate.iloc[0]["selector"])

    stability_dir = output_dir / "robustness"
    stability_dir.mkdir(parents=True, exist_ok=True)
    ranked.to_csv(stability_dir / "selector_ranks_by_exit_and_benchmark.csv", index=False)
    aggregate.to_csv(stability_dir / "robust_selector_summary.csv", index=False)
    by_policy.to_csv(stability_dir / "selector_rank_by_exit_policy.csv", index=False)
    winners.to_csv(stability_dir / "best_selector_by_exit_policy.csv", index=False)
    correlation_table.to_csv(stability_dir / "selector_rank_correlation_across_exit_policies.csv", index=False)
    return {
        "ranked": ranked,
        "aggregate": aggregate,
        "by_policy": by_policy,
        "winners": winners,
        "correlations": correlation_table,
        "robust_selector": robust_selector,
        "dominant_winner_share": dominant_share,
        "median_pairwise_spearman": median_correlation,
        "selector_changes_materially": material_change,
    }


def _best_exit_for_selector(ranked: pd.DataFrame, selector: str) -> str:
    selected = ranked[ranked["selector"].eq(selector)].copy()
    exit_scores = selected.groupby("exit_policy", as_index=False).agg(
        mean_composite_rank=("composite_selector_rank", "mean"),
        mean_excess_return=("excess_return", "mean"),
        mean_active_ir=("active_information_ratio", "mean"),
        mean_active_drawdown_pct=("active_max_drawdown_pct", "mean"),
    )
    # Across exits for one selector, metrics are ranked again so scale does not
    # let return dominate IR/drawdown.
    for metric, column in (
        ("mean_excess_return", "rank_excess"),
        ("mean_active_ir", "rank_ir"),
        ("mean_active_drawdown_pct", "rank_drawdown"),
    ):
        exit_scores[column] = exit_scores[metric].rank(method="average", ascending=False)
    exit_scores["exit_composite"] = exit_scores[["rank_excess", "rank_ir", "rank_drawdown"]].mean(axis=1)
    return str(exit_scores.sort_values(["exit_composite", "mean_excess_return"], ascending=[True, False], kind="mergesort").iloc[0]["exit_policy"])


def _alternating_pass(stability: dict[str, Any], output_dir: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    ranked = stability["ranked"]
    selector = stability["robust_selector"]
    exit_policy = _best_exit_for_selector(ranked, selector)
    rows.append({"iteration": 0, "frozen_side": "all_predeclared_exit_baselines", "selected_selector": selector, "selected_exit_policy": exit_policy})
    if stability["selector_changes_materially"]:
        for iteration in (1, 2):
            frozen_exit = stability["by_policy"][stability["by_policy"]["exit_policy"].eq(exit_policy)].copy()
            selector = str(frozen_exit.sort_values(["mean_composite_rank", "mean_excess_return"], ascending=[True, False], kind="mergesort").iloc[0]["selector"])
            exit_policy = _best_exit_for_selector(ranked, selector)
            rows.append({"iteration": iteration, "frozen_side": "exit_then_selector", "selected_selector": selector, "selected_exit_policy": exit_policy})
    result = pd.DataFrame(rows)
    result.to_csv(output_dir / "robustness" / "development_only_alternating_pass.csv", index=False)
    return result


def _path_diagnostics(path_summary: pd.DataFrame, output_dir: Path) -> dict[str, pd.DataFrame]:
    diagnostics_dir = output_dir / "path_diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    available = path_summary[path_summary["path_available"].map(_as_bool)].copy()
    class_summary = (
        available.groupby(["benchmark", "event_family", "path_class"], dropna=False, as_index=False)
        .agg(
            candidates=("stage2e_candidate_id", "size"),
            mean_terminal_active_pct=("terminal_active_return_pct", "mean"),
            mean_mfe_active_pct=("terminal_mfe_active_pct", "mean"),
            mean_mae_active_pct=("terminal_mae_active_pct", "mean"),
            mean_legal_holding_days=("legal_holding_days", "mean"),
        )
    )
    class_summary.to_csv(diagnostics_dir / "path_class_summary.csv", index=False)

    reversals: list[dict[str, Any]] = []
    for _, row in available.iterrows():
        terminal = float(row["terminal_active_return_pct"])
        for horizon in (2, 4, 8):
            early = row.get(f"day_{horizon}_active_return_pct", np.nan)
            if not np.isfinite(early) or early == 0 or terminal == 0 or np.sign(early) == np.sign(terminal):
                continue
            reversals.append({
                "stage2e_candidate_id": row["stage2e_candidate_id"],
                "benchmark": row["benchmark"],
                "symbol": row["symbol"],
                "event_family": row["event_family"],
                "path_class": row["path_class"],
                "early_horizon_days": horizon,
                "early_active_return_pct": early,
                "te1_active_return_pct": terminal,
                "reversal": "early_positive_to_terminal_negative" if early > 0 else "early_negative_to_terminal_positive",
            })
    reversal_table = pd.DataFrame(reversals)
    reversal_table.to_csv(diagnostics_dir / "early_vs_te1_classification_reversals.csv", index=False)
    early_mfe_negative = available[(available["first4_mfe_active_pct"] >= 2.0) & (available["terminal_active_return_pct"] < 0)].copy()
    terminal_winner_drawdown = available[(available["terminal_active_return_pct"] > 0) & (available["terminal_mae_active_pct"] <= -5.0)].copy()
    early_mfe_negative.to_csv(diagnostics_dir / "early_mfe_finish_negative.csv", index=False)
    terminal_winner_drawdown.to_csv(diagnostics_dir / "terminal_winner_severe_interim_drawdown.csv", index=False)
    return {
        "class_summary": class_summary,
        "reversals": reversal_table,
        "early_mfe_negative": early_mfe_negative,
        "terminal_winner_drawdown": terminal_winner_drawdown,
    }


def _preserve_stage3_audits(output_dir: Path) -> dict[str, Any]:
    stage2b_184 = STAGE2C / "baselines" / "stage3_raw_global_connection_baseline" / "stage3_exit_development_trades.csv"
    stage2c_138 = STAGE3 / "stage3_exit_development_trades.csv"
    entries = []
    for label, path, expected in (
        ("stage2b_184_trade_sample", stage2b_184, 184),
        ("stage2c_138_trade_sample", stage2c_138, 138),
    ):
        rows = len(pd.read_csv(path))
        if rows != expected:
            raise AssertionError(f"{label} expected {expected} rows, found {rows}")
        entries.append({"label": label, "path": str(path), "rows": rows, "sha256": _hash(path), "role": "audit_baseline_only", "exit_model_training_used": False})
    manifest = {
        "label": "stage2e_preserved_stage3_audit_baselines",
        "samples": entries,
        "samples_overwritten": False,
        "exit_model_training_performed": False,
        "lockbox_opened": False,
    }
    _json(output_dir / "audit_baselines_manifest.json", manifest)
    return manifest


def _freeze_selector(stability: dict[str, Any], alternating: pd.DataFrame, output_dir: Path) -> dict[str, Any]:
    selector = stability["robust_selector"]
    selector_spec = {
        "direct_always_fill": {"direct_ranking": "source order", "direct_admission": "always fill"},
        "predicted_target_a_positive": {"direct_ranking": "OOF predicted Target A descending then expected slot days", "direct_admission": "OOF predicted Target A > 0"},
        "target_b_per_slot_day": {"direct_ranking": "OOF predicted active return per slot-day descending then expected slot days", "direct_admission": "OOF predicted Target B per slot-day > 0"},
        "expected_slot_days_ranking": {"direct_ranking": "expected slot days ascending", "direct_admission": "always fill"},
        "corrected_reference_selector": {"direct_ranking": "legacy connection descending then expected slot days", "direct_admission": "legacy connection >= 1.00"},
    }[selector]
    manifest = {
        "label": "research_frozen_selector_stage2e",
        "selected_performance_selector": selector,
        **selector_spec,
        "selection_rule": "minimum 50% median plus 50% 75th-percentile composite rank across all eight predeclared exits and both benchmarks; composite equally ranks excess return, active IR, and less-negative active drawdown",
        "semantic_stage2c_conclusions_frozen": True,
        "direct_mapping_eligibility": "mapping_valid direct_issuer",
        "indirect_mapping_eligibility_and_ranking": "unchanged Stage 2C semantic lane: mapping confidence >=3, event-relative semantic rank, novelty/repricing guard",
        "selector_changes_materially_across_exit_policies": stability["selector_changes_materially"],
        "dominant_exit_policy_winner_share": stability["dominant_winner_share"],
        "median_pairwise_exit_policy_selector_rank_spearman": stability["median_pairwise_spearman"],
        "alternating_pass_performed": bool(stability["selector_changes_materially"]),
        "alternating_pass_final_diagnostic_selector": str(alternating.iloc[-1]["selected_selector"]),
        "alternating_pass_final_diagnostic_exit": str(alternating.iloc[-1]["selected_exit_policy"]),
        "exit_policy_frozen_for_learned_exit_research": False,
        "selected_target_label_is_terminal_derived": selector in {"predicted_target_a_positive", "target_b_per_slot_day"},
        "terminal_label_caveat": "Target B remains T_e-1 active return divided by slot days; it is frozen only because its OOF selector survived the cross-exit matrix, not because the training label became path-aware",
        "predicted_target_a_positive_stage2c_freeze_superseded": True,
        "exit_model_training_performed": False,
        "lockbox_opened": False,
        "lockbox_reserved_for_final_modular_pipeline_evaluation": True,
        "te_is_never_exit": True,
        "latest_legal_exit_horizon": "T_e - 1",
    }
    path = output_dir / "research_frozen_selector_stage2e.json"
    _json(path, manifest)
    return {**manifest, "path": str(path)}


def _markdown_table(frame: pd.DataFrame, columns: list[str], digits: int = 4) -> str:
    if frame.empty:
        return "No observations."
    view = frame[[column for column in columns if column in frame]].copy()
    for column in view.select_dtypes(include=[np.number]).columns:
        view[column] = view[column].map(lambda value: "" if pd.isna(value) else f"{value:.{digits}f}")
    lines = ["| " + " | ".join(view.columns) + " |", "| " + " | ".join(["---"] * len(view.columns)) + " |"]
    lines.extend("| " + " | ".join(map(str, row)) + " |" for row in view.itertuples(index=False, name=None))
    return "\n".join(lines)


def _build_report(
    all_development: pd.DataFrame,
    stream: pd.DataFrame,
    path_summary: pd.DataFrame,
    matrix: dict[str, pd.DataFrame],
    diagnostics: dict[str, pd.DataFrame],
    stability: dict[str, Any],
    alternating: pd.DataFrame,
    frozen: dict[str, Any],
    output_dir: Path,
) -> Path:
    combined = matrix["combined"]
    result_table = _markdown_table(
        combined,
        ["selector", "exit_policy", "benchmark", "excess_return", "active_information_ratio", "active_max_drawdown_pct", "turnover_x_average_equity", "n_trades", "slot_usage_pct"],
    )
    robust_table = _markdown_table(
        stability["aggregate"],
        ["selector", "robust_rank_score", "median_composite_rank", "q75_composite_rank", "mean_excess_return", "mean_active_ir", "mean_active_drawdown_pct", "robust_selector_order"],
    )
    class_counts = path_summary["path_class"].value_counts(dropna=False).rename_axis("path_class").reset_index(name="candidates")
    class_table = _markdown_table(class_counts, ["path_class", "candidates"], digits=0)
    alternating_table = _markdown_table(alternating, ["iteration", "frozen_side", "selected_selector", "selected_exit_policy"], digits=0)
    frozen_selector = frozen["selected_performance_selector"]
    selected_folds = matrix["folds"][matrix["folds"]["selector"].eq(frozen_selector)]
    fold_summary = (
        selected_folds.groupby(["exit_policy", "benchmark"], as_index=False)
        .agg(
            panels=("oof_panel", "nunique"),
            mean_panel_excess=("excess_return", "mean"),
            median_panel_excess=("excess_return", "median"),
            positive_panel_share=("excess_return", lambda values: float((pd.to_numeric(values, errors="coerce") > 0).mean())),
            mean_panel_active_ir=("active_information_ratio", "mean"),
            mean_panel_active_drawdown_pct=("active_max_drawdown_pct", "mean"),
        )
    )
    fold_table = _markdown_table(
        fold_summary,
        ["exit_policy", "benchmark", "panels", "mean_panel_excess", "median_panel_excess", "positive_panel_share", "mean_panel_active_ir", "mean_panel_active_drawdown_pct"],
    )
    selected_family = matrix["family"][matrix["family"]["selector"].eq(frozen_selector)]
    family_summary = (
        selected_family.groupby(["benchmark", "event_family"], as_index=False)
        .agg(
            exit_policies=("exit_policy", "nunique"),
            mean_trade_count=("trade_count", "mean"),
            mean_net_pnl_across_exits=("net_pnl", "mean"),
            mean_trade_pnl_pct_across_exits=("mean_trade_pnl_pct", "mean"),
            mean_win_rate_across_exits=("winning_trade_rate", "mean"),
        )
    )
    family_table = _markdown_table(
        family_summary,
        ["benchmark", "event_family", "exit_policies", "mean_trade_count", "mean_net_pnl_across_exits", "mean_trade_pnl_pct_across_exits", "mean_win_rate_across_exits"],
    )
    report = f"""# Stage 2E — Path-Aware Selection–Exit Interaction

## Scope and legal horizon

Stage 2E used development data only: {len(all_development)} semantic candidates and {len(stream)} chronological OOF evaluation candidates. Test/lockbox rows read: 0. Stage 2C semantic mapping conclusions remain frozen, while the Stage 2C performance-selector freeze based on `predicted_target_a_positive` is superseded.

Every path and replay enforces `exit_date < T_e`. `T_e` is never an exit; `T_e - 1` is the latest legal horizon. The 184-trade Stage 2B and 138-trade Stage 2C Stage 3 samples remain unchanged and audit-only. No exit model was trained.

## Legal paths and descriptive classes

The long path table reports stock, benchmark, and active return on every legal holding day, running active MFE/MAE and their timing, peak giveback, underwater duration, trough recovery, positive-active-day fraction, realized volatility, and overnight gaps. Classes are fixed diagnostics and were not selected on a lockbox.

{class_table}

Early-vs-terminal sign reversals: {len(diagnostics['reversals'])}. Candidates with first-four-day active MFE >=2% that finish negative at `T_e-1`: {len(diagnostics['early_mfe_negative'])}. Terminal winners that suffered active MAE <=-5%: {len(diagnostics['terminal_winner_drawdown'])}.

## Exact selector × exit-policy matrix

Each of the 40 selector/exit combinations was replayed independently for SPY and QQQ. Every replay rebuilt its own capacity, capital, benchmark rotation, turnover, and later admissions. The same matrix was also replayed separately on every OOF panel for fold and event-family reporting.

{result_table}

The volatility stop uses only pre-entry ATR20 to scale its fixed threshold. The time-underwater and trailing-giveback exits are deterministic sequential rules; no threshold grid was searched.

## Fold and event-family stability

For the research-frozen selector, the exact outer-panel results are:

{fold_table}

SPY panel medians remain weaker than QQQ for several exits, so the combined-stream gains should not be interpreted as uniform temporal dominance. The complete 880-row file reports every selector, exit, benchmark, and OOF panel separately.

Event-family contribution for the frozen selector, averaged across the eight exit policies:

{family_table}

Geo remains the weak lane across both benchmarks. The strong long-horizon combined return is concentrated partly in the small `other` catalyst family; this concentration is retained as an explicit Stage 3 risk rather than used to alter the frozen Stage 2C semantic rules.

## Selector robustness across exits

{robust_table}

The best selector changed materially across exit policies: `{stability['selector_changes_materially']}`. Dominant exit-policy winner share: {stability['dominant_winner_share']:.1%}. Median pairwise Spearman correlation of selector ranks across exits: {stability['median_pairwise_spearman']:.3f}.

The robust rule gives equal weight to excess return, active IR, and less-negative active drawdown within each benchmark/exit cell, then minimizes 50% median plus 50% 75th-percentile rank across all cells. It therefore does not freeze a selector merely for winning hold-to-`T_e-1`.

Development-only alternating diagnostic:

{alternating_table}

## Stage 2E freeze and Stage 3 status

Research-frozen performance selector: `{frozen['selected_performance_selector']}`. Direct ranking: {frozen['direct_ranking']}. Direct admission: {frozen['direct_admission']}.

Target B itself remains a terminal-derived label (`T_e-1` active return divided by slot days). Stage 2E freezes the selector because its OOF decisions were comparatively robust across the predeclared exit matrix—not because Target B has become a path-aware or causal label.

The indirect semantic lane remains unchanged. No exit policy is frozen by this branch. Learned binary hold/close research may use the Stage 2E selector only after this interaction report is accepted; training remains paused in the generated Stage 3 status manifest. The later lockbox remains sealed for one final full-pipeline evaluation.
"""
    path = output_dir / "stage2e_path_aware_selection_exit_report.md"
    path.write_text(report, encoding="utf-8")
    return path


def _update_stage3_status(frozen: dict[str, Any], audits: dict[str, Any], report: Path) -> Path:
    sample_path = STAGE3 / "stage3_exit_development_trades.csv"
    folds_path = STAGE3 / "stage3_development_folds.csv"
    manifest = {
        "label": "stage3_exit_research_paused_after_stage2e",
        "performance_selector_status": "research_frozen_after_path_aware_interaction; training still paused pending review",
        "research_frozen_selector_stage2e": frozen,
        "stage2c_predicted_target_a_positive_is_frozen_selector": False,
        "preserved_audit_baselines": audits["samples"],
        "current_138_trade_file_role": "audit_baseline_only",
        "current_138_trade_file": str(sample_path),
        "current_138_trade_file_sha256": _hash(sample_path),
        "development_folds_file": str(folds_path),
        "stage2e_report": str(report),
        "exit_policy_trained": False,
        "exit_model_selection_performed": False,
        "exit_model_training_status": "paused",
        "lockbox_opened": False,
        "lockbox_reserved_for_final_modular_pipeline_evaluation": True,
        "te_is_never_exit": True,
        "latest_legal_exit_horizon": "T_e - 1",
    }
    manifest_path = STAGE3 / "stage3_exit_manifest.json"
    _json(manifest_path, manifest)
    plan = f"""# Stage 3 Exit Research — Paused After Stage 2E

Do not train on the existing 184-trade or 138-trade samples; both are audit baselines only.

The Stage 2C semantic conclusions remain frozen. Its `predicted_target_a_positive` performance-selector freeze was reopened because Target A used a terminal hold-to-`T_e-1` label. Stage 2E completed the development-only selector × exit interaction matrix and research-froze `{frozen['selected_performance_selector']}` as the performance selector.

No exit policy or learned binary hold/close model was trained. Review `{report}` before creating a new Stage 3 development sample under the Stage 2E selector.

All future exits must satisfy `exit_date < T_e`. `T_e` is never an exit and `T_e - 1` is the latest legal horizon. The later lockbox remains sealed for the final full modular pipeline evaluation.
"""
    (STAGE3 / "stage3_exit_research_plan.md").write_text(plan, encoding="utf-8")
    return manifest_path


def run_stage2e(output_dir: Path | str = OUTPUT) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    audits = _preserve_stage3_audits(output_dir)
    audit_hashes_before = {item["label"]: item["sha256"] for item in audits["samples"]}
    all_development, stream, prices, probs = _load_inputs()
    paths, path_summary, plans = _build_paths_and_exit_plans(all_development, stream, prices, probs, output_dir)
    matrix = _run_matrix(stream, plans, path_summary, prices, probs, output_dir)
    diagnostics = _path_diagnostics(path_summary, output_dir)
    stability = _rank_stability(matrix["combined"], output_dir)
    alternating = _alternating_pass(stability, output_dir)
    frozen = _freeze_selector(stability, alternating, output_dir)
    report = _build_report(all_development, stream, path_summary, matrix, diagnostics, stability, alternating, frozen, output_dir)
    stage3_manifest = _update_stage3_status(frozen, audits, report)

    for item in audits["samples"]:
        if _hash(Path(item["path"])) != audit_hashes_before[item["label"]]:
            raise AssertionError(f"Stage 2E modified preserved audit sample {item['label']}")
    if not plans["exit_strictly_before_te"].all():
        raise AssertionError("Stage 2E exit-plan audit failed")
    manifest = {
        "label": "stage2e_path_aware_selection_exit_interaction",
        "development_only": True,
        "semantic_stage2c_conclusions_frozen": True,
        "stage2c_performance_selector_reopened": True,
        "all_development_candidates": int(len(all_development)),
        "oof_matrix_candidates": int(len(stream)),
        "legal_path_rows": int(len(paths)),
        "kernel_executable_oof_candidates": int(plans["stage2e_candidate_id"].nunique()),
        "selectors": list(SELECTORS),
        "exit_policies": list(EXIT_POLICIES),
        "path_class_rules": PATH_CLASS_RULES,
        "exit_policy_rules": EXIT_POLICY_RULES,
        "combined_exact_replays": int(len(matrix["combined"])),
        "fold_exact_replays": int(len(matrix["folds"])),
        "research_frozen_selector": frozen,
        "exit_model_training_performed": False,
        "preserved_audit_samples": audits["samples"],
        "source_hashes": {"semantic_candidates": _hash(SEMANTIC_CANDIDATES), "oof_stream": _hash(OOF_STREAM), "prices": _hash(PRICES), "probs": _hash(PROBS)},
        "test_rows_read": 0,
        "lockbox_opened": False,
        "te_is_never_exit": True,
        "latest_legal_exit_horizon": "T_e - 1",
        "outputs": {
            "report": str(report),
            "frozen_selector": frozen["path"],
            "path_table": str(output_dir / "legal_paths" / "candidate_legal_path_table.csv"),
            "path_summary": str(output_dir / "legal_paths" / "candidate_path_summary.csv"),
            "exit_plans": str(output_dir / "legal_paths" / "candidate_exit_plans.csv"),
            "combined_matrix": str(output_dir / "selector_exit_matrix" / "selector_exit_combined_exact_results.csv"),
            "fold_matrix": str(output_dir / "selector_exit_matrix" / "selector_exit_fold_exact_results.csv"),
            "stage3_manifest": str(stage3_manifest),
        },
    }
    manifest_path = output_dir / "stage2e_manifest.json"
    _json(manifest_path, manifest)
    return {"manifest": manifest_path, "report": report, "frozen_selector": Path(frozen["path"]), "stage3_manifest": stage3_manifest}


if __name__ == "__main__":
    for name, path in run_stage2e().items():
        print(f"{name}: {path}")
