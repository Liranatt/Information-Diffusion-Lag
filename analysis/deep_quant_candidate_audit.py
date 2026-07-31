"""Candidate-level forensic audit of the timestamp-safe earnings research.

The repository has thousands of repeated replay and optimizer logs.  This
module works only from the canonical OOF candidate panels and legal-path
tables after ``deep_quant_audit`` has catalogued every CSV.  It produces a
compact, reproducible evidence pack answering three questions:

* did a pre-entry score separate good and bad earnings paths;
* which observed path states actually separated later outcomes; and
* whether the full Polymarket trajectory added signal beyond Target B.

All score/outcome relationships use out-of-fold scores.  The path-state
tables are descriptive only; they are inputs to the subsequent chronological
exit test, never a direct live rule.
"""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / "data" / "deep_quant_audit" / "candidate_forensics"

PREDICTIONS = PROJECT / "data" / "selection_stage2f" / "nested_oof_models" / "stage2f_oof_predictions.csv"
PATHS = PROJECT / "data" / "selection_stage2f" / "oracle_labels" / "candidate_legal_net_active_paths.csv"
TRAJECTORY_PREDICTIONS = PROJECT / "data" / "selection_stage2g" / "polymarket_rerun" / "nested_oof_models" / "stage2g_oof_predictions.csv"
STAGE2F_REPLAY = PROJECT / "data" / "selection_stage2f" / "exact_replay" / "selector_exit_combined_exact_results.csv"
STAGE2G_REPLAY = PROJECT / "data" / "selection_stage2g" / "polymarket_rerun" / "exact_replay" / "selector_exit_combined_exact_results.csv"
OPEN_PRICES = PROJECT / "assistant_generated_all_artifacts" / "assistant_generated_research_large_supplements" / "prices_open_merged.pkl"
CEM_CORRECTED = PROJECT / "assistant_generated_all_artifacts" / "assistant_generated_research_core" / "proper_execution_rerun"


def _qbin(values: pd.Series, n: int = 4) -> pd.Series:
    """Stable quantile labels that also retain ties and missingness."""
    result = pd.Series("missing", index=values.index, dtype="object")
    valid = values.notna()
    if valid.sum() < n:
        result.loc[valid] = "all"
        return result
    ranks = values.loc[valid].rank(method="first")
    result.loc[valid] = pd.qcut(ranks, n, labels=[f"Q{i}" for i in range(1, n + 1)]).astype(str)
    return result


def _summarize_outcomes(frame: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    return (
        frame.groupby(by, dropna=False, as_index=False)
        .agg(
            n=("stage2e_candidate_id", "size"),
            mean_terminal_active_pct=("terminal_net_active_return_pct", "mean"),
            median_terminal_active_pct=("terminal_net_active_return_pct", "median"),
            mean_best_legal_active_pct=("best_legal_net_active_return_pct", "mean"),
            median_best_legal_active_pct=("best_legal_net_active_return_pct", "median"),
            never_profitable_rate=("never_profitable_after_costs", "mean"),
            persistent_loser_rate=("persistent_loser", "mean"),
            reaches_2pct_rate=("reaches_2pct_active_net", "mean"),
            severe_adverse_rate=("severe_adverse_before_meaningful_gain", "mean"),
        )
        .sort_values(by)
    )


def _safe_spearman(x: pd.Series, y: pd.Series) -> float:
    keep = x.notna() & y.notna()
    return float(x.loc[keep].corr(y.loc[keep], method="spearman")) if keep.sum() >= 3 else np.nan


def _load_candidates() -> pd.DataFrame:
    candidates = pd.read_csv(PREDICTIONS)
    required = {
        "stage2e_candidate_id", "benchmark", "outer_fold",
        "oof_predicted_target_b_slot", "best_legal_net_active_return_pct",
        "terminal_net_active_return_pct", "never_profitable_after_costs",
        "persistent_loser", "reaches_2pct_active_net",
        "severe_adverse_before_meaningful_gain",
    }
    missing = required.difference(candidates.columns)
    if missing:
        raise ValueError(f"Stage 2F panel lacks required columns: {sorted(missing)}")
    candidates["entry_date"] = pd.to_datetime(candidates["entry_date"], utc=True)
    candidates["target_b_score_bin"] = candidates.groupby("benchmark")["oof_predicted_target_b_slot"].transform(_qbin)
    candidates["connection_bin"] = np.where(
        candidates["connection_strength"].ge(0.999), "direct_1.00",
        np.where(candidates["connection_strength"].notna(), "below_1.00", "missing"),
    )
    candidates["entry_probability_bin"] = pd.cut(
        candidates["entry_prob"], [-np.inf, 0.70, 0.80, 0.90, np.inf],
        labels=["<=0.70", "0.70-0.80", "0.80-0.90", ">0.90"], include_lowest=True,
    ).astype(str)
    for feature in ("stock_minus_sector_20d", "feat_asset_2w_trend", "feat_prob_at_trigger", "expected_slot_days"):
        candidates[f"{feature}_bin"] = candidates.groupby("benchmark")[feature].transform(_qbin)
    return candidates


def _candidate_tables(candidates: pd.DataFrame) -> None:
    candidate_columns = [
        "stage2e_candidate_id", "benchmark", "outer_fold", "entry_date", "symbol", "entry_prob",
        "connection_strength", "stock_minus_sector_20d", "feat_asset_2w_trend", "expected_slot_days",
        "oof_predicted_target_b_slot", "target_b_score_bin", "best_legal_net_active_return_pct",
        "terminal_net_active_return_pct", "active_mfe_net_pct", "active_mae_net_pct",
        "never_profitable_after_costs", "persistent_loser", "reaches_2pct_active_net",
        "severe_adverse_before_meaningful_gain", "outer_fold",
    ]
    candidates.loc[:, candidate_columns].to_csv(OUTPUT / "oof_candidate_outcomes.csv", index=False)
    _summarize_outcomes(candidates, ["benchmark", "target_b_score_bin"]).to_csv(
        OUTPUT / "target_b_score_bins.csv", index=False
    )
    _summarize_outcomes(candidates, ["benchmark", "outer_fold"]).to_csv(
        OUTPUT / "outcomes_by_fold.csv", index=False
    )

    bins = []
    for grouping in ("connection_bin", "entry_probability_bin", "stock_minus_sector_20d_bin", "feat_asset_2w_trend_bin", "expected_slot_days_bin"):
        table = _summarize_outcomes(candidates, ["benchmark", grouping])
        table.insert(1, "feature", grouping)
        table = table.rename(columns={grouping: "feature_bin"})
        bins.append(table)
    pd.concat(bins, ignore_index=True).to_csv(OUTPUT / "pre_entry_feature_bins.csv", index=False)

    score_columns = [
        "oof_predicted_target_b_slot", "pooled_opportunity_probability",
        "pooled_never_profitable_probability", "pooled_persistent_loss_probability",
    ]
    score_rows = []
    outcomes = {
        "terminal_active": "terminal_net_active_return_pct",
        "best_legal_active": "best_legal_net_active_return_pct",
        "never_profitable": "never_profitable_after_costs",
        "persistent_loser": "persistent_loser",
    }
    for benchmark, group in candidates.groupby("benchmark"):
        for score in score_columns:
            for label, outcome in outcomes.items():
                score_rows.append({
                    "benchmark": benchmark,
                    "score": score,
                    "outcome": label,
                    "spearman_oof": _safe_spearman(group[score], group[outcome]),
                    "n": int((group[score].notna() & group[outcome].notna()).sum()),
                })
    pd.DataFrame(score_rows).to_csv(OUTPUT / "stage2f_oof_score_associations.csv", index=False)


def _path_confirmation(candidates: pd.DataFrame) -> None:
    paths = pd.read_csv(PATHS)
    paths["path_date"] = pd.to_datetime(paths["path_date"], utc=True)
    paths = paths.sort_values(["stage2e_candidate_id", "benchmark", "legal_holding_day", "path_date"])
    keys = ["stage2e_candidate_id", "benchmark"]

    # At a live EOD decision on day d, cumulative return and high watermark
    # through day d are observable.  No later row enters any early-state cell.
    early = []
    for key, group in paths.groupby(keys, sort=False):
        row = {"stage2e_candidate_id": key[0], "benchmark": key[1]}
        for day in (1, 2, 3):
            known = group.loc[group["legal_holding_day"] <= day]
            current = group.loc[group["legal_holding_day"] == day]
            row[f"day{day}_active_close_pct"] = current["net_active_return_pct"].iloc[-1] if len(current) else np.nan
            row[f"day{day}_active_high_pct"] = known["active_high_return_pct"].max() if len(known) else np.nan
            row[f"day{day}_active_low_pct"] = known["active_low_return_pct"].min() if len(known) else np.nan
        early.append(row)
    early = pd.DataFrame(early)
    merged = candidates.merge(early, on=keys, how="left", validate="one_to_one")
    merged["day2_state"] = np.select(
        [
            merged["day2_active_high_pct"].isna(),
            merged["day2_active_high_pct"].ge(1.0),
            merged["day2_active_close_pct"].gt(0.0),
        ],
        ["no_day2", "hit_+1pct", "positive_not_+1pct"],
        default="no_positive_confirmation",
    )
    merged["day2_close_state"] = pd.cut(
        merged["day2_active_close_pct"], [-np.inf, -2.0, 0.0, 1.0, np.inf],
        labels=["<=-2pct", "-2_to_0", "0_to_+1", ">+1pct"], include_lowest=True,
    ).astype(str)
    column_order = keys + [
        "outer_fold", "entry_date", "symbol", "oof_predicted_target_b_slot",
        "day1_active_close_pct", "day1_active_high_pct", "day1_active_low_pct",
        "day2_active_close_pct", "day2_active_high_pct", "day2_active_low_pct",
        "day3_active_close_pct", "day3_active_high_pct", "day3_active_low_pct",
        "day2_state", "day2_close_state", "best_legal_net_active_return_pct",
        "terminal_net_active_return_pct", "never_profitable_after_costs", "persistent_loser",
        "reaches_2pct_active_net", "severe_adverse_before_meaningful_gain",
    ]
    merged.loc[:, column_order].to_csv(OUTPUT / "candidate_early_path_states.csv", index=False)
    _summarize_outcomes(merged, ["benchmark", "day2_state"]).to_csv(OUTPUT / "day2_state_outcomes.csv", index=False)
    _summarize_outcomes(merged, ["benchmark", "day2_close_state"]).to_csv(OUTPUT / "day2_close_state_outcomes.csv", index=False)

    associations = []
    for benchmark, group in merged.groupby("benchmark"):
        for state in ("day1_active_close_pct", "day1_active_high_pct", "day2_active_close_pct", "day2_active_high_pct", "day3_active_close_pct"):
            for label, outcome in {
                "terminal_active": "terminal_net_active_return_pct",
                "best_legal_active": "best_legal_net_active_return_pct",
                "never_profitable": "never_profitable_after_costs",
                "persistent_loser": "persistent_loser",
            }.items():
                associations.append({
                    "benchmark": benchmark, "early_state": state, "outcome": label,
                    "spearman": _safe_spearman(group[state], group[outcome]),
                    "n": int((group[state].notna() & group[outcome].notna()).sum()),
                })
    pd.DataFrame(associations).to_csv(OUTPUT / "early_state_associations.csv", index=False)


def _trajectory_comparison() -> None:
    traj = pd.read_csv(TRAJECTORY_PREDICTIONS)
    rows = []
    outcome_map = {
        "terminal_active": "terminal_net_active_return_pct",
        "best_legal_active": "best_legal_net_active_return_pct",
        "never_profitable": "never_profitable_after_costs",
        "persistent_loser": "persistent_loser",
    }
    for benchmark, group in traj.groupby("benchmark"):
        for score in ("oof_predicted_target_b_slot", "A_opportunity_probability", "B_opportunity_probability", "C_opportunity_probability"):
            for label, outcome in outcome_map.items():
                rows.append({
                    "benchmark": benchmark, "model": score, "outcome": label,
                    "spearman_oof": _safe_spearman(group[score], group[outcome]),
                    "n": int((group[score].notna() & group[outcome].notna()).sum()),
                })
    pd.DataFrame(rows).to_csv(OUTPUT / "trajectory_oof_associations.csv", index=False)

    replay_rows = []
    for source, path in (("stage2f_base", STAGE2F_REPLAY), ("stage2g_trajectory", STAGE2G_REPLAY)):
        frame = pd.read_csv(path)
        keep = [column for column in (
            "evaluation_scope", "outer_fold", "benchmark", "selector", "exit_policy",
            "excess_return", "max_dd", "n_trades", "win_rate", "total_return", "benchmark_return",
        ) if column in frame]
        result = frame.loc[:, keep].copy()
        result.insert(0, "source", source)
        replay_rows.append(result)
    pd.concat(replay_rows, ignore_index=True).to_csv(OUTPUT / "stage2f_vs_stage2g_replay_evidence.csv", index=False)


def _corrected_cem_transfer_diagnostic() -> None:
    """Check a path-state mechanism on corrected CEM OOS paths only.

    This does not reuse a CEM strategy result, a CEM exit, or CEM P&L.  It is
    a cross-period descriptive check: after a logged OOS earnings entry, does
    the first available relative-close state relate to the legal pre-event
    endpoint in the same direction as the Stage 2F path audit?  It is retained
    as a transfer diagnostic, never a portfolio-performance claim.
    """
    raw = pickle.loads(OPEN_PRICES.read_bytes())
    prices: dict[str, pd.DataFrame] = {}
    for symbol, records in raw.items():
        valid = [record for record in records if len(record) >= 5]
        if not valid:
            continue
        frame = pd.DataFrame(valid, columns=["date", "open", "high", "low", "close"])
        frame["date"] = pd.to_datetime(frame["date"], utc=True).dt.normalize()
        prices[str(symbol)] = frame.set_index("date").sort_index()
    rows = []
    for benchmark in ("SPY", "QQQ"):
        trades = pd.read_csv(CEM_CORRECTED / f"{benchmark.lower()}_proper_retrained_oos_trades.csv")
        earnings = trades[trades["question"].str.contains("earning|eps|quarterly", case=False, na=False)]
        for trade in earnings.itertuples(index=False):
            symbol = str(trade.symbol)
            if symbol not in prices or benchmark not in prices:
                continue
            entry_date = pd.Timestamp(trade.entry_date, tz="UTC").normalize()
            legal_te = pd.Timestamp(trade.candidate_t_e, tz="UTC").normalize()
            stock = prices[symbol].loc[(prices[symbol].index >= entry_date) & (prices[symbol].index < legal_te)]
            bench = prices[benchmark].loc[(prices[benchmark].index >= entry_date) & (prices[benchmark].index < legal_te)]
            dates = stock.index.intersection(bench.index)
            if len(dates) < 3 or float(trade.entry_price) <= 0.0:
                continue
            stock, bench = stock.loc[dates], bench.loc[dates]
            benchmark_entry_close = float(bench.iloc[0]["close"])
            early_active = 100.0 * (
                float(stock.iloc[1]["close"]) / float(trade.entry_price) - 1.0
                - (float(bench.iloc[1]["close"]) / benchmark_entry_close - 1.0)
            )
            terminal_active = 100.0 * (
                float(stock.iloc[-1]["close"]) / float(trade.entry_price) - 1.0
                - (float(bench.iloc[-1]["close"]) / benchmark_entry_close - 1.0)
            )
            rows.append({
                "benchmark": benchmark,
                "symbol": symbol,
                "entry_date": entry_date,
                "legal_t_e": legal_te,
                "legal_sessions": int(len(dates)),
                "early_relative_close_pct": early_active,
                "terminal_relative_close_pct": terminal_active,
            })
    detail = pd.DataFrame(rows)
    detail["early_relative_bin"] = pd.cut(
        detail["early_relative_close_pct"], [-np.inf, -2.0, 0.0, 1.0, np.inf],
        labels=["<=-2pct", "-2_to_0", "0_to_+1", ">+1pct"], include_lowest=True,
    ).astype(str)
    summary = (
        detail.groupby(["benchmark", "early_relative_bin"], as_index=False)
        .agg(n=("symbol", "size"), mean_terminal_relative_pct=("terminal_relative_close_pct", "mean"), median_terminal_relative_pct=("terminal_relative_close_pct", "median"))
    )
    correlations = (
        detail.groupby("benchmark", as_index=False)
        .apply(lambda group: group["early_relative_close_pct"].corr(group["terminal_relative_close_pct"], method="spearman"), include_groups=False)
        .rename(columns={None: "spearman_early_vs_terminal"})
    )
    detail.to_csv(OUTPUT / "corrected_cem_oos_relative_state_transfer_detail.csv", index=False)
    summary.merge(correlations, on="benchmark", how="left").to_csv(
        OUTPUT / "corrected_cem_oos_relative_state_transfer.csv", index=False
    )


def run() -> dict:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    candidates = _load_candidates()
    _candidate_tables(candidates)
    _path_confirmation(candidates)
    _trajectory_comparison()
    _corrected_cem_transfer_diagnostic()
    return {
        "candidates": int(len(candidates)),
        "benchmarks": sorted(candidates["benchmark"].dropna().unique().tolist()),
        "outputs": sorted(path.name for path in OUTPUT.glob("*.csv")),
    }


if __name__ == "__main__":
    print(run())
