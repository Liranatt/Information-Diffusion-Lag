from __future__ import annotations

import pandas as pd
import pytest

import backtesting.optimize_cem as optimizer
from selection.stage2e_path_aware import _choose_exit, _classify_path, _rank_selector


def _path(active: list[float]) -> pd.DataFrame:
    running_high = []
    running_low = []
    peaks = []
    high = float("-inf")
    low = float("inf")
    peak = float("-inf")
    below = 0
    rows = []
    for index, value in enumerate(active, start=1):
        high = max(high, value)
        low = min(low, value)
        peak = max(peak, value)
        below = below + 1 if value < 0 else 0
        running_high.append(high)
        running_low.append(low)
        peaks.append(peak)
        rows.append({
            "legal_holding_day": index,
            "path_date": pd.Timestamp("2025-01-01", tz="UTC") + pd.offsets.BDay(index - 1),
            "stock_close": 100.0 + value,
            "stock_return_pct": value,
            "active_return_pct": value,
            "running_mfe_active_pct": high,
            "running_mae_active_pct": low,
            "time_to_mfe_days": active[:index].index(max(active[:index])) + 1,
            "fraction_positive_active_days": sum(item > 0 for item in active[:index]) / index,
            "running_peak_active_close_pct": peak,
            "running_peak_giveback_pct": peak - value,
            "duration_below_zero_days": below,
        })
    return pd.DataFrame(rows)


def test_path_classes_are_descriptive_and_predeclared() -> None:
    assert _classify_path(_path([3.0, 2.5, 0.5, -1.0])) == "early_winner_with_giveback"
    assert _classify_path(_path([-3.0, -2.0, -0.5, 1.5])) == "early_loser_with_recovery"
    assert _classify_path(_path([-0.5, -1.0, -1.5, -2.0])) == "persistent_loser"


def test_exit_rules_never_need_te_as_an_exit() -> None:
    path = _path([1.0, 3.5, 3.0, 1.5, 2.0])
    summary = {
        "pre_entry_atr20_pct": 1.0,
        "reference_exit_date": path.iloc[2]["path_date"],
        "reference_exit_price": 103.0,
        "reference_exit_reason": "reference",
    }
    assert _choose_exit("fixed_2_day", path, summary)["exit_date"] == path.iloc[1]["path_date"]
    assert _choose_exit("hold_to_te1", path, summary)["exit_date"] == path.iloc[-1]["path_date"]
    assert _choose_exit("trailing_profit_giveback_exit", path, summary)["exit_date"] == path.iloc[3]["path_date"]


def test_selector_ranking_uses_only_declared_ex_ante_fields() -> None:
    frame = pd.DataFrame({
        "benchmark": ["SPY", "SPY"],
        "analysis_split": ["train", "train"],
        "entry_date": pd.to_datetime(["2025-01-02", "2025-01-02"], utc=True),
        "mapping_type": ["direct_issuer", "direct_issuer"],
        "predicted_target_a": [-1.0, 2.0],
        "predicted_target_b_slot": [3.0, 1.0],
        "expected_slot_days": [2.0, 8.0],
        "legacy_gemini_relevance_score": [1.0, 0.9],
        "semantic_event_rank": [0, 0],
        "source_order": [1, 2],
        "symbol": ["AAA", "BBB"],
    })
    target_a = _rank_selector(frame, "predicted_target_a_positive").set_index("symbol")
    target_b = _rank_selector(frame, "target_b_per_slot_day").set_index("symbol")
    duration = _rank_selector(frame, "expected_slot_days_ranking").set_index("symbol")
    assert target_a.loc["BBB", "_selector_rank"] < target_a.loc["AAA", "_selector_rank"]
    assert target_b.loc["AAA", "_selector_rank"] < target_b.loc["BBB", "_selector_rank"]
    assert duration.loc["AAA", "_selector_rank"] < duration.loc["BBB", "_selector_rank"]


def test_exact_engine_exit_plan_hook_is_opt_in_and_enforces_te(monkeypatch: pytest.MonkeyPatch) -> None:
    dates = pd.date_range("2025-01-02", periods=4, freq="B", tz="UTC")
    prices = {
        "SPY": [(date, 100.0, 101.0, 99.0, 100.0 + index) for index, date in enumerate(dates)],
        "AAA": [(date, 100.0, 102.0, 99.0, 100.0 + index) for index, date in enumerate(dates)],
    }

    def fake_simulate_one(row, _prices, _probs, _policy):
        return {
            "market_id": "1", "symbol": "AAA", "question": "q", "entry_date": str(dates[0].date()),
            "entry_price": 100.0, "exit_date": str(dates[1].date()), "exit_price": 101.0,
            "exit_reason": "reference", "entry_prob": 0.8, "split": "train",
        }

    monkeypatch.setattr(optimizer, "simulate_one", fake_simulate_one)
    frame = pd.DataFrame([{
        "market_id": "1", "event_id": "1", "symbol": "AAA", "question": "q",
        "t_theta": dates[0], "t_e": dates[3], "_selector_rank": 0,
        "_planned_date": dates[2], "_planned_price": 102.0, "_planned_reason": "planned",
    }])
    trades, *_ = optimizer.sim_opp_cost(
        frame, prices, {}, dict(optimizer.PORT_DEFAULT), bench_sym="SPY",
        start_date=dates[0], end_date=dates[3],
        exit_plan_columns=("_planned_date", "_planned_price", "_planned_reason"),
    )
    assert trades.iloc[0]["exit_date"] == str(dates[2].date())
    assert trades.iloc[0]["realized_exit_reason"] == "planned"

    illegal = frame.copy()
    illegal["_planned_date"] = dates[3]
    with pytest.raises(AssertionError, match="strictly before"):
        optimizer.sim_opp_cost(
            illegal, prices, {}, dict(optimizer.PORT_DEFAULT), bench_sym="SPY",
            start_date=dates[0], end_date=dates[3],
            exit_plan_columns=("_planned_date", "_planned_price", "_planned_reason"),
        )
