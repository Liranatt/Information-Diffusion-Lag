import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import re

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

def main():
    equity_dir = Path("data/experiment_equity_logs_clean")
    
    experiments = [
        "Baseline", "T1 FrictionPenalty", "T2 TrainWindows", "T3 Kelly",
        "T1+T2", "T1+T3", "T2+T3", "T1+T2+T3"
    ]
    
    metrics = {
        "Exp": [],
        "PnL": [],
        "Sharpe": [],
        "Max DD": [],
        "Sortino": []
    }
    
    bench_pnl, bench_sharpe, bench_max_dd, bench_sortino = {}, {}, {}, {}
    
    for bench in ["spy", "qqq"]:
        test_file = equity_dir / f"{bench}_baseline_test.csv"
        if not test_file.exists():
            print(f"File not found: {test_file}")
            continue
        df = pd.read_csv(test_file)
        bench_ret = df["benchmark_equity"].pct_change().dropna()
        bm = calc_daily_metrics(bench_ret)
        bench_pnl[bench.upper()] = bm["pnl"]
        bench_sharpe[bench.upper()] = bm["sharpe"]
        bench_max_dd[bench.upper()] = bm["max_dd"]
        bench_sortino[bench.upper()] = bm["sortino"]
    
    for exp in experiments:
        slug = re.sub(r"[^a-z0-9]+", "_", exp.lower()).strip("_")
        test_file = equity_dir / f"spy_{slug}_test.csv"
        if not test_file.exists():
            continue
        df = pd.read_csv(test_file)
        strat_ret = df["equity"].pct_change().dropna()
        sm = calc_daily_metrics(strat_ret)
        metrics["Exp"].append(exp)
        metrics["PnL"].append(sm["pnl"])
        metrics["Sharpe"].append(sm["sharpe"])
        metrics["Max DD"].append(sm["max_dd"])
        metrics["Sortino"].append(sm["sortino"])
        
    if not metrics["Exp"]:
        print("No metrics found. Run backtest first.")
        return

    x = np.arange(len(metrics["Exp"]))
    width = 0.6
    
    fig, axs = plt.subplots(2, 2, figsize=(15, 12))
    
    def plot_metric(ax, name, ylabel, data, b_data):
        ax.bar(x, data, width, color="#10b981", label="Strategy (SPY-optimized)")
        ax.axhline(b_data.get("SPY", 0), color="#5a6b7f", linestyle="--", linewidth=2, label="SPY B&H")
        if "QQQ" in b_data:
            ax.axhline(b_data["QQQ"], color="#f97316", linestyle=":", linewidth=2, label="QQQ B&H")
        ax.set_title(name, fontsize=14, pad=10)
        ax.set_ylabel(ylabel, fontsize=12)
        ax.set_xticks(x)
        ax.set_xticklabels(metrics["Exp"], rotation=45, ha="right", fontsize=10)
        ax.grid(axis='y', linestyle='--', alpha=0.7)
        ax.legend()

    plot_metric(axs[0, 0], "Total Return (PnL)", "Percentage (%)", metrics["PnL"], bench_pnl)
    plot_metric(axs[0, 1], "Sharpe Ratio", "Ratio", metrics["Sharpe"], bench_sharpe)
    plot_metric(axs[1, 0], "Max Drawdown", "Percentage (%)", metrics["Max DD"], bench_max_dd)
    plot_metric(axs[1, 1], "Sortino Ratio", "Ratio", metrics["Sortino"], bench_sortino)
    
    plt.tight_layout()
    plt.savefig("data/cem_metrics_comparison.png", dpi=300, bbox_inches="tight")
    print("Saved graphs to data/cem_metrics_comparison.png")

if __name__ == "__main__":
    main()
