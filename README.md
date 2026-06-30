# Information Diffusion Lag: Trading Strategy Optimization

This repository contains the complete, self-contained codebase for optimizing and backtesting a rule-based trading strategy that exploits information diffusion lag between prediction markets (Polymarket) and traditional equity markets.

## The Strategy Pipeline

The pipeline is engineered to rigorously search for and validate deployable trading edges using a highly modular architecture:

### 1. Data Ingestion & Offline Availability
This repository comes **fully pre-loaded with all required data**. We have queried a massive historical database to isolate the exact market candidates, equity price bars, and prediction probability trajectories needed for this experiment. 
*   **`data/candidates.parquet`**: Contains the filtered, high-quality event candidates derived from our larger database.
*   **`data/prices.pkl` & `data/probs.pkl`**: Pre-cached historical equity prices and Polymarket probability curves. 

Because this data is bundled directly in the `data/` folder, **you do not need a database connection to run this**. The pipeline will automatically load these local files, ensuring a seamless, plug-and-play reproduction of our experiments.

### 2. Core Execution Kernel (`pipeline/sim_kernel.py`)
At the heart of the project is a vectorized, high-performance simulation kernel. It processes the raw probability and price trajectories for every candidate event and applies our 10-dimensional trading heuristic ruleset (`pipeline/strategy.py`). This kernel handles everything from conditional entry triggers (e.g., probability floors) to dynamic trailing exits.

### 3. Cross-Entropy Method Optimization (`optimize_cem.py`)
Because our trading logic relies on hard, non-differentiable IF/THEN constraints, we cannot use standard backpropagation to learn the best parameters. Instead, we use the **Cross-Entropy Method (CEM)**. 
The optimizer iteratively samples policy parameter vectors, simulates the entire portfolio, and updates its sampling distribution based on the "elite" performing policies. 

### 4. Advanced Experimental Constraints
To ensure our results reflect true deployable edge rather than in-sample overfitting, the flagship configuration (T1) strictly enforces three constraints:
1.  **Walk-Forward Optimization**: The policy is continuously re-trained using an expanding window, generating strictly out-of-sample (OOS) testing results.
2.  **Dynamic Kelly Sizing**: Capital allocation dynamically scales in proportion to the strategy's empirical rolling win rate and risk-reward ratio.
3.  **Friction-Aware Fitness**: The CEM optimizer maximizes a custom objective that actively subtracts transaction costs and slippage:
    `Sharpe Ratio - (0.30 x Max DD) - (2.0 x Friction Failure Rate)`

## Setup and Usage

### Prerequisites

- Python 3.10+

### Installation

```bash
git clone https://github.com/Liranatt/Information-Diffusion-Lag.git
cd cem_clean_repo
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
pip install -r requirements.txt
```

### Running

All data is pre-packaged. To run the full Cross-Entropy Method optimizer and generate trading logs:
```bash
python optimize_cem.py
```

Once the optimization completes, you can visualize the results (equity curves, drawdown charts, portfolio allocations) by running:
```bash
python plot_cem_results_new.py
```
