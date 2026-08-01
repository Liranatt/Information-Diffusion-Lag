"""Rebuild the explanatory policy ledger without sending broker orders.

IB remains authoritative for actual inventory, marks, fills and account value.
This script answers a different, explicitly labelled question: given the
persisted strategy metadata and price/probability history, which IB-held
positions had a deterministic exit signal, what is the best recorded
first-executable valuation, and what would the policy target *now* after those
exits?  The result is written only to read-only dashboard audit tables.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
from datetime import datetime, timedelta, timezone

import pandas as pd

from database.backtesting.schema import SCHEMA
from live.config import CONFIG
from live.database import LiveStore
from live.policy import kelly_size, load_live_policy
from live.strategy_engine import (
    StrategyEngine,
    _existing_position_exit_decision,
    _prob_path,
)


UTC = timezone.utc


def _json_value(value):
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def _signal_ready_at(reason: str, triggered_at: datetime) -> datetime:
    """Daily signals become executable after the US regular-session close."""
    if reason == "resolution-1d":
        return triggered_at.astimezone(UTC)
    day = pd.Timestamp(triggered_at).tz_convert("UTC").normalize()
    return (day + pd.Timedelta(hours=20, minutes=1)).to_pydatetime()


async def _first_hour_bar(conn, symbol: str, start: datetime, end: datetime):
    return await conn.fetchrow(
        f"""SELECT ts,open,close
            FROM {SCHEMA}.historical_price_bars
            WHERE symbol=$1 AND resolution='1h' AND ts >= $2 AND ts <= $3
            ORDER BY ts LIMIT 1""",
        symbol, start, end,
    )


async def rebuild(*, apply: bool) -> dict:
    store = await LiveStore.create()
    try:
        policy = load_live_policy(CONFIG)
        engine = StrategyEngine(policy)
        async with store.pool.acquire() as conn:
            account = await conn.fetchrow(
                f"""SELECT * FROM {SCHEMA}.live_broker_account_cache
                    WHERE cache_key='primary' AND source='IB'"""
            )
            broker_rows = await conn.fetch(
                f"""SELECT * FROM {SCHEMA}.live_broker_positions_cache
                    WHERE source='IB' ORDER BY symbol"""
            )
        if not account or account["observed_at"] is None:
            raise RuntimeError("No authoritative IB account snapshot is cached")

        as_of = account["observed_at"].astimezone(UTC)
        broker = {str(row["symbol"]): dict(row) for row in broker_rows}
        open_positions = [
            pos for pos in await store.open_positions()
            if float((broker.get(str(pos["symbol"])) or {}).get("qty") or 0.0) > 1e-6
        ]

        exits: list[dict] = []
        retained: list[dict] = []
        for pos in open_positions:
            bars = await store.daily_bars_since(
                pos["symbol"], pd.Timestamp(pos["entry_ts"]).normalize().to_pydatetime(),
            )
            rich_path = [
                (
                    pd.Timestamp(row["ts"]).tz_convert("UTC").normalize(),
                    float(row["open"]), float(row["high"]),
                    float(row["low"]), float(row["close"]),
                )
                for row in bars
            ]
            probs = _prob_path(await store.daily_prob_closes(pos["market_id"]))
            decision = _existing_position_exit_decision(
                pos, rich_path, probs, policy, as_of,
            )
            ib_row = broker[pos["symbol"]]
            ib_qty = float(ib_row["qty"])
            if decision.reason is None:
                retained.append({
                    "position_id": int(pos["position_id"]),
                    "symbol": pos["symbol"],
                    "qty": ib_qty,
                    "entry_price": float(ib_row.get("avg_cost") or pos["entry_price"]),
                    "last": float(ib_row.get("market_price") or 0.0),
                    "unrealized_pnl": float(ib_row.get("unrealized_pnl") or 0.0),
                    "source": "IB",
                })
                continue

            triggered_at = decision.triggered_at or as_of
            ready_at = _signal_ready_at(decision.reason, triggered_at)
            async with store.pool.acquire() as conn:
                symbol_bar = await _first_hour_bar(conn, pos["symbol"], ready_at, as_of)
                market_clock = await _first_hour_bar(conn, CONFIG.benchmark, ready_at, as_of)

            first_executable_at = None
            model_price = decision.model_exit_price
            model_source = decision.model_price_source
            if symbol_bar:
                first_executable_at = symbol_bar["ts"].astimezone(UTC)
                model_price = float(symbol_bar["open"] or symbol_bar["close"])
                model_source = "first_stored_1h_open_after_daily_signal"
                status = "missed_exit_still_open_at_IB"
            elif market_clock:
                first_executable_at = market_clock["ts"].astimezone(UTC)
                status = "missed_exit_model_price_only"
            else:
                status = "pending_next_market_session"

            audit_position = dict(pos)
            audit_position["qty"] = ib_qty
            current_unrealized = float(ib_row.get("unrealized_pnl") or 0.0)
            details = {
                "signal_ready_at": ready_at.isoformat(),
                "policy_as_reconstructed_now": policy,
                "historical_policy_version_available": False,
                "valuation_is_not_fill": True,
                "hypothetical_exit_commission_included": False,
            }
            if apply:
                await store.upsert_policy_exit_audit(
                    position=audit_position,
                    reason=decision.reason,
                    triggered_at=triggered_at,
                    first_executable_at=first_executable_at,
                    model_exit_price=model_price,
                    model_price_source=model_source,
                    observed_at=as_of,
                    current_ib_unrealized_pnl=current_unrealized,
                    actual_ib_status=status,
                    details=details,
                )

            entry_price = float(pos["entry_price"])
            entry_costs = float(pos.get("entry_costs") or 0.0)
            gross = ib_qty * (float(model_price) - entry_price) if model_price is not None else None
            expected_net = gross - entry_costs if gross is not None else None
            exits.append({
                "position_id": int(pos["position_id"]),
                "symbol": pos["symbol"],
                "qty": ib_qty,
                "reason": decision.reason,
                "triggered_at": triggered_at.isoformat(),
                "first_executable_at": first_executable_at.isoformat()
                    if first_executable_at else None,
                "model_exit_price": round(float(model_price), 4)
                    if model_price is not None else None,
                "model_price_source": model_source,
                "expected_gross_pnl": round(gross, 2) if gross is not None else None,
                "expected_net_before_exit_cost": round(expected_net, 2)
                    if expected_net is not None else None,
                "current_ib_unrealized_pnl": round(current_unrealized, 2),
                "post_exit_opportunity_delta": round(current_unrealized - expected_net, 2)
                    if expected_net is not None else None,
                "status": status,
            })

        # The current target is recomputed from current stored signals after
        # removing every policy-exit obligation.  It deliberately does not
        # claim which unknown historical fills would have occurred.
        retained_symbols = {row["symbol"] for row in retained}
        retained_market_assets = {
            (pos["market_id"], pos["symbol"])
            for pos in open_positions if pos["symbol"] in retained_symbols
        }
        markets = await store.active_markets()
        signals = await engine.scan_entries(
            store, markets, retained_symbols, retained_market_assets, now=as_of,
        )
        slots = max(0, int(policy["max_concurrent"]) - len(retained))
        history = await store.realized_trades(limit=50)
        position_size = (
            kelly_size(history, float(policy["position_size_pct"]))
            if CONFIG.use_kelly else float(policy["position_size_pct"])
        )
        equity = float(account["net_liquidation"] or 0.0)
        allocation = equity * position_size
        replacements: list[dict] = []
        for signal in signals[:slots]:
            price = await store.latest_close(signal.symbol)
            qty = math.floor(allocation / price) if price and price > 0 else 0
            replacements.append({
                "market_id": signal.market_id,
                "symbol": signal.symbol,
                "question": signal.question,
                "reference_price": round(float(price), 4) if price else None,
                "model_qty": qty,
                "model_notional": round(qty * float(price), 2) if price else None,
                "entry_prob": round(float(signal.prob), 4),
                "atr_pct": round(float(signal.atr_pct), 6),
                "status": "candidate_for_next_executable_tick",
                "source": "current_policy_and_recorded_daily_data",
            })

        target = [
            {**row, "role": "retain_IB_position"} for row in retained
        ] + [
            {**row, "role": "new_policy_candidate"} for row in replacements
        ]
        target_event_value = sum(
            float(row.get("last") or 0.0) * float(row.get("qty") or 0.0)
            for row in retained
        ) + sum(float(row.get("model_notional") or 0.0) for row in replacements)
        benchmark_residual = max(0.0, equity - target_event_value)
        note = (
            "IB is actual truth. Target removes deterministic exit obligations, "
            "retains positions with no exit, adds only signals firing on the latest "
            "stored session, and assigns residual capital to SPY. No order is sent. "
            "Historical policy versions were not persisted before this repair, so "
            "historical exit valuations use the currently deployed policy."
        )
        if apply:
            await store.save_policy_target_snapshot(
                as_of=as_of,
                policy=policy,
                retained_positions=retained,
                required_exits=exits,
                replacement_candidates=replacements,
                target_positions=target,
                benchmark_residual=benchmark_residual,
                method="IB_actual_plus_current_policy_reconstruction_v1",
                note=note,
            )

        return {
            "apply": apply,
            "as_of": as_of,
            "ib_equity": equity,
            "policy": policy,
            "required_exits": exits,
            "retained_positions": retained,
            "replacement_candidates": replacements,
            "target_positions": target,
            "benchmark_residual": round(benchmark_residual, 2),
            "method_note": note,
        }
    finally:
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(rebuild(apply=args.apply))
    print(json.dumps(result, default=_json_value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
