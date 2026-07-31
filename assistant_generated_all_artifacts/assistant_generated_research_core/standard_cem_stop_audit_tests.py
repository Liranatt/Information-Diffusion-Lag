"""Minimal independent tests for the stop semantics in standard_cem_strategy.ipynb.

This file does not retrain CEM and does not alter entry, ATR, peak, probability,
resolution, allocation, or cost logic. It isolates only the stop fill rule.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float


def original_stop_fill(active_stop: float, bar: Bar) -> float | None:
    """Exact consequence of: if low <= stop: fill=max(low, stop)."""
    if bar.low <= active_stop:
        return max(bar.low, active_stop)
    return None


def open_aware_stop_fill(active_stop: float, bar: Bar) -> float | None:
    """Minimal long stop-market rule using daily Open and Low."""
    if bar.open <= active_stop:
        return bar.open
    if bar.low <= active_stop:
        return active_stop
    return None


def original_day_with_prior_stop(
    prior_stop: float,
    bar: Bar,
    stop_created_from_current_high: float | None = None,
) -> tuple[float | None, float | None]:
    """Mirror the notebook's ordering: test prior stop, then update peak for tomorrow.

    Returns (today_fill, tomorrow_stop).  The optional tomorrow stop is supplied by
    the caller only to show that today's High cannot trigger it on today's Low.
    """
    today_fill = original_stop_fill(prior_stop, bar)
    tomorrow_stop = stop_created_from_current_high if today_fill is None else None
    return today_fill, tomorrow_stop


def run() -> None:
    cases = {
        "normal_intraday": (100.0, Bar(104, 108, 98, 102)),
        "overnight_gap": (100.0, Bar(94, 98, 90, 96)),
        "no_crossing": (100.0, Bar(104, 110, 102, 108)),
        "gap_then_recovery": (100.0, Bar(90, 105, 85, 102)),
    }
    print("case, original_fill, open_aware_fill")
    for name, (stop, bar) in cases.items():
        print(name, original_stop_fill(stop, bar), open_aware_stop_fill(stop, bar), sep=",")

    # Previous peak implies a stop at 90. Today's High may imply a higher stop for
    # tomorrow, but the notebook checks today's Low against 90 before updating peak.
    ordering_bar = Bar(100, 115, 95, 108)
    today_fill, tomorrow_stop = original_day_with_prior_stop(
        90.0, ordering_bar, stop_created_from_current_high=105.0
    )
    print(f"same_bar_ordering,today_fill={today_fill},tomorrow_stop={tomorrow_stop}")

    assert original_stop_fill(100, cases["normal_intraday"][1]) == 100
    assert open_aware_stop_fill(100, cases["normal_intraday"][1]) == 100
    assert original_stop_fill(100, cases["overnight_gap"][1]) == 100
    assert open_aware_stop_fill(100, cases["overnight_gap"][1]) == 94
    assert open_aware_stop_fill(100, cases["no_crossing"][1]) is None
    assert original_stop_fill(100, cases["gap_then_recovery"][1]) == 100
    assert open_aware_stop_fill(100, cases["gap_then_recovery"][1]) == 90
    assert today_fill is None
    assert tomorrow_stop == 105
    print("all assertions passed")


if __name__ == "__main__":
    run()
