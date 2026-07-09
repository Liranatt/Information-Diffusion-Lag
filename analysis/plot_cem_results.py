import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import re
import json
import asyncio
import sys

# To run sim_opp_cost, we need to import from optimize_cem
# But optimize_cem has if __name__ == "__main__": main()
import optimize_cem
from database.db_connection import connect

def calc_daily_metrics(r_series):
    if len(r_series) < 2: return {"sharpe":0, "sortino":0, "pnl":0, "max_dd":0}
    mu = r_series.mean()
    sig = r_series.std()
    sharpe = (mu/sig * np.sqrt(252)) if sig > 1e-9 else 0
    down = r_series[r_series < 0]
    down_sig = down.std() if len(down) > 0 else 1e-9
    sortino = (mu/down_sig * np.sqrt(252)) if down_sig > 1e-9 else 0
    cum = (1+r_series).cumprod()
    peak = cum.cummax()
    dd = (cum - peak)/peak * 100
    max_dd = dd.min()
    pnl = (cum.iloc[-1] - 1)*100 if len(cum) > 0 else 0
    return {"sharpe": sharpe, "sortino": sortino, "max_dd": max_dd, "pnl": pnl}

def add_explanations(ax, x=0.05, y=0.95, va='top', ha='left'):
    textstr = (
        "T1: Friction Penalty - Penalizes for txn costs & slippage.\n"
        "T2: Walk-Forward - Expanding chronological training windows.\n"
        "T3: Kelly - Dynamic sizing based on half-Kelly criterion."
    )
    props = dict(boxstyle='round', facecolor='wheat', alpha=0.5)
    ax.text(x, y, textstr, transform=ax.transAxes, fontsize=10,
            verticalalignment=va, horizontalalignment=ha, bbox=props)

def plot_bar_chart(title, ylabel, exp_names, data, b_data, filename):
    x = np.arange(len(exp_names))
    width = 0.6
    fig, ax = plt.subplots(figsize=(10, 6))
    
    ax.bar(x, data, width, color="#10b981", label="Strategy (SPY-optimized)")
    ax.axhline(b_data.get("SPY", 0), color="#5a6b7f", linestyle="--", linewidth=2, label="SPY B&H")
    if "QQQ" in b_data:
        ax.axhline(b_data["QQQ"], color="#f97316", linestyle=":", linewidth=2, label="QQQ B&H")
        
    ax.set_title(title, fontsize=14, pad=10)
    ax.set_ylabel(ylabel, fontsize=12)
    ax.set_xticks(x)
    ax.set_xticklabels(exp_names, rotation=45, ha="right", fontsize=10)
    ax.grid(axis='y', linestyle='--', alpha=0.7)
    
    is_drawdown = "Drawdown" in title
    
    # Scale y-axis limits to prevent bar overlap with text/legend
    y_min, y_max = ax.get_ylim()
    if is_drawdown:
        ax.set_ylim(y_min * 1.3, max(y_max, 0)) # give space at bottom for legend
    else:
        ax.set_ylim(min(y_min, 0), y_max * 1.3) # give space at top for legend/text
        
    ax.legend(loc="lower right" if is_drawdown else "upper right")
    
    if is_drawdown:
        # Legend is lower right, place text upper left
        add_explanations(ax, x=0.05, y=0.95, va='top', ha='left')
    else:
        # Legend is upper right, place text upper left
        add_explanations(ax, x=0.05, y=0.95, va='top', ha='left')
    
    plt.tight_layout()
    plt.savefig(filename, dpi=300, bbox_inches="tight")
    plt.close()

def plot_equity_curve(df, title, filename):
    # df should have 'date', 'equity', 'benchmark_equity', maybe 'qqq_equity'
    fig, ax = plt.subplots(figsize=(12, 6))
    
    ax.plot(pd.to_datetime(df['date']), df['equity'], label="Strategy", color="#1f77b4", linewidth=2)
    ax.plot(pd.to_datetime(df['date']), df['benchmark_equity'], label="SPY (Benchmark)", color="#2ca02c", linestyle="--", linewidth=2)
    if 'qqq_equity' in df.columns:
        ax.plot(pd.to_datetime(df['date']), df['qqq_equity'], label="QQQ (Benchmark)", color="#ff7f0e", linestyle=":", linewidth=2)
        
    ax.set_title(title, fontsize=14, pad=10)
    ax.set_ylabel("Equity ($)", fontsize=12)
    ax.grid(True, linestyle='--', alpha=0.7)
    
    # Legend is usually placed in upper left for equity curves (since they grow up and to the right)
    ax.legend(loc="upper left")
    
    # Place text in bottom right so it doesn't overlap the lines or the legend
    add_explanations(ax, x=0.95, y=0.05, va='bottom', ha='right')
    
    plt.tight_layout()
    plt.savefig(filename, dpi=300, bbox_inches="tight")
    plt.close()

def main():
    equity_dir = Path("data/experiment_equity_logs_clean")
    results_csv = Path("data/experiment_results_clean.csv")
    
    experiments = [
        "Baseline", "T1 FrictionPenalty", "T2 TrainWindows", "T3 Kelly",
        "T1+T2", "T1+T3", "T2+T3", "T1+T2+T3"
    ]
    
    metrics = {"Exp": [], "PnL": [], "Sharpe": [], "Max DD": [], "Sortino": []}
    bench_pnl, bench_sharpe, bench_max_dd, bench_sortino = {}, {}, {}, {}
    
    # 1. Bar charts logic
    for bench in ["spy", "qqq"]:
        test_file = equity_dir / f"{bench}_baseline_test.csv"
        if not test_file.exists(): continue
        df_bench = pd.read_csv(test_file)
        bench_ret = df_bench["benchmark_equity"].pct_change().dropna()
        bm = calc_daily_metrics(bench_ret)
        bench_pnl[bench.upper()] = bm["pnl"]
        bench_sharpe[bench.upper()] = bm["sharpe"]
        bench_max_dd[bench.upper()] = bm["max_dd"]
        bench_sortino[bench.upper()] = bm["sortino"]
    
    strat_equity_dfs = []
    
    for exp in experiments:
        slug = re.sub(r"[^a-z0-9]+", "_", exp.lower()).strip("_")
        test_file = equity_dir / f"spy_{slug}_test.csv"
        if not test_file.exists(): continue
        df_strat = pd.read_csv(test_file)
        strat_equity_dfs.append(df_strat['equity'].values)
        
        strat_ret = df_strat["equity"].pct_change().dropna()
        sm = calc_daily_metrics(strat_ret)
        metrics["Exp"].append(exp)
        metrics["PnL"].append(sm["pnl"])
        metrics["Sharpe"].append(sm["sharpe"])
        metrics["Max DD"].append(sm["max_dd"])
        metrics["Sortino"].append(sm["sortino"])
        
    if metrics["Exp"]:
        plot_bar_chart("Total Return (PnL)", "Percentage (%)", metrics["Exp"], metrics["PnL"], bench_pnl, "data/cem_metrics_pnl.png")
        plot_bar_chart("Sharpe Ratio", "Ratio", metrics["Exp"], metrics["Sharpe"], bench_sharpe, "data/cem_metrics_sharpe.png")
        plot_bar_chart("Max Drawdown", "Percentage (%)", metrics["Exp"], metrics["Max DD"], bench_max_dd, "data/cem_metrics_maxdd.png")
        plot_bar_chart("Sortino Ratio", "Ratio", metrics["Exp"], metrics["Sortino"], bench_sortino, "data/cem_metrics_sortino.png")
        print("Generated 4 bar charts.")
        
        # Mean equity plot
        if strat_equity_dfs:
            min_len = min(len(x) for x in strat_equity_dfs)
            mean_equity = np.mean([x[:min_len] for x in strat_equity_dfs], axis=0)
            df_mean = pd.DataFrame({
                "date": pd.read_csv(equity_dir / "spy_baseline_test.csv")["date"].iloc[:min_len],
                "equity": mean_equity,
                "benchmark_equity": pd.read_csv(equity_dir / "spy_baseline_test.csv")["benchmark_equity"].iloc[:min_len]
            })
            if (equity_dir / "qqq_baseline_test.csv").exists():
                df_mean["qqq_equity"] = pd.read_csv(equity_dir / "qqq_baseline_test.csv")["benchmark_equity"].iloc[:min_len]
                
            # Truncate to first trade of OOS (using T1+T2+T3 as reference)
            trade_log = Path("data/experiment_trade_logs_clean/spy_t1_t2_t3_test.csv")
            if trade_log.exists():
                tdf = pd.read_csv(trade_log)
                if not tdf.empty:
                    first_trade_date = tdf["entry_date"].min()
                    df_mean = df_mean[df_mean["date"] >= first_trade_date].copy()
            
            plot_equity_curve(df_mean, "Mean Strategy Equity (All 8 Experiments) vs Benchmarks", "data/cem_mean_strategy_equity.png")
            print("Generated Mean Strategy plot.")

    # 2. Simulate best strategy (Test OOS only, truncated to first trade)
    if results_csv.exists():
        df_res = pd.read_csv(results_csv)
        best_row = df_res.sort_values(by="oos_return_pct", ascending=False).iloc[0]
        slug = re.sub(r"[^a-z0-9]+", "_", best_row["experiment"].lower()).strip("_")
        
        eq_file = equity_dir / f"spy_{slug}_test.csv"
        trade_log = Path(f"data/experiment_trade_logs_clean/spy_{slug}_test.csv")
        
        if eq_file.exists() and trade_log.exists():
            df_best = pd.read_csv(eq_file)
            tdf = pd.read_csv(trade_log)
            
            qqq_file = equity_dir / f"qqq_baseline_test.csv"
            if qqq_file.exists():
                df_best["qqq_equity"] = pd.read_csv(qqq_file)["benchmark_equity"]
                
            if not tdf.empty:
                first_trade_date = tdf["entry_date"].min()
                df_best = df_best[df_best["date"] >= first_trade_date].copy()
                
            plot_equity_curve(df_best, f"OOS Equity: Best Strategy ({best_row['experiment']})", "data/cem_best_strategy_equity.png")
            print("Generated Best Strategy OOS plot.")

if __name__ == "__main__":
    main()
