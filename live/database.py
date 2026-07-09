"""Async storage layer for the live paper-trading pipeline.

Uses the same Postgres database and schema namespace as the backtest
(database/db_connection.py). Market/price/probability history is written into
the existing historical_* tables so nothing is duplicated (we are low on
space); only live *state* gets its own tables:

  live_tracked_markets   -- open Polymarket markets we monitor + their assets
  live_positions         -- open/closed paper positions with full cost audit
  live_orders            -- every order sent to IB
  live_equity_snapshots  -- hourly NAV curve (equity vs passive benchmark)

All access goes through one shared asyncpg pool.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from database.db_connection import create_pool
from database.backtesting.schema import SCHEMA

log = logging.getLogger("live.db")

LIVE_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}.live_tracked_markets (
    market_id       TEXT PRIMARY KEY,
    event_id        TEXT NOT NULL,
    question        TEXT NOT NULL,
    event_title     TEXT,
    yes_token_id    TEXT NOT NULL,
    condition_id    TEXT,
    created_at      TIMESTAMPTZ NOT NULL,
    end_at          TIMESTAMPTZ NOT NULL,
    t0_prob         DOUBLE PRECISION,
    is_earnings     BOOLEAN NOT NULL DEFAULT FALSE,
    status          TEXT NOT NULL DEFAULT 'tracking',
    assets          JSONB NOT NULL DEFAULT '[]'::JSONB,
    discovered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {SCHEMA}.live_positions (
    position_id     BIGSERIAL PRIMARY KEY,
    market_id       TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    question        TEXT NOT NULL,
    is_earnings     BOOLEAN NOT NULL DEFAULT FALSE,
    qty             INTEGER NOT NULL,
    entry_ts        TIMESTAMPTZ NOT NULL,
    entry_price     DOUBLE PRECISION NOT NULL,
    entry_prob      DOUBLE PRECISION,
    atr_pct         DOUBLE PRECISION,
    peak_ret        DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    position_size_pct DOUBLE PRECISION,
    benchmark_sell_qty DOUBLE PRECISION NOT NULL DEFAULT 0,
    entry_costs     DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    status          TEXT NOT NULL DEFAULT 'open',
    exit_ts         TIMESTAMPTZ,
    exit_price      DOUBLE PRECISION,
    exit_reason     TEXT,
    exit_costs      DOUBLE PRECISION,
    pnl             DOUBLE PRECISION,
    pnl_pct         DOUBLE PRECISION,
    t_e             TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_live_positions_status
    ON {SCHEMA}.live_positions(status);

CREATE TABLE IF NOT EXISTS {SCHEMA}.live_orders (
    order_id        BIGSERIAL PRIMARY KEY,
    ib_order_id     INTEGER,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol          TEXT NOT NULL,
    action          TEXT NOT NULL,
    qty             DOUBLE PRECISION NOT NULL,
    kind            TEXT NOT NULL,
    fill_price      DOUBLE PRECISION,
    status          TEXT NOT NULL,
    position_id     BIGINT,
    note            TEXT
);

CREATE TABLE IF NOT EXISTS {SCHEMA}.live_equity_snapshots (
    ts                  TIMESTAMPTZ PRIMARY KEY,
    equity              DOUBLE PRECISION NOT NULL,
    cash                DOUBLE PRECISION NOT NULL,
    benchmark_shares    DOUBLE PRECISION NOT NULL,
    benchmark_price     DOUBLE PRECISION,
    open_positions      INTEGER NOT NULL,
    passive_equity      DOUBLE PRECISION
);

-- Space/disk telemetry so DB growth is observable, not just pruned blindly.
CREATE TABLE IF NOT EXISTS {SCHEMA}.live_system_metrics (
    ts                  TIMESTAMPTZ PRIMARY KEY,
    db_size_bytes       BIGINT,
    disk_total_bytes    BIGINT,
    disk_used_bytes     BIGINT,
    disk_free_bytes     BIGINT
);

-- LLM (Gemini) spend accounting: one row per discovery run's client.
CREATE TABLE IF NOT EXISTS {SCHEMA}.live_api_costs (
    id                  BIGSERIAL PRIMARY KEY,
    ts                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    provider            TEXT NOT NULL DEFAULT 'gemini',
    model               TEXT,
    calls               INTEGER NOT NULL DEFAULT 0,
    prompt_tokens       BIGINT NOT NULL DEFAULT 0,
    completion_tokens   BIGINT NOT NULL DEFAULT 0,
    total_tokens        BIGINT NOT NULL DEFAULT 0,
    est_cost_usd        DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    note                TEXT
);
CREATE INDEX IF NOT EXISTS idx_live_api_costs_ts ON {SCHEMA}.live_api_costs(ts);

-- Restart-safe cadence markers. Without this, every container restart resets
-- in-memory tick_count and can trigger paid discovery again.
CREATE TABLE IF NOT EXISTS {SCHEMA}.live_runtime_state (
    key                 TEXT PRIMARY KEY,
    ts                  TIMESTAMPTZ,
    value               JSONB NOT NULL DEFAULT '{{}}'::JSONB,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Benchmark legs may be fractional (SPY/QQQ are fraction-eligible at IB).
ALTER TABLE {SCHEMA}.live_equity_snapshots
    ALTER COLUMN benchmark_shares TYPE DOUBLE PRECISION;
ALTER TABLE {SCHEMA}.live_positions
    ALTER COLUMN benchmark_sell_qty TYPE DOUBLE PRECISION;
ALTER TABLE {SCHEMA}.live_orders
    ALTER COLUMN qty TYPE DOUBLE PRECISION;

-- Real execution economics captured from IB fills (not the modeled formula):
-- commission = actual CommissionReport sum; reference_price = the mark we
-- decided at, so slippage = fill_price - reference_price.
ALTER TABLE {SCHEMA}.live_orders ADD COLUMN IF NOT EXISTS commission DOUBLE PRECISION;
ALTER TABLE {SCHEMA}.live_orders ADD COLUMN IF NOT EXISTS reference_price DOUBLE PRECISION;
"""


class LiveStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    @classmethod
    async def create(cls) -> "LiveStore":
        pool = await create_pool(min_size=1, max_size=5)
        store = cls(pool)
        await store.ensure_schema()
        return store

    async def close(self) -> None:
        await self.pool.close()

    async def ensure_schema(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(LIVE_SCHEMA_SQL)

    # ── Tracked markets ──────────────────────────────────────────────────

    async def upsert_tracked_market(self, market: dict[str, Any], assets: list[dict]) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {SCHEMA}.live_tracked_markets
                    (market_id, event_id, question, event_title, yes_token_id,
                     condition_id, created_at, end_at, is_earnings, assets)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
                    ON CONFLICT (market_id) DO UPDATE
                    SET assets = EXCLUDED.assets, updated_at = NOW()""",
                market["market_id"], market.get("event_id", ""), market["question"],
                market.get("event_title"), market["yes_token_id"],
                market.get("condition_id"),
                _dt(market["created_at"]), _dt(market["end_at"]),
                "beat quarterly earnings" in market["question"].lower()
                or "earnings" in [t.lower() for t in market.get("tags", [])],
                json.dumps(assets),
            )

    async def active_markets(self) -> list[dict]:
        """Markets still tracking (not resolved / closed by us)."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT * FROM {SCHEMA}.live_tracked_markets
                    WHERE status = 'tracking' ORDER BY end_at"""
            )
        out = []
        for r in rows:
            d = dict(r)
            d["assets"] = json.loads(d["assets"]) if isinstance(d["assets"], str) else d["assets"]
            out.append(d)
        return out

    async def set_market_status(self, market_id: str, status: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"UPDATE {SCHEMA}.live_tracked_markets SET status=$2, updated_at=NOW() "
                f"WHERE market_id=$1", market_id, status,
            )

    async def set_t0_prob(self, market_id: str, prob: float) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"UPDATE {SCHEMA}.live_tracked_markets SET t0_prob=$2, updated_at=NOW() "
                f"WHERE market_id=$1 AND t0_prob IS NULL", market_id, prob,
            )

    async def repair_t0_prob_baselines(self) -> int:
        """Fill missing market T0 probabilities from the nearest stored hourly point.

        Preference is first point at/after discovery; if there is no such point,
        fall back to the nearest earlier point. This repairs old tracked markets
        created before T0 capture was reliable.
        """
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                f"""WITH picked AS (
                        SELECT DISTINCT ON (m.market_id)
                               m.market_id, p.probability
                        FROM {SCHEMA}.live_tracked_markets m
                        JOIN {SCHEMA}.historical_probability_points p
                          ON p.market_id = m.market_id
                        WHERE m.t0_prob IS NULL
                          AND m.status = 'tracking'
                        ORDER BY m.market_id,
                                 CASE WHEN p.hour_ts >= m.discovered_at THEN 0 ELSE 1 END,
                                 ABS(EXTRACT(EPOCH FROM (p.hour_ts - m.discovered_at)))
                    )
                    UPDATE {SCHEMA}.live_tracked_markets m
                    SET t0_prob = picked.probability, updated_at = NOW()
                    FROM picked
                    WHERE picked.market_id = m.market_id"""
            )
        return _rowcount(result)

    async def tracked_symbols(self, benchmark: str) -> list[str]:
        """Every symbol we need data for: benchmark + open-position symbols +
        assets mapped to still-tracking markets."""
        symbols = {benchmark}
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT assets FROM {SCHEMA}.live_tracked_markets WHERE status='tracking'"
            )
            for r in rows:
                assets = json.loads(r["assets"]) if isinstance(r["assets"], str) else r["assets"]
                symbols.update(a["symbol"] for a in assets)
            open_rows = await conn.fetch(
                f"SELECT DISTINCT symbol FROM {SCHEMA}.live_positions WHERE status='open'"
            )
            symbols.update(r["symbol"] for r in open_rows)
        return sorted(symbols)

    # ── Probability points (reuse historical table) ──────────────────────

    async def record_prob_points(
        self, market_id: str, yes_token_id: str, points: list[tuple[datetime, float]]
    ) -> int:
        if not points:
            return 0
        now = datetime.now(timezone.utc)
        async with self.pool.acquire() as conn:
            result = await conn.executemany(
                f"""INSERT INTO {SCHEMA}.historical_probability_points
                    (market_id, yes_token_id, hour_ts, source_ts, available_at, probability)
                    VALUES ($1,$2,$3,$4,$5,$6)
                    ON CONFLICT (market_id, hour_ts) DO NOTHING""",
                [(market_id, yes_token_id, ts, ts, now, p) for ts, p in points],
            )
        return len(points)

    async def latest_prob(self, market_id: str) -> float | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""SELECT probability FROM {SCHEMA}.historical_probability_points
                    WHERE market_id=$1 ORDER BY hour_ts DESC LIMIT 1""", market_id,
            )
        return float(row["probability"]) if row else None

    async def daily_prob_closes(self, market_id: str) -> list[tuple[datetime, float]]:
        """One point per UTC day (last observation <= 20:00 UTC), mirroring the
        backtest's data_loader.load_probs_from_db sampling."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT DISTINCT ON ((hour_ts AT TIME ZONE 'UTC')::date)
                        (hour_ts AT TIME ZONE 'UTC')::date AS d, probability
                    FROM {SCHEMA}.historical_probability_points
                    WHERE market_id=$1
                      AND EXTRACT(HOUR FROM hour_ts AT TIME ZONE 'UTC') <= 20
                    ORDER BY (hour_ts AT TIME ZONE 'UTC')::date, hour_ts DESC""",
                market_id,
            )
        return [(r["d"], float(r["probability"])) for r in rows]

    # ── Price bars (reuse historical table) ──────────────────────────────

    async def upsert_bars(self, symbol: str, resolution: str, bars: list[dict]) -> int:
        if not bars:
            return 0
        async with self.pool.acquire() as conn:
            await conn.executemany(
                f"""INSERT INTO {SCHEMA}.historical_price_bars
                    (symbol, resolution, ts, open, high, low, close, volume)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                    ON CONFLICT (symbol, resolution, ts) DO UPDATE
                    SET open=EXCLUDED.open, high=EXCLUDED.high,
                        low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume""",
                [(symbol, resolution, b["ts"], b["open"], b["high"], b["low"],
                  b["close"], float(b.get("volume") or 0.0)) for b in bars],
            )
        return len(bars)

    async def daily_bars(self, symbol: str, lookback_days: int) -> list[dict]:
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT ts, open, high, low, close FROM {SCHEMA}.historical_price_bars
                    WHERE symbol=$1 AND resolution='1d' AND ts >= $2 ORDER BY ts""",
                symbol, since,
            )
        return [dict(r) for r in rows]

    async def latest_close(self, symbol: str) -> float | None:
        """Most recent close at any resolution (hourly wins when fresher)."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""SELECT close FROM {SCHEMA}.historical_price_bars
                    WHERE symbol=$1 ORDER BY ts DESC LIMIT 1""", symbol,
            )
        return float(row["close"]) if row else None

    async def close_at(self, symbol: str, ts: datetime) -> float | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""SELECT close FROM {SCHEMA}.historical_price_bars
                    WHERE symbol=$1 AND ts <= $2 ORDER BY ts DESC LIMIT 1""",
                symbol, ts,
            )
        return float(row["close"]) if row else None

    async def close_near(self, symbol: str, ts: datetime) -> float | None:
        """Baseline close near ts. Prefer no-lookahead (<= ts), then fallback after ts.

        The fallback is used only when old live rows do not have a pre-discovery
        bar yet; it keeps T0 diagnostics from silently becoming blank/zero.
        """
        before = await self.close_at(symbol, ts)
        if before is not None:
            return before
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""SELECT close FROM {SCHEMA}.historical_price_bars
                    WHERE symbol=$1 AND ts > $2 ORDER BY ts LIMIT 1""",
                symbol, ts,
            )
        return float(row["close"]) if row else None

    # ── Positions / orders / equity ──────────────────────────────────────

    async def open_positions(self) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM {SCHEMA}.live_positions WHERE status='open' ORDER BY entry_ts"
            )
        return [dict(r) for r in rows]

    async def insert_position(self, pos: dict[str, Any]) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""INSERT INTO {SCHEMA}.live_positions
                    (market_id, symbol, question, is_earnings, qty, entry_ts, entry_price,
                     entry_prob, atr_pct, position_size_pct, benchmark_sell_qty,
                     entry_costs, t_e)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                    RETURNING position_id""",
                pos["market_id"], pos["symbol"], pos["question"], pos["is_earnings"],
                pos["qty"], pos["entry_ts"], pos["entry_price"], pos.get("entry_prob"),
                pos.get("atr_pct"), pos.get("position_size_pct"),
                pos.get("benchmark_sell_qty", 0), pos.get("entry_costs", 0.0), pos["t_e"],
            )
        return int(row["position_id"])

    async def update_peak(self, position_id: int, peak_ret: float) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"UPDATE {SCHEMA}.live_positions SET peak_ret=$2 WHERE position_id=$1",
                position_id, peak_ret,
            )

    async def close_position(self, position_id: int, *, exit_ts: datetime,
                             exit_price: float, exit_reason: str, exit_costs: float,
                             pnl: float, pnl_pct: float) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""UPDATE {SCHEMA}.live_positions
                    SET status='closed', exit_ts=$2, exit_price=$3, exit_reason=$4,
                        exit_costs=$5, pnl=$6, pnl_pct=$7
                    WHERE position_id=$1""",
                position_id, exit_ts, exit_price, exit_reason, exit_costs, pnl, pnl_pct,
            )

    async def realized_trades(self, limit: int = 50) -> list[dict]:
        """Latest closed trades, oldest-first, for half-Kelly sizing."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT pnl, pnl_pct FROM {SCHEMA}.live_positions
                    WHERE status='closed' ORDER BY exit_ts DESC LIMIT $1""", limit,
            )
        return [dict(r) for r in reversed(rows)]

    async def record_order(self, *, ib_order_id: int | None, symbol: str, action: str,
                           qty: float, kind: str, fill_price: float | None, status: str,
                           position_id: int | None = None, note: str = "",
                           commission: float | None = None,
                           reference_price: float | None = None) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {SCHEMA}.live_orders
                    (ib_order_id, symbol, action, qty, kind, fill_price, status,
                     position_id, note, commission, reference_price)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)""",
                ib_order_id, symbol, action, qty, kind, fill_price, status,
                position_id, note, commission, reference_price,
            )

    async def snapshot_equity(self, *, equity: float, cash: float, benchmark_shares: float,
                              benchmark_price: float | None, open_positions: int,
                              passive_equity: float | None) -> None:
        ts = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {SCHEMA}.live_equity_snapshots
                    (ts, equity, cash, benchmark_shares, benchmark_price,
                     open_positions, passive_equity)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)
                    ON CONFLICT (ts) DO UPDATE
                    SET equity=EXCLUDED.equity, cash=EXCLUDED.cash,
                        benchmark_shares=EXCLUDED.benchmark_shares,
                        benchmark_price=EXCLUDED.benchmark_price,
                        open_positions=EXCLUDED.open_positions,
                        passive_equity=EXCLUDED.passive_equity""",
                ts, equity, cash, benchmark_shares, benchmark_price,
                open_positions, passive_equity,
            )

    # ── Runtime cadence state ───────────────────────────────────────────

    async def runtime_ts(self, key: str) -> datetime | None:
        async with self.pool.acquire() as conn:
            ts = await conn.fetchval(
                f"SELECT ts FROM {SCHEMA}.live_runtime_state WHERE key=$1", key,
            )
        return ts

    async def should_run_runtime_event(self, key: str, interval: timedelta,
                                       now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        last = await self.runtime_ts(key)
        return last is None or (now - last) >= interval

    async def mark_runtime_event(self, key: str, ts: datetime | None = None,
                                 value: dict | None = None) -> None:
        ts = ts or datetime.now(timezone.utc)
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {SCHEMA}.live_runtime_state (key, ts, value, updated_at)
                    VALUES ($1,$2,$3::jsonb,NOW())
                    ON CONFLICT (key) DO UPDATE
                    SET ts=EXCLUDED.ts, value=EXCLUDED.value, updated_at=NOW()""",
                key, ts, json.dumps(value or {}),
            )

    # ── Retention (we are low on space) ──────────────────────────────────

    async def prune_stale(self, *, tracked_symbols: list[str],
                          bar_retention_days: int, prob_retention_days: int) -> None:
        bar_cutoff = datetime.now(timezone.utc) - timedelta(days=bar_retention_days)
        prob_cutoff = datetime.now(timezone.utc) - timedelta(days=prob_retention_days)
        async with self.pool.acquire() as conn:
            deleted_bars = await conn.execute(
                f"""DELETE FROM {SCHEMA}.historical_price_bars
                    WHERE resolution='1h' AND ts < $1
                      AND NOT (symbol = ANY($2::text[]))""",
                bar_cutoff, tracked_symbols,
            )
            deleted_probs = await conn.execute(
                f"""DELETE FROM {SCHEMA}.historical_probability_points p
                    USING {SCHEMA}.live_tracked_markets m
                    WHERE p.market_id = m.market_id
                      AND m.status IN ('resolved', 'dropped')
                      AND m.end_at < $1""",
                prob_cutoff,
            )
        log.info("prune: %s hourly bars, %s prob points", deleted_bars, deleted_probs)

    # ── System telemetry (DB size + disk) ────────────────────────────────

    async def record_system_metrics(self, *, disk_path: str = "/app") -> dict:
        """Snapshot DB size + disk usage of the host partition backing disk_path.

        disk_path defaults to /app, which is the bind-mounted repo -- so the
        free/total figures reflect the host filesystem, not the container's.
        """
        import shutil
        usage = shutil.disk_usage(disk_path)
        ts = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        async with self.pool.acquire() as conn:
            db_size = await conn.fetchval("SELECT pg_database_size(current_database())")
            await conn.execute(
                f"""INSERT INTO {SCHEMA}.live_system_metrics
                    (ts, db_size_bytes, disk_total_bytes, disk_used_bytes, disk_free_bytes)
                    VALUES ($1,$2,$3,$4,$5)
                    ON CONFLICT (ts) DO UPDATE
                    SET db_size_bytes=EXCLUDED.db_size_bytes,
                        disk_total_bytes=EXCLUDED.disk_total_bytes,
                        disk_used_bytes=EXCLUDED.disk_used_bytes,
                        disk_free_bytes=EXCLUDED.disk_free_bytes""",
                ts, int(db_size), int(usage.total), int(usage.used), int(usage.free),
            )
        metrics = {
            "db_size_bytes": int(db_size),
            "disk_total_bytes": int(usage.total),
            "disk_free_bytes": int(usage.free),
        }
        log.info("system: db=%.1fGB disk_free=%.1fGB",
                 metrics["db_size_bytes"] / 1e9, metrics["disk_free_bytes"] / 1e9)
        return metrics


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _rowcount(command_tag: str) -> int:
    try:
        return int(command_tag.split()[-1])
    except (ValueError, IndexError):
        return 0
