"""Non-destructive live-system health checks.

Verifies the configured IB session, paper-account guard, account summary,
portfolio positions, market-data snapshot, and optional historical bar writes.
No orders are submitted from this module.

NOTE ON PAPER TRADING TIMEOUTS:
If you receive `TimeoutError` on account_summary, positions, or market_data_snapshot,
and the Gateway logs `Warning 2110: Connectivity between TWS and server is broken` or
`Warning 2103: Market data farm connection is broken`, this is usually NOT a code issue.
Common causes:
1. "Download open orders on connection" is checked in Gateway API Settings (Uncheck it).
2. Gateway version bug (Use "Stable" Gateway, e.g., 10.19, instead of "Latest").
3. Active Session Conflict: You are logged into the IBKR website or mobile app simultaneously.
4. IBKR Paper Servers are down/restarting (often happens when the US market is closed).
5. Market Data sharing from the live account has not fully propagated yet.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.backtesting.schema import SCHEMA

from live.config import CONFIG, LiveConfig
from live.connection import IBConnection
from live.data_fetcher import DataFetcher
from live.database import LiveStore


ACCOUNT_SUMMARY_TAGS = {
    "AccountType",
    "NetLiquidation",
    "TotalCashValue",
    "AvailableFunds",
    "BuyingPower",
    "GrossPositionValue",
}


def _mask_account(account: str) -> str:
    if len(account) <= 4:
        return "*" * len(account)
    return f"{account[:2]}...{account[-4:]}"


async def _bar_counts(store: LiveStore, symbol: str) -> list[dict]:
    async with store.pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT resolution, COUNT(*) AS n, MAX(ts) AS latest_ts
                FROM {SCHEMA}.historical_price_bars
                WHERE symbol=$1 AND resolution IN ('1h', '1d')
                GROUP BY resolution
                ORDER BY resolution""",
            symbol,
        )
    return [dict(r) for r in rows]


async def run(cfg: LiveConfig, symbol: str, refresh_bars: bool) -> bool:
    ok = True
    print("stage=db_connecting", flush=True)
    store = await LiveStore.create()
    print("stage=db_ready", flush=True)
    ib_conn = IBConnection(cfg)
    try:
        print("stage=ib_connecting", flush=True)
        ib = await ib_conn.ensure_connected()
        print("stage=ib_ready", flush=True)
        accounts = ib.managedAccounts()
        masked_accounts = ", ".join(_mask_account(a) for a in accounts) or "none"
        target_account = _mask_account(cfg.account) if cfg.account else "default"
        print(
            "ib_connected=true "
            f"host={cfg.ib_host} port={cfg.ib_port} "
            f"client_id={cfg.ib_client_id} account={target_account} "
            f"managed_accounts={masked_accounts} "
            f"paper_guard={cfg.require_paper_account}"
        )

        print("stage=account_summary", flush=True)
        try:
            summary_rows = await asyncio.wait_for(
                ib.accountSummaryAsync(cfg.account or ""), timeout=45
            )
            summary: dict[str, str] = {}
            for row in summary_rows:
                if row.tag not in ACCOUNT_SUMMARY_TAGS:
                    continue
                if row.currency not in {"", "USD"}:
                    continue
                suffix = f" {row.currency}" if row.currency else ""
                summary[row.tag] = f"{row.value}{suffix}"
            for tag in sorted(summary):
                print(f"account_summary {tag}={summary[tag]}")
        except Exception as error:  # noqa: BLE001 - health check should continue
            ok = False
            print(f"account_summary error={type(error).__name__}: {error}", flush=True)

        print("stage=positions", flush=True)
        try:
            positions = await asyncio.wait_for(ib_conn.portfolio_positions(), timeout=45)
            print(f"positions count={len(positions)}")
            for pos_symbol, qty in sorted(positions.items())[:25]:
                print(f"position {pos_symbol} qty={qty:g}")
        except Exception as error:  # noqa: BLE001
            ok = False
            print(f"positions error={type(error).__name__}: {error}", flush=True)

        print("stage=market_data_snapshot", flush=True)
        try:
            snapshot_price = await asyncio.wait_for(ib_conn.last_price(symbol), timeout=20)
            print(f"market_data symbol={symbol} snapshot_price={snapshot_price}")
            if snapshot_price is None:
                ok = False
        except Exception as error:  # noqa: BLE001
            ok = False
            print(f"market_data error={type(error).__name__}: {error}", flush=True)

        if refresh_bars:
            print("stage=bar_refresh", flush=True)
            try:
                fetcher = DataFetcher(ib_conn, store)
                refreshed = await asyncio.wait_for(fetcher.refresh_symbol(symbol), timeout=90)
                latest_close = await store.latest_close(symbol)
                print(
                    f"db_bar_refresh symbol={symbol} ok={refreshed} "
                    f"latest_close={latest_close}"
                )
                if not refreshed:
                    ok = False
                for row in await _bar_counts(store, symbol):
                    print(
                        f"db_bars symbol={symbol} resolution={row['resolution']} "
                        f"count={row['n']} latest_ts={row['latest_ts']}"
                    )
            except Exception as error:  # noqa: BLE001
                ok = False
                print(f"bar_refresh error={type(error).__name__}: {error}", flush=True)
    finally:
        await ib_conn.disconnect()
        await store.close()
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Live IB/DB health check")
    parser.add_argument("--symbol", default=CONFIG.benchmark,
                        help="symbol to use for market-data checks")
    parser.add_argument("--host", help="override IB host for this check")
    parser.add_argument("--port", type=int, help="override IB port for this check")
    parser.add_argument("--client-id", type=int,
                        help="override IB client ID for this check")
    parser.add_argument("--refresh-bars", action="store_true",
                        help="write recent hourly/daily bars for the symbol to the DB")
    args = parser.parse_args()

    logging.basicConfig(
        level=os.environ.get("LIVE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    cfg = CONFIG
    overrides = {}
    if args.host is not None:
        overrides["ib_host"] = args.host
    if args.port is not None:
        overrides["ib_port"] = args.port
    if args.client_id is not None:
        overrides["ib_client_id"] = args.client_id
    if overrides:
        cfg = dataclasses.replace(cfg, **overrides)
    ok = asyncio.run(run(cfg, args.symbol, args.refresh_bars))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
