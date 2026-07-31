#!/usr/bin/env python
"""Tariff-only universe experiment: download every tariff event from Polymarket,
hand-clean it, run the Gemini relevance gate WITHOUT the long-only sentiment
skip, map assets, label polarity, and measure how much the tariff wave adds to
the candidate universe ("the world").

WHY THIS EXISTS
  The committed universe contains zero 2025 tariff contracts: the old regex
  gate rejected the whole category before Gemini ever saw it, and the gate's
  positive-sentiment screen (ingest/world.py:568) drops imposition-framed
  questions even when Gemini rates them highly relevant. Polarity (-1 = trade
  the NO side) already handles direction downstream, so this experiment drops
  the sentiment condition entirely: gate = question_relevance >= 0.60, full stop.

STAGES (each cached under data/tariff_run/, idempotent)
  scan      FREE  Gamma API, tariff tag slugs + tariff-regex question filter,
                  ladder dedup. Also folds in the 2026-07-12 broad-scan cache
                  if present, so nothing the study window saw is missed.
  clean     FREE  structural-noise rules + the hand-curated Claude drop list
                  (data/tariff_run/claude_review.json, written by `review`).
  review    FREE  dump kept/dropped questions for manual curation.
  quote     FREE  exact Gemini call count + list-price cost for map+polarity.
  map       PAID  relevance gate (NO sentiment skip) + tight asset mapping.
                  Requires --approve. Writes gate.json (every decision,
                  including the sentiment flag Gemini returned, so the old
                  gate is reconstructable as a counterfactual) + worlds.json.
  polarity  PAID  LLM polarity for mapped pairs (committed cache reused).
                  Requires --approve. Writes polarity_tariff.json.
  probs     FREE  Polymarket CLOB daily probability paths.
  prices    FREE  yfinance daily bars for newly mapped symbols.
  features  FREE  polarity-aware theta trigger + core.features.compute_features
                  -> tariff_candidates.parquet
  report    FREE  the "how much does it add" summary.

USAGE
  python tariff_run.py scan clean review     # free prep, then curate the list
  python tariff_run.py quote                 # exact paid-call quote
  python tariff_run.py map polarity --approve  # after explicit user OK
  python tariff_run.py probs prices features report
"""
from __future__ import annotations

import asyncio
import json
import pickle
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Pin the same model/prices as the July local_run (import side effect is the pin).
from ingest.label_polarity import label_batch, pair_hash, BATCH_SIZE  # noqa: E402
from ingest.gemini_client import GeminiClient  # noqa: E402
from ingest.world import (  # noqa: E402
    SourceMarket,
    IBTradableAsset,
    RelevanceGateBatch,
    TightAssetWorlds,
    GEMINI_RELEVANCE_GATE_PROMPT,
    GEMINI_TIGHT_MAPPING_PROMPT,
    QUESTION_RELEVANCE_FLOOR,
    gemini_world_payload,
    ib_asset_catalog_index,
    canonicalize_tight_gemini_world,
)
from ingest.scanner import _fetch_events, _scanned_markets_from_event  # noqa: E402
from ingest.dedup import dedup_markets  # noqa: E402
from ingest.prefilter import structural_noise_rule  # noqa: E402
from core.polarity import explain_polarity, effective_prob_path  # noqa: E402
from core.features import find_t_theta, compute_features  # noqa: E402

RUN = ROOT / "data" / "tariff_run"
RUN.mkdir(parents=True, exist_ok=True)

SCAN_JSON = RUN / "tariff_markets.json"
REVIEW_JSON = RUN / "claude_review.json"
GATE_JSON = RUN / "gate.json"
WORLDS_JSON = RUN / "worlds.json"
POLARITY_JSON = RUN / "polarity_tariff.json"
PROBS_PKL = RUN / "probs_tariff.pkl"
PRICES_PKL = RUN / "prices_tariff.pkl"
CANDS_PARQUET = RUN / "tariff_candidates.parquet"
COMMITTED_POLARITY = ROOT / "data" / "polarity_labels.json"

# The broad 2024-07 -> 2026-05 scan cache from the 2026-07-12 session, if it
# still exists; folded into the scan so tag-gaps in Gamma queries cost nothing.
BROAD_CACHE = Path(
    r"C:\Users\Liran\AppData\Local\Temp\claude"
    r"\C--Users-Liran-PycharmProjects-cem-clean-repo"
    r"\bbdc1827-588d-4e3c-85fc-c2e8ab3e1c72\scratchpad\scanned_markets.json"
)

TARIFF_RE = re.compile(
    r"tariff|import\s+dut(?:y|ies)|customs\s+dut(?:y|ies)|trade\s+war"
    r"|trade\s+deal|reciprocal\s+tariff|de\s+minimis|section\s+232|section\s+301",
    re.I,
)
# Tag slugs seen on tariff events in the scan data; unknown slugs just return [].
TARIFF_TAGS = [
    "tariffs", "trade-war", "trade", "liberation-day-tariffs",
    "reciprocal-tariffs", "trump-tariffs", "economic-policy",
]
SCAN_START = datetime(2024, 7, 1, tzinfo=timezone.utc)
SCAN_END = datetime(2026, 5, 27, tzinfo=timezone.utc)

CONNECTION_FLOOR = 0.5           # matches ingest.artifacts.RELEVANCE_FLOOR
CLOB = "https://clob.polymarket.com/prices-history"
GATE_BATCH = 8
COMMON_ETFS = ["SPY", "QQQ", "USO", "BNO", "XLE", "GLD", "SLV", "UNG", "XLF", "XLK",
               "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLC", "SHY", "TLT", "DIA",
               "IWM", "SMH", "OIH", "EWY", "UGA", "CPER",
               # US-listed single-country ETFs: the natural instrument for
               # bilateral tariff questions; absent from the SEC company list.
               "EWA", "EWC", "EWD", "EWG", "EWI", "EWJ", "EWK", "EWL", "EWM",
               "EWN", "EWP", "EWQ", "EWS", "EWT", "EWU", "EWW", "EWZ", "EZA",
               "FXI", "MCHI", "INDA", "EIDO", "EIS", "ARGT", "EFNL", "NORW",
               "EDEN", "TUR", "EPOL", "VNM", "THD", "EPHE", "PAK", "KSA", "EZU", "VGK", "IEUR",
               # OTC ADRs Gemini reached for on EU-car/luxury tariff questions.
               "VWAGY", "BMWYY", "LVMUY", "SIEGY"]


def _mdict(m) -> dict:
    return {"event_id": m.event_id, "market_id": m.market_id, "question": m.question,
            "event_title": m.event_title, "tags": m.tags,
            "created_at": m.created_at.isoformat(), "end_at": m.end_at.isoformat(),
            "yes_token_id": m.yes_token_id, "condition_id": m.condition_id}


# ── scan ──────────────────────────────────────────────────────────────────────
async def stage_scan() -> list[dict]:
    if SCAN_JSON.exists():
        markets = json.loads(SCAN_JSON.read_text(encoding="utf-8"))
        print(f"[scan] cached: {len(markets)} tariff markets")
        return markets

    by_id: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60)) as client:
        for tag in TARIFF_TAGS:
            events = await _fetch_events(client, {
                "tag_slug": tag,
                "end_date_min": SCAN_START.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_date_max": SCAN_END.strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            n0 = len(by_id)
            for ev in events:
                for m in _scanned_markets_from_event(ev):
                    if SCAN_START <= m.end_at <= SCAN_END:
                        by_id[m.market_id] = _mdict(m)
            print(f"  [scan] tag={tag!r}: {len(events)} events, +{len(by_id)-n0} markets")
            await asyncio.sleep(0.5)

    if BROAD_CACHE.exists():
        n0 = len(by_id)
        for m in json.loads(BROAD_CACHE.read_text(encoding="utf-8")):
            by_id.setdefault(m["market_id"], m)
        print(f"  [scan] broad-scan cache folded in: +{len(by_id)-n0}")

    tariff = [m for m in by_id.values() if TARIFF_RE.search(m.get("question") or "")]
    print(f"  [scan] tariff-regex question filter: {len(by_id)} -> {len(tariff)}")
    markets = dedup_markets(tariff)
    print(f"  [scan] ladder dedup: {len(tariff)} -> {len(markets)}")
    SCAN_JSON.write_text(json.dumps(markets, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[scan] {len(markets)} tariff markets -> {SCAN_JSON}")
    return markets


# ── clean ─────────────────────────────────────────────────────────────────────
def _auto_drop_reason(q: str) -> str | None:
    """Programmatic quality cull that needs no judgment call."""
    rule = structural_noise_rule(q)
    if rule is not None:
        return f"structural_noise:{rule}"
    if re.search(r'\bsay\b.{0,30}\btariff|\bmention\b.{0,20}\btariff|times\s+during', q, re.I):
        return "mention_market"          # word-count bingo, not a tariff event
    return None


def stage_clean(markets: list[dict]) -> list[dict]:
    manual: dict[str, str] = {}
    if REVIEW_JSON.exists():
        review = json.loads(REVIEW_JSON.read_text(encoding="utf-8"))
        manual = {r["market_id"]: r["reason"] for r in review.get("drop", [])}
    kept, dropped = [], []
    for m in markets:
        reason = _auto_drop_reason(m["question"]) or manual.get(m["market_id"])
        if reason:
            dropped.append({**m, "drop_reason": reason})
        else:
            kept.append(m)
    print(f"[clean] {len(markets)} -> kept {len(kept)}, dropped {len(dropped)} "
          f"({sum(1 for d in dropped if d['drop_reason'].startswith('structural'))} structural, "
          f"{sum(1 for d in dropped if d['drop_reason'] == 'mention_market')} mention-markets, "
          f"{sum(1 for d in dropped if not d['drop_reason'].startswith(('structural', 'mention')))} manual)")
    (RUN / "clean_kept.json").write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")
    (RUN / "clean_dropped.json").write_text(json.dumps(dropped, ensure_ascii=False, indent=1), encoding="utf-8")
    return kept


def stage_review(markets: list[dict]) -> None:
    """Dump the auto-clean survivors for manual (Claude) curation."""
    rows = []
    for m in markets:
        if _auto_drop_reason(m["question"]):
            continue
        rows.append({"market_id": m["market_id"], "event_id": m["event_id"],
                     "question": m["question"].strip(), "end": m["end_at"][:10],
                     "created": m["created_at"][:10]})
    out = RUN / "for_review.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[review] {len(rows)} questions -> {out}")
    print(f"          curate drops into {REVIEW_JSON} as "
          '{"drop": [{"market_id": ..., "reason": ...}]}')


# ── quote ─────────────────────────────────────────────────────────────────────
def stage_quote(kept: list[dict]) -> None:
    n = len(kept)
    gate_calls = -(-n // GATE_BATCH)
    map_calls = gate_calls                      # upper bound: every market passes
    pairs_ub = n * 3                            # tight mapping caps small worlds; ~3 assets/market
    committed = json.loads(COMMITTED_POLARITY.read_text(encoding="utf-8")) if COMMITTED_POLARITY.exists() else {}
    pol_calls_ub = -(-pairs_ub // BATCH_SIZE)
    # flash list price, mirrors ingest.label_polarity's preflight approach
    in_tok = (gate_calls + map_calls) * 2600 + pol_calls_ub * 2400
    out_tok = n * 60 + n * 260 + pairs_ub * 55
    cost = in_tok / 1e6 * 0.30 + out_tok / 1e6 * 2.50
    print("=" * 68)
    print("  PAID-RUN QUOTE (nothing has been sent)")
    print(f"  markets after clean       : {n}")
    print(f"  relevance-gate calls      : {gate_calls}  (batch {GATE_BATCH})")
    print(f"  tight-mapping calls       : <= {map_calls}  (only gate-passers are sent)")
    print(f"  polarity calls            : <= {pol_calls_ub}  (batch {BATCH_SIZE}; committed cache reused, {len(committed)} entries)")
    print(f"  TOTAL Gemini calls        : <= {gate_calls + map_calls + pol_calls_ub}")
    print(f"  est. cost (flash prices)  : <= ${cost:.2f}  (validation-retry ceiling x3: ${cost*3:.2f})")
    print("=" * 68)


# ── map (PAID): relevance gate WITHOUT the sentiment skip + tight mapping ────
def _catalog() -> list[IBTradableAsset]:
    syms: dict[str, tuple[str, str]] = {}
    try:
        r = httpx.get("https://www.sec.gov/files/company_tickers.json",
                      headers={"User-Agent": "research tariff_run"}, timeout=30)
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
    return [IBTradableAsset(symbol=s, asset_name=name, asset_class=cls,
                            primary_exchange="SMART",
                            stock_type="ETF" if cls == "etf" else "COMMON")
            for s, (name, cls) in syms.items()]


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


async def _gate_batch(gemini, reqs) -> list[dict]:
    """One relevance-gate call; split-retry on validation failure."""
    try:
        resp = await gemini.structured(
            system_prompt=GEMINI_RELEVANCE_GATE_PROMPT,
            payload=gemini_world_payload(reqs),
            response_model=RelevanceGateBatch,
            max_tokens=max(800, len(reqs) * 80),
            prefer_prompt_schema=True,
        )
        by_id = {d.request_id: d for d in resp.decisions}
        return [{"market_id": rid,
                 "question_relevance": float(by_id[rid].question_relevance),
                 "positive_sentiment": bool(by_id[rid].positive_sentiment),
                 "reason": by_id[rid].reason}
                for rid, _, _ in reqs if rid in by_id]
    except Exception as e:  # noqa: BLE001
        if len(reqs) <= 1:
            print(f"  [gate] dropped 1 market: {str(e)[:70]}")
            return []
        mid = len(reqs) // 2
        return await _gate_batch(gemini, reqs[:mid]) + await _gate_batch(gemini, reqs[mid:])


async def _map_batch(gemini, catalog, reqs, rel_by_id) -> list[dict]:
    """One tight-mapping call; split-retry on validation failure."""
    try:
        resp = await gemini.structured(
            system_prompt=GEMINI_TIGHT_MAPPING_PROMPT,
            payload=gemini_world_payload(reqs),
            response_model=TightAssetWorlds,
            max_tokens=max(1200, len(reqs) * 220),
            prefer_prompt_schema=True,
        )
        by_id = {w.request_id: w for w in resp.worlds}
        rows = []
        for rid, market, _ in reqs:
            if rid not in by_id:
                continue
            world = canonicalize_tight_gemini_world(
                by_id[rid], market, catalog, question_relevance=rel_by_id[rid])
            for a in world.assets:
                rows.append({"market_id": rid, "question": market.question,
                             "symbol": a.symbol,
                             "connection_strength": float(a.connection_strength or 0.0),
                             "reason": a.reason,
                             "world_size": len(world.assets)})
        return rows
    except Exception as e:  # noqa: BLE001
        if len(reqs) <= 1:
            print(f"  [map] dropped 1 market: {str(e)[:70]}")
            return []
        mid = len(reqs) // 2
        return (await _map_batch(gemini, catalog, reqs[:mid], rel_by_id)
                + await _map_batch(gemini, catalog, reqs[mid:], rel_by_id))


async def stage_map(kept: list[dict]) -> None:
    if WORLDS_JSON.exists():
        print("[map] cached"); return
    now = datetime.now(timezone.utc)
    reqs_all = [(m["market_id"], _source(m), now) for m in kept]
    gemini = GeminiClient()
    try:
        # Pass 1: relevance gate. NOTE: unlike ingest.world.build_gemini_asset_worlds,
        # the gate here is question_relevance >= floor ONLY. positive_sentiment is
        # RECORDED for the counterfactual but does not gate: polarity handles
        # direction downstream (-1 = trade the NO side).
        gate: list[dict] = []
        for i in range(0, len(reqs_all), GATE_BATCH):
            gate.extend(await _gate_batch(gemini, reqs_all[i:i + GATE_BATCH]))
            print(f"  [gate] {min(i+GATE_BATCH, len(reqs_all))}/{len(reqs_all)}  "
                  f"cost≈${gemini.estimated_cost_usd():.2f}", flush=True)
        GATE_JSON.write_text(json.dumps(gate, ensure_ascii=False, indent=1), encoding="utf-8")
        rel = {g["market_id"]: g["question_relevance"] for g in gate}
        passed = [r for r in reqs_all if rel.get(r[0], 0.0) >= QUESTION_RELEVANCE_FLOOR]
        n_neg = sum(1 for g in gate if g["question_relevance"] >= QUESTION_RELEVANCE_FLOOR
                    and not g["positive_sentiment"])
        print(f"[gate] passed {len(passed)}/{len(reqs_all)} at floor {QUESTION_RELEVANCE_FLOOR} "
              f"({n_neg} of them would have been discarded by the old sentiment skip)")

        # Pass 2: tight mapping for gate-passers.
        pairs: list[dict] = []
        catalog = ib_asset_catalog_index(_catalog())
        for i in range(0, len(passed), GATE_BATCH):
            pairs.extend(await _map_batch(gemini, catalog, passed[i:i + GATE_BATCH], rel))
            print(f"  [map] {min(i+GATE_BATCH, len(passed))}/{len(passed)}  pairs={len(pairs)}  "
                  f"cost≈${gemini.estimated_cost_usd():.2f}", flush=True)
        WORLDS_JSON.write_text(json.dumps(pairs, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[map] {len(pairs)} (market,symbol) pairs -> {WORLDS_JSON}  "
              f"(total spend ≈${gemini.estimated_cost_usd():.2f})")
    finally:
        await gemini.close()


# ── polarity (PAID) ──────────────────────────────────────────────────────────
async def stage_polarity() -> None:
    pairs_rows = json.loads(WORLDS_JSON.read_text(encoding="utf-8"))
    pairs = sorted({(r["question"], r["symbol"]) for r in pairs_rows
                    if r["connection_strength"] >= CONNECTION_FLOOR})
    committed = json.loads(COMMITTED_POLARITY.read_text(encoding="utf-8")) if COMMITTED_POLARITY.exists() else {}
    cache = json.loads(POLARITY_JSON.read_text(encoding="utf-8")) if POLARITY_JSON.exists() else {}
    for k, v in committed.items():          # reuse committed labels, never re-bill
        cache.setdefault(k, v)
    todo = [(q, s) for q, s in pairs if pair_hash(q, s) not in cache]
    print(f"[polarity] {len(pairs)} pairs, {len(todo)} to label")
    if todo:
        gemini = GeminiClient()
        try:
            for i in range(0, len(todo), BATCH_SIZE):
                batch = todo[i:i + BATCH_SIZE]
                try:
                    for rec in await label_batch(gemini, batch):
                        cache[pair_hash(rec["question"], rec["symbol"])] = rec
                except Exception as e:  # noqa: BLE001
                    print(f"  [polarity] batch {i} failed: {str(e)[:70]}")
                print(f"  [polarity] {min(i+BATCH_SIZE, len(todo))}/{len(todo)}  "
                      f"cost≈${gemini.estimated_cost_usd():.2f}", flush=True)
        finally:
            await gemini.close()
    POLARITY_JSON.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[polarity] -> {POLARITY_JSON}")


# ── probs / prices (FREE) ────────────────────────────────────────────────────
async def stage_probs(markets: list[dict]) -> dict:
    if PROBS_PKL.exists():
        print("[probs] cached"); return pickle.load(open(PROBS_PKL, "rb"))
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
            print(f"  [probs] {min(i+200, len(markets))}/{len(markets)}  have={len(probs)}", flush=True)
    pickle.dump(probs, open(PROBS_PKL, "wb"))
    print(f"[probs] {len(probs)} paths -> {PROBS_PKL}")
    return probs


def stage_prices(symbols: set[str]) -> dict:
    if PRICES_PKL.exists():
        print("[prices] cached"); return pickle.load(open(PRICES_PKL, "rb"))
    import yfinance as yf
    with open(ROOT / "data" / "prices.pkl", "rb") as f:
        prices: dict[str, list] = pickle.load(f)
    need = sorted((symbols | set(COMMON_ETFS)) - set(prices))
    print(f"  [prices] {len(prices)} already have; downloading {len(need)} new")
    for s in need:
        try:
            df = yf.download(s, start="2024-06-01", end="2026-07-01",
                             progress=False, auto_adjust=False)
            if df is None or df.empty:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            path = []
            for ts, row in df.iterrows():
                try:
                    t = pd.Timestamp(ts)
                    t = (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).normalize()
                    path.append((t, float(row["High"]), float(row["Low"]), float(row["Close"])))
                except Exception:  # noqa: BLE001
                    continue
            if path:
                prices[s] = path
        except Exception as e:  # noqa: BLE001
            print(f"  [prices] {s} failed: {str(e)[:50]}")
    pickle.dump(prices, open(PRICES_PKL, "wb"))
    print(f"[prices] {len(prices)} symbols -> {PRICES_PKL}")
    return prices


# ── features (FREE) ──────────────────────────────────────────────────────────
def _pair_polarity(question: str, symbol: str, cache: dict) -> tuple[int, str]:
    rec = cache.get(pair_hash(question, symbol))
    if rec is not None:
        return int(rec["polarity"]), "llm"
    pol, rules = explain_polarity(question)
    return pol, f"regex:{','.join(rules) or 'none'}"


def _closes(bars: list) -> list:
    return [(t, c) for (t, _o, _h, _l, c) in bars]


def _family(q: str) -> str:
    return "macro"      # tariff questions are macro-policy by construction


def stage_features(kept: list[dict], probs: dict, prices: dict) -> pd.DataFrame:
    pairs_rows = json.loads(WORLDS_JSON.read_text(encoding="utf-8"))
    cache = json.loads(POLARITY_JSON.read_text(encoding="utf-8")) if POLARITY_JSON.exists() else {}
    by_mid = {m["market_id"]: m for m in kept}
    spy = _closes(prices.get("SPY", []))
    recs, skipped = [], {"below_floor": 0, "polarity0": 0, "no_probs": 0,
                         "no_theta": 0, "no_feature": 0}
    for r in pairs_rows:
        m = by_mid.get(r["market_id"])
        if m is None:
            continue
        if r["connection_strength"] < CONNECTION_FLOOR:
            skipped["below_floor"] += 1; continue
        raw = probs.get(r["market_id"], [])
        if len(raw) < 2:
            skipped["no_probs"] += 1; continue
        pol, pol_src = _pair_polarity(r["question"], r["symbol"], cache)
        if pol == 0:
            skipped["polarity0"] += 1; continue
        t_theta = find_t_theta(effective_prob_path(raw, pol))
        if t_theta is None:
            skipped["no_theta"] += 1; continue
        rec = compute_features(
            market_id=r["market_id"], event_id=m.get("event_id", ""),
            symbol=r["symbol"], question=r["question"], archetype=_family(r["question"]),
            relevance=r["connection_strength"], world_size=r["world_size"],
            t0=pd.Timestamp(m["created_at"]).tz_convert("UTC"),
            t_e=pd.Timestamp(m["end_at"]).tz_convert("UTC"), t_theta=t_theta,
            prices=_closes(prices.get(r["symbol"], [])), probs=raw,
            spy_prices=spy, sector_etf_prices=spy, sector="Unknown",
        )
        if rec is None:
            skipped["no_feature"] += 1; continue
        rec["polarity"] = pol
        rec["polarity_source"] = pol_src
        recs.append(rec)
    df = pd.DataFrame(recs)
    if not df.empty:
        df["split"] = ["train" if pd.Timestamp(t).tz_convert("UTC")
                       < pd.Timestamp("2026-01-01", tz="UTC") else "test"
                       for t in df["t_theta"]]
        df.to_parquet(CANDS_PARQUET, engine="pyarrow", compression="snappy")
    print(f"[features] built {len(df)} tariff candidates (skipped {skipped}) -> {CANDS_PARQUET}")
    return df


# ── report (FREE) ────────────────────────────────────────────────────────────
def stage_report() -> None:
    kept = json.loads((RUN / "clean_kept.json").read_text(encoding="utf-8"))
    gate = json.loads(GATE_JSON.read_text(encoding="utf-8")) if GATE_JSON.exists() else []
    pairs = json.loads(WORLDS_JSON.read_text(encoding="utf-8")) if WORLDS_JSON.exists() else []
    base = pd.read_parquet(ROOT / "data" / "candidates.parquet")
    print("=" * 70)
    print("  TARIFF UNIVERSE — WHAT IT ADDS")
    print("=" * 70)
    print(f"  cleaned tariff markets sent to Gemini : {len(kept)}")
    if gate:
        gp = [g for g in gate if g["question_relevance"] >= QUESTION_RELEVANCE_FLOOR]
        neg = [g for g in gp if not g["positive_sentiment"]]
        print(f"  passed relevance gate (>= {QUESTION_RELEVANCE_FLOOR})       : {len(gp)}"
              f"   (old sentiment skip would have cut {len(neg)} of these)")
    if pairs:
        strong = [p for p in pairs if p["connection_strength"] >= CONNECTION_FLOOR]
        print(f"  mapped (market,symbol) pairs           : {len(pairs)} "
              f"({len(strong)} at connection >= {CONNECTION_FLOOR})")
    if CANDS_PARQUET.exists():
        df = pd.read_parquet(CANDS_PARQUET)
        new = df[~df["market_id"].isin(set(base["market_id"].astype(str)))]
        print(f"  valid candidate rows (theta + features): {len(df)} "
              f"({len(new)} from markets not in the current 1,293-row universe)")
        print(f"  train/test: {(df['split'] == 'train').sum()}/{(df['split'] == 'test').sum()}   "
              f"symbols: {df['symbol'].nunique()}   markets: {df['market_id'].nunique()}")
        print(f"  universe growth: {len(base)} -> {len(base) + len(new)} rows "
              f"(+{len(new) / len(base) * 100:.1f}%)")
    print("=" * 70)


# ── driver ───────────────────────────────────────────────────────────────────
async def main(argv: list[str]) -> int:
    stages = [a for a in argv if not a.startswith("-")] or ["scan", "clean", "review", "quote"]
    approve = "--approve" in argv
    markets = await stage_scan() if "scan" in stages or not SCAN_JSON.exists() \
        else json.loads(SCAN_JSON.read_text(encoding="utf-8"))
    kept = stage_clean(markets)
    if "review" in stages:
        stage_review(markets)
    if "quote" in stages:
        stage_quote(kept)
    for paid in ("map", "polarity"):
        if paid in stages and not approve:
            print(f"[{paid}] PAID stage: run `python tariff_run.py quote`, get an explicit OK, "
                  f"then re-run with --approve")
            return 1
    if "map" in stages:
        await stage_map(kept)
    if "polarity" in stages:
        await stage_polarity()
    probs = await stage_probs(kept) if "probs" in stages else (
        pickle.load(open(PROBS_PKL, "rb")) if PROBS_PKL.exists() else {})
    if "prices" in stages or "features" in stages:
        syms = {r["symbol"] for r in json.loads(WORLDS_JSON.read_text(encoding="utf-8"))} \
            if WORLDS_JSON.exists() else set()
        prices = stage_prices(syms)
    if "features" in stages:
        stage_features(kept, probs, prices)
    if "report" in stages:
        stage_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
