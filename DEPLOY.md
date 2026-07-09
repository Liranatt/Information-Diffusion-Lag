# 24/7 live paper-trading deployment

> **Actual deployment (liranserver, 192.168.1.159).** This server already runs
> the shared infra — an `ib-gateway` container (paper API on `0.0.0.0:4004`,
> `restart=always`) and Postgres (`my_traders_db`) on `5432`. So we deploy
> **only the trader daemon plus read-only dashboard**, as host-networked
> containers that reuse the existing gateway + Postgres via the `.env` values
> (`IB_PORT=4004`, `DB_HOST=192.168.1.159`). `docker-compose.yml` reflects this.
> The generic two-service instructions below are kept as reference for a
> from-scratch box.
>
> Deploy: clone repo → copy `.env` in → `docker compose run --rm trader … --once
> --dry-run` → `docker compose up -d --build`.

Runs the hourly control pipeline (`live.run_live --daemon`)
unattended on a Linux server and serves the read-only dashboard
(`live.dashboard`) on port `8080`. The host already has a
headless IB Gateway container and Postgres available on the LAN IP.

```
┌─────────────────────────────────────────────┐
│ Linux server                                 │
│                                              │
│  ┌────────────┐   4004    ┌───────────────┐  │
│  │  trader    │──────────▶│  ib-gateway   │  │
│  │  (daemon)  │           │  (paper API)  │  │
│  └─────┬──────┘           └───────────────┘  │
│        │ 5432                                 │
│  ┌─────▼──────┐    8080    ┌──────────────┐  │
│  │ Postgres   │◀───────────│  dashboard   │  │
│  │            │            │  (read-only)  │  │
│  └────────────┘            └──────────────┘  │
└─────────────────────────────────────────────┘
```

## 1. Host prerequisites

- Docker Engine + Compose plugin (`docker compose version`).
- Postgres running natively on the host, already loaded with the live tables
  (you have 171 tracked markets in it today).

## 2. Let the container reach the host Postgres

The trader connects to `host.docker.internal`, which resolves to the Docker
bridge gateway (typically `172.17.0.1`). Postgres must listen on that interface
and allow the Docker subnet:

`postgresql.conf`:
```
listen_addresses = 'localhost,172.17.0.1'
```

`pg_hba.conf` (add a line; adjust the subnet if your bridge differs):
```
host    <DB_NAME>   <DB_USER>   172.16.0.0/12    scram-sha-256
```
Then `sudo systemctl reload postgresql`. Confirm the bridge IP with
`docker network inspect bridge | grep Gateway`.

## 3. Secrets: the single `.env` file

**One file holds every secret** the stack needs: the repo-root `.env`. Both
containers read it — the trader via `env_file: .env` (DB creds, `IB_CLIENT_ID`,
the `my_traders_api_key` Gemini key), and the compose file interpolates
`${TWS_USERID}` / `${TWS_PASSWORD}` from it for the Gateway. Nothing else needs
copying.

`.env` is git-ignored, so **cloning the repo on the server does NOT bring it** —
you must copy it there yourself. From this machine:

```bash
# 1. Add the paper Gateway login to .env (the Gemini + DB keys are already in it):
#      TWS_USERID=<your IBKR paper username>
#      TWS_PASSWORD=<your IBKR paper password>

# 2. Copy .env to the repo root on the server, over SSH:
scp .env <user>@<server>:/path/to/cem_clean_repo/.env

# 3. On the server, lock it down (it holds live secrets):
ssh <user>@<server> 'chmod 600 /path/to/cem_clean_repo/.env'
```

Notes:
- `IB_HOST`, `IB_PORT`, `DB_HOST` in `.env` stay at their local values
  (127.0.0.1 / 4002 / localhost) for running locally. The compose file
  overrides them for the container — `python-dotenv` runs with
  `override=False`, so the container environment wins.
- **Discovery runs and costs money.** The Gemini key (`my_traders_api_key`) is
  present, so the daily discovery stage (`LLM/gemini_client.py`) works and
  will scan + classify + asset-map new Polymarket markets **every day the
  daemon is up** — recurring paid API calls by design. Watch the spend.

## 4. Build and validate BEFORE going live

```bash
# Build images and start the Gateway (leave trader down for the dry run).
docker compose up -d --build ib-gateway

# Wait until the Gateway is healthy (auto-login can take 1-2 min).
docker compose ps

# One full tick, NO orders sent. Read the output: probs should load and a NAV
# snapshot should write. (On tick 1 discovery runs — a real Gemini call.)
docker compose run --rm trader python -m live.run_live --once --dry-run

# One real paper tick, during US market hours (09:30-16:00 ET):
docker compose run --rm trader python -m live.run_live --once

# Inspect portfolio state:
docker compose run --rm trader python -m live.run_live --status
```

## 5. Go 24/7

```bash
docker compose up -d --build      # starts trader + dashboard
docker compose logs -f trader     # follow the hourly ticks
docker compose logs -f dashboard  # dashboard server logs
```

The daemon ticks hourly forever. The dashboard is read-only at
`http://192.168.1.159:8080/`. Both services survive host reboots/crashes via
`restart: unless-stopped`, and the daemon tolerates Gateway reconnects between
ticks.

## 5b. Push-based redeploy

The server repo includes two deployment helpers:

```bash
bash scripts/deploy.sh             # compile, rebuild, restart, health-check
bash scripts/deploy_if_changed.sh  # fetch branch, deploy only if SHA changed
```

Recommended user crontab on `liranserver`:

```cron
* * * * * APP_DIR=/home/liranatt/cem_clean_repo DEPLOY_BRANCH=cem/phase0-phase1-plumbing /bin/bash /home/liranatt/cem_clean_repo/scripts/deploy_if_changed.sh >> /home/liranatt/cem_clean_repo/data/deploy.log 2>&1
```

The deploy wrapper uses `git reset --hard origin/<branch>` by design; keep
server-only secrets in `.env` and out of Git. The live discovery cadence is
persisted in Postgres (`live_runtime_state`), so a container restart after a
push does not automatically run paid discovery again.

## 6. Keep walking forward (policy updates)

The live engine always trades the **latest walk-forward fold** for
`T1+T2+T3+T4` / `SPY`, read from
`data/experiment_walkforward_folds_clean.csv`. To roll the policy forward,
re-run `optimize_cem.py` on the host with fresh resolved events; it rewrites
that CSV, and the daemon picks up the new last-fold policy on its **next tick**
— no restart needed (the `data/` dir is bind-mounted).

## 7. Paper-account checklist (one-time, in IBKR)

- Fractional-share trading **enabled** (benchmark legs use fractional SPY/QQQ;
  `LIVE_FRACTIONAL_BENCHMARK=true`).
- US market-data subscription active (delayed is fine for paper).
- Account is a paper account (`DU...`) — the paper guard hard-blocks anything
  that doesn't look like one.

## 8. Things to expect

- **First RTH tick sweeps all idle cash into SPY** (fully-invested rotation
  model), then rotates into event positions as signals fire.
- **~104 IB historical-data requests/tick** (benchmark + ~51 mapped assets ×
  hourly+daily). Near IB's pacing limit — the first minute of each tick is
  slow; retries absorb pacing warnings.
- Port mapping note: `ghcr.io/gnzsnz/ib-gateway` forwards the paper API to
  socat port **4004** internally. If a future image tag changes this, update
  `IB_PORT` in `docker-compose.yml` and the healthcheck. Confirm with
  `docker compose logs ib-gateway`.
