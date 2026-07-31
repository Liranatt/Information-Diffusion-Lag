"""Exact replay on the same chronological training OOF validation groups."""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path

import pandas as pd

from .admission import make_admission_policy
from .dynamic_replay import (
    DEFAULT_PRICES,
    DEFAULT_PROBS,
    DEFAULT_SOURCE,
    DEFAULT_UNIVERSE,
    _attach_admission_values,
    _hash,
    _load_universe,
    _merge_score,
    _replay_one,
    _selector_universe,
    _set_score_rank,
)


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT / "data" / "selection_stage2b" / "dynamic_replay_oof"


def run_oof_replay(
    universe_path: Path | str = DEFAULT_UNIVERSE,
    source_path: Path | str = DEFAULT_SOURCE,
    prices_path: Path | str = DEFAULT_PRICES,
    probs_path: Path | str = DEFAULT_PROBS,
    output_dir: Path | str = DEFAULT_OUTPUT,
) -> dict[str, Path]:
    universe_path, source_path, prices_path, probs_path, output_dir = map(Path, (universe_path, source_path, prices_path, probs_path, output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    universe = _load_universe(universe_path, source_path)
    prices = pickle.loads(prices_path.read_bytes())
    probs = pickle.loads(probs_path.read_bytes())
    score_paths = {
        "monotonic_oof": PROJECT / "data" / "selection_stage2b" / "ranking_models" / "monotonic_oof.csv",
        "pooled_oof": PROJECT / "data" / "selection_stage2b" / "ranking_models" / "pooled_oof.csv",
    }
    monotonic_oof = _merge_score(universe, score_paths["monotonic_oof"], "score_monotonic")
    pooled_oof = _merge_score(universe, score_paths["pooled_oof"], "score_pooled")
    # Use the intersection of validation keys so all selectors see identical
    # candidate groups and identical capacity history.
    keys = ["benchmark", "analysis_split", "entry_date", "symbol"]
    mono_keys = monotonic_oof.loc[monotonic_oof["score_monotonic"].notna(), keys]
    pooled_keys = pooled_oof.loc[pooled_oof["score_pooled"].notna(), keys]
    validation_keys = mono_keys.merge(pooled_keys, on=keys, how="inner").drop_duplicates()
    base = _selector_universe(universe, "connection_oof_tiebreaker", {"v1": Path(""), "v2": Path(""), "monotonic": score_paths["monotonic_oof"], "pooled": score_paths["pooled_oof"]}, 0)
    base = base.merge(validation_keys, on=keys, how="inner")
    mono = _set_score_rank(monotonic_oof.merge(validation_keys, on=keys, how="inner"), "score_monotonic", "_selector_rank")
    pooled = _set_score_rank(pooled_oof.merge(validation_keys, on=keys, how="inner"), "score_pooled", "_selector_rank")
    # Choices were already frozen from the full chronological OOF table.  The
    # replay below applies that frozen connection admission rule without using
    # any test-period information.
    choice_path = PROJECT / "data" / "selection_stage2b" / "admission" / "admission_choices.json"
    choices = json.loads(choice_path.read_text(encoding="utf-8")) if choice_path.exists() else {}
    selected_choice = choices.get("selected_for_initial_dynamic_replay", {"policy": "always_fill", "threshold": None})
    runs = [
        ("connection_oof_tiebreaker", base, None, None),
        ("connection_oof_tiebreaker", base, selected_choice.get("policy"), selected_choice.get("threshold")),
        ("monotonic_additive", mono, None, None),
        ("pooled_set", pooled, None, None),
    ]
    results = []
    for selector, frame, admission_name, threshold in runs:
        frame = _attach_admission_values(frame, admission_name, {"monotonic": score_paths["monotonic_oof"]})
        run_name = selector if admission_name is None else f"{selector}__admission_{admission_name}"
        run_dir = output_dir / run_name
        for benchmark in ("SPY", "QQQ"):
            trades, equity, allocation, result = _replay_one(
                frame,
                prices,
                probs,
                selector,
                benchmark,
                "train",
                admission_name,
                threshold,
            )
            if not trades.empty:
                exit_date = pd.to_datetime(trades["exit_date"], utc=True, errors="coerce").dt.normalize()
                candidate_te = pd.to_datetime(trades["candidate_t_e"], utc=True, errors="coerce").dt.normalize()
                if (exit_date >= candidate_te).any():
                    raise AssertionError("OOF replay generated an exit at or after t_e")
            run_dir.mkdir(parents=True, exist_ok=True)
            trades.to_csv(run_dir / f"trades_{benchmark.lower()}_train.csv", index=False)
            equity.to_csv(run_dir / f"equity_{benchmark.lower()}_train.csv", index=False)
            allocation.to_csv(run_dir / f"allocation_{benchmark.lower()}_train.csv", index=False)
            result["validation_scope"] = "chronological_training_oof_exact_replay"
            result["oof_validation_groups"] = int(validation_keys[["benchmark", "entry_date"]].drop_duplicates().shape[0])
            results.append(result)
    summary_path = output_dir / "exact_oof_replay_summary.csv"
    pd.DataFrame(results).to_csv(summary_path, index=False)
    manifest = {
        "label": "exact_dynamic_oof_selector_replay",
        "validation_scope": "intersection of chronological training OOF validation keys",
        "selectors": [selector for selector, _frame, _admission, _threshold in runs],
        "admission_choice_source": str(choice_path),
        "current_2026_test_used": False,
        "terminal_horizon": "exit_date < candidate_t_e asserted",
        "te_is_never_exit": True,
        "outputs": {"summary": str(summary_path)},
    }
    manifest_path = output_dir / "exact_oof_replay_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return {"summary": summary_path, "manifest": manifest_path, "directory": output_dir}


if __name__ == "__main__":
    for name, path in run_oof_replay().items():
        print(f"{name}: {path}")
