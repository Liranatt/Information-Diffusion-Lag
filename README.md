# CEM — Information-Diffusion-Lag Strategy

A rule-based event strategy that trades a lag in information diffusion: when a
**Polymarket** prediction market re-prices sharply on a catalyst, the
fundamentally-exposed **US equity** often re-prices more slowly. Polymarket's
probability is used as an upstream *oracle* — the trade is on the delayed equity
move, not on Polymarket itself. Entry/exit rules are optimized with the
**Cross-Entropy Method (CEM)** on a leakage-safe walk-forward split.

The point of the project is **selection quality** — turning the Polymarket
catalyst stream into a clean, tradable set of equity candidates — not
market-beating alpha. Performance is reported honestly (see the dashboard's
statistics panel), and a single leakage-safe window is not treated as proof of
long-run edge.

The live system runs on a home server against an **IBKR paper account**, with a
read-only web dashboard.

## The idea pipeline — how a Polymarket market becomes a trade

```
Polymarket Gamma scan  (financial / macro / geo tags, resolution 5–60 days out)
  → bracket dedup            collapse "Fed cuts 25/50/75bps" ladders to one event
  → regex prefilter (free)   cheap keyword gate, culls noise before paid API calls
  → Gemini catalyst gate     is this a real, tradable financial catalyst?
  → Gemini asset mapping      which US equities/ETFs, by what causal channel,
                              with what connection strength (relevance)
  → point-in-time candidate  per (market, symbol): T0 / Tθ / Te with no path leakage
  → CEM policy search         sample rule vectors, simulate the portfolio, keep elites
  → walk-forward held-out     expanding out-of-sample folds; OOS boundary 2026-01-01
  → live                      hourly tick trades the latest fold's policy on IBKR paper
```

**Point-in-time boundaries** (the leakage guard) for each candidate:

- **T0** — market creation. The since-T0 features (probability surge, asset
  run-up) are measured from here.
- **Tθ** — the first day the daily YES-probability crosses **0.55**. This is only
  the *universe anchor* — the point at which a candidate begins to exist. It is
  **not** an entry rule; entry is governed purely by the CEM policy.
- **Te** — the market's resolution date. Trades are forced out by `Te − 1 day`.

Every simulation truncates price/probability paths at its own decision horizon,
and walk-forward folds only fit on candidates whose outcome (`t_e`) completed
before the fold starts — so a fit can never see the future.

## Strategy mechanics

The book is **long-only on raw P(YES)**: entry fires on *high* probability, and
the mapped equity is always bought (never shorted). The CEM searches these knobs:

- **Entry** — `prob ≥ enter_strong` fires immediately; otherwise `prob ≥
  enter_floor` must hold for `hold_days` consecutive daily points. Two gates at
  entry: probability surge since T0 ≤ `max_prob_surge`, and asset run-up since
  T0 ≤ `max_price_runup`.
- **Exit** — probability invalidation (`prob < theta_out`), an ATR trailing stop
  (`atr_mult`), a profit-lock hard floor (once peak ≥ `lock_activate`), or the
  `Te − 1 day` resolution cut.

**The four tiers the CEM can stack** (production runs all four, `T1+T2+T3+T4`):

- **T1 — friction penalty.** Realized transaction-cost penalty inside the
  fitness, so policies that only look good before costs are rejected.
- **T2 — walk-forward.** Expanding out-of-sample folds: fit up to a cutoff, test
  on the next unseen window; the live trader always runs the latest fold's policy.
- **T3 — half-Kelly sizing.** Position size scales with the rolling empirical
  win-rate and payoff ratio (clamped to 5–20% of equity).
- **T4 — event priority.** Allocation mode that prioritizes geo → macro →
  earnings events when competing for the concurrent-position budget.

CEM fitness: `score = daily_Sharpe − 0.30 · |max_drawdown| − 2.0 · friction_fail_rate`.

## Architecture — one kernel, two planes

The **backtest** and the **live trader** share a single, plane-agnostic core so
they can never trade different rules:

- **`core/`** (pure — no DB, no network): `policy` (the parameter space + Kelly
  bounds), `features` (all `feat_*` math), `kernel` (the entry/exit semantics in
  **two implementations**: a numba fast path the CEM search drives thousands of
  times, and a pure-Python reference the live trader uses — a parity test keeps
  them byte-identical).
- **Backtest** (`backtesting/optimize_cem.py`) reads *only* the committed
  artifacts `data/candidates.parquet` + `prices.pkl` + `probs.pkl` — no Postgres.
- **Live** (`live/`) *replays* the same kernel each tick over the data it has so
  far, so entries/exits match the backtest by construction.

Data flows through Postgres (schema `checking_relevant_events`): the ingest
chain and the live trader write market/price/probability history there
(append-only — `ON CONFLICT DO NOTHING`, never overwriting), and a nightly job
rebuilds the parquet/pkl artifacts from it.

## Repository layout

| Path | What it is |
|------|------------|
| **`core/`** | Shared, pure kernel: `policy`, `features`, `kernel` (numba fast path + Python reference, long-only). |
| **`ingest/`** | The one data-cleaning chain: `scanner` (Gamma API), `dedup`, `prefilter` (free regex), `gemini_client` + `world` (Gemini catalyst gate + asset mapping), `evaluator`, `chain` (orchestrator: `backfill` / `discover_and_clean`), `artifacts` (rebuild parquet+pkl from the DB), `migrate_daily_bars`, `__main__` (CLI). |
| **`backtesting/`** | `optimize_cem.py` (CEM search + walk-forward), `seed_sweep.py`, and `pipeline/` (`strategy` = backtest helpers over `core`, `data_loader` = DB loaders, `trade_forensics`). |
| **`live/`** | The paper trader. `run_live.py` (entry point), `control_pipeline.py` (the hourly tick), `strategy_engine.py` (kernel replay), `order_manager`, `position_manager`, `data_fetcher` (IB bars), `database` (`LiveStore`), `policy` (fold policy + Kelly), `connection` (IB), `config`, `utils`, `analytics` (dashboard stats), `dashboard.py` (read-only FastAPI, `:8080`), `health_check`, `db_summary`. |
| **`database/`** | `db_connection.py` (asyncpg pool) and `database/backtesting/` (`schema.py` → the `SCHEMA` name, `market_data` = yfinance, `polymarket` = CLOB history). |
| **`analysis/`, `diagnostics/`** | Research/plots run by hand (statistical tests, attribution). Some still carry stale imports from the reorg. |
| **`testing/`** | `test_c0_invariants.py` (kernel/feature semantics), `test_live_no_margin.py`, `test_ib.py`. |
| **`docker/`, `scripts/`** | Container build + compose, and the deploy / nightly-rebuild scripts. |
| **`data/`** | Committed artifacts `candidates.parquet`, `prices.pkl`, `probs.pkl` (travel with the code). Policy CSVs (`experiment_walkforward_folds_clean.csv`, `experiment_results_clean.csv`, `backtest_equity_log.csv`) are also tracked; other `*.csv`/logs are ignored. |

## Running it

Install once (editable, so everything imports absolutely — no `sys.path` hacks):

```bash
pip install -e .
```

```bash
# ── Data ingestion ─────────────────────────────────────
python -m ingest --rebuild                 # rebuild parquet+pkl from the DB (free)
python -m ingest --backfill                # historical scan → clean → download (paid: Gemini)
python -m ingest --live                    # one live discovery pass (paid: Gemini)

# ── Backtest / optimize (reads the committed artifacts only) ──
python -m backtesting.optimize_cem         # CEM policy search + walk-forward

# ── Live paper trading (needs IB Gateway paper + .env) ──
python -m live.run_live --once --dry-run   # single tick, no orders — validate
python -m live.run_live --status           # print portfolio state
python -m live.run_live --daemon           # hourly control loop
python -m live.dashboard                   # read-only dashboard on :8080

# ── Tests ──────────────────────────────────────────────
pytest testing/
```

`--backfill` and `--live` make paid Gemini calls (catalyst gate + asset mapping);
`--rebuild` is free (DB reads + file writes only).

The live NAV is reconciled from a **cash ledger** (`start equity − net filled
buys − commissions + market value`), not IB's `NetLiquidation`, so it is immune
to IB paper-account ghost-fill inflation (`live/database.py:reconciled_cash`).

## Working hours

Everything the live trader does is gated to a single **data-gathering window,
09:30–16:30 US-Eastern = 16:30–23:30 Israel** (a constant 7-hour offset,
year-round). Outside it — overnight and weekends — every tick is a no-op.

- **Hourly tick** (`LIVE_TICK_SECONDS=3600`): pull fresh Polymarket
  probabilities for tracked markets, refresh IB bars, mark resolutions,
  snapshot NAV. **Trading (entries/exits) only fires 09:30–16:00 ET**, when the
  equity market can actually fill an order; the extra half-hour to 16:30 is for
  capturing the session's closing bars and probabilities.
- **Daily discovery** (every 24 ticks): run the ingest cleaning chain over the
  next 5–60-day resolution window and start tracking whatever passes. Cadence is
  persisted in Postgres, so a restart never re-triggers a paid discovery.
- **Nightly rebuild** (~00:00, on the server): regenerate the parquet/pkl
  artifacts from the DB and push them, so the committed data and a fresh clone
  stay reproducible.

## Home-server deployment

Everything live runs in Docker on the home server (`liranserver`,
`192.168.1.159`), reusing infrastructure that is already up: an `ib-gateway`
container (paper API on `:4004`) and Postgres (`my_traders_db` on `:5432`). The
compose stack (`docker/docker-compose.yml`) is two host-networked services:

- **`trader`** — `python -m live.run_live --daemon` (hourly control loop).
- **`dashboard`** — `python -m live.dashboard` (read-only, `:8080`).

Both bind-mount the repo over `/app`, so a `git pull` and refreshed policy CSVs
propagate without an image rebuild. Secrets live in the server's `.env` (read via
`load_dotenv`); `scp` it over when re-cloning. See `DEPLOY.md` for the full
topology.

### Continuous deployment

A per-minute cron runs `scripts/deploy_if_changed.sh`: it fetches `origin/main`
and, if the remote moved, **fast-forwards or rebases** onto it (never discarding
the server's own nightly data commits) and runs `scripts/deploy.sh`
(`py_compile` gate → `docker compose up -d --build` → `/healthz` check). A
second cron runs `scripts/nightly_rebuild.sh` (~00:00) under the *same* flock, so
the nightly artifact rebuild and a deploy never interleave.

```cron
* * * * *  cd ~/cem_clean_repo && bash scripts/deploy_if_changed.sh >> /tmp/cem_deploy.log 2>&1
7 0 * * *  cd ~/cem_clean_repo && bash scripts/nightly_rebuild.sh   >> /var/log/cem_nightly.log 2>&1
```

**A push to `main` auto-deploys to the live trader within ~60s — treat `main` as
production.** The monthly workflow: re-run `optimize_cem`, review the new policy,
commit the refreshed policy CSVs, push.

## Dashboard

`http://<server>:8080/` (HTTP Basic auth, `DASHBOARD_PASSWORD` in `.env`). It is
read-only and never trades. Alongside live portfolio state, positions, orders,
and the reconciled NAV curve, the **Strategy** tab shows a statistics panel
comparing the out-of-sample backtest and live: Sharpe, excess over benchmark,
max drawdown, mean/median trade return, win rate, and a one-sided significance
test (t-stat, p-value, bootstrap CI). Significance is only claimed with a real
p-value *and* enough trades; thin or non-significant results are footnoted, never
headlined.
