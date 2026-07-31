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
     mapping, via the one ingest.chain cleaning pipeline), and
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
from .utils import is_gather_window, is_market_hours, retry_async, seconds_to_market_close

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
        # Fail fast before the daemon enters its hourly loop.  A missing policy
        # is a deployment/readiness failure, not a recoverable per-tick error.
        load_live_policy(self.cfg)
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

        # Gate the ENTIRE tick to the data-gathering window (09:30-16:30 ET =
        # 16:30-23:30 Israel). Outside it — including overnight and weekends —
        # nothing is pulled, decided, tracked, or snapshotted. A manual
        # --once --discover (force_discovery) bypasses this for testing.
        if not force_discovery and not is_gather_window(now):
            log.info("outside gather window (09:30-16:30 ET) -- tick is a no-op")
            return

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

        # 3. Price bars — refreshed across the whole gather window (IB serves the
        #    session's bars during and just after the close). Trading itself
        #    still requires the market to be open so orders can fill.
        market_open = is_market_hours(now)
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
            orders.seed_broker_state(snapshot)
            await self.store.cache_broker_state(
                observed_at=snapshot["observed_at"],
                account_summary=snapshot["account_summary"],
                positions=snapshot["broker_positions"],
            )
            await self.store.mark_runtime_event(
                "broker_sync", snapshot["observed_at"],
                {
                    "source": "IB",
                    "position_count": len(snapshot["broker_positions"]),
                    "metadata_gaps": snapshot.get("metadata_gaps", []),
                },
            )
            await self.sync_ib_executions()
            await self.record_reconciliation(snapshot, snapshot["observed_at"])
            if snapshot.get("metadata_gaps"):
                log.warning(
                    "IB positions without strategy metadata (still authoritative): %s",
                    ", ".join(snapshot["metadata_gaps"]),
                )

            # 4-6. Trade only when the equity market can fill us.
            if market_open and snapshot["trade_safe"]:
                if float(snapshot.get("cash") or 0.0) < 0:
                    log.warning(
                        "IB reports margin cash %.2f; this is informational and "
                        "does not globally block deterministic strategy orders",
                        float(snapshot.get("cash") or 0.0),
                    )
                await self.run_exits(engine, orders, positions, snapshot)
                snapshot = await positions.snapshot()
                if snapshot["valid"] and snapshot["trade_safe"] and not orders.execution_anomaly:
                    await self.run_entries(
                        engine, orders, positions, snapshot, markets, policy,
                    )
                    snapshot = await positions.snapshot()
                if snapshot["valid"] and snapshot["trade_safe"] and not orders.execution_anomaly:
                    swept = await orders.sweep_idle_cash(
                        cash=snapshot["cash"],
                        benchmark_price=snapshot["benchmark_price"],
                    )
                    if swept:
                        snapshot = await positions.snapshot()
                if snapshot["valid"] and snapshot["trade_safe"] and not orders.execution_anomaly:
                    snapshot = await self.final_hour_cash_sweep(
                        orders, positions, snapshot,
                    )

            # 7. NAV snapshot (also overnight -- probs still move), but only with
            # a real balance so the curve is never polluted by zero rows.
            if snapshot["valid"]:
                observed_at = snapshot["observed_at"]
                await self.store.cache_broker_state(
                    observed_at=observed_at,
                    account_summary=snapshot["account_summary"],
                    positions=snapshot["broker_positions"],
                )
                await self.store.mark_runtime_event(
                    "broker_sync", observed_at,
                    {
                        "source": "IB",
                        "position_count": len(snapshot["broker_positions"]),
                        "metadata_gaps": snapshot.get("metadata_gaps", []),
                        "execution_anomaly": orders.execution_anomaly,
                    },
                )
                await self.sync_ib_executions()
                await self.record_reconciliation(snapshot, observed_at)
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
        start_ts = int((now - timedelta(hours=25)).timestamp())
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
                        positions: PositionManager, snapshot: dict) -> None:
        assert self.store is not None
        open_positions = snapshot["open_positions"]
        if not open_positions:
            return
        exits = await engine.scan_exits(self.store, open_positions)
        by_id = {p["position_id"]: p for p in open_positions}
        current_snapshot = snapshot
        for signal in exits:
            pos = by_id[signal.position_id]
            closed = await orders.exit_position(
                pos, signal.reason, current_snapshot["benchmark_price"],
                cash=float(current_snapshot.get("cash") or 0.0),
            )
            if orders.execution_anomaly:
                break
            if closed:
                current_snapshot = await positions.snapshot()
                if not current_snapshot.get("valid"):
                    log.warning("IB snapshot failed between exits; stopping exit chain")
                    break

    async def run_entries(self, engine: StrategyEngine, orders: OrderManager,
                          positions: PositionManager, snapshot: dict,
                          markets: list[dict], policy: dict) -> None:
        assert self.store is not None
        open_positions = snapshot["open_positions"]
        max_concurrent = int(policy["max_concurrent"])
        slots = max_concurrent - int(snapshot.get("broker_position_count", len(open_positions)))
        if slots <= 0:
            return

        open_symbols = {
            symbol for symbol, qty in snapshot.get("ib_positions", {}).items()
            if symbol != self.cfg.benchmark and abs(float(qty)) > 1e-6
        }
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
        # Hard no-overspend guard: we only ever deploy capital we can fund from
        # current IB cash + liquidatable benchmark. Existing margin reduces this
        # amount, but does not become an unrelated global trading kill-switch.
        investable = max(
            0.0,
            float(cash) + max(0.0, benchmark_shares) * benchmark_price,
        )

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
            if orders.execution_anomaly:
                break
            if position:
                open_symbols.add(signal.symbol)
                latest = await positions.snapshot()
                if not latest.get("valid"):
                    return
                cash = float(latest["cash"])
                benchmark_shares = float(latest["benchmark_shares"])
                benchmark_price = float(latest["benchmark_price"] or benchmark_price)
                investable = max(
                    0.0,
                    cash + max(0.0, benchmark_shares) * benchmark_price,
                )
    async def enforce_no_margin(self, orders: OrderManager, positions: PositionManager,
                                snapshot: dict, *, reason: str) -> dict:
        """Compatibility hook: margin is an IB fact and produces a warning only."""
        cash = float(snapshot.get("cash") or 0.0)
        if cash < 0:
            log.warning("IB margin cash %.2f at %s; no corrective order sent", cash, reason)
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
                log.warning(
                    "final-hour IB cash is %.2f; skipping only the optional cash sweep",
                    cash,
                )
                return snapshot
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
            if orders.execution_anomaly or not snapshot.get("trade_safe", False):
                return snapshot
            if swept or float(snapshot.get("cash") or 0.0) < self.cfg.min_order_notional:
                return snapshot

            sleep_for = min(float(self.cfg.close_sweep_retry_seconds), max(seconds_left, 0.0))
            if sleep_for <= 0:
                return snapshot
            await asyncio.sleep(sleep_for)

    async def sync_ib_executions(self) -> None:
        """Copy IB's retrievable executions/commissions into the audit cache."""
        assert self.store is not None
        try:
            fills = await self.ib_conn.execution_snapshot()
            await self.store.record_execution_fills(fills)
            if fills:
                log.info("synced %d IB executions", len(fills))
        except Exception as error:  # noqa: BLE001 - audit sync must not trade
            log.warning("IB execution sync failed: %s", error)

    async def record_reconciliation(self, snapshot: dict, observed_at: datetime) -> None:
        """Audit DB metadata gaps against IB without blocking or trading."""
        assert self.store is not None
        db_qty = {
            str(pos["symbol"]): float(pos["qty"])
            for pos in snapshot.get("db_open_positions", [])
        }
        ib_qty = {
            str(symbol): float(qty)
            for symbol, qty in snapshot.get("ib_positions", {}).items()
            if symbol != self.cfg.benchmark
        }
        for symbol in sorted(set(db_qty) | set(ib_qty)):
            broker_value = ib_qty.get(symbol, 0.0)
            database_value = db_qty.get(symbol, 0.0)
            if abs(broker_value - database_value) <= 1e-6:
                continue
            await self.store.record_reconciliation_event(
                observed_at=observed_at,
                severity="warning",
                event_type="position_quantity_gap",
                symbol=symbol,
                broker_value=broker_value,
                database_value=database_value,
                details={
                    "action": "warning_only",
                    "execution_quantity_source": "IB",
                    "strategy_metadata_source": "DB",
                },
            )

    async def snapshot_equity(self, snapshot: dict) -> None:
        assert self.store is not None
        passive = None
        baseline = None
        cumulative_flows = 0.0
        observed_at = snapshot.get("observed_at") or datetime.now(timezone.utc)
        benchmark_price = snapshot.get("benchmark_price")
        if benchmark_price and str(snapshot.get("benchmark_source") or "").startswith("IB_"):
            baseline = await self.store.bootstrap_performance_baseline(
                benchmark=self.cfg.benchmark,
                current_nav=float(snapshot["equity"]),
                current_benchmark_price=float(benchmark_price),
                observed_at=observed_at,
            )
            passive, baseline, cumulative_flows = await self.store.current_passive_equity(
                benchmark_price=float(benchmark_price),
                observed_at=observed_at,
            )
        else:
            log.warning(
                "passive benchmark omitted: no authoritative same-snapshot IB %s price",
                self.cfg.benchmark,
            )
        await self.store.snapshot_equity(
            equity=snapshot["equity"], cash=snapshot["cash"],
            benchmark_shares=snapshot["benchmark_shares"],
            benchmark_price=benchmark_price,
            open_positions=int(snapshot.get("broker_position_count", 0)),
            passive_equity=passive,
            source="IB_same_snapshot_v2",
            observed_at=observed_at,
            benchmark_observed_at=observed_at,
            baseline_key=baseline.get("baseline_key") if baseline else None,
            baseline_verified=bool(baseline.get("verified")) if baseline else None,
            cumulative_cash_flows=cumulative_flows,
        )
        log.info("equity=%.2f cash=%.2f bench=%.4f open=%d",
                 snapshot["equity"], snapshot["cash"],
                 snapshot["benchmark_shares"], len(snapshot["open_positions"]))

    # ── Discovery (the one consolidated ingest cleaning chain) ───────────

    async def discover_new_markets(self) -> int:
        """Gamma scan -> dedup -> regex -> Gemini catalyst -> Gemini asset
        mapping -> track, via ingest.chain. Returns how many new markets
        entered tracking."""
        assert self.store is not None
        from ingest.chain import discover_and_clean

        known = {m["market_id"] for m in await self.store.active_markets()}
        async with self.store.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT market_id FROM {SCHEMA}.live_tracked_markets"
            )
            known.update(r["market_id"] for r in rows)

        async with httpx.AsyncClient(timeout=httpx.Timeout(60)) as client:
            passed = await discover_and_clean(client, known=known)
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
