"""Fetch hourly and daily bars from Interactive Brokers for tracked symbols only.

Space discipline: we request bars exclusively for symbols the strategy needs
right now (benchmark + open positions + assets mapped to open markets), store
them in the shared historical_price_bars table, and let retention pruning drop
hourly bars for symbols that leave the tracked set.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .connection import IBConnection
from .database import LiveStore

log = logging.getLogger("live.data")


def _bars_to_rows(bars) -> list[dict]:
    rows = []
    for b in bars:
        ts = b.date
        if not isinstance(ts, datetime):  # daily bars arrive as date
            ts = datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)
        elif ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        rows.append({
            "ts": ts,
            "open": float(b.open), "high": float(b.high),
            "low": float(b.low), "close": float(b.close),
            "volume": float(b.volume) if b.volume and b.volume > 0 else 0.0,
        })
    return rows


class DataFetcher:
    def __init__(self, ib_conn: IBConnection, store: LiveStore) -> None:
        self.ib_conn = ib_conn
        self.store = store

    async def refresh_symbol(self, symbol: str, *, hourly_duration: str = "2 D",
                             daily_duration: str = "60 D") -> bool:
        """Pull recent hourly + daily bars for one symbol into the DB."""
        ib = await self.ib_conn.ensure_connected()
        contract = await self.ib_conn.qualified_stock(symbol)
        if contract is None:
            return False
        try:
            hourly = await asyncio.wait_for(
                ib.reqHistoricalDataAsync(
                    contract, endDateTime="", durationStr=hourly_duration,
                    barSizeSetting="1 hour", whatToShow="TRADES",
                    useRTH=True, formatDate=2,
                ),
                timeout=self.ib_conn.cfg.ib_request_timeout_seconds,
            )
            daily = await asyncio.wait_for(
                ib.reqHistoricalDataAsync(
                    contract, endDateTime="", durationStr=daily_duration,
                    barSizeSetting="1 day", whatToShow="TRADES",
                    useRTH=True, formatDate=2,
                ),
                timeout=self.ib_conn.cfg.ib_request_timeout_seconds,
            )
        except Exception as error:  # noqa: BLE001
            log.warning("historical data failed for %s: %s", symbol, error)
            return False
        n_h = await self.store.upsert_bars(symbol, "1h", _bars_to_rows(hourly))
        n_d = await self.store.upsert_bars(symbol, "1d", _bars_to_rows(daily))
        log.debug("%s: %d hourly, %d daily bars", symbol, n_h, n_d)
        return True

    async def refresh_tracked(self, benchmark: str) -> list[str]:
        """Refresh bars for every tracked symbol; returns symbols refreshed."""
        symbols = await self.store.tracked_symbols(benchmark)
        refreshed = []
        for symbol in symbols:
            if await self.refresh_symbol(symbol):
                refreshed.append(symbol)
        log.info("refreshed bars for %d/%d tracked symbols", len(refreshed), len(symbols))
        return refreshed
