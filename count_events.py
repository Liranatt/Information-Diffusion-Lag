import pandas as pd

df = pd.read_parquet('data/candidates.parquet')
print(f'Total Events: {len(df)}')
df['t_e'] = pd.to_datetime(df['t_e'], utc=True)
df['month'] = df['t_e'].dt.to_period('M')
monthly_counts = df['month'].value_counts().sort_index()
print("Event distribution by month:")
for month, count in monthly_counts.items():
    print(f"{month}: {count} events")

