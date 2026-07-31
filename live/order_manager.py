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
import math
import uuid
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
)

log = logging.getLogger("live.orders")


class OrderManager:
    def __init__(self, cfg: LiveConfig, ib_conn: IBConnection, store: LiveStore) -> None:
        self.cfg = cfg
        self.ib_conn = ib_conn
        self.store = store
        self.execution_anomaly = False
        self.last_commission: float | None = None
        self.last_ib_order_id: int | None = None

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
                       limit_price: float | None = None,
                       operation_id: str | None = None) -> float | None:
        """Place a market order and wait for the fill. Returns avg fill price.

        reference_price is the mark we decided at; recorded so the real slippage
        (fill_price - reference_price) is observable per order. Commission is the
        actual IB CommissionReport sum -- no modeled formula. Buy orders may pass
        limit_price when the caller needs a hard affordability cap.
        """
        self.last_commission = None
        self.last_ib_order_id = None
        operation_id = operation_id or uuid.uuid4().hex
        order_ref = f"CEM:{operation_id}:{kind}"[:100]
        if qty <= 0:
            return None
        if self.cfg.dry_run:
            price = await self.store.latest_close(symbol)
            if price is None:
                await self.store.record_order(
                    ib_order_id=None, symbol=symbol, action=action, qty=0.0, kind=kind,
                    fill_price=None, status="dry_run_no_price",
                    position_id=position_id, note=note,
                    reference_price=reference_price,
                    requested_qty=qty,
                    operation_id=operation_id,
                    order_ref=order_ref,
                )
                return None
            if (
                limit_price is not None and action.upper() == "BUY"
                and price > limit_price
            ):
                log.info("[dry-run] %s %s %s missed limit %.2f < mark %.2f (%s)",
                         action, qty, symbol, limit_price, price, kind)
                await self.store.record_order(
                    ib_order_id=None, symbol=symbol, action=action, qty=0.0, kind=kind,
                    fill_price=None, status="dry_run_limit_miss",
                    position_id=position_id, note=note,
                    reference_price=reference_price,
                    requested_qty=qty,
                    operation_id=operation_id,
                    order_ref=order_ref,
                )
                return None
            log.info("[dry-run] %s %s %s @~%s (%s)", action, qty, symbol, price, kind)
            await self.store.record_order(
                ib_order_id=None, symbol=symbol, action=action, qty=qty, kind=kind,
                fill_price=price, status="dry_run", position_id=position_id, note=note,
                reference_price=reference_price,
                requested_qty=qty,
                operation_id=operation_id,
                order_ref=order_ref,
            )
            self.last_commission = 0.0
            return price

        ib = await self.ib_conn.ensure_connected()
        contract = await self.ib_conn.qualified_stock(symbol)
        if contract is None:
            await self.store.record_order(
                ib_order_id=None, symbol=symbol, action=action, qty=0.0, kind=kind,
                fill_price=None, status="unqualified", position_id=position_id, note=note,
                reference_price=reference_price,
                requested_qty=qty,
                operation_id=operation_id,
                order_ref=order_ref,
            )
            return None

        order = LimitOrder(action, qty, limit_price) if limit_price else MarketOrder(action, qty)
        order.orderRef = order_ref
        trade = ib.placeOrder(contract, order)
        deadline = asyncio.get_event_loop().time() + self.cfg.order_timeout_seconds
        while not trade.isDone() and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1.0)

        # Do not persist the pre-cancellation state. A fill can race with our
        # timeout/cancel request; recording "Submitted" or "Cancelled" first
        # and then returning a late fill is exactly how inventory became free in
        # the old ledger.
        if not trade.isDone():
            ib.cancelOrder(trade.order)
            cancel_deadline = asyncio.get_event_loop().time() + 5.0
            while not trade.isDone() and asyncio.get_event_loop().time() < cancel_deadline:
                await asyncio.sleep(0.5)

        status = str(trade.orderStatus.status or "Unknown")
        filled_qty = float(getattr(trade.orderStatus, "filled", 0.0) or 0.0)
        remaining_qty = float(getattr(trade.orderStatus, "remaining", 0.0) or 0.0)
        if status == "Filled" and filled_qty <= 0:
            # Some IB wrappers omit the filled field after a complete fill.
            filled_qty = float(qty)
        fill_price = float(trade.orderStatus.avgFillPrice or 0.0) or None
        if filled_qty > 0 and (getattr(trade, "fills", []) or []):
            await self._wait_for_commission_reports(trade)
        commission = self._fill_commission(trade)
        self.last_commission = commission
        self.last_ib_order_id = int(trade.order.orderId)

        complete = (
            fill_price is not None
            and filled_qty >= float(qty) - 1e-6
            and remaining_qty <= 1e-6
        )
        if filled_qty > 0 and fill_price is None:
            recorded_status = "UnpricedFill"
        elif filled_qty > 0 and not complete:
            recorded_status = "PartiallyFilled"
        elif complete:
            recorded_status = "Filled"
        else:
            recorded_status = status

        await self.store.record_order(
            ib_order_id=trade.order.orderId, symbol=symbol, action=action,
            qty=filled_qty if fill_price is not None else 0.0,
            requested_qty=qty, kind=kind, fill_price=fill_price,
            status=recorded_status, position_id=position_id, note=note,
            commission=commission, reference_price=reference_price,
            operation_id=operation_id, order_ref=order_ref,
            perm_id=int(getattr(trade.order, "permId", 0) or 0) or None,
        )
        execution_rows = self._execution_rows(
            trade, symbol=symbol, action=action, kind=kind,
            position_id=position_id, operation_id=operation_id,
        )
        if execution_rows:
            await self.store.record_execution_fills(execution_rows)
        if not complete:
            self.execution_anomaly = True
            log.error(
                "order incomplete: %s %s %s -> %s (filled=%s remaining=%s); "
                "this tick will stop; the next tick will re-read IB as source of truth",
                action, qty, symbol, recorded_status, filled_qty, remaining_qty,
            )
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
            if (
                getattr(report, "execId", "")
                and OrderManager._valid_ib_number(commission)
            ):
                total += float(commission)
                seen = True
        return total if seen else None

    @staticmethod
    def _valid_ib_number(value) -> bool:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return math.isfinite(number) and abs(number) < 1e100

    async def _wait_for_commission_reports(self, trade) -> None:
        """Allow IB's CommissionReport events to catch up with the fill."""
        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            fills = list(getattr(trade, "fills", []) or [])
            if fills and all(
                getattr(getattr(fill, "commissionReport", None), "execId", "")
                and self._valid_ib_number(
                    getattr(
                        getattr(fill, "commissionReport", None),
                        "commission",
                        None,
                    )
                )
                for fill in fills
            ):
                return
            await asyncio.sleep(0.25)

    @classmethod
    def _execution_rows(
        cls,
        trade,
        *,
        symbol: str,
        action: str,
        kind: str | None = None,
        position_id: int | None = None,
        operation_id: str | None = None,
    ) -> list[dict]:
        rows = []
        for fill in getattr(trade, "fills", []) or []:
            execution = getattr(fill, "execution", None)
            if execution is None or not getattr(execution, "execId", None):
                continue
            report = getattr(fill, "commissionReport", None)
            commission = getattr(report, "commission", None) if report else None
            realized_pnl = getattr(report, "realizedPNL", None) if report else None
            report_ready = bool(getattr(report, "execId", ""))
            rows.append(
                {
                    "exec_id": str(execution.execId),
                    "ib_order_id": int(trade.order.orderId),
                    "symbol": symbol,
                    "action": action,
                    "exec_ts": getattr(execution, "time", None),
                    "shares": float(execution.shares),
                    "price": float(execution.price),
                    "exchange": getattr(execution, "exchange", None),
                    "commission": (
                        float(commission)
                        if report_ready and cls._valid_ib_number(commission)
                        else None
                    ),
                    "realized_pnl": (
                        float(realized_pnl)
                        if report_ready and cls._valid_ib_number(realized_pnl)
                        else None
                    ),
                    "perm_id": int(getattr(execution, "permId", 0) or 0) or None,
                    "account": getattr(execution, "acctNumber", None),
                    "order_ref": getattr(execution, "orderRef", None)
                    or getattr(trade.order, "orderRef", None),
                    "kind": kind,
                    "position_id": position_id,
                    "operation_id": operation_id,
                }
            )
        return rows

    # ── Rotation legs ────────────────────────────────────────────────────

    async def restore_no_margin_from_benchmark(
        self,
        *,
        cash: float,
        benchmark_price: float | None,
        benchmark_shares: float,
        reason: str,
    ) -> bool:
        """Deprecated warning-only hook; never trades merely to reconcile cash."""
        if cash >= 0:
            return True
        log.warning(
            "IB reports negative cash %.2f before %s; warning only, no corrective order sent",
            cash, reason,
        )
        return False

    async def enter_position(self, signal, *, desired_allocation: float,
                             benchmark_price: float, cash: float,
                             benchmark_shares: float, position_size_pct: float) -> dict | None:
        """Benchmark rotation entry. Returns the stored position dict or None."""
        operation_id = uuid.uuid4().hex
        entry_ref_price = await self.store.latest_close(signal.symbol)
        if entry_ref_price is None or entry_ref_price <= 0:
            return None
        entry_limit_price = self._buy_limit(entry_ref_price)
        benchmark_buy_limit = self._buy_limit(benchmark_price)
        if desired_allocation < max(entry_limit_price, self.cfg.min_order_notional):
            return None

        # Fund from idle cash first, then benchmark inventory (backtest parity).
        cash_contribution = min(max(cash, 0.0), desired_allocation)
        # Negative IB cash is a real account liability.  A normal entry may
        # still proceed, but it must sell enough benchmark to fund both the
        # desired asset and the existing deficit; no hidden extra borrowing.
        shortfall = desired_allocation - cash_contribution + max(-cash, 0.0)
        if shortfall > 0:
            desired_sell = (
                shortfall / benchmark_price if self.cfg.fractional_benchmark
                else int(shortfall / benchmark_price)
            )
            benchmark_sell_qty = round(min(desired_sell, benchmark_shares), 4)
        else:
            benchmark_sell_qty = 0.0
        estimated_funding_after_deficit = (
            cash_contribution
            + benchmark_sell_qty * benchmark_price
            - max(-cash, 0.0)
        )
        if estimated_funding_after_deficit < entry_limit_price:
            return None

        funding = cash_contribution
        benchmark_sell_fill = None
        benchmark_sell_proceeds = 0.0
        benchmark_sell_commission = 0.0
        if benchmark_sell_qty > 0:
            benchmark_sell_fill = await self._execute(
                self.cfg.benchmark, "SELL", benchmark_sell_qty,
                kind="rotation_fund", note=f"fund {signal.symbol}",
                reference_price=benchmark_price,
                operation_id=operation_id,
            )
            if benchmark_sell_fill is None:
                return None
            benchmark_sell_commission = float(self.last_commission or 0.0)
            benchmark_sell_proceeds = (
                benchmark_sell_qty * benchmark_sell_fill - benchmark_sell_commission
            )
            funding += benchmark_sell_proceeds - max(-cash, 0.0)

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
                    operation_id=operation_id,
                )
            return None

        fill_price = await self._execute(signal.symbol, "BUY", asset_qty, kind="entry",
                                         note=signal.question[:100],
                                         reference_price=entry_ref_price,
                                         limit_price=entry_limit_price,
                                         operation_id=operation_id)
        if fill_price is None:
            # A cancelled/partial order can still turn into a late paper fill.
            # Do not send a compensating order while execution is uncertain;
            # keep the sale proceeds as cash and let the broker-drift guard halt
            # the next stage until holdings are reconciled.
            return None

        asset_buy_commission = float(self.last_commission or 0.0)
        entry_costs = benchmark_sell_commission + asset_buy_commission
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
            "operation_id": operation_id,
        }
        position["position_id"] = await self.store.insert_position(position)
        log.info("ENTER %s x%d @ %.2f (%s, prob=%.3f)",
                 signal.symbol, asset_qty, fill_price, signal.question[:60], signal.prob)
        return position

    async def exit_position(self, pos: dict, reason: str,
                            benchmark_price: float | None, *,
                            cash: float = 0.0) -> bool:
        """Sell the asset, rebuy the benchmark with the proceeds."""
        # IB, not the cached strategy row, owns the executable quantity.
        broker_positions = await self.ib_conn.portfolio_positions()
        qty = float(broker_positions.get(pos["symbol"], 0.0))
        if qty <= 0:
            log.warning(
                "IB reports no %s position; cached DB exit row is not executable",
                pos["symbol"],
            )
            return False
        exit_ref = await self.store.latest_close(pos["symbol"])
        operation_id = uuid.uuid4().hex
        fill_price = await self._execute(pos["symbol"], "SELL", qty, kind="exit",
                                         position_id=pos["position_id"], note=reason,
                                         reference_price=exit_ref,
                                         operation_id=operation_id)
        if fill_price is None:
            return False

        sell_cost = float(self.last_commission or 0.0)
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
                    operation_id=operation_id,
                )
                if rebuy_fill is not None:
                    rebuy_cost = float(self.last_commission or 0.0)

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
        log.info("EXIT %s x%g @ %.2f (%s) pnl=%.2f", pos["symbol"], qty, fill_price,
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
