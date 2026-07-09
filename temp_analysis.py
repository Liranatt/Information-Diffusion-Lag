import pandas as pd
import numpy as np

t3_log = pd.read_csv('data/experiment_trade_logs_clean/spy_t1_t2_t3_test.csv')
t4_log = pd.read_csv('data/experiment_trade_logs_clean/spy_t1_t2_t3_t4_test.csv')

def get_stats(df, name):
    print(f'--- {name} ---')
    print(f'Total Trades: {len(df)}')
    df['pnl_pct'] = df['pnl_pct'].astype(float)
    wins = len(df[df['pnl_pct'] > 0])
    losses = len(df[df['pnl_pct'] <= 0])
    print(f'Win Rate: {wins/len(df)*100:.1f}%')
    print(f'Avg PnL: {df["pnl_pct"].mean():.2f}%')
    
    if 'preempt_reason' in df.columns:
        preempted = df[df['preempt_reason'].notna()]
        preempted = preempted[preempted['preempt_reason'].astype(str) != 'nan']
        print(f'Preempted Trades: {len(preempted)}')
        if len(preempted) > 0:
            print(f'Avg PnL of Preempted: {preempted["pnl_pct"].mean():.2f}%')
            
    if 'event_family' in df.columns:
        print('Event Families:')
        print(df['event_family'].value_counts())
        
get_stats(t3_log, 'T1+T2+T3')
get_stats(t4_log, 'T1+T2+T3+T4')
