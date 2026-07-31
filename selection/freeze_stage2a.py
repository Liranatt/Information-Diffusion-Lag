"""Freeze the completed Stage 2A ranking run for reproducible comparison."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
SOURCE_DIR = PROJECT / "data" / "selection_stage1"
FREEZE_DIR = PROJECT / "data" / "selection_stage2a" / "frozen_capacity_same_day_ranking_evaluation"
SOURCE_FILES = (
    "decision_candidates.csv",
    "decision_candidates_all.csv",
    "competition_pairs.csv",
    "selection_scores.csv",
    "selection_summary.csv",
    "pairwise_model.json",
    "manifest.json",
    "baseline_manifest.json",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _date_range(candidates: pd.DataFrame, split: str) -> dict[str, str | None]:
    subset = candidates[candidates["analysis_split"].astype(str).str.lower().eq(split)]
    dates = pd.to_datetime(subset["entry_date"], errors="coerce", utc=True).dropna()
    return {
        "entry_date_min": dates.min().date().isoformat() if len(dates) else None,
        "entry_date_max": dates.max().date().isoformat() if len(dates) else None,
    }


def _selected_rows(scores: pd.DataFrame) -> pd.DataFrame:
    selection_columns = sorted(c for c in scores.columns if c.startswith("selected_"))
    rows = []
    base_columns = [
        c
        for c in (
            "benchmark",
            "analysis_split",
            "entry_date",
            "symbol",
            "te1_exit_date",
            "t_e",
            "te1_active_net_return_pct",
            "active_return_per_slot_day_pct",
        )
        if c in scores.columns
    ]
    for column in selection_columns:
        selected_mask = scores[column].astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})
        selected = scores[selected_mask].loc[:, base_columns].copy()
        selected.insert(0, "baseline", column.removeprefix("selected_"))
        rows.append(selected)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def freeze_stage2a(source_dir: Path | str = SOURCE_DIR, freeze_dir: Path | str = FREEZE_DIR) -> dict[str, Path]:
    source_dir = Path(source_dir)
    freeze_dir = Path(freeze_dir)
    freeze_dir.mkdir(parents=True, exist_ok=True)
    missing = [name for name in SOURCE_FILES if not (source_dir / name).exists()]
    if missing:
        raise FileNotFoundError(f"Stage 2A outputs are missing: {missing}")

    existing_manifest = freeze_dir / "freeze_manifest.json"
    source_hashes = {name: _sha256(source_dir / name) for name in SOURCE_FILES}
    if existing_manifest.exists():
        prior = json.loads(existing_manifest.read_text(encoding="utf-8"))
        if prior.get("source_hashes") != source_hashes:
            raise RuntimeError("Frozen Stage 2A directory already exists with different source hashes")
    else:
        for name in SOURCE_FILES:
            target = freeze_dir / name
            if not target.exists():
                shutil.copy2(source_dir / name, target)

        scores = pd.read_csv(source_dir / "selection_scores.csv")
        selected_path = freeze_dir / "selected_rows.csv"
        _selected_rows(scores).to_csv(selected_path, index=False)

        candidates = pd.read_csv(source_dir / "decision_candidates.csv")
        candidates["entry_date"] = pd.to_datetime(candidates["entry_date"], errors="coerce", utc=True)
        date_ranges = {
            split: _date_range(candidates, split) for split in ("train", "test")
        }
        manifest = {
            "label": "frozen_capacity_same_day_ranking_evaluation",
            "source_dir": str(source_dir),
            "source_hashes": source_hashes,
            "frozen_files": list(SOURCE_FILES) + ["selected_rows.csv"],
            "date_ranges": date_ranges,
            "target_definitions": {
                "target_a": "te1_active_net_return_pct through te1_exit_date, strictly before t_e",
                "target_b": "active_return_per_slot_day_pct",
                "oracle": "oracle_te1 is retrospective diagnostic only",
            },
            "evaluation_label": "frozen_capacity_same_day_ranking_evaluation",
            "dynamic_portfolio_replay": False,
            "exact_intraday_ordering_known": False,
            "random_seeds": [0],
            "preprocessing": {
                "stage_2a_pairwise": "median imputation plus standard scaling, C=0.1",
                "selection_capacity": "free_slots_before from same_day_capacity_summary",
            },
        }
        existing_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return {
        "directory": freeze_dir,
        "manifest": existing_manifest,
        "selected_rows": freeze_dir / "selected_rows.csv",
    }


if __name__ == "__main__":
    for name, path in freeze_stage2a().items():
        print(f"{name}: {path}")
