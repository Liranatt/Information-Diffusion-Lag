"""Order execution for the live paper portfolio.

Mirrors the backtest's benchmark-rotation model with the fully-invested rule:

  entry:  cash first, then sell benchmark shares -> buy asset
  exit:   sell asset -> rebuy benchmark with the proceeds
  sweep:  every cycle, all idle cash above one benchmark share -> benchmark

So capital is always either in an event position or in the index -- the cash
drag identified in the backtest cannot occur.

Sells are market orders. Buys are capped limit orders when they are constrained
by an affordability budget, so the paper account never intentionally buys more
than cash + liquidatable benchmark inventory can fund. Every order is recorded
in live_orders with its fill.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

try:
    from ib_async import LimitOrder, MarketOrder
except ImportError:  # pragma: no cover
    from ib_insync import LimitOrder  # type: ignore[no-redef]
    from ib_insync import MarketOrder  # type: ignore[no-redef]

from .config import LiveConfig
from .connection import IBConnection
from .database import LiveStore
from .utils import (
    affordable_buy_qty,
    affordable_buy_qty_frac,
    benchmark_sell_qty_for_cash_deficit,
    ib_cost,
)

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

    def _buy_limit(self, reference_price: float, buffer_pct: float | None = None) -> float:
        """Maximum buy price used for affordability sizing and limit orders."""
        buffer = self.cfg.execution_buffer_pct if buffer_pct is None else buffer_pct
        return round(reference_price * (1.0 + buffer), 2)

    async def _execute(self, symbol: str, action: str, qty: float, *, kind: str,
                       position_id: int | None = None, note: str = "",
                       reference_price: float | None = None,
                       limit_price: float | None = None) -> float | None:
        """Place a market order and wait for the fill. Returns avg fill price.

        reference_price is the mark we decided at; recorded so the real slippage
        (fill_price - reference_price) is observable per order. Commission is the
        actual IB CommissionReport sum -- no modeled formula. Buy orders may pass
        limit_price when the caller needs a hard affordability cap.
        """
        if qty <= 0:
            return None
        if self.cfg.dry_run:
            price = await self.store.latest_close(symbol)
            if price is None:
                await self.store.record_order(
                    ib_order_id=None, symbol=symbol, action=action, qty=qty, kind=kind,
                    fill_price=None, status="dry_run_no_price",
                    position_id=position_id, note=note,
                    reference_price=reference_price,
                )
                return None
            if (
                limit_price is not None and action.upper() == "BUY"
                and price > limit_price
            ):
                log.info("[dry-run] %s %s %s missed limit %.2f < mark %.2f (%s)",
                         action, qty, symbol, limit_price, price, kind)
                await self.store.record_order(
                    ib_order_id=None, symbol=symbol, action=action, qty=qty, kind=kind,
                    fill_price=None, status="dry_run_limit_miss",
                    position_id=position_id, note=note,
                    reference_price=reference_price,
                )
                return None
            log.info("[dry-run] %s %s %s @~%s (%s)", action, qty, symbol, price, kind)
            await self.store.record_order(
                ib_order_id=None, symbol=symbol, action=action, qty=qty, kind=kind,
                fill_price=price, status="dry_run", position_id=position_id, note=note,
                reference_price=reference_price,
            )
            return price

        ib = await self.ib_conn.ensure_connected()
        contract = await self.ib_conn.qualified_stock(symbol)
        if contract is None:
            await self.store.record_order(
                ib_order_id=None, symbol=symbol, action=action, qty=qty, kind=kind,
                fill_price=None, status="unqualified", position_id=position_id, note=note,
                reference_price=reference_price,
            )
            return None

        order = LimitOrder(action, qty, limit_price) if limit_price else MarketOrder(action, qty)
        trade = ib.placeOrder(contract, order)
        deadline = asyncio.get_event_loop().time() + self.cfg.order_timeout_seconds
        while not trade.isDone() and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1.0)

        status = trade.orderStatus.status
        fill_price = float(trade.orderStatus.avgFillPrice or 0.0) or None
        commission = self._fill_commission(trade)
        await self.store.record_order(
            ib_order_id=trade.order.orderId, symbol=symbol, action=action, qty=qty,
            kind=kind, fill_price=fill_price, status=status,
            position_id=position_id, note=note,
            commission=commission, reference_price=reference_price,
        )
        if status not in {"Filled"}:
            log.warning("order not filled: %s %s %s -> %s", action, qty, symbol, status)
            if not trade.isDone():
                ib.cancelOrder(trade.order)
                cancel_deadline = asyncio.get_event_loop().time() + 5.0
                while not trade.isDone() and asyncio.get_event_loop().time() < cancel_deadline:
                    await asyncio.sleep(0.5)
                # Check status one last time after waiting
                if trade.orderStatus.status == "Filled":
                    log.warning("order %s %s %s filled right before cancellation!", action, qty, symbol)
                    return float(trade.orderStatus.avgFillPrice or 0.0) or None
            return None
        return fill_price

    @staticmethod
    def _fill_commission(trade) -> float | None:
        """Actual commission reported by IB across this order's fills, if any."""
        total = 0.0
        seen = False
        for fill in getattr(trade, "fills", []) or []:
            report = getattr(fill, "commissionReport", None)
            commission = getattr(report, "commission", None) if report else None
            if commission:
                total += float(commission)
                seen = True
        return total if seen else None

    # ── Rotation legs ────────────────────────────────────────────────────

    async def restore_no_margin_from_benchmark(
        self,
        *,
        cash: float,
        benchmark_price: float | None,
        benchmark_shares: float,
        reason: str,
    ) -> bool:
        """Sell benchmark shares when ledger cash is negative."""
        if cash >= 0:
            return True
        qty = benchmark_sell_qty_for_cash_deficit(
            cash,
            benchmark_price,
            benchmark_shares,
            fractional=self.cfg.fractional_benchmark,
            min_notional=self.cfg.min_order_notional,
            buffer_pct=self.cfg.execution_buffer_pct,
        )
        if qty <= 0:
            log.error(
                "cash %.2f is negative but no %s inventory is available to sell",
                cash, self.cfg.benchmark,
            )
            return False

        log.warning(
            "negative ledger cash %.2f before %s -- selling %.4g %s to restore no-margin",
            cash, reason, qty, self.cfg.benchmark,
        )
        fill = await self._execute(
            self.cfg.benchmark,
            "SELL",
            qty,
            kind="margin_rebalance",
            note=f"{reason}: cover negative reconciled cash",
            reference_price=benchmark_price,
        )
        return fill is not None

    async def enter_position(self, signal, *, desired_allocation: float,
                             benchmark_price: float, cash: float,
                             benchmark_shares: float, position_size_pct: float) -> dict | None:
        """Benchmark rotation entry. Returns the stored position dict or None."""
        if cash < 0:
            log.error(
                "refusing entry for %s while reconciled cash is negative (%.2f)",
                signal.symbol, cash,
            )
            return None
        entry_ref_price = await self.store.latest_close(signal.symbol)
        if entry_ref_price is None or entry_ref_price <= 0:
            return None
        entry_limit_price = self._buy_limit(entry_ref_price)
        benchmark_buy_limit = self._buy_limit(benchmark_price)
        if desired_allocation < max(entry_limit_price, self.cfg.min_order_notional):
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
        benchmark_sell_fill = None
        benchmark_sell_proceeds = 0.0
        if benchmark_sell_qty > 0:
            benchmark_sell_fill = await self._execute(
                self.cfg.benchmark, "SELL", benchmark_sell_qty,
                kind="rotation_fund", note=f"fund {signal.symbol}",
                reference_price=benchmark_price,
            )
            if benchmark_sell_fill is None:
                return None
            benchmark_sell_proceeds = (
                benchmark_sell_qty * benchmark_sell_fill
                - ib_cost(benchmark_sell_qty, benchmark_sell_fill, True)
            )
            funding += benchmark_sell_proceeds

        # Size against the capped limit price, not the last mark. If the market
        # runs through the cap, the order simply will not fill.
        asset_qty = affordable_buy_qty(funding, entry_limit_price)
        if asset_qty < 1:
            # Undo: roll the funding back into the benchmark.
            if benchmark_sell_qty > 0:
                undo_cash = max(0.0, benchmark_sell_proceeds)
                undo_qty = self._bench_qty(undo_cash, benchmark_buy_limit)
                await self._execute(
                    self.cfg.benchmark, "BUY", undo_qty,
                    kind="rotation_undo", note=f"undo {signal.symbol}",
                    reference_price=benchmark_price, limit_price=benchmark_buy_limit,
                )
            return None

        fill_price = await self._execute(signal.symbol, "BUY", asset_qty, kind="entry",
                                         note=signal.question[:100],
                                         reference_price=entry_ref_price,
                                         limit_price=entry_limit_price)
        if fill_price is None:
            if benchmark_sell_qty > 0:
                undo_cash = max(0.0, benchmark_sell_proceeds)
                undo_qty = self._bench_qty(undo_cash, benchmark_buy_limit)
                await self._execute(
                    self.cfg.benchmark, "BUY", undo_qty,
                    kind="rotation_undo", note=f"undo {signal.symbol}",
                    reference_price=benchmark_price, limit_price=benchmark_buy_limit,
                )
            return None

        entry_costs = (
            (ib_cost(benchmark_sell_qty, benchmark_sell_fill or benchmark_price, True)
             if benchmark_sell_qty else 0.0)
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
                            benchmark_price: float | None, *,
                            cash: float = 0.0) -> bool:
        """Sell the asset, rebuy the benchmark with the proceeds."""
        qty = int(pos["qty"])
        exit_ref = await self.store.latest_close(pos["symbol"])
        fill_price = await self._execute(pos["symbol"], "SELL", qty, kind="exit",
                                         position_id=pos["position_id"], note=reason,
                                         reference_price=exit_ref)
        if fill_price is None:
            return False

        sell_cost = ib_cost(qty, fill_price, True)
        proceeds = qty * fill_price - sell_cost
        rebuy_budget = proceeds
        if cash < 0:
            rebuy_budget = max(0.0, proceeds + cash)
            if rebuy_budget < proceeds:
                log.warning(
                    "exit %s proceeds first cover cash deficit %.2f; benchmark rebuy budget %.2f",
                    pos["symbol"], -cash, rebuy_budget,
                )

        rebuy_qty = 0.0
        rebuy_cost = 0.0
        if benchmark_price and benchmark_price > 0:
            benchmark_buy_limit = self._buy_limit(benchmark_price)
            rebuy_qty = self._bench_qty(rebuy_budget, benchmark_buy_limit)
            if rebuy_qty > 0:
                rebuy_fill = await self._execute(
                    self.cfg.benchmark, "BUY", rebuy_qty, kind="rotation_rebuy",
                    position_id=pos["position_id"], note=f"rebuy after {pos['symbol']}",
                    reference_price=benchmark_price, limit_price=benchmark_buy_limit,
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

    async def sweep_idle_cash(self, *, cash: float, benchmark_price: float | None,
                              kind: str = "cash_sweep", note: str = "fully-invested sweep",
                              buffer_pct: float | None = None) -> float:
        """Fully-invested rule: idle cash -> benchmark shares."""
        if not benchmark_price or benchmark_price <= 0:
            return 0.0
        # Sub-threshold sweeps churn the $0.35 minimum commission for nothing.
        if cash < self.cfg.min_order_notional:
            return 0.0
        benchmark_buy_limit = self._buy_limit(benchmark_price, buffer_pct)
        qty = self._bench_qty(cash, benchmark_buy_limit)
        if qty <= 0:
            return 0.0
        fill = await self._execute(self.cfg.benchmark, "BUY", qty, kind=kind,
                                   note=note,
                                   reference_price=benchmark_price,
                                   limit_price=benchmark_buy_limit)
        return qty if fill is not None else 0.0
