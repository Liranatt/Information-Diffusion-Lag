# CEM Information-Diffusion Trading System

This repo now contains both the original research/backtest pipeline and the live
IBKR paper-trading system. The live stack is running on `liranserver`
(`192.168.1.159`) in Docker, pulling live Polymarket probabilities plus IBKR
price bars, trading the latest walk-forward CEM policy, and serving a read-only
dashboard at:

`http://192.168.1.159:8080/`

The strategy looks for information-diffusion lag: a prediction market moves
first, then the mapped equity sometimes catches up later. We use only the data
the system actually has: tracked Polymarket questions, mapped assets, IBKR bars,
orders/fills, live positions, NAV snapshots, and the historical fold-audit CSV.

## What Is Live Now

- `trader` container: runs `python -m interactive_brokers.run_live --daemon`.
- `dashboard` container: runs `python -m interactive_brokers.dashboard`.
- IB Gateway: already running separately on the server, paper API on port `4004`.
- Postgres: shared live/backtest database on `192.168.1.159:5432`.
- Cadence: hourly control-loop ticks by default (`LIVE_TICK_SECONDS=3600`).
- Discovery: daily Polymarket Gamma scan by default (`LIVE_DISCOVERY_TICKS=24`).
- Dashboard: read-only FastAPI page on port `8080`; it never connects to IB and
  never sends orders.

## Live Control Loop

Every hourly tick:

1. Pull hourly Polymarket probability history for tracked open markets.
2. Mark resolved markets and force exits when needed.
3. During US regular market hours, pull IBKR `1 hour` and `1 day` bars for the
   benchmark, open positions, and mapped assets of tracked questions.
4. Evaluate exits.
5. Evaluate entries from the latest walk-forward policy.
6. Rotate idle cash into the benchmark index.
7. Write an hourly NAV snapshot against a passive benchmark counterfactual.
8. Write system telemetry: DB size, disk free, and Gemini spend estimates.

Trade records use full timestamps (`entry_ts`, `exit_ts`), and the dashboard/CLI
show hour-level trade timing instead of date-only views.

## Capital Safety

The live strategy is not allowed to deploy more than available capital:

- Entry sizing is capped to `cash + liquidatable benchmark value`.
- It funds trades from cash first, then sells benchmark shares.
- Event-stock buys are sized against a capped limit price:
  `reference_price * (1 + LIVE_EXECUTION_BUFFER_PCT)`.
- Benchmark rebuys, undo buys, and cash sweeps use the same affordability cap.
- If there is not enough investable capital for `LIVE_MIN_ORDER_NOTIONAL`, the
  entry loop stops.
- The system requires a paper account by default (`LIVE_REQUIRE_PAPER_ACCOUNT`).

The default execution buffer is 1% (`LIVE_EXECUTION_BUFFER_PCT=0.01`). If the
market runs through the cap, the order does not fill instead of borrowing/margining.

## Dashboard

The dashboard is a premium single-page interface inspired by the stock/portfolio
references in the screenshots. It has four views:

- Overview: NAV vs passive benchmark, allocation donut, KPIs, execution guard,
  system health, and Gemini spend.
- Portfolio: open positions, probability/stock run-up from T0, Kelly sizing,
  real commission/slippage, recent orders, closed trades, and question watchlist.
- Strategy & Backtest: walk-forward out-of-sample fold summary, fold bars, and
  the exact latest policy parameters.
- Learn: short explanations for CEM, T1-T4, Kelly, walk-forward, and the
  optimized parameters.

The dashboard deliberately does not include news/media feeds or generic watchlists.
The only watchlist is the tracked Polymarket question list with upcoming
resolution dates.

## T1, T2, T3, T4

The active live experiment is `T1+T2+T3+T4`.

- T1: friction-aware fitness. CEM penalizes policies that only work before
  transaction costs and slippage.
- T2: walk-forward optimization. Each fold trains on history up to a cutoff and
  evaluates only on the next unseen window.
- T3: half-Kelly sizing. Position size adapts from realized net trade history,
  clamped to the same live/backtest range.
- T4: priority allocation. Event-driven opportunities get priority over passive
  benchmark exposure when capital is scarce.

## CEM, Kelly, Walk-Forward

CEM means Cross-Entropy Method. The strategy has hard IF/THEN rules, so gradient
descent is not a good fit. CEM samples many parameter vectors, simulates the full
portfolio, keeps the elite performers, and refits the sampling distribution
toward them.

Kelly sizing estimates the growth-optimal bet size from realized win rate and
payoff ratio. We use half-Kelly for smoother live sizing, with min/max clamps.

Walk-forward means the reported performance is out-of-sample: train on the past,
test on the next unseen period, then roll forward. The live engine loads the
latest fold policy from `data/experiment_walkforward_folds_clean.csv` every tick,
so a new optimizer run updates production on the next hourly loop.

## Optimized Policy Parameters

The deployed policy has ten optimized parameters:

- `enter_strong`: immediate-entry probability threshold.
- `enter_floor`: lower probability floor that can trigger after persistence.
- `hold_days`: trained persistence window; live checks the current hourly point.
- `atr_mult`: ATR trailing-stop distance.
- `lock_activate`: profit level where the hard profit-lock activates.
- `theta_out`: probability invalidation exit threshold.
- `max_prob_surge`: maximum allowed probability run-up from T0 before entry.
- `max_price_runup`: maximum allowed stock run-up from T0 before entry.
- `position_size_pct`: base allocation before Kelly adjustment.
- `max_concurrent`: maximum open event positions.

## Running Locally

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Offline optimization/backtest
python optimize_cem.py
python plot_cem_results_new.py

# Live controls, if IB Gateway + Postgres are reachable
python -m interactive_brokers.run_live --status
python -m interactive_brokers.run_live --once --dry-run
python -m interactive_brokers.dashboard
```

## Server Operations

```bash
# Build and validate one dry-run tick
docker compose run --rm trader python -m interactive_brokers.run_live --once --dry-run

# Start/refresh live trader + dashboard
docker compose up -d --build

# Follow logs
docker compose logs -f trader
docker compose logs -f dashboard

# Portfolio status
docker compose run --rm trader python -m interactive_brokers.run_live --status
```

## Push-Based Deploy

The preferred live workflow is:

```bash
git push origin cem/phase0-phase1-plumbing
```

The server can poll that branch with `scripts/deploy_if_changed.sh`. When the
remote SHA changes, it hard-resets the server worktree to the pushed commit,
runs `scripts/deploy.sh`, rebuilds the image, restarts `trader` + `dashboard`,
and checks `/healthz`.

Discovery cadence is stored in Postgres, not in process memory, so a deploy
restart does not automatically burn another paid discovery run.

See `DEPLOY.md` for the full server checklist and `.env` notes.
