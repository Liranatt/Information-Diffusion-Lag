#!/usr/bin/env python
"""Local, DB-free ingestion of the "alive" candidate markets, then merge into
copies of the committed artifacts so you can run a broader backtest off-network.

WHY THIS EXISTS
  The normal pipeline (`python -m ingest --backfill` + `--rebuild`) needs the
  Postgres DB (security master, world persistence) which is unreachable from
  this machine. This script reproduces the same steps DB-free:
    * substitute the IB security master with a public US-ticker catalog (SEC
      company_tickers + the ETFs the strategy maps to + symbols already in
      prices.pkl),
    * call the SAME Gemini relevance-gate + mapping (`build_gemini_asset_worlds`)
      and the SAME polarity labeler (`ingest.label_polarity.label_batch`),
    * download probabilities (Polymarket CLOB) and prices (yfinance),
    * build candidate rows with `core.features.compute_features`, using the
      POLARITY-AWARE candidate trigger: t_theta is found on the EFFECTIVE path
      (raw P for +1 pairs, 1-P for -1 pairs), so NO-conviction signals are no
      longer thrown away. Features stay raw; the kernel flips at sim as today.

INPUT   scratchpad/alive_markets.json (the 694 markets with real probability data)
OUTPUT  data/local_run/*  (intermediates, idempotent) and
        data/candidates_v2.parquet, data/probs_v2.pkl, data/prices_v2.pkl
        (committed universe + the new markets — run the backtest against these)

COST    Gemini flash: ~35 mapping calls + ~35 polarity calls on 694 markets
        (~$1-2 on your key). Each stage caches its output, so re-running never
        re-bills a completed stage. Run stages one at a time the first time.

USAGE   python local_run.py            # run all stages (idempotent)
        the mapping/polarity stages hit Gemini; probs/prices hit public APIs.
"""
from __future__ import annotations

import asyncio
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ingest.world import (  # noqa: E402
    SourceMarket,
    IBTradableAsset,
    build_gemini_asset_worlds,
    QUESTION_RELEVANCE_FLOOR,
)
from ingest.gemini_client import GeminiClient  # noqa: E402
from ingest.label_polarity import label_batch, pair_hash, BATCH_SIZE  # noqa: E402
from core.polarity import (  # noqa: E402
    resolve_polarity,
    effective_prob_path,
    clear_polarity_caches,
)
from core.features import find_t_theta, compute_features, SECTOR_ETFS  # noqa: E402

RUN = ROOT / "data" / "local_run"
RUN.mkdir(parents=True, exist_ok=True)
ALIVE = ROOT / "data" / "local_run" / "alive_markets.json"   # copy your scratchpad file here
POLARITY_LABELS = ROOT / "data" / "polarity_labels.json"
CONNECTION_FLOOR = 0.5           # matches ingest.artifacts.RELEVANCE_FLOOR
CLOB = "https://clob.polymarket.com/prices-history"
COMMON_ETFS = ["SPY", "QQQ", "USO", "BNO", "XLE", "GLD", "SLV", "UNG", "XLF", "XLK",
               "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLC", "SHY", "TLT", "DIA",
               "IWM", "SMH", "OIH", "EWY", "UGA", "CPER"]


# ── Stage 1: substitute IB security-master catalog (no DB) ───────────────────
def build_catalog() -> list[IBTradableAsset]:
    syms: dict[str, tuple[str, str]] = {}
    try:
        r = httpx.get("https://www.sec.gov/files/company_tickers.json",
                      headers={"User-Agent": "research local_run"}, timeout=30)
        for v in r.json().values():
            t = str(v["ticker"]).upper().strip()
            if t.isalpha() and 1 <= len(t) <= 5:
                syms[t] = (str(v["title"]), "stock")
    except Exception as e:  # noqa: BLE001
        print(f"  [catalog] SEC fetch failed ({str(e)[:60]}); using local symbols only")
    with open(ROOT / "data" / "prices.pkl", "rb") as f:
        for s in pickle.load(f):
            syms.setdefault(str(s).upper(), (str(s), "etf" if len(str(s)) <= 4 else "stock"))
    for e in COMMON_ETFS:
        syms[e] = (e, "etf")
    print(f"  [catalog] {len(syms)} tradable symbols")
    return [
        IBTradableAsset(symbol=s, asset_name=name,
                        asset_class=cls, primary_exchange="SMART",
                        stock_type="ETF" if cls == "etf" else "COMMON")
        for s, (name, cls) in syms.items()
    ]


def _source(m: dict) -> SourceMarket:
    return SourceMarket(
        market_id=m["market_id"], event_id=m.get("event_id", ""),
        event_title=m.get("event_title", m["question"][:100]), question=m["question"],
        created_at=datetime.fromisoformat(m["created_at"]),
        end_at=datetime.fromisoformat(m["end_at"]),
        tags=m.get("tags", []), raw_market={},
        yes_token_id=m["yes_token_id"], condition_id=m.get("condition_id", ""),
        final_outcome=None,
    )


# ── Stage 2: Gemini relevance gate + asset mapping ───────────────────────────
async def _map_reqs(gemini, catalog, reqs):
    """Map a batch; on truncated-JSON/validation failure, split and retry down to
    singletons so no market is silently lost to a flaky response."""
    try:
        return await build_gemini_asset_worlds(gemini, reqs, tradable_assets=catalog)
    except Exception as e:  # noqa: BLE001
        if len(reqs) <= 1:
            print(f"  [map] dropped 1 market: {str(e)[:60]}")
            return []
        mid = len(reqs) // 2
        return (await _map_reqs(gemini, catalog, reqs[:mid])
                + await _map_reqs(gemini, catalog, reqs[mid:]))


async def stage_map(markets: list[dict]) -> list[dict]:
    out = RUN / "worlds.json"
    if out.exists():
        print("[map] cached"); return json.loads(out.read_text(encoding="utf-8"))
    catalog = build_catalog()
    gemini = GeminiClient()
    rows: list[dict] = []
    kept = 0
    try:
        now = datetime.now(timezone.utc)
        B = 8
        for i in range(0, len(markets), B):
            batch = markets[i:i + B]
            reqs = [(m["market_id"], _source(m), now) for m in batch]
            worlds = await _map_reqs(gemini, catalog, reqs)
            wm = {w.request_id: w for w in worlds}
            for m in batch:
                w = wm.get(m["market_id"])
                if w is None or w.question_relevance < QUESTION_RELEVANCE_FLOOR:
                    continue
                kept += 1
                for a in w.assets:
                    if (a.connection_strength or 0) < CONNECTION_FLOOR:
                        continue
                    rows.append({
                        "market_id": m["market_id"], "event_id": m.get("event_id", ""),
                        "question": m["question"], "symbol": a.symbol,
                        "relevance": float(a.connection_strength),
                        "world_size": len(w.assets),
                        "created_at": m["created_at"], "end_at": m["end_at"],
                        "yes_token_id": m["yes_token_id"],
                    })
            print(f"  [map] {min(i+B,len(markets))}/{len(markets)}  pairs={len(rows)}  "
                  f"cost≈${gemini.estimated_cost_usd():.2f}", flush=True)
    finally:
        await gemini.close()
    out.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"[map] relevant markets (relevance>={QUESTION_RELEVANCE_FLOOR}): {kept}/{len(markets)}  "
          f"-> {len(rows)} (market,symbol) pairs -> {out}")
    return rows


# ── Stage 3: polarity labels for the new pairs (merged into the labels file) ──
async def stage_polarity(pairs: list[tuple[str, str]]) -> None:
    cache = json.loads(POLARITY_LABELS.read_text(encoding="utf-8")) if POLARITY_LABELS.exists() else {}
    todo = [(q, s) for q, s in pairs if pair_hash(q, s) not in cache]
    print(f"[polarity] {len(pairs)} pairs, {len(todo)} to label")
    if not todo:
        return
    gemini = GeminiClient()
    try:
        for i in range(0, len(todo), BATCH_SIZE):
            batch = todo[i:i + BATCH_SIZE]
            try:
                for rec in await label_batch(gemini, batch):
                    cache[pair_hash(rec["question"], rec["symbol"])] = rec
            except Exception as e:  # noqa: BLE001
                print(f"  [polarity] batch {i} failed: {str(e)[:80]}")
            print(f"  [polarity] {min(i+BATCH_SIZE,len(todo))}/{len(todo)}  "
                  f"cost≈${gemini.estimated_cost_usd():.2f}", flush=True)
    finally:
        await gemini.close()
    POLARITY_LABELS.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    clear_polarity_caches()
    print(f"[polarity] labels -> {POLARITY_LABELS}")


# ── Stage 4: probability paths (Polymarket CLOB) ─────────────────────────────
async def stage_probs(markets: list[dict]) -> dict:
    out = RUN / "probs_new.pkl"
    if out.exists():
        print("[probs] cached"); return pickle.load(open(out, "rb"))
    sem = asyncio.Semaphore(12)
    probs: dict[str, list] = {}

    async def one(client, m):
        async with sem:
            try:
                c = datetime.fromisoformat(m["created_at"]); e = datetime.fromisoformat(m["end_at"])
                r = await client.get(CLOB, params={"market": m["yes_token_id"],
                    "startTs": int(c.timestamp()), "endTs": int(e.timestamp()), "fidelity": 1440})
                if r.status_code != 200:
                    return
                h = r.json().get("history") or []
                pts = [(pd.Timestamp(float(x["t"]), unit="s", tz="UTC").normalize(),
                        min(max(float(x["p"]), 0.0), 1.0)) for x in h]
                if len(pts) >= 2:
                    probs[m["market_id"]] = sorted(set(pts))
            except Exception:  # noqa: BLE001
                return
    async with httpx.AsyncClient(timeout=httpx.Timeout(20)) as client:
        for i in range(0, len(markets), 200):
            await asyncio.gather(*[one(client, m) for m in markets[i:i + 200]])
            print(f"  [probs] {min(i+200,len(markets))}/{len(markets)}  have={len(probs)}", flush=True)
    pickle.dump(probs, open(out, "wb"))
    print(f"[probs] {len(probs)} paths -> {out}")
    return probs


# ── Stage 5: daily prices (yfinance) ─────────────────────────────────────────
def stage_prices(symbols: set[str], start: str, end: str) -> dict:
    """Return a COMPLETE (t,h,l,c) price dict: the committed prices.pkl (SPY +
    every symbol already downloaded) plus any NEW mapped symbols fetched here."""
    out = RUN / "prices_new.pkl"
    if out.exists():
        print("[prices] cached"); return pickle.load(open(out, "rb"))
    import yfinance as yf
    with open(ROOT / "data" / "prices.pkl", "rb") as f:
        prices: dict[str, list] = pickle.load(f)          # seed with existing (incl SPY, ETFs)
    need = sorted((symbols | set(COMMON_ETFS)) - set(prices))
    print(f"  [prices] {len(prices)} already have; downloading {len(need)} new")
    for i, s in enumerate(need):
        try:
            df = yf.download(s, start=start, end=end, progress=False, auto_adjust=False)
            if df is None or df.empty:
                continue
            if isinstance(df.columns, pd.MultiIndex):        # yfinance now returns (field,ticker)
                df.columns = df.columns.get_level_values(0)
            path = []
            for ts, row in df.iterrows():
                try:
                    t = pd.Timestamp(ts)
                    t = (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).normalize()
                    path.append((t, float(row["High"]), float(row["Low"]), float(row["Close"])))
                except Exception:  # noqa: BLE001 - skip NaN/bad bar
                    continue
            if path:
                prices[s] = path
        except Exception as e:  # noqa: BLE001
            print(f"  [prices] {s} failed: {str(e)[:50]}")
        if (i + 1) % 25 == 0:
            print(f"  [prices] {i+1}/{len(need)} new  total={len(prices)}", flush=True)
    pickle.dump(prices, open(out, "wb"))
    print(f"[prices] {len(prices)} symbols total -> {out}")
    return prices


# ── Stage 6: candidate features with the POLARITY-AWARE trigger ──────────────
def _family(q: str) -> str:
    ql = q.lower()
    if "earnings" in ql or "revenue" in ql or "eps" in ql:
        return "earnings"
    if any(k in ql for k in ("iran", "israel", "strike", "war", "hormuz", "military")):
        return "geopolitical"
    if any(k in ql for k in ("fed", "rate", "inflation", "cpi", "gdp", "tariff")):
        return "macro"
    return "other"


def _closes(bars: list) -> list:
    return [(t, c) for (t, _o, _h, _l, c) in bars]


def stage_features(worlds: list[dict], probs: dict, prices: dict) -> pd.DataFrame:
    spy = _closes(prices.get("SPY", []))
    recs = []
    skipped = {"polarity0": 0, "no_theta": 0, "no_probs": 0, "no_feature": 0}
    for w in worlds:
        mid, sym, q = w["market_id"], w["symbol"], w["question"]
        raw = probs.get(mid, [])
        if len(raw) < 2:
            skipped["no_probs"] += 1; continue
        pol, _src = resolve_polarity(q, sym)
        if pol == 0:
            skipped["polarity0"] += 1; continue
        eff = effective_prob_path(raw, pol)          # +1: raw ; -1: 1-P
        t_theta = find_t_theta(eff)                  # trigger on the BULLISH side
        if t_theta is None:
            skipped["no_theta"] += 1; continue
        t0 = pd.Timestamp(w["created_at"]).tz_convert("UTC")
        t_e = pd.Timestamp(w["end_at"]).tz_convert("UTC")
        rec = compute_features(
            market_id=mid, event_id=w.get("event_id", ""), symbol=sym, question=q,
            archetype=_family(q), relevance=w["relevance"], world_size=w["world_size"],
            t0=t0, t_e=t_e, t_theta=t_theta,
            prices=_closes(prices.get(sym, [])), probs=raw,   # features RAW; kernel flips at sim
            spy_prices=spy, sector_etf_prices=spy, sector="Unknown",
        )
        if rec is None:
            skipped["no_feature"] += 1; continue
        recs.append(rec)
    print(f"[features] built {len(recs)} candidates  (skipped {skipped})")
    return pd.DataFrame(recs)


# ── Stage 7: merge into copies of the committed artifacts ────────────────────
def stage_merge(new_df: pd.DataFrame, new_probs: dict, new_prices: dict) -> None:
    old = pd.read_parquet(ROOT / "data" / "candidates.parquet")
    if not new_df.empty:
        new_df = new_df.copy()
        new_df["split"] = ["train" if pd.Timestamp(t).tz_convert("UTC") < pd.Timestamp("2026-01-01", tz="UTC")
                           else "test" for t in new_df["t_theta"]]
    merged = pd.concat([old, new_df], ignore_index=True, sort=False)
    merged.to_parquet(ROOT / "data" / "candidates_v2.parquet", engine="pyarrow", compression="snappy")

    with open(ROOT / "data" / "probs.pkl", "rb") as f:
        probs = pickle.load(f)
    probs.update(new_probs)
    pickle.dump(probs, open(ROOT / "data" / "probs_v2.pkl", "wb"))

    with open(ROOT / "data" / "prices.pkl", "rb") as f:
        prices = pickle.load(f)
    merged_prices = {**new_prices, **prices}          # keep existing (longer) history on overlap
    pickle.dump(merged_prices, open(ROOT / "data" / "prices_v2.pkl", "wb"))

    n_test = int((merged.get("split") == "test").sum()) if "split" in merged else 0
    print(f"\n[merge] candidates_v2.parquet: {len(old)} old + {len(new_df)} new = {len(merged)} "
          f"(test={n_test})")
    print(f"[merge] probs_v2.pkl: {len(probs)}   prices_v2.pkl: {len(merged_prices)}")
    print("\nNEXT: point the backtest at the v2 artifacts, e.g. copy them over data/*.pkl/"
          "candidates.parquet (back up first) or add a --data-dir, then run optimize_cem.")


async def main() -> None:
    if not ALIVE.exists():
        raise SystemExit(f"Put the alive-markets list at {ALIVE} first "
                         f"(copy scratchpad/alive_markets.json there).")
    markets = json.loads(ALIVE.read_text(encoding="utf-8"))
    print(f"alive markets: {len(markets)}")

    worlds = await stage_map(markets)                                   # Gemini $
    await stage_polarity([(w["question"], w["symbol"]) for w in worlds])  # Gemini $
    probs = await stage_probs(markets)                                  # CLOB (free)
    span_t0 = min(m["created_at"] for m in markets)[:10]
    span_te = max(m["end_at"] for m in markets)[:10]
    prices = stage_prices({w["symbol"] for w in worlds}, span_t0, span_te)  # yfinance (free)
    df = stage_features(worlds, probs, prices)
    stage_merge(df, probs, prices)


if __name__ == "__main__":
    asyncio.run(main())
