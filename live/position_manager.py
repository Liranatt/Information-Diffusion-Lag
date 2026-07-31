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
        """Current portfolio state used by the control loop for sizing/sweeps.

        `valid` is False when the live IB balance could not be read (account
        farm reconnecting, request timeout). The control loop must then skip
        trading and skip writing a NAV snapshot rather than act on a phantom
        zero balance.
        """
        valid = True
        net_liquidation = None
        if self.cfg.dry_run:
            cash: float | None = 0.0
            ib_positions: dict[str, float] = {}
        else:
            try:
                summary = await self.ib_conn.account_summary()
                cash = summary.get("TotalCashValue") if summary else None
                net_liquidation = summary.get("NetLiquidation") if summary else None
                ib_positions = await self.ib_conn.portfolio_positions()
            except Exception as error:  # noqa: BLE001 - IB warm-up / timeouts
                log.warning("IB account query failed (%s) -- snapshot marked incomplete",
                            type(error).__name__)
                cash, net_liquidation, ib_positions, valid = None, None, {}, False
            if cash is None or net_liquidation is None:
                valid = False
                log.warning("IB returned no cash/NAV balance -- snapshot marked incomplete")

        open_db = await self.store.open_positions()
        ib_benchmark_shares = float(ib_positions.get(self.cfg.benchmark, 0.0))
        benchmark_shares = ib_benchmark_shares
        benchmark_price = await self.store.latest_close(self.cfg.benchmark)

        open_value = 0.0
        for pos in open_db:
            price = await self.store.latest_close(pos["symbol"]) or float(pos["entry_price"])
            open_value += int(pos["qty"]) * price

        # Equity/cash from a fill ledger, not IB's reported balances. IB *paper*
        # accounts inflate TotalCashValue/NetLiquidation via ghost/duplicate
        # fills; the ledger (all-cash start - net filled buys - commissions) is
        # immune, and it keeps sizing/sweeps honest instead of buying on phantom
        # cash. NetLiquidation is still read above purely for the `valid` gate.
        if not self.cfg.dry_run and valid:
            ledger_cash = await self.store.reconciled_cash()
            if ledger_cash is not None:
                cash = ledger_cash
                benchmark_shares = await self.store.reconciled_position_qty(
                    self.cfg.benchmark
                )
        equity = (cash or 0.0) + benchmark_shares * (benchmark_price or 0.0) + open_value

        snapshot = {
            "cash": cash or 0.0,
            "benchmark_shares": benchmark_shares,
            "ib_benchmark_shares": ib_benchmark_shares,
            "benchmark_price": benchmark_price,
            "open_positions": open_db,
            "open_value": open_value,
            "equity": equity,
            "ib_positions": ib_positions,
            "valid": valid,
        }
        snapshot["broker_drift"] = self._drift(snapshot)
        snapshot["trade_safe"] = valid and (
            self.cfg.dry_run or not snapshot["broker_drift"]
        )
        return snapshot

    async def report_drift(self, snapshot: dict) -> list[str]:
        """Symbols where IB holdings disagree with DB open positions."""
        drift = list(snapshot.get("broker_drift") or self._drift(snapshot))
        if drift:
            log.error("position drift detected; automated trading is blocked: %s",
                      "; ".join(drift))
        return drift

    def _drift(self, snapshot: dict) -> list[str]:
        """Return broker-vs-ledger quantity differences, including benchmark."""
        expected: dict[str, int] = {}
        for pos in snapshot["open_positions"]:
            expected[pos["symbol"]] = expected.get(pos["symbol"], 0) + int(pos["qty"])

        expected[self.cfg.benchmark] = float(snapshot["benchmark_shares"])

        drift: list[str] = []
        ib_positions = dict(snapshot["ib_positions"])
        for symbol, qty in expected.items():
            ib_qty = float(ib_positions.get(symbol, 0.0))
            if abs(ib_qty - float(qty)) > 1e-6:
                drift.append(f"{symbol}: ledger={qty:g} ib={ib_qty:g}")
        for symbol, qty in ib_positions.items():
            if symbol not in expected and abs(float(qty)) > 1e-6:
                drift.append(f"{symbol}: ledger=0 ib={float(qty):g}")
        return drift
