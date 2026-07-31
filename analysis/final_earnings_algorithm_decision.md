# Earnings research: final forensic conclusion

## Bottom line

There is **no validated earnings-stock selection and exit algorithm** in the
available data.  The correct final operational rule is therefore to **not
replace the benchmark with single-name earnings trades**.  A strategy that
occasionally looks strong in QQQ but fails in SPY is not an algorithm to
deploy.

This is not a claim that earnings moves cannot be traded.  It is the much
narrower, evidence-based statement that this project has not demonstrated a
repeatable, timestamp-safe edge after selection, capacity, costs, and
corrected fills.

## What was audited

`analysis.deep_quant_audit` read every available CSV: **7,014 files,
3,008,383 data rows, and 703.6 MB**, with no read failures.  The resulting
ledger is in `data/deep_quant_audit/`.  It separates raw probability history,
selection results, optimizer logs, H1 outputs, corrected-execution outputs,
and known-invalid execution artifacts.  The candidate forensic tables in
`data/deep_quant_audit/candidate_forensics/` are derived only from the
canonical Stage 2F OOF panel and legal paths.

## Findings that survive the audit

### 1. The apparent CEM success is excluded

The profitable original CEM run used defective stop/take-profit execution and
is not evidence.  Its result is deliberately excluded from every decision.
Under corrected daily gap/touch mechanics, the broad CEM family did not
generalize:

| Corrected CEM result | SPY active excess | QQQ active excess |
| --- | ---: | ---: |
| Frozen policy | -8.29 pp | -10.96 pp |
| Retrained policy | -2.33 pp | -14.67 pp |

Some local hard-cap variants looked better in one SPY split, but their QQQ
results remained negative and the seed analysis was not stable.  Those were
exploratory fragments, not a repeatable policy.

### 2. H1 did not establish an earnings-selection edge

The broad H1 raw observation mean was propped up by a concentrated upper tail
and by non-earnings families.  For earnings alone, mean raw net return was
about **+0.02%**, with no positive-mean support (`p=0.4687`).  The aggregate
candidate result lost money after removing the top 5% of winners.  H1 also
documents that the historical daily artifacts cannot prove same-session
execution.  It is useful background, not a tradable rule.

### 3. Target B is a modest screen, not a strong alpha model

Target B's out-of-fold score has only weak association with terminal legal
active return (Spearman **0.092 SPY**, **0.110 QQQ**) and essentially no
monotone bad-trade rejection.  Its score bins are non-monotone.  Direct issuer
mapping is necessary for semantic correctness, but it does not solve the
economic prediction problem.

The pre-entry attributes that looked directionally better were also not a
universal entry rule: severe relative underperformance before entry and the
worst two-week price trend were bad, but neither an entry-probability band nor
connection strength created stable positive active returns in both benchmarks.

### 4. Full timestamp-safe probability trajectories add no usable selection edge

The fresh one-minute Polymarket rerun covered all 415 candidates with at least
61 strictly pre-entry observations and zero post-entry observations.  It was
therefore an honest test.  Target B plus trajectories was worse than Target B
in all eight exit policies on QQQ and in seven of eight on SPY; its paired
median improvement was negative and only one chronological fold was positive.
Trajectory features should remain out of the live selector.

### 5. Early relative follow-through is real descriptively, but not a portfolio edge

The legal paths show a strong descriptive split after the first complete
post-entry session.  In the Stage 2F path audit, a position below -2% active
return versus its benchmark had roughly an 80% persistent-loser rate, while a
position above +1% had no persistent losers.  The same sign pattern appeared
on corrected 2026 CEM OOS earnings paths.

That still does **not** make the rule tradable.  The strict, next-open version
was the single final falsification test below.  It reduces drawdown, but it
also cuts enough delayed recoveries that SPY performance deteriorates.

## Final falsification test

`selection.stage3b_relative_confirmation` held Target B and all entry timing
fixed.  It entered only at the first executable daily open after a raw
Polymarket signal.  At the close of the first full post-entry session, it kept
the stock only if stock return minus selected-benchmark return was at least
+1%; otherwise it exited at the following open.  Every entry and exit used
only information available before its respective fill, and all exits remained
before legal `T_e`.

| Arm | SPY active excess | QQQ active excess | SPY paired fold result | QQQ paired fold result |
| --- | ---: | ---: | --- | --- |
| Corrected reference | -1.08% | +0.90% | reference | reference |
| Relative confirmation | **-2.51%** | +0.67% | median delta **-0.65%**, 2/5 positive | median delta +0.01%, 3/5 positive |

The challenger slightly reduced drawdown in aggregate, but it failed the
required SPY absolute-performance and paired-fold gates.  It is rejected.
The complete replay, plans, timing audit, and fold table are in
`data/selection_stage3b_relative_confirmation/`.

## Why the ideas failed

1. **Probability is evidence about the event, not a reliable forecast of the
   stock's near-term relative path.**  The fresh trajectory test directly
   falsified the extra pre-entry information claim.
2. **The remaining earnings distribution is dominated by delayed winners and
   sharp losers.**  A hard loss cap removes some disaster paths but also gives
   up the slow recoveries needed to offset costs and benchmark opportunity
   cost.
3. **An early relative loss is useful for diagnosis but too late for a clean
   entry edge.**  By the following open, part of the damage is already paid;
   the rule has no capacity benefit here because the exact replay was not
   capacity constrained (`blocked_by_capacity=0`).
4. **The sample is short and regime-specific.**  It cannot support selecting
   a different threshold until one happens to pass.

## Final operational algorithm

```text
if earnings_alpha_is_not_validated:
    hold the mandate benchmark (SPY or QQQ)
    do not open a single-name earnings position
else:
    this branch is unavailable until a prospective test passes
```

That is intentionally not a disguised cash or QQQ-only backtest.  It is the
only rule supported by the evidence that does not knowingly substitute an
unvalidated losing stock-selection process for the index.

## What to collect before revisiting earnings alpha

Run a prospective, paper-only logger with the selector frozen and no threshold
tuning.  For every direct-issuer candidate, store immutable raw Polymarket
`source_ts`/availability, the exact decision timestamp, next-open entry,
daily stock/sector/SPY/QQQ OHLC, and every exit-state observation.  Freeze one
rule before collecting the next untouched sample.  Only a pass against both
benchmarks with paired chronological folds should reopen live-capital
discussion.

The previously exported `final_earnings_algorithm.py` is superseded by this
decision and must not be treated as a deployment recommendation.
