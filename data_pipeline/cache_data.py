import asyncio
import pandas as pd
import pickle
from pathlib import Path
import sys
sys.path.append('.')
from optimize_cem import load_paths

async def cache_data():
    df = pd.read_parquet("data/candidates.parquet")
    print("Fetching data from Postgres...")
    prices, probs = await load_paths(df)
    
    print("Saving to data/prices.pkl and data/probs.pkl")
    with open("data/prices.pkl", "wb") as f:
        pickle.dump(prices, f)
    with open("data/probs.pkl", "wb") as f:
        pickle.dump(probs, f)
    print("Done!")

if __name__ == "__main__":
    asyncio.run(cache_data())
