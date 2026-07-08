import asyncio
import os
import sys
from pathlib import Path

import pandas as pd
import numpy as np
from scipy import stats
from pipeline.data_loader import load_prices_from_db

# Console output contains non-cp1252 glyphs ("→"). Without this, the default
# Windows codepage raises UnicodeEncodeError mid-run and silently truncates the
# test suite -- run_spa() for QQQ and run_jensens_alpha() never execute.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def ib_cost(shares: int, price: float, is_sell: bool) -> float:
    """IB-style commission + SEC fee on sales + fixed 5 bp slippage."""
    if shares <= 0 or price <= 0:
        return 0.0
    trade_value = shares * price
    commission = max(0.35, min(shares * 0.0035, trade_value * 0.01))
    sec = trade_value * 0.0000278 if is_sell else 0.0
    return commission + sec + trade_value * 0.0005


def get_closest_price(prices_series: pd.Series, target_date: str):
    """
    Return the price on target_date, or the most-recent prior trading day
    if that exact date is missing (e.g. weekend / holiday).
    Defined OUTSIDE the trade loop to avoid repeated closure re-definition.
    """
    if target_date in prices_series.index:
        return prices_series[target_date]
    prior = prices_series[prices_series.index <= target_date]
    return prior.iloc[-1] if not prior.empty else None


async def run_tests():
    repo_root = Path(__file__).resolve().parent

    for benchmark in ['SPY', 'QQQ']:
        print(f"\n{'=' * 60}")
        print(f"  STATISTICAL TESTS FOR {benchmark} (T1+T2+T3 Configuration)")
        print(f"{'=' * 60}")

        trade_log_path  = repo_root / 'data' / 'experiment_trade_logs_clean'  / f'{benchmark.lower()}_t1_t2_t3_test.csv'
        equity_log_path = repo_root / 'data' / 'experiment_equity_logs_clean' / f'{benchmark.lower()}_t1_t2_t3_test.csv'

        # ── Load trade log ────────────────────────────────────────────
        try:
            df_all = pd.read_csv(trade_log_path)
        except FileNotFoundError:
            print(f"Error: Trade log not found at {trade_log_path}")
            continue

        # ── FIX 1: restrict to OOS splits only ───────────────────────
        # Training trades are used by CEM for policy search and contain
        # intentional exploration losses.  Including them in a statistical
        # hypothesis test about the strategy's live predictive power is
        # methodologically incorrect — we test only on val + test splits.
        df_trades = df_all[df_all['split'].isin(['val', 'test'])].reset_index(drop=True)

        if df_trades.empty:
            print("No OOS trades found (split in ['val','test']). Skipping.")
            continue

        # ── Test 1: Binomial — directional accuracy ───────────────────
        print("\n=== Test 1: Binomial Test for Directional Accuracy ===")
        trials    = len(df_trades)
        successes = int((df_trades['pnl'] > 0).sum())

        result = stats.binomtest(k=successes, n=trials, p=0.5, alternative='greater')
        print(f"Successes:   {successes}")
        print(f"Total Trials:{trials}")
        print(f"Win Rate:    {successes / trials:.4f}")
        print(f"P-value:     {result.pvalue:.4e}")
        if result.pvalue < 0.05:
            print("Conclusion: Directional accuracy is statistically significant (better than random).")
        else:
            print("Conclusion: Directional accuracy is NOT statistically significant.")

        # ── Test 2: Net CAR T-Test ────────────────────────────────────
        print("\n=== Test 2: Net Cumulative Abnormal Return (CAR) T-Test ===")

        prices_dict = await load_prices_from_db([benchmark])
        benchmark_data = prices_dict.get(benchmark, [])
        if not benchmark_data:
            print(f"Error: Could not load {benchmark} data from DB.")
            continue

        benchmark_prices = pd.Series(
            data  = [price for _, price in benchmark_data],
            index = [ts.strftime('%Y-%m-%d') for ts, _ in benchmark_data],
        ).sort_index()

        net_car_values = []

        for _, row in df_trades.iterrows():
            entry_date = str(row['entry_date'])[:10]
            exit_date  = str(row['exit_date'])[:10]

            # ── FIX 2: use the simulator's already-netted pnl_pct ────
            # The trade log's pnl_pct is computed by the portfolio simulator
            # which already applies IB commissions, SEC fees, and 5 bp
            # slippage on all four rotation legs.  Re-deriving costs from
            # raw entry/exit prices risks double-counting slippage.
            net_asset_return = float(row['pnl_pct']) / 100.0

            b_entry = get_closest_price(benchmark_prices, entry_date)
            b_exit  = get_closest_price(benchmark_prices, exit_date)

            if any(
                x is None or (isinstance(x, float) and np.isnan(x)) or x == 0
                for x in [b_entry, b_exit]
            ):
                continue

            benchmark_return = (b_exit / b_entry) - 1.0
            net_car_values.append(net_asset_return - benchmark_return)

        if not net_car_values:
            print("No valid trades for Net CAR test.")
            continue

        net_car_array = np.array(net_car_values)
        mean_net_car  = net_car_array.mean()
        t_stat_car, p_val_car = stats.ttest_1samp(net_car_array, popmean=0.0, alternative='greater')

        print(f"Number of valid trades for Net CAR: {len(net_car_array)}")
        print(f"Mean Net CAR:   {mean_net_car:.6f}")
        print(f"T-statistic:    {t_stat_car:.4f}")
        print(f"P-value:        {p_val_car:.4e}")
        if p_val_car < 0.05:
            print("Conclusion: Net Abnormal Return (Trade-Level) is statistically significant.")
        else:
            print("Conclusion: Net Abnormal Return (Trade-Level) is NOT statistically significant. "
                  "Transaction costs may have eaten the edge.")

        # ── Test 3: Daily Net Excess Return (Alpha) ───────────────────
        print("\n=== Test 3: Daily Net Excess Return Significance Test ===")

        try:
            df_equity = pd.read_csv(equity_log_path, parse_dates=['date'])
        except FileNotFoundError:
            print(f"Error: Equity log not found at {equity_log_path}")
            continue

        # ── FIX 3: restrict equity curve to OOS period ───────────────
        # Days prior to the first OOS entry belong to the training/exploration
        # phase where the portfolio behaviour is not representative of the
        # live policy.  Including them suppresses the per-day signal estimate.
        oos_start = pd.to_datetime(df_trades['entry_date'].min())
        df_equity = df_equity[df_equity['date'] >= oos_start].copy()

        df_equity['portfolio_return']  = df_equity['equity'].pct_change()
        df_equity['benchmark_return']  = df_equity['benchmark_equity'].pct_change()
        df_equity = df_equity.dropna(subset=['portfolio_return', 'benchmark_return'])
        df_equity['net_excess_return'] = df_equity['portfolio_return'] - df_equity['benchmark_return']

        daily_net_excess = df_equity['net_excess_return'].values
        mean_excess      = daily_net_excess.mean()
        t_stat_alpha, p_val_alpha = stats.ttest_1samp(daily_net_excess, popmean=0.0, alternative='greater')

        print(f"Number of trading days: {len(daily_net_excess)}")
        print(f"Mean Daily Net Excess Return (Alpha): {mean_excess:.6f}")
        print(f"T-statistic: {t_stat_alpha:.4f}")
        print(f"P-value:     {p_val_alpha:.4e}")
        if p_val_alpha < 0.05:
            print("Conclusion: Alpha is statistically significant. "
                  "The strategy generates true value added over the benchmark.")
        else:
            print("Conclusion: Alpha is NOT statistically significant. "
                  "This suggests that while Information Diffusion Lag exists, "
                  "it is largely 'eaten' by transaction costs at the portfolio level.")


def jensens_alpha_test(
    benchmark: str,
    equity_dir: Path,
    experiment_slugs: list[str],
    rf_annual: float = 0.05,
) -> list[dict]:
    """Jensen's Alpha via CAPM regression: Rp - Rf = alpha + beta*(Rm - Rf) + e"""
    daily_rf = (1 + rf_annual) ** (1 / 252) - 1
    results = []

    for slug in experiment_slugs:
        path = equity_dir / f"{benchmark.lower()}_{slug}_test.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        rp = df["equity"].pct_change().dropna().values
        rm = df["benchmark_equity"].pct_change().dropna().values
        n = min(len(rp), len(rm))
        rp, rm = rp[:n], rm[:n]

        y = rp - daily_rf
        x = rm - daily_rf
        X = np.column_stack([np.ones(n), x])
        beta_vec, residuals, _, _ = np.linalg.lstsq(X, y, rcond=None)
        alpha_daily = beta_vec[0]
        beta = beta_vec[1]

        y_hat = X @ beta_vec
        resid = y - y_hat
        se = np.sqrt(np.sum(resid ** 2) / (n - 2) / np.sum((x - x.mean()) ** 2))
        se_alpha = np.sqrt(np.sum(resid ** 2) / (n - 2) * (1 / n + x.mean() ** 2 / np.sum((x - x.mean()) ** 2)))
        t_alpha = alpha_daily / se_alpha if se_alpha > 0 else 0.0
        p_alpha = 1 - stats.t.cdf(t_alpha, df=n - 2)

        alpha_annual = alpha_daily * 252
        results.append({
            "strategy": slug,
            "alpha_daily_bps": alpha_daily * 10_000,
            "alpha_annual_pct": alpha_annual * 100,
            "beta": beta,
            "t_statistic": t_alpha,
            "p_value": p_alpha,
            "n_days": n,
        })

    return results


async def run_jensens_alpha():
    repo_root = Path(__file__).resolve().parent
    equity_dir = repo_root / "data" / "experiment_equity_logs_clean"

    for benchmark in ["SPY", "QQQ"]:
        print(f"\n{'=' * 60}")
        print(f"  JENSEN'S ALPHA (CAPM) — {benchmark} benchmark")
        print(f"  Rp - Rf = alpha + beta*(Rm - Rf)")
        print(f"  Rf = 5.0% annual (10Y Treasury approx)")
        print(f"{'=' * 60}")

        results = jensens_alpha_test(benchmark, equity_dir, EXPERIMENT_SLUGS)
        if not results:
            print("  No equity logs found.")
            continue

        print(f"  {'Strategy':<22} {'Alpha(bps/d)':>12} {'Alpha(%/yr)':>11} "
              f"{'Beta':>6} {'t-stat':>7} {'p-value':>8}")
        print(f"  {'-' * 70}")

        for r in results:
            sig = "*" if r["p_value"] < 0.05 else ""
            print(f"  {r['strategy']:<22} {r['alpha_daily_bps']:>+11.2f} "
                  f"{r['alpha_annual_pct']:>+10.2f}% {r['beta']:>6.3f} "
                  f"{r['t_statistic']:>7.3f} {r['p_value']:>8.4f}{sig}")

        best = min(results, key=lambda r: r["p_value"])
        print(f"\n  Best: {best['strategy']} "
              f"(alpha={best['alpha_annual_pct']:+.2f}%/yr, p={best['p_value']:.4f})")

        # `best` is the argmin of p over len(results) strategies, so its p-value
        # cannot be read against a nominal 5% threshold -- that is the classic
        # multiple-comparisons error. Bonferroni is the floor of the correction,
        # not the whole of it: each strategy here is itself the argmax of a CEM
        # search over 120 (non-WF) or 607 (WF) policy evaluations, so the true
        # trial count is ~7,290, not len(results). Treat even a "pass" below as
        # provisional until a Deflated Sharpe Ratio is computed from
        # output/cem_population.csv.
        k = len(results)
        bonferroni = 0.05 / k
        print(f"  Bonferroni threshold for k={k} reported strategies: p < {bonferroni:.4f}")

        if best["p_value"] < bonferroni:
            print(f"  SIGNIFICANT after Bonferroni: evidence of skill-based excess return")
        elif best["p_value"] < 0.05:
            print(f"  NOT significant after Bonferroni "
                  f"(nominal p={best['p_value']:.4f} survives 5% only because it is "
                  f"the best of {k} searched strategies)")
        else:
            print(f"  NOT significant at 5%, even before correcting for k={k}")


def hansen_spa_test(
    benchmark: str,
    equity_dir: Path,
    experiment_slugs: list[str],
    n_bootstrap: int = 10_000,
    avg_block_length: float = 10.0,
    seed: int = 42,
) -> dict:
    """Hansen's Superior Predictive Ability test (Hansen 2005).

    Tests H0: no strategy beats B&H after adjusting for multiple comparisons.
    Uses stationary bootstrap (Politis & Romano 1994).
    """
    excess_returns = []
    names = []

    for slug in experiment_slugs:
        path = equity_dir / f"{benchmark.lower()}_{slug}_test.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        strat = np.log(df["equity"].values[1:] / df["equity"].values[:-1])
        bench = np.log(df["benchmark_equity"].values[1:] / df["benchmark_equity"].values[:-1])
        excess_returns.append(strat - bench)
        names.append(slug)

    if not excess_returns:
        return {"p_consistent": None}

    D = np.column_stack(excess_returns)
    T, k = D.shape
    d_bar = D.mean(axis=0)
    d_var = D.var(axis=0, ddof=1)
    d_std = np.sqrt(d_var / T)

    t_values = d_bar / d_std
    t_spa = float(np.max(t_values))
    best_idx = int(np.argmax(t_values))

    rng = np.random.default_rng(seed)
    p_new = 1.0 / avg_block_length

    starts = rng.integers(0, T, size=(n_bootstrap, T))
    uniforms = rng.random(size=(n_bootstrap, T))
    indices = np.empty((n_bootstrap, T), dtype=np.intp)
    indices[:, 0] = starts[:, 0]
    for t in range(1, T):
        new_block = uniforms[:, t] < p_new
        indices[:, t] = np.where(new_block, starts[:, t], (indices[:, t - 1] + 1) % T)

    D_boot = D[indices]
    d_bar_boot = D_boot.mean(axis=1)

    threshold = np.sqrt(d_var * 2.0 * np.log(np.log(max(T, 3))) / T)
    g_consistent = d_bar * (d_bar >= -threshold).astype(float)
    g_upper = d_bar.copy()
    g_lower = np.zeros(k)

    results = {}
    for variant, g in [("consistent", g_consistent), ("upper", g_upper), ("lower", g_lower)]:
        centered = d_bar_boot - g[None, :]
        t_boot = np.max(centered / d_std[None, :], axis=1)
        results[f"p_{variant}"] = float(np.mean(t_boot >= t_spa))

    results["t_statistic"] = t_spa
    results["best_strategy"] = names[best_idx]
    results["best_daily_excess_bps"] = float(d_bar[best_idx] * 10_000)
    results["n_strategies"] = k
    results["n_days"] = T
    return results


EXPERIMENT_SLUGS = [
    "baseline", "t1_frictionpenalty", "t2_trainwindows", "t3_kelly",
    "t1_t2", "t1_t3", "t2_t3", "t1_t2_t3",
    "t4_geopriority", "t1_t2_t3_t4",
]


async def run_spa():
    repo_root = Path(__file__).resolve().parent
    equity_dir = repo_root / "data" / "experiment_equity_logs_clean"

    for benchmark in ["SPY", "QQQ"]:
        print(f"\n{'=' * 60}")
        print(f"  HANSEN'S SPA TEST — {benchmark} benchmark")
        print(f"  H0: no strategy beats {benchmark} B&H (data-snooping adjusted)")
        print(f"{'=' * 60}")

        spa = hansen_spa_test(benchmark, equity_dir, EXPERIMENT_SLUGS)
        if spa.get("p_consistent") is None:
            print("  No equity logs found.")
            continue

        print(f"  {spa['n_strategies']} strategies, {spa['n_days']} trading days, "
              f"10,000 stationary bootstrap replications")
        print(f"  Best strategy: {spa['best_strategy']}  "
              f"(avg daily excess: {spa['best_daily_excess_bps']:+.1f} bps/day)")
        print(f"  Test statistic: {spa['t_statistic']:.3f}")
        print(f"  p-value (consistent): {spa['p_consistent']:.4f}")
        print(f"  p-value (upper/conservative): {spa['p_upper']:.4f}")
        print(f"  p-value (lower/liberal): {spa['p_lower']:.4f}")

        if spa["p_consistent"] < 0.05:
            print(f"  → REJECT H0 at 5%: significant evidence of superior predictive ability")
        elif spa["p_consistent"] < 0.10:
            print(f"  → REJECT H0 at 10% (marginal)")
        else:
            print(f"  → FAIL TO REJECT H0 at 10%")


if __name__ == '__main__':
    asyncio.run(run_tests())
    asyncio.run(run_spa())
    asyncio.run(run_jensens_alpha())
