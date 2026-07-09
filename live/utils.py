"""Shared helpers: the backtest-identical cost model, market hours, retries."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("live")

NY = ZoneInfo("America/New_York")


def ib_cost(shares: int, price: float, is_sell: bool) -> float:
    """IB-style commission + SEC fee on sales + fixed 5 bp slippage.

    Identical to the backtest cost model (optimize_cem.ib_cost) so live paper
    fills can be compared against modeled costs.
    """
    if shares <= 0 or price <= 0:
        return 0.0
    trade_value = shares * price
    commission = max(0.35, min(shares * 0.0035, trade_value * 0.01))
    sec = trade_value * 0.0000278 if is_sell else 0.0
    return commission + sec + trade_value * 0.0005


def affordable_buy_qty(cash_available: float, price: float) -> int:
    """Largest whole-share count whose price plus modeled cost fits the cash."""
    if cash_available <= 0 or price <= 0:
        return 0
    qty = int(cash_available / price)
    while qty > 0 and qty * price + ib_cost(qty, price, False) > cash_available + 1e-9:
        qty -= 1
    return qty


def affordable_buy_qty_frac(cash_available: float, price: float) -> float:
    """Fractional-share buy size fitting the cash after modeled costs.

    Used for benchmark legs (SPY/QQQ are fraction-eligible at IB; the account
    needs fractional-share trading permission enabled).
    """
    if cash_available <= 0 or price <= 0:
        return 0.0
    qty = cash_available / price
    for _ in range(4):
        qty = max((cash_available - ib_cost(qty, price, False)) / price, 0.0)
    return round(qty, 4)


def is_market_hours(now: datetime | None = None) -> bool:
    """True during regular NYSE trading hours (no holiday calendar; IB rejects
    orders on holidays anyway, and the control loop tolerates that)."""
    now = (now or datetime.now(timezone.utc)).astimezone(NY)
    if now.weekday() >= 5:
        return False
    return time(9, 30) <= now.time() <= time(16, 0)


def seconds_to_market_close(now: datetime | None = None) -> float | None:
    """Seconds until the regular NYSE close, or None when the market is closed."""
    local = (now or datetime.now(timezone.utc)).astimezone(NY)
    if not is_market_hours(local):
        return None
    close_at = datetime.combine(local.date(), time(16, 0), tzinfo=NY)
    return max(0.0, (close_at - local).total_seconds())


def market_session_status(now: datetime | None = None) -> dict:
    """Small dashboard-friendly market status snapshot in English."""
    local = (now or datetime.now(timezone.utc)).astimezone(NY)
    seconds_left = seconds_to_market_close(local)
    is_open = seconds_left is not None
    return {
        "is_open": is_open,
        "label": "Market open" if is_open else "Market closed",
        "ny_time": local.isoformat(),
        "seconds_to_close": round(seconds_left) if seconds_left is not None else None,
    }


async def retry_async(coro_factory, *, attempts: int = 3, base_delay: float = 2.0, label: str = ""):
    """Run an async factory with exponential backoff. Raises the last error."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await coro_factory()
        except Exception as error:  # noqa: BLE001 - deliberate catch-all with re-raise
            last_error = error
            wait = base_delay * (2 ** attempt)
            log.warning("%s failed (attempt %d/%d): %s -- retrying in %.0fs",
                        label or "operation", attempt + 1, attempts, error, wait)
            await asyncio.sleep(wait)
    raise last_error  # type: ignore[misc]
