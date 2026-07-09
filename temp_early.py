import pandas as pd
import numpy as np

# Load trade logs
t3 = pd.read_csv('data/experiment_trade_logs_clean/qqq_t1_t2_t3_test.csv')
t4 = pd.read_csv('data/experiment_trade_logs_clean/qqq_t1_t2_t3_t4_test.csv')

def summarize_early_trades(df, name):
    df['entry_date'] = pd.to_datetime(df['entry_date'])
    early = df[df['entry_date'] < '2026-03-01']
    print(f"--- {name} (Jan-Feb) ---")
    print(f"Trades: {len(early)}")
    early['pnl_pct'] = early['pnl_pct'].astype(float)
    wins = len(early[early['pnl_pct'] > 0])
    print(f"Win Rate: {wins/len(early)*100:.1f}%")
    print(f"Avg PnL: {early['pnl_pct'].mean():.2f}%")
    print(f"Worst Trade: {early['pnl_pct'].min():.2f}%")
    
    # Calculate simple cumulative sum of PnL as proxy for drawdown shape
    early = early.sort_values('entry_date')
    early['cum_pnl'] = early['pnl_pct'].cumsum()
    print(f"Max Cumulative Drawdown (rough proxy): {early['cum_pnl'].min() - early['cum_pnl'].max():.2f}%")
    print(early[['symbol', 'entry_date', 'pnl_pct']].sort_values('pnl_pct').head(5).to_string())
    print("\n")

summarize_early_trades(t3, "T3 QQQ")
summarize_early_trades(t4, "T4 QQQ")
