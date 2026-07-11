const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const LOCALE = "en-US";
let currentView = "overview";
let showAllOrders = false;
let expandedPositions = new Set();
let backtestLoaded = false;

const C = {
  brand:"#B07D2A", brand2:"#8A5E10", up:"#1A6B45", down:"#A8192E",
  sky:"#1566A0", warn:"#96620A", faint:"#9A8B72", grid:"#E3D9C4"
};

function esc(v){
  return String(v == null ? "" : v).replace(/[&<>"']/g, ch => (
    {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]
  ));
}
const nn = (n,d=2) => n == null || Number.isNaN(Number(n)) ? "-" :
  Number(n).toLocaleString(LOCALE,{minimumFractionDigits:d,maximumFractionDigits:d});
const usd = n => n == null ? "-" : "$" + nn(n,2);
const usd0 = n => n == null ? "-" : "$" + nn(n,0);
const susd = n => n == null ? "-" : (n > 0 ? "+" : n < 0 ? "-" : "") + "$" + nn(Math.abs(n),2);
const gb = b => b == null ? "-" : (b/1e9).toFixed(2) + " GB";
const cl = n => n == null ? "" : (n > 0 ? "up" : n < 0 ? "down" : "");
const sg = (n,d=2) => n == null ? "-" : (n > 0 ? "+" : "") + nn(n,d);
const pct = (n,d=1) => n == null ? "-" : sg(n,d) + "%";
const cadence = s => !s ? "-" : (s >= 3600 ? (s/3600).toFixed(s%3600 ? 1 : 0) + "h" : Math.round(s/60) + "m");
const dt = s => s ? new Date(s).toLocaleString(LOCALE,{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "-";
const dday = s => s ? new Date(s).toLocaleDateString(LOCALE,{month:"short",day:"numeric"}) : "";
const marketText = m => !m ? "Market status unknown" : m.is_open ? `Open, closes in ${Math.max(0,Math.round(m.seconds_to_close/60))}m` : "Closed";

function icon(name){
  const paths = {
    overview:'<path d="M4 13h6V4H4z"/><path d="M14 20h6V4h-6z"/><path d="M4 20h6v-3H4z"/>',
    portfolio:'<path d="M3 7h18"/><path d="M6 7V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2"/><path d="M5 7l1 13h12l1-13"/>',
    strategy:'<path d="M4 19V5"/><path d="M4 19h17"/><path d="M7 15l4-4 3 3 5-7"/>',
    diagnostics:'<path d="M12 3v4"/><path d="M12 17v4"/><path d="M3 12h4"/><path d="M17 12h4"/><path d="M7.8 7.8l2.8 2.8"/><path d="M13.4 13.4l2.8 2.8"/><circle cx="12" cy="12" r="3"/>',
    learn:'<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5z"/>'
  };
  return `<span class="navIcon"><svg viewBox="0 0 24 24" aria-hidden="true">${paths[name]}</svg></span>`;
}

function renderShell(){
  $("#app").innerHTML = `
  <div class="app">
    <aside class="side">
      <div class="logo"><div class="logoMark">CEM</div><div class="logoText">CEM<small>paper trading</small></div></div>
      <nav class="nav" id="nav" aria-label="Dashboard sections">
        <button data-v="overview" class="on">${icon("overview")}Overview</button>
        <button data-v="portfolio">${icon("portfolio")}Portfolio</button>
        <button data-v="strategy">${icon("strategy")}Strategy</button>
        <button data-v="diagnostics">${icon("diagnostics")}Diagnostics</button>
        <button data-v="learn">${icon("learn")}Learn</button>
      </nav>
      <div class="grow"></div>
      <div class="statuscard">
        <div class="liveLine"><span><i class="dot"></i>LIVE HOURLY</span><span id="sideHealth">OK</span></div>
        <div class="eq" id="sideEq">-</div>
        <div class="sub" id="sideSub">connecting</div>
      </div>
      <div class="deployBox">
        <div><b>Deployment</b></div>
        <div id="dep_sha">SHA: -</div>
        <div id="dep_branch">Branch: -</div>
        <div id="dep_dash">Dash uptime: -</div>
        <div id="dep_trader">Trader heartbeat: -</div>
        <div id="dep_health"><b>OK</b></div>
      </div>
    </aside>
    <main class="main">
      <div class="top">
        <div><h1 id="ttl">Overview</h1><div class="crumb" id="crumb">Live status, risk, and capital</div></div>
        <div class="topRight">
          <span class="pill" id="marketPill"><i class="dot"></i><span id="marketLabel">CONNECTING</span></span>
          <div class="meta" id="meta">connecting...</div>
        </div>
      </div>

      <section class="view on" id="overview">
        <div id="alerts"></div>
        <div class="row commandStrip" id="commandStrip"></div>
        <div class="row c2">
          <div class="card"><h3>NAV, strategy vs passive benchmark</h3><div class="chartWrap"><div id="chart"></div><div class="chartTip" id="chartTip"></div></div>
            <div class="legrow"><span><i style="border-color:var(--brand)"></i>Strategy equity</span><span><i style="border-color:var(--faint);border-top-style:dashed"></i>Passive benchmark</span></div></div>
          <div class="card"><h3>Capital allocation</h3><div class="alloc"><div class="donut"><div id="donut"></div><div class="ctr"><b id="invpct">-</b><span>invested</span></div></div><div class="leg" id="alloclegend"></div></div></div>
        </div>
        <div class="sectionTitle">Risk & concentration</div>
        <div class="row c4" id="risk"></div>
        <div class="sectionTitle">Open positions requiring attention</div>
        <div class="card"><div class="tw" id="attention"></div></div>
      </section>

      <section class="view" id="portfolio">
        <div id="palerts"></div>
        <div class="card" style="margin-bottom:16px"><h3>Open positions, runup, Kelly & exits</h3>
          <div class="hint">T0 is the first tracked baseline. Entry diagnostics compare probability and stock movement before the live policy fired.</div>
          <div class="tw" id="positions"></div></div>
        <div class="row c2b">
          <div class="card"><h3>Recent orders</h3><div class="tw" id="orders"></div></div>
          <div class="card"><h3>Recent closed trades</h3><div class="tw" id="trades"></div></div>
        </div>
        <div class="card"><h3>Question watchlist, next resolutions</h3><div class="tw" id="upcoming"></div></div>
      </section>

      <section class="view" id="strategy">
        <div class="row c4" id="btsummary"></div>
        <div class="card" style="margin-bottom:16px"><h3>Statistics, out-of-sample backtest vs live</h3><div class="tw" id="btstats"></div><div class="mut" id="btstatsnote" style="font-size:11px;margin-top:8px"></div></div>
        <div class="card" style="margin-bottom:16px"><h3 id="btttl">Walk-forward out-of-sample folds</h3><div id="btchart"></div><div class="legrow"><span><i style="border-color:var(--brand)"></i>Strategy return</span><span><i style="border-color:var(--faint)"></i>Benchmark return</span></div></div>
        <div class="card" style="margin-bottom:16px"><h3>Overall out-of-sample NAV curve</h3><div class="chartWrap"><div id="bt_equity_chart"></div><div class="chartTip" id="btTip"></div></div></div>
        <div class="row c2b"><div class="card"><h3>Per-fold detail</h3><div class="tw" id="bttable"></div></div><div class="card"><h3>Live policy parameters</h3><div class="params" id="btparams"></div></div></div>
      </section>

      <section class="view" id="diagnostics">
        <div class="row c4" id="attribution"></div>
        <div class="row c4" id="diagCards"></div>
        <div class="row c2b"><div class="card"><h3>Execution health</h3><div id="execHealth"></div></div><div class="card"><h3>Runtime cadence</h3><div id="runtime"></div></div></div>
      </section>

      <section class="view learn" id="learn">
        <div class="row c2b">
          <div class="card"><h3>Strategy execution</h3><p>Runs <span class="term">hourly</span> against an IBKR paper account, trading the latest walk-forward policy. Capital is capped to cash plus liquidatable benchmark inventory, and idle cash rotates back into the benchmark index.</p></div>
          <div class="card"><h3>CEM, Cross-Entropy Method</h3><p>The rules are hard IF/THEN thresholds, so CEM samples policy vectors, simulates each full portfolio, keeps elite performers, and refits the sampling distribution toward them.</p><p>The objective is friction-aware: <span class="formula">S = Sharpe - 0.30 * MaxDD - 2.0 * FFR</span></p></div>
        </div>
        <div class="card" style="margin-bottom:16px"><h3>The experiment tiers, T1 to T4</h3><div class="tiers">
          ${tier("T1","Friction penalty","Rejects policies that only look good before realized costs.")}
          ${tier("T2","Walk-forward windows","Fits on history up to a cutoff and tests on the next unseen window.")}
          ${tier("T3","Half-Kelly sizing","Scales position size with realized win-rate and payoff ratio, clamped for smoother live behavior.")}
          ${tier("T4","Event priority","Prioritizes event-driven positions over passive benchmark inventory when deploying capital.")}
        </div><p style="margin-top:13px" class="mut">The live config is <b class="term" id="expname">-</b>.</p></div>
        <div class="card"><h3>Walk-forward & Kelly, one line each</h3><p><span class="term">Walk-forward</span>: never test on data used for training.</p><p><span class="term">Half-Kelly</span>: growth-optimal sizing, cut in half and re-estimated from realized trades.</p><p><span class="term">The optimized parameters</span>: entry thresholds, hold window, ATR trail, profit lock, theta exit, surge/runup gates, size, and max concurrency.</p></div>
      </section>
    </main>
  </div>`;

  const titles = {
    overview:["Overview","Live status, risk, and capital"],
    portfolio:["Portfolio","Positions, orders, trades, and watchlist"],
    strategy:["Strategy","Walk-forward out-of-sample performance"],
    diagnostics:["Diagnostics","Attribution, execution, runtime, and system health"],
    learn:["Learn","How the system works"]
  };
  $$("#nav button").forEach(b => b.onclick = () => {
    currentView = b.dataset.v;
    $$("#nav button").forEach(x => x.classList.toggle("on", x === b));
    $$(".view").forEach(v => v.classList.toggle("on", v.id === currentView));
    $("#ttl").textContent = titles[currentView][0];
    $("#crumb").textContent = titles[currentView][1];
    if(currentView === "strategy") loadBacktest();
  });
}

function tier(b,t,x){return `<div class="tier"><div class="b">${b}</div><div><div class="t">${esc(t)}</div><div class="x">${esc(x)}</div></div></div>`;}
function deltaHtml(v, money=false, suffix=""){
  if(v == null) return `<span class="delta flat">-</span>`;
  const klass = v > 0 ? "up" : v < 0 ? "down" : "flat";
  const text = money ? susd(v) : sg(v, v === Math.round(v) ? 0 : 2) + suffix;
  return `<span class="delta ${klass}">${text}</span>`;
}
function kpi(label,value,delta,chip,cc="neu"){
  return `<div class="card stat"><div><div class="l">${esc(label)}</div><div class="v">${value}</div></div><div class="subline">${delta || ""}${chip ? `<span class="chip ${cc}">${esc(chip)}</span>` : ""}</div></div>`;
}
function healthCard(d){
  const c = d.alerts.critical.length, w = d.alerts.warning.length;
  const status = c ? "Action needed" : w ? "Check warnings" : "Healthy";
  const detail = c ? `${c} critical, ${w} warnings` : w ? `${w} warnings` : "No critical alerts";
  return `<div class="card stat healthCard"><h3>System status</h3><div class="v">${status}</div><div class="subline">${detail}</div></div>`;
}
function renderAlerts(target, alerts, includeInfo=false){
  const items = [
    ...alerts.critical.map(a => ["critical","!",a]),
    ...alerts.warning.map(a => ["warning","!",a]),
    ...(includeInfo ? alerts.info.map(a => ["info","i",a]) : [])
  ];
  if(!items.length){ target.innerHTML = ""; return; }
  target.innerHTML = `<div class="alertStack">` + items.map(([cls,ic,a]) =>
    `<div class="alert ${cls}"><div class="alertIcon">${ic}</div><div><div class="alertTitle">${esc(a.title)}</div><div class="alertDetail">${esc(a.detail)}</div></div></div>`
  ).join("") + `</div>`;
}

function smooth(pts){
  if(pts.length < 2) return "";
  let d = `M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
  for(let i=0;i<pts.length-1;i++){
    const a=pts[i], b=pts[i+1], mx=(a[0]+b[0])/2;
    d += ` C ${mx.toFixed(1)} ${a[1].toFixed(1)}, ${mx.toFixed(1)} ${b[1].toFixed(1)}, ${b[0].toFixed(1)} ${b[1].toFixed(1)}`;
  }
  return d;
}
function areaChart(el, series, tipId="chartTip"){
  const pts = (series || []).filter(p => p.equity != null);
  if(pts.length < 2){ el.innerHTML = `<div class="empty">Not enough NAV snapshots yet.</div>`; return; }
  const W=1000,H=270,pl=56,pr=58,pt=18,pb=32;
  const eq=pts.map(p=>p.equity), pv=pts.map(p=>p.passive == null ? p.equity : p.passive);
  const lo=Math.min(...eq,...pv), hi=Math.max(...eq,...pv), sp=(hi-lo)||1;
  const X=i=>pl+i*(W-pl-pr)/(pts.length-1), Y=v=>H-pb-(v-lo)/sp*(H-pt-pb);
  const pe=pts.map((p,i)=>[X(i),Y(p.equity)]), pp=pts.map((p,i)=>[X(i),Y(p.passive == null ? p.equity : p.passive)]);
  const area=smooth(pe)+` L ${X(pts.length-1)} ${H-pb} L ${pl} ${H-pb} Z`;
  const grid=[0,.25,.5,.75,1].map(f=>{const y=pt+f*(H-pt-pb), v=hi-f*sp;
    return `<line x1="${pl}" y1="${y.toFixed(1)}" x2="${W-pr}" y2="${y.toFixed(1)}" stroke="${C.grid}"/><text x="${pl-8}" y="${y+4}" fill="${C.faint}" font-size="10.5" text-anchor="end" font-family="var(--num)">${(v/1000).toFixed(1)}k</text>`;
  }).join("");
  const last=pts[pts.length-1], lastY=Y(last.equity);
  el.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Strategy equity versus passive benchmark chart">
    <defs><linearGradient id="navArea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${C.brand}" stop-opacity=".18"/><stop offset="1" stop-color="${C.brand}" stop-opacity="0"/></linearGradient></defs>
    ${grid}<path d="${area}" fill="url(#navArea)"/><path d="${smooth(pp)}" fill="none" stroke="${C.faint}" stroke-width="1.8" stroke-dasharray="5 4"/>
    <path d="${smooth(pe)}" fill="none" stroke="${C.brand}" stroke-width="2.6" stroke-linecap="round"/>
    <circle cx="${X(pts.length-1)}" cy="${lastY}" r="4" fill="${C.brand}"/><text x="${W-pr+8}" y="${lastY+4}" fill="${C.brand}" font-size="11" font-family="var(--num)">${usd0(last.equity)}</text>
    <text x="${pl}" y="${H-8}" fill="${C.faint}" font-size="10.5" font-family="var(--num)">${dday(pts[0].ts)}</text><text x="${W-pr}" y="${H-8}" fill="${C.faint}" font-size="10.5" text-anchor="end" font-family="var(--num)">${dday(pts[pts.length-1].ts)}</text>
  </svg>`;
  const svg = el.querySelector("svg"), tip = document.getElementById(tipId);
  svg.onmousemove = (ev) => {
    const r = svg.getBoundingClientRect();
    const rel = Math.min(1, Math.max(0, (ev.clientX-r.left) / r.width));
    const i = Math.min(pts.length-1, Math.max(0, Math.round(rel*(pts.length-1))));
    const p = pts[i], passive = p.passive == null ? null : p.passive;
    tip.style.display = "block"; tip.style.left = (ev.clientX-r.left+14) + "px"; tip.style.top = (ev.clientY-r.top+10) + "px";
    tip.innerHTML = `${dt(p.ts)}<br>Strategy ${usd(p.equity)}${passive == null ? "" : `<br>Passive ${usd(passive)}<br>Excess ${susd(p.equity-passive)}`}`;
  };
  svg.onmouseleave = () => { tip.style.display = "none"; };
}
function donut(el,segs){
  const size=148,st=18,r=(size-st)/2,c=2*Math.PI*r,cx=size/2; let off=0;
  const tot=segs.reduce((a,s)=>a+Math.max(0,s.v),0)||1;
  const arcs=segs.map(s=>{const f=Math.max(0,s.v)/tot; const out=`<circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="${s.c}" stroke-width="${st}" stroke-dasharray="${(f*c).toFixed(1)} ${c.toFixed(1)}" stroke-dashoffset="${(-off*c).toFixed(1)}" transform="rotate(-90 ${cx} ${cx})"/>`; off+=f; return out;}).join("");
  el.innerHTML = `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" role="img" aria-label="Capital allocation donut"><circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="${C.grid}" stroke-width="${st}"/>${arcs}</svg>`;
}
function barChart(el, folds){
  if(!folds || !folds.length){ el.innerHTML = `<div class="empty">No backtest folds found.</div>`; return; }
  const W=1000,H=270,pl=48,pr=18,pt=18,pb=44,gw=(W-pl-pr)/folds.length;
  const vals=folds.flatMap(f=>[f.return_pct,f.benchmark_pct]); const hi=Math.max(...vals,1), lo=Math.min(...vals,0), sp=(hi-lo)||1;
  const Y=v=>H-pb-(v-lo)/sp*(H-pt-pb), y0=Y(0);
  const grid=[0,.5,1].map(f=>{const v=hi-f*(hi-lo),y=Y(v);return `<line x1="${pl}" y1="${y}" x2="${W-pr}" y2="${y}" stroke="${C.grid}"/><text x="${pl-6}" y="${y+4}" fill="${C.faint}" font-size="10.5" text-anchor="end" font-family="var(--num)">${v.toFixed(0)}%</text>`;}).join("");
  const bars=folds.map((f,i)=>{const bx=pl+i*gw,bw=gw*.28,gap=gw*.12;
    return `<rect x="${bx+gw/2-bw-gap/2}" y="${Math.min(Y(f.return_pct),y0)}" width="${bw}" height="${Math.abs(Y(f.return_pct)-y0)}" rx="3" fill="${C.brand}"/><rect x="${bx+gw/2+gap/2}" y="${Math.min(Y(f.benchmark_pct),y0)}" width="${bw}" height="${Math.abs(Y(f.benchmark_pct)-y0)}" rx="3" fill="${C.faint}"/><text x="${bx+gw/2}" y="${H-25}" fill="${C.faint}" font-size="10.5" text-anchor="middle" font-family="var(--num)">F${f.fold}</text><text x="${bx+gw/2}" y="${H-10}" fill="${f.excess_pct>=0?C.up:C.down}" font-size="10.5" text-anchor="middle" font-family="var(--num)">${sg(f.excess_pct,1)}</text>`;
  }).join("");
  el.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Backtest fold returns">${grid}<line x1="${pl}" y1="${y0}" x2="${W-pr}" y2="${y0}" stroke="${C.faint}"/>${bars}</svg>`;
}
function table(rows, cols, extra){
  if(!rows || !rows.length) return `<div class="empty">Nothing yet.</div>`;
  return `<table><thead><tr>${cols.map(c=>`<th>${esc(c.h)}</th>`).join("")}</tr></thead><tbody>` +
    rows.map(r => {
      const base = `<tr>${cols.map(c => `<td class="${c.n ? "n " : ""}${c.cl ? c.cl(r) : ""}">${c.f(r)}</td>`).join("")}</tr>`;
      return base + (extra ? extra(r) : "");
    }).join("") + `</tbody></table>`;
}
function stateTag(v){
  const map = {fires_now:["ok","Fires"],near_entry:["warn","Near"],overheated:["bad","Hot"],weak_mapping:["warn","Weak"],stale:["bad","Stale"],watching:["neu","Watch"]};
  const m = map[v] || ["neu",v || "-"];
  return `<span class="tag ${m[0]}">${esc(m[1])}</span>`;
}
function riskTag(p){
  const map = {near_stop:["bad","Near stop"],near_theta:["bad","Theta"],near_resolution:["warn","Resolution"],aging:["warn","Aging"],normal:["ok","Normal"]};
  const m = map[p.exit_risk] || map.normal;
  return `<span class="tag ${m[0]}">${m[1]}</span>`;
}

// ── Per-position stock × probability chart (since T0) ────────────────────
const positionHistoryCache = {}; // { posId: {data, expires} }

function positionHistoryChart(el, data) {
  const stock = (data.stock || []).filter(p => p.close != null);
  const prob  = (data.prob  || []).filter(p => p.p    != null);
  if (stock.length < 2 && prob.length < 2) {
    el.innerHTML = '<div class="empty">Not enough history yet.</div>';
    return;
  }
  const W=1000,H=200,pl=56,pr=56,pt=14,pb=28;

  // Shared time axis
  const allTs = [
    ...stock.map(p => new Date(p.ts).getTime()),
    ...prob.map(p  => new Date(p.ts).getTime()),
  ];
  const tMin = Math.min(...allTs), tMax = Math.max(...allTs);
  const tSp  = tMax - tMin || 1;
  const TX   = t  => pl + (new Date(t).getTime() - tMin) / tSp * (W - pl - pr);

  // Stock Y (left)
  const sVals = stock.map(p => p.close);
  const sLo = Math.min(...sVals), sHi = Math.max(...sVals);
  const sSp = (sHi - sLo) || Math.max(1, sLo * 0.01);
  const SY  = v => H - pb - (v - sLo) / sSp * (H - pt - pb);

  // Prob Y (right, 0–1 always)
  const PY = v => H - pb - v * (H - pt - pb);

  // Grid on prob axis
  const grid = [0, 0.25, 0.5, 0.75, 1.0].map(f => {
    const y = PY(f).toFixed(1);
    return `<line x1="${pl}" y1="${y}" x2="${W-pr}" y2="${y}" stroke="${C.grid}" stroke-dasharray="3 3"/>` +
           `<text x="${W-pr+6}" y="${+y+4}" fill="${C.faint}" font-size="9.5" font-family="var(--num)">${(f*100).toFixed(0)}%</text>`;
  }).join("");

  // Stock left-axis labels
  const sGrid = [sLo, (sLo+sHi)/2, sHi].map(v => {
    const y = SY(v).toFixed(1);
    return `<text x="${pl-6}" y="${+y+4}" fill="${C.brand}" font-size="9.5" text-anchor="end" font-family="var(--num)">${v.toFixed(0)}</text>`;
  }).join("");

  // Entry marker
  const entryX  = data.entry_ts ? TX(data.entry_ts) : null;
  const entryMark = entryX
    ? `<line x1="${entryX.toFixed(1)}" y1="${pt}" x2="${entryX.toFixed(1)}" y2="${H-pb}" stroke="${C.warn}" stroke-width="1.2" stroke-dasharray="4 3"/>` +
      `<text x="${entryX.toFixed(1)}" y="${pt-2}" fill="${C.warn}" font-size="8.5" text-anchor="middle" font-family="var(--num)">entry</text>`
    : "";

  const sPts   = stock.map(p => [TX(p.ts), SY(p.close)]);
  const probPts = prob.map(p => [TX(p.ts), PY(p.p)]);

  const t0Lbl  = `<text x="${pl}" y="${H-8}" fill="${C.faint}" font-size="9.5" font-family="var(--num)">T0 ${dday(data.t0_ts)}</text>`;
  const nowLbl = `<text x="${W-pr}" y="${H-8}" fill="${C.faint}" font-size="9.5" text-anchor="end" font-family="var(--num)">${dday(new Date().toISOString())}</text>`;

  el.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" style="height:180px" role="img" aria-label="${esc(data.symbol)} price and probability since T0">
    ${grid}${sGrid}${entryMark}
    ${sPts.length >= 2   ? `<path d="${smooth(sPts)}"   fill="none" stroke="${C.brand}" stroke-width="2.2" stroke-linecap="round"/>` : ""}
    ${probPts.length >= 2 ? `<path d="${smooth(probPts)}" fill="none" stroke="${C.up}"    stroke-width="1.8" stroke-dasharray="6 3" stroke-linecap="round"/>` : ""}
    ${t0Lbl}${nowLbl}
  </svg>
  <div class="legrow" style="margin-top:6px">
    <span><i style="border-color:${C.brand}"></i>${esc(data.symbol)} close price</span>
    <span><i style="border-color:${C.up};border-top-style:dashed"></i>Polymarket probability</span>
  </div>`;
}

async function loadPositionHistory(posId) {
  const el = document.getElementById(`pos-history-${posId}`);
  if (!el) return;
  const now = Date.now();
  const cached = positionHistoryCache[posId];
  if (!cached || cached.expires < now) {
    if (!cached) el.innerHTML = '<div class="empty" style="font-size:12px">Loading history…</div>';
    try {
      const r = await fetch(`/api/position/${posId}/history`, {cache:"no-store"});
      positionHistoryCache[posId] = {data: await r.json(), expires: now + 300_000};
    } catch(e) {
      if (!cached) el.innerHTML = '<div class="empty">History unavailable.</div>';
      return;
    }
  }
  positionHistoryChart(el, positionHistoryCache[posId].data);
}

function loadAllPositionHistories() {
  expandedPositions.forEach(id => loadPositionHistory(id));
}

async function refresh(){
  let d;
  try{ d = await (await fetch("/api/metrics",{cache:"no-store"})).json(); }
  catch(e){ $("#meta").textContent = "reconnecting..."; return; }
  const p=d.portfolio, a=d.allocation, pf=d.performance, x=d.exec, g=d.safety, r=d.risk, de=d.deltas || {};
  $$(".benchname").forEach(e => e.textContent = d.benchmark);
  $("#expname").textContent = d.experiment;
  const mp=$("#marketPill"), ml=$("#marketLabel");
  ml.textContent = d.market?.is_open ? "MARKET OPEN" : "MARKET CLOSED";
  mp.classList.toggle("closed", !d.market?.is_open);
  $("#meta").innerHTML = `NAV ${dt(p.as_of)} | ${marketText(d.market)} | ${cadence(d.tick_seconds)} loop | <span id="clk">${new Date().toLocaleTimeString(LOCALE)}</span>`;
  $("#sideEq").textContent = usd0(p.equity);
  $("#sideSub").textContent = `${p.open_positions} positions | ${d.experiment}`;
  $("#sideHealth").textContent = d.alerts.critical.length ? "ALERT" : d.alerts.warning.length ? "WARN" : "OK";
  $("#dep_sha").innerHTML = `SHA: <b>${esc(d.deployment.git_sha)}</b>`;
  $("#dep_branch").innerHTML = `Branch: <b>${esc(d.deployment.git_branch)}</b>`;
  $("#dep_dash").innerHTML = `Dash uptime: ${cadence(d.deployment.dash_uptime)}`;
  $("#dep_trader").innerHTML = `Trader heartbeat: ${cadence(d.deployment.trader_uptime)}`;
  $("#dep_health").innerHTML = `<b>${esc(d.deployment.dash_health)}</b>`;

  renderAlerts($("#alerts"), d.alerts, false);
  renderAlerts($("#palerts"), d.alerts, false);
  const openPnl=(d.open_positions||[]).reduce((s,x)=>s+(x.unrealized||0),0);
  $("#commandStrip").innerHTML =
    healthCard(d) +
    kpi("Equity", usd0(p.equity), `Last tick ${deltaHtml(de.equity,true)}`, d.experiment, "brand") +
    kpi("Excess vs passive", susd(p.excess), `Last tick ${deltaHtml(de.excess,true)}`, p.excess_pct==null ? null : pct(p.excess_pct,2), cl(p.excess) || "neu") +
    kpi("Open P&L", susd(openPnl), `Positions ${deltaHtml(de.open_positions,false)}`, `${p.open_positions} open`, "neu") +
    kpi("Cash / margin", usd0(a.cash), `Last tick ${deltaHtml(de.cash,true)}`, g.margin_status, g.margin_status === "OK" ? "up" : "warn");

  $("#risk").innerHTML =
    kpi("Max position", nn(r.max_pos_pct,1)+"%", "", null) +
    kpi("Drawdown", nn(r.dd_pct,2)+"%", "", r.dd_pct < -3 ? "watch" : null, r.dd_pct < -3 ? "warn" : "neu") +
    kpi("24h return", pct(de.return_24h_pct,2), "", null, cl(de.return_24h_pct)||"neu") +
    kpi("7d return", pct(de.return_7d_pct,2), "", null, cl(de.return_7d_pct)||"neu");

  const attention = (d.open_positions || []).filter(x => x.exit_risk !== "normal");
  $("#attention").innerHTML = table(attention, [
    {h:"Risk",f:r=>riskTag(r)},{h:"Sym",f:r=>esc(r.symbol)},{h:"Unreal",n:1,f:r=>susd(r.unrealized),cl:r=>cl(r.unrealized)},
    {h:"Stop dist",n:1,f:r=>r.stop_distance_pct==null ? "-" : pct(r.stop_distance_pct,2),cl:r=>r.stop_distance_pct!=null && r.stop_distance_pct<=2 ? "down" : ""},
    {h:"Theta dist",n:1,f:r=>r.theta_distance_pp==null ? "-" : sg(r.theta_distance_pp,1)+"pp",cl:r=>r.theta_distance_pp!=null && r.theta_distance_pp<=3 ? "down" : ""},
    {h:"Days left",n:1,f:r=>nn(r.days_to_resolution,1)}
  ]);

  donut($("#donut"),[{v:a.spy_pct,c:C.brand},{v:a.trades_pct,c:C.sky},{v:Math.max(0,a.cash_pct),c:"#cbd5e1"}]);
  $("#invpct").textContent = nn(a.invested_pct,0)+"%";
  const li=(c,nm,v,pc)=>`<div class="it"><span class="sw" style="background:${c}"></span><span class="nm">${esc(nm)}</span><span class="vl">${usd0(v)}</span><span class="pc">${nn(pc,1)}%</span></div>`;
  $("#alloclegend").innerHTML = li(C.brand,d.benchmark+" index",a.spy_value,a.spy_pct) + li(C.sky,`Event trades (${p.open_positions})`,a.trades_value,a.trades_pct) + li("#cbd5e1","Idle cash",a.cash,a.cash_pct) + `<div class="it"><span class="nm mut" style="font-size:11.5px">${nn(a.bench_shares,3)} ${esc(d.benchmark)} @ ${usd(a.bench_price)}</span></div>`;
  areaChart($("#chart"), d.equity_series, "chartTip");

  renderPositions(d.open_positions || []);
  renderOrdersTrades(d);
  renderWatchlist(d);
  renderDiagnostics(d, openPnl);
  loadAllPositionHistories(); // async, fire-and-forget — charts load after DOM is ready
}

function renderPositions(rows){
  $("#positions").innerHTML = table(rows, [
    {h:"",f:r=>`<button class="rowButton" data-pos="${r.position_id}" aria-label="Toggle position detail">${expandedPositions.has(String(r.position_id)) ? "-" : "+"}</button>`},
    {h:"Risk",f:r=>riskTag(r)},{h:"Sym",f:r=>esc(r.symbol)+(r.is_earnings ? ' <span class="tag er">ER</span>' : "")},
    {h:"Qty",n:1,f:r=>nn(r.qty,0)},{h:"Notional",n:1,f:r=>usd0(r.notional)},
    {h:"Entry",n:1,f:r=>nn(r.entry_price)},{h:"Last",n:1,f:r=>nn(r.last)},
    {h:"Stop",n:1,f:r=>r.is_earnings ? '<span class="tag neu">Theta</span>' : (r.stop_loss==null ? "-" : nn(r.stop_loss)),cl:r=>r.stop_distance_pct!=null && r.stop_distance_pct<=2 ? "down" : ""},
    {h:"Unreal $",n:1,f:r=>susd(r.unrealized),cl:r=>cl(r.unrealized)},
    {h:"Unreal %",n:1,f:r=>r.unrealized_pct==null ? "-" : pct(r.unrealized_pct,2),cl:r=>cl(r.unrealized_pct)},
    {h:"Prob now",n:1,f:r=>r.prob_now==null ? "-" : nn(r.prob_now,3)},
    {h:"Theta dist",n:1,f:r=>r.theta_distance_pp==null ? "-" : sg(r.theta_distance_pp,1)+"pp",cl:r=>r.theta_distance_pp!=null && r.theta_distance_pp<=3 ? "down" : ""},
    {h:"Kelly",n:1,f:r=>r.kelly_pct==null ? "-" : `<span class="tag brand">${nn(r.kelly_pct,1)}%</span>`},
    {h:"Held",n:1,f:r=>nn(r.days_held,1)+"d"},{h:"Question",f:r=>esc(r.question),cl:()=>"q"}
  ], r => expandedPositions.has(String(r.position_id)) ? positionDetail(r) : "");
  $$("#positions .rowButton").forEach(btn => btn.onclick = () => {
    const id = btn.dataset.pos;
    expandedPositions.has(id) ? expandedPositions.delete(id) : expandedPositions.add(id);
    refresh();
  });
}
function positionDetail(r){
  return `<tr class="detailRow"><td colspan="15"><div class="positionDetail">
    <div class="detailBlock"><div class="k">Why this trade exists</div><div class="v">${esc(r.question)}</div></div>
    <div class="detailBlock"><div class="k">Probability path</div><div class="v">T0 ${nn(r.t0_prob,3)} | Entry ${nn(r.entry_prob,3)} | Now ${nn(r.prob_now,3)} | d ${r.prob_runup_pp==null ? "-" : sg(r.prob_runup_pp,1)+"pp"}</div></div>
    <div class="detailBlock"><div class="k">Stock path</div><div class="v">T0 ${usd(r.stock_t0)} | T0->entry ${r.stock_entry_runup_pct==null ? "-" : pct(r.stock_entry_runup_pct,2)} | T0->now ${r.stock_runup_pct==null ? "-" : pct(r.stock_runup_pct,2)}</div></div>
    <div class="detailBlock"><div class="k">Exit pressure</div><div class="v">Stop dist ${r.stop_distance_pct==null ? "-" : pct(r.stop_distance_pct,2)} | Theta dist ${r.theta_distance_pp==null ? "-" : sg(r.theta_distance_pp,1)+"pp"} | Resolves ${dt(r.resolution_ts)}</div></div>
  </div>
  <div id="pos-history-${r.position_id}" class="chartWrap" style="margin-top:12px;min-height:52px"></div>
  </td></tr>`;
}
function renderOrdersTrades(d){
  const orderRows = showAllOrders ? d.recent_orders : d.recent_orders.slice(0,10);
  $("#orders").innerHTML = table(orderRows, [
    {h:"When",n:1,f:r=>dt(r.ts)},{h:"Sym",f:r=>esc(r.symbol)},{h:"Side",f:r=>esc(r.action)},
    {h:"Qty",n:1,f:r=>nn(r.qty,r.qty<10?3:0)},{h:"Kind",f:r=>`<span class="tag neu">${esc(r.kind)}</span>`},
    {h:"Fill",n:1,f:r=>nn(r.fill_price)},{h:"Comm",n:1,f:r=>r.commission==null ? "-" : usd(r.commission)},
    {h:"Slip",n:1,f:r=>r.slip_bps==null ? "-" : sg(r.slip_bps,1)+"bp",cl:r=>r.slip_bps==null ? "" : (r.slip_bps>0 ? "down" : "up")},
    {h:"Status",f:r=>{const ok=r.status==="Filled"||r.status==="dry_run", bad=["Cancelled","unqualified","ApiCancelled","Inactive","dry_run_limit_miss","dry_run_no_price"].includes(r.status); return `<span class="tag ${ok?"ok":bad?"bad":"warn"}">${esc(r.status)}</span>`;}}
  ]) + (d.recent_orders.length>10 ? `<div class="toolbar"><button class="miniBtn" id="ordersToggle">${showAllOrders ? "Show last 10" : "Show all "+d.recent_orders.length}</button></div>` : "");
  const toggle=$("#ordersToggle"); if(toggle) toggle.onclick=()=>{showAllOrders=!showAllOrders; refresh();};
  $("#trades").innerHTML = table(d.recent_trades, [
    {h:"Sym",f:r=>esc(r.symbol)},{h:"PnL",n:1,f:r=>susd(r.pnl),cl:r=>cl(r.pnl)},
    {h:"%",n:1,f:r=>r.pnl_pct==null ? "-" : pct(r.pnl_pct,2),cl:r=>cl(r.pnl_pct)},
    {h:"Reason",f:r=>esc(r.exit_reason || "-"),cl:()=>"q"},{h:"When",n:1,f:r=>dt(r.exit_ts)}
  ]);
}
function renderWatchlist(d){
  const probChip = v => v == null ? "-" : `<span class="${v>=0.7 ? "up" : v>=0.4 ? "warn" : "down"}" style="font-weight:800">${nn(v,2)}</span>`;
  const assetTags = a => !a || !a.length ? '<span class="mut">-</span>' : a.map(x=>`<span class="tag neu">${esc(x.symbol)}</span>`).join(" ");
  const relChip = v => v == null ? "-" : `<span class="tag ${v>=0.8 ? "ok" : v>=0.6 ? "warn" : "bad"}">${nn(v,2)}</span>`;
  $("#upcoming").innerHTML = table(d.markets.upcoming, [
    {h:"#",n:1,f:r=>`<span style="font-weight:900;color:var(--brand)">${r.pecking}</span>`},
    {h:"State",f:r=>stateTag(r.state)},
    {h:"Resolves",n:1,f:r=>`${dday(r.end_at)} <span class="mut">(${nn(r.days_to_resolution,1)}d)</span>`},
    {h:"Market question",f:r=>esc(r.question)+(r.is_earnings ? ' <span class="tag er">ER</span>' : ""),cl:()=>"q"},
    {h:"Relevance",n:1,f:r=>relChip(r.relevance)},{h:"Prob T0",n:1,f:r=>probChip(r.t0_prob)},
    {h:"Prob Now",n:1,f:r=>probChip(r.prob_now)},{h:"d prob",n:1,f:r=>r.prob_delta==null ? "-" : sg(r.prob_delta,1)+"pp",cl:r=>cl(r.prob_delta)},
    {h:"Prob age",n:1,f:r=>r.prob_age_hours==null ? "-" : nn(r.prob_age_hours,1)+"h"},{h:"Mapped assets",f:r=>assetTags(r.assets)}
  ]) + `<div class="mut" style="margin-top:10px;font-size:11.5px">${d.markets.tracked} markets tracked | ${d.markets.with_t0} with T0 baseline | ranked by probability delta</div>`;
}
function renderDiagnostics(d, openPnl){
  const attr=d.attribution, s=d.system, c2=d.api_cost, x=d.exec, g=d.safety;
  $("#attribution").innerHTML =
    kpi("Active return", pct(attr.active_return_pct,2), "", null, cl(attr.active_return_pct)||"neu") +
    kpi("Open contrib", pct(attr.open_contrib_pct,2), susd(openPnl), null, cl(attr.open_contrib_pct)||"neu") +
    kpi("Realized contrib", pct(attr.realized_contrib_pct,2), susd(d.performance.realized_pnl), null, cl(attr.realized_contrib_pct)||"neu") +
    kpi("Cash drag / residual", pct(attr.cash_drag_pct,2), "", null, cl(-attr.cash_drag_pct)||"neu");
  const up = s.disk_used_pct;
  $("#diagCards").innerHTML =
    kpi("DB size", gb(s.db_size_bytes), "", null) +
    kpi("Disk used", up==null ? "-" : nn(up,1)+"%", `<div class="meter" style="width:100%;margin-top:4px"><span style="width:${up||0}%;background:${up>90?C.down:up>75?C.warn:C.up}"></span></div>`, null, up>90?"down":up>75?"warn":"up") +
    kpi("Gemini today", usd(c2.today_usd), `${c2.today_calls} calls`, null) +
    kpi("Gemini total", usd(c2.total_usd), `${c2.total_calls} calls`, null);
  $("#execHealth").innerHTML =
    kv("Fills ok", `${x.filled}/${x.recent}`, x.filled < x.recent ? "down" : "") +
    kv("Failed 24h", String(x.failed_24h || 0), x.failed_24h ? "down" : "") +
    kv("Avg slippage", x.avg_slip_bps == null ? "-" : sg(x.avg_slip_bps,2)+"bp", x.avg_slip_bps > 0 ? "down" : "up") +
    kv("Buy / sell slip", `${x.avg_buy_slip_bps == null ? "-" : sg(x.avg_buy_slip_bps,2)+"bp"} / ${x.avg_sell_slip_bps == null ? "-" : sg(x.avg_sell_slip_bps,2)+"bp"}`) +
    kv("Actual commissions", usd(x.commission_total)) + kv("Modeled costs", usd(x.modeled_cost_total)) +
    kv("Actual - modeled", susd(x.cost_delta), x.cost_delta > 0 ? "down" : x.cost_delta < 0 ? "up" : "") +
    kv("Market", marketText(d.market)) + kv("Capital base", usd0(g.investable)) +
    kv("Buy cap", "+"+nn(g.execution_buffer_pct,2)+"%") + kv("Sizing", `${g.kelly_enabled ? "Half-Kelly" : "Fixed"} | min ${usd0(g.min_order_notional)}`);
  $("#runtime").innerHTML =
    kv("Next tick", dt(d.ops.next_tick)) +
    kv("Last discovery", dt(d.ops.last_discovery)) + kv("Next discovery", dt(d.ops.next_discovery)) +
    kv("Last prune", dt(d.ops.last_prune)) + kv("Next prune", dt(d.ops.next_prune)) +
    kv("Dashboard uptime", cadence(d.deployment.dash_uptime)) + kv("Trader heartbeat", cadence(d.deployment.trader_uptime), d.deployment.trader_uptime > d.tick_seconds*1.5 ? "warn" : "");
}
function kv(k,v,klass=""){return `<div class="kv"><span class="k">${esc(k)}</span><span class="v ${klass}">${esc(v)}</span></div>`;}

async function loadBacktest(){
  if(backtestLoaded) return; backtestLoaded = true;
  let d; try{ d = await (await fetch("/api/backtest",{cache:"no-store"})).json(); }catch(e){ return; }
  if(!d.available){ $("#btsummary").innerHTML = `<div class="card"><div class="empty">Backtest CSV not found on the server.</div></div>`; return; }
  const s=d.summary;
  $("#btttl").textContent = `Walk-forward folds, ${d.experiment} / ${d.benchmark}`;
  $("#btsummary").innerHTML =
    kpi("OOS total return", pct(s.total_return_pct,1), `${s.n_folds} folds`, null, cl(s.total_return_pct)||"neu") +
    kpi("Benchmark", pct(s.total_benchmark_pct,1), "", null, cl(s.total_benchmark_pct)||"neu") +
    kpi("Excess", pct(s.total_excess_pct,1), `${s.positive_folds}/${s.n_folds} positive`, null, s.total_excess_pct>=0?"up":"down") +
    kpi("Worst drawdown", nn(s.worst_dd_pct,1)+"%", `${s.total_trades} trades`, null, "neu");
  let liveStats = {};
  try{ const m = await (await fetch("/api/metrics",{cache:"no-store"})).json(); liveStats = m.live_stats || {}; }catch(e){}
  renderStats(d.stats || {}, liveStats);
  barChart($("#btchart"), d.folds);
  if(d.equity_series) areaChart($("#bt_equity_chart"), d.equity_series, "btTip");
  $("#bttable").innerHTML = table(d.folds, [
    {h:"Fold",f:r=>"F"+r.fold},{h:"Window",f:r=>`<span class="mut" style="font-size:11px">${esc(r.start)} to ${esc(r.end)}</span>`},
    {h:"Return",n:1,f:r=>pct(r.return_pct,1),cl:r=>cl(r.return_pct)},{h:"Bench",n:1,f:r=>pct(r.benchmark_pct,1)},
    {h:"Excess",n:1,f:r=>pct(r.excess_pct,1),cl:r=>cl(r.excess_pct)},{h:"MaxDD",n:1,f:r=>nn(r.max_dd_pct,1)+"%",cl:()=>"down"},{h:"Trades",n:1,f:r=>r.trades}
  ]);
  const P=d.policy, defs={
    enter_strong:["Enter strong","prob fires now"],enter_floor:["Enter floor","held prob"],hold_days:["Hold window","trained days"],
    atr_mult:["ATR mult","trailing stop"],lock_activate:["Lock at","profit-lock trigger"],theta_out:["Theta out","prob exit"],
    max_prob_surge:["Max d prob","surge gate"],max_price_runup:["Max runup","price gate"],position_size_pct:["Base size","of equity"],max_concurrent:["Max concurrent","positions"]
  };
  $("#btparams").innerHTML = Object.keys(defs).map(k => {
    if(P[k] == null) return "";
    let v=P[k];
    if(["enter_strong","enter_floor","theta_out","lock_activate","max_prob_surge","max_price_runup","position_size_pct"].includes(k)) v=(v*100).toFixed(k==="position_size_pct"||k==="lock_activate"?1:0)+"%";
    else if(k==="atr_mult") v=Number(v).toFixed(2)+"x"; else v=String(v);
    return `<div class="param"><div class="k">${defs[k][0]}</div><div class="v">${esc(v)}</div><div class="d">${defs[k][1]}</div></div>`;
  }).join("");
}

function renderStats(bt, live){
  const bs = bt.trades || {}, ls = live.trades || {};
  const v = (x, suf="") => (x == null ? '<span class="mut">n/a</span>' : esc(String(x)) + suf);
  const ci = s => (s.ci_low == null ? null : `[${s.ci_low}, ${s.ci_high}]`);
  const rows = [
    {m:"Sharpe, annualized", b:v(bt.sharpe), l:v(live.sharpe)},
    {m:"Excess vs benchmark", b:v(bt.excess_pct,"%"), l:v(live.excess_pct,"%")},
    {m:"Max drawdown", b:v(bt.max_dd_pct,"%"), l:v(live.max_dd_pct,"%")},
    {m:"Mean trade return", b:v(bs.mean_pct,"%"), l:v(ls.mean_pct,"%")},
    {m:"Win rate", b:v(bs.win_rate,"%"), l:v(ls.win_rate,"%")},
    {m:"t-stat, return &gt; 0", b:v(bs.t_stat), l:v(ls.t_stat)},
    {m:"p-value, one-sided", b:v(bs.p_one_sided), l:v(ls.p_one_sided)},
    {m:"95% CI, mean trade", b:v(ci(bs)), l:v(ci(ls))},
    {m:"Trades, n", b:v(bs.n), l:v(ls.n)},
  ];
  $("#btstats").innerHTML = table(rows, [
    {h:"Metric", f:r=>r.m},
    {h:"Backtest OOS", n:1, f:r=>r.b},
    {h:"Live", n:1, f:r=>r.l},
  ]);
  const notes = [];
  if(bs.n) notes.push(bs.significant
    ? `Backtest OOS mean trade return is significantly &gt; 0 (p=${bs.p_one_sided}, n=${bs.n}).`
    : `Backtest OOS: not significant at 5%${bs.underpowered ? ` (underpowered, n=${bs.n})` : ` (p=${bs.p_one_sided}, n=${bs.n})`}.`);
  notes.push(!ls.n ? "Live: no closed trades yet."
    : ls.underpowered ? `Live: only ${ls.n} closed trades, significance not claimed (underpowered).`
    : ls.significant ? `Live: significant (p=${ls.p_one_sided}, n=${ls.n}).`
    : `Live: not significant (p=${ls.p_one_sided}, n=${ls.n}).`);
  $("#btstatsnote").innerHTML = notes.join(" ");
}

renderShell();
refresh();
setInterval(() => { const e=$("#clk"); if(e) e.textContent = new Date().toLocaleTimeString(LOCALE); }, 1000);
setInterval(refresh, 20000);
