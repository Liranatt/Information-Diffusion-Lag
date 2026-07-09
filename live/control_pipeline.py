"""24/7 control pipeline for live paper trading.

Every tick (hourly by default):
  1. Pull fresh Polymarket probabilities for every tracked open market
     (CLOB prices-history, fidelity=60 -- only markets we care about).
  2. Mark resolved markets; force-exit any position whose market resolved.
  3. During US market hours, pull hourly + daily bars from IB for tracked
     symbols only (benchmark + open positions + mapped assets).
  4. Run exit scan -> execute benchmark-rotation exits.
  5. Run entry scan -> execute benchmark-rotation entries under the frozen
     latest-fold policy (position sizing via half-Kelly when enabled).
  6. Sweep idle cash into the benchmark (fully-invested rule).
  7. Snapshot equity vs the passive benchmark counterfactual.

Once per discovery interval (daily by default):
  8. Discover new Polymarket markets (Gamma scan, 5-60 day window), run the
     exact backtest cleaning chain (regex -> Gemini catalyst -> Gemini asset
     mapping, reused from scan_historical.py), backfill probability history,
     and start tracking whatever passes.
  9. Prune stale hourly bars / probability points (we are low on space).
"""
from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from database.backtesting.schema import SCHEMA

from .config import LiveConfig
from .connection import IBConnection
from .data_fetcher import DataFetcher
from .database import LiveStore
from .order_manager import OrderManager
from .policy import kelly_size, load_live_policy
from .position_manager import PositionManager
from .strategy_engine import StrategyEngine
from .utils import is_market_hours, retry_async, seconds_to_market_close

log = logging.getLogger("live.control")

CLOB_PRICE_HISTORY_URL = "https://clob.polymarket.com/prices-history"
MIN_CONNECTION_STRENGTH = 0.5


class ControlPipeline:
    def __init__(self, cfg: LiveConfig) -> None:
        self.cfg = cfg
        self.store: LiveStore | None = None
        self.ib_conn = IBConnection(cfg)
        self._tick_count = 0

    async def start(self) -> None:
        self.store = await LiveStore.create()

    async def stop(self) -> None:
        await self.ib_conn.disconnect()
        if self.store:
            await self.store.close()

    # ── Tick ─────────────────────────────────────────────────────────────

    async def tick(self, *, force_discovery: bool = False) -> None:
        assert self.store is not None
        self._tick_count += 1
        now = datetime.now(timezone.utc)
        log.info("=== tick %d @ %s ===", self._tick_count, now.isoformat(timespec="seconds"))

        policy = load_live_policy(self.cfg)
        engine = StrategyEngine(policy)
        
        # 0. Cancel any leftover ghost limit orders from previous ticks/crashes
        try:
            await self.ib_conn.cancel_all_open_orders()
        except Exception as e:
            log.warning("failed to cancel open orders: %s", e)

        orders = OrderManager(self.cfg, self.ib_conn, self.store)
        positions = PositionManager(self.cfg, self.ib_conn, self.store)
        fetcher = DataFetcher(self.ib_conn, self.store)

        discovery_interval = timedelta(
            seconds=self.cfg.tick_seconds * self.cfg.discovery_every_ticks
        )
        prune_interval = timedelta(seconds=self.cfg.tick_seconds * self.cfg.prune_every_ticks)

        # 8-9. Discovery + prune on persistent cadence. This is deliberately
        # stored in Postgres so deploy/restart does not trigger paid discovery.
        if force_discovery or await self.store.should_run_runtime_event(
            "discovery", discovery_interval, now,
        ):
            try:
                tracked = await self.discover_new_markets()
                await self.store.mark_runtime_event(
                    "discovery", now, {"force": force_discovery, "tracked": tracked}
                )
            except Exception as error:  # noqa: BLE001 - stage isolation
                log.exception("discovery failed: %s", error)
        if await self.store.should_run_runtime_event("prune", prune_interval, now):
            try:
                symbols = await self.store.tracked_symbols(self.cfg.benchmark)
                await self.store.prune_stale(
                    tracked_symbols=symbols,
                    bar_retention_days=self.cfg.bar_retention_days,
                    prob_retention_days=self.cfg.prob_retention_days,
                )
                await self.store.mark_runtime_event("prune", now, {"symbols": len(symbols)})
            except Exception as error:  # noqa: BLE001
                log.exception("prune failed: %s", error)

        # 1. Probabilities for tracked markets (Polymarket trades 24/7).
        markets = await self.store.active_markets()
        await self.update_probabilities(markets)
        try:
            repaired = await self.store.repair_t0_prob_baselines()
            if repaired:
                log.info("repaired %d missing T0 probability baselines", repaired)
        except Exception as error:  # noqa: BLE001
            log.warning("T0 probability repair failed: %s", error)

        # 2. Resolutions.
        await self.mark_resolutions(markets, now)
        markets = [m for m in markets if m["end_at"] > now]

        # 3. Price bars (IB only fills during/around market hours).
        market_open = is_market_hours(now)
        if market_open:
            try:
                await fetcher.refresh_tracked(self.cfg.benchmark)
            except Exception as error:  # noqa: BLE001
                log.exception("bar refresh failed: %s", error)

        snapshot = await positions.snapshot()

        # Never trade or record NAV on a phantom balance: if IB could not return
        # the account state (farm reconnecting / timeout), skip this tick's
        # trading and NAV snapshot entirely and try again next tick.
        if not snapshot["valid"]:
            log.warning("IB balance unavailable -- skipping trading + NAV snapshot this "
                        "tick (gateway/account farm warming up)")
        else:
            await positions.report_drift(snapshot)

            # 4-6. Trade only when the equity market can fill us.
            if market_open:
                snapshot = await self.enforce_no_margin(
                    orders, positions, snapshot, reason="pre-exit",
                )
                await self.run_exits(engine, orders, snapshot)
                snapshot = await positions.snapshot()
                if snapshot["valid"]:
                    snapshot = await self.enforce_no_margin(
                        orders, positions, snapshot, reason="pre-entry",
                    )
                if snapshot["valid"]:
                    await self.run_entries(engine, orders, snapshot, markets, policy)
                    snapshot = await positions.snapshot()
                if snapshot["valid"]:
                    snapshot = await self.enforce_no_margin(
                        orders, positions, snapshot, reason="pre-sweep",
                    )
                if snapshot["valid"]:
                    swept = await orders.sweep_idle_cash(
                        cash=snapshot["cash"], benchmark_price=snapshot["benchmark_price"],
                    )
                    if swept:
                        snapshot = await positions.snapshot()
                        if snapshot["valid"]:
                            snapshot = await self.enforce_no_margin(
                                orders, positions, snapshot, reason="post-sweep",
                            )
                if snapshot["valid"]:
                    snapshot = await self.final_hour_cash_sweep(
                        orders, positions, snapshot,
                    )

            # 7. NAV snapshot (also overnight -- probs still move), but only with
            # a real balance so the curve is never polluted by zero rows.
            if snapshot["valid"]:
                await self.snapshot_equity(snapshot)

        # 7b. System telemetry (DB size + disk) so space is observable.
        try:
            await self.store.record_system_metrics()
        except Exception as error:  # noqa: BLE001 - telemetry must never break a tick
            log.warning("system-metrics snapshot failed: %s", error)

    # ── Stages ───────────────────────────────────────────────────────────

    async def update_probabilities(self, markets: list[dict]) -> None:
        assert self.store is not None
        if not markets:
            return
        now = datetime.now(timezone.utc)
        start_ts = int((now - timedelta(hours=6)).timestamp())
        async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
            for market in markets:
                try:
                    response = await retry_async(
                        lambda m=market: client.get(CLOB_PRICE_HISTORY_URL, params={
                            "market": m["yes_token_id"],
                            "startTs": start_ts,
                            "endTs": int(now.timestamp()),
                            "fidelity": 60,
                        }),
                        attempts=3, label=f"probs {market['market_id'][:12]}",
                    )
                    response.raise_for_status()
                except Exception as error:  # noqa: BLE001
                    log.warning("prob update failed for %s: %s",
                                market["market_id"][:16], error)
                    continue
                points = []
                for item in response.json().get("history") or []:
                    ts = datetime.fromtimestamp(float(item["t"]), tz=timezone.utc)
                    ts = ts.replace(minute=0, second=0, microsecond=0)
                    points.append((ts, min(max(float(item["p"]), 0.0), 1.0)))
                # Keep the last point per hour.
                dedup = dict(points)
                await self.store.record_prob_points(
                    market["market_id"], market["yes_token_id"], sorted(dedup.items()),
                )
                if points and market.get("t0_prob") is None:
                    await self.store.set_t0_prob(market["market_id"], points[0][1])

    async def mark_resolutions(self, markets: list[dict], now: datetime) -> None:
        assert self.store is not None
        for market in markets:
            if market["end_at"] <= now:
                await self.store.set_market_status(market["market_id"], "resolved")
                log.info("market resolved: %s", market["question"][:70])

    async def run_exits(self, engine: StrategyEngine, orders: OrderManager,
                        snapshot: dict) -> None:
        assert self.store is not None
        open_positions = snapshot["open_positions"]
        if not open_positions:
            return
        exits = await engine.scan_exits(self.store, open_positions)
        by_id = {p["position_id"]: p for p in open_positions}
        for signal in exits:
            pos = by_id[signal.position_id]
            await orders.exit_position(
                pos, signal.reason, snapshot["benchmark_price"],
                cash=float(snapshot.get("cash") or 0.0),
            )

    async def run_entries(self, engine: StrategyEngine, orders: OrderManager,
                          snapshot: dict, markets: list[dict], policy: dict) -> None:
        assert self.store is not None
        open_positions = snapshot["open_positions"]
        max_concurrent = int(policy["max_concurrent"])
        slots = max_concurrent - len(open_positions)
        if slots <= 0:
            return

        open_symbols = {p["symbol"] for p in open_positions}
        open_market_assets = {(p["market_id"], p["symbol"]) for p in open_positions}
        signals = await engine.scan_entries(self.store, markets,
                                            open_symbols, open_market_assets)
        if not signals:
            return

        base_ps = float(policy["position_size_pct"])
        if self.cfg.use_kelly:
            history = await self.store.realized_trades(limit=50)
            position_size = kelly_size(history, base_ps)
        else:
            position_size = base_ps

        cash = snapshot["cash"]
        benchmark_shares = snapshot["benchmark_shares"]
        benchmark_price = snapshot["benchmark_price"]
        if not benchmark_price:
            log.warning("no benchmark price -- skipping entries this tick")
            return
        if cash < 0:
            log.error(
                "negative reconciled cash %.2f remains after margin rebalance -- "
                "skipping entries",
                cash,
            )
            return

        # Hard no-overspend guard: we only ever deploy capital we can fund from
        # cash + liquidatable benchmark. Never buy on margin/borrowed money.
        investable = max(0.0, cash) + max(0.0, benchmark_shares) * benchmark_price

        for signal in signals[:slots]:
            if investable < self.cfg.min_order_notional:
                log.info("entries stopped: investable %.2f < min order %.2f",
                         investable, self.cfg.min_order_notional)
                break
            desired = min(snapshot["equity"] * position_size, investable)
            position = await orders.enter_position(
                signal,
                desired_allocation=desired,
                benchmark_price=benchmark_price,
                cash=cash,
                benchmark_shares=benchmark_shares,
                position_size_pct=position_size,
            )
            if position:
                open_symbols.add(signal.symbol)
                cash = max(0.0, cash - desired)
                benchmark_shares = max(0.0, benchmark_shares - position.get("benchmark_sell_qty", 0))
                investable = max(0.0, investable - desired)

    async def enforce_no_margin(self, orders: OrderManager, positions: PositionManager,
                                snapshot: dict, *, reason: str) -> dict:
        """Bring negative ledger cash back to zero by selling benchmark inventory."""
        cash = float(snapshot.get("cash") or 0.0)
        if cash >= 0:
            return snapshot

        restored = await orders.restore_no_margin_from_benchmark(
            cash=cash,
            benchmark_price=snapshot.get("benchmark_price"),
            benchmark_shares=float(snapshot.get("benchmark_shares") or 0.0),
            reason=reason,
        )
        if restored:
            snapshot = await positions.snapshot()

        if snapshot.get("valid") and float(snapshot.get("cash") or 0.0) < 0:
            log.error(
                "reconciled cash remains negative after %s rebalance: %.2f; "
                "new buys are disabled until cash is non-negative",
                reason, float(snapshot.get("cash") or 0.0),
            )
        return snapshot

    async def final_hour_cash_sweep(self, orders: OrderManager, positions: PositionManager,
                                    snapshot: dict) -> dict:
        """Last-hour sweep loop: keep trying to convert idle cash into benchmark."""
        seconds_left = seconds_to_market_close()
        start_seconds = self.cfg.close_sweep_start_minutes * 60
        if seconds_left is None or seconds_left > start_seconds:
            return snapshot

        while True:
            cash = float(snapshot.get("cash") or 0.0)
            benchmark_price = snapshot.get("benchmark_price")
            seconds_left = seconds_to_market_close()
            if cash < 0:
                snapshot = await self.enforce_no_margin(
                    orders, positions, snapshot, reason="final-hour",
                )
                cash = float(snapshot.get("cash") or 0.0)
            if (
                seconds_left is None
                or cash < self.cfg.min_order_notional
                or not benchmark_price
            ):
                return snapshot

            log.info(
                "final-hour cash sweep: cash=%.2f seconds_to_close=%.0f",
                cash, seconds_left,
            )
            swept = await orders.sweep_idle_cash(
                cash=cash,
                benchmark_price=benchmark_price,
                kind="cash_sweep_close",
                note="final-hour no-overnight-cash sweep",
                buffer_pct=self.cfg.close_sweep_buffer_pct,
            )
            snapshot = await positions.snapshot()
            if swept or float(snapshot.get("cash") or 0.0) < self.cfg.min_order_notional:
                return snapshot

            sleep_for = min(float(self.cfg.close_sweep_retry_seconds), max(seconds_left, 0.0))
            if sleep_for <= 0:
                return snapshot
            await asyncio.sleep(sleep_for)

    async def snapshot_equity(self, snapshot: dict) -> None:
        assert self.store is not None
        passive = None
        async with self.store.pool.acquire() as conn:
            first = await conn.fetchrow(
                f"""SELECT equity, benchmark_price FROM {SCHEMA}.live_equity_snapshots
                    WHERE benchmark_price IS NOT NULL ORDER BY ts LIMIT 1"""
            )
        if first and first["benchmark_price"] and snapshot["benchmark_price"]:
            passive = float(first["equity"]) / float(first["benchmark_price"]) \
                * float(snapshot["benchmark_price"])
        await self.store.snapshot_equity(
            equity=snapshot["equity"], cash=snapshot["cash"],
            benchmark_shares=snapshot["benchmark_shares"],
            benchmark_price=snapshot["benchmark_price"],
            open_positions=len(snapshot["open_positions"]),
            passive_equity=passive,
        )
        log.info("equity=%.2f cash=%.2f bench=%d open=%d",
                 snapshot["equity"], snapshot["cash"],
                 snapshot["benchmark_shares"], len(snapshot["open_positions"]))

    # ── Discovery (reuses the exact backtest cleaning chain) ─────────────

    async def discover_new_markets(self) -> int:
        """Gamma scan -> regex -> Gemini catalyst -> Gemini asset mapping ->
        track. Returns how many new markets entered tracking."""
        assert self.store is not None
        from pipeline.scanner import fetch_active_markets
        from scan_historical import step2a_regex_filter, step2b_catalyst_filter, \
            step2c_asset_mapping

        async with httpx.AsyncClient(timeout=httpx.Timeout(60)) as client:
            scanned = await fetch_active_markets(client)

        known = {m["market_id"] for m in await self.store.active_markets()}
        async with self.store.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT market_id FROM {SCHEMA}.live_tracked_markets"
            )
            known.update(r["market_id"] for r in rows)

        fresh = [{
            "event_id": m.event_id, "market_id": m.market_id, "question": m.question,
            "event_title": m.event_title, "tags": m.tags,
            "created_at": m.created_at.isoformat(), "end_at": m.end_at.isoformat(),
            "yes_token_id": m.yes_token_id, "condition_id": m.condition_id,
        } for m in scanned if m.market_id not in known]
        log.info("discovery: %d scanned, %d new", len(scanned), len(fresh))
        if not fresh:
            return 0

        regex_passed = await step2a_regex_filter(fresh)
        catalysts = await step2b_catalyst_filter(regex_passed)
        passed = await step2c_asset_mapping(catalysts)
        if not passed:
            return 0

        tracked = 0
        for market in passed:
            assets = await self._assets_for_market(market["market_id"])
            if not assets:
                continue
            await self.store.upsert_tracked_market(market, assets)
            tracked += 1
            log.info("tracking: %s -> %s", market["question"][:60],
                     [a["symbol"] for a in assets])
        return tracked

    async def _assets_for_market(self, market_id: str) -> list[dict]:
        """Mapped assets from the newest Gemini asset world for this market."""
        assert self.store is not None
        async with self.store.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT a.symbol, a.asset_name, a.asset_class, a.connection_strength
                    FROM {SCHEMA}.historical_asset_world_assets a
                    JOIN {SCHEMA}.historical_asset_worlds w ON w.world_id = a.world_id
                    WHERE w.market_id = $1
                      AND w.as_of = (SELECT MAX(as_of) FROM {SCHEMA}.historical_asset_worlds
                                     WHERE market_id = $1)""",
                market_id,
            )
        return [
            {"symbol": r["symbol"], "asset_name": r["asset_name"],
             "asset_class": r["asset_class"],
             "connection_strength": float(r["connection_strength"] or 1.0)}
            for r in rows
            if float(r["connection_strength"] or 1.0) >= MIN_CONNECTION_STRENGTH
        ]
