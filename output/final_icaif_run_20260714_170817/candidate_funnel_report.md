# Candidate funnel — exact counts from artifacts (2026-07-14)

Method: every number below was computed directly from the named artifact with the
stated columns/filters (pandas over the committed parquets, JSON caches, and scan
logs). Nothing is taken from the manuscript. The Postgres database
(192.168.1.159:5432, schema `checking_relevant_events`) was attempted with an 8s
timeout and is unreachable from this machine, so the three upstream stages that
live only in DB tables are reported from the surviving offline-scan caches and
explicitly labeled as such.

## The two provenances (must not be conflated)

- **Committed-universe funnel** (what actually produced the paper's artifact):
  original historical ingestion runs recorded in DB tables
  `historical_run_markets`, `historical_run_market_decisions`,
  `historical_asset_worlds`, `historical_asset_world_assets`. **Unreachable —
  counts for stages 1–3 of this provenance cannot be computed locally.**
- **Offline re-enumeration** (2026-07-12 session scratchpad
  `...\bbdc1827-...\scratchpad\`): a full Gamma API rescan over 2024-07-01 →
  2026-05-27 using the same `ingest.scanner.fetch_markets_in_range` +
  `ingest.dedup.dedup_markets` code. It measures the same historical period with
  the current filters but is NOT the run that generated the committed parquet.

## Funnel

**1. Total raw Polymarket market records scanned**
- Committed provenance: **unavailable locally** (needs
  `SELECT count(*) FROM checking_relevant_events.historical_run_markets` or the
  per-run scan logs).
- Offline re-enumeration: **39,545** unique `market_id`s returned by the Gamma
  API for the allowed tag slugs with `end_at` in [2024-07-01, 2026-05-27]
  (source: `scan_cache.log`, cumulative line "2026-05-14..2026-05-27 -> 39545";
  the count is the size of the market_id-keyed dict, i.e., unique markets).

**2. After duration + deterministic structural filters**
- Ladder/date-option dedup at scan level (`ingest.dedup.dedup_markets`):
  **12,166** markets (source: `scanned_markets.json`, len = 12,166; log line
  "CACHED 12166 deduped markets").
- Deterministic structural-noise filters (`ingest/prefilter.py` patterns):
  **6,425** remaining, 2,997 removed (source: `noise_test2.txt`, "NEW gate MINUS
  structural noise: 6425", "TOTAL removed: 2997"). A second-round figure (5,834
  per commit 76a27be's session) left no surviving output artifact — not verified
  here.
- **Duration filter caveat (important):** the scanner's 5–60-day rule is applied
  at *discovery time* in the live path (`fetch_active_markets`: end between
  now+5d and now+60d). The historical range fetch used above filters by
  `end_at` window + allowed tags, not by duration. The current code
  (`ingest/artifacts.py`, commit 3eff022, 2026-07-11) additionally enforces
  5 ≤ (t_e − t_θ) ≤ 60 days at artifact build — **but the committed parquet was
  last committed 2026-07-09, i.e., built BEFORE that filter existed, and it
  violates it**: in `data/candidates.parquet`, computing
  `(t_e − t_theta)` in days gives 1,178/1,293 rows inside [5, 60], **105 rows
  below 5 days and 10 rows above 60**; in the final clean artifact,
  1,089/1,182 inside, **83 below / 10 above**. The 5–60 t_θ→t_e window is
  therefore NOT a property of the paper's universe as committed; whether each
  row satisfied the original *scan-time* duration rule requires the DB.

**3. Unique relevant contracts after the Gemini relevance gate, before asset
mapping (distinct `market_id`)**
- **Unavailable locally** for the committed provenance (needs
  `SELECT count(DISTINCT market_id) FROM checking_relevant_events.historical_asset_worlds`).
- Local lower bound from the committed artifact (post-gate AND post-mapping AND
  post-feature-validity): **1,129** distinct `market_id` in
  `data/candidates.parquet` (column `market_id`, normalized as string). This
  undercounts stage 3 because gate-passing contracts that mapped to no asset or
  failed downstream data checks never reach the parquet.

**4. Unique normalized question families after duplicate/date-option collapse**
(committed artifacts, exact):
- `data/candidates_audit_annotated.parquet` (post exact-dedup, 1,257 rows):
  **1,010** distinct `economic_event_id` (audited `audit_group`/`economic_event_group`
  where reviewed, else `source:<event_id or market_id>`;
  built in `core/candidate_cleaning.py` lines 223–228).
- Final `data/candidates_audit_clean.parquet`: **972** distinct
  `economic_event_id` (matches `primary_economic_events` in
  `data/candidate_cleaning_summary.json`).
- For reference under other definitions of "family": 693 distinct normalized
  question strings (lower-cased/stripped `question`) in the clean artifact;
  609 families under the scan-level `ingest.dedup.event_key` normalization;
  1,013 distinct Polymarket `event_id` in the source parquet.

**5. Final question–asset candidate rows after mapping and manual review**
- `data/candidates_audit_clean.parquet`: **1,182 rows — verified**, equal to
  1,182 distinct `(market_id, symbol)` pairs, with `cem_eligible == True` for
  every row (filter: `treatment_action ∈ {primary, primary_controlled,
  unreviewed_passthrough}` and not `Incorrect endpoint`).
- Reconciliation (source: `data/candidate_cleaning_summary.json` +
  direct parquet counts): 1,293 source rows → 36 exact-duplicate excess rows
  removed → 1,257 annotated → 75 rows removed by the audit (54 quarantined,
  11 secondary-only, 10 excluded/endpoint-invalid) → **1,182**.
  Distinct `market_id` in the final artifact: 1,077; distinct symbols: 426.

## Missing-artifact list (for the DB pass, when the server is reachable)

```sql
-- stage 1 (committed provenance)
SELECT count(DISTINCT market_id) FROM checking_relevant_events.historical_run_markets;
-- stage 2 (per-decision prefilter outcomes)
SELECT decision, count(*) FROM checking_relevant_events.historical_run_market_decisions GROUP BY 1;
-- stage 3 (post-Gemini, pre-mapping)
SELECT count(DISTINCT market_id) FROM checking_relevant_events.historical_asset_worlds;
```
