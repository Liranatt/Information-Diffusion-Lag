"""Live, read-only web dashboard for the paper-trading pipeline.

Serves an auto-refreshing operational view (default 0.0.0.0:8080):
  * NAV curve: equity vs the passive-benchmark counterfactual
  * KPIs: equity, realized PnL, win-rate, open positions, cash, excess vs passive
  * tables: open positions (with unrealized), recent orders, recent closed trades
  * tracked markets + next resolutions
  * system: DB size + host disk free (live_system_metrics)
  * spend: Gemini cost today / total (live_api_costs)

It only ever reads the DB -- it never connects to IB and never trades.

    python -m interactive_brokers.dashboard      # or via docker compose (dashboard service)
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from database.backtesting.schema import SCHEMA

from .database import LiveStore

_STATE: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    _STATE["store"] = await LiveStore.create()
    try:
        yield
    finally:
        await _STATE["store"].close()


app = FastAPI(lifespan=lifespan, title="CEM live paper-trading dashboard")


def _iso(ts) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.astimezone(timezone.utc).isoformat()
    return str(ts)


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
            f"""SELECT ts, symbol, action, qty, kind, fill_price, status
                FROM {SCHEMA}.live_orders ORDER BY ts DESC LIMIT 30""")
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
                WHERE status='tracking' ORDER BY end_at LIMIT 12""")
        sys_latest = await conn.fetchrow(
            f"SELECT * FROM {SCHEMA}.live_system_metrics ORDER BY ts DESC LIMIT 1")
        sys_series = await conn.fetch(
            f"""SELECT ts, db_size_bytes, disk_free_bytes FROM {SCHEMA}.live_system_metrics
                ORDER BY ts DESC LIMIT 300""")
        cost = await conn.fetchrow(
            f"""SELECT COALESCE(SUM(est_cost_usd), 0) AS total,
                       COALESCE(SUM(calls), 0) AS calls,
                       COALESCE(SUM(est_cost_usd) FILTER (WHERE ts >= date_trunc('day', now())), 0) AS today,
                       COALESCE(SUM(calls) FILTER (WHERE ts >= date_trunc('day', now())), 0) AS today_calls
                FROM {SCHEMA}.live_api_costs""")
        cost_series = await conn.fetch(
            f"""SELECT date_trunc('day', ts) AS d, SUM(est_cost_usd) AS usd
                FROM {SCHEMA}.live_api_costs GROUP BY 1 ORDER BY 1 DESC LIMIT 30""")

    equity = float(eq["equity"]) if eq else None
    passive = float(eq["passive_equity"]) if eq and eq["passive_equity"] is not None else None
    excess = (equity - passive) if (equity is not None and passive is not None) else None

    positions = []
    for p in open_pos:
        last = await store.latest_close(p["symbol"]) or float(p["entry_price"])
        entry = float(p["entry_price"])
        unreal = int(p["qty"]) * (last - entry)
        positions.append({
            "symbol": p["symbol"], "qty": int(p["qty"]),
            "entry_price": round(entry, 2), "last": round(float(last), 2),
            "unrealized": round(unreal, 2),
            "unrealized_pct": round((last / entry - 1.0) * 100.0, 2) if entry else None,
            "entry_prob": round(float(p["entry_prob"]), 3) if p["entry_prob"] is not None else None,
            "is_earnings": bool(p["is_earnings"]),
            "question": p["question"][:80], "entry_ts": _iso(p["entry_ts"]),
        })

    closed = int(perf["closed"] or 0)
    wins = int(perf["wins"] or 0)
    return {
        "generated_at": _iso(datetime.now(timezone.utc)),
        "portfolio": {
            "equity": round(equity, 2) if equity is not None else None,
            "cash": round(float(eq["cash"]), 2) if eq else None,
            "benchmark_shares": round(float(eq["benchmark_shares"]), 4) if eq else None,
            "benchmark_price": round(float(eq["benchmark_price"]), 2) if eq and eq["benchmark_price"] else None,
            "open_positions": int(eq["open_positions"]) if eq else len(positions),
            "passive_equity": round(passive, 2) if passive is not None else None,
            "excess": round(excess, 2) if excess is not None else None,
            "excess_pct": round(excess / passive * 100.0, 2) if excess is not None and passive else None,
            "as_of": _iso(eq["ts"]) if eq else None,
        },
        "performance": {
            "realized_pnl": round(float(perf["realized_pnl"] or 0.0), 2),
            "closed_trades": closed, "wins": wins,
            "win_rate": round(wins / closed * 100.0, 1) if closed else None,
        },
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
            "upcoming": [{"end_at": _iso(r["end_at"]), "question": r["question"][:80]} for r in upcoming],
        },
        "system": {
            "db_size_bytes": int(sys_latest["db_size_bytes"]) if sys_latest and sys_latest["db_size_bytes"] else None,
            "disk_free_bytes": int(sys_latest["disk_free_bytes"]) if sys_latest and sys_latest["disk_free_bytes"] else None,
            "disk_total_bytes": int(sys_latest["disk_total_bytes"]) if sys_latest and sys_latest["disk_total_bytes"] else None,
            "as_of": _iso(sys_latest["ts"]) if sys_latest else None,
            "db_series": [
                {"ts": _iso(r["ts"]), "db_gb": round(int(r["db_size_bytes"]) / 1e9, 3) if r["db_size_bytes"] else None,
                 "free_gb": round(int(r["disk_free_bytes"]) / 1e9, 2) if r["disk_free_bytes"] else None}
                for r in reversed(sys_series)
            ],
        },
        "api_cost": {
            "today_usd": round(float(cost["today"] or 0.0), 4),
            "total_usd": round(float(cost["total"] or 0.0), 4),
            "today_calls": int(cost["today_calls"] or 0),
            "total_calls": int(cost["calls"] or 0),
            "daily": [{"day": _iso(r["d"]), "usd": round(float(r["usd"] or 0.0), 4)} for r in reversed(cost_series)],
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


PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CEM live paper trading</title>
<style>
  :root { --bg:#0e1117; --panel:#161b22; --line:#30363d; --fg:#e6edf3; --mut:#8b949e;
          --grn:#3fb950; --red:#f85149; --acc:#58a6ff; --amber:#d29922; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
  header { display:flex; align-items:baseline; gap:14px; padding:14px 20px; border-bottom:1px solid var(--line); }
  header h1 { font-size:16px; margin:0; font-weight:600; }
  header .sub { color:var(--mut); font-size:12px; }
  main { padding:16px 20px; max-width:1200px; margin:0 auto; }
  .kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:16px; }
  .kpi { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
  .kpi .label { color:var(--mut); font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
  .kpi .val { font-size:22px; font-weight:650; margin-top:4px; }
  .kpi .val.sm { font-size:16px; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  @media (max-width:820px){ .grid { grid-template-columns:1fr; } }
  .panel { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px; margin-bottom:16px; }
  .panel h2 { font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--mut); margin:0 0 10px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:right; padding:5px 8px; border-bottom:1px solid var(--line); white-space:nowrap; }
  th:first-child,td:first-child { text-align:left; }
  th { color:var(--mut); font-weight:500; font-size:11px; text-transform:uppercase; }
  td.q { text-align:left; color:var(--mut); max-width:320px; overflow:hidden; text-overflow:ellipsis; }
  .pos { color:var(--grn); } .neg { color:var(--red); } .mut { color:var(--mut); }
  .pill { font-size:10px; padding:1px 6px; border-radius:20px; border:1px solid var(--line); color:var(--mut); }
  svg { width:100%; height:220px; display:block; }
  .legend { display:flex; gap:16px; font-size:12px; color:var(--mut); margin-top:6px; }
  .legend b { font-weight:600; }
  .dot { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:5px; vertical-align:middle; }
  .empty { color:var(--mut); font-style:italic; padding:8px 0; }
  .barrow { display:flex; align-items:center; gap:8px; margin:6px 0; }
  .bar { flex:1; height:8px; background:#21262d; border-radius:6px; overflow:hidden; }
  .bar > span { display:block; height:100%; background:var(--acc); }
</style></head>
<body>
<header>
  <h1>CEM · live paper trading</h1>
  <span class="sub" id="asof">loading…</span>
  <span class="sub" style="margin-left:auto" id="clock"></span>
</header>
<main>
  <div class="kpis" id="kpis"></div>
  <div class="panel">
    <h2>Equity vs passive benchmark</h2>
    <div id="chart"></div>
    <div class="legend">
      <span><span class="dot" style="background:var(--acc)"></span>Strategy equity</span>
      <span><span class="dot" style="background:var(--mut)"></span>Passive (hold benchmark)</span>
    </div>
  </div>
  <div class="grid">
    <div class="panel"><h2>Open positions</h2><div id="positions"></div></div>
    <div class="panel"><h2>Recent closed trades</h2><div id="trades"></div></div>
  </div>
  <div class="grid">
    <div class="panel"><h2>Recent orders</h2><div id="orders"></div></div>
    <div class="panel"><h2>Next resolutions</h2><div id="upcoming"></div></div>
  </div>
  <div class="grid">
    <div class="panel"><h2>System · DB &amp; disk</h2><div id="system"></div></div>
    <div class="panel"><h2>Gemini spend</h2><div id="cost"></div></div>
  </div>
</main>
<script>
const $ = id => document.getElementById(id);
const fmt = (n, d=2) => n==null ? "—" : Number(n).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});
const usd = n => n==null ? "—" : "$"+fmt(n,2);
const gb = b => b==null ? "—" : (b/1e9).toFixed(2)+" GB";
const cls = n => n==null ? "" : (n>0?"pos":(n<0?"neg":""));
const sign = n => n==null ? "—" : (n>0?"+":"")+fmt(n,2);
const dt = s => s ? new Date(s).toLocaleString() : "—";
const dshort = s => s ? new Date(s).toLocaleDateString(undefined,{month:"short",day:"numeric"}) : "";

function kpi(label, val, klass="") {
  return `<div class="kpi"><div class="label">${label}</div><div class="val ${klass}">${val}</div></div>`;
}

function chart(series) {
  const el = $("chart");
  const pts = series.filter(p => p.equity != null);
  if (pts.length < 2) { el.innerHTML = '<div class="empty">Not enough NAV snapshots yet — the curve appears after a few ticks.</div>'; return; }
  const W=1000, H=220, pad=34;
  const eq = pts.map(p=>p.equity), pv = pts.map(p=>p.passive==null?p.equity:p.passive);
  const lo = Math.min(...eq, ...pv), hi = Math.max(...eq, ...pv);
  const span = (hi-lo)||1;
  const x = i => pad + i*(W-2*pad)/(pts.length-1);
  const y = v => H-pad - (v-lo)/span*(H-2*pad);
  const path = arr => arr.map((v,i)=>(i?"L":"M")+x(i).toFixed(1)+" "+y(v).toFixed(1)).join(" ");
  const grid = [0,0.25,0.5,0.75,1].map(f=>{const yy=(pad+f*(H-2*pad)).toFixed(1); const val=(hi-f*span);
    return `<line x1="${pad}" y1="${yy}" x2="${W-pad}" y2="${yy}" stroke="var(--line)" stroke-width="0.5"/>`+
           `<text x="4" y="${(+yy+3)}" fill="var(--mut)" font-size="10">${(val/1000).toFixed(1)}k</text>`;}).join("");
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${grid}
    <path d="${path(pv)}" fill="none" stroke="var(--mut)" stroke-width="1.4" stroke-dasharray="4 3"/>
    <path d="${path(eq)}" fill="none" stroke="var(--acc)" stroke-width="2"/>
    <text x="${pad}" y="${H-6}" fill="var(--mut)" font-size="10">${dshort(pts[0].ts)}</text>
    <text x="${W-pad}" y="${H-6}" fill="var(--mut)" font-size="10" text-anchor="end">${dshort(pts[pts.length-1].ts)}</text>
  </svg>`;
}

function table(rows, cols) {
  if (!rows.length) return '<div class="empty">Nothing yet.</div>';
  const head = "<tr>"+cols.map(c=>`<th>${c.h}</th>`).join("")+"</tr>";
  const body = rows.map(r=>"<tr>"+cols.map(c=>`<td class="${c.cl?c.cl(r):''}">${c.f(r)}</td>`).join("")+"</tr>").join("");
  return `<table>${head}${body}</table>`;
}

async function refresh() {
  let d;
  try { d = await (await fetch("/api/metrics",{cache:"no-store"})).json(); }
  catch(e) { $("asof").textContent = "connection error — retrying…"; return; }
  const p=d.portfolio, perf=d.performance;
  $("asof").textContent = "NAV as of " + dt(p.as_of);
  $("kpis").innerHTML =
    kpi("Equity", usd(p.equity)) +
    kpi("Excess vs passive", sign(p.excess), cls(p.excess)) +
    kpi("Realized PnL", sign(perf.realized_pnl), cls(perf.realized_pnl)) +
    kpi("Win rate", perf.win_rate==null?"—":perf.win_rate+"%", "sm") +
    kpi("Open / closed", `${p.open_positions} / ${perf.closed_trades}`, "sm") +
    kpi("Cash", usd(p.cash), "sm") +
    kpi("Gemini spend (total)", usd(d.api_cost.total_usd), "sm");
  chart(d.equity_series);

  $("positions").innerHTML = table(d.open_positions, [
    {h:"Sym", f:r=>r.symbol+(r.is_earnings?' <span class="pill">ER</span>':'')},
    {h:"Qty", f:r=>r.qty},
    {h:"Entry", f:r=>fmt(r.entry_price)},
    {h:"Last", f:r=>fmt(r.last)},
    {h:"Unreal", f:r=>sign(r.unrealized), cl:r=>cls(r.unrealized)},
    {h:"%", f:r=>r.unrealized_pct==null?"—":sign(r.unrealized_pct)+"%", cl:r=>cls(r.unrealized_pct)},
    {h:"Prob", f:r=>r.entry_prob==null?"—":r.entry_prob},
    {h:"Question", f:r=>r.question, cl:()=>"q"},
  ]);
  $("trades").innerHTML = table(d.recent_trades, [
    {h:"Sym", f:r=>r.symbol},
    {h:"PnL", f:r=>sign(r.pnl), cl:r=>cls(r.pnl)},
    {h:"%", f:r=>r.pnl_pct==null?"—":sign(r.pnl_pct)+"%", cl:r=>cls(r.pnl_pct)},
    {h:"Reason", f:r=>r.exit_reason||"—", cl:()=>"q"},
    {h:"When", f:r=>dshort(r.exit_ts)},
  ]);
  $("orders").innerHTML = table(d.recent_orders, [
    {h:"When", f:r=>dt(r.ts)},
    {h:"Sym", f:r=>r.symbol},
    {h:"Side", f:r=>r.action},
    {h:"Qty", f:r=>fmt(r.qty,r.qty<10?4:0)},
    {h:"Kind", f:r=>`<span class="pill">${r.kind}</span>`},
    {h:"Fill", f:r=>fmt(r.fill_price)},
    {h:"Status", f:r=>r.status, cl:r=>r.status==='Filled'||r.status==='dry_run'?'':'neg'},
  ]);
  $("upcoming").innerHTML = table(d.markets.upcoming, [
    {h:"Resolves", f:r=>dshort(r.end_at)},
    {h:"Question", f:r=>r.question, cl:()=>"q"},
  ]) + `<div class="mut" style="margin-top:8px;font-size:12px">${d.markets.tracked} tracked · ${d.markets.with_t0} with T0</div>`;

  const s=d.system;
  const usedPct = (s.disk_total_bytes&&s.disk_free_bytes)?(1-s.disk_free_bytes/s.disk_total_bytes)*100:null;
  $("system").innerHTML =
    `<div class="barrow"><span style="width:70px;color:var(--mut)">DB size</span><b>${gb(s.db_size_bytes)}</b></div>`+
    `<div class="barrow"><span style="width:70px;color:var(--mut)">Disk</span>`+
      `<div class="bar"><span style="width:${usedPct==null?0:usedPct.toFixed(0)}%;background:${usedPct>90?'var(--red)':usedPct>75?'var(--amber)':'var(--acc)'}"></span></div>`+
      `<span>${gb(s.disk_free_bytes)} free</span></div>`+
    `<div class="mut" style="font-size:12px;margin-top:6px">as of ${dt(s.as_of)}${usedPct!=null?` · ${usedPct.toFixed(0)}% used`:''}</div>`;

  const c=d.api_cost;
  $("cost").innerHTML =
    kpi_inline("Today", usd(c.today_usd)+` <span class="mut">(${c.today_calls} calls)</span>`)+
    kpi_inline("Total", usd(c.total_usd)+` <span class="mut">(${c.total_calls} calls)</span>`)+
    `<div class="mut" style="font-size:11px;margin-top:8px">Cost is an estimate from configurable per-token rates (GEMINI_PRICE_*).</div>`;
}
function kpi_inline(l,v){ return `<div class="barrow"><span style="width:60px;color:var(--mut)">${l}</span><b>${v}</b></div>`; }

$("clock") && setInterval(()=>{ $("clock").textContent = new Date().toLocaleTimeString(); }, 1000);
refresh();
setInterval(refresh, 20000);
</script>
</body></html>
"""


def main() -> None:
    port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    host = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
