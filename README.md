# Information Diffusion Lag: Trading Strategy Optimization

This repository contains the codebase for optimizing and backtesting a rule-based trading strategy that exploits information diffusion lag between prediction markets (Polymarket) and traditional equity markets.

## Project Structure

The codebase is built on a modular simulation pipeline and features a robust optimization engine:

*   **`optimize_cem.py`**: The core optimization engine. It uses the Cross-Entropy Method (CEM) to search a 10-dimensional parameter space to find the optimal trading policy. It features Walk-Forward optimization, Dynamic Kelly capital sizing, and a Friction-Aware fitness objective.
*   **`plot_cem_results.py` & `plot_cem_results_new.py`**: Visualization scripts to generate equity curves, drawdown charts, and portfolio allocation plots from the CEM output logs.
*   **`pipeline/`**: The core strategy and simulation kernel.
    *   `strategy.py`: Defines the 10-dimensional heuristic ruleset and signal generation logic.
    *   `sim_kernel.py`: High-performance execution engine.
    *   `walkforward.py`: Handles the expanding-window training methodology.
*   **`database/`**: Handles connections and schema structures for reading historical asset prices and Polymarket probability data.
*   **`data/`**: Output directory for backtest trade logs, equity curves, and candidate datasets (`candidates.parquet`).

## Methodology Highlights

This strategy is evaluated against a passive Buy-and-Hold benchmark (SPY/QQQ) using three primary architectural constraints:

1.  **Walk-Forward Optimization**: Continuous training using an expanding window to ensure results are out-of-sample (OOS) and free from look-ahead bias.
2.  **Dynamic Kelly Sizing**: Real-time position sizing based on empirical rolling win rates and risk-reward ratios.
3.  **Friction-Aware Fitness**: A custom CEM fitness objective that explicitly penalizes policies that fail to overcome transaction costs and slippage:
    `Sharpe Ratio - (0.30 x Max DD) - (2.0 x Friction Failure Rate)`

## Setup and Usage

Dependencies and environment variables are expected to be managed via `.env`.
To run the main optimization:
```bash
python optimize_cem.py
```
To generate visualization plots after a successful optimization run:
```bash
python plot_cem_results_new.py
```
