"""Expectation-first robustness protocol for the raw T-1 H1 observation.

The primary estimand is an empirical conditional mean, not alpha and not the
performance of a trading strategy:

    E[observed net equity return over T_theta through T_end-1
      | frozen Polymarket-identified stock-event observation] > 0.

Benchmark-adjusted returns are emitted only as secondary diagnostics.  This
module adds immutable artifact IDs, true economic-event aggregation,
dependence-aware inference, conservative timing analysis, end-date provenance
checks, leave-one-out diagnostics, and sensitivity tables around the existing
raw-expectation output.
"""
from __future__ import annotations

import hashlib
import json
import math
import pickle
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy import stats

from core.features import SECTOR_ETFS


PROTOCOL_VERSION = "h1-empirical-net-expectation-v2"
PRIMARY_HYPOTHESIS = (
    "E[observed net equity return from T_theta to the last eligible "
    "pre-event close | frozen Polymarket-identified stock-event observation] > 0"
)
PRIMARY_ESTIMAND = "raw_net_return"
PRIMARY_OBSERVATION_UNIT = "candidate_observation"
PRIMARY_INFERENCE_UNIT = "economic_event_id"
DEFAULT_BOOTSTRAPS = 10_000
DEFAULT_SEED = 42

# These boundaries make the historical roles explicit.  The last block is
# called retrospective_holdout because this history has already influenced
# research decisions; it is not represented as a genuinely untouched test.
STAGE_BOUNDARIES = {
    "discovery_end_exclusive": "2025-07-29",
    "model_selection_end_exclusive": "2026-01-01",
    "validation_end_exclusive": "2026-04-01",
}


REQUIRED_TRADE_COLUMNS = {
    "market_id",
    "event_id",
    "symbol",
    "entry_date",
    "exit_date_t_minus_1",
    "gross_return",
    "net_return",
    "net_pnl",
    "notional",
    "estimated_transaction_cost",
}


@dataclass(frozen=True)
class ProtocolPaths:
    repo_root: Path
    output_dir: Path
    candidate_file: Path | None = None

    @property
    def candidates(self) -> Path:
        return self.candidate_file or self.repo_root / "data" / "candidates.parquet"

    @property
    def prices(self) -> Path:
        return self.repo_root / "data" / "prices.pkl"

    @property
    def probabilities(self) -> Path:
        return self.repo_root / "data" / "probs.pkl"

    @property
    def folds(self) -> Path:
        return self.repo_root / "data" / "experiment_walkforward_folds_clean.csv"

    @property
    def polarity(self) -> Path:
        return self.repo_root / "data" / "polarity_labels.json"

    @property
    def trades(self) -> Path:
        return self.output_dir / "raw_expectation_trades_candidate_level.csv"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_frame_hash(frame: pd.DataFrame, columns: Iterable[str]) -> str:
    selected = [column for column in columns if column in frame.columns]
    if not selected:
        return _sha256_bytes(b"")
    normalized = frame[selected].copy()
    for column in selected:
        normalized[column] = normalized[column].map(
            lambda value: "" if pd.isna(value) else str(value)
        )
    normalized = normalized.sort_values(selected, kind="mergesort").reset_index(drop=True)
    return _sha256_bytes(normalized.to_csv(index=False, lineterminator="\n").encode("utf-8"))


def _combined_id(name: str, values: dict[str, Any]) -> str:
    payload = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str)
    return f"{name}:{_sha256_bytes(payload.encode('utf-8'))[:16]}"


def _git_state(repo_root: Path) -> dict[str, Any]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=repo_root, text=True
            ).strip()
        )
        return {"commit": commit, "dirty_worktree": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty_worktree": None}


def _stage_for_dates(values: pd.Series) -> pd.Series:
    dates = pd.to_datetime(values, errors="coerce")
    discovery_end = pd.Timestamp(STAGE_BOUNDARIES["discovery_end_exclusive"])
    selection_end = pd.Timestamp(STAGE_BOUNDARIES["model_selection_end_exclusive"])
    validation_end = pd.Timestamp(STAGE_BOUNDARIES["validation_end_exclusive"])
    result = np.select(
        [dates < discovery_end, dates < selection_end, dates < validation_end],
        ["discovery", "model_selection", "validation"],
        default="retrospective_holdout",
    )
    return pd.Series(result, index=values.index, dtype="object")


def _economic_event_ids(frame: pd.DataFrame) -> pd.Series:
    event = frame["event_id"].astype("string").fillna("").str.strip()
    market = frame["market_id"].astype("string").fillna("").str.strip()
    return event.where(event.ne(""), "market-fallback:" + market)


def build_true_event_frame(trades: pd.DataFrame, return_column: str = "net_return") -> pd.DataFrame:
    """Collapse candidate rows to unique opportunities, then economic events.

    One event receives one equal weight regardless of how many Polymarket
    markets or mapped symbols it generated.  Within an event, exact repeated
    symbol/entry/exit opportunities are first collapsed so duplicated contract
    rows do not receive extra weight.
    """
    required = {
        "event_id", "market_id", "symbol", "entry_date", "exit_date_t_minus_1",
        return_column,
    }
    missing = required.difference(trades.columns)
    if missing:
        raise ValueError(f"Cannot build true events; missing columns: {sorted(missing)}")

    work = trades.copy()
    work["economic_event_id"] = _economic_event_ids(work)
    work["research_stage"] = _stage_for_dates(work["entry_date"])
    opportunity_keys = [
        "economic_event_id", "symbol", "entry_date", "exit_date_t_minus_1"
    ]
    opportunity = (
        work.groupby(opportunity_keys, dropna=False, sort=False)
        .agg(
            opportunity_return=(return_column, "mean"),
            opportunity_net_pnl=("net_pnl", "mean") if "net_pnl" in work else (return_column, "size"),
            n_candidate_rows=(return_column, "size"),
            n_markets=("market_id", "nunique"),
            event_family=("event_family", "first") if "event_family" in work else (return_column, "size"),
            question=("question", "first") if "question" in work else (return_column, "size"),
            research_stage=("research_stage", "first"),
        )
        .reset_index()
    )

    event = (
        opportunity.groupby("economic_event_id", dropna=False, sort=False)
        .agg(
            event_id=("economic_event_id", "first"),
            question=("question", "first"),
            event_family=("event_family", "first"),
            research_stage=("research_stage", "first"),
            first_entry_date=("entry_date", "min"),
            last_exit_date=("exit_date_t_minus_1", "max"),
            n_opportunities=("opportunity_return", "size"),
            n_symbols=("symbol", "nunique"),
            n_candidate_rows=("n_candidate_rows", "sum"),
            n_markets=("n_markets", "sum"),
            mean_raw_net_return=("opportunity_return", "mean"),
            median_opportunity_return=("opportunity_return", "median"),
            opportunity_win_rate=("opportunity_return", lambda values: float(np.mean(np.asarray(values) > 0))),
            hypothetical_net_pnl=("opportunity_net_pnl", "sum"),
        )
        .reset_index(drop=True)
    )
    event["entry_month"] = pd.to_datetime(event["first_entry_date"]).dt.strftime("%Y-%m")
    event["entry_week"] = pd.to_datetime(event["first_entry_date"]).dt.strftime("%G-W%V")
    return event.sort_values(["first_entry_date", "event_id"], kind="mergesort").reset_index(drop=True)


def _one_sided_t(values: np.ndarray) -> tuple[float, float]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 2:
        return math.nan, math.nan
    result = stats.ttest_1samp(values, 0.0, alternative="greater")
    return float(result.statistic), float(result.pvalue)


def _distribution_stats(values: np.ndarray) -> dict[str, Any]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    t_stat, p_t = _one_sided_t(values)
    wins = int(np.sum(values > 0))
    p_binomial = (
        float(stats.binomtest(wins, len(values), 0.5, alternative="greater").pvalue)
        if len(values)
        else math.nan
    )
    return {
        "n": int(len(values)),
        "mean_raw_net_return": float(np.mean(values)) if len(values) else math.nan,
        "median_raw_net_return": float(np.median(values)) if len(values) else math.nan,
        "win_rate": float(np.mean(values > 0)) if len(values) else math.nan,
        "standard_deviation": float(np.std(values, ddof=1)) if len(values) > 1 else math.nan,
        "t_statistic": t_stat,
        "p_t_one_sided": p_t,
        "p_binomial_win_rate_gt_50": p_binomial,
    }


def _cluster_bootstrap(
    values: np.ndarray,
    labels: np.ndarray,
    *,
    n_boot: int,
    seed: int,
) -> dict[str, Any]:
    """Centered cluster bootstrap for H0 mean <= 0 plus percentile CI."""
    values = np.asarray(values, dtype=float)
    labels = np.asarray(labels).astype(str)
    ok = np.isfinite(values)
    values = values[ok]
    labels = labels[ok]
    unique, inverse = np.unique(labels, return_inverse=True)
    groups = len(unique)
    if len(values) < 2 or groups < 2:
        return {
            "n_clusters": groups,
            "cluster_bootstrap_p": math.nan,
            "cluster_ci_lo": math.nan,
            "cluster_ci_hi": math.nan,
        }

    sums = np.bincount(inverse, weights=values, minlength=groups)
    sizes = np.bincount(inverse, minlength=groups).astype(float)
    observed = float(np.mean(values))
    centered_sums = sums - observed * sizes
    rng = np.random.default_rng(seed)
    null_means: list[np.ndarray] = []
    raw_means: list[np.ndarray] = []
    chunk_size = 500
    for start in range(0, n_boot, chunk_size):
        count = min(chunk_size, n_boot - start)
        draw = rng.integers(0, groups, size=(count, groups))
        denominators = sizes[draw].sum(axis=1)
        null_means.append(centered_sums[draw].sum(axis=1) / denominators)
        raw_means.append(sums[draw].sum(axis=1) / denominators)
    null = np.concatenate(null_means)
    raw = np.concatenate(raw_means)
    return {
        "n_clusters": groups,
        "cluster_bootstrap_p": float((np.sum(null >= observed) + 1) / (len(null) + 1)),
        "cluster_ci_lo": float(np.quantile(raw, 0.025)),
        "cluster_ci_hi": float(np.quantile(raw, 0.975)),
    }


def build_cluster_inference(
    trades: pd.DataFrame,
    events: pd.DataFrame,
    *,
    n_boot: int = DEFAULT_BOOTSTRAPS,
    seed: int = DEFAULT_SEED,
) -> pd.DataFrame:
    work = trades.copy()
    work["economic_event_id"] = _economic_event_ids(work)
    entry = pd.to_datetime(work["entry_date"])
    work["entry_week"] = entry.dt.strftime("%G-W%V")
    work["entry_month"] = entry.dt.strftime("%Y-%m")

    rows: list[dict[str, Any]] = []
    candidate_values = work["net_return"].to_numpy(float)
    base = _distribution_stats(candidate_values)
    rows.append({"level": "candidate_observation", "cluster_type": "iid_reference", **base})
    candidate_clusters = {
        "economic_event_id": work["economic_event_id"],
        "symbol": work["symbol"],
        "entry_week_block": work["entry_week"],
        "entry_month_block": work["entry_month"],
    }
    for offset, (name, labels) in enumerate(candidate_clusters.items(), start=1):
        rows.append({
            "level": "candidate_observation",
            "cluster_type": name,
            **base,
            **_cluster_bootstrap(
                candidate_values,
                labels.to_numpy(),
                n_boot=n_boot,
                seed=seed + offset,
            ),
        })

    event_values = events["mean_raw_net_return"].to_numpy(float)
    event_base = _distribution_stats(event_values)
    rows.append({"level": "economic_event", "cluster_type": "iid_reference", **event_base})
    for offset, name in enumerate(("entry_week", "entry_month"), start=20):
        rows.append({
            "level": "economic_event",
            "cluster_type": f"{name}_block",
            **event_base,
            **_cluster_bootstrap(
                event_values,
                events[name].to_numpy(),
                n_boot=n_boot,
                seed=seed + offset,
            ),
        })
    return pd.DataFrame(rows)


def _ib_cost(shares: int, price: float, is_sell: bool) -> float:
    if shares <= 0 or price <= 0:
        return 0.0
    trade_value = shares * price
    commission = max(0.35, min(shares * 0.0035, trade_value * 0.01))
    sec_fee = trade_value * 0.0000278 if is_sell else 0.0
    return commission + sec_fee + trade_value * 0.0005


def build_timing_audit(trades: pd.DataFrame, prices: dict[str, list[tuple]]) -> pd.DataFrame:
    """Reprice at the next stored trading close when same-day timing is unverifiable."""
    rows: list[dict[str, Any]] = []
    for index, trade in trades.iterrows():
        entry_date = pd.Timestamp(trade["entry_date"])
        exit_date = pd.Timestamp(trade["exit_date_t_minus_1"])
        signal = pd.to_datetime(trade.get("threshold_cross_time"), utc=True, errors="coerce")
        precision = "missing"
        if not pd.isna(signal):
            precision = "date_only_or_midnight_normalized" if (
                signal.hour == 0 and signal.minute == 0 and signal.second == 0
            ) else "timestamp_present"

        bars = prices.get(str(trade["symbol"]), [])
        later = [
            (pd.Timestamp(ts), float(close))
            for ts, _high, _low, close in bars
            if pd.Timestamp(ts).tz_localize(None) > entry_date
            and pd.Timestamp(ts).tz_localize(None) < exit_date
        ]
        record: dict[str, Any] = {
            "row_index": int(index),
            "candidate_id": trade.get("candidate_id", index),
            "economic_event_id": (
                str(trade.get("event_id"))
                if pd.notna(trade.get("event_id")) and str(trade.get("event_id")).strip()
                else f"market-fallback:{trade['market_id']}"
            ),
            "market_id": trade["market_id"],
            "symbol": trade["symbol"],
            "signal_timestamp": None if pd.isna(signal) else signal.isoformat(),
            "signal_timestamp_precision": precision,
            "exchange_timezone_required": "America/New_York",
            "original_entry_date": str(entry_date.date()),
            "original_entry_price": float(trade["entry_price"]),
            "original_net_return": float(trade["net_return"]),
            "same_session_entry_verified": False,
            "same_session_verification_reason": (
                "daily probability and equity artifacts are date-normalized; "
                "source observation time versus market close is unavailable"
            ),
            "conservative_entry_rule": "next_stored_trading_close",
            "conservative_entry_available": bool(later),
            "exit_date": str(exit_date.date()),
        }
        if later:
            next_ts, entry_price = later[0]
            exit_price = float(trade["exit_price"])
            shares = int(10_000 // entry_price)
            costs = _ib_cost(shares, entry_price, False) + _ib_cost(shares, exit_price, True)
            actual_notional = shares * entry_price
            net_return = (
                (shares * (exit_price - entry_price) - costs) / actual_notional
                if shares > 0 and actual_notional > 0
                else math.nan
            )
            record.update({
                "conservative_entry_date": str(next_ts.date()),
                "conservative_entry_price": entry_price,
                "conservative_trading_session_latency": 1,
                "conservative_net_return": net_return,
                "conservative_cost": costs,
            })
        else:
            record.update({
                "conservative_entry_date": None,
                "conservative_entry_price": math.nan,
                "conservative_trading_session_latency": math.nan,
                "conservative_net_return": math.nan,
                "conservative_cost": math.nan,
            })
        rows.append(record)
    return pd.DataFrame(rows)


def build_end_date_audit(trades: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    candidate_columns = [
        column for column in ("market_id", "symbol", "t_theta", "t_e", "question")
        if column in candidates.columns
    ]
    candidate_view = candidates[candidate_columns].drop_duplicates(["market_id", "symbol"])
    merged = trades.merge(candidate_view, on=["market_id", "symbol"], how="left", suffixes=("", "_candidate"))
    result = pd.DataFrame({
        "candidate_id": merged.get("candidate_id", pd.Series(range(len(merged)))),
        "market_id": merged["market_id"],
        "event_id": merged["event_id"],
        "symbol": merged["symbol"],
        "signal_timestamp_from_candidate": pd.to_datetime(
            merged.get("t_theta"), utc=True, errors="coerce"
        ),
        "scheduled_end_timestamp_current_artifact": pd.to_datetime(
            merged.get("t_e"), utc=True, errors="coerce"
        ),
        "exit_date_t_minus_1": merged["exit_date_t_minus_1"],
    })
    result["scheduled_end_source"] = "current candidates.parquet t_e"
    result["scheduled_end_snapshot_at_signal_available"] = False
    result["scheduled_end_revision_history_available"] = False
    result["actual_public_outcome_timestamp_available"] = False
    result["uncertainty_still_open_at_exit_status"] = "unverifiable"
    result["primary_h1_eligibility_status"] = "provisional_pending_outcome_timestamp_audit"
    result["required_correction"] = (
        "version original scheduled end and exit before min(original end, actual public outcome)"
    )
    return result


def _leave_one_rows(
    frame: pd.DataFrame,
    *,
    level: str,
    return_column: str,
    group_columns: Iterable[str],
) -> list[dict[str, Any]]:
    values = pd.to_numeric(frame[return_column], errors="coerce")
    total = float(values.sum())
    n_total = int(values.notna().sum())
    baseline = total / n_total
    rows: list[dict[str, Any]] = []
    for column in group_columns:
        if column not in frame.columns:
            continue
        grouped = frame.assign(_return=values).groupby(column, dropna=False)["_return"].agg(["sum", "count"])
        for value, group in grouped.iterrows():
            n_left = n_total - int(group["count"])
            if n_left <= 0:
                continue
            leave_mean = (total - float(group["sum"])) / n_left
            rows.append({
                "level": level,
                "group_type": column,
                "group_value": str(value),
                "n_removed": int(group["count"]),
                "baseline_mean_raw_net_return": baseline,
                "leave_out_mean_raw_net_return": leave_mean,
                "change_from_baseline": leave_mean - baseline,
            })
    return rows


def build_leave_one_out(trades: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    candidate = trades.copy()
    candidate["economic_event_id"] = _economic_event_ids(candidate)
    candidate["entry_month"] = pd.to_datetime(candidate["entry_date"]).dt.strftime("%Y-%m")
    rows = _leave_one_rows(
        candidate,
        level="candidate_observation",
        return_column="net_return",
        group_columns=("economic_event_id", "event_family", "symbol", "entry_month"),
    )
    rows.extend(_leave_one_rows(
        events,
        level="economic_event",
        return_column="mean_raw_net_return",
        group_columns=("event_family", "entry_month"),
    ))
    if not rows:
        # A single-trade sample has nothing to leave out; emit the empty frame
        # with the expected schema instead of crashing the protocol.
        return pd.DataFrame({
            "level": pd.Series(dtype="object"),
            "group_type": pd.Series(dtype="object"),
            "group_value": pd.Series(dtype="object"),
            "n_removed": pd.Series(dtype="int64"),
            "baseline_mean_raw_net_return": pd.Series(dtype="float64"),
            "leave_out_mean_raw_net_return": pd.Series(dtype="float64"),
            "change_from_baseline": pd.Series(dtype="float64"),
        })
    return pd.DataFrame(rows).sort_values(
        ["level", "group_type", "change_from_baseline"], kind="mergesort"
    ).reset_index(drop=True)


def _tail_sensitivity(values: np.ndarray, level: str) -> list[dict[str, Any]]:
    values = np.sort(np.asarray(values, dtype=float))
    values = values[np.isfinite(values)]
    rows: list[dict[str, Any]] = []
    for fraction in (0.01, 0.05, 0.10):
        k = max(1, int(math.ceil(len(values) * fraction)))
        variants = {
            f"drop_top_{int(fraction * 100)}pct": values[:-k],
            f"symmetric_trim_{int(fraction * 100)}pct": values[k:-k],
        }
        for variant, sample in variants.items():
            if len(sample) < 2:
                continue
            rows.append({
                "level": level,
                "family": "tail",
                "variant": variant,
                **_distribution_stats(sample),
            })
        top_share = float(values[-k:].sum() / values.sum()) if values.sum() else math.nan
        rows.append({
            "level": level,
            "family": "tail_concentration",
            "variant": f"top_{int(fraction * 100)}pct_share_of_total_return",
            "n": k,
            "mean_raw_net_return": top_share,
        })
    return rows


def build_sensitivities(
    trades: pd.DataFrame,
    events: pd.DataFrame,
    timing: pd.DataFrame,
) -> pd.DataFrame:
    rows = _tail_sensitivity(trades["net_return"].to_numpy(float), "candidate_observation")
    rows.extend(_tail_sensitivity(events["mean_raw_net_return"].to_numpy(float), "economic_event"))

    for multiplier in (0.0, 0.5, 1.0, 2.0, 3.0):
        returns = (
            trades["gross_pnl"].astype(float)
            - multiplier * trades["estimated_transaction_cost"].astype(float)
        ) / trades["notional"].astype(float)
        rows.append({
            "level": "candidate_observation",
            "family": "cost",
            "variant": f"modeled_cost_x_{multiplier:g}",
            **_distribution_stats(returns.to_numpy(float)),
        })

    dated = trades.copy()
    dated["entry_month"] = pd.to_datetime(dated["entry_date"]).dt.strftime("%Y-%m")
    for month, group in dated.groupby("entry_month"):
        rows.append({
            "level": "candidate_observation",
            "family": "calendar_month",
            "variant": str(month),
            **_distribution_stats(group["net_return"].to_numpy(float)),
        })
    for family, group in dated.groupby("event_family", dropna=False):
        rows.append({
            "level": "candidate_observation",
            "family": "event_family",
            "variant": str(family),
            **_distribution_stats(group["net_return"].to_numpy(float)),
        })

    conservative = timing["conservative_net_return"].dropna().to_numpy(float)
    if len(conservative):
        rows.append({
            "level": "candidate_observation",
            "family": "entry_timing",
            "variant": "next_stored_trading_close",
            **_distribution_stats(conservative),
        })
        timing_events = timing.dropna(subset=["conservative_net_return"]).copy()
        event_conservative = timing_events.groupby("economic_event_id")["conservative_net_return"].mean()
        rows.append({
            "level": "economic_event",
            "family": "entry_timing",
            "variant": "next_stored_trading_close",
            **_distribution_stats(event_conservative.to_numpy(float)),
        })
    return pd.DataFrame(rows)


def build_stage_summary(trades: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    candidate = trades.copy()
    candidate["research_stage"] = _stage_for_dates(candidate["entry_date"])
    for stage, group in candidate.groupby("research_stage", sort=False):
        rows.append({
            "level": "candidate_observation",
            "research_stage": stage,
            "stage_is_genuinely_untouched": False,
            **_distribution_stats(group["net_return"].to_numpy(float)),
        })
    for stage, group in events.groupby("research_stage", sort=False):
        rows.append({
            "level": "economic_event",
            "research_stage": stage,
            "stage_is_genuinely_untouched": False,
            **_distribution_stats(group["mean_raw_net_return"].to_numpy(float)),
        })
    return pd.DataFrame(rows)


def _benchmark_window_return(
    series: dict[str, float], entry_date: Any, exit_date: Any
) -> float:
    entry = str(entry_date)[:10]
    exit_ = str(exit_date)[:10]
    if entry not in series or exit_ not in series or series[entry] <= 0:
        return math.nan
    return series[exit_] / series[entry] - 1.0


def _benchmark_window_pnl(
    series: dict[str, float], entry_date: Any, exit_date: Any, notional: float = 10_000.0
) -> dict[str, Any]:
    entry = str(entry_date)[:10]
    exit_ = str(exit_date)[:10]
    if entry not in series or exit_ not in series or series[entry] <= 0:
        return {
            "gross_return": math.nan,
            "gross_pnl": math.nan,
            "estimated_transaction_cost": math.nan,
            "net_return": math.nan,
            "net_pnl": math.nan,
        }
    entry_price = float(series[entry])
    exit_price = float(series[exit_])
    shares = int(notional // entry_price)
    if shares <= 0:
        return {
            "gross_return": math.nan,
            "gross_pnl": math.nan,
            "estimated_transaction_cost": math.nan,
            "net_return": math.nan,
            "net_pnl": math.nan,
        }
    actual_notional = shares * entry_price
    gross_return = exit_price / entry_price - 1.0
    gross_pnl = shares * (exit_price - entry_price)
    buy_cost = _ib_cost(shares, entry_price, False)
    sell_cost = _ib_cost(shares, exit_price, True)
    total_cost = buy_cost + sell_cost
    net_pnl = gross_pnl - total_cost
    net_return = net_pnl / actual_notional
    return {
        "gross_return": gross_return,
        "gross_pnl": gross_pnl,
        "estimated_transaction_cost": total_cost,
        "net_return": net_return,
        "net_pnl": net_pnl,
    }


def _sector_benchmark_symbol(row: pd.Series) -> str:
    sector_etf = row.get("sector_etf")
    if isinstance(sector_etf, str) and sector_etf.strip():
        return sector_etf.strip()
    sector = row.get("feat_sector") or row.get("sector")
    if isinstance(sector, str) and sector.strip():
        return SECTOR_ETFS.get(sector.strip(), "SPY")
    return "SPY"


def _strict_sector_benchmark_symbol(row: pd.Series) -> str | None:
    sector_etf = row.get("sector_etf")
    if isinstance(sector_etf, str) and sector_etf.strip():
        return sector_etf.strip()
    sector = row.get("feat_sector") or row.get("sector")
    if isinstance(sector, str) and sector.strip():
        return SECTOR_ETFS.get(sector.strip())
    return None


def build_secondary_benchmark_diagnostics(
    trades: pd.DataFrame,
    prices: dict[str, list[tuple]],
    candidates: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Emit non-primary market-adjusted controls without redefining H1."""
    work = trades.copy()
    work["economic_event_id"] = _economic_event_ids(work)
    if candidates is not None and not candidates.empty:
        candidate_columns = [
            column
            for column in ("market_id", "symbol", "feat_sector", "sector", "sector_etf")
            if column in candidates.columns
        ]
        if {"market_id", "symbol"}.issubset(candidate_columns):
            candidate_view = candidates[candidate_columns].drop_duplicates(
                subset=["market_id", "symbol"]
            )
            work = work.merge(candidate_view, on=["market_id", "symbol"], how="left")
    rows: list[dict[str, Any]] = []
    for benchmark in ("sector_etf", "SPY"):
        benchmark_net_returns: list[float] = []
        benchmark_net_pnls: list[float] = []
        benchmark_costs: list[float] = []
        stock_net_pnls: list[float] = []
        excess_net_pnls: list[float] = []
        economic_event_ids: list[str] = []
        for _, row in work.iterrows():
            benchmark_symbol = benchmark
            if benchmark == "sector_etf":
                benchmark_symbol = _strict_sector_benchmark_symbol(row)
                if benchmark_symbol is None:
                    continue
            bars = prices.get(str(benchmark_symbol), [])
            series = {str(pd.Timestamp(ts).date()): float(close) for ts, _h, _l, close in bars}
            pnl = _benchmark_window_pnl(series, row["entry_date"], row["exit_date_t_minus_1"])
            if pd.isna(pnl["net_return"]):
                continue
            benchmark_net_returns.append(pnl["net_return"])
            benchmark_net_pnls.append(pnl["net_pnl"])
            benchmark_costs.append(pnl["estimated_transaction_cost"])
            stock_net_pnls.append(float(row["net_pnl"]))
            excess_net_pnls.append(float(row["net_pnl"]) - pnl["net_pnl"])
            economic_event_ids.append(str(row["economic_event_id"]))
        benchmark_net_returns = np.array(benchmark_net_returns, dtype=float)
        benchmark_net_pnls = np.array(benchmark_net_pnls, dtype=float)
        benchmark_costs = np.array(benchmark_costs, dtype=float)
        stock_net_pnls = np.array(stock_net_pnls, dtype=float)
        excess_net_pnls = np.array(excess_net_pnls, dtype=float)
        rows.append({
            "estimand_role": "secondary_diagnostic_not_H1",
            "level": "candidate_observation",
            "benchmark": benchmark,
            "n": int(len(excess_net_pnls)),
            "n_stock_better": int(np.sum(excess_net_pnls > 0)),
            "n_benchmark_better": int(np.sum(excess_net_pnls < 0)),
            "share_stock_better": float(np.mean(excess_net_pnls > 0)) if len(excess_net_pnls) else math.nan,
            "mean_stock_net_pnl": float(np.mean(stock_net_pnls)) if len(stock_net_pnls) else math.nan,
            "median_stock_net_pnl": float(np.median(stock_net_pnls)) if len(stock_net_pnls) else math.nan,
            "mean_benchmark_net_pnl": float(np.mean(benchmark_net_pnls)) if len(benchmark_net_pnls) else math.nan,
            "median_benchmark_net_pnl": float(np.median(benchmark_net_pnls)) if len(benchmark_net_pnls) else math.nan,
            "mean_excess_net_pnl": float(np.mean(excess_net_pnls)) if len(excess_net_pnls) else math.nan,
            "median_excess_net_pnl": float(np.median(excess_net_pnls)) if len(excess_net_pnls) else math.nan,
            "mean_benchmark_transaction_cost": float(np.nanmean(benchmark_costs)) if len(benchmark_costs) else math.nan,
            "p_t_one_sided": float(stats.ttest_1samp(excess_net_pnls, 0.0, alternative="greater").pvalue)
            if len(excess_net_pnls) > 1 else math.nan,
            "t_statistic": float(stats.ttest_1samp(excess_net_pnls, 0.0, alternative="greater").statistic)
            if len(excess_net_pnls) > 1 else math.nan,
            "win_rate": float(np.mean(excess_net_pnls > 0)) if len(excess_net_pnls) else math.nan,
            "standard_deviation": float(np.std(excess_net_pnls, ddof=1)) if len(excess_net_pnls) > 1 else math.nan,
            "p_binomial_win_rate_gt_50": float(stats.binomtest(int(np.sum(excess_net_pnls > 0)), len(excess_net_pnls), 0.5, alternative="greater").pvalue)
            if len(excess_net_pnls) else math.nan,
        })
        temp = pd.DataFrame({
            "economic_event_id": economic_event_ids,
            "stock_net_pnl": stock_net_pnls,
            "benchmark_net_pnl": benchmark_net_pnls,
            "excess_net_pnl": excess_net_pnls,
        })
        event_adjusted = temp.groupby("economic_event_id", dropna=False)["excess_net_pnl"].mean().dropna()
        event_stock = temp.groupby("economic_event_id", dropna=False)["stock_net_pnl"].mean().dropna()
        event_benchmark = temp.groupby("economic_event_id", dropna=False)["benchmark_net_pnl"].mean().dropna()
        rows.append({
            "estimand_role": "secondary_diagnostic_not_H1",
            "level": "economic_event",
            "benchmark": benchmark,
            "n": int(len(event_adjusted)),
            "n_stock_better": int(np.sum(event_adjusted > 0)),
            "n_benchmark_better": int(np.sum(event_adjusted < 0)),
            "share_stock_better": float(np.mean(event_adjusted > 0)) if len(event_adjusted) else math.nan,
            "mean_stock_net_pnl": float(np.mean(event_stock)) if len(event_stock) else math.nan,
            "median_stock_net_pnl": float(np.median(event_stock)) if len(event_stock) else math.nan,
            "mean_benchmark_net_pnl": float(np.mean(event_benchmark)) if len(event_benchmark) else math.nan,
            "median_benchmark_net_pnl": float(np.median(event_benchmark)) if len(event_benchmark) else math.nan,
            "mean_excess_net_pnl": float(np.mean(event_adjusted)) if len(event_adjusted) else math.nan,
            "median_excess_net_pnl": float(np.median(event_adjusted)) if len(event_adjusted) else math.nan,
            "p_t_one_sided": float(stats.ttest_1samp(event_adjusted, 0.0, alternative="greater").pvalue)
            if len(event_adjusted) > 1 else math.nan,
            "t_statistic": float(stats.ttest_1samp(event_adjusted, 0.0, alternative="greater").statistic)
            if len(event_adjusted) > 1 else math.nan,
            "win_rate": float(np.mean(event_adjusted > 0)) if len(event_adjusted) else math.nan,
            "standard_deviation": float(np.std(event_adjusted, ddof=1)) if len(event_adjusted) > 1 else math.nan,
            "p_binomial_win_rate_gt_50": float(stats.binomtest(int(np.sum(event_adjusted > 0)), len(event_adjusted), 0.5, alternative="greater").pvalue)
            if len(event_adjusted) else math.nan,
        })
    return pd.DataFrame(rows)


def build_manifest(
    paths: ProtocolPaths,
    candidates: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    n_boot: int,
    seed: int,
) -> dict[str, Any]:
    file_hashes = {
        "candidates.parquet": _sha256_file(paths.candidates),
        "prices.pkl": _sha256_file(paths.prices),
        "probs.pkl": _sha256_file(paths.probabilities),
        "experiment_walkforward_folds_clean.csv": _sha256_file(paths.folds),
        "polarity_labels.json": _sha256_file(paths.polarity),
        "raw_expectation_trades_candidate_level.csv": _sha256_file(paths.trades),
        "h1_expectation_protocol.py": _sha256_file(Path(__file__)),
    }
    dataset_components = {
        key: file_hashes[key]
        for key in ("candidates.parquet", "prices.pkl", "probs.pkl")
    }
    mapping_hash = _stable_frame_hash(
        candidates,
        (
            "event_id", "market_id", "symbol", "question", "feat_archetype",
            "feat_connection_strength", "feat_world_size",
        ),
    )
    protocol_config = {
        "protocol_version": PROTOCOL_VERSION,
        "primary_hypothesis": PRIMARY_HYPOTHESIS,
        "primary_estimand": PRIMARY_ESTIMAND,
        "primary_observation_unit": PRIMARY_OBSERVATION_UNIT,
        "primary_inference_unit": PRIMARY_INFERENCE_UNIT,
        "stage_boundaries": STAGE_BOUNDARIES,
        "entry_primary_in_current_history": "reported same-session close, timing unverifiable",
        "entry_conservative_sensitivity": "next stored trading close",
        "exit_rule": "last stored trading close before current-artifact t_e",
        "scheduled_end_snapshot_at_signal_available": False,
        "actual_public_outcome_timestamp_available": False,
        "genuine_untouched_holdout_available": False,
        "benchmark_adjusted_returns_are_primary": False,
        "n_bootstrap": n_boot,
        "seed": seed,
    }
    ids = {
        "dataset_version_id": _combined_id("dataset", dataset_components),
        "mapping_version_id": f"mapping:{mapping_hash[:16]}",
        "polarity_version_id": _combined_id(
            "polarity", {"sha256": file_hashes["polarity_labels.json"]}
        ),
        "policy_version_id": _combined_id(
            "policy", {"sha256": file_hashes["experiment_walkforward_folds_clean.csv"]}
        ),
        "observation_output_version_id": _combined_id(
            "observations", {"sha256": file_hashes["raw_expectation_trades_candidate_level.csv"]}
        ),
        "protocol_config_id": _combined_id("protocol", protocol_config),
        "implementation_version_id": _combined_id(
            "implementation", {"sha256": file_hashes["h1_expectation_protocol.py"]}
        ),
    }
    ids["analysis_run_id"] = _combined_id("h1run", ids)
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        **ids,
        "input_paths": {
            "candidates": str(paths.candidates),
            "prices": str(paths.prices),
            "probabilities": str(paths.probabilities),
            "folds": str(paths.folds),
            "polarity": str(paths.polarity),
        },
        "protocol": protocol_config,
        "git": _git_state(paths.repo_root),
        "file_sha256": file_hashes,
        "row_counts": {
            "candidates": int(len(candidates)),
            "candidate_observations": int(len(trades)),
            "unique_market_ids": int(trades["market_id"].nunique()),
            "unique_event_ids": int(_economic_event_ids(trades).nunique()),
            "unique_symbols": int(trades["symbol"].nunique()),
        },
        "limitations_that_block_confirmatory_status": [
            "same-session signal-to-close ordering is not recoverable from date-normalized artifacts",
            "original scheduled-end snapshots are not versioned at the signal timestamp",
            "actual public outcome timestamps are absent",
            "the retrospective holdout period has already influenced project development",
        ],
    }


def _pct(value: Any) -> str:
    return "—" if pd.isna(value) else f"{float(value) * 100:+.3f}%"


def _p(value: Any) -> str:
    if pd.isna(value):
        return "—"
    value = float(value)
    return "<0.0001" if value < 0.0001 else f"{value:.4f}"


def _markdown_table(frame: pd.DataFrame, columns: list[str]) -> str:
    if frame.empty:
        return "No rows."
    shown = frame[columns].copy()
    def clean(value: Any) -> str:
        if pd.isna(value):
            return "—"
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(columns) + " |"]
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for values in shown.itertuples(index=False, name=None):
        lines.append("| " + " | ".join(clean(value) for value in values) + " |")
    return "\n".join(lines)


def write_protocol_report(
    paths: ProtocolPaths,
    manifest: dict[str, Any],
    inference: pd.DataFrame,
    sensitivities: pd.DataFrame,
    leave_one: pd.DataFrame,
    timing: pd.DataFrame,
    end_audit: pd.DataFrame,
    stages: pd.DataFrame,
    benchmark: pd.DataFrame,
) -> Path:
    candidate_iid = inference[
        (inference["level"] == "candidate_observation")
        & (inference["cluster_type"] == "iid_reference")
    ].iloc[0]
    event_iid = inference[
        (inference["level"] == "economic_event")
        & (inference["cluster_type"] == "iid_reference")
    ].iloc[0]
    candidate_clusters = inference[
        (inference["level"] == "candidate_observation")
        & (inference["cluster_type"] != "iid_reference")
    ].copy()
    event_clusters = inference[
        (inference["level"] == "economic_event")
        & (inference["cluster_type"] != "iid_reference")
    ].copy()
    for table in (candidate_clusters, event_clusters):
        table["mean"] = table["mean_raw_net_return"].map(_pct)
        table["p_cluster"] = table["cluster_bootstrap_p"].map(_p)
        table["ci"] = table.apply(
            lambda row: f"[{_pct(row['cluster_ci_lo'])}, {_pct(row['cluster_ci_hi'])}]", axis=1
        )

    timing_row = sensitivities[
        (sensitivities["family"] == "entry_timing")
        & (sensitivities["level"] == "candidate_observation")
    ]
    timing_event_row = sensitivities[
        (sensitivities["family"] == "entry_timing")
        & (sensitivities["level"] == "economic_event")
    ]
    worst_leave = leave_one.nsmallest(10, "leave_out_mean_raw_net_return").copy()
    worst_leave["leave_out_mean"] = worst_leave["leave_out_mean_raw_net_return"].map(_pct)
    worst_leave["change"] = worst_leave["change_from_baseline"].map(_pct)

    stage_table = stages.copy()
    stage_table["mean"] = stage_table["mean_raw_net_return"].map(_pct)
    stage_table["median"] = stage_table["median_raw_net_return"].map(_pct)
    stage_table["positive_frequency"] = stage_table["win_rate"].map(
        lambda value: f"{value * 100:.2f}%"
    )
    stage_table["p_t"] = stage_table["p_t_one_sided"].map(_p)

    benchmark_table = benchmark.copy()
    benchmark_table["mean_stock_net_pnl"] = benchmark_table["mean_stock_net_pnl"].map(
        lambda value: f"${value:,.2f}"
    )
    benchmark_table["median_stock_net_pnl"] = benchmark_table["median_stock_net_pnl"].map(
        lambda value: f"${value:,.2f}"
    )
    benchmark_table["mean_benchmark_net_pnl"] = benchmark_table["mean_benchmark_net_pnl"].map(
        lambda value: f"${value:,.2f}"
    )
    benchmark_table["median_benchmark_net_pnl"] = benchmark_table[
        "median_benchmark_net_pnl"
    ].map(lambda value: f"${value:,.2f}")
    benchmark_table["mean_excess_net_pnl"] = benchmark_table["mean_excess_net_pnl"].map(
        lambda value: f"${value:,.2f}"
    )
    benchmark_table["median_excess_net_pnl"] = benchmark_table[
        "median_excess_net_pnl"
    ].map(lambda value: f"${value:,.2f}")
    benchmark_table["share_stock_better"] = benchmark_table["share_stock_better"].map(
        lambda value: f"{value * 100:.2f}%"
    )
    benchmark_table["p_t"] = benchmark_table["p_t_one_sided"].map(_p)

    tail_table = sensitivities[sensitivities["family"] == "tail"].copy()
    tail_table["mean"] = tail_table["mean_raw_net_return"].map(_pct)
    tail_table["median"] = tail_table["median_raw_net_return"].map(_pct)
    tail_table["positive_frequency"] = tail_table["win_rate"].map(
        lambda value: f"{value * 100:.2f}%"
    )
    tail_table["p_t"] = tail_table["p_t_one_sided"].map(_p)

    concentration_table = sensitivities[
        sensitivities["family"] == "tail_concentration"
    ].copy()
    concentration_table["share_of_total_return"] = concentration_table[
        "mean_raw_net_return"
    ].map(lambda value: f"{value * 100:.1f}%")

    cost_table = sensitivities[
        (sensitivities["family"] == "cost")
        & (sensitivities["level"] == "candidate_observation")
    ].copy()
    cost_table["mean"] = cost_table["mean_raw_net_return"].map(_pct)
    cost_table["median"] = cost_table["median_raw_net_return"].map(_pct)
    cost_table["positive_frequency"] = cost_table["win_rate"].map(
        lambda value: f"{value * 100:.2f}%"
    )
    cost_table["p_t"] = cost_table["p_t_one_sided"].map(_p)

    family_table = sensitivities[
        (sensitivities["family"] == "event_family")
        & (sensitivities["level"] == "candidate_observation")
    ].copy()
    family_table["mean"] = family_table["mean_raw_net_return"].map(_pct)
    family_table["median"] = family_table["median_raw_net_return"].map(_pct)
    family_table["positive_frequency"] = family_table["win_rate"].map(
        lambda value: f"{value * 100:.2f}%"
    )
    family_table["p_t"] = family_table["p_t_one_sided"].map(_p)

    monthly = sensitivities[
        (sensitivities["family"] == "calendar_month")
        & (sensitivities["level"] == "candidate_observation")
    ].copy()
    positive_months = int((monthly["mean_raw_net_return"] > 0).sum())
    month_extremes = pd.concat([
        monthly.nsmallest(3, "mean_raw_net_return"),
        monthly.nlargest(3, "mean_raw_net_return"),
    ]).drop_duplicates("variant")
    month_extremes["mean"] = month_extremes["mean_raw_net_return"].map(_pct)
    month_extremes["median"] = month_extremes["median_raw_net_return"].map(_pct)
    month_extremes["positive_frequency"] = month_extremes["win_rate"].map(
        lambda value: f"{value * 100:.2f}%"
    )

    def _first_or_placeholder(frame: pd.DataFrame) -> pd.Series:
        # Small samples can leave a robustness variant empty (e.g. dropping the
        # top 1% of <100 events removes zero rows and the variant is skipped).
        # Report `n/a` for that line instead of crashing the whole report.
        if len(frame):
            return frame.iloc[0]
        return pd.Series({
            "mean_raw_net_return": float("nan"),
            "p_t_one_sided": float("nan"),
            "ci": "n/a (variant empty at this sample size)",
            "p_cluster": "n/a",
        })

    event_drop_top_one = _first_or_placeholder(tail_table[
        (tail_table["level"] == "economic_event")
        & (tail_table["variant"] == "drop_top_1pct")
    ])
    candidate_drop_top_five = _first_or_placeholder(tail_table[
        (tail_table["level"] == "candidate_observation")
        & (tail_table["variant"] == "drop_top_5pct")
    ])
    candidate_cost_three = _first_or_placeholder(cost_table[
        cost_table["variant"] == "modeled_cost_x_3"
    ])
    event_month_block = _first_or_placeholder(event_clusters[
        event_clusters["cluster_type"] == "entry_month_block"
    ])

    lines = [
        "# H1 Expectation-First Protocol Report",
        "",
        f"Protocol: `{manifest['protocol_config_id']}`  ",
        f"Dataset: `{manifest['dataset_version_id']}`  ",
        f"Mapping: `{manifest['mapping_version_id']}`  ",
        f"Polarity: `{manifest['polarity_version_id']}`  ",
        f"Policy: `{manifest['policy_version_id']}`  ",
        f"Implementation: `{manifest['implementation_version_id']}`  ",
        f"Run: `{manifest['analysis_run_id']}`",
        "",
        "## Primary hypothesis",
        "",
        f"> **{PRIMARY_HYPOTHESIS}**",
        "",
        "The primary estimand is the empirical conditional mean of observed net returns. It is not alpha, and H1 does not evaluate a trading strategy. Candidate observations are mapped stock-event intervals identified through Polymarket; equal-weight economic events and clustered/block bootstraps evaluate uncertainty in that same observational claim.",
        "",
        "## Primary results",
        "",
        f"- Candidate observations: N={int(candidate_iid['n'])}, mean {_pct(candidate_iid['mean_raw_net_return'])}, median {_pct(candidate_iid['median_raw_net_return'])}, positive-return frequency {candidate_iid['win_rate'] * 100:.2f}%, one-sided mean p={_p(candidate_iid['p_t_one_sided'])}.",
        f"- True economic events: N={int(event_iid['n'])}, mean {_pct(event_iid['mean_raw_net_return'])}, median {_pct(event_iid['median_raw_net_return'])}, positive-return frequency {event_iid['win_rate'] * 100:.2f}%, one-sided mean p={_p(event_iid['p_t_one_sided'])}.",
        "",
        "### Candidate-observation mean with dependence-aware uncertainty",
        "",
        _markdown_table(candidate_clusters, ["cluster_type", "n", "n_clusters", "mean", "ci", "p_cluster"]),
        "",
        "### Equal-weight economic-event expectation",
        "",
        _markdown_table(event_clusters, ["cluster_type", "n", "n_clusters", "mean", "ci", "p_cluster"]),
        "",
        "## What the expectation tests actually establish",
        "",
        f"- The sample estimate of the conditional raw net-return expectation is positive at both levels: {_pct(candidate_iid['mean_raw_net_return'])} per candidate observation and {_pct(event_iid['mean_raw_net_return'])} per equal-weight economic event.",
        f"- The evidence is not uniform through time. At the stricter economic-event level, the month-block interval is {event_month_block['ci']} with p={event_month_block['p_cluster']}; this does not establish a positive population expectation under month-level dependence.",
        f"- The event-level mean is upper-tail dependent: after removing only the best 1% of events, the mean becomes {_pct(event_drop_top_one['mean_raw_net_return'])} with p={_p(event_drop_top_one['p_t_one_sided'])}.",
        f"- Candidate-level cost robustness is materially better: even at three times the modeled transaction costs, the estimated mean is {_pct(candidate_cost_three['mean_raw_net_return'])}. But removing the best 5% of candidate returns changes the mean to {_pct(candidate_drop_top_five['mean_raw_net_return'])}.",
        "- These results support a promising positive conditional-mean estimate, not a completed proof that the expectation is positive across new independent events and future periods. A positive expectation does not require more than half of observations to be positive; the median and positive-return frequency are distributional diagnostics, not alternative definitions of H1.",
        "",
        "## Tail and concentration sensitivity",
        "",
        _markdown_table(tail_table, ["level", "variant", "n", "mean", "median", "positive_frequency", "p_t"]),
        "",
        "Contribution shares above 100% mean that the remaining observations collectively lost money and the upper tail more than accounted for the full positive total.",
        "",
        _markdown_table(concentration_table, ["level", "variant", "n", "share_of_total_return"]),
        "",
        "## Net-return sensitivity to transaction-cost assumptions",
        "",
        _markdown_table(cost_table, ["variant", "n", "mean", "median", "positive_frequency", "p_t"]),
        "",
        "## Event-family and calendar sensitivity",
        "",
        _markdown_table(family_table, ["variant", "n", "mean", "median", "positive_frequency", "p_t"]),
        "",
        f"Candidate-level monthly mean return is positive in {positive_months} of {len(monthly)} observed entry months. The three weakest and three strongest months are:",
        "",
        _markdown_table(month_extremes, ["variant", "n", "mean", "median", "positive_frequency"]),
        "",
        "## Executable timing",
        "",
        f"Exact same-session ordering is verified for {int(timing['same_session_entry_verified'].sum())} of {len(timing)} rows. Current probability and equity artifacts are date-normalized, so the historical same-day close cannot be declared executable from the stored evidence.",
    ]
    if not timing_row.empty:
        row = timing_row.iloc[0]
        lines.append(
            f"Recomputing the observation interval from the next stored equity close gives a candidate-observation mean of {_pct(row['mean_raw_net_return'])} across N={int(row['n'])}, median {_pct(row['median_raw_net_return'])}, positive-return frequency {row['win_rate'] * 100:.2f}%, p={_p(row['p_t_one_sided'])}."
        )
    if not timing_event_row.empty:
        row = timing_event_row.iloc[0]
        lines.append(
            f"At true economic-event level under that timing convention: mean {_pct(row['mean_raw_net_return'])}, median {_pct(row['median_raw_net_return'])}, positive-return frequency {row['win_rate'] * 100:.2f}%, p={_p(row['p_t_one_sided'])}."
        )
    lines.extend([
        "",
        "## End-date and event-uncertainty provenance",
        "",
        f"- Versioned scheduled-end snapshot available at signal: {bool(end_audit['scheduled_end_snapshot_at_signal_available'].all())}.",
        f"- Actual public outcome timestamp available: {bool(end_audit['actual_public_outcome_timestamp_available'].all())}.",
        "- Until those fields exist, `T_e-1` observations are provisional for confirmatory H1 because the project cannot prove the event remained unresolved at the measured interval endpoint.",
        "",
        "## Research-stage separation",
        "",
        _markdown_table(stage_table, ["level", "research_stage", "n", "mean", "median", "positive_frequency", "p_t", "stage_is_genuinely_untouched"]),
        "",
        "No historical stage is labeled genuinely untouched. The next prospective, version-frozen sample is the confirmatory holdout.",
        "",
        "## Most influential leave-one-out exclusions",
        "",
        _markdown_table(worst_leave, ["level", "group_type", "group_value", "n_removed", "leave_out_mean", "change"]),
        "",
        "## Secondary benchmark diagnostics — after-cost stock vs benchmark",
        "",
        "These controls compare the after-cost dollar PnL of each stock trade against an equal-notional benchmark ETF trade over the same window. The sector control only uses trades with a real sector mapping; unknown-sector rows are excluded from the sector ETF comparison instead of being silently treated as SPY.",
        "",
        _markdown_table(
            benchmark_table,
            [
                "level",
                "benchmark",
                "n",
                "n_stock_better",
                "n_benchmark_better",
                "share_stock_better",
                "mean_stock_net_pnl",
                "mean_benchmark_net_pnl",
                "mean_excess_net_pnl",
                "p_t",
            ],
        ),
        "",
        "## Confirmatory status",
        "",
        "The protocol and version identifiers are now reproducible, and the raw expectation is reported at candidate and economic-event levels with event/symbol/time dependence controls. Confirmatory status remains blocked by missing point-in-time end-date history, missing actual outcome timestamps, and unverifiable same-session execution in the historical daily artifacts. The prospective hourly logger should populate those fields without moving to minute-level decisions.",
        "",
        "## Output files",
        "",
        "- `h1_expectation_manifest.json` — immutable IDs, hashes, protocol, split and limitation declarations.",
        "- `raw_expectation_true_event_level.csv` — real `event_id` aggregation.",
        "- `h1_raw_expectation_cluster_inference.csv` — event, symbol, week and month bootstrap inference.",
        "- `h1_timing_audit.csv` — same-session verification and next-session repricing.",
        "- `h1_end_date_uncertainty_audit.csv` — scheduled-end and outcome-timestamp provenance.",
        "- `h1_leave_one_out.csv` — event, family, ticker and month exclusions.",
        "- `h1_sensitivity.csv` — tails, families, months, timing and cost multipliers.",
        "- `h1_research_stage_summary.csv` — discovery, model selection, validation and retrospective holdout.",
        "- `h1_secondary_benchmark_diagnostics.csv` — explicitly non-primary market-adjusted controls.",
    ])
    report_path = paths.output_dir / "h1_expectation_protocol_report.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def run_expectation_protocol(
    repo_root: str | Path,
    output_dir: str | Path | None = None,
    *,
    candidates_path: str | Path | None = None,
    n_boot: int = DEFAULT_BOOTSTRAPS,
    seed: int = DEFAULT_SEED,
) -> dict[str, Path]:
    repo_root = Path(repo_root).resolve()
    output = (
        Path(output_dir).resolve()
        if output_dir is not None
        else repo_root / "output" / "raw_expectation_tminus1"
    )
    output.mkdir(parents=True, exist_ok=True)
    candidate_file = Path(candidates_path).resolve() if candidates_path is not None else None
    paths = ProtocolPaths(
        repo_root=repo_root,
        output_dir=output,
        candidate_file=candidate_file,
    )

    trades = pd.read_csv(paths.trades)
    missing = REQUIRED_TRADE_COLUMNS.difference(trades.columns)
    if missing:
        raise ValueError(f"H1 trade file is missing required columns: {sorted(missing)}")
    candidates = pd.read_parquet(paths.candidates)
    with paths.prices.open("rb") as handle:
        prices = pickle.load(handle)

    events = build_true_event_frame(trades)
    inference = build_cluster_inference(trades, events, n_boot=n_boot, seed=seed)
    timing = build_timing_audit(trades, prices)
    end_audit = build_end_date_audit(trades, candidates)
    leave_one = build_leave_one_out(trades, events)
    sensitivities = build_sensitivities(trades, events, timing)
    stages = build_stage_summary(trades, events)
    benchmark = build_secondary_benchmark_diagnostics(trades, prices, candidates)
    manifest = build_manifest(paths, candidates, trades, n_boot=n_boot, seed=seed)

    outputs = {
        "manifest": output / "h1_expectation_manifest.json",
        "events": output / "raw_expectation_true_event_level.csv",
        "inference": output / "h1_raw_expectation_cluster_inference.csv",
        "timing": output / "h1_timing_audit.csv",
        "end_audit": output / "h1_end_date_uncertainty_audit.csv",
        "leave_one": output / "h1_leave_one_out.csv",
        "sensitivity": output / "h1_sensitivity.csv",
        "stages": output / "h1_research_stage_summary.csv",
        "benchmark": output / "h1_secondary_benchmark_diagnostics.csv",
    }
    outputs["manifest"].write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    events.to_csv(outputs["events"], index=False)
    inference.to_csv(outputs["inference"], index=False)
    timing.to_csv(outputs["timing"], index=False)
    end_audit.to_csv(outputs["end_audit"], index=False)
    leave_one.to_csv(outputs["leave_one"], index=False)
    sensitivities.to_csv(outputs["sensitivity"], index=False)
    stages.to_csv(outputs["stages"], index=False)
    benchmark.to_csv(outputs["benchmark"], index=False)
    outputs["report"] = write_protocol_report(
        paths,
        manifest,
        inference,
        sensitivities,
        leave_one,
        timing,
        end_audit,
        stages,
        benchmark,
    )
    return outputs


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    written = run_expectation_protocol(root)
    for name, path in written.items():
        print(f"{name}: {path}")
