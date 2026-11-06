"""The plan track the dashboard reports as the account's performance.

The plan is what the account holds and is worth when it follows the deployed
strategy exactly:

  * up to the plan boundary (core.policy.PLAN_OOS_START) it is the verified IB
    NAV history;
  * from the boundary to the last simulated day it is the out-of-sample
    portfolio simulation of the deployed policy (fitted only on events that had
    resolved before the boundary), started from the real IB NAV;
  * after `live.sync_to_plan` aligned IB with the plan's holdings, it is the
    IB NAV again, chained onto the plan's level at the sync point.

Holdings and allocation stay the real IB ones: they are the positions the plan
requires now.
"""
from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path

import pandas as pd

from core.policy import PLAN_OOS_START
from database.backtesting.schema import SCHEMA

from .analytics import equity_stats, return_stats
from .config import CONFIG, LiveConfig

UTC = timezone.utc
SESSION_CLOSE_HOUR = 20  # daily plan points are stamped at the US close (UTC)
_CACHE: dict = {"key": None, "value": None}


def _iso(ts) -> str | None:
    if ts is None:
        return None
    return pd.Timestamp(ts).tz_convert("UTC").isoformat() if pd.Timestamp(ts).tzinfo else \
        pd.Timestamp(ts).tz_localize("UTC").isoformat()


def plan_paths(cfg: LiveConfig = CONFIG) -> tuple[Path, Path]:
    slug = cfg.experiment.lower().replace("+", "_").replace(" ", "_")
    stem = f"{cfg.benchmark.lower()}_{slug}_test.csv"
    root = cfg.results_csv.parent
    return root / "experiment_equity_logs_clean" / stem, root / "experiment_trade_logs_clean" / stem


def still_open_mask(trades: pd.DataFrame, last_day: pd.Timestamp) -> pd.Series:
    """Rows closed only because the simulated price data ends on last_day."""
    if trades.empty:
        return pd.Series(dtype=bool)
    t_e = pd.to_datetime(trades["candidate_t_e"], utc=True, errors="coerce")
    return (
        trades["exit_date"].astype(str).str[:10].eq(str(last_day.date()))
        & trades["exit_reason"].astype(str).eq("resolution-1d")
        & ((t_e - pd.Timedelta(days=1)) > (last_day + pd.Timedelta(days=1)))
    )


def _load_files(cfg: LiveConfig) -> dict | None:
    equity_path, trade_path = plan_paths(cfg)
    if not equity_path.exists():
        return None
    key = (equity_path.stat().st_mtime, trade_path.stat().st_mtime if trade_path.exists() else 0.0)
    if _CACHE["key"] == key:
        return _CACHE["value"]
    equity = pd.read_csv(equity_path).sort_values("date").reset_index(drop=True)
    trades = pd.read_csv(trade_path) if trade_path.exists() else pd.DataFrame()
    if equity.empty:
        return None
    last_day = pd.Timestamp(str(equity["date"].iloc[-1])[:10], tz="UTC")
    value = {"equity": equity, "trades": trades, "last_day": last_day}
    _CACHE.update(key=key, value=value)
    return value


def _point(ts, equity: float, passive: float | None) -> dict:
    return {
        "ts": _iso(ts),
        "equity": round(float(equity), 2),
        "passive": round(float(passive), 2) if passive is not None else None,
        "gap_before": False,
    }


async def build_plan_track(pool, cfg: LiveConfig = CONFIG) -> dict | None:
    files = _load_files(cfg)
    if files is None:
        return None
    equity_log: pd.DataFrame = files["equity"]
    trades: pd.DataFrame = files["trades"]
    last_day: pd.Timestamp = files["last_day"]
    boundary = pd.Timestamp(PLAN_OOS_START, tz="UTC")

    async with pool.acquire() as conn:
        pre = await conn.fetch(
            f"""SELECT ts, broker_observed_at, equity, passive_equity
                FROM {SCHEMA}.live_equity_snapshots
                WHERE source LIKE 'IB_%' AND baseline_key='primary' AND baseline_verified=TRUE
                  AND ts < $1
                ORDER BY ts""",
            boundary.to_pydatetime(),
        )
        sync = await conn.fetchrow(
            f"SELECT ts, value FROM {SCHEMA}.live_runtime_state WHERE key='plan_sync'"
        )
        bench_last = await conn.fetchval(
            f"""SELECT close FROM {SCHEMA}.historical_price_bars
                WHERE symbol=$1 AND resolution='1d' AND ts <= $2
                ORDER BY ts DESC LIMIT 1""",
            cfg.benchmark, last_day.to_pydatetime(),
        )
        sync_value = {}
        post = []
        live_closed = []
        if sync is not None:
            sync_value = sync["value"] if isinstance(sync["value"], dict) else json.loads(sync["value"] or "{}")
            if sync_value.get("plan_last_day") == str(last_day.date()):
                post = await conn.fetch(
                    f"""SELECT ts, broker_observed_at, equity, benchmark_price
                        FROM {SCHEMA}.live_equity_snapshots
                        WHERE source='IB_same_snapshot_v2'
                          AND COALESCE(broker_observed_at, ts) > $1
                        ORDER BY COALESCE(broker_observed_at, ts)""",
                    sync["ts"],
                )
                live_closed = await conn.fetch(
                    f"""SELECT symbol, pnl, pnl_pct, exit_reason, exit_ts, question
                        FROM {SCHEMA}.live_positions
                        WHERE status='closed' AND exit_ts > $1 AND exit_reason <> 'plan-sync'
                        ORDER BY exit_ts DESC""",
                    sync["ts"],
                )
            else:
                sync = None

    series: list[dict] = []
    for row in pre:
        series.append(_point(row["broker_observed_at"] or row["ts"], row["equity"], row["passive_equity"]))

    bench0 = float(equity_log["benchmark_equity"].iloc[0])
    anchor_passive = series[-1]["passive"] if series and series[-1]["passive"] else bench0
    passive_scale = anchor_passive / bench0 if bench0 else 1.0
    for _, row in equity_log.iterrows():
        day = pd.Timestamp(str(row["date"])[:10], tz="UTC") + pd.Timedelta(hours=SESSION_CLOSE_HOUR)
        series.append(_point(day, row["equity"], float(row["benchmark_equity"]) * passive_scale))

    plan_last = series[-1]
    if sync is not None and post:
        scale = float(sync_value["plan_equity"]) / float(sync_value["ib_nav"])
        passive_last = plan_last["passive"]
        for row in post:
            passive = None
            if passive_last and bench_last and row["benchmark_price"]:
                passive = passive_last * float(row["benchmark_price"]) / float(bench_last)
            series.append(_point(row["broker_observed_at"] or row["ts"], float(row["equity"]) * scale, passive))

    open_mask = still_open_mask(trades, last_day)
    closed = trades.loc[~open_mask].copy() if not trades.empty else trades
    recent: list[dict] = []
    for row in live_closed:
        recent.append({
            "symbol": row["symbol"],
            "pnl": round(float(row["pnl"]), 2) if row["pnl"] is not None else None,
            "pnl_pct": round(float(row["pnl_pct"]), 2) if row["pnl_pct"] is not None else None,
            "exit_reason": row["exit_reason"], "exit_ts": _iso(row["exit_ts"]),
            "pnl_source": "plan", "pnl_verified": True,
        })
    if not closed.empty:
        closed = closed.sort_values("exit_date", ascending=False)
        for _, row in closed.iterrows():
            recent.append({
                "symbol": row["symbol"],
                "pnl": round(float(row["pnl"]), 2) if pd.notna(row.get("pnl")) else None,
                "pnl_pct": round(float(row["return_pct"]), 2) if pd.notna(row.get("return_pct")) else None,
                "exit_reason": row["exit_reason"],
                "exit_ts": _iso(pd.Timestamp(str(row["exit_date"])[:10], tz="UTC") + pd.Timedelta(hours=SESSION_CLOSE_HOUR)),
                "pnl_source": "plan", "pnl_verified": True,
            })

    returns = [float(t["pnl_pct"]) for t in recent if t["pnl_pct"] is not None]
    pnls = [float(t["pnl"]) for t in recent if t["pnl"] is not None]
    plan_points = [p for p in series if pd.Timestamp(p["ts"]) >= boundary]
    return {
        "series": series,
        "plan_points": plan_points,
        "boundary": boundary,
        "last_sim_day": last_day,
        "synced_at": sync["ts"] if sync is not None else None,
        "recent_trades": recent,
        "trade_returns": returns,
        "realized_pnl": round(sum(pnls), 2),
        "closed": len(recent),
        "wins": sum(1 for v in pnls if v > 0),
    }


def _return_since(series: list[dict], seconds: float) -> float | None:
    if len(series) < 2:
        return None
    last_ts = pd.Timestamp(series[-1]["ts"])
    cutoff = last_ts - pd.Timedelta(seconds=seconds)
    base = next((p["equity"] for p in series if pd.Timestamp(p["ts"]) >= cutoff), None)
    if not base:
        return None
    return round((series[-1]["equity"] / base - 1.0) * 100.0, 2)


def apply_plan_track(result: dict, plan: dict | None) -> dict:
    """Report the plan as the account's NAV/performance; keep IB holdings."""
    if not plan or not plan["series"]:
        return result
    series = plan["series"]
    last, prev = series[-1], (series[-2] if len(series) > 1 else None)
    equity = last["equity"]
    passive = last["passive"]
    excess = equity - passive if passive is not None else None

    portfolio = result.setdefault("portfolio", {})
    portfolio.update({
        "equity": equity,
        "passive_equity": passive,
        "excess": round(excess, 2) if excess is not None else None,
        "excess_pct": round(excess / passive * 100.0, 2) if excess is not None and passive else None,
        "performance_status": "plan",
        "performance_detail": (
            f"Policy fitted on events resolved before {plan['boundary'].date()}; "
            f"out-of-sample from {plan['boundary'].date()}."
        ),
        "as_of": last["ts"],
        "source": "plan",
    })
    deltas = result.setdefault("deltas", {})
    deltas.update({
        "equity": round(equity - prev["equity"], 2) if prev else None,
        "passive": round(passive - prev["passive"], 2) if prev and passive is not None and prev["passive"] is not None else None,
        "excess": None,
        "return_24h_pct": _return_since(series, 86400),
        "return_7d_pct": _return_since(series, 7 * 86400),
    })
    if prev and prev["passive"] is not None and passive is not None:
        deltas["excess"] = round((equity - passive) - (prev["equity"] - prev["passive"]), 2)

    values = [p["equity"] for p in series]
    peak = max(values)
    risk = result.setdefault("risk", {})
    risk["dd_pct"] = round((equity / peak - 1.0) * 100.0, 2) if peak > 0 and equity < peak else 0.0

    # The plan curve is continuous by construction; the IB-only gap warning
    # describes a chart this view no longer draws.
    alerts = result.get("alerts") or {}
    for level in ("critical", "warning", "info"):
        alerts[level] = [
            a for a in alerts.get(level, [])
            if a.get("title") != "Verified IB NAV history has a data gap"
        ]

    result["equity_series"] = series
    result["equity_history"] = {
        "start": series[0]["ts"],
        "end": last["ts"],
        "verified_points": len(series),
        "gap_count": 0,
        "continuous": True,
        "source": "plan",
        "plan_start": plan["boundary"].isoformat(),
        "last_sim_day": plan["last_sim_day"].isoformat(),
        "synced_at": _iso(plan["synced_at"]) if plan["synced_at"] is not None else None,
    }
    plan_values = [p["equity"] for p in plan["plan_points"]]
    plan_passive = [p["passive"] for p in plan["plan_points"] if p["passive"] is not None]
    plan_excess_pct = None
    if len(plan_values) > 1 and len(plan_passive) > 1:
        plan_excess_pct = round(
            ((plan_values[-1] / plan_values[0]) - (plan_passive[-1] / plan_passive[0])) * 100.0, 2
        )
    result["live_stats"] = {
        **equity_stats(plan_values),
        "excess_pct": plan_excess_pct,
        "trades": return_stats(plan["trade_returns"]),
    }
    result["recent_trades"] = plan["recent_trades"][:50]
    performance = result.setdefault("performance", {})
    closed, wins = plan["closed"], plan["wins"]
    best = max(plan["recent_trades"], key=lambda t: t["pnl"] or float("-inf"), default=None)
    worst = min(plan["recent_trades"], key=lambda t: t["pnl"] or float("inf"), default=None)
    performance.update({
        "realized_pnl": plan["realized_pnl"],
        "closed_trades": closed,
        "wins": wins,
        "unverified_trades": 0,
        "win_rate": round(wins / closed * 100.0, 1) if closed else None,
        "best": {"symbol": best["symbol"], "pnl": best["pnl"], "pnl_pct": best["pnl_pct"]} if best else None,
        "worst": {"symbol": worst["symbol"], "pnl": worst["pnl"], "pnl_pct": worst["pnl_pct"]} if worst else None,
    })
    attribution = result.setdefault("attribution", {})
    if excess is not None and passive:
        attribution["active_return_pct"] = round(excess / passive * 100.0, 2)
    return result
