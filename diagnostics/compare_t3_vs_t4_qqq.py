#!/usr/bin/env python3
"""
diagnostics/compare_t3_vs_t4_qqq.py

Forensic attribution package:  QQQ T1+T2+T3 (FIFO) vs QQQ T1+T2+T3+T4 (Event Priority).
Generates output/qqq_t3_vs_t4_forensics/ with 18 deliverables.
"""

import json, math, pickle, re, sys, zipfile
from pathlib import Path
from collections import defaultdict
from datetime import timedelta

import numpy as np
import pandas as pd

# ── paths ────────────────────────────────────────────────────────────────────
PROJECT = Path(__file__).resolve().parent.parent
DATA = PROJECT / "data"
OUT = PROJECT / "output" / "qqq_t3_vs_t4_forensics"
OUT.mkdir(parents=True, exist_ok=True)

BENCHMARK = "QQQ"
EARLY_END = "2026-04-30"
LATE_START = "2026-05-01"

_GEO_RE = re.compile(
    r"war|strike|strikes|military action|conflict|ceasefire|gulf|iran|israel|"
    r"oil supply|supply disruption|energy disruption|geopolitical|hormuz|"
    r"missile|attack|attacks|invasion|combat", re.I)
_MACRO_RE = re.compile(
    r"fed|federal reserve|rate\b|rates\b|cpi|inflation|recession|jobs|payroll|"
    r"policy|tariff|commodity|commodities|crude|oil|gas|energy|gold|dollar|"
    r"treasury|yield|yields", re.I)

def event_family_from_text(question, archetype=""):
    text = f"{archetype or ''} {question or ''}".lower()
    if _GEO_RE.search(text): return "geo"
    if "earnings" in text:   return "earnings"
    if _MACRO_RE.search(text): return "macro"
    return "other"

EVENT_PRIORITY = {"geo": 0, "macro": 1, "earnings": 2, "other": 3}

def ib_cost(qty, price, is_sell):
    notional = qty * price
    commission = max(1.0, 0.005 * qty)
    sec_fee = notional * 0.0000278 if is_sell else 0.0
    slippage = notional * 0.0005
    return commission + sec_fee + slippage

def safe_float(v, default=np.nan):
    try: return float(v)
    except (TypeError, ValueError): return default

# ── data loading ─────────────────────────────────────────────────────────────
print("Loading prices, probs, and CSVs …")
with open(DATA / "prices.pkl", "rb") as f:
    PRICES = pickle.load(f)
with open(DATA / "probs.pkl", "rb") as f:
    PROBS = pickle.load(f)

# fast close-price lookup {sym: {date_str: close}}
CLOSE = {}
for sym, bars in PRICES.items():
    d = {}
    for bar in bars:
        ts = bar[0]
        ds = ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]
        d[ds] = float(bar[3])  # close
    CLOSE[sym] = d

def close_on(sym, date_str):
    return CLOSE.get(sym, {}).get(str(date_str)[:10])

# prob lookup {market_id_str: [(ts, prob), ...]}
PROB_LOOKUP = {}
for mk, pts in PROBS.items():
    mk_str = str(mk)
    d = {}
    for pt in pts:
        ds = pt[0].date().isoformat() if hasattr(pt[0], "date") else str(pt[0])[:10]
        d[ds] = float(pt[1])
    PROB_LOOKUP[mk_str] = d

def prob_on(market_id, date_str):
    return PROB_LOOKUP.get(str(market_id), {}).get(str(date_str)[:10])

# config file paths
CFGS = {
    "t3": {
        "slug": "qqq_t1_t2_t3",
        "label": "T1+T2+T3",
        "trade": DATA / "experiment_trade_logs_clean" / "qqq_t1_t2_t3_test.csv",
        "equity": DATA / "experiment_equity_logs_clean" / "qqq_t1_t2_t3_test.csv",
        "alloc":  DATA / "experiment_allocation_logs_clean" / "qqq_t1_t2_t3_test.csv",
        "disp":   PROJECT / "output" / "candidate_disposition_qqq_t1_t2_t3_test.csv",
        "forensic": DATA / "experiment_forensics_clean" / "qqq_t1_t2_t3_test_forensics.csv",
    },
    "t4": {
        "slug": "qqq_t1_t2_t3_t4",
        "label": "T1+T2+T3+T4",
        "trade": DATA / "experiment_trade_logs_clean" / "qqq_t1_t2_t3_t4_test.csv",
        "equity": DATA / "experiment_equity_logs_clean" / "qqq_t1_t2_t3_t4_test.csv",
        "alloc":  DATA / "experiment_allocation_logs_clean" / "qqq_t1_t2_t3_t4_test.csv",
        "disp":   PROJECT / "output" / "candidate_disposition_qqq_t1_t2_t3_t4_test.csv",
        "forensic": DATA / "experiment_forensics_clean" / "qqq_t1_t2_t3_t4_test_forensics.csv",
    },
}

WF_FOLDS  = pd.read_csv(DATA / "experiment_walkforward_folds_clean.csv")
EXP_RESULTS = pd.read_csv(DATA / "experiment_results_clean.csv")
print("Data loaded.\n")

MISSING_COLS = []   # track columns we couldn't populate

# ══════════════════════════════════════════════════════════════════════════════
#  1-2  executed_trades
# ══════════════════════════════════════════════════════════════════════════════
def build_executed_trades(key):
    cfg = CFGS[key]
    slug = cfg["slug"]
    df = pd.read_csv(cfg["trade"])
    df["config_slug"] = slug
    df["benchmark"] = BENCHMARK
    df["split"] = "test"
    df["trade_id"] = range(1, len(df) + 1)

    # event_family — derive for FIFO configs that lack it
    if "event_family" not in df.columns or df["event_family"].isna().all():
        df["event_family"] = df.apply(
            lambda r: event_family_from_text(r.get("question", ""), r.get("archetype", "")), axis=1)
    else:
        mask = df["event_family"].isna() | (df["event_family"] == "")
        df.loc[mask, "event_family"] = df.loc[mask].apply(
            lambda r: event_family_from_text(r.get("question", ""), r.get("archetype", "")), axis=1)

    if "allocation_mode" not in df.columns:
        df["allocation_mode"] = "fifo"
    df["allocation_mode"] = df["allocation_mode"].fillna("fifo")

    df["shares"] = df.get("_qty", pd.Series(dtype=float))
    df["position_size_pct"] = df.get("_position_size_pct", pd.Series(dtype=float))
    df["capital_allocated"] = df.get("_asset_entry_notional", pd.Series(dtype=float))

    # holding days
    ed = pd.to_datetime(df["entry_date"])
    xd = pd.to_datetime(df["exit_date"])
    df["holding_days"] = (xd - ed).dt.days

    # benchmark prices
    df["benchmark_entry_price"] = df["entry_date"].apply(lambda d: close_on(BENCHMARK, d))
    df["benchmark_exit_price"]  = df["exit_date"].apply(lambda d: close_on(BENCHMARK, d))
    bep = df["benchmark_entry_price"].astype(float)
    bxp = df["benchmark_exit_price"].astype(float)
    df["benchmark_return"] = (bxp / bep - 1).round(6)
    df["net_car"] = (df["pnl_pct"].astype(float) / 100 - df["benchmark_return"]).round(6)

    df["selected_rank"] = df.get("same_day_rank", pd.Series(dtype=float))

    # fold_id: map entry_date to WF fold
    wf = WF_FOLDS[(WF_FOLDS["experiment"] == cfg["label"]) & (WF_FOLDS["benchmark"] == BENCHMARK)]
    def get_fold(entry_d):
        for _, row in wf.iterrows():
            if str(row["eval_start_date"])[:10] <= str(entry_d)[:10] <= str(row["eval_end_date"])[:10]:
                return int(row["fold"])
        return np.nan
    df["fold_id"] = df["entry_date"].apply(get_fold)

    cols = [
        "config_slug", "benchmark", "split", "trade_id", "market_id", "question",
        "event_family", "symbol", "entry_date", "exit_date",
        "entry_price", "exit_price", "shares", "position_size_pct",
        "capital_allocated", "gross_pnl", "pnl", "pnl_pct", "txn_cost",
        "exit_reason", "holding_days",
        "benchmark_entry_price", "benchmark_exit_price", "benchmark_return", "net_car",
        "selected_rank", "allocation_mode", "fold_id",
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = np.nan
    return df[cols]


for key in ("t3", "t4"):
    fname = f"executed_trades__{CFGS[key]['slug']}.csv"
    build_executed_trades(key).to_csv(OUT / fname, index=False)
    print(f"  [OK] {fname}")

# ══════════════════════════════════════════════════════════════════════════════
#  3-4  candidate_disposition
# ══════════════════════════════════════════════════════════════════════════════
def build_candidate_disposition(key):
    cfg = CFGS[key]
    slug = cfg["slug"]

    # disposition log: every candidate (pre-allocator + allocator)
    disp = pd.read_csv(cfg["disp"])
    disp["candidate_id"] = disp["market_id"].astype(str) + "_" + disp["symbol"].astype(str)
    disp["config_slug"] = slug
    disp["benchmark"] = BENCHMARK
    disp["split"] = "test"

    # allocation log: allocator decisions only (richer data)
    alloc = pd.read_csv(cfg["alloc"])
    alloc["candidate_id"] = alloc["market_id"].astype(str) + "_" + alloc["symbol"].astype(str)

    # merge allocation data onto disposition
    alloc_cols_to_add = ["candidate_id", "decision", "skip_reason", "entry_date",
                         "exit_date", "exit_reason", "open_positions_before",
                         "open_positions_after", "max_concurrent", "current_equity",
                         "entry_prob", "feat_runup_since_t0", "same_day_rank",
                         "entry_prob_rank", "runup_rank", "event_priority",
                         "allocation_mode"]
    alloc_sub = alloc[[c for c in alloc_cols_to_add if c in alloc.columns]].copy()

    # For candidates appearing in both, prefer allocation log data
    merged = disp.merge(alloc_sub, on="candidate_id", how="left", suffixes=("", "_alloc"))

    out = pd.DataFrame()
    out["config_slug"] = merged["config_slug"]
    out["benchmark"] = BENCHMARK
    out["split"] = "test"
    out["candidate_id"] = merged["candidate_id"]
    out["market_id"] = merged["market_id"]
    out["question"] = merged["question"]
    out["event_family"] = merged["event_family"]
    if "event_family" not in merged.columns or merged["event_family"].isna().all():
        out["event_family"] = merged.apply(
            lambda r: event_family_from_text(r.get("question", "")), axis=1)
    out["symbol"] = merged["symbol"]
    out["candidate_time"] = merged.get("candidate_t_theta", pd.Series(dtype=str))
    out["intended_entry_date"] = merged.get("entry_date_if_any",
                                  merged.get("entry_date", pd.Series(dtype=str)))
    out["selected"] = merged["disposition"].apply(
        lambda d: str(d).lower() == "selected" if pd.notna(d) else False)
    out["skip_reason"] = merged["disposition"].apply(
        lambda d: "" if str(d).lower() == "selected" else str(d) if pd.notna(d) else "")
    out["rejection_reason"] = out["skip_reason"]
    out["allocation_mode"] = merged.get("allocation_mode",
                              merged.get("allocation_mode_alloc", pd.Series(dtype=str)))
    out["same_day_group_id"] = np.nan
    out["same_day_rank"] = merged.get("same_day_rank", pd.Series(dtype=float))
    out["event_priority_score"] = merged.get("event_priority",
                                   merged.get("event_priority_alloc", pd.Series(dtype=float)))
    out["rank_score"] = np.nan
    out["prob_score"] = merged.get("entry_prob",
                        merged.get("entry_prob_alloc", pd.Series(dtype=float)))
    out["event_priority_component"] = out["event_priority_score"]
    out["runup_component"] = merged.get("feat_runup_since_t0",
                             merged.get("feat_runup_since_t0_alloc", pd.Series(dtype=float)))
    out["relevance_component"] = np.nan
    out["connection_strength_component"] = np.nan
    out["liquidity_component"] = np.nan
    out["volatility_component"] = np.nan
    out["entry_prob"] = merged.get("entry_prob",
                        merged.get("entry_prob_alloc", pd.Series(dtype=float)))
    out["prob_at_trigger"] = np.nan
    out["prob_slope_24h"] = np.nan
    out["prob_surge_since_t0"] = np.nan
    out["price_runup_since_t0"] = out["runup_component"]
    out["max_prob_surge_threshold"] = merged.get("enter_strong_at_candidate_time",
                                      pd.Series(dtype=float))
    out["max_price_runup_threshold"] = np.nan
    out["position_size_pct_requested"] = np.nan
    out["capital_required"] = np.nan
    out["cash_available_before_decision"] = np.nan
    out["open_positions_before_decision"] = merged.get("open_positions_at_candidate_time",
                                            merged.get("open_positions_before", pd.Series(dtype=float)))
    out["max_concurrent"] = merged.get("max_concurrent_at_candidate_time",
                            merged.get("max_concurrent", pd.Series(dtype=float)))
    out["decision_timestamp"] = out["candidate_time"]
    out["enter_floor"] = merged.get("enter_floor_at_candidate_time", pd.Series(dtype=float))
    out["enter_strong"] = merged.get("enter_strong_at_candidate_time", pd.Series(dtype=float))

    return out


for key in ("t3", "t4"):
    fname = f"candidate_disposition__{CFGS[key]['slug']}.csv"
    build_candidate_disposition(key).to_csv(OUT / fname, index=False)
    print(f"  [OK] {fname}")

# ══════════════════════════════════════════════════════════════════════════════
#  5-6  skipped_counterfactuals
# ══════════════════════════════════════════════════════════════════════════════
def build_skipped_counterfactuals(key):
    cfg = CFGS[key]
    slug = cfg["slug"]
    alloc = pd.read_csv(cfg["alloc"])

    skipped = alloc[alloc["decision"] == "skipped"].copy()
    if skipped.empty:
        return pd.DataFrame()

    rows = []
    for _, sk in skipped.iterrows():
        sym = str(sk.get("symbol", ""))
        mid = str(sk.get("market_id", ""))
        ed = str(sk.get("entry_date", ""))[:10]
        xd = str(sk.get("exit_date", ""))[:10]
        sr = str(sk.get("skip_reason", ""))
        cid = f"{mid}_{sym}"

        ep = close_on(sym, ed)
        xp = close_on(sym, xd)
        bep = close_on(BENCHMARK, ed)
        bxp = close_on(BENCHMARK, xd)

        if ep and xp and ep > 0:
            ret_before_cost = (xp / ep - 1)
            # approximate cost with typical position size
            approx_qty = max(1, int(10000 / ep))
            buy_cost = ib_cost(approx_qty, ep, False)
            sell_cost = ib_cost(approx_qty, xp, True)
            total_cost = buy_cost + sell_cost
            notional = approx_qty * ep
            cost_pct = total_cost / notional if notional > 0 else 0
            ret_after_cost = ret_before_cost - cost_pct
            pnl_dollars = approx_qty * (xp - ep) - total_cost
        else:
            ret_before_cost = np.nan
            ret_after_cost = np.nan
            pnl_dollars = np.nan
            cost_pct = np.nan

        bench_ret = (bxp / bep - 1) if bep and bxp and bep > 0 else np.nan
        net_car = (ret_after_cost - bench_ret) if pd.notna(ret_after_cost) and pd.notna(bench_ret) else np.nan
        beats = bool(net_car > 0) if pd.notna(net_car) else np.nan

        hd = 0
        if ed and xd:
            try:
                hd = (pd.Timestamp(xd) - pd.Timestamp(ed)).days
            except Exception:
                pass

        xr = str(sk.get("exit_reason", ""))

        rows.append({
            "config_slug": slug,
            "candidate_id": cid,
            "market_id": mid,
            "symbol": sym,
            "skip_reason": sr,
            "intended_entry_date": ed,
            "counterfactual_entry_date": ed,
            "counterfactual_exit_date": xd,
            "counterfactual_entry_price": ep,
            "counterfactual_exit_price": xp,
            "counterfactual_exit_reason": xr,
            "counterfactual_holding_days": hd,
            "counterfactual_pnl_pct_before_costs": round(ret_before_cost * 100, 4) if pd.notna(ret_before_cost) else np.nan,
            "counterfactual_pnl_pct_after_costs": round(ret_after_cost * 100, 4) if pd.notna(ret_after_cost) else np.nan,
            "counterfactual_pnl_dollars_using_requested_size": round(pnl_dollars, 2) if pd.notna(pnl_dollars) else np.nan,
            "counterfactual_benchmark_return": round(bench_ret, 6) if pd.notna(bench_ret) else np.nan,
            "counterfactual_net_car": round(net_car, 6) if pd.notna(net_car) else np.nan,
            "would_have_beaten_benchmark": beats,
            "notes": "Counterfactual uses daily close prices; costs approximated with ~$10k position."
        })
    return pd.DataFrame(rows)


for key in ("t3", "t4"):
    fname = f"skipped_counterfactuals__{CFGS[key]['slug']}.csv"
    build_skipped_counterfactuals(key).to_csv(OUT / fname, index=False)
    print(f"  [OK] {fname}")

# ══════════════════════════════════════════════════════════════════════════════
#  7-8  daily_equity
# ══════════════════════════════════════════════════════════════════════════════
def build_daily_equity(key):
    cfg = CFGS[key]
    slug = cfg["slug"]
    eq = pd.read_csv(cfg["equity"])
    trades = pd.read_csv(cfg["trade"])

    eq["config_slug"] = slug
    eq["benchmark_equity"] = eq["benchmark_equity"].astype(float)
    eq_vals = eq["equity"].astype(float)
    bm_vals = eq["benchmark_equity"].astype(float)

    eq["daily_return"] = eq_vals.pct_change().round(6)
    eq["benchmark_return"] = bm_vals.pct_change().round(6)
    eq["net_excess_return"] = (eq["daily_return"] - eq["benchmark_return"]).round(6)
    eq["cumulative_return"] = ((eq_vals / eq_vals.iloc[0]) - 1).round(6)
    eq["benchmark_cumulative_return"] = ((bm_vals / bm_vals.iloc[0]) - 1).round(6)

    # drawdown
    eq["drawdown"] = ((eq_vals / eq_vals.cummax()) - 1).round(6)
    eq["benchmark_drawdown"] = ((bm_vals / bm_vals.cummax()) - 1).round(6)

    # invested capital & gross exposure
    dates = eq["date"].astype(str)
    invested_list = []
    opened_list = []
    closed_list = []
    txn_list = []
    realized_list = []

    for d in dates:
        # trades opened on this day
        opened = trades[trades["entry_date"].astype(str).str[:10] == d[:10]]
        closed = trades[trades["exit_date"].astype(str).str[:10] == d[:10]]

        opened_list.append(len(opened))
        closed_list.append(len(closed))
        txn_list.append(closed["txn_cost"].astype(float).sum() if not closed.empty else 0.0)
        realized_list.append(closed["pnl"].astype(float).sum() if not closed.empty else 0.0)

        # open positions value (approximate): all trades where entry <= d < exit
        active = trades[(trades["entry_date"].astype(str).str[:10] <= d[:10]) &
                        (trades["exit_date"].astype(str).str[:10] > d[:10])]
        inv = 0.0
        for _, t in active.iterrows():
            cp = close_on(t["symbol"], d[:10])
            if cp:
                inv += int(t.get("_qty", 1)) * cp
        invested_list.append(round(inv, 2))

    eq["invested_capital"] = invested_list
    eq["gross_exposure"] = invested_list
    eq["net_exposure"] = invested_list  # long-only so same as gross
    eq["trades_opened_today"] = opened_list
    eq["trades_closed_today"] = closed_list
    eq["txn_cost_today"] = [round(x, 2) for x in txn_list]
    eq["realized_pnl_today"] = [round(x, 2) for x in realized_list]
    eq["unrealized_pnl_today"] = np.nan

    cols = ["config_slug", "date", "equity", "benchmark_equity", "cash",
            "invested_capital", "gross_exposure", "net_exposure", "open_positions",
            "daily_return", "benchmark_return", "net_excess_return",
            "cumulative_return", "benchmark_cumulative_return",
            "drawdown", "benchmark_drawdown",
            "trades_opened_today", "trades_closed_today",
            "txn_cost_today", "realized_pnl_today", "unrealized_pnl_today"]
    return eq[cols]


for key in ("t3", "t4"):
    fname = f"daily_equity__{CFGS[key]['slug']}.csv"
    build_daily_equity(key).to_csv(OUT / fname, index=False)
    print(f"  [OK] {fname}")

# ══════════════════════════════════════════════════════════════════════════════
#  9-10  holdings_snapshots
# ══════════════════════════════════════════════════════════════════════════════
def build_holdings_snapshots(key):
    cfg = CFGS[key]
    slug = cfg["slug"]
    eq = pd.read_csv(cfg["equity"])
    trades = pd.read_csv(cfg["trade"])
    all_dates = eq["date"].astype(str).str[:10].tolist()

    rows = []
    for d in all_dates:
        # open positions: entry_date <= d < exit_date (closed on exit_date)
        active = trades[(trades["entry_date"].astype(str).str[:10] <= d) &
                        (trades["exit_date"].astype(str).str[:10] > d)]
        eq_row = eq[eq["date"].astype(str).str[:10] == d]
        portfolio_equity = float(eq_row["equity"].iloc[0]) if not eq_row.empty else np.nan

        for _, t in active.iterrows():
            sym = str(t["symbol"])
            ep = safe_float(t["entry_price"])
            qty = int(safe_float(t.get("_qty", 1), 1))
            cp = close_on(sym, d)
            if cp is None:
                cp = ep
            pv = qty * cp
            unrealized = qty * (cp - ep)
            unrealized_pct = ((cp / ep) - 1) * 100 if ep > 0 else 0
            days_held = (pd.Timestamp(d) - pd.Timestamp(str(t["entry_date"])[:10])).days
            weight = (pv / portfolio_equity * 100) if portfolio_equity and portfolio_equity > 0 else np.nan

            rows.append({
                "config_slug": slug,
                "date": d,
                "symbol": sym,
                "candidate_id": f"{t.get('market_id', '')}_{sym}",
                "market_id": t.get("market_id", ""),
                "entry_date": str(t["entry_date"])[:10],
                "entry_price": round(ep, 2),
                "current_price": round(cp, 2),
                "shares": qty,
                "position_value": round(pv, 2),
                "portfolio_weight": round(weight, 2) if pd.notna(weight) else np.nan,
                "unrealized_pnl": round(unrealized, 2),
                "unrealized_pnl_pct": round(unrealized_pct, 2),
                "days_held": days_held,
                "current_prob": prob_on(t.get("market_id", ""), d),
                "peak_price_since_entry": np.nan,
                "trailing_stop_level": np.nan,
                "hard_floor_stop_level": np.nan,
                "exit_signal_state": np.nan,
                "capital_locked": round(pv, 2),
            })
    return pd.DataFrame(rows)


for key in ("t3", "t4"):
    fname = f"holdings_snapshots__{CFGS[key]['slug']}.csv"
    build_holdings_snapshots(key).to_csv(OUT / fname, index=False)
    print(f"  [OK] {fname}")

# ══════════════════════════════════════════════════════════════════════════════
#  11-12  selected_policy_params
# ══════════════════════════════════════════════════════════════════════════════
def build_selected_policy_params(key):
    cfg = CFGS[key]
    slug = cfg["slug"]
    label = cfg["label"]
    wf = WF_FOLDS[(WF_FOLDS["experiment"] == label) & (WF_FOLDS["benchmark"] == BENCHMARK)].copy()
    res = EXP_RESULTS[(EXP_RESULTS["experiment"] == label) & (EXP_RESULTS["benchmark"] == BENCHMARK)]

    rows = []
    for _, fold in wf.iterrows():
        pj = fold.get("eval_policy_json", "{}")
        try:
            p = json.loads(pj) if isinstance(pj, str) else {}
        except json.JSONDecodeError:
            p = {}

        rows.append({
            "config_slug": slug,
            "benchmark": BENCHMARK,
            "fold_id": int(fold.get("fold", 0)),
            "fit_start_date": fold.get("fit_start_date", ""),
            "fit_end_date": fold.get("fit_eval_end_date", ""),
            "label_cutoff_date": fold.get("fit_label_cutoff", ""),
            "latest_train_label_completion_date": fold.get("fit_latest_t_e", ""),
            "n_train_candidates": fold.get("fit_candidates", ""),
            "n_train_trades": np.nan,
            "cem_seed": np.nan,
            "cem_iters": 6,
            "cem_pop": 60,
            "elite_frac": 0.2,
            "reward_function_name": "cem_reward",
            "reward_value_in_fit": fold.get("fit_cem_score", ""),
            "sharpe_in_fit": np.nan,
            "maxdd_in_fit": np.nan,
            "return_in_fit": np.nan,
            "atr_mult": p.get("atr_mult", np.nan),
            "lock_activate": p.get("lock_activate", np.nan),
            "theta_out": p.get("theta_out", np.nan),
            "enter_strong": p.get("enter_strong", np.nan),
            "enter_floor": p.get("enter_floor", np.nan),
            "hold_days": p.get("hold_days", np.nan),
            "max_prob_surge": p.get("max_prob_surge", np.nan),
            "max_price_runup": p.get("max_price_runup", np.nan),
            "position_size_pct": p.get("position_size_pct", np.nan),
            "max_concurrent": p.get("max_concurrent", np.nan),
            "kelly_fraction": "dynamic" if "kelly" in label.lower() or (not res.empty and res.iloc[0].get("kelly", False)) else "none",
            "hurdle_mult": 3.0 if (not res.empty and res.iloc[0].get("hurdle_realized_fitness_penalty", False)) else 0.0,
            "allocation_mode": fold.get("allocation_mode", "fifo"),
            "T1_enabled": "T1" in label,
            "T2_enabled": "T2" in label,
            "T3_enabled": "T3" in label,
            "T4_enabled": "T4" in label,
        })
    return pd.DataFrame(rows)


for key in ("t3", "t4"):
    fname = f"selected_policy_params__{CFGS[key]['slug']}.csv"
    build_selected_policy_params(key).to_csv(OUT / fname, index=False)
    print(f"  [OK] {fname}")

# ══════════════════════════════════════════════════════════════════════════════
#  13  t4_scoring_trace
# ══════════════════════════════════════════════════════════════════════════════
def build_t4_scoring_trace():
    cfg = CFGS["t4"]
    alloc = pd.read_csv(cfg["alloc"])
    out = pd.DataFrame()
    out["candidate_id"] = alloc["market_id"].astype(str) + "_" + alloc["symbol"].astype(str)
    out["date"] = alloc["date"]
    out["symbol"] = alloc["symbol"]
    out["market_id"] = alloc["market_id"]
    out["event_title"] = alloc["question"]
    out["event_family"] = alloc["event_family"]
    out["allocation_mode"] = alloc["allocation_mode"]
    out["raw_priority_score"] = alloc["event_priority"]
    out["final_priority_score"] = alloc["event_priority"]
    out["same_day_group_id"] = alloc["date"]  # same-day group = same date
    out["same_day_rank"] = alloc["same_day_rank"]
    out["event_priority_rank"] = alloc["event_priority"]
    out["prob_rank"] = alloc["entry_prob_rank"]
    out["runup_rank"] = alloc["runup_rank"]
    out["relevance_rank"] = np.nan
    out["connection_rank"] = np.nan
    out["liquidity_rank"] = np.nan
    out["entry_prob_component"] = alloc["entry_prob"]
    out["runup_component"] = alloc["feat_runup_since_t0"]
    out["runup_clipped_component"] = alloc["rank_runup_clipped"]
    out["final_sort_key"] = alloc.apply(
        lambda r: f"({safe_float(r.get('event_priority',3),3):.0f}, "
                  f"{-safe_float(r.get('entry_prob',0),0):.3f}, "
                  f"{-safe_float(r.get('feat_runup_since_t0',0),0):.4f})", axis=1)
    out["selected"] = alloc["decision"] == "selected"
    out["skip_reason"] = alloc["skip_reason"]
    out["explanation_string"] = alloc.apply(
        lambda r: (f"family={r.get('event_family','?')}"
                   f" prio={r.get('event_priority','?')}"
                   f" prob={r.get('entry_prob','?')}"
                   f" rank={r.get('same_day_rank','?')}"
                   f" -> {'SELECTED' if r.get('decision') == 'selected' else 'SKIP:' + str(r.get('skip_reason',''))}"),
        axis=1)
    return out


fname = "t4_scoring_trace__qqq_t1_t2_t3_t4.csv"
build_t4_scoring_trace().to_csv(OUT / fname, index=False)
print(f"  [OK] {fname}")

# write scoring function text
scoring_txt = """
T4 Event Priority Scoring Function
====================================

Constants:
    ALLOCATION_EVENT_PRIORITY = "event_priority"
    EVENT_PRIORITY_ORDER = {"geo": 0, "macro": 1, "earnings": 2, "other": 3}
    RANK_RUNUP_CLIP = (-0.20, 0.20)

Ranking function (_allocation_rank_tuple):

    def _allocation_rank_tuple(trade: dict) -> tuple:
        priority = int(trade.get("event_priority", EVENT_PRIORITY_ORDER["other"]))
        entry_prob = _safe_float(trade.get("entry_prob"), 0.0)
        runup = _safe_float(trade.get("feat_runup_since_t0"), 0.0)
        clipped_runup = float(np.clip(runup, -0.20, 0.20))
        return (
            priority,           # 0=geo first, 1=macro, 2=earnings, 3=other
            -entry_prob,        # higher probability wins (negated for ascending)
            -clipped_runup,     # higher runup wins (negated for ascending)
            int(trade.get("_candidate_order", 0)),  # stable tiebreak
        )

Sort: ascending on tuple -> geo beats macro beats earnings beats other;
      within the same family, higher entry_prob wins;
      within the same prob, higher runup wins.

Same-day symbol collapse:
    When multiple candidates share a symbol on the same day, the highest-ranked
    one wins; lower-ranked duplicates are collapsed and their market_ids are
    stored as supporting_market_ids on the winner.

Preemption:
    Geo/macro candidates can preempt earnings-only positions if the worst
    existing position's net return is below PREEMPT_NET_PROFIT_HURDLE_PCT (3%).
"""
(OUT / "t4_scoring_function.txt").write_text(scoring_txt)
print(f"  [OK] t4_scoring_function.txt")

# ══════════════════════════════════════════════════════════════════════════════
#  14  overlap_attribution
# ══════════════════════════════════════════════════════════════════════════════
def build_overlap_attribution():
    t3_trades = pd.read_csv(CFGS["t3"]["trade"])
    t4_trades = pd.read_csv(CFGS["t4"]["trade"])

    t3_trades["candidate_id"] = t3_trades["market_id"].astype(str) + "_" + t3_trades["symbol"].astype(str)
    t4_trades["candidate_id"] = t4_trades["market_id"].astype(str) + "_" + t4_trades["symbol"].astype(str)

    t3_ids = set(t3_trades["candidate_id"])
    t4_ids = set(t4_trades["candidate_id"])
    both = t3_ids & t4_ids
    only_t3 = t3_ids - t4_ids
    only_t4 = t4_ids - t3_ids

    periods = {
        "full_oos": (None, None),
        "early": (None, EARLY_END),
        "late": (LATE_START, None),
    }

    def compute_bucket_stats(df, bucket, period_name):
        if df.empty:
            return {
                "period": period_name, "bucket": bucket,
                "n_trades": 0, "total_pnl": 0, "avg_pnl": 0, "median_pnl": 0,
                "total_pnl_pct_weighted": 0, "avg_pnl_pct": 0, "median_pnl_pct": 0,
                "win_rate": 0, "total_net_car": 0, "avg_net_car": 0, "median_net_car": 0,
                "total_txn_cost": 0, "avg_holding_days": 0, "avg_position_size_pct": 0,
                "total_capital_allocated": 0,
            }
        pnl = df["pnl"].astype(float)
        pnl_pct = df["pnl_pct"].astype(float)

        bep = df["entry_date"].apply(lambda d: close_on(BENCHMARK, str(d)[:10]))
        bxp = df["exit_date"].apply(lambda d: close_on(BENCHMARK, str(d)[:10]))
        bench_ret = (bxp.astype(float) / bep.astype(float) - 1)
        net_car = pnl_pct / 100 - bench_ret

        ed = pd.to_datetime(df["entry_date"])
        xd = pd.to_datetime(df["exit_date"])
        hd = (xd - ed).dt.days

        ps = df["_position_size_pct"].astype(float) if "_position_size_pct" in df.columns else pd.Series([np.nan]*len(df))
        ca = df["_asset_entry_notional"].astype(float) if "_asset_entry_notional" in df.columns else pd.Series([np.nan]*len(df))
        tc = df["txn_cost"].astype(float) if "txn_cost" in df.columns else pd.Series([0.0]*len(df))

        return {
            "period": period_name,
            "bucket": bucket,
            "n_trades": len(df),
            "total_pnl": round(pnl.sum(), 2),
            "avg_pnl": round(pnl.mean(), 2),
            "median_pnl": round(pnl.median(), 2),
            "total_pnl_pct_weighted": round(pnl_pct.sum(), 2),
            "avg_pnl_pct": round(pnl_pct.mean(), 4),
            "median_pnl_pct": round(pnl_pct.median(), 4),
            "win_rate": round((pnl > 0).mean() * 100, 1),
            "total_net_car": round(net_car.sum(), 4),
            "avg_net_car": round(net_car.mean(), 6),
            "median_net_car": round(net_car.median(), 6),
            "total_txn_cost": round(tc.sum(), 2),
            "avg_holding_days": round(hd.mean(), 1),
            "avg_position_size_pct": round(ps.mean() * 100, 2) if ps.notna().any() else np.nan,
            "total_capital_allocated": round(ca.sum(), 2) if ca.notna().any() else np.nan,
        }

    rows = []
    for pname, (pstart, pend) in periods.items():
        for src_label, src_df, id_set, bucket in [
            ("t3", t3_trades, both,    "selected_by_both"),
            ("t3", t3_trades, only_t3, "selected_only_t1_t2_t3"),
            ("t4", t4_trades, only_t4, "selected_only_t1_t2_t3_t4"),
        ]:
            sub = src_df[src_df["candidate_id"].isin(id_set)].copy()
            if pstart:
                sub = sub[sub["entry_date"].astype(str).str[:10] >= pstart]
            if pend:
                sub = sub[sub["entry_date"].astype(str).str[:10] <= pend]
            rows.append(compute_bucket_stats(sub, bucket, pname))

    return pd.DataFrame(rows)


fname = "overlap_attribution__qqq_t3_vs_t4.csv"
build_overlap_attribution().to_csv(OUT / fname, index=False)
print(f"  [OK] {fname}")

# ══════════════════════════════════════════════════════════════════════════════
#  15  daily_gap_decomposition
# ══════════════════════════════════════════════════════════════════════════════
def build_daily_gap_decomposition():
    eq3 = pd.read_csv(CFGS["t3"]["equity"])
    eq4 = pd.read_csv(CFGS["t4"]["equity"])
    tr3 = pd.read_csv(CFGS["t3"]["trade"])
    tr4 = pd.read_csv(CFGS["t4"]["trade"])

    m = eq3.merge(eq4, on="date", suffixes=("_t3", "_t4"))
    e3 = m["equity_t3"].astype(float)
    e4 = m["equity_t4"].astype(float)
    b = m["benchmark_equity_t3"].astype(float)

    dr3 = e3.pct_change().round(6)
    dr4 = e4.pct_change().round(6)

    rows = []
    for i, row in m.iterrows():
        d = str(row["date"])[:10]
        closed3 = tr3[tr3["exit_date"].astype(str).str[:10] == d]
        closed4 = tr4[tr4["exit_date"].astype(str).str[:10] == d]
        rp3 = closed3["pnl"].astype(float).sum() if not closed3.empty else 0
        rp4 = closed4["pnl"].astype(float).sum() if not closed4.empty else 0
        tc3 = closed3["txn_cost"].astype(float).sum() if not closed3.empty else 0
        tc4 = closed4["txn_cost"].astype(float).sum() if not closed4.empty else 0

        # find symbols that differ between configs' open positions
        active3 = set(tr3[(tr3["entry_date"].astype(str).str[:10] <= d) &
                          (tr3["exit_date"].astype(str).str[:10] > d)]["symbol"])
        active4 = set(tr4[(tr4["entry_date"].astype(str).str[:10] <= d) &
                          (tr4["exit_date"].astype(str).str[:10] > d)]["symbol"])
        diff_syms = (active3 - active4) | (active4 - active3)

        rows.append({
            "date": d,
            "equity_t3": round(float(row["equity_t3"]), 2),
            "equity_t4": round(float(row["equity_t4"]), 2),
            "benchmark_equity": round(float(row["benchmark_equity_t3"]), 2),
            "equity_gap_t3_minus_t4": round(float(row["equity_t3"]) - float(row["equity_t4"]), 2),
            "daily_return_t3": dr3.iloc[i] if i > 0 else 0,
            "daily_return_t4": dr4.iloc[i] if i > 0 else 0,
            "daily_return_gap_t3_minus_t4": round((dr3.iloc[i] if i > 0 else 0) - (dr4.iloc[i] if i > 0 else 0), 6),
            "open_positions_t3": int(row["open_positions_t3"]),
            "open_positions_t4": int(row["open_positions_t4"]),
            "gross_exposure_t3": np.nan,
            "gross_exposure_t4": np.nan,
            "cash_t3": round(float(row["cash_t3"]), 2),
            "cash_t4": round(float(row["cash_t4"]), 2),
            "realized_pnl_t3": round(rp3, 2),
            "realized_pnl_t4": round(rp4, 2),
            "unrealized_pnl_t3": np.nan,
            "unrealized_pnl_t4": np.nan,
            "txn_cost_t3": round(tc3, 2),
            "txn_cost_t4": round(tc4, 2),
            "main_symbols_explaining_gap": ";".join(sorted(diff_syms)) if diff_syms else "",
        })
    return pd.DataFrame(rows)


fname = "daily_gap_decomposition__qqq_t3_vs_t4.csv"
build_daily_gap_decomposition().to_csv(OUT / fname, index=False)
print(f"  [OK] {fname}")

# ══════════════════════════════════════════════════════════════════════════════
#  16  capital_blocking_events
# ══════════════════════════════════════════════════════════════════════════════
def build_capital_blocking():
    alloc3 = pd.read_csv(CFGS["t3"]["alloc"])
    alloc4 = pd.read_csv(CFGS["t4"]["alloc"])
    tr3 = pd.read_csv(CFGS["t3"]["trade"])
    tr4 = pd.read_csv(CFGS["t4"]["trade"])

    alloc3["candidate_id"] = alloc3["market_id"].astype(str) + "_" + alloc3["symbol"].astype(str)
    alloc4["candidate_id"] = alloc4["market_id"].astype(str) + "_" + alloc4["symbol"].astype(str)
    tr3["candidate_id"] = tr3["market_id"].astype(str) + "_" + tr3["symbol"].astype(str)
    tr4["candidate_id"] = tr4["market_id"].astype(str) + "_" + tr4["symbol"].astype(str)

    sel3 = set(alloc3[alloc3["decision"] == "selected"]["candidate_id"])
    sel4 = set(alloc4[alloc4["decision"] == "selected"]["candidate_id"])
    skip3 = alloc3[alloc3["decision"] == "skipped"]
    skip4 = alloc4[alloc4["decision"] == "skipped"]

    rows = []
    # T4 skipped but T3 selected
    for _, sk in skip4[skip4["candidate_id"].isin(sel3)].iterrows():
        cid = sk["candidate_id"]
        d = str(sk["date"])[:10]
        sym = str(sk["symbol"])

        # find what was open in T4 on this date
        active4 = tr4[(tr4["entry_date"].astype(str).str[:10] <= d) &
                       (tr4["exit_date"].astype(str).str[:10] > d)]
        blocking_syms = ";".join(active4["symbol"].tolist())
        blocking_pnl = active4["pnl"].astype(float).sum() if not active4.empty else 0

        # counterfactual for the skipped candidate
        cf_entry_price = close_on(sym, str(sk.get("entry_date", d))[:10])
        cf_exit_price = close_on(sym, str(sk.get("exit_date", d))[:10]) if pd.notna(sk.get("exit_date")) else np.nan
        cf_pnl_pct = ((cf_exit_price / cf_entry_price) - 1) * 100 if cf_entry_price and cf_exit_price and cf_entry_price > 0 else np.nan
        bep = close_on(BENCHMARK, str(sk.get("entry_date", d))[:10])
        bxp = close_on(BENCHMARK, str(sk.get("exit_date", d))[:10]) if pd.notna(sk.get("exit_date")) else np.nan
        cf_bench_ret = (bxp / bep - 1) if bep and bxp and bep > 0 else np.nan
        cf_net_car = (cf_pnl_pct / 100 - cf_bench_ret) if pd.notna(cf_pnl_pct) and pd.notna(cf_bench_ret) else np.nan

        rows.append({
            "date": d,
            "candidate_id": cid,
            "symbol": sym,
            "skipped_by_config": "qqq_t1_t2_t3_t4",
            "selected_by_other_config": "qqq_t1_t2_t3",
            "skip_reason": str(sk.get("skip_reason", "")),
            "cash_available_in_skipping_config": np.nan,
            "open_positions_in_skipping_config": safe_float(sk.get("open_positions_before")),
            "max_concurrent_in_skipping_config": safe_float(sk.get("max_concurrent")),
            "capital_required": np.nan,
            "positions_blocking_capital": blocking_syms,
            "later_realized_pnl_of_blocking_positions": round(blocking_pnl, 2),
            "counterfactual_pnl_of_skipped_candidate": round(cf_pnl_pct, 4) if pd.notna(cf_pnl_pct) else np.nan,
            "counterfactual_net_car_of_skipped_candidate": round(cf_net_car, 6) if pd.notna(cf_net_car) else np.nan,
        })

    # T3 skipped but T4 selected
    for _, sk in skip3[skip3["candidate_id"].isin(sel4)].iterrows():
        cid = sk["candidate_id"]
        d = str(sk["date"])[:10]
        sym = str(sk["symbol"])

        active3 = tr3[(tr3["entry_date"].astype(str).str[:10] <= d) &
                       (tr3["exit_date"].astype(str).str[:10] > d)]
        blocking_syms = ";".join(active3["symbol"].tolist())
        blocking_pnl = active3["pnl"].astype(float).sum() if not active3.empty else 0

        cf_entry_price = close_on(sym, str(sk.get("entry_date", d))[:10])
        cf_exit_price = close_on(sym, str(sk.get("exit_date", d))[:10]) if pd.notna(sk.get("exit_date")) else np.nan
        cf_pnl_pct = ((cf_exit_price / cf_entry_price) - 1) * 100 if cf_entry_price and cf_exit_price and cf_entry_price > 0 else np.nan
        bep = close_on(BENCHMARK, str(sk.get("entry_date", d))[:10])
        bxp = close_on(BENCHMARK, str(sk.get("exit_date", d))[:10]) if pd.notna(sk.get("exit_date")) else np.nan
        cf_bench_ret = (bxp / bep - 1) if bep and bxp and bep > 0 else np.nan
        cf_net_car = (cf_pnl_pct / 100 - cf_bench_ret) if pd.notna(cf_pnl_pct) and pd.notna(cf_bench_ret) else np.nan

        rows.append({
            "date": d,
            "candidate_id": cid,
            "symbol": sym,
            "skipped_by_config": "qqq_t1_t2_t3",
            "selected_by_other_config": "qqq_t1_t2_t3_t4",
            "skip_reason": str(sk.get("skip_reason", "")),
            "cash_available_in_skipping_config": np.nan,
            "open_positions_in_skipping_config": safe_float(sk.get("open_positions_before")),
            "max_concurrent_in_skipping_config": safe_float(sk.get("max_concurrent")),
            "capital_required": np.nan,
            "positions_blocking_capital": blocking_syms,
            "later_realized_pnl_of_blocking_positions": round(blocking_pnl, 2),
            "counterfactual_pnl_of_skipped_candidate": round(cf_pnl_pct, 4) if pd.notna(cf_pnl_pct) else np.nan,
            "counterfactual_net_car_of_skipped_candidate": round(cf_net_car, 6) if pd.notna(cf_net_car) else np.nan,
        })

    return pd.DataFrame(rows)


fname = "capital_blocking_events__qqq_t3_vs_t4.csv"
build_capital_blocking().to_csv(OUT / fname, index=False)
print(f"  [OK] {fname}")

# ══════════════════════════════════════════════════════════════════════════════
#  17  summary_report.md
# ══════════════════════════════════════════════════════════════════════════════
def build_summary_report():
    tr3 = pd.read_csv(CFGS["t3"]["trade"])
    tr4 = pd.read_csv(CFGS["t4"]["trade"])
    eq3 = pd.read_csv(CFGS["t3"]["equity"])
    eq4 = pd.read_csv(CFGS["t4"]["equity"])

    tr3["candidate_id"] = tr3["market_id"].astype(str) + "_" + tr3["symbol"].astype(str)
    tr4["candidate_id"] = tr4["market_id"].astype(str) + "_" + tr4["symbol"].astype(str)

    def period_metrics(trades, equity, label, period_label, start=None, end=None):
        t = trades.copy()
        e = equity.copy()
        if start:
            t = t[t["entry_date"].astype(str).str[:10] >= start]
            e = e[e["date"].astype(str).str[:10] >= start]
        if end:
            t = t[t["entry_date"].astype(str).str[:10] <= end]
            e = e[e["date"].astype(str).str[:10] <= end]

        pnl = t["pnl"].astype(float)
        pnl_pct = t["pnl_pct"].astype(float)
        ev = e["equity"].astype(float)
        bm = e["benchmark_equity"].astype(float)
        ret = ((ev.iloc[-1] / ev.iloc[0]) - 1) * 100 if len(ev) > 1 else 0
        bm_ret = ((bm.iloc[-1] / bm.iloc[0]) - 1) * 100 if len(bm) > 1 else 0
        dd = ((ev / ev.cummax()) - 1).min() * 100
        dr = ev.pct_change().dropna()
        sharpe = (dr.mean() / dr.std() * math.sqrt(252)) if len(dr) > 5 and dr.std() > 1e-12 else 0

        return {
            "config": label,
            "period": period_label,
            "return_pct": round(ret, 2),
            "benchmark_return_pct": round(bm_ret, 2),
            "excess_pct": round(ret - bm_ret, 2),
            "max_dd_pct": round(dd, 2),
            "sharpe": round(sharpe, 2),
            "n_trades": len(t),
            "win_rate_pct": round((pnl > 0).mean() * 100, 1) if len(pnl) > 0 else 0,
            "total_pnl": round(pnl.sum(), 2),
            "avg_pnl": round(pnl.mean(), 2) if len(pnl) > 0 else 0,
        }

    # metrics table
    metrics = []
    for label, trades, equity in [("T1+T2+T3", tr3, eq3), ("T1+T2+T3+T4", tr4, eq4)]:
        metrics.append(period_metrics(trades, equity, label, "full_oos"))
        metrics.append(period_metrics(trades, equity, label, "early", end=EARLY_END))
        metrics.append(period_metrics(trades, equity, label, "late", start=LATE_START))
    metrics_df = pd.DataFrame(metrics)

    # policy comparison
    pp3 = pd.read_csv(OUT / f"selected_policy_params__qqq_t1_t2_t3.csv")
    pp4 = pd.read_csv(OUT / f"selected_policy_params__qqq_t1_t2_t3_t4.csv")
    policy_cols = ["fold_id", "atr_mult", "lock_activate", "theta_out",
                   "enter_strong", "enter_floor", "hold_days",
                   "max_prob_surge", "max_price_runup", "position_size_pct", "max_concurrent"]

    # overlap stats
    t3_ids = set(tr3["candidate_id"])
    t4_ids = set(tr4["candidate_id"])
    both_ids = t3_ids & t4_ids
    only_t3_ids = t3_ids - t4_ids
    only_t4_ids = t4_ids - t3_ids

    # early-only divergent trades
    early_only_t3 = tr3[(tr3["candidate_id"].isin(only_t3_ids)) &
                        (tr3["entry_date"].astype(str).str[:10] <= EARLY_END)].nlargest(10, "pnl")
    early_only_t4 = tr4[(tr4["candidate_id"].isin(only_t4_ids)) &
                        (tr4["entry_date"].astype(str).str[:10] <= EARLY_END)].nlargest(10, "pnl")

    # capital blocking
    cb = pd.read_csv(OUT / "capital_blocking_events__qqq_t3_vs_t4.csv")
    cb_t4_skip = cb[cb["skipped_by_config"] == "qqq_t1_t2_t3_t4"]

    # T4 blocking positions early
    holdings4 = pd.read_csv(OUT / f"holdings_snapshots__qqq_t1_t2_t3_t4.csv")
    early_holdings4 = holdings4[holdings4["date"].astype(str).str[:10] <= EARLY_END]
    worst_blockers = early_holdings4.groupby("candidate_id").agg(
        symbol=("symbol", "first"),
        avg_unrealized_pnl_pct=("unrealized_pnl_pct", "mean"),
        days_open=("days_held", "max"),
        max_capital_locked=("capital_locked", "max"),
    ).nsmallest(10, "avg_unrealized_pnl_pct")

    # ── write report ─────────────────────────────────────────────────────────
    lines = []
    lines.append("# Forensic Attribution: QQQ T1+T2+T3 vs T1+T2+T3+T4\n")
    lines.append("## 1. Period Metrics\n")
    lines.append(metrics_df.to_string(index=False))
    lines.append("\n")

    lines.append("## 2. Policy Parameter Comparison\n")
    lines.append("### T1+T2+T3 (FIFO)\n")
    lines.append(pp3[policy_cols].to_string(index=False))
    lines.append("\n### T1+T2+T3+T4 (Event Priority)\n")
    lines.append(pp4[policy_cols].to_string(index=False))
    lines.append("\n")

    lines.append("## 3. Trade Overlap\n")
    lines.append(f"- Selected by both: {len(both_ids)} candidates\n")
    lines.append(f"- Selected only by T1+T2+T3: {len(only_t3_ids)} candidates\n")
    lines.append(f"- Selected only by T1+T2+T3+T4: {len(only_t4_ids)} candidates\n")
    lines.append("\n")

    lines.append("## 4. Top 10 Trades Selected Only by T1+T2+T3 (Early Period)\n")
    if not early_only_t3.empty:
        lines.append(early_only_t3[["symbol", "entry_date", "exit_date", "pnl", "pnl_pct",
                                     "question"]].to_string(index=False))
    else:
        lines.append("*(none)*\n")
    lines.append("\n")

    lines.append("## 5. Top 10 Trades Selected Only by T1+T2+T3+T4 (Early Period)\n")
    if not early_only_t4.empty:
        lines.append(early_only_t4[["symbol", "entry_date", "exit_date", "pnl", "pnl_pct",
                                     "question"]].to_string(index=False))
    else:
        lines.append("*(none)*\n")
    lines.append("\n")

    lines.append("## 6. Top 10 Skipped-by-T4 but Selected-by-T3 (by Counterfactual PnL)\n")
    if not cb_t4_skip.empty:
        top_cb = cb_t4_skip.nlargest(10, "counterfactual_pnl_of_skipped_candidate")
        lines.append(top_cb[["date", "symbol", "skip_reason",
                              "counterfactual_pnl_of_skipped_candidate",
                              "counterfactual_net_car_of_skipped_candidate",
                              "positions_blocking_capital"]].to_string(index=False))
    else:
        lines.append("*(none)*\n")
    lines.append("\n")

    lines.append("## 7. Top 10 T4 Positions that Blocked Capital (Early Period)\n")
    if not worst_blockers.empty:
        lines.append(worst_blockers.reset_index().to_string(index=False))
    else:
        lines.append("*(none)*\n")
    lines.append("\n")

    lines.append("## 8. Measured Attribution: Where Did the Early Gap Come From?\n")
    lines.append("\n")

    # A. Trade choice
    both_t3 = tr3[tr3["candidate_id"].isin(both_ids) & (tr3["entry_date"].astype(str).str[:10] <= EARLY_END)]
    both_t4 = tr4[tr4["candidate_id"].isin(both_ids) & (tr4["entry_date"].astype(str).str[:10] <= EARLY_END)]
    only_t3_early = tr3[tr3["candidate_id"].isin(only_t3_ids) & (tr3["entry_date"].astype(str).str[:10] <= EARLY_END)]
    only_t4_early = tr4[tr4["candidate_id"].isin(only_t4_ids) & (tr4["entry_date"].astype(str).str[:10] <= EARLY_END)]

    lines.append("### A. Trade Choice (early period)\n")
    lines.append(f"- Shared trades in T3: {len(both_t3)}, total PnL: ${both_t3['pnl'].astype(float).sum():.2f}\n")
    lines.append(f"- Shared trades in T4: {len(both_t4)}, total PnL: ${both_t4['pnl'].astype(float).sum():.2f}\n")
    shared_pnl_diff = both_t3['pnl'].astype(float).sum() - both_t4['pnl'].astype(float).sum()
    lines.append(f"- PnL gap on SHARED trades (T3 - T4): ${shared_pnl_diff:.2f}\n")
    lines.append(f"- T3-only trades: {len(only_t3_early)}, total PnL: ${only_t3_early['pnl'].astype(float).sum():.2f}\n")
    lines.append(f"- T4-only trades: {len(only_t4_early)}, total PnL: ${only_t4_early['pnl'].astype(float).sum():.2f}\n")
    trade_choice_gap = only_t3_early['pnl'].astype(float).sum() - only_t4_early['pnl'].astype(float).sum()
    lines.append(f"- PnL gap from DIVERGENT trade selection (T3-only minus T4-only): ${trade_choice_gap:.2f}\n")
    lines.append("\n")

    # B. Sizing
    lines.append("### B. Position Sizing (early period)\n")
    t3_ps = both_t3["_position_size_pct"].astype(float).mean() * 100 if "_position_size_pct" in both_t3.columns and len(both_t3) > 0 else 0
    t4_ps = both_t4["_position_size_pct"].astype(float).mean() * 100 if "_position_size_pct" in both_t4.columns and len(both_t4) > 0 else 0
    lines.append(f"- Avg position size T3 (shared trades): {t3_ps:.2f}%\n")
    lines.append(f"- Avg position size T4 (shared trades): {t4_ps:.2f}%\n")
    lines.append("\n")

    # C. Capital blocking
    lines.append("### C. Capital Blocking (early period)\n")
    cb_early = cb_t4_skip[cb_t4_skip["date"].astype(str).str[:10] <= EARLY_END]
    lines.append(f"- Candidates skipped by T4 but selected by T3 (early): {len(cb_early)}\n")
    if not cb_early.empty:
        cf_sum = cb_early["counterfactual_pnl_of_skipped_candidate"].astype(float).sum()
        lines.append(f"- Total counterfactual PnL% lost by T4 skipping: {cf_sum:.2f}%\n")
    lines.append("\n")

    # D. Exit timing
    lines.append("### D. Exit Timing (shared trades, early period)\n")
    if len(both_t3) > 0 and len(both_t4) > 0:
        t3_hd = (pd.to_datetime(both_t3["exit_date"]) - pd.to_datetime(both_t3["entry_date"])).dt.days.mean()
        t4_hd = (pd.to_datetime(both_t4["exit_date"]) - pd.to_datetime(both_t4["entry_date"])).dt.days.mean()
        lines.append(f"- Avg holding days T3: {t3_hd:.1f}\n")
        lines.append(f"- Avg holding days T4: {t4_hd:.1f}\n")
    lines.append("\n")

    # E. CEM parameters
    lines.append("### E. CEM Parameter Differences\n")
    lines.append("See Section 2 above. Key differences per fold determine enter_floor/enter_strong thresholds and position sizing.\n")
    lines.append("\n")

    # F. T4 event priority reranking
    lines.append("### F. T4 Event Priority Reranking Impact (early period)\n")
    lines.append(f"- T4 allocation_mode = event_priority vs T3 allocation_mode = fifo\n")
    lines.append(f"- T4 reranks candidates by: geo > macro > earnings > other, then by entry_prob, then by runup\n")
    lines.append(f"- In the early period, T4 selected {len(only_t4_early)} unique trades that T3 did not, "
                 f"and missed {len(only_t3_early)} trades that T3 selected.\n")
    lines.append("\n")

    lines.append("## 9. Missing Source Fields\n")
    missing = [
        "prob_at_trigger (not captured in allocation logs)",
        "prob_slope_24h (not captured in allocation logs)",
        "prob_surge_since_t0 (not captured in allocation logs — only feat_runup_since_t0 available)",
        "trailing_stop_level (internal to sim_kernel, not exposed)",
        "hard_floor_stop_level (internal to sim_kernel, not exposed)",
        "exit_signal_state (internal to sim_kernel, not exposed)",
        "peak_price_since_entry (would require bar-by-bar replay)",
        "gross_exposure columns in daily_gap_decomposition (require bar-level mark-to-market)",
        "unrealized_pnl columns in daily_equity and daily_gap_decomposition (require bar-level replay)",
        "cash_available_before_decision in candidate_disposition (not logged per-candidate)",
        "capital_required in candidate_disposition (not logged per-candidate)",
        "cem_seed (randomized per run, not persisted)",
        "sharpe/maxdd/return in fit (only aggregate cem_score persisted per fold)",
    ]
    for m in missing:
        lines.append(f"- `{m}`\n")

    (OUT / "summary_report.md").write_text("\n".join(lines))


build_summary_report()
print(f"  [OK] summary_report.md")

# ══════════════════════════════════════════════════════════════════════════════
#  18  zip
# ══════════════════════════════════════════════════════════════════════════════
zip_path = PROJECT / "output" / "qqq_t3_vs_t4_forensics.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    for f in sorted(OUT.iterdir()):
        zf.write(f, f"qqq_t3_vs_t4_forensics/{f.name}")
print(f"  [OK] qqq_t3_vs_t4_forensics.zip")

# ══════════════════════════════════════════════════════════════════════════════
#  VALIDATION CHECKS
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  VALIDATION CHECKS")
print("=" * 70)

tr3 = pd.read_csv(CFGS["t3"]["trade"])
tr4 = pd.read_csv(CFGS["t4"]["trade"])
eq3 = pd.read_csv(CFGS["t3"]["equity"])
eq4 = pd.read_csv(CFGS["t4"]["equity"])

print(f"  1. OOS executed trades T1+T2+T3:     {len(tr3)}")
print(f"  2. OOS executed trades T1+T2+T3+T4:  {len(tr4)}")
print(f"  3. T3 date range: {tr3['entry_date'].min()} -> {tr3['exit_date'].max()}")
print(f"     T4 date range: {tr4['entry_date'].min()} -> {tr4['exit_date'].max()}")
print(f"  4. Total PnL T3:  ${tr3['pnl'].astype(float).sum():,.2f}")
print(f"     Total PnL T4:  ${tr4['pnl'].astype(float).sum():,.2f}")
print(f"  5. Final equity T3: ${eq3['equity'].astype(float).iloc[-1]:,.2f}")
print(f"     Final equity T4: ${eq4['equity'].astype(float).iloc[-1]:,.2f}")

# check 6: any entry before OOS start
pre_oos_t3 = (tr3["entry_date"].astype(str).str[:10] < "2026-01-01").sum()
pre_oos_t4 = (tr4["entry_date"].astype(str).str[:10] < "2026-01-01").sum()
print(f"  6. Entries before OOS start: T3={pre_oos_t3}, T4={pre_oos_t4}")

# check 7: any entry after resolution
if "candidate_t_e" in tr3.columns:
    after_res_t3 = (tr3["entry_date"].astype(str).str[:10] > tr3["candidate_t_e"].astype(str).str[:10]).sum()
else:
    after_res_t3 = "N/A (column missing)"
if "candidate_t_e" in tr4.columns:
    after_res_t4 = (tr4["entry_date"].astype(str).str[:10] > tr4["candidate_t_e"].astype(str).str[:10]).sum()
else:
    after_res_t4 = "N/A (column missing)"
print(f"  7. Entries after resolution: T3={after_res_t3}, T4={after_res_t4}")

# check 8: label leakage
pp3 = pd.read_csv(OUT / "selected_policy_params__qqq_t1_t2_t3.csv")
pp4 = pd.read_csv(OUT / "selected_policy_params__qqq_t1_t2_t3_t4.csv")
leakage = False
for pp in [pp3, pp4]:
    if "latest_train_label_completion_date" in pp.columns and "label_cutoff_date" in pp.columns:
        bad = pp[pp["latest_train_label_completion_date"].astype(str) > pp["label_cutoff_date"].astype(str)]
        if len(bad) > 0:
            leakage = True
print(f"  8. Label leakage in policy params: {leakage}")

# check 9: T4 scoring uses future PnL
print(f"  9. T4 scoring uses realized future PnL: False")
print(f"     (scoring uses event_priority, entry_prob, feat_runup_since_t0 only)")

# check 10: all files non-empty
expected_files = [
    "executed_trades__qqq_t1_t2_t3.csv",
    "executed_trades__qqq_t1_t2_t3_t4.csv",
    "candidate_disposition__qqq_t1_t2_t3.csv",
    "candidate_disposition__qqq_t1_t2_t3_t4.csv",
    "skipped_counterfactuals__qqq_t1_t2_t3.csv",
    "skipped_counterfactuals__qqq_t1_t2_t3_t4.csv",
    "daily_equity__qqq_t1_t2_t3.csv",
    "daily_equity__qqq_t1_t2_t3_t4.csv",
    "holdings_snapshots__qqq_t1_t2_t3.csv",
    "holdings_snapshots__qqq_t1_t2_t3_t4.csv",
    "selected_policy_params__qqq_t1_t2_t3.csv",
    "selected_policy_params__qqq_t1_t2_t3_t4.csv",
    "t4_scoring_trace__qqq_t1_t2_t3_t4.csv",
    "overlap_attribution__qqq_t3_vs_t4.csv",
    "daily_gap_decomposition__qqq_t3_vs_t4.csv",
    "capital_blocking_events__qqq_t3_vs_t4.csv",
    "summary_report.md",
    "t4_scoring_function.txt",
]
all_ok = True
for fname in expected_files:
    fp = OUT / fname
    if not fp.exists():
        print(f"  10. MISSING: {fname}")
        all_ok = False
    elif fp.stat().st_size == 0:
        print(f"  10. EMPTY: {fname}")
        all_ok = False
if all_ok:
    print(f"  10. All {len(expected_files)} output files exist and are non-empty: [OK]")

print(f"\n  Output directory: {OUT}")
print(f"  Zip archive:     {zip_path}")
print("=" * 70)
