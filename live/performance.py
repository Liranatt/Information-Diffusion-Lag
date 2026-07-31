"""Pure performance math for the IB-authoritative live account.

The database stores the baseline and external cash-flow audit trail, but the
current account value and benchmark mark always come from the same IB snapshot.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping


def passive_units(
    start_nav: float,
    benchmark_start_price: float,
    cash_flows: Iterable[Mapping[str, float]] = (),
) -> float:
    """Benchmark units represented by the initial NAV plus dated cash flows."""
    if start_nav <= 0 or benchmark_start_price <= 0:
        raise ValueError("performance baseline must contain positive NAV and price")
    units = start_nav / benchmark_start_price
    for flow in cash_flows:
        amount = float(flow["amount"])
        price = float(flow["benchmark_price"])
        if price <= 0:
            raise ValueError("cash-flow benchmark price must be positive")
        units += amount / price
    return units


def passive_equity(
    start_nav: float,
    benchmark_start_price: float,
    current_benchmark_price: float,
    cash_flows: Iterable[Mapping[str, float]] = (),
) -> float:
    """Flow-adjusted passive benchmark value at the current IB mark."""
    if current_benchmark_price <= 0:
        raise ValueError("current benchmark price must be positive")
    return passive_units(start_nav, benchmark_start_price, cash_flows) * current_benchmark_price


def excess_performance(account_nav: float, passive_nav: float) -> tuple[float, float]:
    """Return absolute and percentage excess over the passive benchmark."""
    excess = float(account_nav) - float(passive_nav)
    pct = excess / passive_nav * 100.0 if passive_nav else 0.0
    return excess, pct
