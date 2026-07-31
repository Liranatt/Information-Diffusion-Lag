"""Reproducible statistical inputs for the revised short technical paper."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats


ROOT = Path(__file__).resolve().parent.parent
CLEAN_RESULTS = ROOT / "data" / "experiment_results_clean.csv"
CLEAN_EQUITY = ROOT / "data" / "experiment_equity_logs_clean"
LEGACY_ROOT = ROOT / "runs" / "paper_legacy_key_arms"
LEGACY_RESULTS = LEGACY_ROOT / "experiment_results_clean.csv"
LEGACY_EQUITY = LEGACY_ROOT / "experiment_equity_logs_clean"
OUTPUT = ROOT / "output" / "pdf" / "paper_revision"

N_BOOT = 20_000
BLOCK_LENGTH = 5
SEED = 20260713


def _daily_returns(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["date"]).sort_values("date")
    frame["strategy_return"] = frame["equity"].pct_change()
    frame["benchmark_return"] = frame["benchmark_equity"].pct_change()
    frame["excess_return"] = frame["strategy_return"] - frame["benchmark_return"]
    return frame.dropna(subset=["strategy_return", "benchmark_return"]).reset_index(drop=True)


def _hac_mean_test(values: np.ndarray, max_lag: int = 5) -> dict[str, float]:
    x = np.asarray(values, dtype=float)
    n = len(x)
    mean = float(x.mean())
    residual = x - mean
    long_run_variance = float(np.dot(residual, residual) / n)
    for lag in range(1, min(max_lag, n - 1) + 1):
        weight = 1.0 - lag / (max_lag + 1.0)
        covariance = float(np.dot(residual[lag:], residual[:-lag]) / n)
        long_run_variance += 2.0 * weight * covariance
    standard_error = np.sqrt(max(long_run_variance, 0.0) / n)
    t_stat = mean / standard_error if standard_error > 0 else np.nan
    p_one_sided = float(1.0 - stats.t.cdf(t_stat, df=max(n - 1, 1)))
    return {
        "mean": mean,
        "hac_standard_error": float(standard_error),
        "hac_t": float(t_stat),
        "hac_p_one_sided": p_one_sided,
    }


def _moving_block_bootstrap(values: np.ndarray, rng: np.random.Generator) -> dict[str, float]:
    x = np.asarray(values, dtype=float)
    n = len(x)
    block = min(BLOCK_LENGTH, n)
    starts = np.arange(0, n - block + 1)
    n_blocks = int(np.ceil(n / block))

    boot_means = np.empty(N_BOOT, dtype=float)
    null_means = np.empty(N_BOOT, dtype=float)
    centered = x - x.mean()
    for iteration in range(N_BOOT):
        chosen = rng.choice(starts, size=n_blocks, replace=True)
        indices = np.concatenate([np.arange(start, start + block) for start in chosen])[:n]
        boot_means[iteration] = x[indices].mean()
        null_means[iteration] = centered[indices].mean()

    observed = float(x.mean())
    return {
        "block_length": BLOCK_LENGTH,
        "bootstrap_repetitions": N_BOOT,
        "bootstrap_ci_low": float(np.quantile(boot_means, 0.025)),
        "bootstrap_ci_high": float(np.quantile(boot_means, 0.975)),
        "bootstrap_p_one_sided": float((1 + np.sum(null_means >= observed)) / (N_BOOT + 1)),
    }


def _result_row(results: pd.DataFrame, experiment: str, benchmark: str) -> pd.Series:
    row = results[(results["experiment"] == experiment) & (results["benchmark"] == benchmark)]
    if len(row) != 1:
        raise ValueError(f"Expected one {experiment}/{benchmark} result, found {len(row)}")
    return row.iloc[0]


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clean_results = pd.read_csv(CLEAN_RESULTS)
    legacy_results = pd.read_csv(LEGACY_RESULTS)
    rng = np.random.default_rng(SEED)
    rows: list[dict] = []
    matched_rows: list[dict] = []

    for benchmark in ("SPY", "QQQ"):
        slug = benchmark.lower()
        clean_path = CLEAN_EQUITY / f"{slug}_t1_t2_t3_t4_test.csv"
        legacy_path = LEGACY_EQUITY / f"{slug}_t1_t2_t3_t4_test.csv"
        clean_daily = _daily_returns(clean_path)
        legacy_daily = _daily_returns(legacy_path)

        clean_result = _result_row(clean_results, "T1+T2+T3+T4", benchmark)
        legacy_result = _result_row(legacy_results, "T1+T2+T3+T4", benchmark)
        for label, daily, result in (
            ("audit_clean", clean_daily, clean_result),
            ("original", legacy_daily, legacy_result),
        ):
            values = daily["excess_return"].to_numpy(float)
            rows.append(
                {
                    "benchmark": benchmark,
                    "universe": label,
                    "n_daily_returns": len(values),
                    "portfolio_return_pct": float(result["test_return_pct"]),
                    "benchmark_return_pct": float(result["test_benchmark_return_pct"]),
                    "excess_return_pct": float(result["test_excess_return_pct"]),
                    "max_drawdown_pct": float(result["test_max_dd_pct"]),
                    "sharpe": float(result["test_sharpe"]),
                    "trades": int(result["test_trades"]),
                    "win_rate_pct": float(result["test_win_rate_pct"]),
                    **_hac_mean_test(values),
                    **_moving_block_bootstrap(values, rng),
                }
            )

        matched = clean_daily[["date", "strategy_return"]].merge(
            legacy_daily[["date", "strategy_return"]],
            on="date",
            suffixes=("_clean", "_original"),
            validate="one_to_one",
        )
        delta = (
            matched["strategy_return_clean"] - matched["strategy_return_original"]
        ).to_numpy(float)
        matched_rows.append(
            {
                "benchmark": benchmark,
                "n_matched_days": len(delta),
                "clean_minus_original_terminal_return_pct": float(
                    clean_result["test_return_pct"] - legacy_result["test_return_pct"]
                ),
                **_hac_mean_test(delta),
                **_moving_block_bootstrap(delta, rng),
            }
        )

    inference = pd.DataFrame(rows)
    matched_inference = pd.DataFrame(matched_rows)
    inference.to_csv(OUTPUT / "cem_oos_block_inference.csv", index=False)
    matched_inference.to_csv(OUTPUT / "cem_cleaning_delta_inference.csv", index=False)

    payload = {
        "seed": SEED,
        "n_bootstrap": N_BOOT,
        "block_length": BLOCK_LENGTH,
        "cem_oos": inference.to_dict(orient="records"),
        "cleaning_delta": matched_inference.to_dict(orient="records"),
    }
    (OUTPUT / "paper_statistical_results.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    print(inference.to_string(index=False))
    print("\nMatched cleaning deltas")
    print(matched_inference.to_string(index=False))


if __name__ == "__main__":
    main()
