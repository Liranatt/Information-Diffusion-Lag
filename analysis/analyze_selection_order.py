from __future__ import annotations

import json
import pickle
import re
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtesting.optimize_cem import _close_on, as_utc_day, truncate_paths
from backtesting.pipeline.strategy import simulate_one


PROJECT = Path(__file__).resolve().parent.parent
DATA = PROJECT / "data"
OUTPUT = PROJECT / "output"

RESULTS_CSV = DATA / "experiment_results_clean.csv"
WF_FOLDS_CSV = DATA / "experiment_walkforward_folds_clean.csv"
CANDIDATES = DATA / "candidates.parquet"
PRICES = DATA / "prices.pkl"
PROBS = DATA / "probs.pkl"
TRADE_DIR = DATA / "experiment_trade_logs_clean"

RUNS_TO_AUDIT = [
    ("SPY", "T1+T2+T3+T4"),
    ("QQQ", "T1+T2+T3+T4"),
    ("SPY", "T4 GeoPriority"),
]

JOIN_COLS = [
    "market_id",
    "symbol",
    "entry_date",
    "exit_date",
    "entry_price",
    "exit_price",
    "exit_reason",
]


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def norm_date(value: Any) -> str:
    return str(as_utc_day(value).date())


def norm_num(value: Any, decimals: int = 6) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(number):
        return ""
    return f"{number:.{decimals}f}"


def add_join_keys(df: pd.DataFrame) -> pd.DataFrame:
    keyed = df.copy()
    for col in JOIN_COLS:
        if col not in keyed.columns:
            keyed[col] = ""
    keyed["_join_market_id"] = keyed["market_id"].astype(str).str.strip()
    keyed["_join_symbol"] = keyed["symbol"].astype(str).str.upper().str.strip()
    keyed["_join_entry_date"] = keyed["entry_date"].map(norm_date)
    keyed["_join_exit_date"] = keyed["exit_date"].map(norm_date)
    keyed["_join_entry_price"] = keyed["entry_price"].map(norm_num)
    keyed["_join_exit_price"] = keyed["exit_price"].map(norm_num)
    keyed["_join_exit_reason"] = keyed["exit_reason"].astype(str).str.strip()
    join_cols = [c for c in keyed.columns if c.startswith("_join_")]
    keyed["_join_occurrence"] = keyed.groupby(join_cols, dropna=False).cumcount()
    return keyed


def load_pickle(path: Path) -> Any:
    with path.open("rb") as handle:
        return pickle.load(handle)


def build_policy(
    results: pd.DataFrame,
    folds: pd.DataFrame,
    benchmark: str,
    label: str,
) -> Callable[[pd.Timestamp], dict]:
    result_row = results[
        results["benchmark"].astype(str).str.upper().eq(benchmark)
        & results["experiment"].astype(str).eq(label)
    ]
    if result_row.empty:
        raise ValueError(f"No result row for {benchmark} {label}")
    result = result_row.iloc[0]

    if not bool(result.get("train_windows", False)):
        policy = json.loads(result["policy_json"])
        return lambda _day: policy

    fold_rows = folds[
        folds["benchmark"].astype(str).str.upper().eq(benchmark)
        & folds["experiment"].astype(str).eq(label)
    ].copy()
    if fold_rows.empty:
        policy = json.loads(result["policy_json"])
        return lambda _day: policy

    fold_rows["eval_start_ts"] = pd.to_datetime(fold_rows["eval_start_date"], utc=True)
    fold_rows["eval_end_exclusive_ts"] = (
        pd.to_datetime(fold_rows["eval_end_date"], utc=True) + pd.Timedelta(days=1)
    )
    fold_rows = fold_rows.sort_values("eval_start_ts")
    policies = [
        (
            as_utc_day(row["eval_start_ts"]),
            as_utc_day(row["eval_end_exclusive_ts"]),
            json.loads(row["eval_policy_json"]),
        )
        for _, row in fold_rows.iterrows()
    ]

    def dynamic_policy(day: pd.Timestamp) -> dict:
        day = as_utc_day(day)
        matched = policies[0][2]
        for start, end_exclusive, policy in policies:
            if start <= day < end_exclusive:
                return policy
            if day >= start:
                matched = policy
        return matched

    return dynamic_policy


def benchmark_return_pct(prices: dict, benchmark: str, entry_date: Any, exit_date: Any) -> float:
    entry = _close_on(prices, benchmark, entry_date)
    exit_ = _close_on(prices, benchmark, exit_date)
    if entry is None or exit_ is None or entry <= 0:
        return float("nan")
    return (float(exit_) / float(entry) - 1.0) * 100.0


def generate_opportunities(
    candidates: pd.DataFrame,
    prices: dict,
    probs: dict,
    policy_for_day: Callable[[pd.Timestamp], dict],
    benchmark: str,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for candidate_order, (_, row) in enumerate(candidates.sort_values("t_theta").iterrows(), start=1):
        candidate_theta = as_utc_day(row["t_theta"])
        policy = policy_for_day(candidate_theta)
        trade = simulate_one(row, prices, probs, policy)
        if trade is None:
            continue
        trade = dict(trade)
        bench_ret = benchmark_return_pct(prices, benchmark, trade["entry_date"], trade["exit_date"])
        trade.update(
            {
                "candidate_order_by_t_theta": candidate_order,
                "candidate_t_theta": str(candidate_theta.date()),
                "candidate_t_e": str(as_utc_day(row["t_e"]).date()),
                "expected_return_pct": row.get("expected_return_pct", np.nan),
                "confidence_score": row.get("confidence_score", np.nan),
                "feat_llm_expected_return": row.get("feat_llm_expected_return", np.nan),
                "feat_llm_confidence": row.get("feat_llm_confidence", np.nan),
                "feat_connection_strength": row.get("feat_connection_strength", np.nan),
                "feat_prob_volatility": row.get("feat_prob_volatility", np.nan),
                "feat_prob_surge_since_t0": row.get("feat_prob_surge_since_t0", np.nan),
                "feat_runup_since_t0": row.get("feat_runup_since_t0", np.nan),
                "feat_sector": row.get("feat_sector", ""),
                "benchmark_entry_to_exit_return_pct": bench_ret,
                "asset_minus_benchmark_return_pct": float(trade["return_pct"]) - bench_ret,
            }
        )
        rows.append(trade)

    if not rows:
        return pd.DataFrame()

    opportunities = pd.DataFrame(rows)
    opportunities["_entry_ts"] = pd.to_datetime(opportunities["entry_date"], utc=True)
    opportunities = opportunities.sort_values(
        ["_entry_ts", "candidate_order_by_t_theta"],
        kind="mergesort",
    ).reset_index(drop=True)
    opportunities["opportunity_order_by_entry"] = np.arange(1, len(opportunities) + 1)
    opportunities["same_entry_day_order"] = (
        opportunities.groupby("entry_date").cumcount() + 1
    )
    opportunities["same_entry_day_count"] = opportunities.groupby("entry_date")["symbol"].transform("size")
    opportunities["same_entry_day_return_rank"] = opportunities.groupby("entry_date")[
        "return_pct"
    ].rank(method="min", ascending=False)
    opportunities["same_entry_day_excess_rank"] = opportunities.groupby("entry_date")[
        "asset_minus_benchmark_return_pct"
    ].rank(method="min", ascending=False)
    return opportunities


def mark_selected(opportunities: pd.DataFrame, selected_path: Path) -> pd.DataFrame:
    selected = pd.read_csv(selected_path)
    selected = add_join_keys(selected)
    opportunities = add_join_keys(opportunities)
    join_cols = [c for c in opportunities.columns if c.startswith("_join_")]
    selected_keys = selected[join_cols].copy()
    selected_keys["selected"] = 1
    selected_keys["selected_pnl"] = selected["pnl"]
    selected_keys["selected_pnl_pct"] = selected["pnl_pct"]
    selected_keys["selected_position_size_pct"] = selected["_position_size_pct"]

    marked = opportunities.merge(selected_keys, on=join_cols, how="left")
    marked["selected"] = marked["selected"].fillna(0).astype(int)
    return marked.drop(columns=join_cols, errors="ignore")


def summarize_run(marked: pd.DataFrame, benchmark: str, label: str) -> dict[str, Any]:
    selected = marked[marked["selected"].eq(1)]
    skipped = marked[marked["selected"].eq(0)]
    selected_days = selected.groupby("entry_date")["return_pct"].mean()

    skipped_vs_day_avg = 0
    if not skipped.empty:
        skipped_vs_day_avg = int(
            skipped.apply(
                lambda row: row["return_pct"] > selected_days.get(row["entry_date"], np.inf),
                axis=1,
            ).sum()
        )

    return {
        "benchmark": benchmark,
        "experiment_label": label,
        "generated_opportunities": int(len(marked)),
        "selected_trades": int(len(selected)),
        "skipped_opportunities": int(len(skipped)),
        "selected_avg_return_pct": float(selected["return_pct"].mean()) if len(selected) else np.nan,
        "skipped_avg_return_pct": float(skipped["return_pct"].mean()) if len(skipped) else np.nan,
        "selected_avg_excess_pct": float(selected["asset_minus_benchmark_return_pct"].mean()) if len(selected) else np.nan,
        "skipped_avg_excess_pct": float(skipped["asset_minus_benchmark_return_pct"].mean()) if len(skipped) else np.nan,
        "selected_positive_excess": int((selected["asset_minus_benchmark_return_pct"] > 0).sum()),
        "skipped_positive_excess": int((skipped["asset_minus_benchmark_return_pct"] > 0).sum()),
        "selected_negative_excess": int((selected["asset_minus_benchmark_return_pct"] < 0).sum()),
        "skipped_better_than_selected_day_avg": skipped_vs_day_avg,
        "entry_days_with_more_opportunities_than_selected": int(
            (
                marked.groupby("entry_date")["selected"].agg(["size", "sum"])
                .assign(extra=lambda x: x["size"] > x["sum"])
                ["extra"]
                .sum()
            )
        ),
        "selected_avg_same_day_return_rank": float(selected["same_entry_day_return_rank"].mean()) if len(selected) else np.nan,
        "selected_avg_same_day_excess_rank": float(selected["same_entry_day_excess_rank"].mean()) if len(selected) else np.nan,
        "best_skipped_return_pct": float(skipped["return_pct"].max()) if len(skipped) else np.nan,
        "best_skipped_excess_pct": float(skipped["asset_minus_benchmark_return_pct"].max()) if len(skipped) else np.nan,
    }


def main() -> None:
    candidates = pd.read_parquet(CANDIDATES)
    candidates = candidates[candidates["feat_connection_strength"].astype(float) > 0.5].copy()
    candidates["split"] = candidates["split"].astype(str).str.lower().str.strip().replace({"val": "test"})
    candidates["t_theta"] = pd.to_datetime(candidates["t_theta"], utc=True)
    candidates["t_e"] = pd.to_datetime(candidates["t_e"], utc=True)

    oos_start = pd.Timestamp("2026-01-01", tz="UTC")
    oos_end = as_utc_day(candidates["t_theta"].max())
    oos_candidates = candidates[
        candidates["t_theta"].ge(oos_start) & candidates["t_theta"].le(oos_end)
    ].copy()

    prices = load_pickle(PRICES)
    probs = load_pickle(PROBS)
    sim_prices, sim_probs = truncate_paths(prices, probs, oos_end)
    results = pd.read_csv(RESULTS_CSV)
    folds = pd.read_csv(WF_FOLDS_CSV)

    all_marked: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []

    for benchmark, label in RUNS_TO_AUDIT:
        policy = build_policy(results, folds, benchmark, label)
        opportunities = generate_opportunities(oos_candidates, sim_prices, sim_probs, policy, benchmark)
        selected_path = TRADE_DIR / f"{benchmark.lower()}_{slug(label)}_test.csv"
        marked = mark_selected(opportunities, selected_path)
        marked.insert(0, "experiment_label", label)
        marked.insert(0, "benchmark", benchmark)
        all_marked.append(marked)
        summaries.append(summarize_run(marked, benchmark, label))

    audit = pd.concat(all_marked, ignore_index=True, sort=False)
    summary = pd.DataFrame(summaries)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    audit.to_csv(OUTPUT / "selection_order_opportunity_audit.csv", index=False)
    summary.to_csv(OUTPUT / "selection_order_summary.csv", index=False)
    print(summary.to_string(index=False))
    print(f"\nWrote {len(audit):,} opportunity rows to output/selection_order_opportunity_audit.csv")
    print(f"Wrote {len(summary):,} summary rows to output/selection_order_summary.csv")


if __name__ == "__main__":
    main()
