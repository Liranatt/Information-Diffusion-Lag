# Causal Event–Dislocation Engine (CEDE)

Status: **executable research/paper-trading specification**.  This replaces
the old one-parameter-set CEM idea.  It must earn a family-level out-of-fold
pass before any live allocation; it is not a deployment claim.

## Why this architecture

The historical work shows that a Polymarket probability is an estimate of an
event outcome, not automatically an estimate of a tradable asset return.  The
strategy must therefore trade only when three separate claims are true:

1. the market is providing a new, directionally coherent update about one
   *economic event*;
2. a specified asset has a causal exposure to that event; and
3. that causal exposure has not already been absorbed by the asset relative to
   its appropriate benchmark.

This is event-study logic: target *abnormal* rather than raw return.  It also
prevents the H1/geo error in which many questions about the same Iran/oil event
were counted as many independent USO opportunities.

## 0. Immutable event schema

Every raw Polymarket market becomes an `EventLeg` only after the following
fields are present and timestamped:

```text
economic_event_id     canonical_event_key      family
market_id             source_ts                available_at
event_end_ts          resolved_polarity         probability
asset                 hedge/benchmark           expected_direction
transmission_template mapping_confidence         liquidity_ok
```

Families are `earnings`, `geopolitics`, and `macro`.  A row with missing causal
template, direction, timestamp, or liquid mapping is *not* a trade.  It is
kept in the research log.

Canonical event keys are not optional:

```text
earnings     = issuer + fiscal-quarter + report date
geopolitics  = geography + actor pair + event type + deadline window
macro        = policy/inflation/growth release + country + release/deadline
```

There may be at most one open position per `economic_event_id`, even if the
event has twenty Polymarket questions or maps to several oil equities.

## 1. Event posterior

At an allowed decision timestamp, realign every market so a higher probability
means a positive return for the declared exposure.  Form the cluster posterior
from a liquidity/confidence weighted median, not a sum:

```text
p_event       = weighted_median(aligned_probability_i)
delta_logit   = logit(p_event[t]) - logit(p_event[t - 1 trading day])
agreement     = weight supporting sign(delta_logit) / total active weight
```

An event is eligible only when:

```text
all source_ts < decision_ts < next executable open
agreement >= 0.75
p_event >= 0.60
abs(delta_logit) >= max(1.0 MAD_24h(delta_logit), family rolling 80th percentile)
2 <= business_days_to_event_end <= 20
```

The probability level alone is deliberately insufficient.  Earnings research
already rejected "high probability means buy."  The trigger is a **new,
cross-market-consistent event update**.

## 2. Causal price-dislocation test

For each allowed asset/hedge pair, calculate a rolling 60-session market-model
abnormal return at the latest close:

```text
AR_1d = r_asset - beta_60 * r_hedge
AR_2d = cumulative market-model abnormal return over two sessions
D    = sign(expected_direction) * z(delta_logit) - z(AR_1d / RV_20)
```

`D` is high only when the event posterior has moved in the exposure's direction
and the tradable asset has not fully repriced.  The entry condition is:

```text
D >= family rolling 80th percentile
and signed AR_2d < family rolling 60th percentile
```

This is the central replacement for CEM's high-probability threshold and its
single `max_price_runup` knob.  It measures probability-price *dislocation*,
not a price move in isolation.

## 3. Family sleeves and directions

| Family | Eligible causal mapping | Trade form | Default interpretation |
| --- | --- | --- | --- |
| Earnings | Direct issuer, same fiscal-quarter report only | Stock versus sector ETF | Positive report surprise must be both direct and unpriced versus sector. |
| Geopolitics | Canonical conflict/trade/shipping event mapped to a liquid causal proxy | Proxy versus broad/commodity hedge | One cluster, one exposure; no repeated USO or XLE bets from paraphrased questions. |
| Macro | Named scheduled policy/inflation/growth event with a predeclared transmission template | Liquid ETF pair/basket versus rate or equity hedge | Trade the rate/inflation/growth transmission, not an arbitrary company associated with the headline. |

Examples of macro templates must be predeclared, not inferred after the move:

```text
inflation upside / hawkish policy  -> rate-sensitive equity relative underweight
growth downside                    -> cyclicals relative underweight
oil supply disruption              -> oil proxy relative strength
trade escalation                   -> affected-country/industry ETF relative weakness
```

The current repository has too few independently mapped macro events to fit a
macro return model.  CEDE therefore records macro events now but assigns its
macro sleeve zero alpha capital until the minimum independent-event gate is
met.  That is a data sufficiency rule, not an exclusion from the architecture.

## 4. Meta-label and ranking

The primary signal is the causal direction.  A simple family-aware,
regularized meta-model estimates the probability that the **remaining**
abnormal return will exceed all-in costs.  It uses only pre-entry data:

```text
[p_event, delta_logit, agreement, D, AR_1d, AR_2d, RV_20,
  days_to_event_end, mapping_confidence, family, event-cluster count]
```

Model fitting is expanding chronological OOF and clustered by
`economic_event_id`.  It uses one regularized model with family intercepts;
no CEM search and no full-path labels in live features.

```text
edge_score = P(net remaining AR > 0) * E[positive AR]
             - P(loss) * E[expected shortfall] - all_in_rotation_cost
```

Admission requires a positive score and a score above the rolling
family-specific 80th percentile.  At a simultaneous decision, rank *unique
economic events*, not market rows.

## 5. Entry and sizing

```text
entry fill        = first regular-session open strictly after decision_ts
base portfolio    = benchmark ETF; event positions replace benchmark exposure
gross event cap   = 35% of equity
family caps       = 10% earnings, 15% geopolitics, 10% macro
single event cap  = 8%
same proxy cap    = 10%
```

Size by ex-ante volatility rather than Kelly:

```text
w = min(event_cap, family_remaining, 0.40% / max(RV20_pct, 1%))
    * min(1, edge_score / rolling_family_80th_score)
```

The unallocated balance remains in the selected benchmark.  This makes the
comparison honest: an event sleeve must beat the asset it displaced.

## 6. Exit state machine

There is **no pure price take-profit**.  The old take-profit result was
execution-invalid and price-only profit locks are not reused.

```text
1. Catastrophe stop:
   standing stop at 2.5 * ATR20 from entry; gap fills at the open and
   intraday touches fill at the standing stop.  It is a loss-control rule,
   never an optimized profit rule.

2. Information invalidation:
   at a pre-open decision, exit at that open if aligned p_event < 0.55
   OR delta_logit reverses by at least 1.0 trailing-24-hour MAD from the
   entry update.

3. Dislocation closure:
   after two complete sessions, exit at next open only if both
   D <= 0 and active abnormal return <= 0.  Price alone is insufficient;
   this avoids the failed Stage 3B hard confirmation filter.

4. Time exit:
   close on the final tradable session before the published event end.

5. Cluster exit:
   if a new, mutually exclusive market within the same economic event
   invalidates the transmission template, flatten the entire cluster.
```

## 7. Non-negotiable validation

Every family receives its own event-clustered chronological OOF report:

```text
entry coverage and timestamp audit
event-cluster—not row—sample count
same-decision ranking and regret
portfolio replay with conservative gap/touch fills
SPY and QQQ active results
fold-by-fold paired results, drawdown, turnover, concentration
```

No family may borrow an apparent edge from another family.  In particular,
the current geo USO concentration is not evidence for repeated oil bets, and
the current macro sample is insufficient for a live macro allocation.

## 8. Current executable implementation

The engine is implemented by:

```text
data/cede/canonical_event_policy.json  reviewed mapping and basket policy
selection/cede_event_map.py            one-event / one-vote canonicalizer
selection/cede_pipeline.py             strict-pre-entry feature and paper-order builder
selection/causal_event_dislocation.py  pure admission, sizing, and exit state machine
```

Run a data audit / research-only pass with:

```powershell
python -m selection.cede_pipeline --output data/cede/latest_run
```

The command always writes the source, mapping, probability, price, coverage,
and allocation audit.  Without a separately created chronological OOF or live
meta-prediction CSV, it writes **zero orders**.  That is the intended safety
default.  A valid prediction file is keyed by `trade_event_id` and provides:

```text
probability_positive       expected_positive_return
probability_loss           expected_shortfall
all_in_rotation_cost       family_edge_score_q80
```

Then, and only then, the same command can produce a capacity-constrained paper
blotter:

```powershell
python -m selection.cede_pipeline `
  --meta-predictions data/cede/chronological_meta_predictions.csv `
  --output data/cede/paper_run
```

The mapping policy is deliberately restrictive. Earnings must be direct
issuer mappings. Geo clusters use an equal-dollar basket of direct commodity
ETPs only, never additional XLE/CVX/XOM clones. The current tariff episodes
use reviewed, predeclared baskets. Unknown macro events are logged and
rejected until their components are reviewed before entry.

## Research basis

The design uses abnormal-return event-study logic, treats prediction-market
prices as event forecasts rather than asset-return forecasts, and requires
family/exposure-specific treatment of geopolitical transmission.  See
MacKinlay (1997), Wolfers & Zitzewitz (2006), and the IMF's 2025 GFSR chapter
on geopolitical risk for the corresponding methodological rationale.
