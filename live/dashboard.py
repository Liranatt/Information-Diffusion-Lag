"""Live, read-only web dashboard for the paper-trading pipeline.

A light, premium single-page app (default 0.0.0.0:8080). Read-only: it never
connects to IB and never trades -- everything is served from the shared Postgres
plus the walk-forward backtest CSV.

    python -m live.dashboard      # or the docker compose `dashboard` service

Views: Overview (allocation index-vs-trades, NAV vs passive, KPIs), Portfolio
(open positions with prob/stock runup, Kelly, real commission + slippage; orders;
trades; market watchlist), Strategy & Backtest (walk-forward OOS folds + the live
policy), and Learn (CEM / T1-T4 / Kelly / walk-forward explained).
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from database.backtesting.schema import SCHEMA

from .config import CONFIG
from .database import LiveStore
from .policy import load_live_policy
from .utils import market_session_status

_STATE: dict = {}
BENCH = CONFIG.benchmark


@asynccontextmanager
async def lifespan(app: FastAPI):
    _STATE["store"] = await LiveStore.create()
    try:
        yield
    finally:
        await _STATE["store"].close()


app = FastAPI(lifespan=lifespan, title="CEM live paper-trading dashboard")


def _iso(ts):
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.astimezone(timezone.utc).isoformat()
    return str(ts)


def _f(v):
    return float(v) if v is not None else None


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
        comp = lambda key: (np.prod([1 + f[key] / 100 for f in folds]) - 1) * 100
        summary = {
            "n_folds": len(folds),
            "total_return_pct": round(float(comp("return_pct")), 2),
            "total_benchmark_pct": round(float(comp("benchmark_pct")), 2),
            "total_excess_pct": round(float(comp("return_pct") - comp("benchmark_pct")), 2),
            "worst_dd_pct": round(min(f["max_dd_pct"] for f in folds), 2),
            "total_trades": sum(f["trades"] for f in folds),
            "positive_folds": sum(1 for f in folds if f["excess_pct"] > 0),
        }
        policy = json.loads(sub.iloc[-1]["eval_policy_json"])
        return {"available": True, "experiment": CONFIG.experiment,
                "benchmark": CONFIG.benchmark, "folds": folds,
                "summary": summary, "policy": policy}
    except Exception as error:  # noqa: BLE001
        return {"available": False, "error": str(error)}


async def gather_metrics() -> dict:
    store: LiveStore = _STATE["store"]
    pool = store.pool
    async with pool.acquire() as conn:
        eq = await conn.fetchrow(
            f"SELECT * FROM {SCHEMA}.live_equity_snapshots ORDER BY ts DESC LIMIT 1")
        eq_series = await conn.fetch(
            f"""SELECT ts, equity, passive_equity FROM {SCHEMA}.live_equity_snapshots
                ORDER BY ts DESC LIMIT 600""")
        perf = await conn.fetchrow(
            f"""SELECT COUNT(*) FILTER (WHERE status='closed') AS closed,
                       COUNT(*) FILTER (WHERE status='closed' AND pnl > 0) AS wins,
                       COALESCE(SUM(pnl) FILTER (WHERE status='closed'), 0) AS realized_pnl
                FROM {SCHEMA}.live_positions""")
        open_pos = await conn.fetch(
            f"SELECT * FROM {SCHEMA}.live_positions WHERE status='open' ORDER BY entry_ts")
        orders = await conn.fetch(
            f"""SELECT ts, symbol, action, qty, kind, fill_price, status,
                       reference_price, commission
                FROM {SCHEMA}.live_orders ORDER BY ts DESC LIMIT 200""")
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
        cost = await conn.fetchrow(
            f"""SELECT COALESCE(SUM(est_cost_usd),0) AS total, COALESCE(SUM(calls),0) AS calls,
                       COALESCE(SUM(est_cost_usd) FILTER (WHERE ts>=date_trunc('day',now())),0) AS today,
                       COALESCE(SUM(calls) FILTER (WHERE ts>=date_trunc('day',now())),0) AS today_calls
                FROM {SCHEMA}.live_api_costs""")
        pos_market_ids = [p["market_id"] for p in open_pos]
        pos_ids = [p["position_id"] for p in open_pos]
        mkt_rows = await conn.fetch(
            f"""SELECT market_id, t0_prob, discovered_at FROM {SCHEMA}.live_tracked_markets
                WHERE market_id = ANY($1::text[])""", pos_market_ids) if pos_market_ids else []
        entry_orders = await conn.fetch(
            f"""SELECT DISTINCT ON (position_id) position_id, fill_price, reference_price, commission
                FROM {SCHEMA}.live_orders WHERE position_id = ANY($1::bigint[]) AND kind='entry'
                ORDER BY position_id, ts""", pos_ids) if pos_ids else []
    mkt_map = {r["market_id"]: r for r in mkt_rows}
    entry_map = {r["position_id"]: r for r in entry_orders}

    equity = _f(eq["equity"]) if eq else None
    passive = _f(eq["passive_equity"]) if eq and eq["passive_equity"] is not None else None
    excess = (equity - passive) if (equity is not None and passive is not None) else None

    # Load policy for stop-loss computation
    try:
        policy = load_live_policy(CONFIG)
        atr_mult = float(policy.get("atr_mult", 0))
    except Exception:  # noqa: BLE001
        policy, atr_mult = {}, 0.0

    positions, trades_value = [], 0.0
    for p in open_pos:
        last = await store.latest_close(p["symbol"]) or float(p["entry_price"])
        entry = float(p["entry_price"])
        trades_value += int(p["qty"]) * last
        mk = mkt_map.get(p["market_id"])
        t0_prob = _f(mk["t0_prob"]) if mk else None
        prob_now = await store.latest_prob(p["market_id"])
        stock_t0 = await store.close_near(p["symbol"], mk["discovered_at"]) if mk and mk["discovered_at"] else None
        eo = entry_map.get(p["position_id"])
        ref = _f(eo["reference_price"]) if eo else None
        efill = _f(eo["fill_price"]) if eo else entry
        slip_bps = ((efill / ref - 1.0) * 1e4) if (ref and efill) else None

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

        positions.append({
            "symbol": p["symbol"], "qty": int(p["qty"]),
            "entry_price": round(entry, 2), "last": round(float(last), 2),
            "unrealized": round(int(p["qty"]) * (last - entry), 2),
            "unrealized_pct": round((last / entry - 1.0) * 100.0, 2) if entry else None,
            "t0_prob": round(t0_prob, 3) if t0_prob is not None else None,
            "entry_prob": round(float(p["entry_prob"]), 3) if p["entry_prob"] is not None else None,
            "prob_now": round(float(prob_now), 3) if prob_now is not None else None,
            "prob_entry_runup_pp": round((float(p["entry_prob"]) - t0_prob) * 100.0, 1)
                if (p["entry_prob"] is not None and t0_prob is not None) else None,
            "prob_runup_pp": round((float(prob_now) - t0_prob) * 100.0, 1)
                if (prob_now is not None and t0_prob is not None) else None,
            "stock_t0": round(float(stock_t0), 2) if stock_t0 else None,
            "stock_entry_runup_pct": stock_entry_runup_pct_val,
            "stock_runup_pct": stock_runup_pct_val,
            "kelly_pct": round(float(p["position_size_pct"]) * 100.0, 1)
                if p["position_size_pct"] is not None else None,
            "commission": round(_f(eo["commission"]), 2) if eo and eo["commission"] is not None else None,
            "slip_bps": round(slip_bps, 1) if slip_bps is not None else None,
            "stop_loss": stop_loss,
            "is_earnings": bool(p["is_earnings"]),
            "question": p["question"][:80], "entry_ts": _iso(p["entry_ts"]),
        })

    bench_shares = float(eq["benchmark_shares"]) if eq else 0.0
    bench_price = await store.latest_close(BENCH)
    spy_value = bench_shares * (bench_price or 0.0)
    cash = float(eq["cash"]) if eq else 0.0
    total = spy_value + trades_value + cash
    closed, wins = int(perf["closed"] or 0), int(perf["wins"] or 0)
    filled = sum(1 for o in orders if o["status"] in ("Filled", "dry_run"))
    failed = [{"symbol": o["symbol"], "action": o["action"], "kind": o["kind"],
               "status": o["status"], "ts": _iso(o["ts"])}
              for o in orders if o["status"] not in ("Filled", "dry_run")]

    return {
        "generated_at": _iso(datetime.now(timezone.utc)), "benchmark": BENCH,
        "experiment": CONFIG.experiment, "tick_seconds": CONFIG.tick_seconds,
        "market": market_session_status(),
        "portfolio": {
            "equity": round(equity, 2) if equity is not None else None,
            "open_positions": len(positions),
            "excess": round(excess, 2) if excess is not None else None,
            "excess_pct": round(excess / passive * 100.0, 2) if excess is not None and passive else None,
            "as_of": _iso(eq["ts"]) if eq else None,
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
        },
        "performance": {
            "realized_pnl": round(float(perf["realized_pnl"] or 0.0), 2),
            "closed_trades": closed, "wins": wins,
            "win_rate": round(wins / closed * 100.0, 1) if closed else None,
        },
        "exec": {"filled": filled, "recent": len(orders), "failed": failed},
        "equity_series": [
            {"ts": _iso(r["ts"]), "equity": round(float(r["equity"]), 2),
             "passive": round(float(r["passive_equity"]), 2) if r["passive_equity"] is not None else None}
            for r in reversed(eq_series)],
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
            "as_of": _iso(sys_latest["ts"]) if sys_latest else None},
        "api_cost": {
            "today_usd": round(float(cost["today"] or 0.0), 4),
            "total_usd": round(float(cost["total"] or 0.0), 4),
            "today_calls": int(cost["today_calls"] or 0), "total_calls": int(cost["calls"] or 0)},
    }


async def _build_markets_payload(store: LiveStore, markets, upcoming) -> dict:
    """Enrich the upcoming watchlist with prob, assets, relevance, pecking order."""
    enriched = []
    for r in upcoming:
        t0 = _f(r["t0_prob"])
        prob_now = await store.latest_prob(r["market_id"])
        raw_assets = json.loads(r["assets"]) if isinstance(r["assets"], str) else (r["assets"] or [])
        asset_list = [{"symbol": a.get("symbol", "?"),
                       "relevance": round(float(a.get("connection_strength", 0)), 2)}
                      for a in raw_assets]
        max_rel = max((a["relevance"] for a in asset_list), default=None)
        prob_delta = round((float(prob_now) - float(t0)) * 100.0, 1) if (prob_now is not None and t0 is not None) else None
        enriched.append({
            "end_at": _iso(r["end_at"]),
            "question": r["question"][:100],
            "t0_prob": round(t0, 3) if t0 is not None else None,
            "prob_now": round(float(prob_now), 3) if prob_now is not None else None,
            "prob_delta": prob_delta,
            "assets": asset_list,
            "relevance": max_rel,
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


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CEM · Live Paper Trading</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#eef1f6; --card:#ffffff; --ink:#0f1729; --ink2:#334155; --mut:#7b8698; --faint:#aab4c4;
    --line:#eceff4; --line2:#e2e7ef;
    --brand:#6d5efc; --brand2:#8b7bff; --brandsoft:#efedff;
    --up:#10b981; --down:#f43f5e; --amber:#f59e0b; --sky:#38bdf8;
    --shadow:0 1px 2px rgba(16,24,40,.05), 0 10px 30px -12px rgba(16,24,40,.14);
    --font:"Inter","Segoe UI Variable","Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    --num:"JetBrains Mono","Roboto Mono","IBM Plex Mono","SF Mono",ui-monospace,Menlo,Consolas,monospace;
    --math:"Cambria Math","STIX Two Math","Times New Roman",serif;
  }
  *{box-sizing:border-box}
  html,body{margin:0}
  body{background:var(--bg); color:var(--ink);
    font:14px/1.5 var(--font); -webkit-font-smoothing:antialiased; font-feature-settings:"kern" 1}
  .tnum{font-variant-numeric:tabular-nums}
  .up{color:var(--up)} .down{color:var(--down)} .mut{color:var(--mut)}
  .app{display:flex; min-height:100vh}
  /* Sidebar */
  .side{width:236px; flex-shrink:0; background:var(--card); border-right:1px solid var(--line2);
    padding:20px 16px; display:flex; flex-direction:column; gap:6px; position:sticky; top:0; height:100vh}
  .logo{display:flex; align-items:center; gap:11px; padding:6px 8px 18px; font-weight:800; font-size:17px; letter-spacing:-.02em}
  .logo .mk{width:30px; height:30px; border-radius:9px; background:linear-gradient(135deg,var(--brand),var(--brand2));
    display:grid; place-items:center; color:#fff; font-size:15px; box-shadow:0 6px 16px -4px rgba(109,94,252,.5)}
  .logo small{display:block; font-weight:500; font-size:10.5px; color:var(--mut); letter-spacing:.05em}
  .nav{display:flex; flex-direction:column; gap:3px; margin-top:6px}
  .nav button{display:flex; align-items:center; gap:11px; width:100%; text-align:left; border:0; cursor:pointer;
    background:transparent; color:var(--ink2); font:600 13.5px/1 inherit; padding:11px 12px; border-radius:8px; transition:.16s}
  .nav button .ic{width:18px; text-align:center; opacity:.8}
  .nav button:hover{background:#f5f6fa}
  .nav button.on{background:var(--brandsoft); color:var(--brand)}
  .side .grow{flex:1}
  .statuscard{background:linear-gradient(160deg,#111827,#1f2937); color:#fff; border-radius:12px; padding:15px; margin-top:8px}
  .statuscard .live{display:flex; align-items:center; gap:7px; font:700 11px/1 var(--num); color:#6ee7b7; letter-spacing:.1em}
  .statuscard .live .dot{width:7px; height:7px; border-radius:50%; background:#34d399; box-shadow:0 0 9px #34d399; animation:pulse 1.8s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
  .statuscard .eq{font:700 22px/1.1 var(--num); margin-top:11px; letter-spacing:-.01em}
  .statuscard .sub{font-size:11px; color:#9ca3af; margin-top:3px}
  .ver{font-size:10.5px; color:var(--faint); text-align:center; padding-top:12px}
  /* Main */
  .main{flex:1; min-width:0; padding:26px 30px 60px; max-width:1280px}
  .top{display:flex; align-items:flex-end; justify-content:space-between; gap:16px; margin-bottom:22px}
  .top h1{margin:0; font-size:23px; font-weight:800; letter-spacing:-.02em}
  .top .crumb{color:var(--mut); font-size:12.5px; margin-top:3px}
  .top .meta{font:500 11.5px/1.5 var(--num); color:var(--mut); text-align:right}
  .pill{display:inline-flex; align-items:center; gap:6px; background:#eafaf3; color:#0a8f5f; font:700 11px/1 var(--num);
    padding:5px 10px; border-radius:20px}
  .pill .d{width:6px;height:6px;border-radius:50%;background:#10b981; animation:pulse 1.8s infinite}
  .pill.closed{background:#f1f3f8; color:#64748b}.pill.closed .d{background:#94a3b8; box-shadow:none; animation:none}
  /* Cards / grid */
  .row{display:grid; gap:18px; margin-bottom:18px}
  .c4{grid-template-columns:repeat(4,1fr)} .c3{grid-template-columns:repeat(3,1fr)} .c2{grid-template-columns:2fr 1fr}
  .c2b{grid-template-columns:1fr 1fr}
  @media (max-width:1100px){.c4{grid-template-columns:repeat(2,1fr)} .c3,.c2,.c2b{grid-template-columns:1fr}}
  .card{background:var(--card); border:1px solid var(--line); border-radius:8px; padding:18px 19px; box-shadow:var(--shadow);
    animation:rise .4s ease both}
  @keyframes rise{from{opacity:0; transform:translateY(8px)}to{opacity:1; transform:none}}
  .card h3{margin:0 0 14px; font-size:12px; font-weight:700; color:var(--mut); text-transform:uppercase; letter-spacing:.06em}
  .stat .l{font-size:12px; color:var(--mut); font-weight:600}
  .stat .v{font:800 26px/1.15 var(--num); letter-spacing:-.02em; margin-top:8px}
  .stat .v.sm{font-size:20px}
  .stat .chip{display:inline-block; margin-top:9px; font:700 11px/1 var(--num); padding:4px 8px; border-radius:8px}
  .chip.up{background:#e7f8f1; color:#0a8f5f} .chip.down{background:#fdeaee; color:#d81b4a} .chip.neu{background:#f1f3f8; color:var(--mut)}
  /* allocation */
  .alloc{display:flex; gap:22px; align-items:center; flex-wrap:wrap}
  .donut{position:relative; width:150px; height:150px; flex-shrink:0}
  .donut .ctr{position:absolute; inset:0; display:grid; place-items:center; text-align:center}
  .donut .ctr b{font:800 24px/1 var(--num); letter-spacing:-.02em} .donut .ctr span{font-size:10.5px; color:var(--mut)}
  .leg{display:flex; flex-direction:column; gap:12px; flex:1; min-width:180px}
  .leg .it{display:flex; align-items:center; gap:11px}
  .leg .sw{width:11px; height:11px; border-radius:4px; flex-shrink:0}
  .leg .it .nm{font-size:12.5px; color:var(--ink2); font-weight:600; flex:1}
  .leg .it .vl{font:700 13px/1 var(--num)} .leg .it .pc{font-size:11px; color:var(--mut); width:44px; text-align:right}
  /* chart */
  svg.chart{width:100%; height:250px; display:block}
  .legrow{display:flex; gap:20px; font-size:11.5px; color:var(--mut); margin-top:10px}
  .legrow i{display:inline-block; width:16px; border-top:2.5px solid; margin-right:6px; vertical-align:middle}
  /* tables */
  .tw{overflow-x:auto; margin:0 -4px}
  table{width:100%; border-collapse:collapse; font-size:13px}
  th,td{padding:9px 11px; text-align:right; white-space:nowrap; border-bottom:1px solid var(--line)}
  th:first-child,td:first-child{text-align:left}
  thead th{font-size:10.5px; font-weight:700; color:var(--faint); text-transform:uppercase; letter-spacing:.05em}
  tbody tr{transition:.12s} tbody tr:hover{background:#f8f9fc}
  td.n{font-family:var(--num); font-variant-numeric:tabular-nums}
  td.q{text-align:left; color:var(--mut); max-width:300px; overflow:hidden; text-overflow:ellipsis}
  .tag{font:700 9.5px/1.5 var(--num); padding:2px 7px; border-radius:6px; text-transform:uppercase; background:#f1f3f8; color:var(--mut)}
  .tag.er{background:#fff4e5; color:#b8730c} .tag.ok{background:#e7f8f1; color:#0a8f5f}
  .tag.bad{background:#fdeaee; color:#d81b4a} .tag.warn{background:#fff4e5; color:#b8730c}
  .tag.brand{background:var(--brandsoft); color:var(--brand)}
  .empty{color:var(--faint); font-style:italic; padding:14px 4px}
  .alert{display:flex; gap:11px; align-items:center; background:#fff5f6; border:1px solid #fbd5dc; color:#b3213f;
    border-radius:8px; padding:13px 16px; margin-bottom:18px; font-size:13px; box-shadow:var(--shadow)}
  .alert b{color:#d81b4a}
  .meter{height:9px; border-radius:6px; background:#eef1f6; overflow:hidden}
  .meter>span{display:block; height:100%; border-radius:6px; transition:width .6s}
  .kv{display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid var(--line); font-size:13px}
  .kv:last-child{border:0} .kv .k{color:var(--mut)} .kv .v{font-family:var(--num); font-weight:700}
  .hint{border:1px solid var(--line); background:#f8f9fc; color:var(--ink2); border-radius:8px; padding:10px 12px;
    font-size:12.5px; margin:-2px 0 12px}
  .toolbar{display:flex; justify-content:flex-end; margin-top:12px}
  .miniBtn{border:1px solid var(--line2); background:#fff; color:var(--ink2); border-radius:8px; padding:7px 10px;
    font:700 11px/1 var(--num); cursor:pointer}
  .miniBtn:hover{background:#f8f9fc}
  .formula{display:inline-block; font:700 14px/1.6 var(--math); background:#f8f9fc; border:1px solid var(--line);
    border-radius:8px; padding:4px 8px; color:var(--ink)}
  /* params grid */
  .params{display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:12px}
  .param{background:#f8f9fc; border:1px solid var(--line); border-radius:8px; padding:11px 13px}
  .param .k{font-size:10.5px; color:var(--mut); text-transform:uppercase; letter-spacing:.04em; font-weight:600}
  .param .v{font:800 17px/1.2 var(--num); margin-top:5px} .param .d{font-size:10.5px; color:var(--faint); margin-top:3px}
  /* learn */
  .learn h3{font-size:13px} .learn p{color:var(--ink2); font-size:13.5px; margin:0 0 10px}
  .learn .term{font-weight:800; color:var(--brand)}
  .tiers{display:flex; flex-direction:column; gap:11px}
  .tier{display:flex; gap:13px; align-items:flex-start; padding:12px; background:#f8f9fc; border-radius:8px; border:1px solid var(--line)}
  .tier .b{width:34px; height:34px; border-radius:8px; flex-shrink:0; display:grid; place-items:center; color:#fff; font:800 13px/1 var(--num);
    background:linear-gradient(135deg,var(--brand),var(--brand2))}
  .tier .t{font-weight:800; margin-bottom:2px} .tier .x{font-size:13px; color:var(--ink2)}
  .view{display:none} .view.on{display:block}
  @media (max-width:760px){
    .app{display:block}.side{position:relative; width:100%; height:auto; border-right:0; border-bottom:1px solid var(--line2)}
    .nav{flex-direction:row; overflow-x:auto}.nav button{min-width:max-content}
    .side .grow,.statuscard,.ver{display:none}.main{padding:20px 16px 44px}
    .top{align-items:flex-start}.top h1{font-size:21px}.top .meta{text-align:left}.c4{grid-template-columns:1fr}
    th,td{padding:8px 9px}.card{padding:16px}
  }
</style></head>
<body>
<div class="app">
  <aside class="side">
    <div class="logo"><span class="mk">◆</span><div>CEM<small>paper trading</small></div></div>
    <nav class="nav" id="nav">
      <button data-v="overview" class="on"><span class="ic">◧</span>Overview</button>
      <button data-v="portfolio"><span class="ic">▤</span>Portfolio</button>
      <button data-v="strategy"><span class="ic">◔</span>Strategy &amp; Backtest</button>
      <button data-v="learn"><span class="ic">✦</span>Learn</button>
    </nav>
    <div class="grow"></div>
    <div class="statuscard">
      <div class="live"><span class="dot"></span>LIVE · HOURLY</div>
      <div class="eq" id="sideEq">—</div>
      <div class="sub" id="sideSub">equity</div>
    </div>
    <div class="ver" id="ver">CEM · IBKR paper</div>
  </aside>

  <main class="main">
    <div class="top">
      <div><h1 id="ttl">Overview</h1><div class="crumb" id="crumb">Live portfolio & allocation</div></div>
      <div style="text-align:right">
        <span class="pill" id="marketPill"><span class="d"></span><span id="marketLabel">LIVE</span></span>
        <div class="meta" id="meta">connecting…</div>
      </div>
    </div>

    <!-- OVERVIEW -->
    <section class="view on" id="overview">
      <div id="alerts"></div>
      <div class="row c4" id="kpis"></div>
      <div class="row c2">
        <div class="card"><h3>NAV · strategy vs passive benchmark</h3><div id="chart"></div>
          <div class="legrow"><span><i style="border-color:var(--brand)"></i>Strategy equity</span>
            <span><i style="border-color:var(--faint); border-top-style:dashed"></i>Passive (hold <span class="benchname">SPY</span>)</span></div></div>
        <div class="card"><h3>Capital allocation</h3><div class="alloc">
          <div class="donut"><div id="donut"></div><div class="ctr"><b id="invpct">—</b><span>invested</span></div></div>
          <div class="leg" id="alloclegend"></div></div></div>
      </div>
      <div class="row c4" id="ministats"></div>
    </section>

    <!-- PORTFOLIO -->
    <section class="view" id="portfolio">
      <div id="palerts"></div>
      <div class="card" style="margin-bottom:18px"><h3>Open positions · runup, Kelly, real cost &amp; slippage</h3>
        <div class="hint">T0 is the first tracked baseline. T epsilon is the entry decision. The T0 to entry columns show how far probability and stock price moved before the trade fired, which is the gate that catches late or over-extended entries.</div>
        <div class="tw" id="positions"></div></div>
      <div class="row c2b">
        <div class="card"><h3>Recent orders</h3><div class="tw" id="orders"></div></div>
        <div class="card"><h3>Recent closed trades</h3><div class="tw" id="trades"></div></div>
      </div>
      <div class="card"><h3>Question watchlist · next resolutions</h3><div class="tw" id="upcoming"></div></div>
    </section>

    <!-- STRATEGY -->
    <section class="view" id="strategy">
      <div class="row c4" id="btsummary"></div>
      <div class="card" style="margin-bottom:18px"><h3 id="btttl">Walk-forward out-of-sample folds · strategy vs benchmark return</h3>
        <div id="btchart"></div>
        <div class="legrow"><span><i style="border-color:var(--brand)"></i>Strategy return</span>
          <span><i style="border-color:var(--faint)"></i>Benchmark return</span></div></div>
      <div class="row c2b">
        <div class="card"><h3>Per-fold detail (out-of-sample)</h3><div class="tw" id="bttable"></div></div>
        <div class="card"><h3>Live policy parameters (latest fold)</h3><div class="params" id="btparams"></div></div>
      </div>
    </section>

    <!-- LEARN -->
    <section class="view learn" id="learn">
      <div class="row c2b">
        <div class="card"><h3>What is this?</h3>
          <p>A rule-based strategy that exploits an <span class="term">information-diffusion lag</span>: when a Polymarket prediction market moves sharply on a catalyst (e.g. an earnings beat), the mapped stock often reacts more slowly. We enter the stock on a high, sustained probability and exit on an ATR trailing stop, a profit-lock, probability invalidation, or the market's resolution.</p>
          <p>It runs <span class="term">hourly</span> against an IBKR paper account, always trading the latest walk-forward policy. Capital is capped to cash plus liquidatable benchmark inventory, and idle cash rotates back into the benchmark index so there is no cash drag.</p></div>
        <div class="card"><h3>CEM · Cross-Entropy Method</h3>
          <p>The rules use hard IF/THEN thresholds, so they aren't differentiable — no gradient descent. <span class="term">CEM</span> instead samples many random policy vectors, simulates the whole portfolio for each, keeps the top "elite" performers, and re-fits its sampling distribution toward them. Repeat until it converges on a strong parameter set.</p>
          <p>The objective is friction-aware: <span class="formula">S = Sharpe - 0.30 &times; MaxDD - 2.0 &times; FFR</span></p></div>
      </div>
      <div class="card" style="margin-bottom:18px"><h3>The experiment tiers — T1 · T2 · T3 · T4</h3>
        <div class="tiers">
          <div class="tier"><div class="b">T1</div><div><div class="t">Friction penalty</div><div class="x">A realized transaction-cost penalty inside the CEM fitness, so policies that only look good before costs are rejected.</div></div></div>
          <div class="tier"><div class="b">T2</div><div><div class="t">Walk-forward windows</div><div class="x">Expanding out-of-sample folds: each fold fits on all history up to a cutoff and is tested on the next unseen window. This is what "keep walking forward" means live.</div></div></div>
          <div class="tier"><div class="b">T3</div><div><div class="t">Half-Kelly sizing</div><div class="x">Position size scales with the strategy's realized win-rate and payoff ratio (half-Kelly, clamped 3–15%) from fully net historical trades.</div></div></div>
          <div class="tier"><div class="b">T4</div><div><div class="t">Geo / event priority</div><div class="x">An allocation mode that prioritizes event-driven positions over the passive benchmark when deploying capital.</div></div></div>
        </div>
        <p style="margin-top:13px" class="mut">The live config is <b class="term" id="expname">T1+T2+T3+T4</b> — all four stacked.</p></div>
      <div class="card"><h3>Walk-forward &amp; Kelly, in one line each</h3>
        <p><span class="term">Walk-forward</span>: never test on data you trained on — refit as time advances so every reported result is genuinely out-of-sample.</p>
        <p><span class="term">Half-Kelly</span>: the Kelly criterion gives the growth-optimal bet size; we take half of it for a smoother ride, and re-estimate it from the live trade history each tick.</p>
        <p><span class="term">The 10 optimized parameters</span> (see Strategy tab): entry floor/strong probability, persistence window, ATR trailing multiple, profit-lock trigger, probability exit threshold, max probability surge &amp; price run-up gates, position size, and max concurrent positions.</p></div>
    </section>
  </main>
</div>
<script>
const $=s=>document.querySelector(s), $$=s=>document.querySelectorAll(s);
const LOCALE="en-US";
let showAllOrders=false;
const nn=(n,d=2)=>n==null?"—":Number(n).toLocaleString(LOCALE,{minimumFractionDigits:d,maximumFractionDigits:d});
const usd=n=>n==null?"—":"$"+nn(n,2), usd0=n=>n==null?"—":"$"+nn(n,0);
const gb=b=>b==null?"—":(b/1e9).toFixed(2)+" GB";
const cl=n=>n==null?"":(n>0?"up":(n<0?"down":""));
const sg=(n,d=2)=>n==null?"—":(n>0?"+":"")+nn(n,d);
const cadence=s=>!s?"—":(s>=3600?(s/3600).toFixed(s%3600?1:0)+"h":Math.round(s/60)+"m");
const dt=s=>s?new Date(s).toLocaleString(LOCALE,{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}):"—";
const dday=s=>s?new Date(s).toLocaleDateString(LOCALE,{month:"short",day:"numeric"}):"";
const C={brand:"#6d5efc",up:"#10b981",down:"#f43f5e",sky:"#38bdf8",amber:"#f59e0b",faint:"#aab4c4",mut:"#7b8698"};
const marketText=m=>!m?"Market status unknown":m.is_open?`Open · closes in ${Math.max(0,Math.round(m.seconds_to_close/60))}m`:"Closed";

// nav
const TITLES={overview:["Overview","Live portfolio & allocation"],portfolio:["Portfolio","Positions, orders & watchlist"],
  strategy:["Strategy & Backtest","Walk-forward out-of-sample performance"],learn:["Learn","How the system works"]};
$$("#nav button").forEach(b=>b.onclick=()=>{
  $$("#nav button").forEach(x=>x.classList.remove("on")); b.classList.add("on");
  $$(".view").forEach(v=>v.classList.remove("on")); const v=b.dataset.v; $("#"+v).classList.add("on");
  $("#ttl").textContent=TITLES[v][0]; $("#crumb").textContent=TITLES[v][1];
  if(v==="strategy") loadBacktest();
});

function smooth(pts){ if(pts.length<2) return "";
  let d="M"+pts[0][0].toFixed(1)+" "+pts[0][1].toFixed(1);
  for(let i=0;i<pts.length-1;i++){const a=pts[i],b=pts[i+1],mx=(a[0]+b[0])/2;
    d+=` C ${mx.toFixed(1)} ${a[1].toFixed(1)}, ${mx.toFixed(1)} ${b[1].toFixed(1)}, ${b[0].toFixed(1)} ${b[1].toFixed(1)}`;} return d;}
function areaChart(el, series){
  const pts=series.filter(p=>p.equity!=null);
  if(pts.length<2){el.innerHTML='<div class="empty">Not enough NAV snapshots yet — the curve appears after a few clean ticks.</div>';return;}
  const W=1000,H=250,pl=52,pr=14,pt=14,pb=24;
  const eq=pts.map(p=>p.equity),pv=pts.map(p=>p.passive==null?p.equity:p.passive);
  const lo=Math.min(...eq,...pv),hi=Math.max(...eq,...pv),sp=(hi-lo)||1;
  const X=i=>pl+i*(W-pl-pr)/(pts.length-1),Y=v=>H-pb-(v-lo)/sp*(H-pt-pb);
  const pe=pts.map((p,i)=>[X(i),Y(p.equity)]),pp=pts.map((p,i)=>[X(i),Y(p.passive==null?p.equity:p.passive)]);
  const area=smooth(pe)+` L ${X(pts.length-1)} ${H-pb} L ${pl} ${H-pb} Z`;
  const grid=[0,.25,.5,.75,1].map(f=>{const y=(pt+f*(H-pt-pb)).toFixed(1),v=hi-f*sp;
    return `<line x1="${pl}" y1="${y}" x2="${W-pr}" y2="${y}" stroke="#eef1f6"/>`+
      `<text x="${pl-8}" y="${+y+4}" fill="${C.faint}" font-size="10" text-anchor="end" font-family="var(--num)">${(v/1000).toFixed(1)}k</text>`;}).join("");
  el.innerHTML=`<svg class="chart" viewBox="0 0 ${W} ${H}"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="${C.brand}" stop-opacity=".18"/><stop offset="1" stop-color="${C.brand}" stop-opacity="0"/></linearGradient></defs>
    ${grid}<path d="${area}" fill="url(#g)"/>
    <path d="${smooth(pp)}" fill="none" stroke="${C.faint}" stroke-width="1.6" stroke-dasharray="5 4"/>
    <path d="${smooth(pe)}" fill="none" stroke="${C.brand}" stroke-width="2.4" stroke-linecap="round"/>
    <text x="${pl}" y="${H-6}" fill="${C.faint}" font-size="10" font-family="var(--num)">${dday(pts[0].ts)}</text>
    <text x="${W-pr}" y="${H-6}" fill="${C.faint}" font-size="10" text-anchor="end" font-family="var(--num)">${dday(pts[pts.length-1].ts)}</text></svg>`;
}
function donut(el, segs){
  const size=150,st=20,r=(size-st)/2,c=2*Math.PI*r,cx=size/2; let off=0;
  const tot=segs.reduce((a,s)=>a+s.v,0)||1;
  const arcs=segs.map(s=>{const f=s.v/tot;const el=`<circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="${s.c}" stroke-width="${st}"
    stroke-dasharray="${(f*c).toFixed(1)} ${c.toFixed(1)}" stroke-dashoffset="${(-off*c).toFixed(1)}"
    transform="rotate(-90 ${cx} ${cx})" style="transition:stroke-dasharray .7s ease"/>`; off+=f; return el;}).join("");
  el.innerHTML=`<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}"><circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="#f0f2f7" stroke-width="${st}"/>${arcs}</svg>`;
}
function barChart(el, folds){
  if(!folds||!folds.length){el.innerHTML='<div class="empty">No backtest folds found.</div>';return;}
  const W=1000,H=250,pl=44,pr=12,pt=16,pb=40,gw=(W-pl-pr)/folds.length;
  const vals=folds.flatMap(f=>[f.return_pct,f.benchmark_pct]);
  const hi=Math.max(...vals,1),lo=Math.min(...vals,0),sp=(hi-lo)||1;
  const Y=v=>H-pb-(v-lo)/sp*(H-pt-pb), y0=Y(0);
  const grid=[0,.5,1].map(f=>{const v=hi-f*(hi-lo),y=Y(v);
    return `<line x1="${pl}" y1="${y}" x2="${W-pr}" y2="${y}" stroke="#eef1f6"/><text x="${pl-6}" y="${y+4}" fill="${C.faint}" font-size="10" text-anchor="end" font-family="var(--num)">${v.toFixed(0)}%</text>`;}).join("");
  const bars=folds.map((f,i)=>{const bx=pl+i*gw, bw=gw*.28, gap=gw*.12;
    const s=`<rect x="${bx+gw/2-bw-gap/2}" y="${Math.min(Y(f.return_pct),y0)}" width="${bw}" height="${Math.abs(Y(f.return_pct)-y0)}" rx="3" fill="${C.brand}"/>`;
    const b=`<rect x="${bx+gw/2+gap/2}" y="${Math.min(Y(f.benchmark_pct),y0)}" width="${bw}" height="${Math.abs(Y(f.benchmark_pct)-y0)}" rx="3" fill="#cfd6e4"/>`;
    const lab=`<text x="${bx+gw/2}" y="${H-24}" fill="${C.mut}" font-size="10.5" text-anchor="middle" font-family="var(--num)">F${f.fold}</text>`+
      `<text x="${bx+gw/2}" y="${H-10}" fill="${f.excess_pct>=0?C.up:C.down}" font-size="10" text-anchor="middle" font-family="var(--num)">${f.excess_pct>0?'+':''}${f.excess_pct.toFixed(1)}</text>`;
    return s+b+lab;}).join("");
  el.innerHTML=`<svg class="chart" viewBox="0 0 ${W} ${H}">${grid}<line x1="${pl}" y1="${y0}" x2="${W-pr}" y2="${y0}" stroke="#d7dde8"/>${bars}</svg>`;
}
function table(rows,cols){ if(!rows.length) return '<div class="empty">Nothing yet.</div>';
  return `<table><thead><tr>${cols.map(c=>`<th>${c.h}</th>`).join("")}</tr></thead><tbody>`+
    rows.map(r=>`<tr>${cols.map(c=>`<td class="${c.n?'n ':''}${c.cl?c.cl(r):''}">${c.f(r)}</td>`).join("")}</tr>`).join("")+`</tbody></table>`;}
const kpi=(l,v,chip,cc)=>`<div class="card stat"><div class="l">${l}</div><div class="v">${v}</div>${chip?`<span class="chip ${cc}">${chip}</span>`:""}</div>`;

async function refresh(){
  let d; try{d=await(await fetch("/api/metrics",{cache:"no-store"})).json();}catch(e){$("#meta").textContent="reconnecting…";return;}
  const p=d.portfolio,a=d.allocation,pf=d.performance,x=d.exec,g=d.safety;
  $$(".benchname").forEach(e=>e.textContent=d.benchmark);
  $("#expname").textContent=d.experiment;
  const mp=$("#marketPill"), ml=$("#marketLabel");
  if(ml) ml.textContent=d.market?.is_open?"MARKET OPEN":"MARKET CLOSED";
  if(mp) mp.classList.toggle("closed", !d.market?.is_open);
  $("#meta").innerHTML=`NAV ${dt(p.as_of)} · ${marketText(d.market)} · ${cadence(d.tick_seconds)} loop · <span id="clk">${new Date().toLocaleTimeString(LOCALE)}</span>`;
  $("#sideEq").textContent=usd0(p.equity); $("#sideSub").textContent=`${p.open_positions} positions · ${d.experiment}`;

  const alert = x.failed.length
    ? `<div class="alert">⚠ <b>${x.failed.length} order(s) not filled</b> recently — `+
      x.failed.slice(0,4).map(o=>`${o.action} ${o.symbol} (${o.kind}: ${o.status})`).join(", ")+
      (a.spy_pct<1?`. Idle cash is <b>not</b> in the ${d.benchmark} index — the sweep is failing.`:"")+`</div>` : "";
  $("#alerts").innerHTML=alert; $("#palerts").innerHTML=alert;

  $("#kpis").innerHTML=
    kpi("Equity", usd0(p.equity), d.experiment, "neu")+
    kpi(`In ${d.benchmark} index`, usd0(a.spy_value), a.spy_pct+"%", a.spy_value<1?"down":"neu")+
    kpi("In event trades", usd0(a.trades_value), a.trades_pct+"%", "neu")+
    kpi("Excess vs passive", sg(p.excess), p.excess_pct==null?null:sg(p.excess_pct)+"%", cl(p.excess)||"neu");

  donut($("#donut"),[{v:a.spy_pct,c:C.brand},{v:a.trades_pct,c:C.sky},{v:a.cash_pct,c:"#dfe4ee"}]);
  $("#invpct").textContent=nn(a.invested_pct,0)+"%";
  const li=(c,nm,v,pc)=>`<div class="it"><span class="sw" style="background:${c}"></span><span class="nm">${nm}</span><span class="vl">${usd0(v)}</span><span class="pc">${nn(pc,1)}%</span></div>`;
  $("#alloclegend").innerHTML=li(C.brand,d.benchmark+" index",a.spy_value,a.spy_pct)+
    li(C.sky,"Event trades ("+p.open_positions+")",a.trades_value,a.trades_pct)+
    li("#dfe4ee","Idle cash",a.cash,a.cash_pct)+
    `<div class="it"><span class="nm mut" style="font-size:11.5px">${nn(a.bench_shares,3)} ${d.benchmark} @ ${usd(a.bench_price)}</span></div>`;

  areaChart($("#chart"), d.equity_series);

  const s=d.system, up=(s.disk_total_bytes&&s.disk_free_bytes)?(1-s.disk_free_bytes/s.disk_total_bytes)*100:null, c2=d.api_cost;
  $("#ministats").innerHTML=
    `<div class="card"><h3>Performance</h3>
       <div class="kv"><span class="k">Realized PnL</span><span class="v ${cl(pf.realized_pnl)}">${sg(pf.realized_pnl)}</span></div>
       <div class="kv"><span class="k">Win rate</span><span class="v">${pf.win_rate==null?"—":pf.win_rate+"%"}</span></div>
       <div class="kv"><span class="k">Closed / open</span><span class="v">${pf.closed_trades} / ${p.open_positions}</span></div>
       <div class="kv"><span class="k">Fills ok</span><span class="v ${x.filled<x.recent?'down':''}">${x.filled}/${x.recent}</span></div></div>
    <div class="card"><h3>Execution guard</h3>
       <div class="kv"><span class="k">Market</span><span class="v">${marketText(d.market)}</span></div>
       <div class="kv"><span class="k">Cadence</span><span class="v">${cadence(d.tick_seconds)}</span></div>
       <div class="kv"><span class="k">Capital base</span><span class="v">${usd0(g.investable)}</span></div>
       <div class="kv"><span class="k">Buy cap</span><span class="v">+${nn(g.execution_buffer_pct,2)}%</span></div>
       <div class="kv"><span class="k">Sizing</span><span class="v">${g.kelly_enabled?"Half-Kelly":"Fixed"} · min ${usd0(g.min_order_notional)}</span></div></div>
    <div class="card"><h3>System</h3>
       <div class="kv"><span class="k">DB size</span><span class="v">${gb(s.db_size_bytes)}</span></div>
       <div class="kv"><span class="k">Disk free</span><span class="v">${gb(s.disk_free_bytes)}</span></div>
       <div class="meter" style="margin-top:10px"><span style="width:${up==null?0:up.toFixed(0)}%;background:${up>90?C.down:up>75?C.amber:C.up}"></span></div>
       <div class="mut" style="font-size:11px;margin-top:6px">${up==null?"":up.toFixed(0)+"% used"}</div></div>
    <div class="card"><h3>Gemini spend</h3>
       <div class="kv"><span class="k">Today</span><span class="v">${usd(c2.today_usd)} <span class="mut" style="font-weight:400">${c2.today_calls} calls</span></span></div>
       <div class="kv"><span class="k">Total</span><span class="v">${usd(c2.total_usd)} <span class="mut" style="font-weight:400">${c2.total_calls} calls</span></span></div>
       <div class="mut" style="font-size:10.5px;margin-top:8px">Estimate · configurable per-token rates</div></div>`;

  $("#positions").innerHTML=table(d.open_positions,[
    {h:"Sym",f:r=>r.symbol+(r.is_earnings?' <span class="tag er">ER</span>':'')},
    {h:"Qty",n:1,f:r=>r.qty},{h:"Entry",n:1,f:r=>nn(r.entry_price)},{h:"Last",n:1,f:r=>nn(r.last)},
    {h:"Stop",n:1,f:r=>r.is_earnings?'<span class="tag" title="Earnings: theta-only exit">θ</span>':(r.stop_loss==null?"—":nn(r.stop_loss)),
      cl:r=>!r.is_earnings&&r.stop_loss!=null&&r.last<=r.stop_loss*1.02?"down":""},
    {h:"Unreal $",n:1,f:r=>sg(r.unrealized),cl:r=>cl(r.unrealized)},
    {h:"Unreal %",n:1,f:r=>r.unrealized_pct==null?"—":sg(r.unrealized_pct)+"%",cl:r=>cl(r.unrealized_pct)},
    {h:"Prob now",n:1,f:r=>r.prob_now==null?"—":nn(r.prob_now,3)},
    {h:"Prob T0→Entry",n:1,f:r=>r.prob_entry_runup_pp==null?"—":sg(r.prob_entry_runup_pp,1)+"pp",cl:r=>cl(r.prob_entry_runup_pp)},
    {h:"Stock T0→Entry",n:1,f:r=>r.stock_entry_runup_pct==null?"—":sg(r.stock_entry_runup_pct,2)+"%",cl:r=>cl(r.stock_entry_runup_pct)},
    {h:"Stock T0→Now",n:1,f:r=>r.stock_runup_pct==null?"—":sg(r.stock_runup_pct,2)+"%",cl:r=>cl(r.stock_runup_pct)},
    {h:"Kelly",n:1,f:r=>r.kelly_pct==null?"—":`<span class="tag brand">${nn(r.kelly_pct,1)}%</span>`},
    {h:"Comm",n:1,f:r=>r.commission==null?"—":usd(r.commission)},
    {h:"Slip",n:1,f:r=>r.slip_bps==null?"—":sg(r.slip_bps,1)+"bp",cl:r=>r.slip_bps==null?"":(r.slip_bps>0?"down":"up")},
    {h:"Opened",n:1,f:r=>dt(r.entry_ts)},
    {h:"Question",f:r=>r.question,cl:()=>"q"}]);
  const orderRows=showAllOrders?d.recent_orders:d.recent_orders.slice(0,10);
  $("#orders").innerHTML=table(orderRows,[
    {h:"When",n:1,f:r=>dt(r.ts)},{h:"Sym",f:r=>r.symbol},{h:"Side",f:r=>r.action},
    {h:"Qty",n:1,f:r=>nn(r.qty,r.qty<10?3:0)},{h:"Kind",f:r=>`<span class="tag">${r.kind}</span>`},
    {h:"Fill",n:1,f:r=>nn(r.fill_price)},{h:"Comm",n:1,f:r=>r.commission==null?"—":usd(r.commission)},
    {h:"Slip",n:1,f:r=>r.slip_bps==null?"—":sg(r.slip_bps,1)+"bp",cl:r=>r.slip_bps==null?"":(r.slip_bps>0?"down":"up")},
    {h:"Status",f:r=>{const ok=r.status==="Filled"||r.status==="dry_run",bad=["Cancelled","unqualified","ApiCancelled","Inactive","dry_run_limit_miss","dry_run_no_price"].includes(r.status);
      return `<span class="tag ${ok?"ok":bad?"bad":"warn"}">${r.status}</span>`;}}])+
    (d.recent_orders.length>10?`<div class="toolbar"><button class="miniBtn" id="ordersToggle">${showAllOrders?"Show last 10":"Show all "+d.recent_orders.length}</button></div>`:"")
  const toggle=$("#ordersToggle"); if(toggle) toggle.onclick=()=>{showAllOrders=!showAllOrders; refresh();};
  $("#trades").innerHTML=table(d.recent_trades,[
    {h:"Sym",f:r=>r.symbol},{h:"PnL",n:1,f:r=>sg(r.pnl),cl:r=>cl(r.pnl)},
    {h:"%",n:1,f:r=>r.pnl_pct==null?"—":sg(r.pnl_pct)+"%",cl:r=>cl(r.pnl_pct)},
    {h:"Reason",f:r=>r.exit_reason||"—",cl:()=>"q"},{h:"When",n:1,f:r=>dt(r.exit_ts)}]);
  // Enriched question watchlist with prob, assets, relevance, pecking order
  const probChip=(v)=>{if(v==null) return '—'; const c=v>=0.7?C.up:v>=0.4?C.amber:C.down;
    return `<span style="color:${c};font-weight:700">${nn(v,2)}</span>`;};
  const assetTags=(a)=>!a||!a.length?'<span class="mut">—</span>':a.map(x=>`<span class="tag">${x.symbol}</span>`).join(' ');
  const relChip=(v)=>{if(v==null) return '—'; const c=v>=0.8?'ok':v>=0.6?'warn':'bad';
    return `<span class="tag ${c}">${nn(v,2)}</span>`;};
  $("#upcoming").innerHTML=table(d.markets.upcoming,[
    {h:"#",n:1,f:r=>`<span style="font-weight:800;color:var(--brand)">${r.pecking}</span>`},
    {h:"Resolves",n:1,f:r=>dday(r.end_at)},
    {h:"Market question",f:r=>r.question+(r.is_earnings?' <span class="tag er">ER</span>':''),cl:()=>"q"},
    {h:"Relevance",n:1,f:r=>relChip(r.relevance)},
    {h:"Prob T0",n:1,f:r=>probChip(r.t0_prob)},
    {h:"Prob Now",n:1,f:r=>probChip(r.prob_now)},
    {h:"Δ prob",n:1,f:r=>r.prob_delta==null?"—":sg(r.prob_delta,1)+"pp",cl:r=>cl(r.prob_delta)},
    {h:"Mapped assets",f:r=>assetTags(r.assets)}])+
    `<div class="mut" style="margin-top:10px;font-size:11.5px">${d.markets.tracked} markets tracked · ${d.markets.with_t0} with T0 baseline · ranked by Δprob (T4 pecking order)</div>`;
}

let btLoaded=false;
async function loadBacktest(){ if(btLoaded) return; btLoaded=true;
  let d; try{d=await(await fetch("/api/backtest",{cache:"no-store"})).json();}catch(e){return;}
  if(!d.available){$("#btsummary").innerHTML='<div class="card"><div class="empty">Backtest CSV not found on the server.</div></div>';return;}
  const s=d.summary;
  $("#btttl").textContent=`Walk-forward out-of-sample folds · ${d.experiment} / ${d.benchmark}`;
  $("#btsummary").innerHTML=
    kpi("OOS total return", sg(s.total_return_pct,1)+"%", s.n_folds+" folds", "neu")+
    kpi("Benchmark", sg(s.total_benchmark_pct,1)+"%", null)+
    kpi("Excess", sg(s.total_excess_pct,1)+"%", s.positive_folds+"/"+s.n_folds+" folds +", s.total_excess_pct>=0?"up":"down")+
    kpi("Worst drawdown", nn(s.worst_dd_pct,1)+"%", s.total_trades+" trades", "neu");
  barChart($("#btchart"), d.folds);
  $("#bttable").innerHTML=table(d.folds,[
    {h:"Fold",f:r=>"F"+r.fold},{h:"Window",f:r=>`<span class="mut" style="font-size:11px">${r.start}→${r.end}</span>`},
    {h:"Return",n:1,f:r=>sg(r.return_pct,1)+"%",cl:r=>cl(r.return_pct)},
    {h:"Bench",n:1,f:r=>sg(r.benchmark_pct,1)+"%"},
    {h:"Excess",n:1,f:r=>sg(r.excess_pct,1)+"%",cl:r=>cl(r.excess_pct)},
    {h:"MaxDD",n:1,f:r=>nn(r.max_dd_pct,1)+"%",cl:()=>"down"},{h:"Trades",n:1,f:r=>r.trades}]);
  const P=d.policy, defs={
    enter_strong:["Enter (strong)","prob fires now"],enter_floor:["Enter (floor)","held prob"],
    hold_days:["Hold window","trained days, checked hourly"],atr_mult:["ATR mult","trailing stop"],lock_activate:["Lock at","profit-lock trigger"],
    theta_out:["Theta out","prob exit"],max_prob_surge:["Max Δprob","surge gate"],max_price_runup:["Max runup","price gate"],
    position_size_pct:["Base size","of equity"],max_concurrent:["Max concurrent","positions"]};
  $("#btparams").innerHTML=Object.keys(defs).map(k=>{ if(P[k]==null) return "";
    let v=P[k]; if(["enter_strong","enter_floor","theta_out","lock_activate","max_prob_surge","max_price_runup","position_size_pct"].includes(k)) v=(v*100).toFixed(k==="position_size_pct"||k==="lock_activate"?1:0)+(k==="position_size_pct"?"%":k==="lock_activate"?"%":"%");
    else if(k==="atr_mult") v=Number(v).toFixed(2)+"×"; else v=String(v);
    return `<div class="param"><div class="k">${defs[k][0]}</div><div class="v">${v}</div><div class="d">${defs[k][1]}</div></div>`;}).join("");
}
setInterval(()=>{const e=document.getElementById("clk"); if(e) e.textContent=new Date().toLocaleTimeString(LOCALE);},1000);
refresh(); setInterval(refresh,20000);
</script>
</body></html>
"""


def main() -> None:
    port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    host = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
