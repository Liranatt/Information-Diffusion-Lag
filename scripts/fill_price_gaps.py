"""Fill missing daily bars in historical_price_bars from Yahoo Finance.

The live trader writes daily bars from IB only while it is connected, so any
period without a broker session leaves a hole in the shared price table that
the backtest artifacts and the dashboard both read. This script inserts the
missing (symbol, day) rows for every symbol in the candidate universe plus the
benchmarks and sector ETFs. Existing rows are never touched (ON CONFLICT DO
NOTHING), so IB bars always win.

    python -m scripts.fill_price_gaps              # fill through the last closed session
    python -m scripts.fill_price_gaps --dry-run    # report gaps only
"""
from __future__ import annotations

import argparse
import asyncio
import math
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from core.features import SECTOR_ETFS
from database.backtesting.schema import SCHEMA
from database.db_connection import connect

UTC = timezone.utc
RELEVANCE_FLOOR = 0.5
FULL_HISTORY_START = pd.Timestamp("2024-06-01", tz="UTC")
OVERLAP_DAYS = 7
BATCH = 40


def _yahoo_symbol(symbol: str) -> str:
    return symbol.replace(".", "-").replace("/", "-")


def _last_closed_session(now: datetime) -> pd.Timestamp:
    """Today's bar is only final after the US close (16:00 ET ~ 21:00 UTC)."""
    day = pd.Timestamp(now).tz_convert("UTC").normalize()
    if now.hour < 21 or (now.hour == 21 and now.minute < 15):
        day -= pd.Timedelta(days=1)
    return day


async def _universe(conn) -> list[str]:
    rows = await conn.fetch(
        f"""SELECT DISTINCT a.symbol
            FROM {SCHEMA}.historical_asset_world_assets a
            WHERE a.connection_strength >= $1""",
        RELEVANCE_FLOOR,
    )
    symbols = {str(r["symbol"]).strip().upper() for r in rows if r["symbol"]}
    meta = await conn.fetch(
        f"SELECT DISTINCT sector_etf FROM {SCHEMA}.historical_asset_metadata WHERE sector_etf IS NOT NULL"
    )
    symbols |= {str(r["sector_etf"]).strip().upper() for r in meta}
    symbols |= {"SPY", "QQQ"} | {str(v).upper() for v in SECTOR_ETFS.values()}
    return sorted(s for s in symbols if s and s.replace(".", "").replace("-", "").isalnum())


def _download(symbols: list[str], start: pd.Timestamp, end: pd.Timestamp) -> dict[str, pd.DataFrame]:
    tickers = {_yahoo_symbol(s): s for s in symbols}
    data = yf.download(
        list(tickers), start=start.strftime("%Y-%m-%d"),
        end=(end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        auto_adjust=False, group_by="ticker", progress=False, threads=True,
    )
    out: dict[str, pd.DataFrame] = {}
    if data is None or data.empty:
        return out
    for ticker, symbol in tickers.items():
        try:
            frame = data[ticker] if isinstance(data.columns, pd.MultiIndex) else data
        except KeyError:
            continue
        frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
        if not frame.empty:
            out[symbol] = frame
    return out


async def fill(*, dry_run: bool) -> dict:
    now = datetime.now(UTC)
    last_day = _last_closed_session(now)
    conn = await connect()
    try:
        symbols = await _universe(conn)
        last_rows = await conn.fetch(
            f"""SELECT symbol, max(ts) AS last_ts FROM {SCHEMA}.historical_price_bars
                WHERE resolution='1d' AND symbol = ANY($1::text[]) GROUP BY symbol""",
            symbols,
        )
        last_bar = {r["symbol"]: pd.Timestamp(r["last_ts"]).tz_convert("UTC").normalize() for r in last_rows}

        # Group symbols by download start so each batch is one Yahoo request.
        groups: dict[pd.Timestamp, list[str]] = {}
        for symbol in symbols:
            last = last_bar.get(symbol)
            if last is not None and last >= last_day:
                continue
            start = (last - pd.Timedelta(days=OVERLAP_DAYS)) if last is not None else FULL_HISTORY_START
            start = max(start, FULL_HISTORY_START)
            groups.setdefault(start.normalize(), []).append(symbol)
        stale = sum(len(v) for v in groups.values())
        print(f"[fill] universe={len(symbols)} symbols, {stale} need bars through {last_day.date()}")
        if dry_run or not stale:
            return {"universe": len(symbols), "stale": stale, "inserted": 0}

        no_data: list[str] = []
        # Coarsen starts to the week so batches stay large.
        coarse: dict[pd.Timestamp, list[str]] = {}
        for start, syms in groups.items():
            coarse.setdefault(start - pd.Timedelta(days=start.weekday()), []).extend(syms)
        for start, syms in sorted(coarse.items()):
            for i in range(0, len(syms), BATCH):
                batch = syms[i:i + BATCH]
                frames = _download(batch, start, last_day)
                no_data.extend(s for s in batch if s not in frames)
                rows = []
                for symbol, frame in frames.items():
                    for ts, bar in frame.iterrows():
                        day = pd.Timestamp(ts)
                        day = (day.tz_localize("UTC") if day.tzinfo is None else day.tz_convert("UTC")).normalize()
                        if day > last_day:
                            continue
                        volume = float(bar.get("Volume", 0.0) or 0.0)
                        rows.append((
                            symbol, "1d", day.to_pydatetime(),
                            float(bar["Open"]), float(bar["High"]), float(bar["Low"]), float(bar["Close"]),
                            0.0 if math.isnan(volume) else volume,
                        ))
                if rows:
                    await conn.executemany(
                        f"""INSERT INTO {SCHEMA}.historical_price_bars
                            (symbol, resolution, ts, open, high, low, close, volume)
                            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                            ON CONFLICT (symbol, resolution, ts) DO NOTHING""",
                        rows,
                    )
                print(f"[fill] {start.date()} batch {i // BATCH + 1}: {len(frames)}/{len(batch)} symbols, {len(rows)} bars offered")
        total_new = await conn.fetchval(
            f"""SELECT count(*) FROM {SCHEMA}.historical_price_bars
                WHERE resolution='1d' AND downloaded_at >= $1""",
            now - timedelta(seconds=1),
        )
        print(f"[fill] inserted {total_new} new daily bars; no Yahoo data for {len(no_data)}: {no_data[:15]}")
        return {"universe": len(symbols), "stale": stale, "inserted": int(total_new or 0), "no_data": no_data}
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(fill(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
