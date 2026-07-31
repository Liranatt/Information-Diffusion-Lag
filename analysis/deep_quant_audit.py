"""Complete CSV inventory and evidence ledger for the final quant review.

This module deliberately profiles every CSV in the repository (including raw
market fragments and repeated replay logs) before extracting the compact
result tables used for inference.  It separates documented-invalid execution
artifacts from corrected or OOF research evidence; a profitable file is never
treated as valid merely because its P&L is high.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / "data" / "deep_quant_audit"
ROOTS = ("data", "output", "runs", "seeds", "assistant_generated_all_artifacts", "notebooks")

KEY_TABLES = {
    "execution_corrected": PROJECT / "assistant_generated_all_artifacts" / "assistant_generated_research_core" / "proper_execution_rerun" / "proper_execution_summary_full.csv",
    "execution_exit_attribution": PROJECT / "assistant_generated_all_artifacts" / "assistant_generated_research_core" / "proper_execution_rerun" / "oos_exit_attribution.csv",
    "local_quant_fix": PROJECT / "assistant_generated_all_artifacts" / "assistant_generated_research_core" / "local_quant_fix_summary.csv",
    "local_quant_seed_robustness": PROJECT / "assistant_generated_all_artifacts" / "assistant_generated_research_core" / "local_quant_fix_variant_seed_aggregate.csv",
    "selection_stage2b": PROJECT / "data" / "selection_stage2b" / "nested_final_validation" / "threshold_stability_outer_fold.csv",
    "selection_stage2e": PROJECT / "data" / "selection_stage2e" / "selector_exit_matrix" / "selector_exit_combined_exact_results.csv",
    "selection_stage2f": PROJECT / "data" / "selection_stage2f" / "exact_replay" / "selector_exit_combined_exact_results.csv",
    "selection_stage2g": PROJECT / "data" / "selection_stage2g" / "polymarket_rerun" / "exact_replay" / "selector_exit_combined_exact_results.csv",
    "stage3a": PROJECT / "data" / "selection_stage3a_execution_safe" / "combined_exact_results.csv",
}


def _family(path: Path) -> str:
    text = str(path).lower().replace("\\", "/")
    if "polymarket_download" in text:
        return "raw_probability_history"
    if "original_flawed" in text or "flawed" in text:
        return "known_invalid_execution"
    if "proper_execution" in text or "corrected_full" in text:
        return "corrected_execution"
    if "/runs/" in f"/{text}" or "/seeds/" in f"/{text}":
        return "optimizer_or_seed_log"
    if "selection_stage" in text:
        return "selection_research"
    if "stage3" in text:
        return "exit_research"
    if "/h1/" in f"/{text}":
        return "h1_protocol"
    return "other"


def _profile_csv(path: Path) -> dict:
    relative = path.relative_to(PROJECT)
    result = {
        "path": str(relative),
        "family": _family(relative),
        "bytes": path.stat().st_size,
        "rows": 0,
        "columns": 0,
        "header": "",
        "schema_signature": "",
        "read_status": "ok",
    }
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
            result["columns"] = len(header)
            result["header"] = "|".join(header)
            result["schema_signature"] = "|".join(header[:20])
            result["rows"] = sum(1 for _ in reader)
    except Exception as exc:  # Inventory must record, not hide, malformed files.
        result["read_status"] = f"error:{type(exc).__name__}"
    return result


def _read_key_tables() -> tuple[pd.DataFrame, pd.DataFrame]:
    summaries = []
    columns = []
    (OUTPUT / "key_tables").mkdir(parents=True, exist_ok=True)
    for label, path in KEY_TABLES.items():
        if not path.exists():
            summaries.append({"label": label, "path": str(path.relative_to(PROJECT)), "status": "missing"})
            continue
        table = pd.read_csv(path)
        summaries.append({
            "label": label,
            "path": str(path.relative_to(PROJECT)),
            "status": "read",
            "rows": len(table),
            "columns": len(table.columns),
        })
        columns.extend({"label": label, "column": column} for column in table.columns)
        table.to_csv(OUTPUT / "key_tables" / f"{label}.csv", index=False)
    return pd.DataFrame(summaries), pd.DataFrame(columns)


def run(output: Path = OUTPUT) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    files = []
    for root in ROOTS:
        directory = PROJECT / root
        if directory.exists():
            files.extend(path for path in directory.rglob("*.csv") if path.is_file())
    profiles = pd.DataFrame(_profile_csv(path) for path in sorted(set(files)))
    profiles.to_csv(output / "csv_inventory.csv", index=False)
    schema = (
        profiles.groupby(["family", "schema_signature"], dropna=False, as_index=False)
        .agg(files=("path", "size"), rows=("rows", "sum"), bytes=("bytes", "sum"), statuses=("read_status", lambda values: "|".join(sorted(set(values)))))
        .sort_values(["bytes", "files"], ascending=False)
    )
    schema.to_csv(output / "schema_rollup.csv", index=False)
    key_summary, key_columns = _read_key_tables()
    key_summary.to_csv(output / "key_table_read_ledger.csv", index=False)
    key_columns.to_csv(output / "key_table_columns.csv", index=False)
    family_rollup = (
        profiles.groupby("family", as_index=False)
        .agg(files=("path", "size"), rows=("rows", "sum"), bytes=("bytes", "sum"), read_errors=("read_status", lambda values: int((values != "ok").sum())))
        .sort_values("bytes", ascending=False)
    )
    family_rollup.to_csv(output / "family_rollup.csv", index=False)
    manifest = {
        "csv_files_profiled": int(len(profiles)),
        "csv_rows_scanned": int(profiles["rows"].sum()),
        "csv_bytes_scanned": int(profiles["bytes"].sum()),
        "families": family_rollup.set_index("family").to_dict(orient="index"),
        "key_tables": key_summary.to_dict(orient="records"),
        "known_invalid_rule": "Files labelled original_flawed/flawed are ledgered but excluded from performance inference.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
