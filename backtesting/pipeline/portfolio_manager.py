"""RL/CEM portfolio manager.

Adapts CEM from per-trade policy search to portfolio-level optimization:
position sizing, candidate selection, risk limits. Maximises Sharpe ratio
with max-drawdown penalty.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from pipeline.strategy import (
    DEFAULT_POLICY,
    CEM_BOUNDS,
    policy_from_vector,
    run_backtest,
    score_sharpe_per_day,
    score_mean_return,
)


def _max_drawdown(returns: pd.Series) -> float:
    """Max drawdown from a series of trade returns (%)."""
    if returns.empty:
        return 0.0
    cum = (1 + returns / 100).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    return float(dd.min()) * 100  # negative %


def reward_sharpe_dd(tdf: pd.DataFrame, dd_penalty: float = 0.5) -> float:
    """Combined reward: Sharpe − penalty * |max_drawdown|."""
    sharpe = score_sharpe_per_day(tdf)
    if sharpe <= -900:
        return -999.0
    dd = _max_drawdown(tdf["return_pct"])
    return sharpe - dd_penalty * abs(dd)


def cem_search(
    df: pd.DataFrame,
    prices: dict,
    probs: dict,
    reward_fn: Callable = reward_sharpe_dd,
    n_iter: int = 10,
    pop_size: int = 40,
    elite_frac: float = 0.25,
    seed: int = 42,
) -> dict:
    """Cross-Entropy Method search over policy knobs.

    Trains on 'train' split only. Returns the best policy dict.
    """
    rng = np.random.default_rng(seed)
    names = list(CEM_BOUNDS.keys())
    dim = len(names)
    elite_k = max(2, int(pop_size * elite_frac))

    # Initialise population means: use DEFAULT_POLICY
    mean = np.array([DEFAULT_POLICY[n] for n in names], dtype=float)
    # Initialise standard deviations: (max - min) / 4
    std = np.array([(CEM_BOUNDS[n][1] - CEM_BOUNDS[n][0]) / 4.0 for n in names], dtype=float)

    best_score = -999.0
    best_policy = None

    for it in range(n_iter):
        samples = rng.normal(mean, std, size=(pop_size, len(names)))
        policies = [policy_from_vector(s) for s in samples]

        scores = []
        for p in policies:
            tdf = run_backtest(df, prices, probs, p, split_filter="train")
            scores.append(reward_fn(tdf))
        scores = np.array(scores)

        elite_idx = np.argsort(scores)[-elite_k:]
        elite = samples[elite_idx]
        mean = elite.mean(axis=0)
        std = elite.std(axis=0) + 1e-4

        it_best = scores.max()
        if it_best > best_score:
            best_score = it_best
            best_policy = policies[elite_idx[-1]]

        p_str = "  ".join(f"{n}={mean[i]:.3f}" for i, n in enumerate(names))
        print(f"  iter {it:2d}/{n_iter}  best_train={it_best:+.3f}  {p_str}")

    print(f"\n  CONVERGED POLICY: {best_policy}")
    print(f"  Best train reward: {best_score:+.3f}\n")
    return best_policy


def evaluate_splits(
    df: pd.DataFrame,
    prices: dict,
    probs: dict,
    policy: dict,
    reward_fn: Callable = reward_sharpe_dd,
) -> dict[str, dict]:
    """Evaluate a policy on each split, returning summary stats."""
    results = {}
    for sp in ("train", "val", "test"):
        tdf = run_backtest(df, prices, probs, policy, split_filter=sp)
        if tdf.empty:
            results[sp] = {"n": 0}
            continue
        results[sp] = {
            "n": len(tdf),
            "mean_return": round(float(tdf["return_pct"].mean()), 2),
            "win_pct": round(float((tdf["return_pct"] > 0).mean()) * 100, 1),
            "median_return": round(float(tdf["return_pct"].median()), 2),
            "max_drawdown": round(_max_drawdown(tdf["return_pct"]), 2),
            "reward": round(float(reward_fn(tdf)), 3),
            "exit_reasons": tdf["exit_reason"].value_counts().to_dict(),
        }
    return results


def select_candidates(
    df: pd.DataFrame,
    predicted_returns: pd.Series,
    min_predicted_return: float = 0.5,
    max_positions: int = 10,
    min_relevance: float = 0.5,
) -> pd.DataFrame:
    """Select top candidates for live trading based on RF predictions."""
    eligible = df.copy()
    eligible["predicted_return"] = predicted_returns

    mask = (
        (eligible["predicted_return"] >= min_predicted_return)
        & (eligible["feat_connection_strength"] >= min_relevance)
    )
    eligible = eligible[mask]

    if len(eligible) > max_positions:
        eligible = eligible.nlargest(max_positions, "predicted_return")

    return eligible
