import asyncio
import os
from pathlib import Path

import pandas as pd
import numpy as np
from scipy import stats
from pipeline.data_loader import load_prices_from_db


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
        print(f"  STATISTICAL TESTS FOR {benchmark} (T1+T3 Configuration)")
        print(f"{'=' * 60}")

        trade_log_path  = repo_root / 'data' / 'experiment_trade_logs_clean'  / f'{benchmark.lower()}_t1_t3_test.csv'
        equity_log_path = repo_root / 'data' / 'experiment_equity_logs_clean' / f'{benchmark.lower()}_t1_t3_test.csv'

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


if __name__ == '__main__':
    asyncio.run(run_tests())
