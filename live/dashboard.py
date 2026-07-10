"""Live, read-only web dashboard for the paper-trading pipeline.

A light, premium single-page app (default 0.0.0.0:8080). Read-only: it never
connects to IB and never trades -- everything is served from the shared Postgres
plus the walk-forward backtest CSV.

    python -m live.dashboard      # or the docker compose `dashboard` service

Views: Overview (live status, risk, capital, NAV), Portfolio (open positions
with exit pressure and trade detail; orders; trades; market watchlist),
Strategy & Backtest (walk-forward OOS folds + the live policy), Diagnostics
(attribution, execution, runtime, system, spend), and Learn (CEM / T1-T4 /
Kelly / walk-forward explained).
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from database.backtesting.schema import SCHEMA

from .config import CONFIG
from .database import LiveStore
from .policy import load_live_policy
from .utils import ib_cost, market_session_status, benchmark_sell_qty_for_cash_deficit

_STATE: dict = {}
BENCH = CONFIG.benchmark
_BOOT_TIME = datetime.now(timezone.utc)
try:
    _GIT_SHA = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL, text=True).strip()
    _GIT_BRANCH = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL, text=True).strip()
except Exception:
    _GIT_SHA = "unknown"
    _GIT_BRANCH = "unknown"



@asynccontextmanager
async def lifespan(app: FastAPI):
    _STATE["store"] = await LiveStore.create()
    try:
        yield
    finally:
        await _STATE["store"].close()


app = FastAPI(lifespan=lifespan, title="CEM live paper-trading dashboard")
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# HTTP Basic Auth. The dashboard exposes a live view of a real (paper) broker
# account, so gate it behind a shared password from the environment. Set
# DASHBOARD_PASSWORD (and optionally DASHBOARD_USER, default "admin") in .env;
# if unset, the dashboard stays open (local dev). /healthz is always open so the
# deploy healthcheck keeps working.
_DASH_USER = os.environ.get("DASHBOARD_USER", "admin")
_DASH_PW = os.environ.get("DASHBOARD_PASSWORD", "")


@app.middleware("http")
async def _basic_auth(request: Request, call_next):
    if _DASH_PW and request.url.path != "/healthz":
        ok = False
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                ok = (secrets.compare_digest(user, _DASH_USER)
                      and secrets.compare_digest(pw, _DASH_PW))
            except Exception:  # noqa: BLE001 - malformed header -> treat as unauthorized
                ok = False
        if not ok:
            return Response(status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="CEM dashboard"'})
    return await call_next(request)


def _iso(ts):
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.astimezone(timezone.utc).isoformat()
    return str(ts)


def _f(v):
    return float(v) if v is not None else None


def _reconciled_series(eq_series, reconciled_latest):
    """NAV curve (chronological). The stored equity can carry IB paper ghost-fill
    inflation; pin the most recent point to the ledger-reconciled equity so the
    curve ends on a truthful value."""
    series = [
        {"ts": _iso(r["ts"]), "equity": round(float(r["equity"]), 2),
         "passive": round(float(r["passive_equity"]), 2) if r["passive_equity"] is not None else None}
        for r in reversed(eq_series)]
    if series and reconciled_latest is not None:
        series[-1]["equity"] = round(reconciled_latest, 2)
    return series


# ── Backtest (walk-forward folds + live policy) ──────────────────────────────

def load_backtest() -> dict:
    path = CONFIG.fold_audit_csv
    try:
        import numpy as np
        import pandas as pd
        if not path.exists():
            return {"available": False}
        df = pd.read_csv(path)
        sub = df[(df["experiment"] == CONFIG.experiment)
                 & (df["benchmark"] == CONFIG.benchmark)].sort_values("fold")
        if sub.empty:
            return {"available": False}
        folds = [{
            "fold": int(r["fold"]),
            "start": str(r["eval_start_date"])[:10], "end": str(r["eval_end_date"])[:10],
            "return_pct": round(float(r["eval_return_pct"]), 2),
            "benchmark_pct": round(float(r["eval_benchmark_return_pct"]), 2),
            "excess_pct": round(float(r["eval_excess_return_pct"]), 2),
            "max_dd_pct": round(float(r["eval_max_dd_pct"]), 2),
            "trades": int(r["eval_trades"]),
        } for _, r in sub.iterrows()]
        res_path = CONFIG.results_csv
        try:
            if res_path.exists():
                res_df = pd.read_csv(res_path)
                res_sub = res_df[(res_df["experiment"] == CONFIG.experiment)
                                 & (res_df["benchmark"] == CONFIG.benchmark)]
                if not res_sub.empty:
                    r_res = res_sub.iloc[-1]
                    total_return_pct = float(r_res["test_return_pct"])
                    total_benchmark_pct = float(r_res["test_benchmark_return_pct"])
                    total_excess_pct = float(r_res["test_excess_return_pct"])
                    worst_dd_pct = float(r_res["test_max_dd_pct"])
                    total_trades = int(r_res["test_trades"])
                else:
                    raise ValueError("Not found in results")
            else:
                raise ValueError("No results csv")
        except Exception:
            comp = lambda key: (np.prod([1 + f[key] / 100 for f in folds]) - 1) * 100
            total_return_pct = float(comp("return_pct"))
            total_benchmark_pct = float(comp("benchmark_pct"))
            total_excess_pct = float(comp("return_pct") - comp("benchmark_pct"))
            worst_dd_pct = min(f["max_dd_pct"] for f in folds)
            total_trades = sum(f["trades"] for f in folds)

        summary = {
            "n_folds": len(folds),
            "total_return_pct": round(total_return_pct, 2),
            "total_benchmark_pct": round(total_benchmark_pct, 2),
            "total_excess_pct": round(total_excess_pct, 2),
            "worst_dd_pct": round(worst_dd_pct, 2),
            "total_trades": total_trades,
            "positive_folds": sum(1 for f in folds if f["excess_pct"] > 0),
        }
        policy = json.loads(sub.iloc[-1]["eval_policy_json"])
        # Out-of-sample equity curve for THIS experiment/benchmark, from the
        # latest optimize_cem run: experiment_equity_logs_clean/<bench>_<slug>_test.csv
        # (e.g. spy_t1_t2_t3_t4_test.csv). Falls back to the legacy single-file
        # log only if the per-experiment file is missing.
        slug = CONFIG.experiment.lower().replace("+", "_").replace(" ", "_")
        eq_path = (CONFIG.results_csv.parent / "experiment_equity_logs_clean"
                   / f"{CONFIG.benchmark.lower()}_{slug}_test.csv")
        if not eq_path.exists():
            eq_path = CONFIG.backtest_equity_csv
        equity_series = []
        if eq_path.exists():
            try:
                eq_df = pd.read_csv(eq_path)
                for _, row in eq_df.iterrows():
                    equity_series.append({
                        "ts": str(row["date"])[:10] + "T00:00:00Z",
                        "equity": float(row["equity"]),
                        "passive": float(row["benchmark_equity"]),
                    })
            except Exception:
                pass

        return {"available": True, "experiment": CONFIG.experiment,
                "benchmark": CONFIG.benchmark, "folds": folds,
                "summary": summary, "policy": policy, "equity_series": equity_series}
    except Exception as error:  # noqa: BLE001
        return {"available": False, "error": str(error)}


async def gather_metrics() -> dict:
    store: LiveStore = _STATE["store"]
    pool = store.pool
    async with pool.acquire() as conn:
        eq = await conn.fetchrow(
            f"SELECT * FROM {SCHEMA}.live_equity_snapshots ORDER BY ts DESC LIMIT 1")
        eq_series = await conn.fetch(
            f"""SELECT ts, equity, cash, benchmark_shares, benchmark_price,
                       open_positions, passive_equity
                FROM {SCHEMA}.live_equity_snapshots
                ORDER BY ts DESC LIMIT 600""")
        perf = await conn.fetchrow(
            f"""SELECT COUNT(*) FILTER (WHERE status='closed') AS closed,
                       COUNT(*) FILTER (WHERE status='closed' AND pnl > 0) AS wins,
                       COALESCE(SUM(pnl) FILTER (WHERE status='closed'), 0) AS realized_pnl
                FROM {SCHEMA}.live_positions""")
        best_trade = await conn.fetchrow(
            f"SELECT symbol, pnl, pnl_pct FROM {SCHEMA}.live_positions WHERE status='closed' AND pnl IS NOT NULL ORDER BY pnl DESC LIMIT 1"
        )
        worst_trade = await conn.fetchrow(
            f"SELECT symbol, pnl, pnl_pct FROM {SCHEMA}.live_positions WHERE status='closed' AND pnl IS NOT NULL ORDER BY pnl ASC LIMIT 1"
        )
        open_pos = await conn.fetch(
            f"SELECT * FROM {SCHEMA}.live_positions WHERE status='open' ORDER BY entry_ts")
        orders = await conn.fetch(
            f"""SELECT ts, symbol, action, qty, kind, fill_price, status,
                       reference_price, commission
                FROM {SCHEMA}.live_orders ORDER BY ts DESC LIMIT 200""")
        order_economics = await conn.fetch(
            f"""SELECT ts, action, qty, fill_price, reference_price, commission, status
                FROM {SCHEMA}.live_orders
                WHERE status IN ('Filled', 'dry_run') AND fill_price IS NOT NULL""")
        trades = await conn.fetch(
            f"""SELECT symbol, pnl, pnl_pct, exit_reason, exit_ts
                FROM {SCHEMA}.live_positions WHERE status='closed'
                ORDER BY exit_ts DESC LIMIT 20""")
        markets = await conn.fetchrow(
            f"""SELECT COUNT(*) AS tracked,
                       COUNT(*) FILTER (WHERE t0_prob IS NOT NULL) AS with_t0
                FROM {SCHEMA}.live_tracked_markets WHERE status='tracking'""")
        upcoming = await conn.fetch(
            f"""SELECT market_id, end_at, question, t0_prob, assets, is_earnings
                FROM {SCHEMA}.live_tracked_markets
                WHERE status='tracking' ORDER BY end_at LIMIT 30""")
        sys_latest = await conn.fetchrow(
            f"SELECT * FROM {SCHEMA}.live_system_metrics ORDER BY ts DESC LIMIT 1")
        runtime_rows = await conn.fetch(
            f"""SELECT key, ts, value, updated_at FROM {SCHEMA}.live_runtime_state
                WHERE key = ANY($1::text[])""", ["discovery", "prune"])
        cost = await conn.fetchrow(
            f"""SELECT COALESCE(SUM(est_cost_usd),0) AS total, COALESCE(SUM(calls),0) AS calls,
                       COALESCE(SUM(est_cost_usd) FILTER (WHERE ts>=date_trunc('day',now())),0) AS today,
                       COALESCE(SUM(calls) FILTER (WHERE ts>=date_trunc('day',now())),0) AS today_calls
                FROM {SCHEMA}.live_api_costs""")
        pos_market_ids = [p["market_id"] for p in open_pos]
        mkt_rows = await conn.fetch(
            f"""SELECT market_id, t0_prob, discovered_at, end_at FROM {SCHEMA}.live_tracked_markets
                WHERE market_id = ANY($1::text[])""", pos_market_ids) if pos_market_ids else []
    mkt_map = {r["market_id"]: r for r in mkt_rows}
    runtime_map = {r["key"]: r for r in runtime_rows}

    equity = _f(eq["equity"]) if eq else None
    passive = _f(eq["passive_equity"]) if eq and eq["passive_equity"] is not None else None
    excess = (equity - passive) if (equity is not None and passive is not None) else None

    # Load policy for stop-loss computation
    try:
        policy = load_live_policy(CONFIG)
        atr_mult = float(policy.get("atr_mult", 0))
    except Exception:  # noqa: BLE001
        policy, atr_mult = {}, 0.0

    theta_out = float(policy.get("theta_out", 0.0) or 0.0)
    now_ts = datetime.now(timezone.utc)
    positions, trades_value = [], 0.0
    for p in open_pos:
        last = await store.latest_close(p["symbol"]) or float(p["entry_price"])
        entry = float(p["entry_price"])
        qty = int(p["qty"])
        notional = qty * last
        trades_value += notional
        mk = mkt_map.get(p["market_id"])
        t0_prob = _f(mk["t0_prob"]) if mk else None
        prob_now = await store.latest_prob(p["market_id"])
        stock_t0 = await store.close_near(p["symbol"], mk["discovered_at"]) if mk and mk["discovered_at"] else None
        # Stop-loss: ATR trailing for non-earnings, theta-only for earnings
        stop_loss = None
        if not p["is_earnings"] and atr_mult and p["atr_pct"]:
            atr_pct = float(p["atr_pct"])
            peak = float(p["peak_ret"] or 0.0)
            stop_ret = peak - atr_mult * atr_pct
            stop_loss = round(entry * (1.0 + stop_ret), 2)

        # Stock T0→Now
        stock_runup_pct_val = None
        if stock_t0 and float(stock_t0) > 0:
            stock_runup_pct_val = round((float(last) / float(stock_t0) - 1.0) * 100.0, 2)

        stock_entry_runup_pct_val = None
        if stock_t0 and float(stock_t0) > 0:
            stock_entry_runup_pct_val = round((entry / float(stock_t0) - 1.0) * 100.0, 2)

        entry_ts = p["entry_ts"]
        if isinstance(entry_ts, datetime):
            entry_age_seconds = max(0.0, (now_ts - entry_ts.astimezone(timezone.utc)).total_seconds())
        else:
            entry_age_seconds = None
        days_to_resolution = None
        if mk and mk["end_at"]:
            days_to_resolution = round((mk["end_at"].astimezone(timezone.utc) - now_ts).total_seconds() / 86400, 1)
        stop_distance_pct = None
        if stop_loss and last:
            stop_distance_pct = round((float(last) / stop_loss - 1.0) * 100.0, 2)
        theta_distance_pp = None
        if theta_out and prob_now is not None:
            theta_distance_pp = round((float(prob_now) - theta_out) * 100.0, 1)
        if not p["is_earnings"] and stop_distance_pct is not None and stop_distance_pct <= 2.0:
            exit_risk = "near_stop"
        elif theta_distance_pp is not None and theta_distance_pp <= 3.0:
            exit_risk = "near_theta"
        elif days_to_resolution is not None and days_to_resolution <= 2.0:
            exit_risk = "near_resolution"
        elif entry_age_seconds is not None and entry_age_seconds >= 7 * 86400:
            exit_risk = "aging"
        else:
            exit_risk = "normal"

        positions.append({
            "position_id": int(p["position_id"]),
            "market_id": p["market_id"],
            "symbol": p["symbol"], "qty": qty,
            "entry_price": round(entry, 2), "last": round(float(last), 2),
            "notional": round(notional, 2),
            "unrealized": round(qty * (last - entry), 2),
            "unrealized_pct": round((last / entry - 1.0) * 100.0, 2) if entry else None,
            "t0_prob": round(t0_prob, 3) if t0_prob is not None else None,
            "entry_prob": round(float(p["entry_prob"]), 3) if p["entry_prob"] is not None else None,
            "prob_now": round(float(prob_now), 3) if prob_now is not None else None,
            "theta_out": round(theta_out, 3) if theta_out else None,
            "theta_distance_pp": theta_distance_pp,
            "prob_entry_runup_pp": round((float(p["entry_prob"]) - t0_prob) * 100.0, 1)
                if (p["entry_prob"] is not None and t0_prob is not None) else None,
            "prob_runup_pp": round((float(prob_now) - t0_prob) * 100.0, 1)
                if (prob_now is not None and t0_prob is not None) else None,
            "stock_t0": round(float(stock_t0), 2) if stock_t0 else None,
            "stock_entry_runup_pct": stock_entry_runup_pct_val,
            "stock_runup_pct": stock_runup_pct_val,
            "kelly_pct": round(float(p["position_size_pct"]) * 100.0, 1)
                if p["position_size_pct"] is not None else None,
            "stop_loss": stop_loss,
            "stop_distance_pct": stop_distance_pct,
            "exit_risk": exit_risk,
            "days_held": round(entry_age_seconds / 86400, 1) if entry_age_seconds is not None else None,
            "days_to_resolution": days_to_resolution,
            "is_earnings": bool(p["is_earnings"]),
            "question": p["question"], "entry_ts": _iso(p["entry_ts"]),
            "resolution_ts": _iso(mk["end_at"]) if mk and mk["end_at"] else None,
        })

    bench_shares = float(eq["benchmark_shares"]) if eq else 0.0
    bench_price = await store.latest_close(BENCH)
    spy_value = bench_shares * (bench_price or 0.0)

    # Reconcile away IB paper-account ghost-fill inflation using the shared cash
    # ledger -- the same source the trader now stores (LiveStore.reconciled_cash):
    #   real_cash = all-cash start equity - net filled buys - commissions
    #   equity    = real_cash + open-position market value + benchmark value
    # `glitch` is any leftover gap vs the stored (pre-fix) equity, for display.
    ledger_cash = await store.reconciled_cash()
    reported_equity = _f(eq["equity"]) if eq else None
    if ledger_cash is not None:
        cash = ledger_cash
        equity = cash + trades_value + spy_value
        excess = (equity - passive) if passive is not None else None
        glitch = (reported_equity - equity) if reported_equity is not None else None
    else:
        cash = float(eq["cash"]) if eq else 0.0
        glitch = None
    total = spy_value + trades_value + cash
    closed, wins = int(perf["closed"] or 0), int(perf["wins"] or 0)
    filled = sum(1 for o in orders if o["status"] in ("Filled", "dry_run"))
    failed = [{"symbol": o["symbol"], "action": o["action"], "kind": o["kind"],
               "status": o["status"], "ts": _iso(o["ts"])}
              for o in orders if o["status"] not in ("Filled", "dry_run")]
    failed_24h = sum(
        1 for o in orders
        if o["status"] not in ("Filled", "dry_run")
        and o["ts"].astimezone(timezone.utc) >= now_ts - timedelta(hours=24)
    )
    slip_values = [
        (float(o["fill_price"]) / float(o["reference_price"]) - 1.0) * 1e4
        for o in order_economics if o["fill_price"] and o["reference_price"]
    ]
    buy_slip_values = [
        (float(o["fill_price"]) / float(o["reference_price"]) - 1.0) * 1e4
        for o in order_economics
        if o["action"] == "BUY" and o["fill_price"] and o["reference_price"]
    ]
    sell_slip_values = [
        (float(o["fill_price"]) / float(o["reference_price"]) - 1.0) * 1e4
        for o in order_economics
        if o["action"] == "SELL" and o["fill_price"] and o["reference_price"]
    ]
    actual_commission_total = sum(float(o["commission"] or 0.0) for o in order_economics)
    modeled_cost_total = sum(
        ib_cost(float(o["qty"]), float(o["fill_price"]), o["action"] == "SELL")
        for o in order_economics if o["fill_price"]
    )

    deficit_to_cover = -cash if cash < 0 else 0.0
    spy_shares_to_sell = 0.0
    margin_status = "OK"
    if deficit_to_cover > 0:
        if bench_shares > 0 and bench_price:
            spy_shares_to_sell = benchmark_sell_qty_for_cash_deficit(
                cash=cash, benchmark_price=bench_price, benchmark_shares=bench_shares,
                fractional=CONFIG.fractional_benchmark, min_notional=CONFIG.min_order_notional,
                buffer_pct=CONFIG.execution_buffer_pct,
            )
            margin_status = "Needs SPY rebalance"
        else:
            margin_status = "No SPY inventory"

    max_pos_notional = max((p["qty"] * p["last"] for p in positions), default=0.0)
    max_pos_pct = round(max_pos_notional / total * 100.0, 1) if total else 0.0
    reconciled_series_data = _reconciled_series(eq_series, equity)
    equity_curve = [r["equity"] for r in reconciled_series_data if r["equity"] is not None]
    peak = max(equity_curve, default=total) if equity_curve else total
    dd_pct = round((total / peak - 1.0) * 100.0, 2) if peak > 0 and total < peak else 0.0
    
    open_pos_sorted = sorted(positions, key=lambda x: x["unrealized"] or 0)
    best_open = open_pos_sorted[-1] if open_pos_sorted and (open_pos_sorted[-1]["unrealized"] or 0) > 0 else None
    worst_open = open_pos_sorted[0] if open_pos_sorted and (open_pos_sorted[0]["unrealized"] or 0) < 0 else None

    active_return_pct = round(excess / passive * 100.0, 2) if excess is not None and passive else None
    open_pnl = sum((x["unrealized"] or 0) for x in positions)
    open_contrib = round(open_pnl / total * 100.0, 2) if total else 0.0
    realized_contrib = round(float(perf["realized_pnl"] or 0.0) / total * 100.0, 2) if total else 0.0
    cash_drag = round(active_return_pct - (open_contrib + realized_contrib), 2) if active_return_pct is not None else 0.0

    trader_uptime = (now_ts - eq["ts"]).total_seconds() if eq and eq.get("ts") else None
    dash_uptime = (now_ts - _BOOT_TIME).total_seconds()

    eq_chrono = list(reversed(eq_series))
    prev_eq = eq_chrono[-2] if len(eq_chrono) >= 2 else None

    def _return_since(seconds: int) -> float | None:
        if not eq_chrono or equity is None:
            return None
        cutoff = now_ts.timestamp() - seconds
        base = None
        for row in eq_chrono:
            ts = row["ts"].astimezone(timezone.utc).timestamp()
            if ts >= cutoff:
                base = float(row["equity"])
                break
        if base is None or base == 0:
            return None
        return round((equity / base - 1.0) * 100.0, 2)

    prev_equity = float(prev_eq["equity"]) if prev_eq and prev_eq["equity"] is not None else None
    prev_passive = float(prev_eq["passive_equity"]) if prev_eq and prev_eq["passive_equity"] is not None else None
    prev_excess = (prev_equity - prev_passive) if (prev_equity is not None and prev_passive is not None) else None
    deltas = {
        "equity": round(equity - prev_equity, 2) if equity is not None and prev_equity is not None else None,
        "passive": round(passive - prev_passive, 2) if passive is not None and prev_passive is not None else None,
        "excess": round(excess - prev_excess, 2) if excess is not None and prev_excess is not None else None,
        "cash": round(cash - float(prev_eq["cash"]), 2) if prev_eq and prev_eq["cash"] is not None else None,
        "open_positions": len(positions) - int(prev_eq["open_positions"]) if prev_eq and prev_eq["open_positions"] is not None else None,
        "return_24h_pct": _return_since(86400),
        "return_7d_pct": _return_since(7 * 86400),
    }

    disk_used_pct = None
    if sys_latest and sys_latest["disk_total_bytes"] and sys_latest["disk_free_bytes"]:
        disk_used_pct = round((1.0 - int(sys_latest["disk_free_bytes"]) / int(sys_latest["disk_total_bytes"])) * 100.0, 1)
    critical_alerts, warning_alerts, info_alerts = [], [], []
    if trader_uptime is None:
        critical_alerts.append({"title": "Trader heartbeat missing", "detail": "No equity snapshot is available yet."})
    elif trader_uptime > CONFIG.tick_seconds * 2.5:
        critical_alerts.append({"title": "Trader heartbeat stale", "detail": f"Last NAV snapshot was {round(trader_uptime / 60)} minutes ago."})
    elif trader_uptime > CONFIG.tick_seconds * 1.5:
        warning_alerts.append({"title": "Trader heartbeat delayed", "detail": f"Last NAV snapshot was {round(trader_uptime / 60)} minutes ago."})
    if deficit_to_cover > 0:
        level = critical_alerts if margin_status == "No SPY inventory" else warning_alerts
        level.append({"title": margin_status, "detail": f"Cash deficit {round(deficit_to_cover, 2):,.2f}; estimated {BENCH} sale {round(spy_shares_to_sell, 4):,.4f} shares."})
    failed_recent = len(failed)
    if failed_recent:
        warning_alerts.append({"title": "Recent order issues", "detail": f"{failed_recent} of the last {len(orders)} orders are not filled/dry-run."})
    near_exit = [p for p in positions if p["exit_risk"] != "normal"]
    if near_exit:
        warning_alerts.append({"title": "Positions need attention", "detail": ", ".join(f"{p['symbol']}:{p['exit_risk'].replace('_', ' ')}" for p in near_exit[:5])})
    if disk_used_pct is not None and disk_used_pct >= 90:
        critical_alerts.append({"title": "Disk pressure", "detail": f"Host disk is {disk_used_pct}% used."})
    elif disk_used_pct is not None and disk_used_pct >= 75:
        warning_alerts.append({"title": "Disk usage rising", "detail": f"Host disk is {disk_used_pct}% used."})
    info_alerts.append({"title": "Open P&L", "detail": f"{open_pnl:+,.2f} across {len(positions)} open positions."})

    next_discovery = None
    if runtime_map.get("discovery") and runtime_map["discovery"]["ts"]:
        next_discovery = runtime_map["discovery"]["ts"] + timedelta(seconds=CONFIG.tick_seconds * CONFIG.discovery_every_ticks)
    next_prune = None
    if runtime_map.get("prune") and runtime_map["prune"]["ts"]:
        next_prune = runtime_map["prune"]["ts"] + timedelta(seconds=CONFIG.tick_seconds * CONFIG.prune_every_ticks)
    next_tick = eq["ts"] + timedelta(seconds=CONFIG.tick_seconds) if eq and eq.get("ts") else None


    return {
        "generated_at": _iso(datetime.now(timezone.utc)), "benchmark": BENCH,
        "experiment": CONFIG.experiment, "tick_seconds": CONFIG.tick_seconds,
        "market": market_session_status(),
        "portfolio": {
            "equity": round(equity, 2) if equity is not None else None,
            "open_positions": len(positions),
            "excess": round(excess, 2) if excess is not None else None,
            "excess_pct": round(excess / passive * 100.0, 2) if excess is not None and passive else None,
            "reported_equity": round(reported_equity, 2) if reported_equity is not None else None,
            "glitch": round(glitch, 2) if glitch else None,
            "as_of": _iso(eq["ts"]) if eq else None,
        },
        "deltas": deltas,
        "alerts": {
            "critical": critical_alerts,
            "warning": warning_alerts,
            "info": info_alerts,
        },
        "allocation": {
            "spy_value": round(spy_value, 2), "trades_value": round(trades_value, 2),
            "cash": round(cash, 2), "total": round(total, 2),
            "spy_pct": round(spy_value / total * 100.0, 1) if total else 0.0,
            "trades_pct": round(trades_value / total * 100.0, 1) if total else 0.0,
            "cash_pct": round(cash / total * 100.0, 1) if total else 0.0,
            "invested_pct": round((spy_value + trades_value) / total * 100.0, 1) if total else 0.0,
            "bench_shares": round(bench_shares, 4),
            "bench_price": round(bench_price, 2) if bench_price else None,
        },
        "safety": {
            "investable": round(max(0.0, cash) + max(0.0, spy_value), 2),
            "min_order_notional": round(float(CONFIG.min_order_notional), 2),
            "execution_buffer_pct": round(float(CONFIG.execution_buffer_pct) * 100.0, 2),
            "kelly_enabled": bool(CONFIG.use_kelly),
            "fractional_benchmark": bool(CONFIG.fractional_benchmark),
            "deficit_to_cover": round(deficit_to_cover, 2),
            "spy_shares_to_sell": round(spy_shares_to_sell, 4),
            "margin_status": margin_status,
        },
        "risk": {
            "max_pos_pct": max_pos_pct,
            "dd_pct": dd_pct,
            "best_open": best_open,
            "worst_open": worst_open,
        },
        "attribution": {
            "active_return_pct": active_return_pct,
            "open_contrib_pct": open_contrib,
            "realized_contrib_pct": realized_contrib,
            "cash_drag_pct": cash_drag,
        },
        "deployment": {
            "git_sha": _GIT_SHA,
            "git_branch": _GIT_BRANCH,
            "trader_uptime": trader_uptime,
            "dash_uptime": dash_uptime,
            "dash_health": "OK",
        },
        "ops": {
            "next_tick": _iso(next_tick),
            "last_discovery": _iso(runtime_map["discovery"]["ts"]) if runtime_map.get("discovery") and runtime_map["discovery"]["ts"] else None,
            "next_discovery": _iso(next_discovery),
            "last_prune": _iso(runtime_map["prune"]["ts"]) if runtime_map.get("prune") and runtime_map["prune"]["ts"] else None,
            "next_prune": _iso(next_prune),
            "discovery_every_ticks": CONFIG.discovery_every_ticks,
            "prune_every_ticks": CONFIG.prune_every_ticks,
        },
        "performance": {
            "realized_pnl": round(float(perf["realized_pnl"] or 0.0), 2),
            "closed_trades": closed, "wins": wins,
            "best": dict(best_trade) if best_trade else None,
            "worst": dict(worst_trade) if worst_trade else None,
            "win_rate": round(wins / closed * 100.0, 1) if closed else None,
        },
        "exec": {
            "filled": filled, "recent": len(orders), "failed": failed,
            "failed_24h": failed_24h,
            "avg_slip_bps": round(sum(slip_values) / len(slip_values), 2) if slip_values else None,
            "avg_buy_slip_bps": round(sum(buy_slip_values) / len(buy_slip_values), 2) if buy_slip_values else None,
            "avg_sell_slip_bps": round(sum(sell_slip_values) / len(sell_slip_values), 2) if sell_slip_values else None,
            "commission_total": round(actual_commission_total, 2),
            "modeled_cost_total": round(modeled_cost_total, 2),
            "cost_delta": round(actual_commission_total - modeled_cost_total, 2),
        },
        "equity_series": _reconciled_series(eq_series, equity),
        "open_positions": positions,
        "recent_orders": [
            {"ts": _iso(r["ts"]), "symbol": r["symbol"], "action": r["action"],
             "qty": round(float(r["qty"]), 4), "kind": r["kind"],
             "fill_price": round(float(r["fill_price"]), 2) if r["fill_price"] is not None else None,
             "commission": round(float(r["commission"]), 2) if r["commission"] is not None else None,
             "slip_bps": round((float(r["fill_price"]) / float(r["reference_price"]) - 1.0) * 1e4, 1)
                 if r["fill_price"] and r["reference_price"] else None,
             "status": r["status"]} for r in orders],
        "recent_trades": [
            {"symbol": r["symbol"],
             "pnl": round(float(r["pnl"]), 2) if r["pnl"] is not None else None,
             "pnl_pct": round(float(r["pnl_pct"]), 2) if r["pnl_pct"] is not None else None,
             "exit_reason": r["exit_reason"], "exit_ts": _iso(r["exit_ts"])} for r in trades],
        "markets": await _build_markets_payload(store, markets, upcoming),
        "system": {
            "db_size_bytes": int(sys_latest["db_size_bytes"]) if sys_latest and sys_latest["db_size_bytes"] else None,
            "disk_free_bytes": int(sys_latest["disk_free_bytes"]) if sys_latest and sys_latest["disk_free_bytes"] else None,
            "disk_total_bytes": int(sys_latest["disk_total_bytes"]) if sys_latest and sys_latest["disk_total_bytes"] else None,
            "disk_used_pct": disk_used_pct,
            "as_of": _iso(sys_latest["ts"]) if sys_latest else None},
        "api_cost": {
            "today_usd": round(float(cost["today"] or 0.0), 4),
            "total_usd": round(float(cost["total"] or 0.0), 4),
            "today_calls": int(cost["today_calls"] or 0), "total_calls": int(cost["calls"] or 0)},
    }


async def _build_markets_payload(store: LiveStore, markets, upcoming) -> dict:
    """Enrich the upcoming watchlist with prob, assets, relevance, pecking order."""
    now_ts = datetime.now(timezone.utc)
    try:
        policy = load_live_policy(CONFIG)
    except Exception:  # noqa: BLE001
        policy = {}
    enter_strong = float(policy.get("enter_strong", 1.1) or 1.1)
    enter_floor = float(policy.get("enter_floor", 1.1) or 1.1)
    max_prob_surge = float(policy.get("max_prob_surge", 999.0) or 999.0)
    market_ids = [r["market_id"] for r in upcoming]
    latest_map = {}
    if market_ids:
        async with store.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT DISTINCT ON (market_id) market_id, hour_ts, probability
                    FROM {SCHEMA}.historical_probability_points
                    WHERE market_id = ANY($1::text[])
                    ORDER BY market_id, hour_ts DESC""", market_ids)
        latest_map = {r["market_id"]: r for r in rows}
    enriched = []
    for r in upcoming:
        t0 = _f(r["t0_prob"])
        latest = latest_map.get(r["market_id"])
        prob_now = float(latest["probability"]) if latest else None
        raw_assets = json.loads(r["assets"]) if isinstance(r["assets"], str) else (r["assets"] or [])
        asset_list = [{"symbol": a.get("symbol", "?"),
                       "relevance": round(float(a.get("connection_strength", 0)), 2)}
                      for a in raw_assets]
        max_rel = max((a["relevance"] for a in asset_list), default=None)
        prob_delta = round((float(prob_now) - float(t0)) * 100.0, 1) if (prob_now is not None and t0 is not None) else None
        days_to_resolution = round((r["end_at"].astimezone(timezone.utc) - now_ts).total_seconds() / 86400, 1) if r["end_at"] else None
        prob_age_hours = round((now_ts - latest["hour_ts"].astimezone(timezone.utc)).total_seconds() / 3600, 1) if latest else None
        if prob_now is None:
            state = "stale"
        elif max_rel is not None and max_rel < 0.5:
            state = "weak_mapping"
        elif prob_delta is not None and prob_delta / 100.0 > max_prob_surge:
            state = "overheated"
        elif prob_now >= enter_strong:
            state = "fires_now"
        elif prob_now >= enter_floor:
            state = "near_entry"
        else:
            state = "watching"
        enriched.append({
            "end_at": _iso(r["end_at"]),
            "question": r["question"][:100],
            "t0_prob": round(t0, 3) if t0 is not None else None,
            "prob_now": round(float(prob_now), 3) if prob_now is not None else None,
            "prob_age_hours": prob_age_hours,
            "prob_delta": prob_delta,
            "assets": asset_list,
            "relevance": max_rel,
            "state": state,
            "days_to_resolution": days_to_resolution,
            "is_earnings": bool(r["is_earnings"]),
        })
    # Pecking order: rank by prob_delta descending (T4 natural priority)
    ranked = sorted(enriched, key=lambda x: x["prob_delta"] if x["prob_delta"] is not None else -999, reverse=True)
    for i, item in enumerate(ranked):
        item["pecking"] = i + 1
    # Restore resolution-date order for display
    enriched.sort(key=lambda x: x["end_at"] or "")
    return {
        "tracked": int(markets["tracked"] or 0),
        "with_t0": int(markets["with_t0"] or 0),
        "upcoming": enriched,
    }


@app.get("/api/metrics")
async def api_metrics() -> JSONResponse:
    return JSONResponse(await gather_metrics())


@app.get("/api/backtest")
async def api_backtest() -> JSONResponse:
    return JSONResponse(load_backtest())


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(PAGE)


PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CEM · Live Paper Trading</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/dashboard.css">
</head>
<body>
  <div id="app"></div>
  <script src="/static/dashboard.js" defer></script>
</body>
</html>
"""

def main() -> None:
    port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    host = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
