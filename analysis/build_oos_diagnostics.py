from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT / "data"
OUTPUT_DIR = PROJECT / "output"

RESULTS_CSV = DATA_DIR / "experiment_results_clean.csv"
WF_FOLDS_CSV = DATA_DIR / "experiment_walkforward_folds_clean.csv"
TRADE_DIR = DATA_DIR / "experiment_trade_logs_clean"
EQUITY_DIR = DATA_DIR / "experiment_equity_logs_clean"
FORENSIC_DIR = DATA_DIR / "experiment_forensics_clean"

TRADE_DIAGNOSTICS_CSV = OUTPUT_DIR / "trade_diagnostics_oos.csv"
EQUITY_DIAGNOSTICS_CSV = OUTPUT_DIR / "equity_diagnostics_oos.csv"
EXPERIMENT_INDEX_CSV = OUTPUT_DIR / "experiment_index_oos.csv"

EXPERIMENT_IDS = {
    "Baseline": 0,
    "T1 FrictionPenalty": 1,
    "T2 TrainWindows": 2,
    "T3 Kelly": 3,
    "T1+T2": 4,
    "T1+T3": 5,
    "T2+T3": 6,
    "T1+T2+T3": 7,
    "T4 GeoPriority": 8,
    "T1+T2+T3+T4": 9,
}

REQUIRED_OOS_RUNS = {
    ("SPY", "T1+T2+T3+T4"),
    ("QQQ", "T1+T2+T3+T4"),
}

JOIN_FIELDS = [
    "market_id",
    "symbol",
    "entry_date",
    "exit_date",
    "entry_price",
    "exit_price",
    "_qty",
    "realized_exit_reason",
]

BASE_TRADE_COLUMNS = [
    "benchmark",
    "experiment_label",
    "experiment_id",
    "stage",
    "oos_flag",
    "source_trade_log",
    "source_forensic_log",
    "forensic_join_status",
    "drop_reason",
    "market_id",
    "symbol",
    "question",
    "archetype",
    "entry_date",
    "exit_date",
    "candidate_t_theta",
    "candidate_t_e",
    "entry_prob",
    "asset_confidence",
    "question_confidence",
    "feat_connection_strength",
    "relevance",
    "split",
    "exit_reason",
    "realized_exit_reason",
    "pnl",
    "pnl_pct",
    "gross_pnl",
    "txn_cost",
    "friction_fail",
    "benchmark_counterfactual_pnl",
    "asset_minus_benchmark_net_pnl",
    "index_better",
    "_position_size_pct",
    "invested_frac_pct",
    "_asset_entry_notional",
    "_equity_at_entry",
    "_qty",
    "_benchmark_sell_cost",
    "_asset_buy_cost",
    "exit_value",
    "benchmark_rebuy_qty",
]

FORENSIC_FEATURE_COLUMNS = [
    "trade_sequence_by_entry",
    "entry_month",
    "is_iran_question",
    "is_feb_mar_iran_question",
    "macro_context_label",
    "entry_delay_days_from_candidate",
    "days_to_resolution_at_entry",
    "trade_duration_calendar_days",
    "run_total_net_trade_pnl",
    "pnl_share_of_run_net_trade_pnl_pct",
    "cumulative_net_trade_pnl_by_entry",
    "run_start_date",
    "run_end_date",
    "run_start_equity",
    "run_end_equity",
    "run_max_drawdown_pct",
    "run_max_drawdown_date",
    "run_start_benchmark_equity",
    "run_end_benchmark_equity",
    "policy_atr_mult",
    "policy_lock_activate",
    "policy_theta_out",
    "policy_enter_strong",
    "policy_enter_floor",
    "policy_hold_days",
    "policy_max_prob_surge",
    "policy_max_price_runup",
    "policy_position_size_pct",
    "policy_max_concurrent",
    "candidate_event_id",
    "candidate_market_id",
    "candidate_symbol",
    "candidate_t0",
    "candidate_entry_price",
    "candidate_feat_archetype",
    "candidate_feat_sector",
    "candidate_feat_prob_at_trigger",
    "candidate_feat_prob_slope_24h",
    "candidate_feat_prob_volatility",
    "candidate_feat_prob_surge_since_t0",
    "candidate_feat_time_to_resolution_days",
    "candidate_feat_crossing_latency_days",
    "candidate_feat_pre_entry_volume_log",
    "candidate_feat_runup_since_t0",
    "candidate_feat_asset_2w_trend",
    "candidate_feat_sector_1m_trend",
    "candidate_feat_spy_2w_trend",
    "candidate_feat_ytd_change",
    "candidate_feat_debt_to_equity",
    "candidate_feat_cash_to_marketcap",
    "candidate_feat_beta",
    "candidate_feat_profit_margin",
    "candidate_feat_log_market_cap",
    "candidate_feat_connection_strength",
    "candidate_feat_world_size",
    "candidate_feat_runup_rank",
    "candidate_feat_size_rank",
    "candidate_asset_return",
    "candidate_expected_return_pct",
    "candidate_confidence_score",
    "candidate_feat_llm_expected_return",
    "candidate_feat_llm_confidence",
    "asset_bars_available",
    "asset_trade_bar_count",
    "asset_pre_5d_return_pct",
    "asset_pre_10d_return_pct",
    "asset_pre_20d_return_pct",
    "asset_post_exit_5d_return_pct",
    "asset_post_exit_10d_return_pct",
    "asset_entry_close",
    "asset_exit_close",
    "asset_entry_to_exit_close_return_pct",
    "asset_max_high_during_trade",
    "asset_max_high_date",
    "asset_min_low_during_trade",
    "asset_min_low_date",
    "asset_max_favorable_excursion_pct",
    "asset_max_adverse_excursion_pct",
    "asset_close_to_low_gap_pct",
    "benchmark_bars_available",
    "benchmark_trade_bar_count",
    "benchmark_pre_5d_return_pct",
    "benchmark_pre_10d_return_pct",
    "benchmark_pre_20d_return_pct",
    "benchmark_post_exit_5d_return_pct",
    "benchmark_post_exit_10d_return_pct",
    "benchmark_entry_close",
    "benchmark_exit_close",
    "benchmark_entry_to_exit_close_return_pct",
    "benchmark_max_high_during_trade",
    "benchmark_max_high_date",
    "benchmark_min_low_during_trade",
    "benchmark_min_low_date",
    "benchmark_max_favorable_excursion_pct",
    "benchmark_max_adverse_excursion_pct",
    "benchmark_close_to_low_gap_pct",
    "asset_minus_benchmark_close_return_pct",
    "prob_points_available",
    "prob_trade_point_count",
    "prob_at_candidate_day",
    "prob_at_entry_day",
    "prob_at_exit_day",
    "prob_min_during_trade",
    "prob_max_during_trade",
    "prob_entry_to_exit_change",
    "open_during_run_max_drawdown",
    "equity_at_trade_entry_or_next",
    "equity_at_trade_exit_or_prior",
    "equity_delta_during_trade",
    "worst_portfolio_drawdown_during_trade_pct",
    "worst_portfolio_drawdown_during_trade_date",
    "max_open_positions_during_trade",
    "avg_open_positions_during_trade",
    "benchmark_equity_delta_during_trade",
    "is_open_on_run_max_drawdown_date",
    "asset_close_on_run_max_drawdown_date",
    "asset_unrealized_return_on_run_max_drawdown_pct",
    "asset_unrealized_gross_pnl_on_run_max_drawdown",
    "benchmark_return_entry_to_run_max_drawdown_pct",
    "pnl_rank_worst_in_run",
    "pnl_rank_best_in_run",
    "negative_pnl_and_open_during_run_max_drawdown",
]


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def rel_path(path: Path) -> str:
    try:
        return path.relative_to(PROJECT).as_posix()
    except ValueError:
        return str(path)


def require_non_empty_file(path: Path) -> None:
    if not path.exists() or not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(f"Required file is missing or empty: {path}")


def require_non_empty_dir(path: Path) -> None:
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"Required directory is missing: {path}")
    if not any(path.glob("*.csv")):
        raise FileNotFoundError(f"Required directory contains no CSV files: {path}")


def read_csv(path: Path) -> pd.DataFrame:
    require_non_empty_file(path)
    return pd.read_csv(path)


def calc_adv_metrics(equity: pd.Series) -> dict[str, float]:
    values = pd.to_numeric(equity, errors="coerce").dropna()
    if len(values) < 2:
        return {"sharpe": 0.0, "sortino": 0.0}

    returns = values.pct_change().dropna()
    if returns.empty:
        return {"sharpe": 0.0, "sortino": 0.0}

    mean = returns.mean()
    std = returns.std()
    sharpe = mean / std * math.sqrt(252.0) if std > 1e-12 else 0.0

    downside = returns[returns < 0.0]
    downside_std = downside.std() if not downside.empty else 0.0
    sortino = mean / downside_std * math.sqrt(252.0) if downside_std > 1e-12 else 0.0
    return {"sharpe": float(sharpe), "sortino": float(sortino)}


def build_expected_paths(benchmark: str, experiment_label: str) -> dict[str, Path]:
    stem = f"{benchmark.lower()}_{slug(experiment_label)}_test"
    return {
        "trade": TRADE_DIR / f"{stem}.csv",
        "equity": EQUITY_DIR / f"{stem}.csv",
        "forensic": FORENSIC_DIR / f"{stem}_forensics.csv",
    }


def build_experiment_index(results: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []

    for _, result in results.iterrows():
        benchmark = str(result["benchmark"]).upper()
        label = str(result["experiment"])
        paths = build_expected_paths(benchmark, label)
        equity_sortino = np.nan
        benchmark_sortino = np.nan

        if paths["equity"].exists() and paths["equity"].stat().st_size > 0:
            equity_df = pd.read_csv(paths["equity"])
            equity_sortino = calc_adv_metrics(equity_df["equity"])["sortino"]
            benchmark_sortino = calc_adv_metrics(equity_df["benchmark_equity"])["sortino"]

        rows.append(
            {
                "benchmark": benchmark,
                "experiment_label": label,
                "experiment_id": EXPERIMENT_IDS.get(label, np.nan),
                "trade_log_path": rel_path(paths["trade"]),
                "equity_log_path": rel_path(paths["equity"]),
                "forensic_path": rel_path(paths["forensic"]) if paths["forensic"].exists() else "",
                "trade_log_exists": int(paths["trade"].exists() and paths["trade"].stat().st_size > 0),
                "equity_log_exists": int(paths["equity"].exists() and paths["equity"].stat().st_size > 0),
                "forensic_exists": int(paths["forensic"].exists() and paths["forensic"].stat().st_size > 0),
                "total_return": result.get("test_return_pct", np.nan),
                "benchmark_return": result.get("test_benchmark_return_pct", np.nan),
                "excess_return": result.get("test_excess_return_pct", np.nan),
                "sharpe": result.get("test_sharpe", np.nan),
                "sortino": round(float(equity_sortino), 6) if pd.notna(equity_sortino) else np.nan,
                "benchmark_sharpe": result.get("test_benchmark_sharpe", np.nan),
                "benchmark_sortino": round(float(benchmark_sortino), 6) if pd.notna(benchmark_sortino) else np.nan,
                "max_dd": result.get("test_max_dd_pct", np.nan),
                "n_trades": result.get("test_trades", np.nan),
            }
        )

    index_df = pd.DataFrame(rows)
    return index_df.sort_values(["experiment_id", "benchmark"], na_position="last").reset_index(drop=True)


def normalize_date_key(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce", utc=True)
    normalized = parsed.dt.strftime("%Y-%m-%d")
    return normalized.fillna(series.astype(str).str.slice(0, 10))


def normalize_number_key(series: pd.Series, decimals: int = 6) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce").round(decimals)
    return numeric.map(lambda x: "" if pd.isna(x) else f"{x:.{decimals}f}")


def normalize_text_key(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip()


def add_join_keys(df: pd.DataFrame) -> pd.DataFrame:
    keyed = df.copy()
    for field in JOIN_FIELDS:
        if field not in keyed.columns:
            keyed[field] = ""

    keyed["_join_market_id"] = normalize_text_key(keyed["market_id"])
    keyed["_join_symbol"] = normalize_text_key(keyed["symbol"]).str.upper()
    keyed["_join_entry_date"] = normalize_date_key(keyed["entry_date"])
    keyed["_join_exit_date"] = normalize_date_key(keyed["exit_date"])
    keyed["_join_entry_price"] = normalize_number_key(keyed["entry_price"])
    keyed["_join_exit_price"] = normalize_number_key(keyed["exit_price"])
    keyed["_join_qty"] = normalize_number_key(keyed["_qty"], decimals=0)
    keyed["_join_realized_exit_reason"] = normalize_text_key(keyed["realized_exit_reason"])

    join_cols = [col for col in keyed.columns if col.startswith("_join_")]
    keyed["_join_occurrence"] = keyed.groupby(join_cols, dropna=False).cumcount()
    keyed["_join_duplicate_count"] = keyed.groupby(join_cols, dropna=False)["_join_occurrence"].transform("count")
    return keyed


def merge_forensics(trades: pd.DataFrame, forensic_path: Path) -> pd.DataFrame:
    trades = add_join_keys(trades)
    trades["_source_forensic_file_exists"] = int(forensic_path.exists() and forensic_path.stat().st_size > 0)

    if not forensic_path.exists() or forensic_path.stat().st_size == 0:
        trades["source_forensic_log"] = ""
        trades["forensic_join_status"] = "missing_forensic_file"
        for column in FORENSIC_FEATURE_COLUMNS:
            if column not in trades.columns:
                trades[column] = np.nan
        return trades

    forensic = add_join_keys(pd.read_csv(forensic_path))
    join_cols = [col for col in trades.columns if col.startswith("_join_")]
    available_features = [col for col in FORENSIC_FEATURE_COLUMNS if col in forensic.columns]
    forensic_small = forensic[join_cols + available_features].copy()

    merged = trades.merge(
        forensic_small,
        on=join_cols,
        how="left",
        indicator=True,
        suffixes=("", "_forensic"),
    )
    merged["source_forensic_log"] = rel_path(forensic_path)
    merged["forensic_join_status"] = np.where(merged["_merge"].eq("both"), "matched", "unmatched_forensic")
    merged = merged.drop(columns=["_merge"])

    for column in FORENSIC_FEATURE_COLUMNS:
        if column not in merged.columns:
            merged[column] = np.nan
    return merged


def to_numeric(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index)
    return pd.to_numeric(df[column], errors="coerce")


def derive_trade_labels(df: pd.DataFrame) -> pd.DataFrame:
    diagnostics = df.copy()

    gross = to_numeric(diagnostics, "gross_pnl")
    txn_cost = to_numeric(diagnostics, "txn_cost")
    pnl = to_numeric(diagnostics, "pnl")
    entry_notional = to_numeric(diagnostics, "_asset_entry_notional")
    entry_equity = to_numeric(diagnostics, "_equity_at_entry")

    friction_fail = pd.Series(pd.NA, index=diagnostics.index, dtype="Int64")
    friction_mask = gross.notna() & txn_cost.notna()
    friction_fail.loc[friction_mask] = (
        gross.loc[friction_mask] < 3.0 * txn_cost.loc[friction_mask]
    ).astype(int)
    diagnostics["friction_fail"] = friction_fail

    benchmark_return = to_numeric(diagnostics, "benchmark_entry_to_exit_close_return_pct")
    benchmark_counterfactual = entry_notional * benchmark_return / 100.0

    missing_counterfactual = benchmark_counterfactual.isna()
    if "benchmark_equity_delta_during_trade" in diagnostics.columns:
        benchmark_equity_delta = to_numeric(diagnostics, "benchmark_equity_delta_during_trade")
        scale = entry_notional / entry_equity.replace(0.0, np.nan)
        benchmark_counterfactual = benchmark_counterfactual.where(
            ~missing_counterfactual,
            benchmark_equity_delta * scale,
        )

    diagnostics["benchmark_counterfactual_pnl"] = benchmark_counterfactual.round(6)
    diagnostics["asset_minus_benchmark_net_pnl"] = (pnl - benchmark_counterfactual).round(6)
    index_better = pd.Series(pd.NA, index=diagnostics.index, dtype="Int64")
    index_mask = benchmark_counterfactual.notna() & pnl.notna()
    index_better.loc[index_mask] = (
        benchmark_counterfactual.loc[index_mask] > pnl.loc[index_mask]
    ).astype(int)
    diagnostics["index_better"] = index_better

    if "candidate_feat_connection_strength" in diagnostics.columns:
        diagnostics["feat_connection_strength"] = diagnostics["candidate_feat_connection_strength"]
    elif "feat_connection_strength" not in diagnostics.columns:
        diagnostics["feat_connection_strength"] = diagnostics.get("relevance", np.nan)

    reasons: list[str] = []
    for _, row in diagnostics.iterrows():
        row_reasons = []
        if int(row.get("oos_flag", 0) or 0) != 1:
            row_reasons.append("non_oos_split")
        if row.get("forensic_join_status") != "matched":
            row_reasons.append(str(row.get("forensic_join_status")))
        if pd.isna(row.get("benchmark_counterfactual_pnl")):
            row_reasons.append("missing_benchmark_counterfactual")
        reasons.append(";".join(reason for reason in row_reasons if reason))
    diagnostics["drop_reason"] = reasons

    return diagnostics


def build_trade_diagnostics(index_df: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []

    for _, run in index_df.iterrows():
        trade_path = PROJECT / str(run["trade_log_path"])
        if not trade_path.exists() or trade_path.stat().st_size == 0:
            continue

        trades = pd.read_csv(trade_path)
        benchmark = str(run["benchmark"]).upper()
        label = str(run["experiment_label"])
        forensic_path = PROJECT / str(run["forensic_path"]) if str(run["forensic_path"]) else Path("")

        trades["benchmark"] = benchmark
        trades["experiment_label"] = label
        trades["experiment_id"] = run["experiment_id"]
        trades["stage"] = "test"
        trades["source_trade_log"] = rel_path(trade_path)

        split = trades["split"].astype(str).str.lower().str.strip() if "split" in trades.columns else "test"
        trades["oos_flag"] = np.where(split.eq("test"), 1, 0)

        merged = merge_forensics(trades, forensic_path)
        merged = derive_trade_labels(merged)
        frames.append(merged)

    if not frames:
        return pd.DataFrame(columns=BASE_TRADE_COLUMNS + FORENSIC_FEATURE_COLUMNS)

    diagnostics = pd.concat(frames, ignore_index=True, sort=False)
    diagnostics = diagnostics.loc[diagnostics["oos_flag"].eq(1)].copy()

    for column in BASE_TRADE_COLUMNS + FORENSIC_FEATURE_COLUMNS:
        if column not in diagnostics.columns:
            diagnostics[column] = np.nan

    cleanup_cols = [col for col in diagnostics.columns if col.startswith("_join_")]
    diagnostics = diagnostics.drop(columns=cleanup_cols, errors="ignore")

    ordered = BASE_TRADE_COLUMNS + [col for col in FORENSIC_FEATURE_COLUMNS if col not in BASE_TRADE_COLUMNS]
    remaining = [col for col in diagnostics.columns if col not in ordered]
    return diagnostics[ordered + remaining].sort_values(
        ["experiment_id", "benchmark", "entry_date", "exit_date", "symbol", "market_id"],
        kind="mergesort",
    )


def build_equity_diagnostics(index_df: pd.DataFrame, results: pd.DataFrame) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    result_lookup = {
        (str(row["benchmark"]).upper(), str(row["experiment"])): row
        for _, row in results.iterrows()
    }

    for _, run in index_df.iterrows():
        equity_path = PROJECT / str(run["equity_log_path"])
        if not equity_path.exists() or equity_path.stat().st_size == 0:
            continue

        benchmark = str(run["benchmark"]).upper()
        label = str(run["experiment_label"])
        experiment_id = run["experiment_id"]
        equity_df = pd.read_csv(equity_path)

        daily = equity_df.copy()
        daily.insert(0, "row_type", "daily")
        daily.insert(0, "experiment_id", experiment_id)
        daily.insert(0, "experiment_label", label)
        daily.insert(0, "benchmark", benchmark)

        adv = calc_adv_metrics(daily["equity"])
        benchmark_adv = calc_adv_metrics(daily["benchmark_equity"])
        result = result_lookup.get((benchmark, label))

        summary = {
            "benchmark": benchmark,
            "experiment_label": label,
            "experiment_id": experiment_id,
            "row_type": "summary",
            "date": "",
            "equity": np.nan,
            "benchmark_equity": np.nan,
            "cash": np.nan,
            "benchmark_shares": np.nan,
            "open_positions": np.nan,
            "total_return_pct": result.get("test_return_pct", np.nan) if result is not None else np.nan,
            "benchmark_return_pct": result.get("test_benchmark_return_pct", np.nan) if result is not None else np.nan,
            "excess_return_pct": result.get("test_excess_return_pct", np.nan) if result is not None else np.nan,
            "max_dd_pct": result.get("test_max_dd_pct", np.nan) if result is not None else np.nan,
            "sharpe": adv["sharpe"],
            "sortino": adv["sortino"],
            "benchmark_sharpe": benchmark_adv["sharpe"],
            "benchmark_sortino": benchmark_adv["sortino"],
            "n_trades": result.get("test_trades", np.nan) if result is not None else np.nan,
            "source_equity_log": rel_path(equity_path),
        }

        for column in [
            "total_return_pct",
            "benchmark_return_pct",
            "excess_return_pct",
            "max_dd_pct",
            "sharpe",
            "sortino",
            "benchmark_sharpe",
            "benchmark_sortino",
            "n_trades",
            "source_equity_log",
        ]:
            daily[column] = np.nan
        daily["source_equity_log"] = rel_path(equity_path)

        rows.append(pd.concat([daily, pd.DataFrame([summary])], ignore_index=True, sort=False))

    if not rows:
        return pd.DataFrame()

    diagnostics = pd.concat(rows, ignore_index=True, sort=False)
    ordered = [
        "benchmark",
        "experiment_label",
        "experiment_id",
        "row_type",
        "date",
        "equity",
        "benchmark_equity",
        "open_positions",
        "cash",
        "benchmark_shares",
        "total_return_pct",
        "benchmark_return_pct",
        "excess_return_pct",
        "max_dd_pct",
        "sharpe",
        "sortino",
        "benchmark_sharpe",
        "benchmark_sortino",
        "n_trades",
        "source_equity_log",
    ]
    for column in ordered:
        if column not in diagnostics.columns:
            diagnostics[column] = np.nan
    remaining = [col for col in diagnostics.columns if col not in ordered]
    return diagnostics[ordered + remaining].sort_values(
        ["experiment_id", "benchmark", "row_type", "date"],
        kind="mergesort",
    )


def validate_required_runs(index_df: pd.DataFrame) -> None:
    present = {
        (str(row["benchmark"]).upper(), str(row["experiment_label"]))
        for _, row in index_df.iterrows()
        if int(row["trade_log_exists"]) == 1 and int(row["equity_log_exists"]) == 1
    }
    missing = sorted(REQUIRED_OOS_RUNS - present)
    if missing:
        raise FileNotFoundError(f"Missing required OOS run outputs: {missing}")


def validate_clean_outputs() -> None:
    require_non_empty_file(RESULTS_CSV)
    require_non_empty_file(WF_FOLDS_CSV)
    require_non_empty_dir(TRADE_DIR)
    require_non_empty_dir(EQUITY_DIR)
    require_non_empty_dir(FORENSIC_DIR)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def main() -> None:
    validate_clean_outputs()
    results = read_csv(RESULTS_CSV)

    index_df = build_experiment_index(results)
    validate_required_runs(index_df)

    trade_diagnostics = build_trade_diagnostics(index_df)
    equity_diagnostics = build_equity_diagnostics(index_df, results)

    write_csv(trade_diagnostics, TRADE_DIAGNOSTICS_CSV)
    write_csv(equity_diagnostics, EQUITY_DIAGNOSTICS_CSV)
    write_csv(index_df, EXPERIMENT_INDEX_CSV)

    print(f"Wrote {len(trade_diagnostics):,} rows to {rel_path(TRADE_DIAGNOSTICS_CSV)}")
    print(f"Wrote {len(equity_diagnostics):,} rows to {rel_path(EQUITY_DIAGNOSTICS_CSV)}")
    print(f"Wrote {len(index_df):,} rows to {rel_path(EXPERIMENT_INDEX_CSV)}")


if __name__ == "__main__":
    main()
