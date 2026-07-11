"""Entry point for the live paper-trading control pipeline.

Run on the private server:

    python -m live.run_live --daemon          # 24/7 hourly loop
    python -m live.run_live --once            # single tick (cron)
    python -m live.run_live --once --discover # tick + force discovery
    python -m live.run_live --status          # print portfolio state

Requires: IB Gateway/TWS running in paper mode, the repo .env with DB_* and
Gemini credentials, and at least one completed optimize_cem.py run (the live
policy is the latest walk-forward fold of the configured experiment).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from live.config import CONFIG, LiveConfig
from live.control_pipeline import ControlPipeline


def setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LIVE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                Path(__file__).resolve().parents[1] / "data" / "live_pipeline.log",
                encoding="utf-8",
            ),
        ],
    )


async def run_status(cfg: LiveConfig) -> None:
    pipeline = ControlPipeline(cfg)
    await pipeline.start()
    try:
        assert pipeline.store is not None
        markets = await pipeline.store.active_markets()
        positions = await pipeline.store.open_positions()
        trades = await pipeline.store.realized_trades(limit=200)
        print(f"tracked markets: {len(markets)}")
        for m in markets[:20]:
            print(f"  {str(m['end_at'])[:10]}  {m['question'][:70]}")
        print(f"open positions: {len(positions)}")
        for p in positions:
            entry_ts = p["entry_ts"].astimezone().strftime("%Y-%m-%d %H:%M")
            print(f"  {p['symbol']:>6} x{p['qty']}  entry {p['entry_price']:.2f} "
                  f"({entry_ts})  {p['question'][:50]}")
        if trades:
            pnl = sum(float(t["pnl"] or 0) for t in trades)
            wins = sum(1 for t in trades if float(t["pnl"] or 0) > 0)
            print(f"closed trades: {len(trades)}  win%={wins / len(trades) * 100:.1f}  "
                  f"total pnl=${pnl:,.2f}")
    finally:
        await pipeline.stop()


async def run_once(cfg: LiveConfig, force_discovery: bool) -> None:
    pipeline = ControlPipeline(cfg)
    await pipeline.start()
    try:
        await pipeline.tick(force_discovery=force_discovery)
    finally:
        await pipeline.stop()


async def run_daemon(cfg: LiveConfig) -> None:
    pipeline = ControlPipeline(cfg)
    await pipeline.start()
    log = logging.getLogger("live")
    try:
        # Align to :30 past each UTC hour so the first tick fires at
        # 9:30 ET / 16:30 IST (US market open) rather than an arbitrary time.
        now = datetime.now(timezone.utc)
        next_half = now.replace(minute=30, second=0, microsecond=0)
        if (next_half - now).total_seconds() < 60:
            next_half += timedelta(hours=1)
        initial_sleep = (next_half - now).total_seconds()
        if initial_sleep > 60:
            log.info("aligning to :30 mark, sleeping %.0fs until %s",
                     initial_sleep, next_half.isoformat(timespec="seconds"))
            await asyncio.sleep(initial_sleep)

        while True:
            try:
                await pipeline.tick()
            except Exception as error:  # noqa: BLE001 - the loop must survive
                log.exception("tick failed: %s", error)
            now = datetime.now(timezone.utc)
            next_half = now.replace(minute=30, second=0, microsecond=0)
            if (next_half - now).total_seconds() < 60:
                next_half += timedelta(hours=1)
            sleep_for = max(60.0, (next_half - now).total_seconds())
            log.info("sleeping %.0fs until next tick at %s",
                     sleep_for, next_half.isoformat(timespec="seconds"))
            await asyncio.sleep(sleep_for)
    finally:
        await pipeline.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Live paper-trading control pipeline")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--daemon", action="store_true", help="run the 24/7 hourly loop")
    mode.add_argument("--once", action="store_true", help="run one tick and exit")
    mode.add_argument("--status", action="store_true", help="print portfolio state")
    parser.add_argument("--discover", action="store_true",
                        help="force market discovery on this tick")
    parser.add_argument("--dry-run", action="store_true",
                        help="no orders are sent to IB")
    parser.add_argument("--host", help="override IB host for this process")
    parser.add_argument("--port", type=int, help="override IB port for this process")
    parser.add_argument("--client-id", type=int,
                        help="override IB client ID for this process")
    args = parser.parse_args()

    setup_logging()
    cfg = CONFIG
    if args.dry_run or args.host is not None or args.port is not None or args.client_id is not None:
        import dataclasses
        overrides = {}
        if args.dry_run:
            overrides["dry_run"] = True
        if args.host is not None:
            overrides["ib_host"] = args.host
        if args.port is not None:
            overrides["ib_port"] = args.port
        if args.client_id is not None:
            overrides["ib_client_id"] = args.client_id
        cfg = dataclasses.replace(cfg, **overrides)

    if args.status:
        asyncio.run(run_status(cfg))
    elif args.once:
        asyncio.run(run_once(cfg, force_discovery=args.discover))
    else:
        asyncio.run(run_daemon(cfg))


if __name__ == "__main__":
    main()
