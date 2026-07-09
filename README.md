# CEM - Information Diffusion Lag Strategy

CEM is an algorithmic trading system that exploits information diffusion lag by trading Polymarket event derivatives alongside a traditional equities benchmark (SPY/QQQ).

**All live components run fully containerized on a home server. A cron job automatically pulls the latest code from GitHub every minute, triggering an automatic deployment if changes are detected.**

---

## Directory Structure

The repository has been neatly organized into specific functional domains:

* **`backtesting/`**  
  Contains the core optimization and historical simulation engine. Use `optimize_cem.py` for policy search and `scan_historical.py` for historical data analysis. Also contains scripts for downloading historical backtest data.

* **`live/`** *(formerly interactive_brokers/)*  
  The live trading bot. Connects to the Interactive Brokers API to fetch the latest NAV, sweep idle cash into SPY, and maintain the live dashboard. Runs 24/7 inside a Docker container on the server.

* **`analysis/`**  
  All statistical tests, diagnostics, and plotting scripts. Run `plot_cem_results.py` to generate equity curves and bar charts comparing the strategy against benchmarks.

* **`data_pipeline/`**  
  Scripts for fetching, cleaning, and formatting market data and probabilities. Includes deduplication and database sync tools.

* **`llm_models/`**  
  Consolidates all Claude and Gemini AI interactions. Responsible for parsing raw questions, world-building, and generating candidate asset tags.

* **`testing/`**  
  Unit tests and integration tests, including `test_ib.py`.

---

## Using Cached Data Files (.pkl & .parquet)

The system relies on cached dataset snapshots to rapidly run backtests and statistical analyses without re-querying APIs.

1. **`.parquet` files** are used for large, tabular datasets (like historical price feeds and order books) because they are highly compressed and load extremely fast into Pandas or Polars.
2. **`.pkl` files** are used for serialized Python objects (like complex dictionaries or pre-compiled strategy state) that don't fit neatly into a table.

To use them in your backtesting or analysis scripts:
```python
import pandas as pd

# Load tabular price data
prices_df = pd.read_parquet("data/historical_prices.parquet")

# Load complex state or models
import pickle
with open("data/model_state.pkl", "rb") as f:
    model = pickle.load(f)
```

---

## Live Trading & Deployment

The live system operates inside Docker containers on a home server:
1. **Trader**: A daemon that wakes up hourly to scan markets, evaluate positions, and execute trades via Interactive Brokers.
2. **Dashboard**: A web server exposing a dashboard on port 8080 showing live NAV, Stock T0→Now, recent orders, and the questions watchlist.

### Automated Continuous Deployment
You do not need to manually deploy code. A cron job on the server executes `scripts/deploy_if_changed.sh` every 60 seconds:
```bash
* * * * * cd ~/cem_clean_repo && bash scripts/deploy_if_changed.sh >> /tmp/cem_deploy.log 2>&1
```
When you push code to GitHub, the server will detect it within a minute, automatically pull the changes, and restart the Docker containers. You can watch this happen live on the server via:
```bash
tail -f /tmp/cem_deploy.log
```
