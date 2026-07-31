"""Async Interactive Brokers connection via ib_async (successor of ib_insync).

One shared connection for the whole control pipeline; auto-reconnects between
ticks if TWS/Gateway restarted. Requires a running TWS or IB Gateway in paper
mode (default port 4002 for Gateway paper, 7497 for TWS paper).
"""
from __future__ import annotations

import asyncio
import logging
import math

try:
    from ib_async import IB, Stock, util  # maintained fork
except ImportError:  # pragma: no cover
    from ib_insync import IB, Stock, util  # type: ignore[no-redef]

from .config import LiveConfig

log = logging.getLogger("live.ib")


class IBConnection:
    def __init__(self, cfg: LiveConfig) -> None:
        self.cfg = cfg
        self.ib = IB()
        self._contracts: dict[str, Stock] = {}
        self._paper_account_checked = False

    async def ensure_connected(self) -> IB:
        if self.ib.isConnected():
            await self._assert_paper_account()
            return self.ib
        log.info("connecting to IB %s:%s (clientId=%s)",
                 self.cfg.ib_host, self.cfg.ib_port, self.cfg.ib_client_id)
        await self.ib.connectAsync(
            self.cfg.ib_host, self.cfg.ib_port,
            clientId=self.cfg.ib_client_id, timeout=20,
        )
        await self._assert_paper_account()
        # Ask for delayed data so paper accounts without a live market-data
        # subscription still get prices/bars instead of silent empties.
        try:
            self.ib.reqMarketDataType(self.cfg.ib_market_data_type)
        except Exception as error:  # noqa: BLE001 - non-fatal preference
            log.warning("reqMarketDataType(%s) failed: %s",
                        self.cfg.ib_market_data_type, error)
        return self.ib

    async def _assert_paper_account(self) -> None:
        if self._paper_account_checked or not self.cfg.require_paper_account:
            return
        accounts = [a for a in self.ib.managedAccounts() if a]
        targets = [self.cfg.account] if self.cfg.account else accounts
        if not targets:
            raise RuntimeError(
                "LIVE_REQUIRE_PAPER_ACCOUNT is enabled, but IB returned no managed accounts"
            )
        non_paper = [a for a in targets if not a.upper().startswith("DU")]
        if non_paper:
            masked = ", ".join(_mask_account(a) for a in non_paper)
            raise RuntimeError(
                "LIVE_REQUIRE_PAPER_ACCOUNT blocked this IB session because the "
                f"selected account does not look like an IB paper account: {masked}. "
                "Set IB_ACCOUNT to the paper account or explicitly set "
                "LIVE_REQUIRE_PAPER_ACCOUNT=false if this is intentional."
            )
        log.info("paper account guard passed for %s",
                 ", ".join(_mask_account(a) for a in targets))
        self._paper_account_checked = True

    async def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    async def qualified_stock(self, symbol: str) -> Stock | None:
        """SMART-routed USD stock contract, qualified once and cached."""
        cached = self._contracts.get(symbol)
        if cached is not None:
            return cached
        ib = await self.ensure_connected()
        contract = Stock(symbol, "SMART", "USD")
        try:
            qualified = await asyncio.wait_for(
                ib.qualifyContractsAsync(contract),
                timeout=self.cfg.ib_request_timeout_seconds,
            )
        except Exception as error:  # noqa: BLE001
            log.warning("qualify failed for %s: %s", symbol, error)
            return None
        if not qualified:
            log.warning("IB cannot qualify %s -- skipping symbol", symbol)
            return None
        self._contracts[symbol] = qualified[0]
        return qualified[0]

    async def cancel_all_open_orders(self) -> None:
        """Fetch all open orders from IB and cancel them if any are left over."""
        ib = await self.ensure_connected()
        open_orders = await ib.reqAllOpenOrdersAsync()
        if not open_orders:
            return
        log.warning("Found %d leftover open orders in IB! Cancelling them now...", len(open_orders))
        for trade in open_orders:
            ib.cancelOrder(trade.order)
        # Wait a moment for cancellations to process
        await asyncio.sleep(2.0)

    async def account_summary(self) -> dict[str, float] | None:
        """USD TotalCashValue and NetLiquidation, or None if unavailable.

        None signals "balance unavailable" -- e.g. the account farm
        is mid-reconnect and the summary came back empty. Callers must NOT treat
        that as a real zero balance and must not size/trade on it.
        """
        ib = await self.ensure_connected()
        rows = await asyncio.wait_for(
            ib.accountSummaryAsync(self.cfg.account or ""),
            timeout=self.cfg.ib_request_timeout_seconds,
        )
        res = {}
        wanted = {
            "TotalCashValue",
            "NetLiquidation",
            "BuyingPower",
            "AvailableFunds",
            "GrossPositionValue",
            "SettledCash",
            "AccruedCash",
            "InitMarginReq",
            "MaintMarginReq",
            "ExcessLiquidity",
            "FullInitMarginReq",
            "FullMaintMarginReq",
            "LookAheadExcessLiquidity",
            "Leverage",
        }
        for row in rows:
            if row.currency in {"USD", "BASE", ""} and row.tag in wanted:
                res[row.tag] = float(row.value)
        return res if res else None

    async def execution_snapshot(self) -> list[dict]:
        """Executions currently retrievable from IB, including actual fees.

        IB controls the execution-retention window.  Repeated calls are safe
        because ``exec_id`` is immutable and the database upserts only the
        late-arriving CommissionReport fields.
        """
        ib = await self.ensure_connected()
        fills = await asyncio.wait_for(
            ib.reqExecutionsAsync(),
            timeout=self.cfg.ib_request_timeout_seconds,
        )
        rows: list[dict] = []
        for fill in fills or []:
            execution = getattr(fill, "execution", None)
            contract = getattr(fill, "contract", None)
            if execution is None or contract is None or not execution.execId:
                continue
            if self.cfg.account and execution.acctNumber != self.cfg.account:
                continue
            report = getattr(fill, "commissionReport", None)
            commission = _ib_number(getattr(report, "commission", None)) if report else None
            realized = _ib_number(getattr(report, "realizedPNL", None)) if report else None
            side = str(getattr(execution, "side", "")).upper()
            action = "BUY" if side in {"BOT", "BUY"} else "SELL"
            rows.append(
                {
                    "exec_id": execution.execId,
                    "ib_order_id": int(execution.orderId or 0) or None,
                    "symbol": contract.symbol,
                    "action": action,
                    "exec_ts": getattr(execution, "time", None),
                    "shares": float(execution.shares or 0.0),
                    "price": float(execution.price or 0.0),
                    "exchange": getattr(execution, "exchange", None),
                    "commission": commission,
                    "realized_pnl": realized,
                    "perm_id": int(execution.permId or 0) or None,
                    "account": getattr(execution, "acctNumber", None),
                    "order_ref": getattr(execution, "orderRef", None),
                }
            )
        return rows

    async def portfolio_snapshot(self) -> dict[str, dict[str, float]]:
        """Current IB portfolio facts keyed by symbol.

        This is the authoritative live view used by the strategy and then
        mirrored to Postgres.  Values come from IB's PortfolioItem objects;
        the DB never reconstructs quantities, marks, cost basis, or P&L.
        """
        ib = await self.ensure_connected()
        account = self.cfg.account or ""
        items = ib.portfolio(account)
        out: dict[str, dict[str, float]] = {}
        for item in items:
            symbol = item.contract.symbol
            row = out.setdefault(
                symbol,
                {
                    "qty": 0.0,
                    "market_price": 0.0,
                    "market_value": 0.0,
                    "avg_cost": 0.0,
                    "unrealized_pnl": 0.0,
                    "realized_pnl": 0.0,
                },
            )
            row["qty"] += float(item.position or 0.0)
            row["market_value"] += float(item.marketValue or 0.0)
            row["unrealized_pnl"] += float(item.unrealizedPNL or 0.0)
            row["realized_pnl"] += float(item.realizedPNL or 0.0)
            # One SMART USD contract per symbol is expected in this account.
            # Keep IB's own aggregate price/cost rather than re-deriving them.
            row["market_price"] = float(item.marketPrice or 0.0)
            row["avg_cost"] = float(item.averageCost or 0.0)
        return out

    async def portfolio_positions(self) -> dict[str, float]:
        """Current IB paper account positions as {symbol: signed qty}.

        Quantities are floats because benchmark holdings may be fractional.
        """
        ib = await self.ensure_connected()
        positions = await asyncio.wait_for(
            ib.reqPositionsAsync(),
            timeout=self.cfg.ib_request_timeout_seconds,
        )
        out: dict[str, float] = {}
        for pos in positions:
            if self.cfg.account and pos.account != self.cfg.account:
                continue
            out[pos.contract.symbol] = out.get(pos.contract.symbol, 0.0) + float(pos.position)
        return out

    async def last_price(self, symbol: str) -> float | None:
        """Snapshot last/close price for a symbol (delayed data is fine for paper)."""
        ib = await self.ensure_connected()
        contract = await asyncio.wait_for(
            self.qualified_stock(symbol),
            timeout=self.cfg.ib_request_timeout_seconds,
        )
        if contract is None:
            return None
        ticker = ib.reqMktData(contract, "", snapshot=True, regulatorySnapshot=False)
        for _ in range(20):
            await asyncio.sleep(0.25)
            price = ticker.last or ticker.close
            if price and price > 0:
                ib.cancelMktData(contract)
                return float(price)
        ib.cancelMktData(contract)
        return None


def _mask_account(account: str) -> str:
    if len(account) <= 4:
        return "*" * len(account)
    return f"{account[:2]}...{account[-4:]}"


def _ib_number(value) -> float | None:
    """Normalize IB's unset/NaN numeric sentinels to an honest missing value."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and abs(number) < 1e100 else None
