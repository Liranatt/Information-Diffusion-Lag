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
  * EXIT   — evaluate the position that IB actually holds from its persisted
             fill facts.  Never require the historical entry simulation to be
             reproducible before an already-open position can be sold.

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
from core.kernel import calc_atr, entry_day
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
    triggered_at: datetime | None = None
    model_exit_price: float | None = None
    model_price_source: str | None = None


@dataclass(frozen=True)
class ExitDecision:
    """Auditable policy result for a position that IB still owns.

    ``model_exit_price`` is the shared daily-policy valuation, not a claimed IB
    fill.  The broker remains the source of truth for whether an order actually
    filled and for its final price, commission and slippage.
    """

    reason: str | None
    peak_ret: float
    triggered_at: datetime | None = None
    model_exit_price: float | None = None
    model_price_source: str | None = None


def _utc_timestamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _decision_bar(bar: tuple) -> tuple[pd.Timestamp, float, float, float, float]:
    """Accept the live four-field path and the kernel five-field OHLC path."""
    if len(bar) == 5:
        ts, open_price, high, low, close = bar
    elif len(bar) == 4:
        ts, high, low, close = bar
        open_price = close
    else:
        raise ValueError(f"Unsupported price bar shape: {len(bar)}")
    return (
        _utc_timestamp(ts), float(open_price), float(high), float(low), float(close),
    )


def _existing_position_exit_decision(
    pos: dict,
    price_path: list[tuple],
    prob_path: list[tuple],
    policy: dict,
    now: datetime,
) -> ExitDecision:
    """Return the first exit already triggered for an actual IB position."""
    entry_ts = _utc_timestamp(pos["entry_ts"])
    if now.date() <= entry_ts.date():
        return ExitDecision(None, 0.0)

    t_e = _utc_timestamp(pos["t_e"])
    cutoff = t_e - RESOLUTION_CUT
    now_ts = _utc_timestamp(now)

    entry_price = float(pos.get("entry_price") or 0.0)
    atr_pct = float(pos.get("atr_pct") or 0.0)
    atr_mult = float(policy.get("atr_mult") or 0.0)
    lock_activate = float(policy.get("lock_activate") or 0.0)
    theta_out = float(policy.get("theta_out") or 0.0)

    polarity, _source = resolve_polarity(pos.get("question", ""), pos.get("symbol"))
    effective_by_day: dict[object, float] = {}
    if polarity in {-1, 1}:
        for ts, probability in effective_prob_path(prob_path, polarity):
            effective_by_day[_utc_timestamp(ts).date()] = float(probability)

    peak = 0.0
    entry_date = entry_ts.date()
    last_eligible_close: tuple[pd.Timestamp, float] | None = None
    for raw_bar in price_path:
        bar_time, open_price, high, low, close = _decision_bar(raw_bar)
        if bar_time.date() <= entry_date:
            continue
        if bar_time >= cutoff or bar_time.normalize() > now_ts.normalize():
            break
        last_eligible_close = (bar_time, close)

        if entry_price > 0 and atr_pct > 0 and atr_mult > 0:
            stop_dist = atr_mult * atr_pct
            active_stop = entry_price * (1.0 + peak - stop_dist)
            reason = "trailing-ATR"
            if peak >= lock_activate:
                hard_floor = int(peak * 100.0) / 100.0
                lock_stop = entry_price * (1.0 + hard_floor)
                if lock_stop >= active_stop:
                    active_stop = lock_stop
                    reason = "profit-lock"
            if low <= active_stop:
                model_price = open_price if open_price <= active_stop else active_stop
                return ExitDecision(
                    reason, peak, bar_time.to_pydatetime(), model_price,
                    "daily_policy_stop",
                )

        probability = effective_by_day.get(bar_time.date())
        if theta_out > 0 and probability is not None and probability < theta_out:
            return ExitDecision(
                "probability-out", peak, bar_time.to_pydatetime(), close,
                "daily_close_after_probability_signal",
            )

        if entry_price > 0:
            peak = max(peak, high / entry_price - 1.0)

    # This lifecycle check deliberately does not depend on bars, probabilities
    # or the ability to reproduce the old entry decision.
    if now_ts >= cutoff:
        model_price = last_eligible_close[1] if last_eligible_close else None
        return ExitDecision(
            "resolution-1d", peak, cutoff.to_pydatetime(), model_price,
            "last_daily_close_before_resolution_cutoff" if model_price is not None else None,
        )
    return ExitDecision(None, peak)


def _existing_position_exit(
    pos: dict,
    price_path: list[tuple],
    prob_path: list[tuple],
    policy: dict,
    now: datetime,
) -> tuple[str | None, float]:
    """Return the first exit already triggered for an IB-held position.

    The old live implementation replayed entry selection on every tick.  Any
    later data/policy drift that made the historical entry fail caused the
    simulator to return ``None`` and made the real IB position immortal.  Exit
    rules only need the actual fill, ATR and event metadata already persisted
    for that position, so evaluate those facts directly and chronologically.
    """
    decision = _existing_position_exit_decision(
        pos, price_path, prob_path, policy, now,
    )
    return decision.reason, decision.peak_ret


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
        # Reserve within this scan as well as against IB.  Without this, two
        # different markets mapped to the same stock could both be returned in
        # one tick before run_entries had a chance to update open_symbols.
        reserved_symbols = set(open_symbols)
        reserved_market_assets = set(open_market_assets)

        for market in markets:
            t_e = market["end_at"]
            if t_e <= now + RESOLUTION_CUT:
                continue  # too close to resolution to enter

            for asset in market["assets"]:
                symbol = asset["symbol"]
                if symbol in reserved_symbols:
                    continue  # duplicate-symbol guard, as in the backtest
                if (market["market_id"], symbol) in reserved_market_assets:
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
                reserved_symbols.add(symbol)
                reserved_market_assets.add((market["market_id"], symbol))
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

            entry_day = pd.Timestamp(pos["entry_ts"]).normalize()
            price_path = _price_path(
                await store.daily_bars_since(
                    pos["symbol"], entry_day.to_pydatetime(),
                )
            )
            prob_path = _prob_path(await store.daily_prob_closes(pos["market_id"]))
            decision = _existing_position_exit_decision(
                pos, price_path, prob_path, self.policy, now,
            )
            reason, peak_ret = decision.reason, decision.peak_ret
            if abs(peak_ret - float(pos.get("peak_ret") or 0.0)) > 1e-9:
                await store.update_peak(pos["position_id"], peak_ret)
            if reason is not None:
                if hasattr(store, "upsert_policy_exit_audit"):
                    await store.upsert_policy_exit_audit(
                        position=pos,
                        reason=reason,
                        triggered_at=decision.triggered_at or now,
                        model_exit_price=decision.model_exit_price,
                        model_price_source=decision.model_price_source,
                        observed_at=now,
                    )
                signals.append(ExitSignal(
                    position_id=pos["position_id"], symbol=pos["symbol"],
                    qty=int(pos["qty"]), reason=reason,
                    triggered_at=decision.triggered_at,
                    model_exit_price=decision.model_exit_price,
                    model_price_source=decision.model_price_source,
                ))
        return signals
