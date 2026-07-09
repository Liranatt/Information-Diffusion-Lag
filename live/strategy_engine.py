"""Live decision engine with backtest-kernel parity (pipeline/sim_kernel.py).

Entry (per candidate market x asset):
  - prob >= enter_strong fires immediately; otherwise prob >= enter_floor must
    hold for MORE THAN hold_days consecutive daily probability points.
  - gates at entry: probability surge since T0 <= max_prob_surge, and asset
    price run-up since T0 <= max_price_runup.

Exit (per open position, evaluated hourly, entry day excluded):
  - earnings positions exit ONLY on probability invalidation (daily prob <
    theta_out) or resolution cut (T_e - 1 day) -- the kernel skips ATR /
    profit-lock for is_earnings.
  - non-earnings positions additionally use the ATR trailing stop
    (ret_low <= peak - atr_mult * atr_pct) and the profit-lock hard floor
    (once peak >= lock_activate, floor = floor(peak * 100) / 100).

ATR is the simple mean true range of the <= 15 daily ranges ending at entry,
expressed as a fraction of entry price -- identical to calc_atr in the kernel.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

log = logging.getLogger("live.engine")

RESOLUTION_CUT = timedelta(days=1)


@dataclass
class EntrySignal:
    market_id: str
    symbol: str
    question: str
    is_earnings: bool
    prob: float
    t_e: datetime
    atr_pct: float


@dataclass
class ExitSignal:
    position_id: int
    symbol: str
    qty: int
    reason: str


def entry_triggered(daily_probs: list[tuple], current_prob: float,
                    enter_strong: float, enter_floor: float, hold_days: int) -> bool:
    """Kernel entry rule on the daily probability series plus the live point."""
    if current_prob >= enter_strong:
        return True
    series = [p for _, p in daily_probs] + [current_prob]
    held = 0
    for p in series:
        if p >= enter_floor:
            held += 1
            if held > hold_days:
                return True
        else:
            held = 0
    return False


def atr_pct_from_daily(bars: list[dict]) -> float | None:
    """Mean true range over the <= 15 most recent daily ranges / last close."""
    if len(bars) < 2:
        return None
    window = bars[-16:]
    trs = []
    for prev, cur in zip(window, window[1:]):
        hh, ll, pc = float(cur["high"]), float(cur["low"]), float(prev["close"])
        trs.append(max(hh - ll, abs(hh - pc), abs(ll - pc)))
    entry_price = float(window[-1]["close"])
    if not trs or entry_price <= 0:
        return None
    atr = sum(trs) / len(trs)
    return atr / entry_price if atr > 0 else None


class StrategyEngine:
    def __init__(self, policy: dict) -> None:
        self.policy = policy

    # ── Entries ──────────────────────────────────────────────────────────

    async def scan_entries(self, store, markets: list[dict],
                           open_symbols: set[str], open_market_assets: set[tuple],
                           now: datetime | None = None) -> list[EntrySignal]:
        now = now or datetime.now(timezone.utc)
        p = self.policy
        signals: list[EntrySignal] = []

        for market in markets:
            t_e = market["end_at"]
            if t_e <= now + RESOLUTION_CUT:
                continue  # too close to resolution to enter

            current_prob = await store.latest_prob(market["market_id"])
            if current_prob is None:
                continue

            daily = await store.daily_prob_closes(market["market_id"])
            if not entry_triggered(daily, current_prob,
                                   p["enter_strong"], p["enter_floor"], p["hold_days"]):
                continue

            t0_prob = market.get("t0_prob")
            prob_surge = (current_prob - float(t0_prob)) if t0_prob is not None else None
            if prob_surge is not None and prob_surge > p["max_prob_surge"]:
                continue

            for asset in market["assets"]:
                symbol = asset["symbol"]
                if symbol in open_symbols:
                    continue  # duplicate-symbol guard, as in the backtest
                if (market["market_id"], symbol) in open_market_assets:
                    continue

                bars = await store.daily_bars(symbol, 40)
                if len(bars) < 2:
                    continue

                t0_close = await store.close_near(symbol, market["discovered_at"])
                last_close = float(bars[-1]["close"])
                if t0_close and t0_close > 0:
                    runup = last_close / t0_close - 1.0
                    if runup > p["max_price_runup"]:
                        continue

                atr_pct = atr_pct_from_daily(bars)
                if atr_pct is None or not math.isfinite(atr_pct):
                    continue

                signals.append(EntrySignal(
                    market_id=market["market_id"], symbol=symbol,
                    question=market["question"], is_earnings=bool(market["is_earnings"]),
                    prob=current_prob, t_e=t_e, atr_pct=atr_pct,
                ))
        return signals

    # ── Exits ────────────────────────────────────────────────────────────

    async def scan_exits(self, store, positions: list[dict],
                         now: datetime | None = None) -> list[ExitSignal]:
        now = now or datetime.now(timezone.utc)
        p = self.policy
        signals: list[ExitSignal] = []

        for pos in positions:
            # Kernel parity: no exits on the entry day itself.
            if now.date() <= pos["entry_ts"].date():
                continue

            last_close = await store.latest_close(pos["symbol"])
            if last_close is None or pos["entry_price"] <= 0:
                continue
            ret_c = last_close / pos["entry_price"] - 1.0

            peak = max(float(pos["peak_ret"] or 0.0), ret_c)
            if peak > float(pos["peak_ret"] or 0.0):
                await store.update_peak(pos["position_id"], peak)

            reason: str | None = None
            if not pos["is_earnings"]:
                atr_pct = float(pos["atr_pct"] or 0.0)
                stop_dist = p["atr_mult"] * atr_pct
                if atr_pct > 0 and ret_c <= peak - stop_dist:
                    reason = f"trailing_{p['atr_mult']:.1f}ATR"
                elif peak >= p["lock_activate"]:
                    hard_floor = math.floor(peak * 100.0) / 100.0
                    if ret_c < hard_floor:
                        reason = f"profit_lock_{int(hard_floor * 100)}%"

            if reason is None:
                prob = await store.latest_prob(pos["market_id"])
                if prob is not None and prob < p["theta_out"]:
                    reason = f"poly<{p['theta_out']:.4f}"
                elif now >= pos["t_e"] - RESOLUTION_CUT:
                    reason = "resolution-1d"

            if reason:
                signals.append(ExitSignal(
                    position_id=pos["position_id"], symbol=pos["symbol"],
                    qty=int(pos["qty"]), reason=reason,
                ))
        return signals
