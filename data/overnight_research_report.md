# Overnight Research Report — 2026-07-07

Scope: Gemini question-labeling backfill, backtest re-run, trade/candidate analysis by
event family (train + test), event study on never-traded macro/war questions.
LLM spend this session: **$1.99 total** (gemini-2.5-flash; 5,063 questions labeled, cached
forever in `question_labels`).

---

## 1. Question labels (new dataset)

- **5,063 / 5,064 questions labeled** (candidates + Gemini-killed macro/war + raw-scan-only).
- Quality: 3 inconsistencies out of 5,063; all spot checks correct (Fed ladder → easing;
  Iran–Qatar → periphery; ceasefire → russia_ukraine; NVDA → earnings/single_stock).
  "Powell says X" junk correctly got no direction.
- Inventory: **2,903 high-materiality broad-market movers** — 1,545 war, 356 CPI,
  286 Fed-rates, 171 jobs, 124 GDP. Belligerents: 404 israel_iran, 335 russia_ukraine,
  283 us_iran, 146 iran_gulf_periphery, 8 china_taiwan.

## 2. Backtest reproducibility — Kelly configs are fragile (again)

- Re-run is byte-identical to the previous run → pipeline deterministic.
- BUT: between two earlier runs, a **pure-logging code change moved ONLY the Kelly (T3)
  configs** (non-Kelly rows identical to the cent; the T3 == T1+T3 twins split by 0.06pp).
- Treat any single-run Kelly number as one draw. Stable across all perturbations:
  **SPY T1 (+24.6% excess, Sharpe 3.3, DD better than SPY)** — still the robust config.

## 3. What the strategy trades, and what pays (labels joined to trades, deduped)

| family | stage | n | win% | avg ret | total PnL |
|---|---|---|---|---|---|
| earnings | test | 187 | 54.0 | +2.20% | +$41,243 |
| earnings | train | 115 | 53.0 | +0.31% | +$4,906 |
| war_conflict | test | 20 | 65.0 | +3.08% | +$5,142 |
| war_conflict | train | 10 | 60.0 | +0.21% | +$161 |
| fda | train | 17 | 76.5 | +4.03% | +$7,980 |
| fda | test | 3 | 0 | −3.55% | −$1,253 |

- **Gemini materiality predicts trade quality**: high = 61.3% win / +3.40% avg;
  medium = 52.4% / +0.83%. Free pre-registered filter for live.
- Broad-market-scope trades: 63.3% win vs 54.2% for single-stock.

## 4. Pool vs policy (every candidate, traded or not; `asset_return` = window move)

- **Entry selection adds nothing OOS**: test traded 59.1% hit / +2.96% vs passed
  59.6% / +3.22%. In-sample (train) selection looked great (59% vs 40%) — that skill
  did not transfer. The candidate pool itself carries the edge.
- **War pool (test): 158 candidates, 89.9% hit, +13.2% avg window move — policy traded
  12.7% of it and captured ~+3% per trade.** Largest under-harvested edge in the system.
- `geopolitics_other` test pool: 11.8% hit, −7.8% avg — **toxic, and the policy traded
  zero of them** (selection worked exactly where needed).
- FDA train pool: 70% hit, +13.3% avg (works when approval is material to company size
  and stock hasn't pre-run; failure modes: mega-cap immateriality, nano-cap sell-the-news
  — the run-up gate is blind to moves before T0, see GRCE +30% pre-entry, −50% post).

## 5. Event study — the never-traded macro/war questions (870 selected)

- **Coverage is the first finding: only 56/870 had usable CLOB price history.**
  Most Fed-ladder/CPI/jobs markets have little or no trade history → Polymarket is
  currently too illiquid in these families to source signals. Fed/CPI edge = UNKNOWN,
  not disproven. (31 threshold-crossing events total.)
- Jobs: 8 events, 5d directed +0.36%, 62.5% hit (p=0.12) — weakly positive, tiny n.
- **Russia–Ukraine: following crossings LOSES −3.34% per 5d event (n=19, t=−2.91,
  p=0.009).** Consistent with the episode-age rule from the Iran analysis: by the time a
  war probability crosses 0.70, the move is priced and reverses. (The fade of such
  crossings is a hypothesis only — not tested out-of-sample.)
- Event table: `data/macro_event_study_events.csv`.

## 6. Carry-over findings confirmed this session

- Earnings tape-correlation is **mechanical beta** (excess-vs-tape corr ≈ 0 in every
  season); no regime gate justified. The 5-day trend gate was tested and **rejected**
  (blocks +$22k of profitable trades incl. 13 golden-cluster trades; p≈0.8).
- War tape-correlation is **real countercyclical alpha** (excess corr −0.64).
- War vehicle matters: USO 81% win / +4.7% vs XLE 50% / −0.5% — trade the commodity,
  not the sector ETF.
- Episode age beats probability level for war entries across all 3 conflicts.
- Data hole: macro-pipeline trades carry placeholder Gemini scores (qc=0, ac=−1).

## 7. Subject ranking for a low-frequency swing strategy

1. **War, core conflict (US/Israel–Iran), episode onset, via USO** — strongest per-trade
   edge; currently under-harvested; never enter after saturation (prob pinned ≥0.9 for days).
2. **Earnings, high materiality, in season** — thin (+0.3–2.2%/trade) but positive and
   plentiful; the volume engine; no tape gate needed.
3. **FDA, small/mid-cap, material, no pre-run-up** — good pool when supply exists
   (supply gap in 2026Q1 unexplained; check scanner tags vs Polymarket listings).
4. **Jobs prints** — weak maybe; needs more liquid markets.
5. **Fed/CPI** — desired but currently unmeasurable on Polymarket (illiquid ladders).
6. **Avoid**: `geopolitics_other` periphery questions; late-episode war entries;
   Iran-vs-Gulf periphery via equity ETFs.

## 8. Morning decisions

- Complete-fix for the run-up gate (look back before T0)? Cheap, mechanism-based.
- Investigate FDA supply gap (scanner tags vs Polymarket listing history).
- Multi-seed CEM sweep (5 seeds ≈ 20 min) to bound the Kelly-config noise.
- Optional: extend live catalyst prompt to emit labels for new markets (zero extra calls).
