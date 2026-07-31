"""Fetch hourly and daily bars from Interactive Brokers for tracked symbols only.

Space discipline: we request bars exclusively for symbols the strategy needs
right now (benchmark + open positions + assets mapped to open markets), store
them in the shared historical_price_bars table, and let retention pruning drop
hourly bars for symbols that leave the tracked set.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone

from .connection import IBConnection
from .database import LiveStore
from .utils import NY

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


def _partition_session_rows(
    rows: list[dict], resolution: str, now: datetime,
) -> tuple[list[dict], list[dict], list[datetime]]:
    """Split immutable history, replaceable completed-session rows and open bars."""
    now = now.astimezone(timezone.utc)
    ny_now = now.astimezone(NY)
    session_day = ny_now.date()
    historical: list[dict] = []
    completed_session: list[dict] = []
    incomplete: list[datetime] = []
    for row in rows:
        ts = row["ts"].astimezone(timezone.utc)
        if resolution == "1d":
            row_session_day = ts.date()
            is_complete = ny_now.time() > time(16, 0)
        else:
            row_session_day = ts.astimezone(NY).date()
            is_complete = ts + timedelta(hours=1) <= now
        if row_session_day != session_day:
            historical.append(row)
        elif is_complete:
            completed_session.append(row)
        else:
            incomplete.append(ts)
    return historical, completed_session, incomplete


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
        now = datetime.now(timezone.utc)
        hourly_old, hourly_done, hourly_open = _partition_session_rows(
            _bars_to_rows(hourly), "1h", now,
        )
        daily_old, daily_done, daily_open = _partition_session_rows(
            _bars_to_rows(daily), "1d", now,
        )
        # Old versions cached IB's in-progress bar with ON CONFLICT DO NOTHING,
        # permanently freezing an intraday value as the day's close. Remove
        # exact open intervals and replace only completed rows from this session.
        await self.store.delete_session_bars(symbol, "1h", hourly_open)
        await self.store.delete_session_bars(symbol, "1d", daily_open)
        n_h = await self.store.upsert_bars(symbol, "1h", hourly_old)
        n_h += await self.store.replace_session_bars(symbol, "1h", hourly_done)
        n_d = await self.store.upsert_bars(symbol, "1d", daily_old)
        n_d += await self.store.replace_session_bars(symbol, "1d", daily_done)
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
