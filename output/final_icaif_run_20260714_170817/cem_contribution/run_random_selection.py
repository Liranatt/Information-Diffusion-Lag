"""Matched random-selection test.

Question: does the CEM-selected candidate set beat arbitrary selection from the
contemporaneously available candidate pool, holding execution fixed?

Design (documented limitation: in this architecture, candidate selection and
entry-confirmation timing are one mechanism — the entry gates — so "selection"
here includes the gate's timing):

  Observed arm  = CEM_SEL_SIMPLE_EXEC (seed-42 frozen-baseline selection params
                  + repository-default execution/sizing/capacity, FIFO).
  Availability  = every eligible test candidate's first probability point
                  >= 0.55 (the fixed screening rule), i.e. the permissive-gate
                  signal set. Signals are point-in-time by construction.
  Resample      = for each entry month, draw (without replacement) as many
                  available candidates as the observed arm *entered* that month;
                  run the identical portfolio simulation on the sampled subset
                  with the permissive-gate + default-execution policy.
  B             = 500 resamples, numpy seed 42.

Comparison metrics: test excess return, Sharpe, MaxDD, trade transaction cost.
Empirical one-sided p = (#{random >= observed} + 1)/(B + 1) for excess/Sharpe
(and <= for MaxDD magnitude / costs, reported descriptively).

Output: random_selection_results.csv (summary) and
random_selection_draws.csv (per-resample metrics).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from sim_lib import (
    ROOT,
    SIMPLE_POLICY,
    hybrid_policy,
    run_test,
    sharpe_from_equity,
    test_frame,
)

OUT = Path(__file__).resolve().parent
N_RESAMPLES = 500
RNG_SEED = 42

PERMISSIVE_SELECTION = {
    "enter_strong": 0.55,
    "enter_floor": 0.55,
    "hold_days": 1,
    "max_prob_surge": 999.0,
    "max_price_runup": 999.0,
}
POOL_POLICY = hybrid_policy(PERMISSIVE_SELECTION, SIMPLE_POLICY)


def seed42_baseline_policy(bench: str) -> dict:
    results = pd.read_csv(ROOT / "runs" / "icaif_base_vs_all_seed42" / "experiment_results_clean.csv")
    row = results[(results["experiment"] == "Baseline") & (results["benchmark"] == bench)].iloc[0]
    return json.loads(row["policy_snapshot_json"])


def available_signals() -> pd.DataFrame:
    """Per-candidate permissive entry signals (no portfolio constraints)."""
    from backtesting.optimize_cem import truncate_paths
    from core.kernel import simulate_one
    from sim_lib import load_universe

    df, prices, probs, _oos_start, oos_end = load_universe()
    frame = test_frame()
    sim_prices, sim_probs = truncate_paths(prices, probs, oos_end)
    rows = []
    for idx, row in frame.iterrows():
        trade = simulate_one(row, sim_prices, sim_probs, POOL_POLICY)
        if trade is None:
            continue
        rows.append({
            "cand_index": idx,
            "market_id": row["market_id"],
            "symbol": row["symbol"],
            "entry_month": str(trade["entry_date"])[:7],
        })
    return pd.DataFrame(rows)


def run_bench(bench: str, rng: np.random.Generator) -> tuple[dict, pd.DataFrame]:
    frame = test_frame()
    observed_policy = hybrid_policy(seed42_baseline_policy(bench), SIMPLE_POLICY)
    obs_trades, obs_equity, obs_stats = run_test(observed_policy, bench)
    obs = {
        "excess": float(obs_stats["excess_return"]),
        "sharpe": sharpe_from_equity(obs_equity),
        "max_dd": float(obs_stats["max_dd"]),
        "txn": float(obs_stats["trade_txn_cost"]),
        "trades": int(obs_stats["n_trades"]),
    }
    monthly_counts = (
        pd.Series([d[:7] for d in obs_trades["entry_date"].astype(str)])
        .value_counts()
        .to_dict()
    )

    pool = available_signals()
    pool_by_month = {m: g["cand_index"].to_numpy() for m, g in pool.groupby("entry_month")}
    print(f"{bench}: observed trades={obs['trades']} excess={obs['excess']:+.2f} "
          f"pool signals={len(pool)} months={sorted(monthly_counts)}", flush=True)

    draws = []
    for b in range(N_RESAMPLES):
        chosen: list[int] = []
        for month, count in monthly_counts.items():
            candidates = pool_by_month.get(month, np.array([], dtype=int))
            take = min(count, len(candidates))
            if take > 0:
                chosen.extend(rng.choice(candidates, size=take, replace=False).tolist())
        subset = frame.loc[sorted(set(chosen))]
        _t, equity, stats = run_test(POOL_POLICY, bench, candidate_df=subset)
        draws.append({
            "benchmark": bench,
            "draw": b,
            "n_sampled": len(chosen),
            "excess": float(stats["excess_return"]),
            "sharpe": sharpe_from_equity(equity),
            "max_dd": float(stats["max_dd"]),
            "txn": float(stats["trade_txn_cost"]),
            "trades": int(stats["n_trades"]),
        })
        if (b + 1) % 100 == 0:
            print(f"  {bench} resample {b + 1}/{N_RESAMPLES}", flush=True)
    draw_df = pd.DataFrame(draws)

    def pct_rank(series: pd.Series, value: float) -> float:
        return float((series < value).mean() * 100.0)

    summary = {
        "benchmark": bench,
        "n_resamples": N_RESAMPLES,
        "observed_trades": obs["trades"],
        "observed_excess_pct": obs["excess"],
        "random_excess_median": draw_df["excess"].median(),
        "random_excess_p05": draw_df["excess"].quantile(0.05),
        "random_excess_p95": draw_df["excess"].quantile(0.95),
        "excess_percentile_rank": pct_rank(draw_df["excess"], obs["excess"]),
        "p_random_ge_observed_excess": float(((draw_df["excess"] >= obs["excess"]).sum() + 1) / (N_RESAMPLES + 1)),
        "observed_sharpe": obs["sharpe"],
        "random_sharpe_median": draw_df["sharpe"].median(),
        "random_sharpe_p05": draw_df["sharpe"].quantile(0.05),
        "random_sharpe_p95": draw_df["sharpe"].quantile(0.95),
        "sharpe_percentile_rank": pct_rank(draw_df["sharpe"], obs["sharpe"]),
        "p_random_ge_observed_sharpe": float(((draw_df["sharpe"] >= obs["sharpe"]).sum() + 1) / (N_RESAMPLES + 1)),
        "observed_max_dd_pct": obs["max_dd"],
        "random_max_dd_median": draw_df["max_dd"].median(),
        "observed_txn_cost": obs["txn"],
        "random_txn_median": draw_df["txn"].median(),
        "random_trades_median": draw_df["trades"].median(),
    }
    return summary, draw_df


def main() -> None:
    rng = np.random.default_rng(RNG_SEED)
    summaries, all_draws = [], []
    for bench in ("SPY", "QQQ"):
        summary, draws = run_bench(bench, rng)
        summaries.append(summary)
        all_draws.append(draws)
    pd.DataFrame(summaries).to_csv(OUT / "random_selection_results.csv", index=False)
    pd.concat(all_draws, ignore_index=True).to_csv(OUT / "random_selection_draws.csv", index=False)
    print(pd.DataFrame(summaries).to_string(index=False))


if __name__ == "__main__":
    main()
