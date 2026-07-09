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
        """Current portfolio state used by the control loop for sizing/sweeps."""
        cash = await self.ib_conn.account_cash() if not self.cfg.dry_run else 0.0
        ib_positions = (
            await self.ib_conn.portfolio_positions() if not self.cfg.dry_run else {}
        )
        open_db = await self.store.open_positions()

        benchmark_shares = float(ib_positions.get(self.cfg.benchmark, 0.0))
        benchmark_price = await self.store.latest_close(self.cfg.benchmark)

        open_value = 0.0
        for pos in open_db:
            price = await self.store.latest_close(pos["symbol"]) or float(pos["entry_price"])
            open_value += int(pos["qty"]) * price

        equity = cash + benchmark_shares * (benchmark_price or 0.0) + open_value
        return {
            "cash": cash,
            "benchmark_shares": benchmark_shares,
            "benchmark_price": benchmark_price,
            "open_positions": open_db,
            "open_value": open_value,
            "equity": equity,
            "ib_positions": ib_positions,
        }

    async def report_drift(self, snapshot: dict) -> list[str]:
        """Symbols where IB holdings disagree with DB open positions."""
        expected: dict[str, int] = {}
        for pos in snapshot["open_positions"]:
            expected[pos["symbol"]] = expected.get(pos["symbol"], 0) + int(pos["qty"])

        drift: list[str] = []
        ib_positions = dict(snapshot["ib_positions"])
        ib_positions.pop(self.cfg.benchmark, None)
        for symbol, qty in expected.items():
            if ib_positions.get(symbol, 0) != qty:
                drift.append(f"{symbol}: db={qty} ib={ib_positions.get(symbol, 0)}")
        for symbol, qty in ib_positions.items():
            if symbol not in expected and qty != 0:
                drift.append(f"{symbol}: db=0 ib={qty}")
        if drift:
            log.warning("position drift detected: %s", "; ".join(drift))
        return drift
