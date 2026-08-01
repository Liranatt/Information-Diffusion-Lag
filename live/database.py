"""Async storage layer for the live paper-trading pipeline.

Uses the same Postgres database and schema namespace as the backtest
(database/db_connection.py). Market/price/probability history is written into
the existing historical_* tables so nothing is duplicated (we are low on
space); only live *state* gets its own tables:

  live_tracked_markets   -- open Polymarket markets we monitor + their assets
  live_positions         -- open/closed paper positions with full cost audit
  live_orders            -- every order sent to IB
  live_broker_account_cache -- latest account values copied verbatim from IB
  live_broker_positions_cache -- latest position facts copied verbatim from IB
  live_execution_fills   -- immutable execution/fill facts reported by IB
  live_equity_snapshots  -- hourly NAV curve (equity vs passive benchmark)

All access goes through one shared asyncpg pool.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from database.db_connection import create_pool
from database.backtesting.schema import SCHEMA
from .performance import passive_equity

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
    pnl_source      TEXT NOT NULL DEFAULT 'legacy_model',
    pnl_verified    BOOLEAN NOT NULL DEFAULT FALSE,
    entry_costs_verified BOOLEAN NOT NULL DEFAULT FALSE,
    operation_id    TEXT,
    metadata_source TEXT NOT NULL DEFAULT 'strategy_entry',
    recovered_from_position_id BIGINT,
    broker_qty      DOUBLE PRECISION,
    broker_observed_at TIMESTAMPTZ,
    broker_state    TEXT,
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
    note            TEXT,
    perm_id         INTEGER,
    order_ref       TEXT,
    operation_id    TEXT
);

-- The DB is a cache/audit trail; these values are copied from IB and are never
-- used to overrule the broker during execution.
CREATE TABLE IF NOT EXISTS {SCHEMA}.live_broker_account_cache (
    cache_key           TEXT PRIMARY KEY DEFAULT 'primary',
    observed_at         TIMESTAMPTZ NOT NULL,
    net_liquidation     DOUBLE PRECISION,
    total_cash_value    DOUBLE PRECISION,
    buying_power        DOUBLE PRECISION,
    available_funds     DOUBLE PRECISION,
    gross_position_value DOUBLE PRECISION,
    settled_cash        DOUBLE PRECISION,
    accrued_cash        DOUBLE PRECISION,
    init_margin_req     DOUBLE PRECISION,
    maint_margin_req    DOUBLE PRECISION,
    excess_liquidity    DOUBLE PRECISION,
    full_init_margin_req DOUBLE PRECISION,
    full_maint_margin_req DOUBLE PRECISION,
    lookahead_excess_liquidity DOUBLE PRECISION,
    leverage            DOUBLE PRECISION,
    source              TEXT NOT NULL DEFAULT 'IB'
);

CREATE TABLE IF NOT EXISTS {SCHEMA}.live_broker_positions_cache (
    symbol              TEXT PRIMARY KEY,
    observed_at         TIMESTAMPTZ NOT NULL,
    qty                 DOUBLE PRECISION NOT NULL,
    market_price        DOUBLE PRECISION,
    market_value        DOUBLE PRECISION,
    avg_cost            DOUBLE PRECISION,
    unrealized_pnl      DOUBLE PRECISION,
    realized_pnl        DOUBLE PRECISION,
    source              TEXT NOT NULL DEFAULT 'IB'
);

CREATE TABLE IF NOT EXISTS {SCHEMA}.live_execution_fills (
    exec_id             TEXT PRIMARY KEY,
    ib_order_id         INTEGER,
    symbol              TEXT NOT NULL,
    action              TEXT NOT NULL,
    exec_ts             TIMESTAMPTZ,
    shares              DOUBLE PRECISION NOT NULL,
    price               DOUBLE PRECISION NOT NULL,
    exchange            TEXT,
    commission          DOUBLE PRECISION,
    realized_pnl        DOUBLE PRECISION,
    perm_id             INTEGER,
    account             TEXT,
    order_ref           TEXT,
    kind                TEXT,
    position_id         BIGINT,
    operation_id        TEXT,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source              TEXT NOT NULL DEFAULT 'IB'
);

CREATE TABLE IF NOT EXISTS {SCHEMA}.live_equity_snapshots (
    ts                  TIMESTAMPTZ PRIMARY KEY,
    equity              DOUBLE PRECISION NOT NULL,
    cash                DOUBLE PRECISION NOT NULL,
    benchmark_shares    DOUBLE PRECISION NOT NULL,
    benchmark_price     DOUBLE PRECISION,
    open_positions      INTEGER NOT NULL,
    passive_equity      DOUBLE PRECISION,
    source              TEXT NOT NULL DEFAULT 'legacy',
    broker_observed_at  TIMESTAMPTZ,
    benchmark_observed_at TIMESTAMPTZ,
    baseline_key        TEXT,
    baseline_verified   BOOLEAN,
    cumulative_cash_flows DOUBLE PRECISION NOT NULL DEFAULT 0.0
);

-- Performance is measured from an explicit, immutable starting point.  Cash
-- flows are applied to both the account and passive benchmark at the SPY price
-- observed when the flow occurred, so deposits cannot masquerade as alpha.
CREATE TABLE IF NOT EXISTS {SCHEMA}.live_performance_baselines (
    baseline_key        TEXT PRIMARY KEY DEFAULT 'primary',
    start_ts            TIMESTAMPTZ NOT NULL,
    start_nav           DOUBLE PRECISION NOT NULL,
    benchmark_symbol    TEXT NOT NULL,
    benchmark_start_price DOUBLE PRECISION NOT NULL,
    source              TEXT NOT NULL,
    verified            BOOLEAN NOT NULL DEFAULT FALSE,
    note                TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {SCHEMA}.live_account_cash_flows (
    flow_id             BIGSERIAL PRIMARY KEY,
    flow_ts             TIMESTAMPTZ NOT NULL,
    amount              DOUBLE PRECISION NOT NULL,
    flow_type           TEXT NOT NULL,
    benchmark_price     DOUBLE PRECISION NOT NULL,
    external_ref        TEXT UNIQUE,
    source              TEXT NOT NULL DEFAULT 'IB',
    note                TEXT,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {SCHEMA}.live_reconciliation_events (
    reconciliation_id   BIGSERIAL PRIMARY KEY,
    observed_at         TIMESTAMPTZ NOT NULL,
    severity            TEXT NOT NULL,
    symbol              TEXT,
    event_type          TEXT NOT NULL,
    broker_value        DOUBLE PRECISION,
    database_value      DOUBLE PRECISION,
    details             JSONB NOT NULL DEFAULT '{{}}'::JSONB,
    source_of_truth     TEXT NOT NULL DEFAULT 'IB',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Policy truth is deliberately separate from broker truth.  A row here says
-- the deterministic policy required an exit while IB still owned the shares;
-- it never pretends that a fill occurred and never closes live_positions.
CREATE TABLE IF NOT EXISTS {SCHEMA}.live_policy_exit_audit (
    position_id         BIGINT PRIMARY KEY,
    symbol              TEXT NOT NULL,
    market_id           TEXT,
    qty                 DOUBLE PRECISION NOT NULL,
    entry_ts            TIMESTAMPTZ NOT NULL,
    entry_price         DOUBLE PRECISION NOT NULL,
    expected_exit_reason TEXT NOT NULL,
    triggered_at        TIMESTAMPTZ NOT NULL,
    first_executable_at TIMESTAMPTZ,
    model_exit_price    DOUBLE PRECISION,
    model_price_source  TEXT,
    expected_gross_pnl  DOUBLE PRECISION,
    expected_net_before_exit_cost DOUBLE PRECISION,
    current_ib_unrealized_pnl DOUBLE PRECISION,
    post_exit_opportunity_delta DOUBLE PRECISION,
    actual_ib_status    TEXT NOT NULL DEFAULT 'still_open_at_IB',
    calculation_note    TEXT,
    details             JSONB NOT NULL DEFAULT '{{}}'::JSONB,
    first_observed_at   TIMESTAMPTZ NOT NULL,
    last_observed_at    TIMESTAMPTZ NOT NULL,
    calculated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Latest deterministic target is an explanatory snapshot only.  It is not an
-- order queue and the trader never consumes it.
CREATE TABLE IF NOT EXISTS {SCHEMA}.live_policy_target_snapshots (
    snapshot_key        TEXT PRIMARY KEY DEFAULT 'latest',
    as_of               TIMESTAMPTZ NOT NULL,
    policy              JSONB NOT NULL,
    retained_positions  JSONB NOT NULL DEFAULT '[]'::JSONB,
    required_exits      JSONB NOT NULL DEFAULT '[]'::JSONB,
    replacement_candidates JSONB NOT NULL DEFAULT '[]'::JSONB,
    target_positions    JSONB NOT NULL DEFAULT '[]'::JSONB,
    benchmark_residual  DOUBLE PRECISION,
    method              TEXT NOT NULL,
    note                TEXT,
    calculated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Future counterfactuals must use the policy that was actually active at the
-- time, not whichever policy happens to be deployed when an audit is run.
CREATE TABLE IF NOT EXISTS {SCHEMA}.live_policy_versions (
    policy_hash         TEXT PRIMARY KEY,
    policy              JSONB NOT NULL,
    experiment          TEXT NOT NULL,
    benchmark           TEXT NOT NULL,
    first_seen_at       TIMESTAMPTZ NOT NULL,
    last_seen_at        TIMESTAMPTZ NOT NULL,
    source              TEXT NOT NULL DEFAULT 'live_tick'
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
ALTER TABLE {SCHEMA}.live_orders ADD COLUMN IF NOT EXISTS requested_qty DOUBLE PRECISION;
ALTER TABLE {SCHEMA}.live_equity_snapshots
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE {SCHEMA}.live_positions ADD COLUMN IF NOT EXISTS pnl_source TEXT NOT NULL DEFAULT 'legacy_model';
ALTER TABLE {SCHEMA}.live_positions ADD COLUMN IF NOT EXISTS pnl_verified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE {SCHEMA}.live_positions ADD COLUMN IF NOT EXISTS entry_costs_verified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE {SCHEMA}.live_positions ADD COLUMN IF NOT EXISTS operation_id TEXT;
ALTER TABLE {SCHEMA}.live_positions ADD COLUMN IF NOT EXISTS metadata_source TEXT NOT NULL DEFAULT 'strategy_entry';
ALTER TABLE {SCHEMA}.live_positions ADD COLUMN IF NOT EXISTS recovered_from_position_id BIGINT;
ALTER TABLE {SCHEMA}.live_positions ADD COLUMN IF NOT EXISTS broker_qty DOUBLE PRECISION;
ALTER TABLE {SCHEMA}.live_positions ADD COLUMN IF NOT EXISTS broker_observed_at TIMESTAMPTZ;
ALTER TABLE {SCHEMA}.live_positions ADD COLUMN IF NOT EXISTS broker_state TEXT;
ALTER TABLE {SCHEMA}.live_orders ADD COLUMN IF NOT EXISTS perm_id INTEGER;
ALTER TABLE {SCHEMA}.live_orders ADD COLUMN IF NOT EXISTS order_ref TEXT;
ALTER TABLE {SCHEMA}.live_orders ADD COLUMN IF NOT EXISTS operation_id TEXT;
ALTER TABLE {SCHEMA}.live_execution_fills ADD COLUMN IF NOT EXISTS perm_id INTEGER;
ALTER TABLE {SCHEMA}.live_execution_fills ADD COLUMN IF NOT EXISTS account TEXT;
ALTER TABLE {SCHEMA}.live_execution_fills ADD COLUMN IF NOT EXISTS order_ref TEXT;
ALTER TABLE {SCHEMA}.live_execution_fills ADD COLUMN IF NOT EXISTS kind TEXT;
ALTER TABLE {SCHEMA}.live_execution_fills ADD COLUMN IF NOT EXISTS position_id BIGINT;
ALTER TABLE {SCHEMA}.live_execution_fills ADD COLUMN IF NOT EXISTS operation_id TEXT;
ALTER TABLE {SCHEMA}.live_broker_account_cache ADD COLUMN IF NOT EXISTS settled_cash DOUBLE PRECISION;
ALTER TABLE {SCHEMA}.live_broker_account_cache ADD COLUMN IF NOT EXISTS accrued_cash DOUBLE PRECISION;
ALTER TABLE {SCHEMA}.live_broker_account_cache ADD COLUMN IF NOT EXISTS init_margin_req DOUBLE PRECISION;
ALTER TABLE {SCHEMA}.live_broker_account_cache ADD COLUMN IF NOT EXISTS maint_margin_req DOUBLE PRECISION;
ALTER TABLE {SCHEMA}.live_broker_account_cache ADD COLUMN IF NOT EXISTS excess_liquidity DOUBLE PRECISION;
ALTER TABLE {SCHEMA}.live_broker_account_cache ADD COLUMN IF NOT EXISTS full_init_margin_req DOUBLE PRECISION;
ALTER TABLE {SCHEMA}.live_broker_account_cache ADD COLUMN IF NOT EXISTS full_maint_margin_req DOUBLE PRECISION;
ALTER TABLE {SCHEMA}.live_broker_account_cache ADD COLUMN IF NOT EXISTS lookahead_excess_liquidity DOUBLE PRECISION;
ALTER TABLE {SCHEMA}.live_broker_account_cache ADD COLUMN IF NOT EXISTS leverage DOUBLE PRECISION;
ALTER TABLE {SCHEMA}.live_equity_snapshots ADD COLUMN IF NOT EXISTS broker_observed_at TIMESTAMPTZ;
ALTER TABLE {SCHEMA}.live_equity_snapshots ADD COLUMN IF NOT EXISTS benchmark_observed_at TIMESTAMPTZ;
ALTER TABLE {SCHEMA}.live_equity_snapshots ADD COLUMN IF NOT EXISTS baseline_key TEXT;
ALTER TABLE {SCHEMA}.live_equity_snapshots ADD COLUMN IF NOT EXISTS baseline_verified BOOLEAN;
ALTER TABLE {SCHEMA}.live_equity_snapshots ADD COLUMN IF NOT EXISTS cumulative_cash_flows DOUBLE PRECISION NOT NULL DEFAULT 0.0;
CREATE INDEX IF NOT EXISTS idx_live_execution_fills_operation
    ON {SCHEMA}.live_execution_fills(operation_id);
CREATE INDEX IF NOT EXISTS idx_live_orders_operation
    ON {SCHEMA}.live_orders(operation_id);
CREATE INDEX IF NOT EXISTS idx_live_cash_flows_ts
    ON {SCHEMA}.live_account_cash_flows(flow_ts);
CREATE INDEX IF NOT EXISTS idx_live_reconciliation_observed
    ON {SCHEMA}.live_reconciliation_events(observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_live_policy_exit_triggered
    ON {SCHEMA}.live_policy_exit_audit(triggered_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_live_positions_one_open_recovery
    ON {SCHEMA}.live_positions(recovered_from_position_id)
    WHERE status='open' AND recovered_from_position_id IS NOT NULL;
UPDATE {SCHEMA}.live_orders SET requested_qty = qty WHERE requested_qty IS NULL;
UPDATE {SCHEMA}.live_positions
SET entry_costs_verified=TRUE
WHERE status='open' AND pnl_source='IB_execution';
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
            # Append-only: the first probability recorded for an (market, hour) is
            # what we knew at that available_at, so never overwrite it — the
            # backtest and the nightly rebuild treat this history as immutable.
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
        # Append-only into the shared historical table: never overwrite a bar the
        # backtest may already depend on. Daily bars use midnight-UTC ts (see
        # DataFetcher._bars_to_rows / market_data) so a re-fetch of the same
        # session collides on the primary key and is left untouched.
        async with self.pool.acquire() as conn:
            await conn.executemany(
                f"""INSERT INTO {SCHEMA}.historical_price_bars
                    (symbol, resolution, ts, open, high, low, close, volume)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                    ON CONFLICT (symbol, resolution, ts) DO NOTHING""",
                [(symbol, resolution, b["ts"], b["open"], b["high"], b["low"],
                  b["close"], float(b.get("volume") or 0.0)) for b in bars],
            )
        return len(bars)

    async def replace_session_bars(
        self, symbol: str, resolution: str, bars: list[dict]
    ) -> int:
        """Replace only explicitly supplied current-session bars.

        IB includes an in-progress daily/hourly bar in historical responses.
        Those rows are not immutable facts until their interval closes.  The
        fetcher routes only current-session timestamps here; older finalized
        history continues through the append-only ``upsert_bars`` path.
        """
        if not bars:
            return 0
        timestamps = [bar["ts"] for bar in bars]
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    f"""DELETE FROM {SCHEMA}.historical_price_bars
                        WHERE symbol=$1 AND resolution=$2
                          AND ts = ANY($3::timestamptz[])""",
                    symbol, resolution, timestamps,
                )
                await conn.executemany(
                    f"""INSERT INTO {SCHEMA}.historical_price_bars
                        (symbol, resolution, ts, open, high, low, close, volume)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                    [(symbol, resolution, b["ts"], b["open"], b["high"], b["low"],
                      b["close"], float(b.get("volume") or 0.0)) for b in bars],
                )
        return len(bars)

    async def delete_session_bars(
        self, symbol: str, resolution: str, timestamps: list[datetime]
    ) -> int:
        """Remove exact in-progress timestamps accidentally cached as final."""
        if not timestamps:
            return 0
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                f"""DELETE FROM {SCHEMA}.historical_price_bars
                    WHERE symbol=$1 AND resolution=$2
                      AND ts = ANY($3::timestamptz[])""",
                symbol, resolution, timestamps,
            )
        return _rowcount(result)

    async def daily_bars(self, symbol: str, lookback_days: int) -> list[dict]:
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        return await self.daily_bars_since(symbol, since)

    async def daily_bars_since(self, symbol: str, since: datetime) -> list[dict]:
        """Daily bars for `symbol` with ts >= `since` (ascending).

        The kernel replay needs the price window back to t_theta - 30d, which can
        be older than the fixed daily lookback, so callers pass an explicit start.
        """
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
                     entry_costs, entry_costs_verified, operation_id, pnl_source,
                     pnl_verified, metadata_source, t_e)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,TRUE,$13,
                            'IB_execution',TRUE,'strategy_entry',$14)
                    RETURNING position_id""",
                pos["market_id"], pos["symbol"], pos["question"], pos["is_earnings"],
                pos["qty"], pos["entry_ts"], pos["entry_price"], pos.get("entry_prob"),
                pos.get("atr_pct"), pos.get("position_size_pct"),
                pos.get("benchmark_sell_qty", 0), pos.get("entry_costs", 0.0),
                pos.get("operation_id"), pos["t_e"],
            )
        return int(row["position_id"])

    async def recover_broker_metadata_from_history(
        self,
        *,
        symbols: list[str],
        broker_positions: dict[str, dict[str, float]],
        observed_at: datetime,
        apply: bool = False,
    ) -> list[dict[str, Any]]:
        """Plan/apply metadata recovery for IB holdings absent from open DB state.

        Quantity, average cost and presence come exclusively from IB.  Market
        identity, question and resolution date are copied from the latest
        historical strategy row for that symbol.  Recovery never sends orders
        and never marks historical entry costs or P&L as verified.
        """
        results: list[dict[str, Any]] = []
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                for symbol in sorted(set(symbols)):
                    broker = broker_positions.get(symbol)
                    qty = float((broker or {}).get("qty") or 0.0)
                    if qty <= 1e-9:
                        results.append({"symbol": symbol, "status": "not_held_at_IB"})
                        continue
                    if abs(qty - round(qty)) > 1e-6:
                        results.append({
                            "symbol": symbol,
                            "status": "fractional_asset_requires_review",
                            "broker_qty": qty,
                        })
                        continue
                    existing = await conn.fetchrow(
                        f"""SELECT position_id FROM {SCHEMA}.live_positions
                            WHERE symbol=$1 AND status='open' LIMIT 1""",
                        symbol,
                    )
                    if existing:
                        results.append({
                            "symbol": symbol,
                            "status": "already_open",
                            "position_id": int(existing["position_id"]),
                        })
                        continue
                    source = await conn.fetchrow(
                        f"""SELECT * FROM {SCHEMA}.live_positions
                            WHERE symbol=$1 AND status='closed'
                            ORDER BY exit_ts DESC NULLS LAST, position_id DESC
                            LIMIT 1""",
                        symbol,
                    )
                    if not source:
                        results.append({
                            "symbol": symbol,
                            "status": "no_strategy_history",
                            "broker_qty": qty,
                        })
                        continue
                    plan = {
                        "symbol": symbol,
                        "status": "planned",
                        "broker_qty": qty,
                        "broker_avg_cost": float(broker.get("avg_cost") or 0.0),
                        "source_position_id": int(source["position_id"]),
                        "market_id": source["market_id"],
                        "t_e": source["t_e"],
                    }
                    if not apply:
                        results.append(plan)
                        continue
                    entry_price = float(broker.get("avg_cost") or source["entry_price"])
                    operation_id = f"recovered:{source['position_id']}:{int(observed_at.timestamp())}"
                    inserted = await conn.fetchrow(
                        f"""INSERT INTO {SCHEMA}.live_positions
                            (market_id, symbol, question, is_earnings, qty, entry_ts,
                             entry_price, entry_prob, atr_pct, peak_ret,
                             position_size_pct, benchmark_sell_qty, entry_costs,
                             entry_costs_verified, status, pnl_source, pnl_verified,
                             operation_id, metadata_source,
                             recovered_from_position_id, broker_qty,
                             broker_observed_at, broker_state, t_e)
                            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,0,0,FALSE,
                                    'open','IB_recovered_metadata',FALSE,$12,
                                    'IB_qty_plus_historical_strategy_metadata',$13,
                                    $16,$14,'recovered_metadata',$15)
                            RETURNING position_id""",
                        source["market_id"], symbol, source["question"],
                        source["is_earnings"], int(round(qty)), source["entry_ts"],
                        entry_price, source["entry_prob"], source["atr_pct"],
                        source["peak_ret"], source["position_size_pct"],
                        operation_id, source["position_id"], observed_at, source["t_e"],
                        qty,
                    )
                    plan["status"] = "recovered"
                    plan["position_id"] = int(inserted["position_id"])
                    results.append(plan)
        return results

    async def update_peak(self, position_id: int, peak_ret: float) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"UPDATE {SCHEMA}.live_positions SET peak_ret=$2 WHERE position_id=$1",
                position_id, peak_ret,
            )

    async def close_position(self, position_id: int, *, exit_ts: datetime,
                             exit_price: float, exit_reason: str, exit_costs: float,
                             pnl: float, pnl_pct: float,
                             pnl_source: str = "IB_execution") -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    f"""UPDATE {SCHEMA}.live_positions
                        SET status='closed', exit_ts=$2, exit_price=$3, exit_reason=$4,
                            exit_costs=$5, pnl=$6, pnl_pct=$7,
                            pnl_source=CASE
                                WHEN entry_costs_verified THEN $8
                                ELSE 'IB_exit_with_unverified_entry_costs'
                            END,
                            pnl_verified=entry_costs_verified
                        WHERE position_id=$1""",
                    position_id, exit_ts, exit_price, exit_reason, exit_costs, pnl, pnl_pct,
                    pnl_source,
                )
                await conn.execute(
                    f"""UPDATE {SCHEMA}.live_policy_exit_audit
                        SET actual_ib_status='executed_at_IB',
                            details=details || jsonb_build_object(
                                'actual_exit_ts',$2::text,
                                'actual_exit_price',$3,
                                'actual_exit_costs',$4,
                                'actual_pnl',$5
                            ),
                            last_observed_at=$2,
                            calculated_at=NOW()
                        WHERE position_id=$1""",
                    position_id, exit_ts, exit_price, exit_costs, pnl,
                )

    async def upsert_policy_exit_audit(
        self,
        *,
        position: dict[str, Any],
        reason: str,
        triggered_at: datetime,
        model_exit_price: float | None,
        model_price_source: str | None,
        observed_at: datetime,
        current_ib_unrealized_pnl: float | None = None,
        first_executable_at: datetime | None = None,
        actual_ib_status: str = "still_open_at_IB",
        details: dict[str, Any] | None = None,
    ) -> None:
        """Persist a missed/pending policy exit without fabricating an IB fill."""
        qty = float(position.get("qty") or 0.0)
        entry_price = float(position.get("entry_price") or 0.0)
        entry_costs = float(position.get("entry_costs") or 0.0)
        gross = (
            qty * (float(model_exit_price) - entry_price)
            if model_exit_price is not None else None
        )
        net_before_exit = gross - entry_costs if gross is not None else None
        opportunity_delta = (
            float(current_ib_unrealized_pnl) - net_before_exit
            if current_ib_unrealized_pnl is not None and net_before_exit is not None
            else None
        )
        note = (
            "Deterministic policy valuation only; no IB sell/fill is claimed. "
            "Expected net includes known entry costs but excludes hypothetical "
            "exit commission and slippage."
        )
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {SCHEMA}.live_policy_exit_audit
                    (position_id,symbol,market_id,qty,entry_ts,entry_price,
                     expected_exit_reason,triggered_at,first_executable_at,model_exit_price,
                     model_price_source,expected_gross_pnl,
                     expected_net_before_exit_cost,current_ib_unrealized_pnl,
                     post_exit_opportunity_delta,actual_ib_status,
                     calculation_note,details,first_observed_at,last_observed_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,
                            $15,$16,$17,$18::jsonb,$19,$19)
                    ON CONFLICT (position_id) DO UPDATE SET
                        qty=EXCLUDED.qty,
                        expected_exit_reason=EXCLUDED.expected_exit_reason,
                        triggered_at=LEAST(
                            {SCHEMA}.live_policy_exit_audit.triggered_at,
                            EXCLUDED.triggered_at
                        ),
                        first_executable_at=COALESCE(
                            {SCHEMA}.live_policy_exit_audit.first_executable_at,
                            EXCLUDED.first_executable_at
                        ),
                        model_exit_price=COALESCE(
                            EXCLUDED.model_exit_price,
                            {SCHEMA}.live_policy_exit_audit.model_exit_price
                        ),
                        model_price_source=COALESCE(
                            EXCLUDED.model_price_source,
                            {SCHEMA}.live_policy_exit_audit.model_price_source
                        ),
                        expected_gross_pnl=COALESCE(
                            EXCLUDED.expected_gross_pnl,
                            {SCHEMA}.live_policy_exit_audit.expected_gross_pnl
                        ),
                        expected_net_before_exit_cost=COALESCE(
                            EXCLUDED.expected_net_before_exit_cost,
                            {SCHEMA}.live_policy_exit_audit.expected_net_before_exit_cost
                        ),
                        current_ib_unrealized_pnl=COALESCE(
                            EXCLUDED.current_ib_unrealized_pnl,
                            {SCHEMA}.live_policy_exit_audit.current_ib_unrealized_pnl
                        ),
                        post_exit_opportunity_delta=COALESCE(
                            EXCLUDED.post_exit_opportunity_delta,
                            {SCHEMA}.live_policy_exit_audit.post_exit_opportunity_delta
                        ),
                        actual_ib_status=EXCLUDED.actual_ib_status,
                        calculation_note=EXCLUDED.calculation_note,
                        details={SCHEMA}.live_policy_exit_audit.details || EXCLUDED.details,
                        last_observed_at=EXCLUDED.last_observed_at,
                        calculated_at=NOW()""",
                int(position["position_id"]), position["symbol"],
                position.get("market_id"), qty, position["entry_ts"], entry_price,
                reason, triggered_at, first_executable_at,
                model_exit_price, model_price_source,
                gross, net_before_exit, current_ib_unrealized_pnl,
                opportunity_delta, actual_ib_status, note,
                json.dumps(details or {}), observed_at,
            )

    async def save_policy_target_snapshot(
        self,
        *,
        as_of: datetime,
        policy: dict[str, Any],
        retained_positions: list[dict[str, Any]],
        required_exits: list[dict[str, Any]],
        replacement_candidates: list[dict[str, Any]],
        target_positions: list[dict[str, Any]],
        benchmark_residual: float | None,
        method: str,
        note: str,
    ) -> None:
        """Save a read-only explanatory target; this table is never executed."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {SCHEMA}.live_policy_target_snapshots
                    (snapshot_key,as_of,policy,retained_positions,required_exits,
                     replacement_candidates,target_positions,benchmark_residual,
                     method,note)
                    VALUES ('latest',$1,$2::jsonb,$3::jsonb,$4::jsonb,$5::jsonb,
                            $6::jsonb,$7,$8,$9)
                    ON CONFLICT (snapshot_key) DO UPDATE SET
                        as_of=EXCLUDED.as_of,policy=EXCLUDED.policy,
                        retained_positions=EXCLUDED.retained_positions,
                        required_exits=EXCLUDED.required_exits,
                        replacement_candidates=EXCLUDED.replacement_candidates,
                        target_positions=EXCLUDED.target_positions,
                        benchmark_residual=EXCLUDED.benchmark_residual,
                        method=EXCLUDED.method,note=EXCLUDED.note,
                        calculated_at=NOW()""",
                as_of, json.dumps(policy), json.dumps(retained_positions),
                json.dumps(required_exits), json.dumps(replacement_candidates),
                json.dumps(target_positions), benchmark_residual, method, note,
            )

    async def record_policy_version(
        self,
        *,
        policy: dict[str, Any],
        experiment: str,
        benchmark: str,
        observed_at: datetime,
    ) -> str:
        """Persist the exact deterministic policy active for this tick."""
        canonical = json.dumps(policy, sort_keys=True, separators=(",", ":"))
        policy_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {SCHEMA}.live_policy_versions
                    (policy_hash,policy,experiment,benchmark,first_seen_at,last_seen_at)
                    VALUES ($1,$2::jsonb,$3,$4,$5,$5)
                    ON CONFLICT (policy_hash) DO UPDATE SET
                        last_seen_at=GREATEST(
                            {SCHEMA}.live_policy_versions.last_seen_at,
                            EXCLUDED.last_seen_at
                        )""",
                policy_hash, canonical, experiment, benchmark, observed_at,
            )
        return policy_hash

    async def position_exit_fill_summary(self, position_id: int) -> dict[str, float] | None:
        """Aggregate every executed exit slice for one cached position.

        A broker order can partially fill, be cancelled, and finish on a later
        tick under a new IB order id.  ``live_orders.qty`` is the quantity that
        actually executed, so summing all priced SELL/exit rows prevents the
        final retry from calculating P&L on only the last remainder.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""SELECT SUM(qty) AS qty,
                           SUM(qty * fill_price) / NULLIF(SUM(qty), 0) AS avg_price,
                           SUM(COALESCE(commission, 0)) AS commission
                    FROM {SCHEMA}.live_orders
                    WHERE position_id=$1 AND kind='exit' AND action='SELL'
                      AND fill_price IS NOT NULL AND qty > 0""",
                position_id,
            )
        if not row or row["qty"] is None or float(row["qty"]) <= 0:
            return None
        return {
            "qty": float(row["qty"]),
            "avg_price": float(row["avg_price"]),
            "commission": float(row["commission"] or 0.0),
        }

    async def realized_trades(self, limit: int = 50) -> list[dict]:
        """Latest closed trades, oldest-first, for half-Kelly sizing."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT pnl, pnl_pct FROM {SCHEMA}.live_positions
                    WHERE status='closed' AND pnl_verified=TRUE
                    ORDER BY exit_ts DESC LIMIT $1""", limit,
            )
        return [dict(r) for r in reversed(rows)]

    async def record_order(self, *, ib_order_id: int | None, symbol: str, action: str,
                           qty: float, kind: str, fill_price: float | None, status: str,
                           position_id: int | None = None, note: str = "",
                           commission: float | None = None,
                           reference_price: float | None = None,
                           requested_qty: float | None = None,
                           perm_id: int | None = None,
                           order_ref: str | None = None,
                           operation_id: str | None = None) -> None:
        """Persist one broker-order outcome.

        ``qty`` is the quantity that actually executed whenever ``fill_price``
        is present. ``requested_qty`` preserves the original order size for
        cancelled and partially-filled orders. Keeping those two quantities
        separate prevents an unfilled remainder from entering the cash or
        inventory ledger.
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {SCHEMA}.live_orders
                    (ib_order_id, symbol, action, qty, kind, fill_price, status,
                     position_id, note, commission, reference_price, requested_qty,
                     perm_id, order_ref, operation_id)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)""",
                ib_order_id, symbol, action, qty, kind, fill_price, status,
                position_id, note, commission, reference_price,
                requested_qty if requested_qty is not None else qty,
                perm_id, order_ref, operation_id,
            )

    async def cache_broker_state(
        self,
        *,
        observed_at: datetime,
        account_summary: dict[str, float],
        positions: dict[str, dict[str, float]],
    ) -> None:
        """Mirror the latest IB account/portfolio facts into Postgres."""
        rows = [
            (
                symbol,
                observed_at,
                float(item.get("qty") or 0.0),
                float(item.get("market_price") or 0.0),
                float(item.get("market_value") or 0.0),
                float(item.get("avg_cost") or 0.0),
                float(item.get("unrealized_pnl") or 0.0),
                float(item.get("realized_pnl") or 0.0),
            )
            for symbol, item in positions.items()
            if abs(float(item.get("qty") or 0.0)) > 1e-9
        ]
        symbols = [row[0] for row in rows]
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    f"""INSERT INTO {SCHEMA}.live_broker_account_cache
                        (cache_key, observed_at, net_liquidation, total_cash_value,
                         buying_power, available_funds, gross_position_value,
                         settled_cash, accrued_cash, init_margin_req,
                         maint_margin_req, excess_liquidity, full_init_margin_req,
                         full_maint_margin_req, lookahead_excess_liquidity,
                         leverage, source)
                        VALUES ('primary',$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,
                                $12,$13,$14,$15,'IB')
                        ON CONFLICT (cache_key) DO UPDATE SET
                            observed_at=EXCLUDED.observed_at,
                            net_liquidation=EXCLUDED.net_liquidation,
                            total_cash_value=EXCLUDED.total_cash_value,
                            buying_power=EXCLUDED.buying_power,
                            available_funds=EXCLUDED.available_funds,
                            gross_position_value=EXCLUDED.gross_position_value,
                            settled_cash=EXCLUDED.settled_cash,
                            accrued_cash=EXCLUDED.accrued_cash,
                            init_margin_req=EXCLUDED.init_margin_req,
                            maint_margin_req=EXCLUDED.maint_margin_req,
                            excess_liquidity=EXCLUDED.excess_liquidity,
                            full_init_margin_req=EXCLUDED.full_init_margin_req,
                            full_maint_margin_req=EXCLUDED.full_maint_margin_req,
                            lookahead_excess_liquidity=EXCLUDED.lookahead_excess_liquidity,
                            leverage=EXCLUDED.leverage,
                            source='IB'""",
                    observed_at,
                    account_summary.get("NetLiquidation"),
                    account_summary.get("TotalCashValue"),
                    account_summary.get("BuyingPower"),
                    account_summary.get("AvailableFunds"),
                    account_summary.get("GrossPositionValue"),
                    account_summary.get("SettledCash"),
                    account_summary.get("AccruedCash"),
                    account_summary.get("InitMarginReq"),
                    account_summary.get("MaintMarginReq"),
                    account_summary.get("ExcessLiquidity"),
                    account_summary.get("FullInitMarginReq"),
                    account_summary.get("FullMaintMarginReq"),
                    account_summary.get("LookAheadExcessLiquidity"),
                    account_summary.get("Leverage"),
                )
                if symbols:
                    await conn.execute(
                        f"DELETE FROM {SCHEMA}.live_broker_positions_cache "
                        "WHERE NOT (symbol = ANY($1::text[]))",
                        symbols,
                    )
                    await conn.executemany(
                        f"""INSERT INTO {SCHEMA}.live_broker_positions_cache
                            (symbol, observed_at, qty, market_price, market_value,
                             avg_cost, unrealized_pnl, realized_pnl, source)
                            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'IB')
                            ON CONFLICT (symbol) DO UPDATE SET
                                observed_at=EXCLUDED.observed_at,
                                qty=EXCLUDED.qty,
                                market_price=EXCLUDED.market_price,
                                market_value=EXCLUDED.market_value,
                                avg_cost=EXCLUDED.avg_cost,
                                unrealized_pnl=EXCLUDED.unrealized_pnl,
                                realized_pnl=EXCLUDED.realized_pnl,
                                source='IB'""",
                        rows,
                    )
                else:
                    await conn.execute(
                        f"DELETE FROM {SCHEMA}.live_broker_positions_cache"
                    )
                await conn.execute(
                    f"""UPDATE {SCHEMA}.live_positions p
                        SET broker_qty=COALESCE((
                                SELECT b.qty
                                FROM {SCHEMA}.live_broker_positions_cache b
                                WHERE b.symbol=p.symbol
                            ), 0),
                            broker_observed_at=$1,
                            broker_state=CASE
                                WHEN NOT EXISTS (
                                    SELECT 1 FROM {SCHEMA}.live_broker_positions_cache b
                                    WHERE b.symbol=p.symbol AND ABS(b.qty) > 1e-9
                                ) THEN 'absent_at_IB'
                                WHEN ABS(COALESCE((
                                    SELECT b.qty FROM {SCHEMA}.live_broker_positions_cache b
                                    WHERE b.symbol=p.symbol
                                ), 0) - p.qty) > 1e-6 THEN 'qty_diff_IB_wins'
                                ELSE 'matched'
                            END
                        WHERE p.status='open'""",
                    observed_at,
                )

    async def record_execution_fills(self, fills: list[dict[str, Any]]) -> None:
        """Persist immutable execution and commission facts reported by IB."""
        if not fills:
            return
        async with self.pool.acquire() as conn:
            await conn.executemany(
                f"""INSERT INTO {SCHEMA}.live_execution_fills
                    (exec_id, ib_order_id, symbol, action, exec_ts, shares, price,
                     exchange, commission, realized_pnl, perm_id, account,
                     order_ref, kind, position_id, operation_id, source)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,
                            $14,$15,$16,'IB')
                    ON CONFLICT (exec_id) DO UPDATE SET
                        commission=CASE
                            WHEN EXCLUDED.commission IS NULL THEN
                                {SCHEMA}.live_execution_fills.commission
                            WHEN EXCLUDED.commission = 0
                                 AND COALESCE({SCHEMA}.live_execution_fills.commission, 0) <> 0 THEN
                                {SCHEMA}.live_execution_fills.commission
                            ELSE EXCLUDED.commission
                        END,
                        realized_pnl=CASE
                            WHEN EXCLUDED.realized_pnl IS NULL THEN
                                {SCHEMA}.live_execution_fills.realized_pnl
                            WHEN EXCLUDED.realized_pnl = 0
                                 AND COALESCE({SCHEMA}.live_execution_fills.realized_pnl, 0) <> 0 THEN
                                {SCHEMA}.live_execution_fills.realized_pnl
                            ELSE EXCLUDED.realized_pnl
                        END,
                        order_ref=COALESCE(EXCLUDED.order_ref,
                                           {SCHEMA}.live_execution_fills.order_ref),
                        kind=COALESCE(EXCLUDED.kind,
                                      {SCHEMA}.live_execution_fills.kind),
                        position_id=COALESCE(EXCLUDED.position_id,
                                             {SCHEMA}.live_execution_fills.position_id),
                        operation_id=COALESCE(EXCLUDED.operation_id,
                                              {SCHEMA}.live_execution_fills.operation_id)""",
                [
                    (
                        row["exec_id"],
                        row.get("ib_order_id"),
                        row["symbol"],
                        row["action"],
                        row.get("exec_ts"),
                        row["shares"],
                        row["price"],
                        row.get("exchange"),
                        row.get("commission"),
                        row.get("realized_pnl"),
                        row.get("perm_id"),
                        row.get("account"),
                        row.get("order_ref"),
                        row.get("kind"),
                        row.get("position_id"),
                        row.get("operation_id"),
                    )
                    for row in fills
                ],
            )

    async def reconciled_cash(self) -> float | None:
        """Compatibility accessor for cash, now read only from the latest IB cache."""
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                f"""SELECT total_cash_value
                    FROM {SCHEMA}.live_broker_account_cache
                    WHERE cache_key='primary' AND source='IB'"""
            )
        return float(value) if value is not None else None

    async def reconciled_position_qty(self, symbol: str) -> float:
        """Compatibility accessor for quantity, now read only from the IB cache."""
        async with self.pool.acquire() as conn:
            qty = await conn.fetchval(
                f"""SELECT qty FROM {SCHEMA}.live_broker_positions_cache
                    WHERE symbol=$1 AND source='IB'""",
                symbol,
            )
        return float(qty or 0.0)

    async def bootstrap_performance_baseline(
        self,
        *,
        benchmark: str,
        current_nav: float,
        current_benchmark_price: float,
        observed_at: datetime,
    ) -> dict[str, Any]:
        """Create or repair the performance baseline from one IB snapshot.

        A legacy simulator NAV is not commensurate with IB NetLiquidation.  If
        an older deployment preserved that legacy value as an unverified
        baseline, archive it for audit and reset ``primary`` to the current
        same-timestamp IB NAV/SPY mark.  Only a verified IB baseline may drive
        live excess-performance claims.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                first_ib = await conn.fetchrow(
                    f"""SELECT broker_observed_at AS start_ts, equity AS start_nav,
                               benchmark_price AS start_price, source
                        FROM {SCHEMA}.live_equity_snapshots
                        WHERE source LIKE 'IB_%'
                          AND broker_observed_at IS NOT NULL
                          AND benchmark_observed_at IS NOT NULL
                          AND ABS(EXTRACT(EPOCH FROM
                              (broker_observed_at - benchmark_observed_at))) <= 1
                          AND equity > 0 AND benchmark_price > 0
                        ORDER BY broker_observed_at LIMIT 1"""
                )

                async def _set_verified_baseline(
                    start_ts: datetime, start_nav: float, start_price: float,
                ) -> dict[str, Any]:
                    row = await conn.fetchrow(
                        f"""UPDATE {SCHEMA}.live_performance_baselines
                            SET start_ts=$1, start_nav=$2, benchmark_symbol=$3,
                                benchmark_start_price=$4, source='IB_verified_history',
                                verified=TRUE,
                                note='Reset from the earliest preserved same-timestamp IB NAV and benchmark snapshot; legacy baseline archived.',
                                updated_at=NOW()
                            WHERE baseline_key='primary'
                            RETURNING *""",
                        start_ts, float(start_nav), benchmark, float(start_price),
                    )
                    snapshots = await conn.fetch(
                        f"""SELECT ts, broker_observed_at, benchmark_price
                            FROM {SCHEMA}.live_equity_snapshots
                            WHERE source LIKE 'IB_%'
                              AND broker_observed_at >= $1
                              AND benchmark_price > 0
                            ORDER BY broker_observed_at""",
                        start_ts,
                    )
                    flows = await conn.fetch(
                        f"""SELECT flow_ts, amount, benchmark_price
                            FROM {SCHEMA}.live_account_cash_flows
                            WHERE flow_ts > $1 ORDER BY flow_ts""",
                        start_ts,
                    )
                    updates = []
                    for snapshot in snapshots:
                        applicable = [
                            dict(flow) for flow in flows
                            if flow["flow_ts"] <= snapshot["broker_observed_at"]
                        ]
                        passive = passive_equity(
                            float(start_nav), float(start_price),
                            float(snapshot["benchmark_price"]), applicable,
                        )
                        updates.append((
                            snapshot["ts"], passive,
                            sum(float(flow["amount"]) for flow in applicable),
                        ))
                    if updates:
                        await conn.executemany(
                            f"""UPDATE {SCHEMA}.live_equity_snapshots
                                SET passive_equity=$2, baseline_key='primary',
                                    baseline_verified=TRUE,
                                    cumulative_cash_flows=$3
                                WHERE ts=$1""",
                            updates,
                        )
                    return dict(row)

                existing = await conn.fetchrow(
                    f"""SELECT * FROM {SCHEMA}.live_performance_baselines
                        WHERE baseline_key='primary' FOR UPDATE"""
                )
                if existing:
                    if bool(existing["verified"]):
                        has_legacy_archive = await conn.fetchval(
                            f"""SELECT EXISTS (
                                SELECT 1 FROM {SCHEMA}.live_performance_baselines
                                WHERE baseline_key LIKE 'legacy-unverified-%'
                            )"""
                        )
                        if (
                            has_legacy_archive and first_ib
                            and first_ib["start_ts"] < existing["start_ts"]
                        ):
                            return await _set_verified_baseline(
                                first_ib["start_ts"], float(first_ib["start_nav"]),
                                float(first_ib["start_price"]),
                            )
                        return dict(existing)
                    archive_key = (
                        "legacy-unverified-"
                        + existing["start_ts"].astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    )
                    await conn.execute(
                        f"""INSERT INTO {SCHEMA}.live_performance_baselines
                            (baseline_key, start_ts, start_nav, benchmark_symbol,
                             benchmark_start_price, source, verified, note,
                             created_at, updated_at)
                            VALUES ($1,$2,$3,$4,$5,$6,FALSE,$7,$8,$9)
                            ON CONFLICT (baseline_key) DO NOTHING""",
                        archive_key, existing["start_ts"], existing["start_nav"],
                        existing["benchmark_symbol"], existing["benchmark_start_price"],
                        existing["source"], existing["note"], existing["created_at"],
                        existing["updated_at"],
                    )
                    baseline_source = first_ib or {
                        "start_ts": observed_at,
                        "start_nav": current_nav,
                        "start_price": current_benchmark_price,
                    }
                    return await _set_verified_baseline(
                        baseline_source["start_ts"],
                        float(baseline_source["start_nav"]),
                        float(baseline_source["start_price"]),
                    )
                start_ts = observed_at
                start_nav = float(current_nav)
                start_price = float(current_benchmark_price)
                source = "IB_same_snapshot_v2"
                verified = True
                note = "Created from a same-timestamp IB account and benchmark snapshot."
                row = await conn.fetchrow(
                    f"""INSERT INTO {SCHEMA}.live_performance_baselines
                        (baseline_key, start_ts, start_nav, benchmark_symbol,
                         benchmark_start_price, source, verified, note)
                        VALUES ('primary',$1,$2,$3,$4,$5,$6,$7)
                        RETURNING *""",
                    start_ts, start_nav, benchmark, start_price, source, verified, note,
                )
        return dict(row)

    async def performance_baseline(self) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""SELECT * FROM {SCHEMA}.live_performance_baselines
                    WHERE baseline_key='primary'"""
            )
        return dict(row) if row else None

    async def current_passive_equity(
        self,
        *,
        benchmark_price: float,
        observed_at: datetime,
    ) -> tuple[float | None, dict[str, Any] | None, float]:
        """Compute the passive comparator from baseline + dated external flows."""
        baseline = await self.performance_baseline()
        if not baseline:
            return None, None, 0.0
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT amount, benchmark_price
                    FROM {SCHEMA}.live_account_cash_flows
                    WHERE flow_ts > $1 AND flow_ts <= $2
                    ORDER BY flow_ts""",
                baseline["start_ts"], observed_at,
            )
        flows = [dict(r) for r in rows]
        value = passive_equity(
            float(baseline["start_nav"]),
            float(baseline["benchmark_start_price"]),
            float(benchmark_price),
            flows,
        )
        return value, baseline, sum(float(r["amount"]) for r in flows)

    async def record_reconciliation_event(
        self,
        *,
        observed_at: datetime,
        severity: str,
        event_type: str,
        symbol: str | None = None,
        broker_value: float | None = None,
        database_value: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Record a warning/audit fact; reconciliation never sends an order."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {SCHEMA}.live_reconciliation_events
                    (observed_at, severity, symbol, event_type, broker_value,
                     database_value, details, source_of_truth)
                    VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,'IB')""",
                observed_at, severity, symbol, event_type, broker_value,
                database_value, json.dumps(details or {}),
            )

    async def snapshot_equity(self, *, equity: float, cash: float, benchmark_shares: float,
                              benchmark_price: float | None, open_positions: int,
                              passive_equity: float | None,
                              source: str = "IB_same_snapshot_v2",
                              observed_at: datetime | None = None,
                              benchmark_observed_at: datetime | None = None,
                              baseline_key: str | None = None,
                              baseline_verified: bool | None = None,
                              cumulative_cash_flows: float = 0.0) -> None:
        observed_at = observed_at or datetime.now(timezone.utc)
        benchmark_observed_at = benchmark_observed_at or observed_at
        ts = observed_at.replace(minute=0, second=0, microsecond=0)
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {SCHEMA}.live_equity_snapshots
                    (ts, equity, cash, benchmark_shares, benchmark_price,
                     open_positions, passive_equity, source, broker_observed_at,
                     benchmark_observed_at, baseline_key, baseline_verified,
                     cumulative_cash_flows)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                    ON CONFLICT (ts) DO UPDATE
                    SET equity=EXCLUDED.equity, cash=EXCLUDED.cash,
                        benchmark_shares=EXCLUDED.benchmark_shares,
                        benchmark_price=EXCLUDED.benchmark_price,
                        open_positions=EXCLUDED.open_positions,
                        passive_equity=EXCLUDED.passive_equity,
                        source=EXCLUDED.source,
                        broker_observed_at=EXCLUDED.broker_observed_at,
                        benchmark_observed_at=EXCLUDED.benchmark_observed_at,
                        baseline_key=EXCLUDED.baseline_key,
                        baseline_verified=EXCLUDED.baseline_verified,
                        cumulative_cash_flows=EXCLUDED.cumulative_cash_flows""",
                ts, equity, cash, benchmark_shares, benchmark_price,
                open_positions, passive_equity, source, observed_at,
                benchmark_observed_at, baseline_key, baseline_verified,
                cumulative_cash_flows,
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
        """Retention is DISABLED for the shared historical tables.

        The nightly `python -m ingest --rebuild` job treats
        historical_price_bars / historical_probability_points as the immutable
        source of truth for the backtest artifacts, so the live loop must never
        delete rows out from under it. DB head-room is sufficient; if that ever
        changes, archive cold rows to a *_archive table the rebuild also reads
        rather than deleting here.
        """
        log.debug("prune_stale: retention disabled on shared historical tables")

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
