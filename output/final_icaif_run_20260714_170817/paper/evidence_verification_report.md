# Evidence verification report — final revision (2026-07-14)

Scope: only the material claims examined during this revision. Statuses:
VERIFIED / PARTIALLY VERIFIED / UNVERIFIED / CONTRADICTED.

## A. H1 chronology — T_theta vs T_entry

**Status: VERIFIED (and manuscript corrected).**
Evidence: `data/candidates_audit_clean.parquet` column `t_theta` is the first crossing of the
fixed 0.55 screening threshold (set at candidate construction, not optimized);
`core/kernel.py::entry_day` (lines 536–556) defines acceptance: first probability point ≥
`enter_strong`, or `hold_days` consecutive points ≥ `enter_floor`, scanning from
`t_theta.normalize()`; `diagnostics/run_raw_expectation_test_tminus1.py::process_candidate`
(lines 210–222) sets the entry price at the first stored close on/after acceptance.
Return therefore begins at **T_entry (the policy-accepted entry close)**, not at T_theta.
Manuscript change: holding interval rewritten as [T_entry, T_e−1]; the return equation already
used P_entry; the signal-window figure was rebuilt to show T_theta and T_entry separately.

## B. Event endpoint provenance

**Status: PARTIALLY VERIFIED — endpoint value verified, point-in-time provenance UNVERIFIED.**
Evidence: `h1_end_date_uncertainty_audit.csv` (887 rows): `scheduled_end_source = "current
candidates.parquet t_e"`, `scheduled_end_snapshot_at_signal_available = False`,
`actual_public_outcome_timestamp_available = False`. T_e is the scheduled contract endpoint
stored in the research dataset; no versioned endpoint snapshot at signal time and no actual
resolution timestamps exist in the artifacts. Manuscript change: wording switched to
"the scheduled contract endpoint stored in the research dataset" and a limitation added
stating that endpoint revisions and actual resolution times cannot be reconstructed, so it
cannot be proven that every event was still unresolved at the measured exit close.

## C. H1 exclusions and same-session timing

**Status: previous wording CONTRADICTED by the artifact; corrected.**
Evidence: `raw_expectation_invalid_candidates.csv` reason counts — below_entry_threshold 121,
prob_surge_exceeded 108, entry_not_before_T_minus_1 47, no_clean_signal_side 12,
price_runup_exceeded 7. The 47 exclusions are **all** entries reaching acceptance only at or
after the final eligible pre-event close; there are **zero** missing-price or bad-price
exclusions. The manuscript no longer mentions missing prices.
Timing: `h1_timing_audit.csv` — `same_session_entry_verified` is False for 887/887 rows
(probability and equity artifacts are date-normalized). Manuscript now states: daily data do
not establish same-session ordering between the probability signal and the equity close; the
next-stored-close specification is reported as the conservative timing sensitivity.

## D. Gemini methodology

**Status: pipeline and thresholds VERIFIED; runtime model identity UNVERIFIED (qualified).**
Evidence: `ingest/prefilter.py` (deterministic noise-cull, NOISE_FLOOR = 0.15 plus
structural-noise patterns; no relevance scoring), `ingest/dedup.py`, `ingest/world.py`:
two-pass Gemini — pass 1 question-level relevance gate (`GEMINI_RELEVANCE_GATE_PROMPT`,
`question_relevance ∈ [0,1]`, `QUESTION_RELEVANCE_FLOOR = 0.60`, plus a positive-tone
requirement for the long-only book, lines 565–577); pass 2 tight mapping to exposed
U.S.-listed stocks/ETFs with per-asset `connection_strength ∈ [0,1]`; final pair relevance
persisted as `question_relevance × connection_strength` (lines 627–639), i.e.
R_{i,a} = R_i^q · C_{i,a}; the experiment gate `feat_connection_strength > 0.5`
(`backtesting/optimize_cem.py` line 2365). Structured outputs are pydantic-validated JSON
schemas (`ingest/gemini_client.py`, `response_model.model_validate_json`). Polarity labels
are a separate pass (`ingest/label_polarity.py`; resolution override > llm > regex in
`core/polarity.py`). Targeted human audit: 109 question-asset pairs
(`data/candidate_cleaning_summary.json`).
UNVERIFIED: the exact runtime Gemini model — `ingest/gemini_client.py` line 42 reads
`GEMINI_MODEL` from the environment (default "gemini-3.5-flash"); per-call usage rows go to a
database not present in the artifact bundle. The manuscript names no specific model version
and says the model is configured at runtime. No claim of a historically sealed LLM and no
universe-wide accuracy claim is made.

## E. Concentration and benchmark interpretation

**Status: VERIFIED from artifacts; added to manuscript.**
Evidence: `h1/raw_expectation_tminus1_final/h1_leave_one_out.csv` — candidate-level baseline
mean +1.208%: removing symbol USO (104 rows) → +0.313%; removing entry month 2026-03
(116 rows) → +0.206%; event level: removing 2026-03 (43 events) → +0.435% from +0.631%.
Implication stated in the paper: the geopolitical result is materially concentrated in
oil-linked instruments and one calendar month.
Benchmark-relative: `benchmark_excess/benchmark_excess_inference.csv` — candidate vs SPY mean
excess +0.914%, event-cluster 95% CI [−0.116%, +2.013%], null-centered one-sided p = 0.0508;
all 20 scheme/level/benchmark rows have CIs crossing zero (max p ≈ 0.33). The manuscript
reports positive point estimates with no interval excluding zero and claims no established
persistent alpha.

## F. CEM baseline chronology (cross-reference)

**Status: VERIFIED.** See `baseline_methodology_assessment.md` (same directory) for the code
paths, the four chronology distinctions, the T2-only run, and the manuscript restructure.

## Claims deliberately NOT re-audited in this focused revision

H1 central estimates, dependence intervals, sensitivity tables, the 40-run grid, and the
cleaning reconciliation were regenerated and verified earlier in this same final-run bundle
(see `code_methodology_report.md`, `paper_numbers_final.md`, `final_statistical_report.md`)
and were not re-derived again here.
