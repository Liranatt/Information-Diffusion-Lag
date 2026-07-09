# CEM — Information-Diffusion-Lag Strategy

A rule-based event strategy that trades a lag in information diffusion: when a
**Polymarket** prediction market re-prices sharply on a catalyst (e.g. an
earnings beat), the fundamentally-exposed **equity** often re-prices more
slowly. Polymarket's probability is used as an upstream *oracle* — the trade is
on the delayed equity move, not on Polymarket itself. The trading rules are
optimized with the **Cross-Entropy Method (CEM)** and validated on a
leakage-safe held-out window.

The live system runs 24/7 on a home server against an **IBKR paper account**,
alongside a read-only web dashboard.

## Results (leakage-safe test, Jan–May 2026)

Selected policy **T1+T3**, optimized independently against each benchmark:

| Universe | Strategy | Buy & hold | Excess | Sharpe (vs bench) | Max DD | Win rate | Trades |
|----------|---------:|-----------:|-------:|:-----------------:|-------:|---------:|-------:|
| SPY      | +14.43%  | +9.83%     | +4.60 pp | 2.53 (1.82)     | −5.68% | 61.5%    | 65     |
| QQQ      | +20.39%  | +18.97%    | +1.42 pp | 2.81 (2.51)     | −6.59% | 63.1%    | 65     |

687 test / 430 train candidates, all returns net of IB commissions, SEC fees,
and 5 bp slippage. This passes an initial leakage-safe test; it does **not** yet
establish stable long-run alpha (needs more independent event windows).

## How it works

```
Polymarket Gamma scan
   → regex prefilter (financial event tags, resolution window)
   → Gemini catalyst filter (is this a tradable financial event?)
   → Gemini asset mapping (which US equities, causal channel, connection strength)
   → point-in-time candidate dataset (T0 / Tθ / Te boundaries, no path leakage)
   → CEM policy search (sample rule vectors, simulate the portfolio, keep elites)
   → walk-forward / held-out test
   → live: hourly tick trades the latest fold's policy on IBKR paper
```

**The four tiers** the CEM can stack:

- **T1 — friction penalty.** Realized transaction-cost penalty inside the
  fitness, so policies that only look good before costs are rejected.
- **T2 — walk-forward.** Expanding out-of-sample folds: fit up to a cutoff, test
  on the next unseen window.
- **T3 — half-Kelly sizing.** Position size scales with the rolling empirical
  win-rate and payoff ratio via a fractional-Kelly multiplier.
- **T4 — event priority.** Allocation mode that prioritizes event-driven
  positions over the passive benchmark.

CEM fitness: `score = daily_Sharpe − 0.30 · |max_drawdown| − 2.0 · friction_fail_rate`.

## Repository layout

| Path | What it is |
|------|------------|
| **`backtesting/`** | CEM optimization & historical simulation. `optimize_cem.py` (policy search), `scan_historical.py` (build the point-in-time candidate dataset), `download_backtest_data.py`, `seed_sweep.py`. |
| **`backtesting/pipeline/`** | The backtest engine: `scanner` (Gamma API), `data_loader`, `strategy` (CEM policy + `run_backtest`), `portfolio_manager`, `sim_kernel`, `evaluator`, `walkforward`, `trade_forensics`, `polarity_audit`. |
| **`live/`** | The live paper trader (formerly `interactive_brokers/`). `run_live.py` (entry point), `control_pipeline.py` (the hourly tick), `dashboard.py` (read-only FastAPI dashboard, port 8080), plus `config`, `connection` (IB), `data_fetcher`, `database` (`LiveStore`), `order_manager`, `position_manager`, `policy` (Kelly + policy loader), `strategy_engine`, `utils`, `health_check`, `db_summary`. |
| **`llm_models/`** | Gemini & Claude interactions. `gemini_client.py` (used live for daily discovery), `build_world`, `build_claude_candidates`, `run_claude_pipeline`, `label_polarity`, `label_questions`. |
| **`database/`** | `db_connection.py` (asyncpg pool) and `database/backtesting/` (`schema.py` → the `SCHEMA` name, `market_data`, `polymarket`). |
| **`analysis/`** | Plots & statistics run by hand: `plot_cem_results`, `generate_paper_charts`, `statistical_tests`, `build_oos_diagnostics`, `analyze_selection_order`, `analyze_trade_forensics`. |
| **`data_pipeline/`** | One-off data-prep scripts (macro market scan, dedup, caching, probability backfill). |
| **`diagnostics/`** | Ad-hoc investigations (T3-vs-T4, loss attribution, raw-expectation tests). |
| **`testing/`** | `test_ib.py`, `test_polarity.py`, `test_c0_invariants.py`. |
| **`docker/`**, **`scripts/`** | Container build + compose, and the deploy scripts. |
| **`data/`** | Cached datasets: `candidates.parquet`, `prices.pkl`, `probs.pkl`. Policy CSVs (`experiment_*_clean.csv`) are git-ignored and live only on the server. |

## Running it

```bash
# ── Backtest / optimize ────────────────────────────────
python -m backtesting.scan_historical      # (re)build the candidate dataset
python -m backtesting.optimize_cem         # CEM policy search + walk-forward

# ── Live paper trading (needs IB Gateway paper + .env) ──
python -m live.run_live --once --dry-run   # single tick, no orders — validate
python -m live.run_live --status           # print portfolio state
python -m live.run_live --daemon           # 24/7 hourly loop
python -m live.dashboard                   # read-only dashboard on :8080

# ── Tests ──────────────────────────────────────────────
pytest testing/
```

The live NAV is reconciled from a **cash ledger** (`start equity − net filled
buys − commissions + market value`), not IB's `NetLiquidation`, so it is immune
to IB paper-account ghost-fill inflation. See `live/database.py:reconciled_cash`.

## Deployment

Everything live runs in Docker on the home server (`liranserver`,
`192.168.1.159`), reusing infrastructure that is already up: an `ib-gateway`
container (paper API on `:4004`) and Postgres (`my_traders_db` on `:5432`). The
compose stack is two host-networked services:

- **`trader`** — `python -m live.run_live --daemon` (hourly control loop).
- **`dashboard`** — `python -m live.dashboard` (read-only, `:8080`).

Secrets and policy CSVs live in the server's `.env` and `data/` (git-ignored) —
`scp` them over when re-cloning; the app reads `.env` via `load_dotenv`.

### Continuous deployment

A cron on the server runs `scripts/deploy_if_changed.sh` every minute:

```cron
* * * * * cd ~/cem_clean_repo && bash scripts/deploy_if_changed.sh >> /tmp/cem_deploy.log 2>&1
```

It fetches `origin/main`; if the remote moved, it `git reset --hard`s to it and
runs `scripts/deploy.sh` (which `py_compile`s the live modules, rebuilds the
compose stack, and health-checks `/healthz`). **A push to `main` auto-deploys to
the live trader within ~60s** — so treat `main` as production.
