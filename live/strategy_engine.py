"""Live decision engine — replays the shared backtest kernel over data-up-to-now.

Instead of re-implementing the entry/exit rules (which had silently drifted from
the backtest), every tick this reconstructs a candidate's price + probability
paths as they stand *now* and reuses the shared core kernel primitives:

  * ENTRY  — the shared entry rule (``core.kernel.entry_day`` on the raw
             probability path) plus the kernel's surge/run-up gates and
             ATR. Enter iff that entry lands on the freshest bar we have (the
             signal is firing right now, not days ago) and we are flat on that
             (market, symbol). The full-trade replay cannot decide entry at entry
             time — it needs future bars that do not exist yet.
  * EXIT   — the authoritative reference simulation
             ``core.kernel._simulate_one_py`` over data-up-to-now: exit iff the
             simulated trade has already hit a terminal stop (trailing /
             profit-lock / probability-out / resolution); a non-terminal
             ``end_of_window`` result means "still holding".

Both planes reuse the same mechanical entry/exit primitives. Features are the
core definitions (`core.features`), sizing/ATR come from core, and both backtest
and live engines apply the resolved signal polarity (3-state, long-only) to ensure
the logic remains in lockstep.

t_theta is the first daily probability crossing of the fixed 0.55 threshold
(`core.features.find_t_theta`) — the universe anchor, not an entry rule; entry is
governed purely by the CEM policy's enter_strong / enter_floor / hold_days.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from core.features import find_t_theta
from core.kernel import _simulate_one_py, calc_atr, entry_day
from core.polarity import resolve_polarity, effective_prob_path, effective_prob_surge
from core.policy import RELEVANCE_COL

log = logging.getLogger("live.engine")

RESOLUTION_CUT = timedelta(days=1)
# The kernel's price window opens at t_theta - 30d (for ATR history); pull a
# little extra so the 30-day pre-roll and the 20-bar run-up lookback are covered.
_PATH_PREROLL_DAYS = 45
# Don't act on a stale price path (data outage): the freshest bar must be recent.
_MAX_BAR_STALENESS_DAYS = 4


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


def _prob_path(prob_rows: list[tuple]) -> list[tuple]:
    """Daily prob closes -> [(Timestamp(UTC), prob)], matching load_probs_from_db."""
    return [(pd.Timestamp(d).tz_localize("UTC"), float(p)) for d, p in prob_rows]


def _price_path(bars: list[dict]) -> list[tuple]:
    """Daily bar dicts -> sorted [(Timestamp(UTC, midnight), h, l, c)], matching
    the backtest's load_price_prob_paths shape."""
    path = [
        (
            pd.Timestamp(b["ts"]).tz_convert("UTC").normalize(),
            float(b["high"]), float(b["low"]), float(b["close"]),
        )
        for b in bars
    ]
    path.sort(key=lambda x: x[0])
    return path


class StrategyEngine:
    def __init__(self, policy: dict) -> None:
        self.policy = policy

    async def _candidate(self, store, *, market_id: str, symbol: str, question: str,
                         is_earnings: bool, t_e: datetime, relevance: float,
                         t0: datetime | None = None):
        """Build (row, prices, probs, price_path, t_theta) for the kernel replay,
        or None if the candidate has no theta crossing / not enough data yet.

        The since-T0 features are measured from T0 (= market creation) exactly as
        core.features.compute_features does: probability and asset run-up from
        creation to the theta crossing. T0 falls back to the first stored
        probability timestamp when creation time is unavailable."""
        prob_path = _prob_path(await store.daily_prob_closes(market_id))
        if not prob_path:
            return None
        t_theta = find_t_theta(prob_path)
        if t_theta is None:
            return None

        t0_ts = pd.Timestamp(t0).tz_convert("UTC") if t0 is not None else prob_path[0][0]
        window_start = min(
            t_theta - pd.Timedelta(days=_PATH_PREROLL_DAYS),
            t0_ts - pd.Timedelta(days=5),
        )
        price_path = _price_path(await store.daily_bars_since(symbol, window_start.to_pydatetime()))
        if len(price_path) < 2:
            return None

        p_t0 = prob_path[0][1]
        p_theta = next((p for t, p in prob_path if t >= t_theta), prob_path[-1][1])
        bar_theta = next((c for t, _h, _l, c in reversed(price_path) if t <= t_theta), None)
        bar_t0 = next((c for t, _h, _l, c in reversed(price_path) if t <= t0_ts), None) or bar_theta

        row = {
            "symbol": symbol,
            "market_id": market_id,
            "question": question,
            "t_theta": t_theta,
            "t_e": pd.Timestamp(t_e).tz_convert("UTC"),
            "feat_archetype": "earnings" if is_earnings else "",
            "feat_prob_surge_since_t0": (p_theta - p_t0),
            "feat_runup_since_t0": (bar_theta / bar_t0 - 1.0) if (bar_theta and bar_t0) else 0.0,
            RELEVANCE_COL: relevance,
            "split": "live",
        }
        prices = {symbol: price_path}
        probs = {market_id: prob_path}
        return row, prices, probs, price_path, t_theta

    # ── Entries ──────────────────────────────────────────────────────────

    async def scan_entries(self, store, markets: list[dict],
                           open_symbols: set[str], open_market_assets: set[tuple],
                           now: datetime | None = None) -> list[EntrySignal]:
        now = now or datetime.now(timezone.utc)
        signals: list[EntrySignal] = []

        for market in markets:
            t_e = market["end_at"]
            if t_e <= now + RESOLUTION_CUT:
                continue  # too close to resolution to enter

            for asset in market["assets"]:
                symbol = asset["symbol"]
                if symbol in open_symbols:
                    continue  # duplicate-symbol guard, as in the backtest
                if (market["market_id"], symbol) in open_market_assets:
                    continue

                built = await self._candidate(
                    store, market_id=market["market_id"], symbol=symbol,
                    question=market["question"], is_earnings=bool(market["is_earnings"]),
                    t_e=t_e, relevance=float(asset.get("connection_strength") or 1.0),
                    t0=market.get("created_at"),
                )
                if built is None:
                    continue
                row, _prices, probs, price_path, t_theta = built

                last_bar_ts = price_path[-1][0]
                if (now - last_bar_ts).days > _MAX_BAR_STALENESS_DAYS:
                    continue  # freshest bar too old to trade on

                # Entry decision. The full-trade replay (_simulate_one_py) cannot
                # decide entry at entry time — it needs future bars to simulate the
                # holding path, which do not exist yet. So the entry uses the same
                # core primitives the kernel's entry uses (entry_day, the surge/
                # run-up gates, calc_atr), staying in lockstep with the backtest,
                # and fires only when the entry lands on the freshest bar.
                polarity, polarity_source = resolve_polarity(market["question"], symbol)
                if polarity == 0:
                    continue  # Skip pair entirely if no clean side exists

                prob_path = probs[market["market_id"]]
                effective_path = effective_prob_path(prob_path, polarity)
                ent = entry_day(effective_path, t_theta, self.policy)
                if ent is None:
                    continue
                entry_ts, entry_prob = ent

                p_surge = effective_prob_surge(row, polarity)
                r_surge = row["feat_runup_since_t0"]
                if p_surge is not None and p_surge > self.policy.get("max_prob_surge", 999.0):
                    continue
                if r_surge is not None and r_surge > self.policy.get("max_price_runup", 999.0):
                    continue

                entry_idx = next((i for i, b in enumerate(price_path) if b[0] >= entry_ts), -1)
                if entry_idx == -1:
                    continue
                entry_bar = price_path[entry_idx]
                # Enter only when the entry lands on the freshest bar — never on a
                # spike that already resolved days ago (the old stale-entry bug).
                if entry_bar[0] != last_bar_ts:
                    continue
                if entry_bar[0] >= pd.Timestamp(t_e).tz_convert("UTC") - RESOLUTION_CUT:
                    continue

                hist = price_path[max(0, entry_idx - 15):entry_idx + 1]
                atr = calc_atr(hist)
                entry_price = entry_bar[3]
                if atr == 0 or entry_price == 0:
                    continue
                atr_pct = atr / entry_price

                signals.append(EntrySignal(
                    market_id=market["market_id"], symbol=symbol,
                    question=market["question"], is_earnings=bool(market["is_earnings"]),
                    prob=float(entry_prob), t_e=t_e, atr_pct=atr_pct,
                ))
        return signals

    # ── Exits ────────────────────────────────────────────────────────────

    async def scan_exits(self, store, positions: list[dict],
                         now: datetime | None = None) -> list[ExitSignal]:
        now = now or datetime.now(timezone.utc)
        signals: list[ExitSignal] = []

        for pos in positions:
            # Kernel parity: no exits on the entry day itself (exits require i>0).
            if now.date() <= pos["entry_ts"].date():
                continue

            built = await self._candidate(
                store, market_id=pos["market_id"], symbol=pos["symbol"],
                question=pos.get("question", ""), is_earnings=bool(pos.get("is_earnings")),
                t_e=pos["t_e"], relevance=float(pos.get("relevance") or 1.0),
                t0=pos.get("created_at"),
            )
            if built is None:
                continue
            row, prices, probs, _price_path, _t_theta = built

            trade = _simulate_one_py(
                row,
                prices,
                probs,
                self.policy,
                apply_polarity=True,
            )
            if trade is None:
                continue
            # A terminal exit reason means the trade should already be closed given
            # today's data; end_of_window means it is still open.
            if trade["exit_reason"] == "end_of_window":
                continue

            signals.append(ExitSignal(
                position_id=pos["position_id"], symbol=pos["symbol"],
                qty=int(pos["qty"]), reason=trade["exit_reason"],
            ))
        return signals
