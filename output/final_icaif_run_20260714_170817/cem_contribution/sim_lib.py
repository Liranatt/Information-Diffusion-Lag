"""Shared harness for the CEM-contribution experiments.

Replicates exactly the data filtering and test-window invocation that
backtesting/optimize_cem.py::main uses, so any policy dict can be evaluated on
the identical January-June 2026 test period with identical costs and
portfolio mechanics. Correctness is verified by reproducing the canonical
frozen-Baseline seed-42 result to 4 decimals (see verify_harness()).
"""
from __future__ import annotations

import math
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\Users\Liran\PycharmProjects\cem_clean_repo")
sys.path.insert(0, str(ROOT))

from backtesting.optimize_cem import (  # noqa: E402
    INITIAL_CAPITAL,
    REL_COL,
    as_utc_day,
    sim_opp_cost,
)
from core.kernel import clear_kernel_caches  # noqa: E402
from core.policy import DEFAULT_POLICY  # noqa: E402

# Repository-default, pre-specified fixed policy (core/policy.py::DEFAULT_POLICY
# is the documented CEM starting mean; PORT_DEFAULT layers the default sizing).
SIMPLE_POLICY = {**DEFAULT_POLICY, "position_size_pct": 0.10, "max_concurrent": 10}

# Which of the ten parameters drive candidate acceptance/selection vs execution.
SELECTION_KEYS = ("enter_strong", "enter_floor", "hold_days", "max_prob_surge", "max_price_runup")
EXECUTION_KEYS = ("atr_mult", "lock_activate", "theta_out", "position_size_pct", "max_concurrent")

_CACHE: dict = {}


def load_universe():
    """Candidates (filtered exactly as optimize_cem.main), prices, probs, window."""
    if _CACHE:
        return _CACHE["df"], _CACHE["prices"], _CACHE["probs"], _CACHE["oos_start"], _CACHE["oos_end"]
    df = pd.read_parquet(ROOT / "data" / "candidates_audit_clean.parquet")
    if "cem_eligible" in df.columns:
        df = df.loc[df["cem_eligible"].fillna(False).astype(bool)].copy()
    df = df[df[REL_COL].astype(float) > 0.5].copy()
    df["split"] = df["split"].astype(str).str.lower().str.strip().replace({"val": "test"})
    df["t_theta"] = pd.to_datetime(df["t_theta"], utc=True)
    df["t_e"] = pd.to_datetime(df["t_e"], utc=True)

    with open(ROOT / "data" / "prices.pkl", "rb") as f:
        prices = pickle.load(f)
    with open(ROOT / "data" / "probs.pkl", "rb") as f:
        probs = pickle.load(f)
    clear_kernel_caches()

    max_t = as_utc_day(df["t_theta"].max()) + pd.Timedelta(days=1)
    oos_start = pd.Timestamp("2026-01-01", tz="UTC")
    oos_end = max_t - pd.Timedelta(days=1)
    _CACHE.update(df=df, prices=prices, probs=probs, oos_start=oos_start, oos_end=oos_end)
    return df, prices, probs, oos_start, oos_end


def test_frame():
    df, _, _, oos_start, oos_end = load_universe()
    return df[(df["t_theta"] >= oos_start) & (df["t_theta"] <= oos_end)].copy()


def run_test(policy: dict, bench_sym: str, candidate_df: pd.DataFrame | None = None,
             allocation_mode: str = "fifo"):
    """Run the frozen-policy test simulation; returns (trades, equity, stats)."""
    df, prices, probs, oos_start, oos_end = load_universe()
    oos_df = candidate_df if candidate_df is not None else test_frame()
    trades, equity, stats, _, _, _ = sim_opp_cost(
        oos_df, prices, probs, policy,
        bench_sym=bench_sym, initial=INITIAL_CAPITAL, use_kelly=False,
        start_date=oos_start, end_date=oos_end, allocation_mode=allocation_mode,
    )
    return trades, equity, stats


def sharpe_from_equity(equity: pd.DataFrame) -> float:
    daily = equity["equity"].astype(float).pct_change().dropna()
    sd = daily.std(ddof=1)
    return float(daily.mean() / sd * math.sqrt(252.0)) if sd > 1e-12 else 0.0


def stat_row(label: str, bench: str, stats: dict, equity: pd.DataFrame, extra: dict | None = None) -> dict:
    row = {
        "arm": label,
        "benchmark": bench,
        "test_return_pct": stats["total_return"],
        "test_benchmark_return_pct": stats["benchmark_return"],
        "test_excess_return_pct": stats["excess_return"],
        "test_sharpe": round(sharpe_from_equity(equity), 4),
        "test_max_dd_pct": stats["max_dd"],
        "test_trades": stats["n_trades"],
        "test_trade_txn_cost": stats["trade_txn_cost"],
        "test_total_txn_cost": stats["total_txn_cost"],
        "test_win_rate_pct": stats["win_rate"],
        "test_start_date": stats["start_date"],
        "test_end_date": stats["end_date"],
    }
    if extra:
        row.update(extra)
    return row


def hybrid_policy(selection_from: dict, execution_from: dict) -> dict:
    policy = {}
    for key in SELECTION_KEYS:
        policy[key] = selection_from[key]
    for key in EXECUTION_KEYS:
        policy[key] = execution_from[key]
    policy["hold_days"] = int(round(float(policy["hold_days"])))
    policy["max_concurrent"] = int(round(float(policy["max_concurrent"])))
    if float(policy["enter_strong"]) < float(policy["enter_floor"]):
        policy["enter_strong"] = policy["enter_floor"]
    return policy


def verify_harness() -> None:
    """The harness must reproduce the canonical seed-42 frozen Baseline exactly."""
    import json
    results = pd.read_csv(ROOT / "runs" / "icaif_base_vs_all_seed42" / "experiment_results_clean.csv")
    row = results[(results["experiment"] == "Baseline") & (results["benchmark"] == "SPY")].iloc[0]
    policy = json.loads(row["policy_snapshot_json"])
    _, equity, stats = run_test(policy, "SPY")
    expected = float(row["test_return_pct"])
    got = float(stats["total_return"])
    assert abs(got - expected) < 1e-4, f"harness mismatch: {got} vs {expected}"
    print(f"harness verified: Baseline SPY test return {got:.4f} == {expected:.4f}")


if __name__ == "__main__":
    verify_harness()
