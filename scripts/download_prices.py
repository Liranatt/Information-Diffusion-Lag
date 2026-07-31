import pickle
from pathlib import Path
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

def download_prices():
    # 1. Get unique symbols from candidates
    candidates_path = DATA_DIR / "candidates.parquet"
    print(f"Loading candidates from {candidates_path}")
    df = pd.read_parquet(candidates_path)
    symbols = set(df["symbol"].dropna().unique())
    symbols.update({"SPY", "QQQ"})
    
    print(f"Found {len(symbols)} unique symbols to download.")
    
    prices = {}
    total = len(symbols)
    
    for i, s in enumerate(sorted(symbols)):
        try:
            # Download a wide range to cover all backtest dates
            data = yf.download(s, start="2020-01-01", end="2027-01-01", progress=False, auto_adjust=False)
            if data is None or data.empty:
                print(f"[{i+1}/{total}] {s}: No data found.")
                continue
            
            # Handle yfinance multi-index columns if present
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)
                
            path = []
            for ts, row in data.iterrows():
                try:
                    t = pd.Timestamp(ts)
                    t = (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).normalize()
                    path.append((
                        t,
                        float(row["Open"]),
                        float(row["High"]),
                        float(row["Low"]),
                        float(row["Close"])
                    ))
                except Exception:
                    continue
            
            if path:
                prices[s] = path
            print(f"[{i+1}/{total}] {s}: downloaded {len(path)} bars.")
            
        except Exception as e:
            print(f"[{i+1}/{total}] {s} failed: {e}")
            
    out_path = DATA_DIR / "prices_1.pkl"
    with open(out_path, "wb") as f:
        pickle.dump(prices, f)
        
    print(f"\nSuccessfully saved {len(prices)} symbols to {out_path}")

if __name__ == "__main__":
    download_prices()
