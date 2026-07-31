"""Reconcile the DB's view of the portfolio with the IB paper account."""
from __future__ import annotations

import logging

from .config import LiveConfig
from .connection import IBConnection
from .database import LiveStore

log = logging.getLogger("live.positions")


class PositionManager:
    def __init__(self, cfg: LiveConfig, ib_conn: IBConnection, store: LiveStore) -> None:
        self.cfg = cfg
        self.ib_conn = ib_conn
        self.store = store

    async def snapshot(self) -> dict:
        """Authoritative IB portfolio state used for sizing and execution.

        `valid` is False when the live IB balance could not be read (account
        farm reconnecting, request timeout). The control loop must then skip
        trading and skip writing a NAV snapshot rather than act on a phantom
        zero balance.  Postgres is populated from this result only as a cache.
        """
        valid = True
        net_liquidation = None
        account_summary: dict[str, float] = {}
        broker_positions: dict[str, dict[str, float]] = {}
        if self.cfg.dry_run:
            cash: float | None = 0.0
            ib_positions: dict[str, float] = {}
        else:
            try:
                account_summary = await self.ib_conn.account_summary() or {}
                cash = account_summary.get("TotalCashValue")
                net_liquidation = account_summary.get("NetLiquidation")
                broker_positions = await self.ib_conn.portfolio_snapshot()
                ib_positions = {
                    symbol: float(row["qty"])
                    for symbol, row in broker_positions.items()
                }
            except Exception as error:  # noqa: BLE001 - IB warm-up / timeouts
                log.warning("IB account query failed (%s) -- snapshot marked incomplete",
                            type(error).__name__)
                cash, net_liquidation, ib_positions, valid = None, None, {}, False
            if cash is None or net_liquidation is None:
                valid = False
                log.warning("IB returned no cash/NAV balance -- snapshot marked incomplete")

        open_db = await self.store.open_positions()
        benchmark_shares = float(ib_positions.get(self.cfg.benchmark, 0.0))
        benchmark_row = broker_positions.get(self.cfg.benchmark, {})
        benchmark_price = float(benchmark_row.get("market_price") or 0.0) or None
        if benchmark_price is None and not self.cfg.dry_run and valid:
            benchmark_price = await self.ib_conn.last_price(self.cfg.benchmark)
        if benchmark_price is None:
            benchmark_price = await self.store.latest_close(self.cfg.benchmark)

        open_value = sum(
            float(row.get("market_value") or 0.0)
            for symbol, row in broker_positions.items()
            if symbol != self.cfg.benchmark
        )
        if self.cfg.dry_run:
            open_value = 0.0
            for pos in open_db:
                price = await self.store.latest_close(pos["symbol"]) or float(pos["entry_price"])
                open_value += int(pos["qty"]) * price
            equity = (cash or 0.0) + benchmark_shares * (benchmark_price or 0.0) + open_value
        else:
            # NetLiquidation is IB's authoritative account value.  Never rebuild
            # live NAV by mixing cached cash, cached fills, or DB quantities.
            equity = float(net_liquidation or 0.0)

        strategy_symbols = {str(pos["symbol"]) for pos in open_db}
        metadata_gaps = sorted(
            symbol for symbol, qty in ib_positions.items()
            if symbol != self.cfg.benchmark
            and abs(float(qty)) > 1e-6
            and symbol not in strategy_symbols
        )

        snapshot = {
            "cash": cash or 0.0,
            "benchmark_shares": benchmark_shares,
            "ib_benchmark_shares": benchmark_shares,
            "benchmark_price": benchmark_price,
            "open_positions": open_db,
            "open_value": open_value,
            "equity": equity,
            "ib_positions": ib_positions,
            "broker_positions": broker_positions,
            "broker_position_count": sum(
                1 for symbol, qty in ib_positions.items()
                if symbol != self.cfg.benchmark and abs(float(qty)) > 1e-6
            ),
            "account_summary": account_summary,
            "metadata_gaps": metadata_gaps,
            "valid": valid,
            "source": "IB" if not self.cfg.dry_run else "dry_run",
            "trade_safe": valid,
        }
        return snapshot
