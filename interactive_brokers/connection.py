"""Async Interactive Brokers connection via ib_async (successor of ib_insync).

One shared connection for the whole control pipeline; auto-reconnects between
ticks if TWS/Gateway restarted. Requires a running TWS or IB Gateway in paper
mode (default port 4002 for Gateway paper, 7497 for TWS paper).
"""
from __future__ import annotations

import asyncio
import logging

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

    async def account_cash(self) -> float:
        ib = await self.ensure_connected()
        rows = await asyncio.wait_for(
            ib.accountSummaryAsync(self.cfg.account or ""),
            timeout=self.cfg.ib_request_timeout_seconds,
        )
        for row in rows:
            if row.tag == "TotalCashValue" and row.currency == "USD":
                return float(row.value)
        return 0.0

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
