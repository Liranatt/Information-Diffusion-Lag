"""Harness for the T4 allocator experiment.

Freezes, per CEM seed and benchmark: the fitted T1+T2+T3+T4 walk-forward
schedule, the T1 objective's product (the schedule itself), T3 Kelly sizing
with the reference completed-trade history, candidate eligibility, entry/exit
rules, costs, capital mechanics, and test dates. Changes ONLY the allocation
rule applied when contemporaneously eligible candidates compete:

  arm "t4"     - the exact current event-priority allocator (ranking,
                 same-symbol collapse, preemption), as in the reference runs.
  arm "fifo"   - chronological FIFO order among the day's candidates; no
                 ranking, no collapse, no preemption.
  arm "random" - a seeded random ordering of each day's multi-candidate batch;
                 no ranking, no collapse, no preemption. Single-candidate
                 batches are never shuffled. For batches that are uncontested
                 (enough free slots, no duplicate symbols) the ordering is
                 outcome-neutral up to sub-dollar cost interactions, so this
                 is equivalent to randomizing only contested competitions.

Implementation: all three arms run through sim_opp_cost with
allocation_mode="event_priority" so that day-batching, gap handling, Kelly,
and cost mechanics are byte-identical; the batch-preparation function
(optimize_cem._prepare_event_priority_batch) is swapped per arm, and
preemption is neutralized for the fifo/random arms by setting the preemption
hurdle to -inf (the T4 arm keeps the original +3% hurdle). Two verifications
guard the construction:
  * the "t4" arm reproduces the reference run CSV to 1e-4;
  * the "fifo" arm equals a true allocation_mode="fifo" run to 1e-4.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\Users\Liran\PycharmProjects\cem_clean_repo")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "output" / "final_icaif_run_20260714_170817" / "cem_contribution"))

import backtesting.optimize_cem as oc  # noqa: E402
from backtesting.optimize_cem import (  # noqa: E402
    DynamicPolicySchedule,
    INITIAL_CAPITAL,
    as_utc_day,
    completed_trade_history_before,
    rows_completed_before,
    sim_opp_cost,
)
from sim_lib import load_universe, sharpe_from_equity  # noqa: E402

ORIGINAL_BATCH_FN = oc._prepare_event_priority_batch
ORIGINAL_HURDLE = oc.PREEMPT_NET_PROFIT_HURDLE_PCT


def run_dir_for_seed(seed: int) -> Path:
    if seed == 42:
        return ROOT / "runs" / "icaif_base_vs_all_seed42"
    return ROOT / "runs" / f"icaif_t4x_seed{seed}"


def load_schedule(seed: int, bench: str) -> DynamicPolicySchedule:
    audit = pd.read_csv(run_dir_for_seed(seed) / "experiment_walkforward_folds_clean.csv")
    rows = audit[(audit["experiment"] == "T1+T2+T3+T4") & (audit["benchmark"] == bench)]
    rows = rows.sort_values("fold")
    windows = []
    for _, r in rows.iterrows():
        eval_start = pd.Timestamp(r["eval_start_date"]).tz_localize("UTC")
        eval_end = pd.Timestamp(r["eval_end_date"]).tz_localize("UTC")
        windows.append({
            "fold": int(r["fold"]),
            "eval_start": eval_start,
            "eval_end_exclusive": eval_end + pd.Timedelta(days=1),
            "policy": json.loads(r["eval_policy_json"]),
        })
    return DynamicPolicySchedule(windows)


_KELLY_CACHE: dict = {}


def kelly_history(seed: int, bench: str, schedule) -> list[dict]:
    """Reference completed-trade history (train sim under event-priority),
    frozen and shared by every allocator arm."""
    key = (seed, bench)
    if key in _KELLY_CACHE:
        return _KELLY_CACHE[key]
    df, prices, probs, oos_start, _ = load_universe()
    train_df = rows_completed_before(df, oos_start)
    restore_arm()
    train_trades, _, _, _, _, _ = sim_opp_cost(
        train_df, prices, probs, schedule,
        bench_sym=bench, initial=INITIAL_CAPITAL, use_kelly=True,
        start_date=as_utc_day(train_df["t_theta"].min()),
        end_date=oos_start - pd.Timedelta(days=1),
        allocation_mode="event_priority",
    )
    hist = completed_trade_history_before(train_trades, oos_start)
    _KELLY_CACHE[key] = hist
    return hist


def set_arm(arm: str, rng: np.random.Generator | None = None) -> None:
    if arm == "t4":
        oc._prepare_event_priority_batch = ORIGINAL_BATCH_FN
        oc.PREEMPT_NET_PROFIT_HURDLE_PCT = ORIGINAL_HURDLE
    elif arm == "fifo":
        def fifo_batch(day_trades):
            return list(day_trades), []
        oc._prepare_event_priority_batch = fifo_batch
        oc.PREEMPT_NET_PROFIT_HURDLE_PCT = float("-inf")
    elif arm == "random":
        assert rng is not None
        def random_batch(day_trades):
            if len(day_trades) <= 1:
                return list(day_trades), []
            order = rng.permutation(len(day_trades))
            return [day_trades[i] for i in order], []
        oc._prepare_event_priority_batch = random_batch
        oc.PREEMPT_NET_PROFIT_HURDLE_PCT = float("-inf")
    else:
        raise ValueError(arm)


def restore_arm() -> None:
    set_arm("t4")


def run_arm(seed: int, bench: str, arm: str, *, rep: int | None = None,
            collect_logs: bool = False):
    """One full chronological test simulation under the given allocator."""
    df, prices, probs, oos_start, oos_end = load_universe()
    schedule = load_schedule(seed, bench)
    hist = kelly_history(seed, bench, schedule)
    oos_df = df[(df["t_theta"] >= oos_start) & (df["t_theta"] <= oos_end)].copy()

    rng = None
    if arm == "random":
        rng = np.random.default_rng(np.random.SeedSequence([9000, seed, 0 if bench == "SPY" else 1, int(rep)]))
    set_arm(arm, rng)
    try:
        trades, equity, stats, _, alloc, disp = sim_opp_cost(
            oos_df, prices, probs, schedule,
            bench_sym=bench, initial=INITIAL_CAPITAL, use_kelly=True,
            start_date=oos_start, end_date=oos_end,
            initial_kelly_history=[dict(x) for x in hist],
            allocation_mode="event_priority",
            collect_allocation_log=collect_logs,
        )
    finally:
        restore_arm()

    turnover = float(trades["_asset_entry_notional"].astype(float).sum()) if not trades.empty else 0.0
    row = {
        "seed": seed, "benchmark": bench, "arm": arm, "rep": rep,
        "test_return_pct": stats["total_return"],
        "test_benchmark_return_pct": stats["benchmark_return"],
        "test_excess_return_pct": stats["excess_return"],
        "test_sharpe": round(sharpe_from_equity(equity), 4),
        "test_max_dd_pct": stats["max_dd"],
        "test_trades": stats["n_trades"],
        "test_trade_txn_cost": stats["trade_txn_cost"],
        "test_total_txn_cost": stats["total_txn_cost"],
        "turnover_entry_notional": round(turnover, 2),
        "skip_max_concurrent": stats["skip_max_concurrent"],
        "skip_duplicate_symbol": stats["skip_duplicate_symbol"],
        "skip_same_day_symbol_collapsed": stats["skip_same_day_symbol_collapsed"],
        "skip_preempt_hurdle": stats["skip_preempt_hurdle"],
        "preemptions": stats["preemptions"],
    }
    if collect_logs:
        return row, trades, alloc, disp
    return row


def verify(seed: int = 42) -> None:
    """Arm 't4' must reproduce the reference run CSV; arm 'fifo' must equal a
    true allocation_mode='fifo' simulation."""
    ref = pd.read_csv(run_dir_for_seed(seed) / "experiment_results_clean.csv")
    for bench in ("SPY", "QQQ"):
        expected = float(ref[(ref["experiment"] == "T1+T2+T3+T4") & (ref["benchmark"] == bench)]["test_return_pct"].iloc[0])
        got = run_arm(seed, bench, "t4")["test_return_pct"]
        assert abs(got - expected) < 1e-4, ("t4 arm mismatch", bench, got, expected)

        fifo_patched = run_arm(seed, bench, "fifo")
        df, prices, probs, oos_start, oos_end = load_universe()
        schedule = load_schedule(seed, bench)
        hist = kelly_history(seed, bench, schedule)
        oos_df = df[(df["t_theta"] >= oos_start) & (df["t_theta"] <= oos_end)].copy()
        restore_arm()
        _, _, stats, _, _, _ = sim_opp_cost(
            oos_df, prices, probs, schedule,
            bench_sym=bench, initial=INITIAL_CAPITAL, use_kelly=True,
            start_date=oos_start, end_date=oos_end,
            initial_kelly_history=[dict(x) for x in hist],
            allocation_mode="fifo",
        )
        assert abs(fifo_patched["test_return_pct"] - stats["total_return"]) < 1e-4, (
            "fifo-equivalence mismatch", bench, fifo_patched["test_return_pct"], stats["total_return"])
        print(f"verify {bench}: t4 {got:.4f} == ref {expected:.4f}; "
              f"fifo-patched {fifo_patched['test_return_pct']:.4f} == true-fifo {stats['total_return']:.4f}")


if __name__ == "__main__":
    verify()
