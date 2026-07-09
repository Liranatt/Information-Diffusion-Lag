"""Gemini question-labeling backfill (cheap, cached, incremental).

Labels every Polymarket question we have ever seen -- traded or not, kept or
killed by the relevance gate -- with a compact taxonomy for later learning:

  event_family        earnings / fed_rates / inflation_cpi / jobs_employment /
                      war_conflict / fda_regulatory / ...
  belligerents        us_iran / israel_iran / iran_gulf_periphery /
                      russia_ukraine / china_taiwan / ...
  macro_direction     easing / tightening / higher_number / lower_number / ...
  market_mover_scope  single_stock / sector / broad_market
  materiality         low / medium / high

Sources: candidates.parquet, historical_market_decisions (kept AND killed),
and the raw scan caches. Labels are cached by question hash in
{SCHEMA}.question_labels, so re-runs only pay for new questions.

Model: gemini-2.5-flash, 50 questions per call, bounded concurrency.

Usage:
    python label_questions.py            # backfill everything not yet labeled
    python label_questions.py --dry-run  # count what would be labeled, no calls
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Literal

import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv(ROOT / ".env")

# The labeling task is taxonomy, not judgment: the cheap model is the right model.
os.environ["GEMINI_MODEL"] = "gemini-2.5-flash"
# 2.5-flash uses thinkingBudget, not thinkingLevel; omit thinking config entirely.
os.environ["GEMINI_THINKING_LEVEL"] = "none"

from database.db_connection import connect
from database.backtesting.schema import SCHEMA
from LLM.gemini_client import GeminiClient

PROMPT_VERSION = "question-labels-v1"
# 25/batch with compact JSON keeps responses well inside the output window;
# 50/batch pretty-printed truncated and failed validation.
BATCH_SIZE = 25

MACRO_PAT = re.compile(
    r"\bfed\b|federal reserve|rate cut|rate hike|fomc|interest rate|\bcpi\b|inflation|"
    r"unemployment|nonfarm|payroll|jobs report|\bgdp\b|recession|powell|jobless",
    re.I,
)
WAR_PAT = re.compile(
    r"iran|israel|gaza|hezbollah|houthi|strait|gulf state|military|ceasefire|nuclear|"
    r"russia|ukraine|putin|zelensk|kyiv|moscow|crimea|taiwan", re.I,
)

TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}.question_labels (
    question_hash       TEXT PRIMARY KEY,
    question            TEXT NOT NULL,
    model_name          TEXT NOT NULL,
    prompt_version      TEXT NOT NULL,
    event_family        TEXT NOT NULL,
    belligerents        TEXT NOT NULL,
    macro_direction     TEXT NOT NULL,
    market_mover_scope  TEXT NOT NULL,
    materiality         TEXT NOT NULL,
    processed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

SYSTEM_PROMPT = """You label prediction-market questions with a fixed taxonomy for a
financial research dataset. For each question return exactly one label per field.

event_family: the economic event type.
belligerents: only for armed-conflict questions; which parties. 'iran_gulf_periphery'
means Iran versus Gulf states (Qatar/Kuwait/UAE/Saudi/Iraq/Bahrain). 'none' otherwise.
macro_direction: for macro-policy/data questions, the direction the question asks about
(easing = rate cuts / stimulus; tightening = hikes; higher_number / lower_number for
data prints like CPI or unemployment). 'none' if not macro.
market_mover_scope: if the event resolved YES, what does it plausibly move --
a single stock, a sector, or the broad market (indexes/rates/oil). 'none' if nothing.
materiality: how economically material the outcome is to the assets it would move.

Return one label object per input id. Be consistent and literal; do not invent ids.
Output MINIFIED JSON on a single line -- no indentation, no line breaks, no spaces
between tokens. The response must be complete and end with the closing brace."""


class QuestionLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    event_family: Literal[
        "earnings", "fed_rates", "inflation_cpi", "jobs_employment", "gdp_growth",
        "war_conflict", "geopolitics_other", "fda_regulatory", "elections_politics",
        "trade_tariffs", "energy_commodities", "crypto", "company_event",
        "sports_entertainment", "other",
    ]
    belligerents: Literal[
        "none", "us_iran", "israel_iran", "iran_gulf_periphery",
        "russia_ukraine", "china_taiwan", "other_conflict",
    ]
    macro_direction: Literal[
        "none", "easing", "tightening", "higher_number", "lower_number", "no_change",
    ]
    market_mover_scope: Literal["none", "single_stock", "sector", "broad_market"]
    materiality: Literal["low", "medium", "high"]


class QuestionLabelBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    labels: list[QuestionLabel] = Field(max_length=BATCH_SIZE)


def qhash(question: str) -> str:
    return hashlib.sha256(question.strip().lower().encode("utf-8")).hexdigest()


async def gather_questions() -> list[str]:
    questions: dict[str, str] = {}

    def add(q: object) -> None:
        q = str(q or "").strip()
        if len(q) >= 8:
            questions.setdefault(qhash(q), q)

    cand = pd.read_parquet(ROOT / "data" / "candidates.parquet")
    for q in cand["question"].dropna().unique():
        add(q)
    n_cand = len(questions)

    conn = await connect()
    try:
        rows = await conn.fetch(
            f"SELECT DISTINCT market_question FROM {SCHEMA}.historical_market_decisions"
        )
    finally:
        await conn.close()
    for r in rows:
        q = str(r["market_question"] or "")
        if MACRO_PAT.search(q) or WAR_PAT.search(q):
            add(q)
    n_db = len(questions)

    for cache_name in ["markets_cache_historical.json", "markets_cache.json"]:
        p = ROOT / "data" / cache_name
        if not p.exists():
            continue
        for m in json.loads(p.read_text(encoding="utf-8")):
            q = str(m.get("question") or "")
            if MACRO_PAT.search(q) or WAR_PAT.search(q):
                add(q)

    print(f"[gather] candidates={n_cand}  +db_macro_war={n_db - n_cand}  "
          f"+cache={len(questions) - n_db}  total unique={len(questions)}")
    return list(questions.values())


async def already_labeled(conn) -> set[str]:
    rows = await conn.fetch(f"SELECT question_hash FROM {SCHEMA}.question_labels")
    return {r["question_hash"] for r in rows}


async def label_batch(client: GeminiClient, batch: list[str]) -> list[tuple]:
    payload = {"questions": [{"id": i, "question": q} for i, q in enumerate(batch)]}
    result = await client.structured(
        system_prompt=SYSTEM_PROMPT,
        payload=payload,
        response_model=QuestionLabelBatch,
        max_tokens=8192,
        prefer_prompt_schema=True,
    )
    rows = []
    for lab in result.labels:
        if 0 <= lab.id < len(batch):
            q = batch[lab.id]
            rows.append((
                qhash(q), q, client.model_name, PROMPT_VERSION,
                lab.event_family, lab.belligerents, lab.macro_direction,
                lab.market_mover_scope, lab.materiality,
            ))
    return rows


async def main(dry_run: bool) -> None:
    questions = await gather_questions()

    conn = await connect()
    try:
        await conn.execute(TABLE_SQL)
        done = await already_labeled(conn)
    finally:
        await conn.close()

    todo = [q for q in questions if qhash(q) not in done]
    n_batches = (len(todo) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"[plan] already labeled={len(done)}  to label={len(todo)}  "
          f"batches={n_batches}  model=gemini-2.5-flash")
    if dry_run or not todo:
        return

    client = GeminiClient()
    ok_rows: list[tuple] = []
    failed = 0

    async def run_one(i: int, batch: list[str]) -> None:
        nonlocal failed
        try:
            rows = await label_batch(client, batch)
            ok_rows.extend(rows)
            if (i + 1) % 10 == 0 or i + 1 == n_batches:
                print(f"  [{i + 1}/{n_batches}] labeled so far: {len(ok_rows)}", flush=True)
        except Exception as error:  # noqa: BLE001 - keep the backfill alive
            failed += 1
            print(f"  batch {i + 1} FAILED: {str(error)[:160]}", flush=True)

    batches = [todo[i:i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
    await asyncio.gather(*(run_one(i, b) for i, b in enumerate(batches)))
    await client.close()

    if ok_rows:
        conn = await connect()
        try:
            await conn.executemany(
                f"""INSERT INTO {SCHEMA}.question_labels
                    (question_hash, question, model_name, prompt_version, event_family,
                     belligerents, macro_direction, market_mover_scope, materiality)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                    ON CONFLICT (question_hash) DO NOTHING""",
                ok_rows,
            )
        finally:
            await conn.close()

    prompt_tokens = sum(t.get("usage", {}).get("promptTokenCount", 0) for t in client.trace)
    output_tokens = sum(t.get("usage", {}).get("candidatesTokenCount", 0) for t in client.trace)
    est_cost = prompt_tokens / 1e6 * 0.30 + output_tokens / 1e6 * 2.50
    print(f"\n[done] labeled={len(ok_rows)}  failed_batches={failed}")
    print(f"[cost] prompt_tokens={prompt_tokens:,}  output_tokens={output_tokens:,}  "
          f"~${est_cost:.2f} at 2.5-flash rates")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
