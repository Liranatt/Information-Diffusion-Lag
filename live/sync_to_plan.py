"""Align the IB paper account with the plan's current holdings.

The plan is the out-of-sample portfolio simulation written by optimize_cem for
the live experiment/benchmark (data/experiment_*_logs_clean/<bench>_<slug>_test.csv).
Its last simulated day ends with every still-open position marked
``resolution-1d`` because the price paths stop there; those rows whose real
resolution cut lies after that day are the positions the plan holds now.

This script:
  1. exits every IB event position that is not part of the plan (positions the
     live loop entered today under the current policy are kept),
  2. enters each plan position IB does not hold, sized to the plan's weight of
     IB net liquidation, through the normal benchmark-rotation entry path,
  3. stamps those ledger rows with the plan's entry date, entry price and ATR,
     so the live exit rules continue exactly the plan's trade,
  4. sweeps leftover cash into the benchmark.

    python -m live.sync_to_plan            # print the plan and the orders it implies
    python -m live.sync_to_plan --apply    # send them (US market hours only)
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from core.kernel import calc_atr
from database.backtesting.schema import SCHEMA

from .config import CONFIG, LiveConfig
from .connection import IBConnection
from .data_fetcher import DataFetcher
from .database import LiveStore
from .order_manager import OrderManager
from .position_manager import PositionManager
from .strategy_engine import EntrySignal
from .utils import is_market_hours

log = logging.getLogger("live.sync_to_plan")
UTC = timezone.utc
SYNC_REASON = "plan-sync"
DEFAULT_CLIENT_ID = 23  # never collide with the daemon's client id


@dataclass
class PlanHolding:
    market_id: str
    symbol: str
    question: str
    is_earnings: bool
    entry_ts: datetime
    entry_price: float
    entry_prob: float
    plan_qty: int
    mark: float
    weight: float
    t_e: datetime
    atr_pct: float = 0.0


def _plan_paths(cfg: LiveConfig):
    slug = cfg.experiment.lower().replace("+", "_").replace(" ", "_")
    stem = f"{cfg.benchmark.lower()}_{slug}_test.csv"
    root = cfg.results_csv.parent
    return root / "experiment_equity_logs_clean" / stem, root / "experiment_trade_logs_clean" / stem


def load_plan_holdings(cfg: LiveConfig) -> tuple[pd.Timestamp, float, list[PlanHolding]]:
    equity_path, trade_path = _plan_paths(cfg)
    equity = pd.read_csv(equity_path)
    trades = pd.read_csv(trade_path)
    last_day = pd.Timestamp(str(equity["date"].max())[:10], tz="UTC")
    plan_equity = float(equity.sort_values("date").iloc[-1]["equity"])

    holdings: list[PlanHolding] = []
    for _, row in trades.iterrows():
        if str(row["exit_date"])[:10] != str(last_day.date()):
            continue
        if str(row["exit_reason"]) != "resolution-1d":
            continue
        t_e = pd.Timestamp(row["candidate_t_e"])
        t_e = t_e.tz_localize("UTC") if t_e.tzinfo is None else t_e.tz_convert("UTC")
        # The plan's own resolution cut is one day before t_e. If a later daily
        # bar would still have been inside the path, the exit is only the end
        # of the simulated data and the position is still open.
        if t_e - pd.Timedelta(days=1) <= last_day + pd.Timedelta(days=1):
            continue
        qty = int(float(row["_qty"]))
        mark = float(row["exit_price"])
        holdings.append(PlanHolding(
            market_id=str(row["market_id"]),
            symbol=str(row["symbol"]).upper(),
            question=str(row["question"]),
            is_earnings=str(row.get("event_family", "")) == "earnings",
            entry_ts=pd.Timestamp(str(row["entry_date"])[:10], tz="UTC").to_pydatetime(),
            entry_price=float(row["entry_price"]),
            entry_prob=float(row["entry_prob"]),
            plan_qty=qty,
            mark=mark,
            weight=qty * mark / plan_equity if plan_equity > 0 else 0.0,
            t_e=t_e.to_pydatetime(),
        ))
    return last_day, plan_equity, holdings


async def _enrich(store: LiveStore, holdings: list[PlanHolding]) -> None:
    """Live t_e / earnings flag from the tracked market, plan-identical ATR."""
    async with store.pool.acquire() as conn:
        for h in holdings:
            market = await conn.fetchrow(
                f"SELECT end_at, is_earnings FROM {SCHEMA}.live_tracked_markets WHERE market_id=$1",
                h.market_id,
            )
            if market:
                h.t_e = market["end_at"]
                h.is_earnings = bool(market["is_earnings"]) or h.is_earnings
            bars = await conn.fetch(
                f"""SELECT ts, open, high, low, close FROM {SCHEMA}.historical_price_bars
                    WHERE symbol=$1 AND resolution='1d' AND ts <= $2
                    ORDER BY ts DESC LIMIT 16""",
                h.symbol, h.entry_ts,
            )
            path = [(r["ts"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
                    for r in reversed(bars)]
            atr = calc_atr(path)
            h.atr_pct = atr / h.entry_price if h.entry_price > 0 else 0.0


async def sync(*, apply: bool, client_id: int) -> None:
    cfg = dataclasses.replace(CONFIG, ib_client_id=client_id)
    last_day, plan_equity, holdings = load_plan_holdings(cfg)
    store = await LiveStore.create()
    ib_conn = IBConnection(cfg)
    try:
        await _enrich(store, holdings)
        positions = PositionManager(cfg, ib_conn, store)
        orders = OrderManager(cfg, ib_conn, store)
        snapshot = await positions.snapshot()
        if not snapshot["valid"]:
            raise RuntimeError("IB account snapshot unavailable; nothing was sent")
        nav = float(snapshot["equity"])
        now = datetime.now(UTC)
        today = pd.Timestamp(now).normalize()

        plan_keys = {(h.market_id, h.symbol) for h in holdings}
        plan_symbols = {h.symbol for h in holdings}
        ledger = snapshot["db_open_positions"]
        held_keys = {(p["market_id"], p["symbol"]) for p in ledger}
        ib_positions = {
            s: float(q) for s, q in snapshot["ib_positions"].items()
            if s != cfg.benchmark and abs(float(q)) > 1e-6
        }

        stale = [
            p for p in ledger
            if (p["market_id"], p["symbol"]) not in plan_keys
            and pd.Timestamp(p["entry_ts"]).tz_convert("UTC") < today
            and ib_positions.get(p["symbol"], 0.0) > 0
        ]
        ledger_symbols = {p["symbol"] for p in ledger}
        orphans = {s: q for s, q in ib_positions.items() if s not in ledger_symbols}
        missing = [h for h in holdings if (h.market_id, h.symbol) not in held_keys]

        print(f"plan as of {last_day.date()}: equity ${plan_equity:,.2f}, "
              f"{len(holdings)} open positions; IB NAV ${nav:,.2f}")
        for h in sorted(holdings, key=lambda x: -x.weight):
            target = int(h.weight * nav / h.mark) if h.mark > 0 else 0
            state = "held" if (h.market_id, h.symbol) in held_keys else "BUY"
            print(f"  {state:>4} {h.symbol:>6} weight {h.weight * 100:5.2f}%  ~{target} sh  "
                  f"entry {h.entry_ts.date()} @ {h.entry_price:.2f}  atr {h.atr_pct * 100:.2f}%  "
                  f"t_e {pd.Timestamp(h.t_e).date()}  {h.question[:58]}")
        for p in stale:
            print(f"  SELL {p['symbol']:>6} x{ib_positions.get(p['symbol'], 0):g} (ledger #{p['position_id']}, "
                  f"not in plan)  {p['question'][:58]}")
        for s, q in orphans.items():
            if s not in plan_symbols:
                print(f"  SELL {s:>6} x{q:g} (IB holding without ledger row, not in plan)")
        if not apply:
            print("dry run: no orders sent (pass --apply)")
            return
        if not is_market_hours(now):
            raise RuntimeError("US market is closed; orders would not fill. Nothing was sent.")

        orders.seed_broker_state(snapshot)
        for pos in stale:
            await orders.exit_position(
                pos, SYNC_REASON, snapshot["benchmark_price"],
                cash=float(snapshot.get("cash") or 0.0),
            )
            if orders.execution_anomaly:
                raise RuntimeError(f"execution anomaly while exiting {pos['symbol']}; stopped")
            snapshot = await positions.snapshot()
            orders.seed_broker_state(snapshot)
        for symbol, qty in orphans.items():
            if symbol in plan_symbols:
                continue
            await orders._execute(symbol, "SELL", qty, kind="exit", note=SYNC_REASON,
                                  reference_price=await store.latest_close(symbol))
            snapshot = await positions.snapshot()
            orders.seed_broker_state(snapshot)

        # Entry limits are capped off the latest stored close. Refresh today's
        # IB bars first so the cap tracks the live market, not yesterday's close.
        fetcher = DataFetcher(ib_conn, store)
        for symbol in sorted({h.symbol for h in missing} | {cfg.benchmark}):
            try:
                await fetcher.refresh_symbol(symbol)
            except Exception as error:  # noqa: BLE001 - a stale mark only risks a limit miss
                log.warning("bar refresh failed for %s: %s", symbol, error)

        nav = float(snapshot["equity"])
        for h in sorted(missing, key=lambda x: -x.weight):
            snapshot = await positions.snapshot()
            orders.seed_broker_state(snapshot)
            signal = EntrySignal(
                market_id=h.market_id, symbol=h.symbol, question=h.question,
                is_earnings=h.is_earnings, prob=h.entry_prob, t_e=h.t_e, atr_pct=h.atr_pct,
            )
            position = await orders.enter_position(
                signal,
                desired_allocation=h.weight * nav,
                benchmark_price=float(snapshot["benchmark_price"]),
                cash=float(snapshot["cash"]),
                benchmark_shares=float(snapshot["benchmark_shares"]),
                position_size_pct=h.weight,
            )
            if orders.execution_anomaly:
                # An unfilled/cancelled entry leaves its funding as cash; keep
                # going with the other plan entries and sweep the cash at the end.
                print(f"  {h.symbol}: entry order did not fill; continuing")
                orders.execution_anomaly = False
                continue
            if not position:
                print(f"  {h.symbol}: entry not filled (limit or funding); left to the live loop")
                continue
            async with store.pool.acquire() as conn:
                await conn.execute(
                    f"""UPDATE {SCHEMA}.live_positions
                        SET entry_ts=$2, entry_price=$3, metadata_source='plan_sync'
                        WHERE position_id=$1""",
                    position["position_id"], h.entry_ts, h.entry_price,
                )
            print(f"  entered {h.symbol} x{position['qty']} @ {position['entry_price']:.2f} "
                  f"(plan entry {h.entry_ts.date()} @ {h.entry_price:.2f})")

        snapshot = await positions.snapshot()
        orders.seed_broker_state(snapshot)
        await orders.sweep_idle_cash(
            cash=float(snapshot["cash"]), benchmark_price=snapshot["benchmark_price"],
        )
        final = await positions.snapshot()
        held = {s: q for s, q in final["ib_positions"].items() if abs(float(q)) > 1e-6}
        # The dashboard chains the plan track onto IB NAV from this point on.
        await store.mark_runtime_event("plan_sync", final["observed_at"], {
            "plan_last_day": str(last_day.date()),
            "plan_equity": round(plan_equity, 2),
            "ib_nav": round(float(final["equity"]), 2),
            "benchmark_price": float(final["benchmark_price"] or 0.0),
            "holdings": {s: float(q) for s, q in held.items()},
        })
        print(f"done. IB NAV ${float(final['equity']):,.2f}, cash ${float(final['cash']):,.2f}, "
              f"holdings {held}")
    finally:
        await ib_conn.disconnect()
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Align the IB paper account with the plan")
    parser.add_argument("--apply", action="store_true", help="send the orders")
    parser.add_argument("--client-id", type=int, default=DEFAULT_CLIENT_ID)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    asyncio.run(sync(apply=args.apply, client_id=args.client_id))


if __name__ == "__main__":
    main()
