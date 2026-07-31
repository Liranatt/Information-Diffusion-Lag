"""Final ICAIF CEM configuration-matrix report.

Reads the fresh audit-clean seed-42 6x20 matrix (runs/final_icaif_matrix_seed42_6x20),
recomputes return / Sharpe / MaxDD from the equity logs, verifies they match the
result CSV, and writes:
  cem_matrix_final.csv          - one row per configuration x benchmark with both
                                  official and recomputed metrics
  cem_matrix_verification.md    - match report
Also runs the HAC + 5-day moving-block bootstrap inference for the flagship arm
(the analysis previously in analysis/paper_updated_statistics.py) against
  (a) this matrix's T1+T2+T3+T4 equity logs (audit-clean universe), and
  (b) runs/paper_legacy_key_arms (original universe, identical code/seed/budget)
and writes cem_oos_block_inference_final.csv + cem_cleaning_delta_final.csv.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(r"C:\Users\Liran\PycharmProjects\cem_clean_repo")
MATRIX = ROOT / "runs" / "final_icaif_matrix_seed42_6x20"
LEGACY = ROOT / "runs" / "paper_legacy_key_arms"
OUT = Path(__file__).resolve().parent

N_BOOT = 20_000
BLOCK = 5
SEED = 42

SLUGS = {
    "Baseline": "baseline",
    "T1+T2": "t1_t2",
    "T1+T2+T3": "t1_t2_t3",
    "T4 GeoPriority": "t4_geopriority",
    "T1+T2+T3+T4": "t1_t2_t3_t4",
}


INITIAL_CAPITAL = 100_000.0


def recompute(equity_path: Path) -> dict:
    # The official total_return divides terminal equity by the $100k initial
    # capital (which includes the initial benchmark purchase cost); the equity
    # log's first row is post-cost, so returns are recomputed against the
    # constant initial, matching the simulator's definition.
    eq = pd.read_csv(equity_path, parse_dates=["date"]).sort_values("date")
    equity = eq["equity"].astype(float).to_numpy()
    bench = eq["benchmark_equity"].astype(float).to_numpy()
    daily = pd.Series(equity).pct_change().dropna()
    peaks = np.maximum.accumulate(equity)
    return {
        "recomputed_return_pct": (equity[-1] / INITIAL_CAPITAL - 1.0) * 100.0,
        "recomputed_benchmark_return_pct": (bench[-1] / INITIAL_CAPITAL - 1.0) * 100.0,
        "recomputed_sharpe": float(daily.mean() / daily.std(ddof=1) * math.sqrt(252.0)),
        "recomputed_max_dd_pct": float(np.min(np.where(peaks > 0, equity / peaks - 1.0, 0.0)) * 100.0),
        "equity_start": str(eq["date"].iloc[0].date()),
        "equity_end": str(eq["date"].iloc[-1].date()),
        "n_equity_days": int(len(eq)),
    }


def daily_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["date"]).sort_values("date")
    frame["strategy_return"] = frame["equity"].pct_change()
    frame["benchmark_return"] = frame["benchmark_equity"].pct_change()
    frame["excess_return"] = frame["strategy_return"] - frame["benchmark_return"]
    return frame.dropna(subset=["strategy_return"]).reset_index(drop=True)


def hac_mean_test(values: np.ndarray, max_lag: int = 5) -> dict:
    x = np.asarray(values, dtype=float)
    n = len(x)
    mean = float(x.mean())
    resid = x - mean
    lrv = float(resid @ resid / n)
    for lag in range(1, min(max_lag, n - 1) + 1):
        w = 1.0 - lag / (max_lag + 1.0)
        lrv += 2.0 * w * float(resid[lag:] @ resid[:-lag] / n)
    se = math.sqrt(max(lrv, 0.0) / n)
    t = mean / se if se > 0 else np.nan
    return {
        "mean_daily": mean,
        "hac_se": se,
        "hac_t": t,
        "hac_p_one_sided": float(1.0 - stats.t.cdf(t, df=max(n - 1, 1))),
    }


def block_bootstrap(values: np.ndarray, rng: np.random.Generator) -> dict:
    x = np.asarray(values, dtype=float)
    n = len(x)
    block = min(BLOCK, n)
    starts = np.arange(0, n - block + 1)
    n_blocks = int(np.ceil(n / block))
    boot = np.empty(N_BOOT)
    null = np.empty(N_BOOT)
    centered = x - x.mean()
    for i in range(N_BOOT):
        chosen = rng.choice(starts, size=n_blocks, replace=True)
        idx = np.concatenate([np.arange(s, s + block) for s in chosen])[:n]
        boot[i] = x[idx].mean()
        null[i] = centered[idx].mean()
    observed = float(x.mean())
    return {
        "block_length": BLOCK,
        "n_bootstrap": N_BOOT,
        "boot_ci_lo": float(np.quantile(boot, 0.025)),
        "boot_ci_hi": float(np.quantile(boot, 0.975)),
        "boot_p_one_sided_null_centered": float((1 + np.sum(null >= observed)) / (N_BOOT + 1)),
    }


def main() -> None:
    results = pd.read_csv(MATRIX / "experiment_results_clean.csv")
    rows = []
    problems = []
    for _, r in results.iterrows():
        slug = SLUGS[str(r["experiment"])]
        bench = str(r["benchmark"])
        eq_path = MATRIX / "experiment_equity_logs_clean" / f"{bench.lower()}_{slug}_test.csv"
        rec = recompute(eq_path)
        row = {
            "experiment": r["experiment"],
            "benchmark": bench,
            "base_seed": int(r["base_seed"]),
            "cem_iters": int(r["cem_iters"]),
            "cem_pop": int(r["cem_pop"]),
            "policy_scope": r["policy_scope"],
            "cem_objective": float(r["cem_objective"]),
            "train_return_pct": float(r["train_return_pct"]),
            "train_benchmark_return_pct": float(r["train_benchmark_return_pct"]),
            "train_excess_return_pct": float(r["train_excess_return_pct"]),
            "train_sharpe": float(r["train_sharpe"]),
            "train_max_dd_pct": float(r["train_max_dd_pct"]),
            "train_trades": int(r["train_trades"]),
            "test_return_pct": float(r["test_return_pct"]),
            "test_benchmark_return_pct": float(r["test_benchmark_return_pct"]),
            "test_excess_return_pct": float(r["test_excess_return_pct"]),
            "test_sharpe": float(r["test_sharpe"]),
            "test_max_dd_pct": float(r["test_max_dd_pct"]),
            "test_trades": int(r["test_trades"]),
            "test_win_rate_pct": float(r["test_win_rate_pct"]),
            "test_trade_txn_cost": float(r["test_trade_txn_cost"]),
            "test_total_txn_cost": float(r["test_total_txn_cost"]),
            "test_start_date": r["test_start_date"],
            "test_end_date": r["test_end_date"],
            **rec,
        }
        row["return_match"] = abs(row["test_return_pct"] - rec["recomputed_return_pct"]) <= 0.02
        row["sharpe_match"] = abs(row["test_sharpe"] - rec["recomputed_sharpe"]) <= 0.02
        row["dd_match"] = abs(row["test_max_dd_pct"] - rec["recomputed_max_dd_pct"]) <= 0.02
        if not (row["return_match"] and row["sharpe_match"] and row["dd_match"]):
            problems.append(row)
        rows.append(row)
    matrix = pd.DataFrame(rows)
    matrix.to_csv(OUT / "cem_matrix_final.csv", index=False)

    lines = [
        "# CEM matrix verification (equity-log recomputation)",
        "",
        f"Rows: {len(matrix)}; mismatches beyond tolerance (0.02): {len(problems)}",
        "",
        matrix[["experiment", "benchmark", "test_return_pct", "recomputed_return_pct",
                "test_sharpe", "recomputed_sharpe", "test_max_dd_pct", "recomputed_max_dd_pct",
                "return_match", "sharpe_match", "dd_match"]].to_string(index=False),
        "",
    ]
    (OUT / "cem_matrix_verification.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))

    # HAC + block bootstrap for the flagship arm; cleaning delta vs legacy control.
    rng = np.random.default_rng(SEED)
    legacy_results = pd.read_csv(LEGACY / "experiment_results_clean.csv")
    inference_rows = []
    delta_rows = []
    for bench in ("SPY", "QQQ"):
        slug = bench.lower()
        clean_daily = daily_frame(MATRIX / "experiment_equity_logs_clean" / f"{slug}_t1_t2_t3_t4_test.csv")
        legacy_daily = daily_frame(LEGACY / "experiment_equity_logs_clean" / f"{slug}_t1_t2_t3_t4_test.csv")
        clean_row = results[(results["experiment"] == "T1+T2+T3+T4") & (results["benchmark"] == bench)].iloc[0]
        legacy_row = legacy_results[(legacy_results["experiment"] == "T1+T2+T3+T4") & (legacy_results["benchmark"] == bench)].iloc[0]
        for label, daily, res in (("audit_clean", clean_daily, clean_row), ("original", legacy_daily, legacy_row)):
            excess = daily["excess_return"].to_numpy(float)
            inference_rows.append({
                "benchmark": bench,
                "universe": label,
                "n_daily_returns": len(excess),
                "portfolio_return_pct": float(res["test_return_pct"]),
                "benchmark_return_pct": float(res["test_benchmark_return_pct"]),
                "excess_return_pct": float(res["test_excess_return_pct"]),
                "max_drawdown_pct": float(res["test_max_dd_pct"]),
                "sharpe": float(res["test_sharpe"]),
                "trades": int(res["test_trades"]),
                **hac_mean_test(excess),
                **block_bootstrap(excess, rng),
            })
        matched = clean_daily[["date", "strategy_return"]].merge(
            legacy_daily[["date", "strategy_return"]], on="date",
            suffixes=("_clean", "_original"), validate="one_to_one")
        delta = (matched["strategy_return_clean"] - matched["strategy_return_original"]).to_numpy(float)
        delta_rows.append({
            "benchmark": bench,
            "n_matched_days": len(delta),
            "clean_minus_original_terminal_return_pct": float(clean_row["test_return_pct"] - legacy_row["test_return_pct"]),
            **hac_mean_test(delta),
            **block_bootstrap(delta, rng),
        })
    pd.DataFrame(inference_rows).to_csv(OUT / "cem_oos_block_inference_final.csv", index=False)
    pd.DataFrame(delta_rows).to_csv(OUT / "cem_cleaning_delta_final.csv", index=False)
    print(pd.DataFrame(inference_rows).to_string(index=False))
    print(pd.DataFrame(delta_rows).to_string(index=False))


if __name__ == "__main__":
    main()
