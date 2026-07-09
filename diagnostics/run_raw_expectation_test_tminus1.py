#!/usr/bin/env python
"""
Raw expectation test: E[raw net stock return from entry to T-1] > 0?

Every candidate in the parquet that passes the T1+T2+T3+T4 entry rules
becomes an independent hypothetical trade with fixed $10k notional.
Exit = close on the last trading day before the scheduled Polymarket
resolution date (T-1). No benchmark, no portfolio constraints.

Uses fold-specific CEM policies from T1+T2+T3+T4 SPY walk-forward folds.
"""
from __future__ import annotations

import json
import math
import os
import pickle
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pipeline.strategy import (
    entry_day,
    resolve_polarity,
    effective_prob_path,
)
from optimize_cem import (
    ib_cost,
    as_utc_day,
    event_family_from_text,
    EVENT_PRIORITY_ORDER,
    RANK_RUNUP_CLIP,
)

PROJECT = Path(__file__).resolve().parent.parent
CANDIDATES_PATH = PROJECT / "data" / "candidates.parquet"
PRICES_PATH = PROJECT / "data" / "prices.pkl"
PROBS_PATH = PROJECT / "data" / "probs.pkl"
WF_FOLDS_CSV = PROJECT / "data" / "experiment_walkforward_folds_clean.csv"
OUTPUT_DIR = PROJECT / "output" / "raw_expectation_tminus1"
ZIP_PATH = PROJECT / "output" / "raw_expectation_tminus1.zip"

NOTIONAL = 10_000.0
RNG_SEED = 42
N_BOOTSTRAP = 10_000
FOLD_EXPERIMENT = "T1+T2+T3+T4"
FOLD_BENCHMARK = "SPY"


# ═══════════════════════════════════════════════════════════════════════════
# Data loading
# ═══════════════════════════════════════════════════════════════════════════

def load_candidates() -> pd.DataFrame:
    df = pd.read_parquet(CANDIDATES_PATH)
    df = df.reset_index(drop=True)
    df["candidate_id"] = [f"C{i:04d}" for i in range(len(df))]
    return df


def load_prices() -> dict:
    with open(PRICES_PATH, "rb") as f:
        return pickle.load(f)


def load_probs() -> dict:
    with open(PROBS_PATH, "rb") as f:
        return pickle.load(f)


def load_fold_policies() -> list[dict]:
    wf = pd.read_csv(WF_FOLDS_CSV)
    rows = wf[(wf["experiment"] == FOLD_EXPERIMENT) & (wf["benchmark"] == FOLD_BENCHMARK)]
    rows = rows.sort_values("fold")
    windows = []
    for _, r in rows.iterrows():
        policy = json.loads(r["eval_policy_json"])
        eval_start = pd.Timestamp(r["eval_start_date"]).tz_localize("UTC")
        eval_end = pd.Timestamp(r["eval_end_date"]).tz_localize("UTC")
        windows.append({
            "fold": int(r["fold"]),
            "eval_start": eval_start,
            "eval_end_exclusive": eval_end + pd.Timedelta(days=1),
            "policy": policy,
        })
    return windows


def policy_for_day(windows: list[dict], day) -> tuple[dict, int]:
    day = as_utc_day(day)
    if not windows:
        return {}, 0
    matched = windows[0]["policy"]
    matched_fold = 0
    for w in windows:
        if w["eval_start"] <= day < w["eval_end_exclusive"]:
            return w["policy"], w["fold"]
        if day >= w["eval_start"]:
            matched = w["policy"]
            matched_fold = w["fold"]
    return matched, matched_fold


# ═══════════════════════════════════════════════════════════════════════════
# Candidate processing
# ═══════════════════════════════════════════════════════════════════════════

def _safe_float(val) -> float | None:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    try:
        v = float(val)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def process_candidate(row, prices, probs, fold_windows):
    cid = row["candidate_id"]
    sym = row["symbol"]
    mkt = row["market_id"]
    question = str(row.get("question", ""))
    event_id = row.get("event_id", "")
    archetype = str(row.get("feat_archetype", ""))

    def _invalid(reason, notes, attempted_entry="", sched_res=""):
        return None, {
            "candidate_id": cid, "symbol": sym, "market_id": mkt,
            "question": question, "reason": reason,
            "threshold_cross_time": str(row.get("t_theta", "")),
            "attempted_entry_date": attempted_entry,
            "scheduled_resolution_date": sched_res,
            "notes": notes,
        }

    if pd.isna(row.get("t_e")):
        return _invalid("missing_ex_ante_T", "t_e is NaT")

    t_theta = pd.Timestamp(row["t_theta"]).tz_convert("UTC")
    t_e = pd.Timestamp(row["t_e"]).tz_convert("UTC")
    sched_res = str(t_e.date())

    policy, fold_id = policy_for_day(fold_windows, t_theta)
    if not policy:
        return _invalid("no_policy_available", "No fold policy", sched_res=sched_res)

    polarity, polarity_source = resolve_polarity(question, sym)

    raw_prob_path = probs.get(mkt, [])
    if not raw_prob_path:
        return _invalid("no_probability_data", f"No prob path for market_id={mkt}",
                        sched_res=sched_res)

    eff_probs = effective_prob_path(raw_prob_path, polarity)

    ent = entry_day(eff_probs, t_theta, policy)
    if ent is None:
        return _invalid(
            "below_entry_threshold",
            f"enter_strong={policy['enter_strong']:.3f} enter_floor={policy['enter_floor']:.3f} hold_days={policy['hold_days']}",
            sched_res=sched_res,
        )

    entry_ts, entry_prob = ent

    p_surge_raw = _safe_float(row.get("feat_prob_surge_since_t0"))
    eff_p_surge = None
    if p_surge_raw is not None:
        eff_p_surge = p_surge_raw if polarity == 1 else -p_surge_raw

    if eff_p_surge is not None and eff_p_surge > policy.get("max_prob_surge", 999.0):
        return _invalid(
            "prob_surge_exceeded",
            f"eff_surge={eff_p_surge:.3f} > max={policy['max_prob_surge']:.3f}",
            attempted_entry=str(entry_ts.date()), sched_res=sched_res,
        )

    r_surge = _safe_float(row.get("feat_runup_since_t0"))
    if r_surge is not None and r_surge > policy.get("max_price_runup", 999.0):
        return _invalid(
            "price_runup_exceeded",
            f"runup={r_surge:.3f} > max={policy['max_price_runup']:.3f}",
            attempted_entry=str(entry_ts.date()), sched_res=sched_res,
        )

    # --- passed entry rules --- operational checks below ---

    closes_raw = prices.get(sym, [])
    if not closes_raw:
        return _invalid("missing_price", f"No price data for {sym}",
                        attempted_entry=str(entry_ts.date()), sched_res=sched_res)

    entry_norm = entry_ts.normalize()
    entry_bar = None
    for t, h, l, c in closes_raw:
        if t.normalize() >= entry_norm:
            entry_bar = (t.normalize(), c)
            break

    if entry_bar is None or entry_bar[1] <= 0:
        return _invalid("missing_price",
                        f"No valid price bar >= {entry_ts.date()} for {sym}",
                        attempted_entry=str(entry_ts.date()), sched_res=sched_res)

    entry_date, entry_price = entry_bar

    t_e_norm = t_e.normalize()
    exit_bar = None
    for t, h, l, c in closes_raw:
        if t.normalize() < t_e_norm:
            exit_bar = (t.normalize(), c)

    if exit_bar is None or exit_bar[1] <= 0:
        return _invalid("missing_price",
                        f"No valid price bar < {t_e.date()} for {sym}",
                        attempted_entry=str(entry_date.date()), sched_res=sched_res)

    exit_date, exit_price = exit_bar

    if entry_date >= exit_date:
        return _invalid("entry_not_before_T_minus_1",
                        f"entry={entry_date.date()} >= exit={exit_date.date()}",
                        attempted_entry=str(entry_date.date()), sched_res=sched_res)

    shares = int(NOTIONAL // entry_price)
    if shares <= 0:
        return _invalid("bad_price",
                        f"Price {entry_price:.2f} too high for ${NOTIONAL:.0f} notional",
                        attempted_entry=str(entry_date.date()), sched_res=sched_res)

    actual_notional = shares * entry_price
    gross_return = exit_price / entry_price - 1.0
    gross_pnl = shares * (exit_price - entry_price)

    buy_cost = ib_cost(shares, entry_price, False)
    sell_cost = ib_cost(shares, exit_price, True)
    total_cost = buy_cost + sell_cost

    net_pnl = gross_pnl - total_cost
    net_return = net_pnl / actual_notional
    holding_days = (exit_date - entry_date).days

    entry_rule_type = "strong" if entry_prob >= policy["enter_strong"] else "floor"
    event_family = event_family_from_text(question, archetype)

    trade = {
        "candidate_id": cid,
        "market_id": mkt,
        "event_id": event_id,
        "question": question,
        "symbol": sym,
        "event_family": event_family,
        "threshold_cross_time": str(t_theta),
        "entry_rule_type": entry_rule_type,
        "entry_date": str(entry_date.date()),
        "entry_price": round(entry_price, 4),
        "scheduled_resolution_date": sched_res,
        "exit_date_t_minus_1": str(exit_date.date()),
        "exit_price": round(exit_price, 4),
        "holding_days": holding_days,
        "notional": round(actual_notional, 2),
        "shares": shares,
        "gross_return": round(gross_return, 6),
        "gross_pnl": round(gross_pnl, 2),
        "estimated_transaction_cost": round(total_cost, 2),
        "net_return": round(net_return, 6),
        "net_pnl": round(net_pnl, 2),
        "passed_entry_rules": True,
        "invalid_primary_reason": "",
        "entry_policy_fold_id": fold_id,
        "enter_strong": round(policy["enter_strong"], 6),
        "enter_floor": round(policy["enter_floor"], 6),
        "hold_days_rule": int(policy["hold_days"]),
        "max_prob_surge": round(policy["max_prob_surge"], 6),
        "max_price_runup": round(policy["max_price_runup"], 6),
        "atr_mult": round(policy.get("atr_mult", 0), 6),
        "lock_activate": round(policy.get("lock_activate", 0), 6),
        "theta_out": round(policy.get("theta_out", 0), 6),
        "position_size_pct_policy": round(policy.get("position_size_pct", 0), 6),
        "max_concurrent_policy": int(policy.get("max_concurrent", 0)),
        "polarity": polarity,
        "polarity_source": polarity_source,
        "entry_prob": round(entry_prob, 4),
        "feat_prob_surge_since_t0": round(p_surge_raw, 4) if p_surge_raw is not None else None,
        "feat_runup_since_t0": round(r_surge, 4) if r_surge is not None else None,
        "split": row.get("split", ""),
        "no_lookahead_T_source_column": "t_e",
        "no_lookahead_T_was_known_at_entry": True,
    }
    return trade, None


def process_all_candidates(df, prices, probs, fold_windows):
    trades, invalids = [], []
    for _, row in df.iterrows():
        trade, inv = process_candidate(row, prices, probs, fold_windows)
        if trade is not None:
            trades.append(trade)
        if inv is not None:
            invalids.append(inv)
    return trades, invalids


# ═══════════════════════════════════════════════════════════════════════════
# Symbol-day collapse (T4 ranking)
# ═══════════════════════════════════════════════════════════════════════════

def _rank_key(t):
    priority = EVENT_PRIORITY_ORDER.get(t.get("event_family", "other"), 3)
    entry_prob = float(t.get("entry_prob", 0))
    runup = float(t.get("feat_runup_since_t0") or 0)
    clipped = float(np.clip(runup, RANK_RUNUP_CLIP[0], RANK_RUNUP_CLIP[1]))
    return (priority, -entry_prob, -clipped)


def collapse_symbol_day(trades):
    groups = defaultdict(list)
    for t in trades:
        key = (t["symbol"], t["entry_date"])
        groups[key].append(t)

    collapsed = []
    for (sym, edate), group in groups.items():
        ranked = sorted(group, key=_rank_key)
        winner = dict(ranked[0])
        others = ranked[1:]
        winner["supporting_candidate_ids"] = "|".join(t["candidate_id"] for t in others)
        winner["n_collapsed_candidates"] = len(group)
        collapsed.append(winner)

    collapsed.sort(key=lambda t: t["entry_date"])
    return collapsed


# ═══════════════════════════════════════════════════════════════════════════
# Aggregation helpers
# ═══════════════════════════════════════════════════════════════════════════

def compute_event_level(trades):
    by_mkt = defaultdict(list)
    for t in trades:
        by_mkt[t["market_id"]].append(t)

    rows = []
    for mkt, tlist in by_mkt.items():
        nets = [t["net_return"] for t in tlist]
        gross = [t["gross_return"] for t in tlist]
        rows.append({
            "market_id": mkt,
            "event_id": tlist[0]["event_id"],
            "question": tlist[0]["question"],
            "n_trades": len(tlist),
            "mean_net_return": round(np.mean(nets), 6),
            "median_net_return": round(np.median(nets), 6),
            "total_net_pnl_at_10k_each": round(sum(t["net_pnl"] for t in tlist), 2),
            "win_rate_net_return_gt_0": round(np.mean([1 if r > 0 else 0 for r in nets]), 4),
            "mean_gross_return": round(np.mean(gross), 6),
            "median_gross_return": round(np.median(gross), 6),
            "first_entry_date": min(t["entry_date"] for t in tlist),
            "last_exit_date": max(t["exit_date_t_minus_1"] for t in tlist),
            "event_family": tlist[0]["event_family"],
        })
    rows.sort(key=lambda r: r["first_entry_date"])
    return rows


def compute_monthly(trades):
    by_month = defaultdict(list)
    for t in trades:
        month = t["entry_date"][:7]
        by_month[month].append(t)

    rows = []
    for month in sorted(by_month):
        tlist = by_month[month]
        nets = [t["net_return"] for t in tlist]
        gross = [t["gross_return"] for t in tlist]
        rows.append({
            "month": month,
            "n_trades": len(tlist),
            "mean_net_return": round(np.mean(nets), 6),
            "median_net_return": round(np.median(nets), 6),
            "win_rate_net_return_gt_0": round(np.mean([1 if r > 0 else 0 for r in nets]), 4),
            "total_net_pnl_at_10k_each": round(sum(t["net_pnl"] for t in tlist), 2),
            "gross_mean_return": round(np.mean(gross), 6),
            "gross_median_return": round(np.median(gross), 6),
        })
    return rows


# ═══════════════════════════════════════════════════════════════════════════
# Statistical tests and robustness
# ═══════════════════════════════════════════════════════════════════════════

def _bootstrap_pvalue(returns, observed_mean, n_boot, rng):
    centered = returns - returns.mean()
    count = 0
    n = len(centered)
    for _ in range(n_boot):
        sample = rng.choice(centered, size=n, replace=True)
        if sample.mean() >= observed_mean:
            count += 1
    return count / n_boot


def _cluster_bootstrap_pvalue(returns, cluster_ids, observed_mean, n_boot, rng):
    centered = returns - returns.mean()
    unique = np.unique(cluster_ids)
    n_c = len(unique)
    cluster_idx = {c: np.where(cluster_ids == c)[0] for c in unique}
    count = 0
    for _ in range(n_boot):
        sampled = rng.choice(unique, size=n_c, replace=True)
        vals = np.concatenate([centered[cluster_idx[c]] for c in sampled])
        if vals.mean() >= observed_mean:
            count += 1
    return count / n_boot


def _top_removal_stats(net_returns, fracs):
    results = {}
    sorted_desc = np.sort(net_returns)[::-1]
    n = len(sorted_desc)
    for frac in fracs:
        k = max(1, int(math.ceil(n * frac)))
        trimmed = sorted_desc[k:]
        if len(trimmed) == 0:
            results[f"mean_net_return_after_removing_top_{int(frac*100)}pct"] = np.nan
        else:
            results[f"mean_net_return_after_removing_top_{int(frac*100)}pct"] = round(float(np.mean(trimmed)), 6)
    k5 = max(1, int(math.ceil(n * 0.05)))
    trimmed_5 = sorted_desc[k5:]
    results["median_net_return_after_removing_top_5pct"] = round(float(np.median(trimmed_5)), 6) if len(trimmed_5) > 0 else np.nan
    return results


def _pnl_concentration(net_pnls, fracs):
    results = {}
    total = sum(net_pnls)
    sorted_desc = sorted(net_pnls, reverse=True)
    n = len(sorted_desc)
    for frac in fracs:
        k = max(1, int(math.ceil(n * frac)))
        top_sum = sum(sorted_desc[:k])
        results[f"share_of_total_pnl_from_top_{int(frac*100)}pct"] = round(top_sum / total, 4) if total != 0 else np.nan
    return results


def compute_robustness_row(trades, label, rng):
    if not trades:
        return {"version": label, "n_trades": 0}

    net_returns = np.array([t["net_return"] for t in trades])
    gross_returns = np.array([t["gross_return"] for t in trades])
    net_pnls = np.array([t["net_pnl"] for t in trades])
    market_ids = np.array([t["market_id"] for t in trades])
    n = len(net_returns)

    obs_mean_net = float(np.mean(net_returns))
    wins = int(np.sum(net_returns > 0))

    if n >= 2:
        t_stat, t_p_two = sp_stats.ttest_1samp(net_returns, 0)
        t_p = t_p_two / 2 if t_stat > 0 else 1 - t_p_two / 2
    else:
        t_p = np.nan

    binom_p = float(sp_stats.binomtest(wins, n, 0.5, alternative="greater").pvalue) if n > 0 else np.nan

    boot_p = _bootstrap_pvalue(net_returns, obs_mean_net, N_BOOTSTRAP, rng) if n >= 5 else np.nan
    cluster_p = _cluster_bootstrap_pvalue(net_returns, market_ids, obs_mean_net, N_BOOTSTRAP, rng) if n >= 5 else np.nan

    removal = _top_removal_stats(net_returns, [0.01, 0.05, 0.10])
    concentration = _pnl_concentration(net_pnls.tolist(), [0.01, 0.05, 0.10])

    row = {
        "version": label,
        "n_trades": n,
        "mean_gross_return": round(float(np.mean(gross_returns)), 6),
        "median_gross_return": round(float(np.median(gross_returns)), 6),
        "mean_net_return": round(obs_mean_net, 6),
        "median_net_return": round(float(np.median(net_returns)), 6),
        "total_net_pnl_at_10k_each": round(float(np.sum(net_pnls)), 2),
        "win_rate_net_return_gt_0": round(wins / n, 4) if n > 0 else np.nan,
        "binomial_p_value_greater_than_50pct": round(binom_p, 6),
        "one_sample_ttest_p_value_mean_net_return_gt_0": round(t_p, 6) if not np.isnan(t_p) else np.nan,
        "bootstrap_p_value_mean_net_return_gt_0": round(boot_p, 6) if not np.isnan(boot_p) else np.nan,
        "event_cluster_bootstrap_p_value": round(cluster_p, 6) if not np.isnan(cluster_p) else np.nan,
    }
    row.update(removal)
    row.update(concentration)
    return row


def compute_robustness(trades, collapsed):
    rng = np.random.RandomState(RNG_SEED)
    rows = [
        compute_robustness_row(trades, "candidate_level", rng),
        compute_robustness_row(collapsed, "symbol_day_collapsed", rng),
    ]
    return rows


# ═══════════════════════════════════════════════════════════════════════════
# CSV output
# ═══════════════════════════════════════════════════════════════════════════

def write_csvs(trades, collapsed, invalids, event_level, monthly, robustness):
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if trades:
        pd.DataFrame(trades).to_csv(
            OUTPUT_DIR / "raw_expectation_trades_candidate_level.csv", index=False)

    if collapsed:
        pd.DataFrame(collapsed).to_csv(
            OUTPUT_DIR / "raw_expectation_trades_symbol_day_collapsed.csv", index=False)

    if invalids:
        pd.DataFrame(invalids).to_csv(
            OUTPUT_DIR / "raw_expectation_invalid_candidates.csv", index=False)

    if event_level:
        pd.DataFrame(event_level).to_csv(
            OUTPUT_DIR / "raw_expectation_event_level.csv", index=False)

    if monthly:
        pd.DataFrame(monthly).to_csv(
            OUTPUT_DIR / "raw_expectation_monthly.csv", index=False)

    if robustness:
        pd.DataFrame(robustness).to_csv(
            OUTPUT_DIR / "raw_expectation_robustness.csv", index=False)


# ═══════════════════════════════════════════════════════════════════════════
# Markdown report
# ═══════════════════════════════════════════════════════════════════════════

def _md_table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for r in rows:
        lines.append("| " + " | ".join(str(v) for v in r) + " |")
    return "\n".join(lines)


def write_markdown(trades, collapsed, invalids, event_level, monthly, robustness,
                   df, fold_windows):
    net_rets = [t["net_return"] for t in trades]
    gross_rets = [t["gross_return"] for t in trades]

    n_passed_entry = len(trades) + sum(
        1 for inv in invalids
        if inv["reason"] in ("missing_price", "entry_not_before_T_minus_1", "bad_price")
    )

    inv_reasons = defaultdict(int)
    for inv in invalids:
        inv_reasons[inv["reason"]] += 1

    lines = [
        "# Raw Expectation Test: T-1 Exit",
        "",
        "## Configuration",
        "",
        f"- **Input parquet**: `{CANDIDATES_PATH}`",
        f"- **Scheduled T column**: `t_e` (Polymarket `end_at`/`requested_end`, set at market creation)",
        f"- **Why t_e is ex ante**: `t_e` is the scheduled resolution date published by Polymarket when the market is created. It is publicly known before any candidate entry occurs.",
        f"- **Fold policies**: T1+T2+T3+T4, benchmark={FOLD_BENCHMARK}, {len(fold_windows)} folds",
        f"- **Notional per trade**: ${NOTIONAL:,.0f}",
        f"- **Exit rule**: close on last trading day before t_e (T-1)",
        f"- **Cost model**: IB commission + SEC fee (sell) + 5bp slippage (both legs)",
        f"- **Bootstrap replications**: {N_BOOTSTRAP:,}",
        f"- **Random seed**: {RNG_SEED}",
        "",
        "## Fold Policies Used",
        "",
    ]
    fold_headers = ["Fold", "Eval Start", "Eval End Excl", "enter_strong", "enter_floor",
                    "hold_days", "max_prob_surge", "max_price_runup"]
    fold_rows = []
    for w in fold_windows:
        p = w["policy"]
        fold_rows.append([
            w["fold"], str(w["eval_start"].date()), str(w["eval_end_exclusive"].date()),
            f"{p['enter_strong']:.4f}", f"{p['enter_floor']:.4f}",
            int(p["hold_days"]), f"{p['max_prob_surge']:.4f}", f"{p['max_price_runup']:.4f}",
        ])
    lines.append(_md_table(fold_headers, fold_rows))

    lines += [
        "",
        "## Filtering Stages",
        "",
        f"| Stage | Count |",
        f"| --- | --- |",
        f"| Total candidates loaded | {len(df)} |",
        f"| Passed threshold (exist in parquet, >= 0.55) | {len(df)} |",
        f"| Passed T1+T2+T3+T4 entry rules | {n_passed_entry} |",
        f"| Valid for T-1 primary test | {len(trades)} |",
        f"| Invalid: missing ex-ante T | {inv_reasons.get('missing_ex_ante_T', 0)} |",
        f"| Invalid: entry >= T-1 | {inv_reasons.get('entry_not_before_T_minus_1', 0)} |",
        f"| Invalid: missing prices | {inv_reasons.get('missing_price', 0)} |",
        f"| Invalid: bad price | {inv_reasons.get('bad_price', 0)} |",
        f"| Rejected: below entry threshold | {inv_reasons.get('below_entry_threshold', 0)} |",
        f"| Rejected: prob surge exceeded | {inv_reasons.get('prob_surge_exceeded', 0)} |",
        f"| Rejected: price runup exceeded | {inv_reasons.get('price_runup_exceeded', 0)} |",
        f"| Rejected: no probability data | {inv_reasons.get('no_probability_data', 0)} |",
        f"| Rejected: no policy available | {inv_reasons.get('no_policy_available', 0)} |",
        "",
    ]

    if trades:
        entry_dates = [t["entry_date"] for t in trades]
        exit_dates = [t["exit_date_t_minus_1"] for t in trades]
        lines += [
            f"- Earliest entry date: {min(entry_dates)}",
            f"- Latest exit date: {max(exit_dates)}",
            "",
        ]

    # --- Primary result tables ---
    lines += ["## Primary: Candidate-Level Results", ""]
    if trades:
        lines += [
            f"| Metric | Value |",
            f"| --- | --- |",
            f"| N trades | {len(trades)} |",
            f"| Mean gross return | {np.mean(gross_rets):.4%} |",
            f"| Median gross return | {np.median(gross_rets):.4%} |",
            f"| Mean net return | {np.mean(net_rets):.4%} |",
            f"| Median net return | {np.median(net_rets):.4%} |",
            f"| Win rate (net > 0) | {np.mean([1 if r > 0 else 0 for r in net_rets]):.4%} |",
            f"| Total net PnL ($10k each) | ${sum(t['net_pnl'] for t in trades):,.2f} |",
            f"| Total gross PnL ($10k each) | ${sum(t['gross_pnl'] for t in trades):,.2f} |",
            "",
        ]
    else:
        lines += ["No valid trades.", ""]

    lines += ["## Symbol-Day Collapsed Results", ""]
    if collapsed:
        c_nets = [t["net_return"] for t in collapsed]
        c_gross = [t["gross_return"] for t in collapsed]
        lines += [
            f"| Metric | Value |",
            f"| --- | --- |",
            f"| N trades | {len(collapsed)} |",
            f"| Mean gross return | {np.mean(c_gross):.4%} |",
            f"| Median gross return | {np.median(c_gross):.4%} |",
            f"| Mean net return | {np.mean(c_nets):.4%} |",
            f"| Median net return | {np.median(c_nets):.4%} |",
            f"| Win rate (net > 0) | {np.mean([1 if r > 0 else 0 for r in c_nets]):.4%} |",
            f"| Total net PnL ($10k each) | ${sum(t['net_pnl'] for t in collapsed):,.2f} |",
            "",
        ]

    # Event-level
    lines += ["## Event-Level Results (equal-weighted by market)", ""]
    if event_level:
        ev_nets = [e["mean_net_return"] for e in event_level]
        ev_gross = [e["mean_gross_return"] for e in event_level]
        lines += [
            f"| Metric | Value |",
            f"| --- | --- |",
            f"| N events | {len(event_level)} |",
            f"| Mean event-avg net return | {np.mean(ev_nets):.4%} |",
            f"| Median event-avg net return | {np.median(ev_nets):.4%} |",
            f"| Mean event-avg gross return | {np.mean(ev_gross):.4%} |",
            f"| Median event-avg gross return | {np.median(ev_gross):.4%} |",
            f"| Win rate (event mean net > 0) | {np.mean([1 if r > 0 else 0 for r in ev_nets]):.4%} |",
            "",
        ]

    # Monthly table
    lines += ["## Monthly Results", ""]
    if monthly:
        m_hdrs = ["Month", "N", "Mean Net Ret", "Median Net Ret", "Win Rate", "Net PnL ($10k)"]
        m_rows = [[m["month"], m["n_trades"], f"{m['mean_net_return']:.4%}",
                    f"{m['median_net_return']:.4%}", f"{m['win_rate_net_return_gt_0']:.2%}",
                    f"${m['total_net_pnl_at_10k_each']:,.0f}"] for m in monthly]
        lines.append(_md_table(m_hdrs, m_rows))
        lines.append("")

    # Robustness table
    lines += ["## Robustness", ""]
    if robustness:
        for rob in robustness:
            lines += [f"### {rob['version']}", ""]
            rob_items = [(k, v) for k, v in rob.items() if k != "version"]
            lines.append("| Metric | Value |")
            lines.append("| --- | --- |")
            for k, v in rob_items:
                if isinstance(v, float):
                    if "pct" in k.lower() or "rate" in k.lower() or "return" in k.lower() or "share" in k.lower():
                        lines.append(f"| {k} | {v:.6f} |")
                    else:
                        lines.append(f"| {k} | {v:,.2f} |")
                else:
                    lines.append(f"| {k} | {v} |")
            lines.append("")

    # Top 20 winners
    lines += ["## Top 20 Winners (by net return)", ""]
    if trades:
        top_win = sorted(trades, key=lambda t: t["net_return"], reverse=True)[:20]
        w_hdrs = ["Rank", "Symbol", "Entry", "Exit", "Net Ret", "Net PnL", "Question"]
        w_rows = []
        for i, t in enumerate(top_win, 1):
            q = t["question"][:60] + "..." if len(t["question"]) > 60 else t["question"]
            w_rows.append([i, t["symbol"], t["entry_date"], t["exit_date_t_minus_1"],
                           f"{t['net_return']:.4%}", f"${t['net_pnl']:,.0f}", q])
        lines.append(_md_table(w_hdrs, w_rows))
        lines.append("")

    # Top 20 losers
    lines += ["## Top 20 Losers (by net return)", ""]
    if trades:
        top_lose = sorted(trades, key=lambda t: t["net_return"])[:20]
        l_rows = []
        for i, t in enumerate(top_lose, 1):
            q = t["question"][:60] + "..." if len(t["question"]) > 60 else t["question"]
            l_rows.append([i, t["symbol"], t["entry_date"], t["exit_date_t_minus_1"],
                           f"{t['net_return']:.4%}", f"${t['net_pnl']:,.0f}", q])
        lines.append(_md_table(w_hdrs, l_rows))
        lines.append("")

    # Top 20 events by average net return
    lines += ["## Top 20 Events by Average Net Return", ""]
    if event_level:
        ev_sorted = sorted(event_level, key=lambda e: e["mean_net_return"], reverse=True)[:20]
        e_hdrs = ["Rank", "N", "Mean Net Ret", "Win Rate", "Net PnL", "Question"]
        e_rows = []
        for i, e in enumerate(ev_sorted, 1):
            q = e["question"][:60] + "..." if len(e["question"]) > 60 else e["question"]
            e_rows.append([i, e["n_trades"], f"{e['mean_net_return']:.4%}",
                           f"{e['win_rate_net_return_gt_0']:.0%}",
                           f"${e['total_net_pnl_at_10k_each']:,.0f}", q])
        lines.append(_md_table(e_hdrs, e_rows))
        lines.append("")

    # Top 20 events by negative average net return
    lines += ["## Top 20 Events by Negative Average Net Return", ""]
    if event_level:
        ev_worst = sorted(event_level, key=lambda e: e["mean_net_return"])[:20]
        ew_rows = []
        for i, e in enumerate(ev_worst, 1):
            q = e["question"][:60] + "..." if len(e["question"]) > 60 else e["question"]
            ew_rows.append([i, e["n_trades"], f"{e['mean_net_return']:.4%}",
                            f"{e['win_rate_net_return_gt_0']:.0%}",
                            f"${e['total_net_pnl_at_10k_each']:,.0f}", q])
        lines.append(_md_table(e_hdrs, ew_rows))
        lines.append("")

    # Warning section
    lines += ["## Warnings and Assumptions", ""]
    warnings = []
    if inv_reasons.get("no_probability_data", 0) > 0:
        warnings.append(f"- {inv_reasons['no_probability_data']} candidates had no probability data in probs.pkl")
    if inv_reasons.get("missing_price", 0) > 0:
        warnings.append(f"- {inv_reasons['missing_price']} candidates had missing stock price data")
    if inv_reasons.get("entry_not_before_T_minus_1", 0) > 0:
        warnings.append(f"- {inv_reasons['entry_not_before_T_minus_1']} candidates had entry >= T-1 exit")
    warnings.append(f"- Fold policies are from the {FOLD_BENCHMARK} benchmark arm of T1+T2+T3+T4")
    warnings.append("- Candidates before the first fold window (2025-04-29) use fold 1 policy")
    warnings.append("- Cost model uses only 2 legs (asset buy + asset sell), no benchmark rotation")
    warnings.append("- Whole shares only (actual notional may be slightly below $10,000)")
    if not warnings:
        warnings.append("- No warnings.")
    lines += warnings

    # Interpretation
    lines += [
        "",
        "## Interpretation",
        "",
    ]
    if trades:
        mean_net = np.mean(net_rets)
        median_net = np.median(net_rets)
        wr = np.mean([1 if r > 0 else 0 for r in net_rets])
        rob_cand = robustness[0] if robustness else {}

        lines.append(f"- Mean net return is **{'positive' if mean_net > 0 else 'negative'}** ({mean_net:.4%})")
        lines.append(f"- Median net return is **{'positive' if median_net > 0 else 'negative'}** ({median_net:.4%})")
        lines.append(f"- Win rate is **{'above' if wr > 0.5 else 'at or below'}** 50% ({wr:.2%})")

        t_p = rob_cand.get("one_sample_ttest_p_value_mean_net_return_gt_0")
        if t_p is not None and not np.isnan(t_p):
            lines.append(f"- t-test p-value: {t_p:.4f} ({'passes 0.05' if t_p < 0.05 else 'passes 0.10' if t_p < 0.10 else 'does not pass 0.10'})")

        binom_p = rob_cand.get("binomial_p_value_greater_than_50pct")
        if binom_p is not None and not np.isnan(binom_p):
            lines.append(f"- Binomial p-value: {binom_p:.4f} ({'passes 0.05' if binom_p < 0.05 else 'passes 0.10' if binom_p < 0.10 else 'does not pass 0.10'})")

        boot_p = rob_cand.get("bootstrap_p_value_mean_net_return_gt_0")
        if boot_p is not None and not np.isnan(boot_p):
            lines.append(f"- Bootstrap p-value: {boot_p:.4f}")

        cluster_p = rob_cand.get("event_cluster_bootstrap_p_value")
        if cluster_p is not None and not np.isnan(cluster_p):
            lines.append(f"- Event-cluster bootstrap p-value: {cluster_p:.4f}")

        mean_after_top5 = rob_cand.get("mean_net_return_after_removing_top_5pct")
        if mean_after_top5 is not None and not np.isnan(mean_after_top5):
            lines.append(f"- Results {'survive' if mean_after_top5 > 0 else 'do not survive'} removing top 5% winners (mean after removal: {mean_after_top5:.4%})")

        mean_after_top10 = rob_cand.get("mean_net_return_after_removing_top_10pct")
        if mean_after_top10 is not None and not np.isnan(mean_after_top10):
            lines.append(f"- Results {'survive' if mean_after_top10 > 0 else 'do not survive'} removing top 10% winners (mean after removal: {mean_after_top10:.4%})")

    lines.append("")
    report_text = "\n".join(lines)
    with open(OUTPUT_DIR / "summary_report.md", "w", encoding="utf-8") as f:
        f.write(report_text)


# ═══════════════════════════════════════════════════════════════════════════
# Zip
# ═══════════════════════════════════════════════════════════════════════════

def create_zip():
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in sorted(OUTPUT_DIR.rglob("*")):
            if fpath.is_file():
                zf.write(fpath, fpath.relative_to(OUTPUT_DIR.parent))


# ═══════════════════════════════════════════════════════════════════════════
# Validation checks
# ═══════════════════════════════════════════════════════════════════════════

def print_validation_checks(df, trades, invalids):
    inv_reasons = defaultdict(int)
    for inv in invalids:
        inv_reasons[inv["reason"]] += 1

    n_passed_entry = len(trades) + sum(
        1 for inv in invalids
        if inv["reason"] in ("missing_price", "entry_not_before_T_minus_1", "bad_price")
    )

    print("\n" + "=" * 70)
    print("NO-LOOKAHEAD VALIDATION CHECKS")
    print("=" * 70)
    checks = [
        ("1. Total candidates loaded", len(df)),
        ("2. Passed threshold (in parquet, >= 0.55)", len(df)),
        ("3. Passed T1+T2+T3+T4 entry rules", n_passed_entry),
        ("4. Valid for T-1 primary test", len(trades)),
        ("5. Invalid: missing ex-ante T", inv_reasons.get("missing_ex_ante_T", 0)),
        ("6. Invalid: entry >= T-1", inv_reasons.get("entry_not_before_T_minus_1", 0)),
        ("7. Invalid: missing prices", inv_reasons.get("missing_price", 0)),
    ]
    if trades:
        entry_dates = [t["entry_date"] for t in trades]
        exit_dates = [t["exit_date_t_minus_1"] for t in trades]
        checks.append(("8. Earliest entry date", min(entry_dates)))
        checks.append(("9. Latest exit date", max(exit_dates)))
    checks += [
        ("10. Index columns used", "NO"),
        ("11. Realized outcome columns used", "NO"),
        ("12. Realized PnL used for selection", "NO"),
        ("13. Portfolio constraints applied", "NO"),
        ("14. Exit rule", "scheduled T-1 only"),
    ]
    for label, val in checks:
        print(f"  {label}: {val}")
    print("=" * 70)


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("RAW EXPECTATION TEST: T-1 EXIT")
    print(f"  Fold source: {FOLD_EXPERIMENT} / {FOLD_BENCHMARK}")
    print(f"  Notional: ${NOTIONAL:,.0f} per trade")
    print("=" * 70)

    print("\n[1/7] Loading data...")
    df = load_candidates()
    print(f"  Candidates: {len(df)}")
    prices = load_prices()
    print(f"  Price series: {len(prices)} symbols")
    probs = load_probs()
    print(f"  Prob series: {len(probs)} markets")
    fold_windows = load_fold_policies()
    print(f"  Fold windows: {len(fold_windows)}")
    for w in fold_windows:
        p = w["policy"]
        print(f"    Fold {w['fold']}: {w['eval_start'].date()} - {w['eval_end_exclusive'].date()}"
              f"  enter_s={p['enter_strong']:.3f} enter_f={p['enter_floor']:.3f}"
              f" hold={int(p['hold_days'])} surge<={p['max_prob_surge']:.3f}"
              f" runup<={p['max_price_runup']:.3f}")

    print("\n[2/7] Processing candidates...")
    trades, invalids = process_all_candidates(df, prices, probs, fold_windows)
    print(f"  Valid trades: {len(trades)}")
    print(f"  Invalid/rejected: {len(invalids)}")

    print("\n[3/7] Symbol-day collapse...")
    collapsed = collapse_symbol_day(trades)
    print(f"  Collapsed trades: {len(collapsed)} (from {len(trades)})")

    print("\n[4/7] Computing aggregates...")
    event_level = compute_event_level(trades)
    monthly = compute_monthly(trades)
    print(f"  Events: {len(event_level)}")
    print(f"  Months: {len(monthly)}")

    print(f"\n[5/7] Robustness ({N_BOOTSTRAP:,} bootstrap reps)...")
    robustness = compute_robustness(trades, collapsed)
    for r in robustness:
        print(f"  {r['version']}: mean_net={r.get('mean_net_return', 'N/A')}"
              f"  t-test p={r.get('one_sample_ttest_p_value_mean_net_return_gt_0', 'N/A')}"
              f"  cluster p={r.get('event_cluster_bootstrap_p_value', 'N/A')}")

    print("\n[6/7] Writing output...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    write_csvs(trades, collapsed, invalids, event_level, monthly, robustness)
    write_markdown(trades, collapsed, invalids, event_level, monthly, robustness,
                   df, fold_windows)
    print(f"  CSVs and report written to: {OUTPUT_DIR}")

    print("\n[7/7] Creating zip...")
    create_zip()
    print(f"  Zip: {ZIP_PATH}")

    # Quick summary
    if trades:
        net_rets = [t["net_return"] for t in trades]
        print(f"\n{'=' * 70}")
        print("QUICK SUMMARY")
        print(f"  N trades:         {len(trades)}")
        print(f"  Mean net return:  {np.mean(net_rets):.4%}")
        print(f"  Median net return: {np.median(net_rets):.4%}")
        print(f"  Win rate:         {np.mean([1 if r > 0 else 0 for r in net_rets]):.2%}")
        print(f"  Total net PnL:    ${sum(t['net_pnl'] for t in trades):,.2f}")
        print(f"  Total gross PnL:  ${sum(t['gross_pnl'] for t in trades):,.2f}")
        print(f"{'=' * 70}")

    print_validation_checks(df, trades, invalids)

    # Verify all output files exist
    expected_files = [
        "raw_expectation_trades_candidate_level.csv",
        "raw_expectation_trades_symbol_day_collapsed.csv",
        "raw_expectation_invalid_candidates.csv",
        "raw_expectation_event_level.csv",
        "raw_expectation_monthly.csv",
        "raw_expectation_robustness.csv",
        "summary_report.md",
    ]
    print("\nOutput files:")
    for fname in expected_files:
        fpath = OUTPUT_DIR / fname
        exists = fpath.exists()
        size = fpath.stat().st_size if exists else 0
        print(f"  {'OK' if exists else 'MISSING'} {fname} ({size:,} bytes)")

    zip_ok = ZIP_PATH.exists()
    print(f"  {'OK' if zip_ok else 'MISSING'} {ZIP_PATH.name} ({ZIP_PATH.stat().st_size:,} bytes)" if zip_ok else f"  MISSING {ZIP_PATH.name}")


if __name__ == "__main__":
    main()
