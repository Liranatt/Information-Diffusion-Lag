import pandas as pd
import numpy as np

t3_log = pd.read_csv('data/experiment_trade_logs_clean/spy_t1_t2_t3_test.csv')
t4_log = pd.read_csv('data/experiment_trade_logs_clean/spy_t1_t2_t3_t4_test.csv')

t3_trades = set(t3_log['symbol'] + '_' + t3_log['entry_date'].astype(str))
t4_trades = set(t4_log['symbol'] + '_' + t4_log['entry_date'].astype(str))

t4_only = t4_log[t4_log.apply(lambda row: f"{row['symbol']}_{row['entry_date']}" not in t3_trades, axis=1)]
t3_only = t3_log[t3_log.apply(lambda row: f"{row['symbol']}_{row['entry_date']}" not in t4_trades, axis=1)]

print(f"Trades in T4 but not T3: {len(t4_only)}")
if len(t4_only) > 0:
    print(f"Avg PnL of T4 only: {t4_only['pnl_pct'].mean():.2f}%")
    print(t4_only[['symbol', 'entry_date', 'pnl_pct', 'event_family', 'entry_prob']].head(10).to_string(index=False))

print(f"\nTrades in T3 but not T4: {len(t3_only)}")
if len(t3_only) > 0:
    print(f"Avg PnL of T3 only: {t3_only['pnl_pct'].mean():.2f}%")
    print(t3_only[['symbol', 'entry_date', 'pnl_pct']].head(10).to_string(index=False))

