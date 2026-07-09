"""Order execution for the live paper portfolio.

Mirrors the backtest's benchmark-rotation model with the fully-invested rule:

  entry:  cash first, then sell benchmark shares -> buy asset
  exit:   sell asset -> rebuy benchmark with the proceeds
  sweep:  every cycle, all idle cash above one benchmark share -> benchmark

So capital is always either in an event position or in the index -- the cash
drag identified in the backtest cannot occur.

Orders are plain MKT (we are swing trading on hourly signals; queue priority
is irrelevant, and paper fills on MKT are immediate). Every order is recorded
in live_orders with its fill.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

try:
    from ib_async import MarketOrder
except ImportError:  # pragma: no cover
    from ib_insync import MarketOrder  # type: ignore[no-redef]

from .config import LiveConfig
from .connection import IBConnection
from .database import LiveStore
from .utils import affordable_buy_qty, affordable_buy_qty_frac, ib_cost

log = logging.getLogger("live.orders")


class OrderManager:
    def __init__(self, cfg: LiveConfig, ib_conn: IBConnection, store: LiveStore) -> None:
        self.cfg = cfg
        self.ib_conn = ib_conn
        self.store = store

    # ── Low-level ────────────────────────────────────────────────────────

    def _bench_qty(self, cash: float, price: float) -> float:
        """Benchmark buy size: fractional when enabled, else whole shares."""
        if self.cfg.fractional_benchmark:
            return affordable_buy_qty_frac(cash, price)
        return float(affordable_buy_qty(cash, price))

    async def _execute(self, symbol: str, action: str, qty: float, *, kind: str,
                       position_id: int | None = None, note: str = "") -> float | None:
        """Place a market order and wait for the fill. Returns avg fill price."""
        if qty <= 0:
            return None
        if self.cfg.dry_run:
            price = await self.store.latest_close(symbol)
            log.info("[dry-run] %s %d %s @~%s (%s)", action, qty, symbol, price, kind)
            await self.store.record_order(
                ib_order_id=None, symbol=symbol, action=action, qty=qty, kind=kind,
                fill_price=price, status="dry_run", position_id=position_id, note=note,
            )
            return price

        ib = await self.ib_conn.ensure_connected()
        contract = await self.ib_conn.qualified_stock(symbol)
        if contract is None:
            await self.store.record_order(
                ib_order_id=None, symbol=symbol, action=action, qty=qty, kind=kind,
                fill_price=None, status="unqualified", position_id=position_id, note=note,
            )
            return None

        trade = ib.placeOrder(contract, MarketOrder(action, qty))
        deadline = asyncio.get_event_loop().time() + self.cfg.order_timeout_seconds
        while not trade.isDone() and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1.0)

        status = trade.orderStatus.status
        fill_price = float(trade.orderStatus.avgFillPrice or 0.0) or None
        await self.store.record_order(
            ib_order_id=trade.order.orderId, symbol=symbol, action=action, qty=qty,
            kind=kind, fill_price=fill_price, status=status,
            position_id=position_id, note=note,
        )
        if status not in {"Filled"}:
            log.warning("order not filled: %s %d %s -> %s", action, qty, symbol, status)
            if not trade.isDone():
                ib.cancelOrder(trade.order)
            return None
        return fill_price

    # ── Rotation legs ────────────────────────────────────────────────────

    async def enter_position(self, signal, *, desired_allocation: float,
                             benchmark_price: float, cash: float,
                             benchmark_shares: int, position_size_pct: float) -> dict | None:
        """Benchmark rotation entry. Returns the stored position dict or None."""
        entry_ref_price = await self.store.latest_close(signal.symbol)
        if entry_ref_price is None or entry_ref_price <= 0:
            return None
        if desired_allocation < max(entry_ref_price, self.cfg.min_order_notional):
            return None

        # Fund from idle cash first, then benchmark inventory (backtest parity).
        cash_contribution = min(max(cash, 0.0), desired_allocation)
        shortfall = desired_allocation - cash_contribution
        if shortfall > 0:
            desired_sell = (
                shortfall / benchmark_price if self.cfg.fractional_benchmark
                else int(shortfall / benchmark_price)
            )
            benchmark_sell_qty = round(min(desired_sell, benchmark_shares), 4)
        else:
            benchmark_sell_qty = 0.0
        if cash_contribution + benchmark_sell_qty * benchmark_price < entry_ref_price:
            return None

        funding = cash_contribution
        if benchmark_sell_qty > 0:
            fill = await self._execute(
                self.cfg.benchmark, "SELL", benchmark_sell_qty,
                kind="rotation_fund", note=f"fund {signal.symbol}",
            )
            if fill is None:
                return None
            funding += benchmark_sell_qty * fill - ib_cost(benchmark_sell_qty, fill, True)

        asset_qty = affordable_buy_qty(funding, entry_ref_price)
        if asset_qty < 1:
            # Undo: roll the funding back into the benchmark.
            if benchmark_sell_qty > 0:
                await self._execute(self.cfg.benchmark, "BUY", benchmark_sell_qty,
                                    kind="rotation_undo", note=f"undo {signal.symbol}")
            return None

        fill_price = await self._execute(signal.symbol, "BUY", asset_qty, kind="entry",
                                         note=signal.question[:100])
        if fill_price is None:
            if benchmark_sell_qty > 0:
                await self._execute(self.cfg.benchmark, "BUY", benchmark_sell_qty,
                                    kind="rotation_undo", note=f"undo {signal.symbol}")
            return None

        entry_costs = (
            (ib_cost(benchmark_sell_qty, benchmark_price, True) if benchmark_sell_qty else 0.0)
            + ib_cost(asset_qty, fill_price, False)
        )
        position = {
            "market_id": signal.market_id,
            "symbol": signal.symbol,
            "question": signal.question,
            "is_earnings": signal.is_earnings,
            "qty": asset_qty,
            "entry_ts": datetime.now(timezone.utc),
            "entry_price": fill_price,
            "entry_prob": signal.prob,
            "atr_pct": signal.atr_pct,
            "position_size_pct": position_size_pct,
            "benchmark_sell_qty": benchmark_sell_qty,
            "entry_costs": entry_costs,
            "t_e": signal.t_e,
        }
        position["position_id"] = await self.store.insert_position(position)
        log.info("ENTER %s x%d @ %.2f (%s, prob=%.3f)",
                 signal.symbol, asset_qty, fill_price, signal.question[:60], signal.prob)
        return position

    async def exit_position(self, pos: dict, reason: str,
                            benchmark_price: float | None) -> bool:
        """Sell the asset, rebuy the benchmark with the proceeds."""
        qty = int(pos["qty"])
        fill_price = await self._execute(pos["symbol"], "SELL", qty, kind="exit",
                                         position_id=pos["position_id"], note=reason)
        if fill_price is None:
            return False

        sell_cost = ib_cost(qty, fill_price, True)
        proceeds = qty * fill_price - sell_cost

        rebuy_qty = 0.0
        rebuy_cost = 0.0
        if benchmark_price and benchmark_price > 0:
            rebuy_qty = self._bench_qty(proceeds, benchmark_price)
            if rebuy_qty > 0:
                rebuy_fill = await self._execute(
                    self.cfg.benchmark, "BUY", rebuy_qty, kind="rotation_rebuy",
                    position_id=pos["position_id"], note=f"rebuy after {pos['symbol']}",
                )
                if rebuy_fill is not None:
                    rebuy_cost = ib_cost(rebuy_qty, rebuy_fill, False)

        gross_pnl = qty * (fill_price - float(pos["entry_price"]))
        exit_costs = sell_cost + rebuy_cost
        net_pnl = gross_pnl - float(pos["entry_costs"] or 0.0) - exit_costs
        exposure = max(qty * float(pos["entry_price"]), 1e-12)

        await self.store.close_position(
            int(pos["position_id"]),
            exit_ts=datetime.now(timezone.utc),
            exit_price=fill_price,
            exit_reason=reason,
            exit_costs=exit_costs,
            pnl=round(net_pnl, 2),
            pnl_pct=round(net_pnl / exposure * 100.0, 4),
        )
        log.info("EXIT %s x%d @ %.2f (%s) pnl=%.2f", pos["symbol"], qty, fill_price,
                 reason, net_pnl)
        return True

    async def sweep_idle_cash(self, *, cash: float, benchmark_price: float | None) -> float:
        """Fully-invested rule: idle cash -> benchmark shares."""
        if not benchmark_price or benchmark_price <= 0:
            return 0.0
        # Sub-threshold sweeps churn the $0.35 minimum commission for nothing.
        if cash < self.cfg.min_order_notional:
            return 0.0
        qty = self._bench_qty(cash, benchmark_price)
        if qty <= 0:
            return 0.0
        fill = await self._execute(self.cfg.benchmark, "BUY", qty, kind="cash_sweep",
                                   note="fully-invested sweep")
        return qty if fill is not None else 0.0
