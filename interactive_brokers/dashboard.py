"""Live, read-only web dashboard for the paper-trading pipeline.

Terminal-style operational view (default 0.0.0.0:8080). Read-only: it never
connects to IB and never trades. Everything is served from the shared Postgres.

    python -m interactive_brokers.dashboard      # or the docker compose `dashboard` service

Shows: capital allocation (S&P index vs event trades vs cash), NAV vs the passive
benchmark, per-position prob-runup + stock-runup + Kelly + real IB commission +
slippage, recent orders (with failed/cancelled flagged), next resolutions, DB/disk,
and Gemini spend.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from database.backtesting.schema import SCHEMA

from .config import CONFIG
from .database import LiveStore

_STATE: dict = {}
BENCH = CONFIG.benchmark


@asynccontextmanager
async def lifespan(app: FastAPI):
    _STATE["store"] = await LiveStore.create()
    try:
        yield
    finally:
        await _STATE["store"].close()


app = FastAPI(lifespan=lifespan, title="CEM live paper-trading terminal")


def _iso(ts) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.astimezone(timezone.utc).isoformat()
    return str(ts)


def _f(v):
    return float(v) if v is not None else None


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
                FROM {SCHEMA}.live_orders ORDER BY ts DESC LIMIT 40""")
        trades = await conn.fetch(
            f"""SELECT symbol, pnl, pnl_pct, exit_reason, exit_ts
                FROM {SCHEMA}.live_positions WHERE status='closed'
                ORDER BY exit_ts DESC LIMIT 15""")
        markets = await conn.fetchrow(
            f"""SELECT COUNT(*) AS tracked,
                       COUNT(*) FILTER (WHERE t0_prob IS NOT NULL) AS with_t0
                FROM {SCHEMA}.live_tracked_markets WHERE status='tracking'""")
        upcoming = await conn.fetch(
            f"""SELECT end_at, question FROM {SCHEMA}.live_tracked_markets
                WHERE status='tracking' ORDER BY end_at LIMIT 10""")
        sys_latest = await conn.fetchrow(
            f"SELECT * FROM {SCHEMA}.live_system_metrics ORDER BY ts DESC LIMIT 1")
        cost = await conn.fetchrow(
            f"""SELECT COALESCE(SUM(est_cost_usd), 0) AS total,
                       COALESCE(SUM(calls), 0) AS calls,
                       COALESCE(SUM(est_cost_usd) FILTER (WHERE ts >= date_trunc('day', now())), 0) AS today,
                       COALESCE(SUM(calls) FILTER (WHERE ts >= date_trunc('day', now())), 0) AS today_calls
                FROM {SCHEMA}.live_api_costs""")
        # Per-position lookups, batched.
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

    # ── Per-position enrichment ──────────────────────────────────────────
    positions, trades_value = [], 0.0
    for p in open_pos:
        last = await store.latest_close(p["symbol"]) or float(p["entry_price"])
        entry = float(p["entry_price"])
        trades_value += int(p["qty"]) * last
        mk = mkt_map.get(p["market_id"])
        t0_prob = _f(mk["t0_prob"]) if mk else None
        prob_now = await store.latest_prob(p["market_id"])
        stock_t0 = await store.close_at(p["symbol"], mk["discovered_at"]) if mk and mk["discovered_at"] else None
        eo = entry_map.get(p["position_id"])
        ref = _f(eo["reference_price"]) if eo else None
        efill = _f(eo["fill_price"]) if eo else entry
        slip_bps = ((efill / ref - 1.0) * 1e4) if (ref and efill) else None
        positions.append({
            "symbol": p["symbol"], "qty": int(p["qty"]),
            "entry_price": round(entry, 2), "last": round(float(last), 2),
            "unrealized": round(int(p["qty"]) * (last - entry), 2),
            "unrealized_pct": round((last / entry - 1.0) * 100.0, 2) if entry else None,
            "prob_now": round(float(prob_now), 3) if prob_now is not None else None,
            "prob_t0": round(t0_prob, 3) if t0_prob is not None else None,
            "prob_runup_pp": round((float(prob_now) - t0_prob) * 100.0, 1)
                if (prob_now is not None and t0_prob is not None) else None,
            "stock_runup_pct": round((float(last) / float(stock_t0) - 1.0) * 100.0, 2)
                if (stock_t0 and float(stock_t0) > 0) else None,
            "kelly_pct": round(float(p["position_size_pct"]) * 100.0, 1)
                if p["position_size_pct"] is not None else None,
            "commission": round(_f(eo["commission"]), 2) if eo and eo["commission"] is not None else None,
            "slip_bps": round(slip_bps, 1) if slip_bps is not None else None,
            "is_earnings": bool(p["is_earnings"]),
            "question": p["question"][:70], "entry_ts": _iso(p["entry_ts"]),
        })

    # ── Allocation: S&P index vs event trades vs cash ────────────────────
    bench_shares = float(eq["benchmark_shares"]) if eq else 0.0
    bench_price = await store.latest_close(BENCH)
    spy_value = bench_shares * (bench_price or 0.0)
    cash = float(eq["cash"]) if eq else 0.0
    alloc_equity = spy_value + trades_value + cash

    closed = int(perf["closed"] or 0)
    wins = int(perf["wins"] or 0)
    filled = sum(1 for o in orders if o["status"] in ("Filled", "dry_run"))
    failed = [
        {"ts": _iso(o["ts"]), "symbol": o["symbol"], "action": o["action"],
         "qty": round(float(o["qty"]), 2), "kind": o["kind"], "status": o["status"]}
        for o in orders if o["status"] not in ("Filled", "dry_run")
    ]

    return {
        "generated_at": _iso(datetime.now(timezone.utc)),
        "benchmark": BENCH,
        "portfolio": {
            "equity": round(equity, 2) if equity is not None else None,
            "cash": round(cash, 2),
            "open_positions": len(positions),
            "excess": round(excess, 2) if excess is not None else None,
            "excess_pct": round(excess / passive * 100.0, 2) if excess is not None and passive else None,
            "as_of": _iso(eq["ts"]) if eq else None,
        },
        "allocation": {
            "spy_value": round(spy_value, 2), "trades_value": round(trades_value, 2),
            "cash": round(cash, 2), "total": round(alloc_equity, 2),
            "spy_pct": round(spy_value / alloc_equity * 100.0, 1) if alloc_equity else 0.0,
            "trades_pct": round(trades_value / alloc_equity * 100.0, 1) if alloc_equity else 0.0,
            "cash_pct": round(cash / alloc_equity * 100.0, 1) if alloc_equity else 0.0,
            "bench_shares": round(bench_shares, 4), "bench_price": round(bench_price, 2) if bench_price else None,
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
            for r in reversed(eq_series)
        ],
        "open_positions": positions,
        "recent_orders": [
            {"ts": _iso(r["ts"]), "symbol": r["symbol"], "action": r["action"],
             "qty": round(float(r["qty"]), 4), "kind": r["kind"],
             "fill_price": round(float(r["fill_price"]), 2) if r["fill_price"] is not None else None,
             "commission": round(float(r["commission"]), 2) if r["commission"] is not None else None,
             "slip_bps": round((float(r["fill_price"]) / float(r["reference_price"]) - 1.0) * 1e4, 1)
                 if r["fill_price"] and r["reference_price"] else None,
             "status": r["status"]}
            for r in orders
        ],
        "recent_trades": [
            {"symbol": r["symbol"],
             "pnl": round(float(r["pnl"]), 2) if r["pnl"] is not None else None,
             "pnl_pct": round(float(r["pnl_pct"]), 2) if r["pnl_pct"] is not None else None,
             "exit_reason": r["exit_reason"], "exit_ts": _iso(r["exit_ts"])}
            for r in trades
        ],
        "markets": {
            "tracked": int(markets["tracked"] or 0), "with_t0": int(markets["with_t0"] or 0),
            "upcoming": [{"end_at": _iso(r["end_at"]), "question": r["question"][:70]} for r in upcoming],
        },
        "system": {
            "db_size_bytes": int(sys_latest["db_size_bytes"]) if sys_latest and sys_latest["db_size_bytes"] else None,
            "disk_free_bytes": int(sys_latest["disk_free_bytes"]) if sys_latest and sys_latest["disk_free_bytes"] else None,
            "disk_total_bytes": int(sys_latest["disk_total_bytes"]) if sys_latest and sys_latest["disk_total_bytes"] else None,
            "as_of": _iso(sys_latest["ts"]) if sys_latest else None,
        },
        "api_cost": {
            "today_usd": round(float(cost["today"] or 0.0), 4),
            "total_usd": round(float(cost["total"] or 0.0), 4),
            "today_calls": int(cost["today_calls"] or 0),
            "total_calls": int(cost["calls"] or 0),
        },
    }


@app.get("/api/metrics")
async def api_metrics() -> JSONResponse:
    return JSONResponse(await gather_metrics())


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(PAGE)


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CEM Terminal</title>
<style>
  :root{
    --bg:#080a0e; --panel:#0f1319; --panel2:#141922; --line:#212834; --line2:#2c3543;
    --fg:#e9eef6; --mut:#7a869a; --dim:#525d6e;
    --amber:#f2a93b; --amber2:#c9862a; --grn:#3ddc97; --red:#ff5c6c; --blue:#5aa2f0; --violet:#a78bfa;
    --num:"JetBrains Mono",ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  }
  *{box-sizing:border-box}
  html,body{margin:0}
  body{background:
      radial-gradient(1200px 500px at 80% -10%, rgba(242,169,59,.06), transparent 60%),
      var(--bg);
    color:var(--fg);
    font:13px/1.45 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    letter-spacing:.1px;}
  .num{font-family:var(--num); font-variant-numeric:tabular-nums; letter-spacing:0}
  .pos{color:var(--grn)} .neg{color:var(--red)} .mut{color:var(--mut)} .amber{color:var(--amber)}
  a{color:var(--blue)}
  /* Header */
  header{display:flex; align-items:center; gap:16px; padding:12px 22px;
    border-bottom:1px solid var(--line); background:linear-gradient(180deg,#0c0f15,#0a0c11);
    position:sticky; top:0; z-index:5}
  .brand{display:flex; align-items:center; gap:10px; font-weight:700; letter-spacing:.14em; font-size:13px}
  .brand .mk{width:12px;height:12px;transform:rotate(45deg);
    background:linear-gradient(135deg,var(--amber),var(--amber2)); box-shadow:0 0 14px rgba(242,169,59,.5)}
  .brand small{color:var(--mut); font-weight:600; letter-spacing:.2em}
  .live{display:flex; align-items:center; gap:7px; font:600 11px/1 var(--num); color:var(--grn);
    padding:4px 9px; border:1px solid rgba(61,220,151,.3); border-radius:20px; background:rgba(61,220,151,.06)}
  .live .dot{width:7px;height:7px;border-radius:50%;background:var(--grn); box-shadow:0 0 8px var(--grn); animation:pulse 1.8s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
  header .spacer{flex:1}
  header .meta{font:500 11px/1.5 var(--num); color:var(--mut); text-align:right}
  main{padding:18px 22px 40px; max-width:1320px; margin:0 auto}
  /* KPI strip */
  .kpis{display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:1px;
    background:var(--line); border:1px solid var(--line); border-radius:12px; overflow:hidden; margin-bottom:16px}
  .kpi{background:var(--panel); padding:13px 15px}
  .kpi .l{color:var(--mut); font-size:10px; text-transform:uppercase; letter-spacing:.12em; margin-bottom:7px}
  .kpi .v{font-family:var(--num); font-variant-numeric:tabular-nums; font-size:21px; font-weight:600}
  .kpi .v.sm{font-size:16px}
  /* Panels */
  .panel{background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:15px 16px; margin-bottom:16px}
  .panel > h2{margin:0 0 13px; font-size:11px; text-transform:uppercase; letter-spacing:.14em; color:var(--mut);
    display:flex; align-items:center; gap:8px}
  .panel > h2::before{content:""; width:3px; height:12px; background:var(--amber); border-radius:2px}
  .grid2{display:grid; grid-template-columns:1fr 1fr; gap:16px}
  .grid3{display:grid; grid-template-columns:1.3fr 1fr 1fr; gap:16px}
  @media (max-width:900px){ .grid2,.grid3{grid-template-columns:1fr} }
  /* Allocation bar */
  .allocbar{height:26px; border-radius:7px; overflow:hidden; display:flex; background:var(--panel2); border:1px solid var(--line2)}
  .allocbar > span{height:100%; transition:width .5s ease}
  .alloc-legend{display:flex; flex-wrap:wrap; gap:18px; margin-top:12px}
  .alloc-legend .it{display:flex; flex-direction:column; gap:2px}
  .alloc-legend .it .top{display:flex; align-items:center; gap:7px; color:var(--mut); font-size:11px; text-transform:uppercase; letter-spacing:.08em}
  .alloc-legend .it .v{font-family:var(--num); font-variant-numeric:tabular-nums; font-size:16px; font-weight:600}
  .sw{width:10px;height:10px;border-radius:3px}
  /* Chart */
  svg.chart{width:100%; height:230px; display:block}
  .legend{display:flex; gap:18px; font-size:11px; color:var(--mut); margin-top:8px}
  .legend .k{display:inline-block; width:18px; height:0; border-top:2px solid; margin-right:6px; vertical-align:middle}
  /* Tables */
  .tw{overflow-x:auto; margin:-2px}
  table{width:100%; border-collapse:collapse; font-size:12.5px}
  th,td{padding:7px 10px; border-bottom:1px solid var(--line); white-space:nowrap; text-align:right}
  th:first-child,td:first-child{text-align:left}
  thead th{color:var(--dim); font-weight:600; font-size:10px; text-transform:uppercase; letter-spacing:.1em;
    position:sticky; top:0; background:var(--panel)}
  tbody tr:hover{background:rgba(255,255,255,.02)}
  td.n{font-family:var(--num); font-variant-numeric:tabular-nums}
  td.q{text-align:left; color:var(--mut); max-width:320px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
  .tag{font:600 9px/1.4 var(--num); padding:1px 6px; border-radius:5px; border:1px solid var(--line2); color:var(--mut); text-transform:uppercase}
  .tag.er{color:var(--amber); border-color:rgba(242,169,59,.35)}
  .tag.ok{color:var(--grn); border-color:rgba(61,220,151,.3)}
  .tag.bad{color:var(--red); border-color:rgba(255,92,108,.35); background:rgba(255,92,108,.06)}
  .tag.warn{color:var(--amber); border-color:rgba(242,169,59,.35)}
  .empty{color:var(--dim); font-style:italic; padding:10px 2px}
  .alert{display:flex; gap:10px; align-items:center; background:rgba(255,92,108,.08); border:1px solid rgba(255,92,108,.3);
    color:#ffb3ba; border-radius:10px; padding:10px 14px; margin-bottom:16px; font-size:12.5px}
  .alert b{color:var(--red)}
  .meter{height:8px; border-radius:5px; background:var(--panel2); overflow:hidden; border:1px solid var(--line2)}
  .meter > span{display:block; height:100%}
  .kv{display:flex; justify-content:space-between; gap:10px; padding:5px 0; border-bottom:1px solid var(--line)}
  .kv:last-child{border-bottom:0}
  .kv .k{color:var(--mut)} .kv .v{font-family:var(--num); font-variant-numeric:tabular-nums; font-weight:600}
</style></head>
<body>
<header>
  <div class="brand"><span class="mk"></span>CEM&nbsp;TERMINAL <small>· PAPER</small></div>
  <span class="live"><span class="dot"></span>LIVE</span>
  <div class="spacer" style="flex:1"></div>
  <div class="meta" id="meta">connecting…</div>
</header>
<main>
  <div id="alerts"></div>
  <div class="kpis" id="kpis"></div>

  <div class="panel">
    <h2>Capital allocation · index vs event trades</h2>
    <div class="allocbar" id="allocbar"></div>
    <div class="alloc-legend" id="alloclegend"></div>
  </div>

  <div class="panel">
    <h2>NAV · strategy vs passive benchmark</h2>
    <div id="chart"></div>
    <div class="legend">
      <span><span class="k" style="border-color:var(--amber)"></span>Strategy equity</span>
      <span><span class="k" style="border-color:var(--mut); border-top-style:dashed"></span>Passive (hold <span id="benchname">SPY</span>)</span>
    </div>
  </div>

  <div class="panel">
    <h2>Open positions · runup, Kelly, real cost &amp; slippage</h2>
    <div class="tw" id="positions"></div>
  </div>

  <div class="grid2">
    <div class="panel"><h2>Recent orders</h2><div class="tw" id="orders"></div></div>
    <div class="panel"><h2>Recent closed trades</h2><div class="tw" id="trades"></div></div>
  </div>

  <div class="grid3">
    <div class="panel"><h2>Next resolutions</h2><div class="tw" id="upcoming"></div></div>
    <div class="panel"><h2>System · DB &amp; disk</h2><div id="system"></div></div>
    <div class="panel"><h2>Gemini spend</h2><div id="cost"></div></div>
  </div>
</main>
<script>
const $=id=>document.getElementById(id);
const nn=(n,d=2)=>n==null?"—":Number(n).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});
const usd=n=>n==null?"—":"$"+nn(n,2);
const usd0=n=>n==null?"—":"$"+nn(n,0);
const gb=b=>b==null?"—":(b/1e9).toFixed(2)+" GB";
const cls=n=>n==null?"":(n>0?"pos":(n<0?"neg":""));
const sgn=(n,d=2)=>n==null?"—":(n>0?"+":"")+nn(n,d);
const dt=s=>s?new Date(s).toLocaleString([], {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}):"—";
const dday=s=>s?new Date(s).toLocaleDateString([], {month:"short",day:"numeric"}):"";

function chart(series){
  const el=$("chart"), pts=series.filter(p=>p.equity!=null);
  if(pts.length<2){ el.innerHTML='<div class="empty">Not enough NAV snapshots yet — the curve appears after a few clean ticks.</div>'; return; }
  const W=1000,H=230,pl=52,pr=12,pt=12,pb=22;
  const eq=pts.map(p=>p.equity), pv=pts.map(p=>p.passive==null?p.equity:p.passive);
  const lo=Math.min(...eq,...pv), hi=Math.max(...eq,...pv), sp=(hi-lo)||1;
  const x=i=>pl+i*(W-pl-pr)/(pts.length-1), y=v=>H-pb-(v-lo)/sp*(H-pt-pb);
  const path=a=>a.map((v,i)=>(i?"L":"M")+x(i).toFixed(1)+" "+y(v).toFixed(1)).join(" ");
  const area=path(eq)+` L ${x(pts.length-1).toFixed(1)} ${(H-pb)} L ${pl} ${(H-pb)} Z`;
  const grid=[0,.25,.5,.75,1].map(f=>{const yy=(pt+f*(H-pt-pb)).toFixed(1),val=hi-f*sp;
    return `<line x1="${pl}" y1="${yy}" x2="${W-pr}" y2="${yy}" stroke="var(--line)" stroke-width=".5"/>`+
      `<text x="${pl-8}" y="${(+yy+3)}" fill="var(--dim)" font-size="10" text-anchor="end" font-family="var(--num)">${(val/1000).toFixed(1)}k</text>`;}).join("");
  el.innerHTML=`<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <defs><linearGradient id="ag" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="var(--amber)" stop-opacity=".22"/><stop offset="1" stop-color="var(--amber)" stop-opacity="0"/></linearGradient></defs>
    ${grid}
    <path d="${area}" fill="url(#ag)"/>
    <path d="${path(pv)}" fill="none" stroke="var(--mut)" stroke-width="1.3" stroke-dasharray="4 3" opacity=".85"/>
    <path d="${path(eq)}" fill="none" stroke="var(--amber)" stroke-width="2"/>
    <text x="${pl}" y="${H-6}" fill="var(--dim)" font-size="10" font-family="var(--num)">${dday(pts[0].ts)}</text>
    <text x="${W-pr}" y="${H-6}" fill="var(--dim)" font-size="10" text-anchor="end" font-family="var(--num)">${dday(pts[pts.length-1].ts)}</text>
  </svg>`;
}

function table(rows, cols){
  if(!rows.length) return '<div class="empty">Nothing yet.</div>';
  const h="<tr>"+cols.map(c=>`<th>${c.h}</th>`).join("")+"</tr>";
  const b=rows.map(r=>"<tr>"+cols.map(c=>`<td class="${c.n?'n ':''}${c.cl?c.cl(r):''}">${c.f(r)}</td>`).join("")+"</tr>").join("");
  return `<table><thead>${h}</thead><tbody>${b}</tbody></table>`;
}
const kpi=(l,v,k="")=>`<div class="kpi"><div class="l">${l}</div><div class="v ${k}">${v}</div></div>`;

async function refresh(){
  let d; try{ d=await (await fetch("/api/metrics",{cache:"no-store"})).json(); }
  catch(e){ $("meta").textContent="connection error — retrying…"; return; }
  const p=d.portfolio, perf=d.performance, a=d.allocation, x=d.exec;
  $("benchname").textContent=d.benchmark;
  $("meta").innerHTML=`NAV ${dt(p.as_of)} &nbsp;·&nbsp; refreshed <span id="clk">${new Date().toLocaleTimeString()}</span>`;

  // alerts (failed/cancelled orders)
  $("alerts").innerHTML = x.failed.length
    ? `<div class="alert">⚠ <b>${x.failed.length} order(s) not filled</b> recently — `+
      x.failed.slice(0,4).map(o=>`${o.action} ${o.symbol} (${o.kind}: ${o.status})`).join(", ")+
      `. ${a.spy_pct<1?'Idle cash is not in the index — the S&P sweep is failing.':''}</div>` : "";

  $("kpis").innerHTML=
    kpi("Equity", usd0(p.equity)) +
    kpi(`In ${d.benchmark}`, usd0(a.spy_value), a.spy_value<1?"neg":"") +
    kpi("In event trades", usd0(a.trades_value)) +
    kpi("Cash", usd0(a.cash), "sm") +
    kpi("Excess vs passive", sgn(p.excess), cls(p.excess)) +
    kpi("Realized PnL", sgn(perf.realized_pnl), cls(perf.realized_pnl)) +
    kpi("Win / Open", `${perf.wins}·${perf.closed_trades} / ${p.open_positions}`, "sm") +
    kpi("Fills ok", `${x.filled}/${x.recent}`, x.filled<x.recent?"sm neg":"sm");

  // allocation bar
  const seg=(w,c)=>w>0?`<span style="width:${w}%;background:${c}"></span>`:"";
  $("allocbar").innerHTML=seg(a.spy_pct,"var(--amber)")+seg(a.trades_pct,"var(--blue)")+seg(a.cash_pct,"var(--dim)");
  const li=(sw,l,v,pct)=>`<div class="it"><div class="top"><span class="sw" style="background:${sw}"></span>${l}</div>`+
    `<div class="v">${usd(v)} <span class="mut" style="font-size:12px">${nn(pct,1)}%</span></div></div>`;
  $("alloclegend").innerHTML=
    li("var(--amber)", d.benchmark+" index", a.spy_value, a.spy_pct)+
    li("var(--blue)", "Event trades ("+p.open_positions+")", a.trades_value, a.trades_pct)+
    li("var(--dim)", "Idle cash", a.cash, a.cash_pct)+
    `<div class="it"><div class="top">${d.benchmark} held</div><div class="v">${nn(a.bench_shares,4)} sh @ ${usd(a.bench_price)}</div></div>`;

  chart(d.equity_series);

  $("positions").innerHTML=table(d.open_positions,[
    {h:"Sym", f:r=>r.symbol+(r.is_earnings?' <span class="tag er">ER</span>':'')},
    {h:"Qty", n:1, f:r=>r.qty},
    {h:"Entry", n:1, f:r=>nn(r.entry_price)},
    {h:"Last", n:1, f:r=>nn(r.last)},
    {h:"Unreal $", n:1, f:r=>sgn(r.unrealized), cl:r=>cls(r.unrealized)},
    {h:"Unreal %", n:1, f:r=>r.unrealized_pct==null?"—":sgn(r.unrealized_pct)+"%", cl:r=>cls(r.unrealized_pct)},
    {h:"Prob", n:1, f:r=>r.prob_now==null?"—":nn(r.prob_now,3)},
    {h:"ΔProb", n:1, f:r=>r.prob_runup_pp==null?"—":sgn(r.prob_runup_pp,1)+"pp", cl:r=>cls(r.prob_runup_pp)},
    {h:"ΔStock", n:1, f:r=>r.stock_runup_pct==null?"—":sgn(r.stock_runup_pct,2)+"%", cl:r=>cls(r.stock_runup_pct)},
    {h:"Kelly", n:1, f:r=>r.kelly_pct==null?"—":nn(r.kelly_pct,1)+"%", cl:()=>"amber"},
    {h:"Comm", n:1, f:r=>r.commission==null?"—":usd(r.commission)},
    {h:"Slip", n:1, f:r=>r.slip_bps==null?"—":sgn(r.slip_bps,1)+"bp", cl:r=>r.slip_bps==null?"":(r.slip_bps>0?"neg":"pos")},
    {h:"Question", f:r=>r.question, cl:()=>"q"},
  ]);

  $("orders").innerHTML=table(d.recent_orders,[
    {h:"When", n:1, f:r=>dt(r.ts)},
    {h:"Sym", f:r=>r.symbol},
    {h:"Side", f:r=>r.action},
    {h:"Qty", n:1, f:r=>nn(r.qty, r.qty<10?4:0)},
    {h:"Kind", f:r=>`<span class="tag">${r.kind}</span>`},
    {h:"Fill", n:1, f:r=>nn(r.fill_price)},
    {h:"Comm", n:1, f:r=>r.commission==null?"—":usd(r.commission)},
    {h:"Slip", n:1, f:r=>r.slip_bps==null?"—":sgn(r.slip_bps,1)+"bp", cl:r=>r.slip_bps==null?"":(r.slip_bps>0?"neg":"pos")},
    {h:"Status", f:r=>{const ok=r.status==='Filled'||r.status==='dry_run'; const bad=['Cancelled','unqualified','ApiCancelled','Inactive'].includes(r.status);
      return `<span class="tag ${ok?'ok':(bad?'bad':'warn')}">${r.status}</span>`;}},
  ]);

  $("trades").innerHTML=table(d.recent_trades,[
    {h:"Sym", f:r=>r.symbol},
    {h:"PnL", n:1, f:r=>sgn(r.pnl), cl:r=>cls(r.pnl)},
    {h:"%", n:1, f:r=>r.pnl_pct==null?"—":sgn(r.pnl_pct)+"%", cl:r=>cls(r.pnl_pct)},
    {h:"Reason", f:r=>r.exit_reason||"—", cl:()=>"q"},
    {h:"When", n:1, f:r=>dday(r.exit_ts)},
  ]);

  $("upcoming").innerHTML=table(d.markets.upcoming,[
    {h:"Resolves", n:1, f:r=>dday(r.end_at)},
    {h:"Question", f:r=>r.question, cl:()=>"q"},
  ])+`<div class="mut" style="margin-top:9px; font-size:11px">${d.markets.tracked} tracked · ${d.markets.with_t0} with T0</div>`;

  const s=d.system, up=(s.disk_total_bytes&&s.disk_free_bytes)?(1-s.disk_free_bytes/s.disk_total_bytes)*100:null;
  $("system").innerHTML=
    `<div class="kv"><span class="k">DB size</span><span class="v">${gb(s.db_size_bytes)}</span></div>`+
    `<div class="kv"><span class="k">Disk free</span><span class="v">${gb(s.disk_free_bytes)}</span></div>`+
    `<div class="meter" style="margin-top:10px"><span style="width:${up==null?0:up.toFixed(0)}%;background:${up>90?'var(--red)':up>75?'var(--amber)':'var(--grn)'}"></span></div>`+
    `<div class="mut" style="font-size:11px;margin-top:6px">${up==null?'':up.toFixed(0)+'% used · '}as of ${dt(s.as_of)}</div>`;

  const c=d.api_cost;
  $("cost").innerHTML=
    `<div class="kv"><span class="k">Today</span><span class="v">${usd(c.today_usd)} <span class="mut" style="font-weight:400">${c.today_calls} calls</span></span></div>`+
    `<div class="kv"><span class="k">Total</span><span class="v">${usd(c.total_usd)} <span class="mut" style="font-weight:400">${c.total_calls} calls</span></span></div>`+
    `<div class="mut" style="font-size:10.5px;margin-top:8px">Estimate from configurable per-token rates (GEMINI_PRICE_*).</div>`;
}
setInterval(()=>{const e=$("clk"); if(e) e.textContent=new Date().toLocaleTimeString();},1000);
refresh(); setInterval(refresh, 20000);
</script>
</body></html>
"""


def main() -> None:
    port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    host = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
