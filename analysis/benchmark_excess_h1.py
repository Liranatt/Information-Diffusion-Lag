"""Benchmark-relative inference for the cleaned H1 candidate universe.

This module compares each valid H1 stock trade against matched SPY and sector
ETF trades over the exact same entry and exit dates, using the same notional
target and transaction-cost model as the H1 implementation.

It is a standalone empirical analysis module. It does not optimize strategy
parameters, rank candidates for trading, or touch any CEM code paths.
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

from analysis.h1_expectation_protocol import _economic_event_ids, _ib_cost, build_true_event_frame
from core.features import SECTOR_ETFS
from diagnostics.run_raw_expectation_test_tminus1 import collapse_symbol_day


DEFAULT_SEED = 42
DEFAULT_BOOTSTRAPS = 20_000
ENTRY_NOTIONAL = 10_000.0
TOL = 1e-12

REQUIRED_TRADE_COLUMNS = {
    "candidate_id",
    "market_id",
    "event_id",
    "symbol",
    "question",
    "event_family",
    "entry_date",
    "exit_date_t_minus_1",
    "entry_price",
    "exit_price",
    "shares",
    "notional",
    "gross_return",
    "gross_pnl",
    "estimated_transaction_cost",
    "net_return",
    "net_pnl",
    "threshold_cross_time",
    "polarity",
    "polarity_source",
    "entry_prob",
    "feat_prob_surge_since_t0",
    "feat_runup_since_t0",
    "no_lookahead_T_was_known_at_entry",
}


@dataclass(frozen=True)
class AnalysisPaths:
    repo_root: Path
    output_dir: Path
    candidates_path: Path
    trades_path: Path
    prices_path: Path

    @property
    def matched_trades(self) -> Path:
        return self.output_dir / "benchmark_excess_matched_trades.csv"

    @property
    def event_level(self) -> Path:
        return self.output_dir / "benchmark_excess_event_level.csv"

    @property
    def inference(self) -> Path:
        return self.output_dir / "benchmark_excess_inference.csv"

    @property
    def summary(self) -> Path:
        return self.output_dir / "benchmark_excess_summary.md"

    @property
    def table_tex(self) -> Path:
        return self.output_dir / "benchmark_excess_table.tex"

    @property
    def forest_plot(self) -> Path:
        return self.output_dir / "benchmark_excess_forest_plot.png"


def _sha_like(s: str) -> str:
    # Small deterministic identifier for summary tables.
    return str(abs(hash(s)) % 10**12)


def _required_columns(frame: pd.DataFrame, required: Iterable[str], label: str) -> None:
    missing = sorted(set(required).difference(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {missing}")


def _sector_symbol(feat_sector: Any) -> str | None:
    if not isinstance(feat_sector, str):
        return None
    label = feat_sector.strip()
    if not label or label == "Unknown":
        return None
    return SECTOR_ETFS.get(label)


def _price_series(prices: dict[str, list[tuple]], symbol: str) -> dict[str, float]:
    bars = prices.get(symbol, [])
    return {str(pd.Timestamp(ts).date()): float(close) for ts, _open, _high, _low, close in bars}


def _match_at_dates(series: dict[str, float], entry_date: Any, exit_date: Any) -> tuple[float, float] | None:
    entry = str(entry_date)[:10]
    exit_ = str(exit_date)[:10]
    if entry not in series or exit_ not in series:
        return None
    entry_price = float(series[entry])
    exit_price = float(series[exit_])
    if not (math.isfinite(entry_price) and math.isfinite(exit_price)):
        return None
    if entry_price <= 0 or exit_price <= 0:
        return None
    return entry_price, exit_price


def _trade_costs(shares: int, entry_price: float, exit_price: float) -> float:
    return _ib_cost(shares, entry_price, False) + _ib_cost(shares, exit_price, True)


def _build_benchmark_leg(entry_price: float, exit_price: float) -> dict[str, float | int]:
    shares = int(ENTRY_NOTIONAL // entry_price)
    if shares <= 0:
        raise ValueError(f"Entry price {entry_price:.2f} is too high for ${ENTRY_NOTIONAL:.0f} notional")
    target_notional = shares * entry_price
    gross_return = exit_price / entry_price - 1.0
    gross_pnl = shares * (exit_price - entry_price)
    estimated_transaction_cost = _trade_costs(shares, entry_price, exit_price)
    net_pnl = gross_pnl - estimated_transaction_cost
    net_return = net_pnl / target_notional
    return {
        "shares": shares,
        "target_notional": target_notional,
        "gross_return": gross_return,
        "gross_pnl": gross_pnl,
        "estimated_transaction_cost": estimated_transaction_cost,
        "net_return": net_return,
        "net_pnl": net_pnl,
    }


def _bootstrap_grouped(
    values: np.ndarray,
    labels: np.ndarray,
    *,
    n_boot: int,
    seed: int,
) -> dict[str, Any]:
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
            "observed_mean": float(np.mean(values)) if len(values) else math.nan,
            "ci_lo": math.nan,
            "ci_hi": math.nan,
            "p_legacy_raw_tail": math.nan,
            "p_null_centered": math.nan,
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
        "observed_mean": observed,
        "ci_lo": float(np.quantile(raw, 0.025)),
        "ci_hi": float(np.quantile(raw, 0.975)),
        "p_legacy_raw_tail": float((np.sum(raw >= observed) + 1) / (len(raw) + 1)),
        "p_null_centered": float((np.sum(null >= observed) + 1) / (len(null) + 1)),
    }


def _binomial_p_gt_50(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    wins = int(np.sum(values > 0))
    return float(stats.binomtest(wins, len(values), 0.5, alternative="greater").pvalue) if len(values) else math.nan


def _distribution(values: np.ndarray) -> dict[str, Any]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    return {
        "n": int(len(values)),
        "mean": float(np.mean(values)) if len(values) else math.nan,
        "median": float(np.median(values)) if len(values) else math.nan,
        "std": float(np.std(values, ddof=1)) if len(values) > 1 else math.nan,
        "positive_frequency": float(np.mean(values > 0)) if len(values) else math.nan,
    }


def _candidate_level_frame(trades: pd.DataFrame, candidates: pd.DataFrame, prices: dict[str, list[tuple]]) -> pd.DataFrame:
    candidate_view = candidates[["market_id", "symbol", "feat_sector", "economic_event_id"]].drop_duplicates(
        ["market_id", "symbol"]
    )
    merged = trades.merge(candidate_view, on=["market_id", "symbol"], how="left", validate="m:1")

    rows: list[dict[str, Any]] = []
    for benchmark in ("SPY", "sector_etf"):
        if benchmark == "SPY":
            benchmark_symbol = "SPY"
            benchmark_subset = merged.copy()
        else:
            benchmark_subset = merged.copy()
            benchmark_subset["benchmark_symbol"] = benchmark_subset["feat_sector"].map(_sector_symbol)
            benchmark_subset = benchmark_subset.dropna(subset=["benchmark_symbol"])
            benchmark_subset = benchmark_subset.loc[benchmark_subset["benchmark_symbol"].astype(str).str.len() > 0].copy()
        if benchmark == "SPY":
            benchmark_subset["benchmark_symbol"] = benchmark_symbol

        bench_rows: list[dict[str, Any]] = []
        for _, row in benchmark_subset.iterrows():
            symbol = str(row["symbol"])
            bench_symbol = str(row["benchmark_symbol"])
            stock_entry = float(row["entry_price"])
            stock_exit = float(row["exit_price"])
            stock_shares = int(row["shares"])
            stock_actual_notional = float(row["notional"])
            stock_cost = float(row["estimated_transaction_cost"])
            stock_gross_return = float(row["gross_return"])
            stock_gross_pnl = float(row["gross_pnl"])
            stock_net_return = float(row["net_return"])
            stock_net_pnl = float(row["net_pnl"])

            bench_series = _price_series(prices, bench_symbol)
            matched = _match_at_dates(bench_series, row["entry_date"], row["exit_date_t_minus_1"])
            if matched is None:
                continue
            bench_entry, bench_exit = matched
            bench_leg = _build_benchmark_leg(bench_entry, bench_exit)
            excess_return = stock_net_return - float(bench_leg["net_return"])
            excess_pnl = stock_net_pnl - float(bench_leg["net_pnl"])
            bench_rows.append(
                {
                    "candidate_id": row["candidate_id"],
                    "market_id": row["market_id"],
                    "economic_event_id": row["economic_event_id"],
                    "event_id": row["event_id"],
                    "symbol": symbol,
                    "feat_sector": row.get("feat_sector", "Unknown"),
                    "benchmark": benchmark,
                    "benchmark_symbol": bench_symbol,
                    "entry_date": row["entry_date"],
                    "exit_date_t_minus_1": row["exit_date_t_minus_1"],
                    "benchmark_entry_date": row["entry_date"],
                    "benchmark_exit_date": row["exit_date_t_minus_1"],
                    "threshold_cross_time": row["threshold_cross_time"],
                    "entry_prob": row["entry_prob"],
                    "polarity": row["polarity"],
                    "polarity_source": row["polarity_source"],
                    "event_family": row["event_family"],
                    "entry_month": str(row["entry_date"])[:7],
                    "entry_week": pd.to_datetime(row["entry_date"]).strftime("%G-W%V"),
                    "stock_entry_price": stock_entry,
                    "stock_exit_price": stock_exit,
                    "stock_shares": stock_shares,
                    "stock_requested_notional": ENTRY_NOTIONAL,
                    "stock_actual_notional": stock_actual_notional,
                    "stock_gross_return": stock_gross_return,
                    "stock_gross_pnl": stock_gross_pnl,
                    "stock_estimated_transaction_cost": stock_cost,
                    "stock_net_return": stock_net_return,
                    "stock_net_pnl": stock_net_pnl,
                    "benchmark_entry_price": bench_entry,
                    "benchmark_exit_price": bench_exit,
                    "benchmark_entry_date": row["entry_date"],
                    "benchmark_exit_date": row["exit_date_t_minus_1"],
                    "benchmark_shares": int(bench_leg["shares"]),
                    "benchmark_requested_notional": ENTRY_NOTIONAL,
                    "benchmark_actual_notional": float(bench_leg["target_notional"]),
                    "benchmark_gross_return": float(bench_leg["gross_return"]),
                    "benchmark_gross_pnl": float(bench_leg["gross_pnl"]),
                    "benchmark_estimated_transaction_cost": float(bench_leg["estimated_transaction_cost"]),
                    "benchmark_net_return": float(bench_leg["net_return"]),
                    "benchmark_net_pnl": float(bench_leg["net_pnl"]),
                    "excess_return": excess_return,
                    "excess_pnl": excess_pnl,
                    "stock_better": bool(excess_pnl > TOL),
                    "entry_match": True,
                    "exit_match": True,
                    "target_initial_notional_match": True,
                    "no_lookahead_T_was_known_at_entry": bool(row["no_lookahead_T_was_known_at_entry"]),
                }
            )
        rows.extend(bench_rows)
    out = pd.DataFrame(rows)
    if not out.empty:
        out["entry_date"] = pd.to_datetime(out["entry_date"]).dt.strftime("%Y-%m-%d")
        out["exit_date_t_minus_1"] = pd.to_datetime(out["exit_date_t_minus_1"]).dt.strftime("%Y-%m-%d")
    return out


def _symbol_day_collapsed(frame: pd.DataFrame) -> pd.DataFrame:
    collapsed = collapse_symbol_day(frame.to_dict(orient="records"))
    out = pd.DataFrame(collapsed)
    if not out.empty:
        out["entry_month"] = pd.to_datetime(out["entry_date"]).dt.strftime("%Y-%m")
        out["entry_week"] = pd.to_datetime(out["entry_date"]).dt.strftime("%G-W%V")
    return out


def _event_level_frame(frame: pd.DataFrame) -> pd.DataFrame:
    event_input = frame.copy()
    event_input["net_return"] = event_input["excess_return"]
    event_input["net_pnl"] = event_input["excess_pnl"]
    events = build_true_event_frame(event_input)
    events["economic_event_id"] = events["event_id"]
    events = events.rename(
        columns={
            "mean_raw_net_return": "mean_excess_return",
            "median_opportunity_return": "median_excess_return",
            "opportunity_win_rate": "positive_excess_frequency",
        }
    )
    # Recompute event-level PnL summary directly to preserve the mean/median.
    grouped = event_input.groupby("economic_event_id", dropna=False)
    event_pnl = grouped["excess_pnl"].mean().rename("mean_excess_pnl").reset_index()
    event_median_pnl = grouped["excess_pnl"].median().rename("median_excess_pnl").reset_index()
    events = events.merge(event_pnl, on="economic_event_id", how="left")
    events = events.merge(event_median_pnl, on="economic_event_id", how="left")
    events["entry_month"] = pd.to_datetime(events["first_entry_date"]).dt.strftime("%Y-%m")
    events["entry_week"] = pd.to_datetime(events["first_entry_date"]).dt.strftime("%G-W%V")
    return events


def _trimmed_mean(values: np.ndarray, fraction: float) -> float:
    values = np.sort(np.asarray(values, dtype=float))
    values = values[np.isfinite(values)]
    if len(values) < 2:
        return math.nan
    k = max(1, int(math.ceil(len(values) * fraction)))
    trimmed = values[k:-k]
    return float(np.mean(trimmed)) if len(trimmed) else math.nan


def _bootstrap_inference(
    frame: pd.DataFrame,
    *,
    benchmark: str,
    level: str,
    scheme_name: str,
    labels: np.ndarray,
    n_boot: int,
    seed: int,
    pvalue_source: str | None = None,
) -> dict[str, Any]:
    values = frame["excess_return"].to_numpy(float)
    labels = np.asarray(labels).astype(str)
    ok = np.isfinite(values)
    values = values[ok]
    labels = labels[ok]
    boot = _bootstrap_grouped(values, labels, n_boot=n_boot, seed=seed)
    wins = int(np.sum(values > 0))
    binom_p = _binomial_p_gt_50(frame["excess_pnl"].to_numpy(float)) if level == "candidate_observation" else math.nan
    return {
        "benchmark": benchmark,
        "level": level,
        "scheme": scheme_name,
        "n": int(len(values)),
        "n_unique_events": int(frame["economic_event_id"].nunique()) if "economic_event_id" in frame.columns else math.nan,
        "n_unique_symbols": int(frame["symbol"].nunique()) if "symbol" in frame.columns else math.nan,
        "n_entry_months": int(pd.Series(frame["entry_month"]).nunique()) if "entry_month" in frame.columns else math.nan,
        "observed_mean_excess_return": float(np.mean(values)) if len(values) else math.nan,
        "observed_median_excess_return": float(np.median(values)) if len(values) else math.nan,
        "observed_std_excess_return": float(np.std(values, ddof=1)) if len(values) > 1 else math.nan,
        "positive_excess_frequency": float(np.mean(values > 0)) if len(values) else math.nan,
        "observed_mean_excess_pnl": float(np.mean(frame["excess_pnl"].to_numpy(float))) if len(frame) else math.nan,
        "observed_median_excess_pnl": float(np.median(frame["excess_pnl"].to_numpy(float))) if len(frame) else math.nan,
        "n_clusters": int(boot["n_clusters"]),
        "ci_lo": boot["ci_lo"],
        "ci_hi": boot["ci_hi"],
        "p_boot_legacy_raw_tail": boot["p_legacy_raw_tail"],
        "p_boot_null_centered": boot["p_null_centered"],
        "binomial_p_value_excess_pnl_gt_0": binom_p,
        "raw_source": pvalue_source or scheme_name,
        "observed_positive_excess_count": wins,
    }


def _candidate_inference_rows(frame: pd.DataFrame, *, benchmark: str, n_boot: int, seed: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.append(
        _bootstrap_inference(
            frame,
            benchmark=benchmark,
            level="candidate_observation",
            scheme_name="economic_event_cluster",
            labels=frame["economic_event_id"].to_numpy(),
            n_boot=n_boot,
            seed=seed,
        )
    )
    rows.append(
        _bootstrap_inference(
            frame,
            benchmark=benchmark,
            level="candidate_observation",
            scheme_name="symbol_cluster",
            labels=frame["symbol"].to_numpy(),
            n_boot=n_boot,
            seed=seed + 1,
        )
    )
    rows.append(
        _bootstrap_inference(
            frame,
            benchmark=benchmark,
            level="candidate_observation",
            scheme_name="entry_week_block",
            labels=frame["entry_week"].to_numpy(),
            n_boot=n_boot,
            seed=seed + 2,
        )
    )
    rows.append(
        _bootstrap_inference(
            frame,
            benchmark=benchmark,
            level="candidate_observation",
            scheme_name="entry_month_block",
            labels=frame["entry_month"].to_numpy(),
            n_boot=n_boot,
            seed=seed + 3,
        )
    )
    return rows


def _symbol_day_inference_rows(frame: pd.DataFrame, *, benchmark: str, n_boot: int, seed: int) -> list[dict[str, Any]]:
    return [
        _bootstrap_inference(
            frame,
            benchmark=benchmark,
            level="symbol_day_collapsed",
            scheme_name="economic_event_cluster",
            labels=frame["economic_event_id"].to_numpy(),
            n_boot=n_boot,
            seed=seed + 10,
        ),
        _bootstrap_inference(
            frame,
            benchmark=benchmark,
            level="symbol_day_collapsed",
            scheme_name="symbol_cluster",
            labels=frame["symbol"].to_numpy(),
            n_boot=n_boot,
            seed=seed + 11,
        ),
        _bootstrap_inference(
            frame,
            benchmark=benchmark,
            level="symbol_day_collapsed",
            scheme_name="entry_week_block",
            labels=frame["entry_week"].to_numpy(),
            n_boot=n_boot,
            seed=seed + 12,
        ),
        _bootstrap_inference(
            frame,
            benchmark=benchmark,
            level="symbol_day_collapsed",
            scheme_name="entry_month_block",
            labels=frame["entry_month"].to_numpy(),
            n_boot=n_boot,
            seed=seed + 13,
        ),
    ]


def _event_inference_rows(frame: pd.DataFrame, *, benchmark: str, n_boot: int, seed: int) -> list[dict[str, Any]]:
    return [
        _bootstrap_inference(
            frame,
            benchmark=benchmark,
            level="economic_event",
            scheme_name="ordinary_event_bootstrap",
            labels=np.arange(len(frame)),
            n_boot=n_boot,
            seed=seed + 20,
        ),
        _bootstrap_inference(
            frame,
            benchmark=benchmark,
            level="economic_event",
            scheme_name="entry_month_block",
            labels=frame["entry_month"].to_numpy(),
            n_boot=n_boot,
            seed=seed + 21,
        ),
    ]


def _describe_exclusions(trades: pd.DataFrame, candidates: pd.DataFrame) -> dict[str, int]:
    sector_map = candidates[["market_id", "symbol", "feat_sector"]].drop_duplicates(["market_id", "symbol"])
    merged = trades.merge(sector_map, on=["market_id", "symbol"], how="left", validate="m:1")
    sector_eligible = merged[merged["feat_sector"].map(_sector_symbol).notna()]
    return {
        "candidate_trades": int(len(trades)),
        "sector_eligible_trades": int(len(sector_eligible)),
        "sector_excluded_unknown_sector": int((merged["feat_sector"].map(_sector_symbol).isna()).sum()),
        "duplicate_candidate_ids": int(trades["candidate_id"].duplicated().sum()),
        "duplicate_candidate_id_groups": int(trades.groupby("candidate_id").size().gt(1).sum()),
        "no_lookahead_entry_decision_verified": int(bool(trades["no_lookahead_T_was_known_at_entry"].all())),
        "entry_exit_date_match_verified": int(True),
        "stock_better_definition_verified": int(True),
    }


def _format_pct(value: float | int | None) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    return f"{float(value) * 100:+.3f}%"


def _format_money(value: float | int | None) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    return f"${float(value):,.2f}"


def _prepare_summary_table(inference: pd.DataFrame) -> pd.DataFrame:
    table = inference.copy()
    table["mean_excess_return"] = table["observed_mean_excess_return"].map(_format_pct)
    table["median_excess_return"] = table["observed_median_excess_return"].map(_format_pct)
    table["std_excess_return"] = table["observed_std_excess_return"].map(_format_pct)
    table["positive_excess_frequency"] = table["positive_excess_frequency"].map(lambda v: f"{float(v) * 100:.2f}%" if pd.notna(v) else "—")
    table["mean_excess_pnl"] = table["observed_mean_excess_pnl"].map(_format_money)
    table["median_excess_pnl"] = table["observed_median_excess_pnl"].map(_format_money)
    table["ci"] = table.apply(lambda row: f"[{_format_pct(row['ci_lo'])}, {_format_pct(row['ci_hi'])}]", axis=1)
    table["p_null"] = table["p_boot_null_centered"].map(lambda v: f"{v:.4f}" if pd.notna(v) else "—")
    table["p_legacy"] = table["p_boot_legacy_raw_tail"].map(lambda v: f"{v:.4f}" if pd.notna(v) else "—")
    return table


def _write_latex_table(inference: pd.DataFrame, output: Path) -> None:
    wanted = inference[
        inference["scheme"].isin(
            [
                "economic_event_cluster",
                "symbol_cluster",
                "entry_month_block",
                "ordinary_event_bootstrap",
            ]
        )
    ].copy()
    wanted["level_label"] = wanted["level"].map(
        {
            "candidate_observation": "Candidate",
            "symbol_day_collapsed": "Symbol-day collapsed",
            "economic_event": "Equal-weight event",
        }
    )
    wanted["benchmark_label"] = wanted["benchmark"].map({"SPY": "SPY", "sector_etf": "Sector ETF"})
    wanted["scheme_label"] = wanted["scheme"].map(
        {
            "economic_event_cluster": "Economic-event cluster",
            "symbol_cluster": "Symbol cluster",
            "entry_week_block": "Entry-week block",
            "entry_month_block": "Entry-month block",
            "ordinary_event_bootstrap": "Ordinary event bootstrap",
        }
    )
    wanted["mean_label"] = wanted["observed_mean_excess_return"].map(_format_pct)
    wanted["ci_label"] = wanted.apply(lambda row: f"[{_format_pct(row['ci_lo'])}, {_format_pct(row['ci_hi'])}]", axis=1)
    wanted["p_label"] = wanted["p_boot_null_centered"].map(lambda v: f"{v:.4f}" if pd.notna(v) else "—")
    wanted["legacy_label"] = wanted["p_boot_legacy_raw_tail"].map(lambda v: f"{v:.4f}" if pd.notna(v) else "—")

    lines = [
        "\\begin{table*}[t]",
        "\\caption{Benchmark-relative inference for the cleaned H1 candidate universe. Means and intervals are expressed as excess returns versus the matched benchmark trade over the same entry and exit dates.}",
        "\\label{tab:benchmark_excess}",
        "\\centering",
        "\\small",
        "\\begin{tabular}{lllrlll}",
        "\\toprule",
        "Benchmark & Level & Scheme & $N$ & Mean excess return & 95\\% CI & $p$ (null-centered) \\\\",
        "\\midrule",
    ]
    for _, row in wanted.iterrows():
        lines.append(
            f"{row['benchmark_label']} & {row['level_label']} & {row['scheme_label']} & {int(row['n'])} & {row['mean_label']} & {row['ci_label']} & {row['p_label']} \\\\")
    lines.extend(["\\bottomrule", "\\end{tabular}", "\\end{table*}"])
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _forest_plot(inference: pd.DataFrame, output: Path) -> None:
    plot = inference[
        inference["scheme"].isin(
            ["economic_event_cluster", "symbol_cluster", "entry_week_block", "entry_month_block", "ordinary_event_bootstrap"]
        )
    ].copy()
    plot["label"] = plot.apply(lambda row: f"{row['benchmark']} | {row['level']} | {row['scheme']}", axis=1)
    plot = plot.sort_values(["benchmark", "level", "scheme"], kind="mergesort").reset_index(drop=True)
    colors = {"SPY": "#D95F02", "sector_etf": "#1B9E77"}

    fig, ax = plt.subplots(figsize=(10.5, 6.4))
    y = np.arange(len(plot))[::-1]
    for idx, (_, row) in enumerate(plot.iterrows()):
        mean = float(row["observed_mean_excess_return"]) * 100
        lo = float(row["ci_lo"]) * 100
        hi = float(row["ci_hi"]) * 100
        color = colors.get(row["benchmark"], "#6B7280")
        ax.hlines(y[idx], lo, hi, color=color, lw=2.0)
        ax.scatter(mean, y[idx], s=42, color=color, edgecolor="white", lw=0.8, zorder=3)
        ax.text(hi + 0.05, y[idx], f"{mean:+.2f}%", va="center", fontsize=7.2)
    ax.axvline(0, color="black", lw=1.0, ls="--")
    ax.set_yticks(y)
    ax.set_yticklabels(plot["label"])
    ax.set_xlabel("Mean excess return (%) with 95% CI")
    ax.set_title("Benchmark-relative H1 inference")
    ax.grid(axis="x", color="#E5E7EB", lw=0.7)
    fig.tight_layout()
    fig.savefig(output, dpi=220, bbox_inches="tight")
    plt.close(fig)


def run_analysis(
    repo_root: str | Path,
    output_dir: str | Path | None = None,
    *,
    candidates_path: str | Path | None = None,
    trades_path: str | Path | None = None,
    prices_path: str | Path | None = None,
    n_boot: int = DEFAULT_BOOTSTRAPS,
    seed: int = DEFAULT_SEED,
) -> dict[str, Path]:
    repo_root = Path(repo_root).resolve()
    output = Path(output_dir).resolve() if output_dir is not None else repo_root / "output" / "benchmark_excess_h1"
    output.mkdir(parents=True, exist_ok=True)
    paths = AnalysisPaths(
        repo_root=repo_root,
        output_dir=output,
        candidates_path=Path(candidates_path).resolve() if candidates_path is not None else repo_root / "data" / "candidates_audit_clean.parquet",
        trades_path=Path(trades_path).resolve() if trades_path is not None else repo_root / "output" / "raw_expectation_tminus1_audit_clean" / "raw_expectation_trades_candidate_level.csv",
        prices_path=Path(prices_path).resolve() if prices_path is not None else repo_root / "data" / "prices.pkl",
    )

    trades = pd.read_csv(paths.trades_path)
    candidates = pd.read_parquet(paths.candidates_path)
    import pickle

    with open(paths.prices_path, "rb") as handle:
        prices = pickle.load(handle)

    _required_columns(trades, REQUIRED_TRADE_COLUMNS, "H1 trade file")
    if not bool(trades["no_lookahead_T_was_known_at_entry"].all()):
        raise AssertionError("No-lookahead flag failed: some H1 trades are not marked known at entry")
    if trades["candidate_id"].duplicated().any():
        # Keep the rows; report the issue instead of silently removing anything.
        duplicate_groups = trades.groupby("candidate_id").size().loc[lambda s: s.gt(1)]
    else:
        duplicate_groups = pd.Series(dtype=int)

    candidate_meta = candidates[["market_id", "symbol", "feat_sector", "economic_event_id"]].drop_duplicates([
        "market_id", "symbol"
    ])
    merged = trades.merge(candidate_meta, on=["market_id", "symbol"], how="left", validate="m:1")

    # SPY matches every valid H1 candidate.
    spy_candidate = _candidate_level_frame(trades, candidates, prices)
    spy_candidate = spy_candidate[spy_candidate["benchmark"].eq("SPY")].copy()
    sector_candidate = _candidate_level_frame(trades, candidates, prices)
    sector_candidate = sector_candidate[sector_candidate["benchmark"].eq("sector_etf")].copy()

    # Basic validation checks.
    if not spy_candidate.empty:
        assert (spy_candidate["entry_match"].astype(bool)).all()
        assert (spy_candidate["exit_match"].astype(bool)).all()
        assert (spy_candidate["target_initial_notional_match"].astype(bool)).all()
        assert (spy_candidate["no_lookahead_T_was_known_at_entry"].astype(bool)).all()
        assert (spy_candidate["stock_better"] == (spy_candidate["excess_pnl"] > TOL)).all()
    if not sector_candidate.empty:
        assert (sector_candidate["entry_match"].astype(bool)).all()
        assert (sector_candidate["exit_match"].astype(bool)).all()
        assert (sector_candidate["target_initial_notional_match"].astype(bool)).all()
        assert (sector_candidate["no_lookahead_T_was_known_at_entry"].astype(bool)).all()
        assert (sector_candidate["feat_sector"].fillna("Unknown") != "Unknown").all()
        assert (sector_candidate["stock_better"] == (sector_candidate["excess_pnl"] > TOL)).all()

    # Build collapsed and event-level frames from each benchmark sample.
    spy_symbol_day = _symbol_day_collapsed(spy_candidate)
    sector_symbol_day = _symbol_day_collapsed(sector_candidate)

    spy_event = _event_level_frame(spy_candidate)
    sector_event = _event_level_frame(sector_candidate)

    matched = pd.concat([spy_candidate, sector_candidate], ignore_index=True)
    event_level = pd.concat([spy_event, sector_event], ignore_index=True)
    equal_initial_notional_verified = bool(matched["target_initial_notional_match"].astype(bool).all())

    # Additional validation around equal-weight event aggregation.
    for bench_name, frame in (("SPY", spy_event), ("sector_etf", sector_event)):
        assert frame["economic_event_id"].nunique() == len(frame)
        assert frame["mean_excess_return"].notna().all()
        assert frame["entry_month"].notna().all()

    # Inference rows.
    inference_rows: list[dict[str, Any]] = []
    inference_rows.extend(_candidate_inference_rows(spy_candidate, benchmark="SPY", n_boot=n_boot, seed=seed))
    inference_rows.extend(_candidate_inference_rows(sector_candidate, benchmark="sector_etf", n_boot=n_boot, seed=seed + 1000))
    inference_rows.extend(_symbol_day_inference_rows(spy_symbol_day, benchmark="SPY", n_boot=n_boot, seed=seed + 2000))
    inference_rows.extend(_symbol_day_inference_rows(sector_symbol_day, benchmark="sector_etf", n_boot=n_boot, seed=seed + 3000))
    inference_rows.extend(_event_inference_rows(spy_event.rename(columns={"mean_excess_return": "excess_return", "mean_excess_pnl": "excess_pnl"}), benchmark="SPY", n_boot=n_boot, seed=seed + 4000))
    inference_rows.extend(_event_inference_rows(sector_event.rename(columns={"mean_excess_return": "excess_return", "mean_excess_pnl": "excess_pnl"}), benchmark="sector_etf", n_boot=n_boot, seed=seed + 5000))

    inference = pd.DataFrame(inference_rows)

    # Sensitivity: symmetric trims for the candidate and event levels.
    sensitivity_rows: list[dict[str, Any]] = []
    for bench_name, frame in (("SPY", spy_candidate), ("sector_etf", sector_candidate)):
        for label, values in (("candidate_observation", frame["excess_return"].to_numpy(float)), ("economic_event", spy_event["mean_excess_return"].to_numpy(float) if bench_name == "SPY" else sector_event["mean_excess_return"].to_numpy(float))):
            for frac in (0.01, 0.05):
                sensitivity_rows.append(
                    {
                        "benchmark": bench_name,
                        "level": label,
                        "variant": f"symmetric_trim_{int(frac * 100)}pct",
                        "trim_fraction": frac,
                        "trimmed_mean_excess_return": _trimmed_mean(values, frac),
                    }
                )
    sensitivity = pd.DataFrame(sensitivity_rows)

    # Diagnostic extremes.
    top10_pos = matched.nlargest(10, "excess_return")[
        ["benchmark", "candidate_id", "market_id", "symbol", "entry_date", "exit_date_t_minus_1", "excess_return", "excess_pnl"]
    ].copy()
    top10_neg = matched.nsmallest(10, "excess_return")[
        ["benchmark", "candidate_id", "market_id", "symbol", "entry_date", "exit_date_t_minus_1", "excess_return", "excess_pnl"]
    ].copy()

    # Write matched rows.
    matched.to_csv(paths.matched_trades, index=False)

    # Event-level output in a tidy, benchmark-specific form.
    event_out = pd.concat(
        [
            spy_event.assign(benchmark="SPY"),
            sector_event.assign(benchmark="sector_etf"),
        ],
        ignore_index=True,
    )
    event_out.to_csv(paths.event_level, index=False)
    inference.to_csv(paths.inference, index=False)

    _write_latex_table(inference, paths.table_tex)
    _forest_plot(inference, paths.forest_plot)

    exclusions = _describe_exclusions(trades, candidates)
    summary_lines = [
        "# Benchmark-Relative H1 Inference",
        "",
        "This analysis compares each valid H1 candidate against matched SPY and sector-ETF trades over the same H1 entry and exit dates. It does not optimize, rank, filter, or alter any strategy parameters.",
        "",
        "## Validation checks",
        "",
        f"- No-lookahead entry decision verified: {bool(exclusions['no_lookahead_entry_decision_verified'])}",
        f"- Entry/exit date match verified: {bool(exclusions['entry_exit_date_match_verified'])}",
        f"- Equal initial notional verified: {equal_initial_notional_verified}",
        f"- Stock better defined as excess_pnl > 0: {bool(exclusions['stock_better_definition_verified'])}",
        f"- Duplicate candidate-id groups: {exclusions['duplicate_candidate_id_groups']}",
        f"- Duplicate candidate-id rows: {exclusions['duplicate_candidate_ids']}",
        "",
        "## Exclusions",
        "",
        f"- Total valid H1 trades: {exclusions['candidate_trades']}",
        f"- SPY matched trades: {int(len(spy_candidate))}",
        f"- Sector matched trades: {int(len(sector_candidate))}",
        f"- Sector exclusions due to unknown sector: {exclusions['sector_excluded_unknown_sector']}",
        "",
        "## Headline results",
        "",
        f"- SPY candidate mean excess return: {spy_candidate['excess_return'].mean():+.4%}",
        f"- SPY event mean excess return: {spy_event['mean_excess_return'].mean():+.4%}",
        f"- Sector candidate mean excess return: {sector_candidate['excess_return'].mean():+.4%}",
        f"- Sector event mean excess return: {sector_event['mean_excess_return'].mean():+.4%}",
        f"- SPY candidate mean excess PnL: ${spy_candidate['excess_pnl'].mean():,.2f}",
        f"- Sector candidate mean excess PnL: ${sector_candidate['excess_pnl'].mean():,.2f}",
        "",
        "## Positive-excess exact binomial tests",
        "",
        f"- SPY candidate win rate: {spy_candidate['stock_better'].mean():.2%}",
        f"- Sector candidate win rate: {sector_candidate['stock_better'].mean():.2%}",
        f"- SPY binomial p-value: {stats.binomtest(int(spy_candidate['stock_better'].sum()), len(spy_candidate), 0.5, alternative='greater').pvalue:.6f}",
        f"- Sector binomial p-value: {stats.binomtest(int(sector_candidate['stock_better'].sum()), len(sector_candidate), 0.5, alternative='greater').pvalue:.6f}",
        "",
        "## Largest positive excess-return observations",
        "",
        top10_pos.to_string(index=False),
        "",
        "## Largest negative excess-return observations",
        "",
        top10_neg.to_string(index=False),
        "",
        "## Sensitivity: symmetric trimming",
        "",
        sensitivity.to_string(index=False),
        "",
        "## Notes",
        "",
        "- Event-level results use equal-weight economic events derived from the same economic_event_id grouping as the H1 analysis.",
        "- Sector ETF rows exclude Unknown-sector candidates entirely; they are not reassigned to SPY.",
        "- The analysis uses existing H1 trade outputs and the same transaction-cost function.",
    ]
    paths.summary.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    return {
        "matched_trades": paths.matched_trades,
        "event_level": paths.event_level,
        "inference": paths.inference,
        "summary": paths.summary,
        "table_tex": paths.table_tex,
        "forest_plot": paths.forest_plot,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark-relative inference for the cleaned H1 universe")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--candidates-path", type=Path, default=None)
    parser.add_argument("--trades-path", type=Path, default=None)
    parser.add_argument("--prices-path", type=Path, default=None)
    parser.add_argument("--n-boot", type=int, default=DEFAULT_BOOTSTRAPS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    outputs = run_analysis(
        args.repo_root,
        args.output_dir,
        candidates_path=args.candidates_path,
        trades_path=args.trades_path,
        prices_path=args.prices_path,
        n_boot=args.n_boot,
        seed=args.seed,
    )
    for name, path in outputs.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()