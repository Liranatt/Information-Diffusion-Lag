import pandas as pd

eq = pd.read_csv('output/equity_diagnostics_oos.csv')

# SPY T1+T2+T3
t3 = eq[(eq['benchmark'] == 'SPY') & (eq['experiment_label'] == 'T1+T2+T3')].copy()
t4 = eq[(eq['benchmark'] == 'SPY') & (eq['experiment_label'] == 'T1+T2+T3+T4')].copy()

def get_max_dd(df):
    if len(df) == 0: return None, None, None
    df = df.sort_values('date')
    df['peak'] = df['equity'].cummax()
    df['dd'] = (df['equity'] - df['peak']) / df['peak'] * 100
    max_dd = df['dd'].min()
    max_dd_date = df.loc[df['dd'].idxmin(), 'date']
    peak_date = df.loc[df['equity'] == df.loc[df['dd'].idxmin(), 'peak'], 'date'].iloc[0]
    return max_dd, max_dd_date, peak_date

print(f"T3 SPY MaxDD: {get_max_dd(t3)}")
print(f"T4 SPY MaxDD: {get_max_dd(t4)}")

# QQQ T1+T2+T3
t3q = eq[(eq['benchmark'] == 'QQQ') & (eq['experiment_label'] == 'T1+T2+T3')].copy()
t4q = eq[(eq['benchmark'] == 'QQQ') & (eq['experiment_label'] == 'T1+T2+T3+T4')].copy()

print(f"T3 QQQ MaxDD: {get_max_dd(t3q)}")
print(f"T4 QQQ MaxDD: {get_max_dd(t4q)}")
