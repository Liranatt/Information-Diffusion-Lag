"""Frozen-capacity Target A/B diagnostics for Stage 2B."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATES = PROJECT / "data" / "selection_stage1" / "decision_candidates.csv"
DEFAULT_OUTPUT = PROJECT / "data" / "selection_stage2b" / "target_diagnostics"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for column in ("entry_date", "t_e", "te1_exit_date"):
        if column in frame:
            frame[column] = pd.to_datetime(frame[column], errors="coerce", utc=True).dt.normalize()
    return frame


def _selected_rows(frame: pd.DataFrame, selector: str) -> pd.Series:
    column = {
        "connection": "selected_connection",
        "fixed_monotonic": "selected_fixed_monotonic",
        "pairwise_v1": "selected_pairwise_v1",
        "pairwise_v2": "selected_pairwise_v2",
        "monotonic_additive": "selected_monotonic",
        "pooled_set": "selected_pooled",
    }[selector]
    if column not in frame:
        return pd.Series(False, index=frame.index)
    return frame[column].astype(str).str.lower().isin({"true", "1"})


def _load_selector(path: Path, selector: str) -> pd.DataFrame:
    frame = _read(path)
    if selector == "pairwise_v2" and "selected_pairwise_v2" not in frame.columns:
        selected_path = path.parent / "pairwise_v2_selected_rows.csv"
        keys = ["benchmark", "analysis_split", "entry_date", "symbol"]
        if selected_path.exists():
            selected = _read(selected_path)[keys].drop_duplicates().assign(_selected_pairwise_v2=True)
            frame = frame.merge(selected, on=keys, how="left")
            frame["selected_pairwise_v2"] = frame["_selected_pairwise_v2"].fillna(False)
            frame = frame.drop(columns=["_selected_pairwise_v2"])
    frame["selected_for_selector"] = _selected_rows(frame, selector)
    frame["selector"] = selector
    return frame


def _attach_labels(frame: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    keys = ["benchmark", "analysis_split", "entry_date", "symbol"]
    columns = keys + ["te1_active_net_return_pct", "active_return_per_slot_day_pct", "slot_days", "te1_exit_date", "t_e"]
    extra = labels[[column for column in columns if column in labels.columns]].drop_duplicates(keys)
    out = frame.merge(extra, on=keys, how="left", suffixes=("", "_label"))
    for column in columns:
        if column in keys or column not in out.columns:
            continue
        label_column = f"{column}_label"
        if label_column in out.columns:
            out[column] = out[column].where(out[column].notna(), out[label_column])
            out = out.drop(columns=[label_column])
    return out


def run_target_diagnostics(
    candidates_path: Path | str = DEFAULT_CANDIDATES,
    output_dir: Path | str = DEFAULT_OUTPUT,
) -> dict[str, Path]:
    candidates_path, output_dir = Path(candidates_path), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = _read(candidates_path)
    candidates["target_a_active_return_pct"] = pd.to_numeric(candidates["te1_active_net_return_pct"], errors="coerce")
    slot_days = pd.to_numeric(candidates.get("slot_days", np.nan), errors="coerce").clip(lower=1.0)
    candidates["target_b_active_per_slot_day_pct"] = candidates["target_a_active_return_pct"] / slot_days
    candidates["target_b_active_per_sqrt_slot_day_pct"] = candidates["target_a_active_return_pct"] / np.sqrt(slot_days)
    candidates["te1_horizon_assertion"] = pd.to_datetime(candidates["te1_exit_date"], utc=True, errors="coerce") < pd.to_datetime(
        candidates["t_e"], utc=True, errors="coerce"
    )
    label_columns = [
        "benchmark", "analysis_split", "entry_date", "symbol", "event_family",
        "target_a_active_return_pct", "target_b_active_per_slot_day_pct",
        "target_b_active_per_sqrt_slot_day_pct", "slot_days", "te1_exit_date", "t_e", "te1_horizon_assertion",
    ]
    labels_path = output_dir / "target_labels.csv"
    candidates[label_columns].to_csv(labels_path, index=False)

    source = PROJECT / "data" / "selection_stage1" / "selection_scores.csv"
    v1 = _load_selector(source, "connection")
    selectors = {
        "connection": v1,
        "fixed_monotonic": _load_selector(source, "fixed_monotonic"),
        "pairwise_v1": _load_selector(source, "pairwise_v1"),
        "pairwise_v2": _load_selector(PROJECT / "data" / "selection_stage2b" / "pairwise_v2" / "pairwise_v2_scores.csv", "pairwise_v2"),
        "monotonic_additive": _load_selector(PROJECT / "data" / "selection_stage2b" / "ranking_models" / "monotonic_scores.csv", "monotonic_additive"),
        "pooled_set": _load_selector(PROJECT / "data" / "selection_stage2b" / "ranking_models" / "pooled_scores.csv", "pooled_set"),
    }
    summary_rows = []
    for name, frame in selectors.items():
        frame = _attach_labels(frame, candidates)
        for split, part in frame.groupby("analysis_split", sort=True):
            chosen = part[part["selected_for_selector"]].copy()
            a = pd.to_numeric(chosen.get("te1_active_net_return_pct"), errors="coerce")
            b = pd.to_numeric(chosen.get("active_return_per_slot_day_pct"), errors="coerce")
            chosen_slot_days = pd.to_numeric(chosen["slot_days"], errors="coerce").to_numpy(dtype=float)
            b_sqrt = pd.to_numeric(chosen["te1_active_net_return_pct"], errors="coerce") / np.sqrt(
                np.maximum(chosen_slot_days, 1.0)
            )
            summary_rows.append(
                {
                    "selector": name,
                    "analysis_split": split,
                    "scope": "frozen_capacity_same_day_ranking_evaluation",
                    "n_candidates": len(part),
                    "n_selected": len(chosen),
                    "mean_target_a_pct": float(a.mean()) if a.notna().any() else np.nan,
                    "median_target_a_pct": float(a.median()) if a.notna().any() else np.nan,
                    "q10_target_a_pct": float(a.quantile(0.10)) if a.notna().any() else np.nan,
                    "mean_target_b_slot_pct": float(b.mean()) if b.notna().any() else np.nan,
                    "median_target_b_slot_pct": float(b.median()) if b.notna().any() else np.nan,
                    "q10_target_b_slot_pct": float(b.quantile(0.10)) if b.notna().any() else np.nan,
                    "mean_target_b_sqrt_pct": float(b_sqrt.mean()) if b_sqrt.notna().any() else np.nan,
                    "median_target_b_sqrt_pct": float(b_sqrt.median()) if b_sqrt.notna().any() else np.nan,
                    "q10_target_b_sqrt_pct": float(b_sqrt.quantile(0.10)) if b_sqrt.notna().any() else np.nan,
                    "te1_horizon_assertion": bool(
                        (pd.to_datetime(part["te1_exit_date"], utc=True, errors="coerce") < pd.to_datetime(part["t_e"], utc=True, errors="coerce")).all()
                    ),
                }
            )
    comparison_path = output_dir / "target_ab_comparison.csv"
    pd.DataFrame(summary_rows).to_csv(comparison_path, index=False)
    manifest = {
        "label": "stage2b_target_a_b_diagnostics",
        "candidate_sha256": _hash(candidates_path),
        "target_a": "te1_active_net_return_pct",
        "target_b": ["active_return / slot_days", "active_return / sqrt(slot_days)"],
        "target_selection_formula_tuning": "none; formulas predeclared",
        "evaluation_scope": "frozen_capacity_same_day_ranking_evaluation",
        "current_2026_test_is_exploratory": True,
        "te_is_never_exit": True,
        "outputs": {"labels": str(labels_path), "comparison": str(comparison_path)},
    }
    manifest_path = output_dir / "target_diagnostics_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"labels": labels_path, "comparison": comparison_path, "manifest": manifest_path}


if __name__ == "__main__":
    for name, path in run_target_diagnostics().items():
        print(f"{name}: {path}")
