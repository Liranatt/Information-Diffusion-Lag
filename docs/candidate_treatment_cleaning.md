# Candidate treatment cleaning

The source file `data/candidates.parquet` is never overwritten by the treatment
audit. Run:

```powershell
python -m ingest.clean_candidates
```

This creates:

- `data/candidates_audit_annotated.parquet`: every exactly deduplicated source
  row, including excluded, secondary, and quarantined questions.
- `data/candidates_audit_clean.parquet`: rows currently eligible for primary
  CEM selection.
- `data/candidate_cleaning_disposition.csv`: human-readable row dispositions
  and recommended treatments.
- `data/candidate_cleaning_summary.json`: reconciliation counts.

`backtesting.optimize_cem` uses `candidates_audit_clean.parquet` by default.
Use `--candidates-path data/candidates.parquet` only for an explicit legacy
comparison.

## Frozen actions

| Audit verdict | Cleaning action |
|---|---|
| Mostly correct | Primary eligible |
| Mostly correct after controls | Primary eligible with event controls |
| Data repair, then keep | Deduplicate, then primary eligible |
| Partly correct | Quarantine until the named transform is available |
| Needs redesign | Quarantine until the named transform is available |
| Secondary only | Retain for confirmation research, not standalone CEM |
| Exclude from primary | Exclude |
| Incorrect endpoint | Exclude until the endpoint is reconstructed |

The audit covers 109 question-asset keys. Other historical rows are labelled
`unreviewed_passthrough`; they remain eligible so this targeted audit does not
silently remove the rest of the research universe.

## Machine-readable transforms

Audited rows include `required_probability_transform`,
`requires_external_baseline`, `requires_path_aggregation`, and
`standalone_signal_role`. Threshold distributions, deadline hazards, product
bundles, OIS/consensus surprises, and exposure-conditioned signals stay
quarantined until those input series exist. Cleaning never substitutes the old
binary probability and calls the transform complete.

All rows also receive an `economic_event_id`. Primary rows within an economic
event receive equal `economic_event_weight` values summing to one, for
event-level inference and downstream concentration controls.
