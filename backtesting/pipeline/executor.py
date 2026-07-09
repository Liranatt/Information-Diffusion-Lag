"""IB paper trading execution via ib_insync.

Connects to IB Gateway, submits market orders, tracks positions,
and monitors for exit signals.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

logger = logging.getLogger(__name__)

IB_HOST = "127.0.0.1"
IB_PORT = 4002  # paper trading port
IB_CLIENT_ID = 1


@dataclass
class LiveTrade:
    symbol: str
    market_id: str
    direction: str  # "long"
    qty: int
    entry_price: float | None = None
    fill_price: float | None = None
    order_id: int | None = None
    status: str = "pending"
    entry_time: datetime | None = None
    exit_price: float | None = None
    exit_time: datetime | None = None
    exit_reason: str | None = None


class IBExecutor:
    def __init__(
        self,
        host: str = IB_HOST,
        port: int = IB_PORT,
        client_id: int = IB_CLIENT_ID,
    ):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.ib = None
        self.active_trades: list[LiveTrade] = []

    async def connect(self):
        from ib_insync import IB
        self.ib = IB()
        await self.ib.connectAsync(self.host, self.port, clientId=self.client_id)
        logger.info(f"Connected to IB Gateway at {self.host}:{self.port}")

    async def disconnect(self):
        if self.ib and self.ib.isConnected():
            self.ib.disconnect()
            logger.info("Disconnected from IB Gateway")

    def _make_contract(self, symbol: str):
        from ib_insync import Stock
        return Stock(symbol, "SMART", "USD")

    async def submit_order(self, symbol: str, qty: int, market_id: str) -> LiveTrade:
        """Submit a market buy order."""
        from ib_insync import MarketOrder

        contract = self._make_contract(symbol)
        await self.ib.qualifyContractsAsync(contract)

        order = MarketOrder("BUY", qty)
        trade = self.ib.placeOrder(contract, order)

        lt = LiveTrade(
            symbol=symbol,
            market_id=market_id,
            direction="long",
            qty=qty,
            order_id=trade.order.orderId,
            status="submitted",
            entry_time=datetime.now(timezone.utc),
        )
        self.active_trades.append(lt)
        logger.info(f"Submitted BUY {qty} {symbol} (order {lt.order_id})")
        return lt

    async def submit_trades(self, candidates: pd.DataFrame, capital_per_trade: float = 5000.0):
        """Submit market orders for all selected candidates."""
        results = []
        for _, row in candidates.iterrows():
            sym = row["symbol"]
            price = row.get("entry_price", row.get("predicted_return", 100))
            qty = max(1, int(capital_per_trade / price))
            try:
                lt = await self.submit_order(sym, qty, row["market_id"])
                results.append(lt)
            except Exception as e:
                logger.error(f"Failed to submit order for {sym}: {e}")
        return results

    async def close_position(self, trade: LiveTrade, reason: str):
        """Submit a market sell to close a position."""
        from ib_insync import MarketOrder

        contract = self._make_contract(trade.symbol)
        await self.ib.qualifyContractsAsync(contract)

        order = MarketOrder("SELL", trade.qty)
        self.ib.placeOrder(contract, order)

        trade.status = "closing"
        trade.exit_reason = reason
        trade.exit_time = datetime.now(timezone.utc)
        logger.info(f"Closing {trade.symbol}: {reason}")

    async def get_positions(self) -> list[dict]:
        """Get current IB portfolio positions."""
        positions = self.ib.positions()
        return [
            {
                "symbol": p.contract.symbol,
                "qty": p.position,
                "avg_cost": p.avgCost,
                "value": p.position * p.marketPrice if hasattr(p, "marketPrice") else None,
            }
            for p in positions
        ]

    async def get_account_summary(self) -> dict:
        """Get key account metrics."""
        summary = self.ib.accountSummary()
        return {item.tag: item.value for item in summary}
