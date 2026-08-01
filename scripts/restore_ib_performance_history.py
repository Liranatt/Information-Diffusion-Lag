"""Restore the live performance baseline from preserved IB Gateway evidence.

The July 9 deployment log contains direct ``NetLiquidation`` snapshots from
the broker before the old code switched to an internal cash ledger.  That
switch made the displayed NAV fall by $33,025 without an account event.  This
one-off, idempotent migration preserves the false legacy series for audit but
uses only direct IB observations for performance.

Run without ``--apply`` to preview.  The production invocation is::

    python -m scripts.restore_ib_performance_history --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

from database.backtesting.schema import SCHEMA
from database.db_connection import connect
from live.performance import passive_equity


UTC = timezone.utc
SOURCE = "IB_historical_log_v1"
NOTE = (
    "Restored from preserved IB Gateway/API logs. At 2026-07-09T16:56:40.773Z "
    "the running direct-IB code logged NetLiquidation=251760.47; IB's SPY "
    "PortfolioItem at 2026-07-09T16:56:40.956Z logged marketPrice=750.4699707. "
    "The later internal-ledger series is retained as legacy and excluded."
)


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


# Only observations where the account snapshot and IB SPY PortfolioItem were
# within about one second are included.  Later direct-IB points with wider
# timestamp gaps remain in the server log but are not promoted to verified NAV.
POINTS = (
    {
        "observed_at": dt("2026-07-09T16:56:40.773Z"),
        "benchmark_observed_at": dt("2026-07-09T16:56:40.956Z"),
        "nav": 251_760.47,
        "cash": 23_939.83,
        "benchmark_shares": 31.0,
        "benchmark_price": 750.4699707,
        "open_positions": 8,
    },
    {
        "observed_at": dt("2026-07-09T17:11:42.253Z"),
        "benchmark_observed_at": dt("2026-07-09T17:11:41.097Z"),
        "nav": 251_793.15,
        "cash": 673.95,
        "benchmark_shares": 31.0,
        "benchmark_price": 751.3295288,
        "open_positions": 8,
    },
    {
        "observed_at": dt("2026-07-09T18:11:29.173Z"),
        "benchmark_observed_at": dt("2026-07-09T18:11:29.408Z"),
        "nav": 252_112.47,
        "cash": 52_810.32,
        "benchmark_shares": 100.0,
        "benchmark_price": 751.73480225,
        "open_positions": 8,
    },
)


async def migrate(apply: bool) -> None:
    baseline = POINTS[0]
    if not apply:
        print(json.dumps({
            "apply": False,
            "source": SOURCE,
            "baseline": baseline,
            "points": len(POINTS),
            "note": "No database changes made; rerun with --apply.",
        }, default=str, indent=2))
        return

    conn = await connect()
    try:
        async with conn.transaction():
            current = await conn.fetchrow(
                f"""SELECT * FROM {SCHEMA}.live_performance_baselines
                    WHERE baseline_key='primary' FOR UPDATE"""
            )
            if current and current["start_ts"] != baseline["observed_at"]:
                archive_key = (
                    "superseded-primary-"
                    + current["start_ts"].astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
                )
                await conn.execute(
                    f"""INSERT INTO {SCHEMA}.live_performance_baselines
                        (baseline_key,start_ts,start_nav,benchmark_symbol,
                         benchmark_start_price,source,verified,note,created_at,updated_at)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                        ON CONFLICT (baseline_key) DO NOTHING""",
                    archive_key, current["start_ts"], current["start_nav"],
                    current["benchmark_symbol"], current["benchmark_start_price"],
                    current["source"], current["verified"], current["note"],
                    current["created_at"], current["updated_at"],
                )

            await conn.execute(
                f"""INSERT INTO {SCHEMA}.live_performance_baselines
                    (baseline_key,start_ts,start_nav,benchmark_symbol,
                     benchmark_start_price,source,verified,note)
                    VALUES ('primary',$1,$2,'SPY',$3,$4,TRUE,$5)
                    ON CONFLICT (baseline_key) DO UPDATE SET
                        start_ts=EXCLUDED.start_ts,start_nav=EXCLUDED.start_nav,
                        benchmark_symbol=EXCLUDED.benchmark_symbol,
                        benchmark_start_price=EXCLUDED.benchmark_start_price,
                        source=EXCLUDED.source,verified=TRUE,note=EXCLUDED.note,
                        updated_at=NOW()""",
                baseline["observed_at"], baseline["nav"],
                baseline["benchmark_price"], SOURCE, NOTE,
            )

            for point in POINTS:
                passive = passive_equity(
                    baseline["nav"], baseline["benchmark_price"],
                    point["benchmark_price"],
                )
                await conn.execute(
                    f"""INSERT INTO {SCHEMA}.live_equity_snapshots
                        (ts,equity,cash,benchmark_shares,benchmark_price,
                         open_positions,passive_equity,source,broker_observed_at,
                         benchmark_observed_at,baseline_key,baseline_verified,
                         cumulative_cash_flows)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$1,$9,'primary',TRUE,0)
                        ON CONFLICT (ts) DO UPDATE SET
                            equity=EXCLUDED.equity,cash=EXCLUDED.cash,
                            benchmark_shares=EXCLUDED.benchmark_shares,
                            benchmark_price=EXCLUDED.benchmark_price,
                            open_positions=EXCLUDED.open_positions,
                            passive_equity=EXCLUDED.passive_equity,
                            source=EXCLUDED.source,
                            broker_observed_at=EXCLUDED.broker_observed_at,
                            benchmark_observed_at=EXCLUDED.benchmark_observed_at,
                            baseline_key='primary',baseline_verified=TRUE,
                            cumulative_cash_flows=0""",
                    point["observed_at"], point["nav"], point["cash"],
                    point["benchmark_shares"], point["benchmark_price"],
                    point["open_positions"], passive, SOURCE,
                    point["benchmark_observed_at"],
                )

            await conn.execute(
                f"""UPDATE {SCHEMA}.live_equity_snapshots
                    SET baseline_key=NULL, baseline_verified=FALSE
                    WHERE source='legacy'"""
            )

            flows = await conn.fetch(
                f"""SELECT flow_ts,amount,benchmark_price
                    FROM {SCHEMA}.live_account_cash_flows
                    WHERE flow_ts > $1 ORDER BY flow_ts""",
                baseline["observed_at"],
            )
            trusted = await conn.fetch(
                f"""SELECT ts,broker_observed_at,benchmark_price
                    FROM {SCHEMA}.live_equity_snapshots
                    WHERE source LIKE 'IB_%' AND broker_observed_at >= $1
                      AND benchmark_price > 0
                    ORDER BY broker_observed_at""",
                baseline["observed_at"],
            )
            for snapshot in trusted:
                applicable = [
                    dict(flow) for flow in flows
                    if flow["flow_ts"] <= snapshot["broker_observed_at"]
                ]
                passive = passive_equity(
                    baseline["nav"], baseline["benchmark_price"],
                    float(snapshot["benchmark_price"]), applicable,
                )
                await conn.execute(
                    f"""UPDATE {SCHEMA}.live_equity_snapshots
                        SET passive_equity=$2,baseline_key='primary',
                            baseline_verified=TRUE,cumulative_cash_flows=$3
                        WHERE ts=$1""",
                    snapshot["ts"], passive,
                    sum(float(flow["amount"]) for flow in applicable),
                )

            # The July 31 UNTY close filled 230 + 88 shares.  The old finalizer
            # used only the last 88; IB's zero-position PortfolioItem reported
            # the authoritative session realized P&L of $648.51.
            corrected = await conn.fetchrow(
                f"""UPDATE {SCHEMA}.live_positions AS p
                    SET exit_price=(
                            SELECT SUM(qty*fill_price)/SUM(qty)
                            FROM {SCHEMA}.live_orders
                            WHERE position_id=p.position_id
                              AND kind='exit' AND action='SELL'
                              AND fill_price IS NOT NULL AND qty>0
                        ),
                        exit_costs=(
                            SELECT SUM(COALESCE(commission,0))
                            FROM {SCHEMA}.live_orders
                            WHERE position_id=p.position_id
                              AND kind='exit' AND action='SELL'
                              AND fill_price IS NOT NULL AND qty>0
                        ),
                        pnl=648.51,
                        pnl_pct=648.51/NULLIF(qty*entry_price,0)*100,
                        pnl_source='IB_portfolio_realized_pnl',
                        pnl_verified=TRUE
                    WHERE symbol='UNTY' AND status='closed'
                      AND recovered_from_position_id IS NOT NULL
                      AND exit_ts::date=DATE '2026-07-31'
                    RETURNING p.position_id"""
            )

            await conn.execute(
                f"""INSERT INTO {SCHEMA}.live_reconciliation_events
                    (observed_at,severity,symbol,event_type,broker_value,
                     database_value,details,source_of_truth)
                    SELECT $1,'info','UNTY','historical_partial_exit_pnl_corrected',
                           648.51,180.60,$2::jsonb,'IB'
                    WHERE NOT EXISTS (
                        SELECT 1 FROM {SCHEMA}.live_reconciliation_events
                        WHERE event_type='historical_partial_exit_pnl_corrected'
                          AND symbol='UNTY'
                    )""",
                dt("2026-07-31T18:26:22.937Z"),
                json.dumps({
                    "fills": [230, 88],
                    "source": "IB updatePortfolio realizedPNL",
                    "corrected_position_id": (
                        int(corrected["position_id"]) if corrected else None
                    ),
                }),
            )

        print(json.dumps({
            "apply": True,
            "baseline_start": baseline["observed_at"],
            "baseline_nav": baseline["nav"],
            "baseline_spy": baseline["benchmark_price"],
            "historical_points": len(POINTS),
            "corrected_unty_position": (
                int(corrected["position_id"]) if corrected else None
            ),
        }, default=str, indent=2))
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(migrate(args.apply))


if __name__ == "__main__":
    main()
