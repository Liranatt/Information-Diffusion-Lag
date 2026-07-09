"""Configuration for the live paper-trading control pipeline.

Everything is overridable via environment variables so the same code runs on
the private server and locally. The DB settings come from the repo-root .env
(same Postgres the backtest uses -- see database/db_connection.py).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class LiveConfig:
    # ── Interactive Brokers ──────────────────────────────────────────────
    # 7497 = TWS paper, 4002 = IB Gateway paper.
    ib_host: str = os.environ.get("IB_HOST", "127.0.0.1")
    ib_port: int = _env_int("IB_PORT", 4002)
    ib_client_id: int = _env_int("IB_CLIENT_ID", 17)
    account: str = os.environ.get("IB_ACCOUNT", "")  # empty = default account
    # Market-data type for the session: 1=live, 2=frozen, 3=delayed,
    # 4=delayed-frozen. Paper accounts without a live data subscription need
    # delayed (3) or requests silently return nothing.
    ib_market_data_type: int = _env_int("IB_MARKET_DATA_TYPE", 3)

    # ── Strategy ─────────────────────────────────────────────────────────
    benchmark: str = os.environ.get("LIVE_BENCHMARK", "SPY")
    # Latest walk-forward fold policy for this experiment/benchmark is loaded
    # from the fold audit CSV; refit runs replace the CSV and the live engine
    # picks the new policy up on the next tick ("keep walking forward").
    experiment: str = os.environ.get("LIVE_EXPERIMENT", "T1+T2+T3+T4")
    fold_audit_csv: Path = REPO_ROOT / "data" / "experiment_walkforward_folds_clean.csv"
    results_csv: Path = REPO_ROOT / "data" / "experiment_results_clean.csv"
    use_kelly: bool = _env_bool("LIVE_USE_KELLY", True)
    # Benchmark legs use fractional shares (requires fractional-share trading
    # permission on the IB account; SPY/QQQ are fraction-eligible).
    fractional_benchmark: bool = _env_bool("LIVE_FRACTIONAL_BENCHMARK", False)
    # Offset for IB paper trading glitches (e.g. ghost fills that inflate cash).
    # This amount is subtracted from total equity and cash when reporting to DB.
    paper_glitch_offset: float = _env_float("LIVE_PAPER_GLITCH_OFFSET", 0.0)

    # ── Control loop cadence ─────────────────────────────────────────────
    tick_seconds: int = _env_int("LIVE_TICK_SECONDS", 3600)       # hourly
    discovery_every_ticks: int = _env_int("LIVE_DISCOVERY_TICKS", 24)  # daily
    prune_every_ticks: int = _env_int("LIVE_PRUNE_TICKS", 24)
    # In the final hour before the equity close, keep trying to sweep free cash
    # into the benchmark so the account does not carry idle cash overnight.
    close_sweep_start_minutes: int = _env_int("LIVE_CLOSE_SWEEP_START_MINUTES", 60)
    close_sweep_retry_seconds: int = _env_int("LIVE_CLOSE_SWEEP_RETRY_SECONDS", 60)

    # ── Data & space management ──────────────────────────────────────────
    # Hourly bars are stored only for symbols we track (open positions +
    # mapped assets of open markets) plus the benchmark. Retention prunes
    # hourly bars of symbols we no longer track and probability points of
    # markets resolved longer ago than this window.
    bar_retention_days: int = _env_int("LIVE_BAR_RETENTION_DAYS", 90)
    prob_retention_days: int = _env_int("LIVE_PROB_RETENTION_DAYS", 30)
    daily_lookback_days: int = _env_int("LIVE_DAILY_LOOKBACK_DAYS", 40)  # ATR needs ~16 bars

    # ── Execution safety ─────────────────────────────────────────────────
    dry_run: bool = _env_bool("LIVE_DRY_RUN", False)
    require_paper_account: bool = _env_bool("LIVE_REQUIRE_PAPER_ACCOUNT", True)
    ib_request_timeout_seconds: int = _env_int("LIVE_IB_REQUEST_TIMEOUT", 45)
    order_timeout_seconds: int = _env_int("LIVE_ORDER_TIMEOUT", 120)
    min_order_notional: float = _env_float("LIVE_MIN_ORDER_NOTIONAL", 200.0)
    # Buy orders are sent as capped limit orders at reference * (1 + buffer).
    # Sizing also uses this capped price, so a fill cannot consume more capital
    # than the cash + benchmark inventory explicitly allocated to that trade.
    execution_buffer_pct: float = _env_float("LIVE_EXECUTION_BUFFER_PCT", 0.01)
    close_sweep_buffer_pct: float = _env_float("LIVE_CLOSE_SWEEP_BUFFER_PCT", 0.03)

    # Markets we track must resolve within this window (mirrors scanner).
    min_resolution_days: int = 5
    max_resolution_days: int = 60

    extra: dict = field(default_factory=dict)


CONFIG = LiveConfig()
