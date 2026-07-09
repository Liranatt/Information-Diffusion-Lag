"""Load the deployed trading policy and compute half-Kelly position sizes.

"Keep walking forward": the live engine always trades the policy of the
LATEST walk-forward fold for the configured experiment/benchmark, read from
the fold-audit CSV that optimize_cem.py writes. Re-running the optimizer with
fresh resolved events refits the folds and the live engine picks up the new
last-fold policy on its next tick -- exactly the T2 mechanism, continued into
production.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .config import LiveConfig

log = logging.getLogger("live.policy")

# Sizing constants mirror optimize_cem.py exactly.
KELLY_MIN_N = 10
KELLY_LOOKBACK_N = 30
KELLY_MIN_SZ = 0.03
KELLY_MAX_SZ = 0.15

REQUIRED_KEYS = (
    "atr_mult", "lock_activate", "theta_out", "enter_strong", "enter_floor",
    "hold_days", "max_prob_surge", "max_price_runup",
    "position_size_pct", "max_concurrent",
)


def load_live_policy(cfg: LiveConfig) -> dict:
    """Latest-fold policy for cfg.experiment on cfg.benchmark."""
    policy = _from_fold_audit(cfg.fold_audit_csv, cfg.experiment, cfg.benchmark)
    if policy is None:
        policy = _from_results(cfg.results_csv, cfg.experiment, cfg.benchmark)
    if policy is None:
        raise RuntimeError(
            f"No policy found for {cfg.experiment}/{cfg.benchmark} in "
            f"{cfg.fold_audit_csv} or {cfg.results_csv}. Run optimize_cem.py first."
        )
    missing = [k for k in REQUIRED_KEYS if k not in policy]
    if missing:
        raise RuntimeError(f"Policy is missing keys: {missing}")
    policy["hold_days"] = int(policy["hold_days"])
    policy["max_concurrent"] = int(policy["max_concurrent"])
    return policy


def _from_fold_audit(path: Path, experiment: str, benchmark: str) -> dict | None:
    if not path.exists():
        return None
    df = pd.read_csv(path)
    sub = df[(df["experiment"] == experiment) & (df["benchmark"] == benchmark)]
    if sub.empty:
        return None
    last = sub.sort_values("fold").iloc[-1]
    policy = json.loads(last["eval_policy_json"])
    log.info("loaded fold-%s policy for %s/%s from fold audit",
             last["fold"], experiment, benchmark)
    return policy


def _from_results(path: Path, experiment: str, benchmark: str) -> dict | None:
    if not path.exists():
        return None
    df = pd.read_csv(path)
    sub = df[(df["experiment"] == experiment) & (df["benchmark"] == benchmark)]
    if sub.empty:
        return None
    policy = json.loads(sub.iloc[0]["policy_json"])
    log.info("loaded terminal policy for %s/%s from results csv", experiment, benchmark)
    return policy


def kelly_size(completed_history: list[dict], base: float) -> float:
    """Half-Kelly from the latest fully net realised trades (backtest parity)."""
    if len(completed_history) < KELLY_MIN_N:
        return base

    recent = completed_history[-KELLY_LOOKBACK_N:]
    wins = [float(t["pnl_pct"]) for t in recent if float(t.get("pnl_pct") or 0.0) > 0]
    losses = [float(t["pnl_pct"]) for t in recent if float(t.get("pnl_pct") or 0.0) <= 0]

    if not wins or not losses:
        return base

    win_probability = len(wins) / len(recent)
    payoff_ratio = abs(float(np.mean(wins))) / abs(float(np.mean(losses)))
    if payoff_ratio <= 0 or not np.isfinite(payoff_ratio):
        return base

    full_kelly = (win_probability * payoff_ratio - (1.0 - win_probability)) / payoff_ratio
    half_kelly = max(0.0, full_kelly / 2.0)
    return float(np.clip(half_kelly, KELLY_MIN_SZ, KELLY_MAX_SZ))
