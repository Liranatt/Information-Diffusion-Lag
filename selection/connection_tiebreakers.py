"""Explicit tie-breaker study for the connection-strength reference selector."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .baselines import _merge_capacity, _read_candidates, _read_capacity


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATES = PROJECT / "data" / "selection_stage1" / "decision_candidates.csv"
DEFAULT_CAPACITY = (
    PROJECT
    / "assistant_generated_all_artifacts"
    / "assistant_generated_research_core"
    / "trade_opportunity_research"
    / "same_day_capacity_summary.csv"
)
DEFAULT_OUTPUT = PROJECT / "data" / "selection_stage2b" / "connection_tiebreakers"
# This is intentionally large enough to estimate the random legal allocator
# distribution rather than treating one arbitrary seed as a result.
RANDOM_SEEDS = tuple(range(1000))
DETERMINISTIC_TIE_BREAKERS = (
    "entry_probability",
    "expected_slot_days",
    "sector_relative_extension",
    "source_order",
    "symbol_independent_hash",
)


def _stable_hash(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:16], 16)


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "source_row_number" in out.columns:
        out["source_order"] = pd.to_numeric(out["source_row_number"], errors="coerce")
    else:
        out["source_order"] = np.nan
    for column in (
        "feat_connection_strength",
        "entry_prob",
        "feat_time_to_resolution_days",
        "feat_asset_2w_trend",
        "feat_sector_1m_trend",
        "te1_active_net_return_pct",
        "active_return_per_slot_day_pct",
    ):
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    if "feat_time_to_resolution_days" in out.columns:
        out["expected_slot_days"] = out["feat_time_to_resolution_days"]
    elif "expected_slot_days" not in out.columns:
        out["expected_slot_days"] = np.nan
    if "feat_asset_2w_trend" in out.columns and "feat_sector_1m_trend" in out.columns:
        out["sector_relative_extension"] = out["feat_asset_2w_trend"] - out["feat_sector_1m_trend"]
    elif "sector_relative_extension" not in out.columns:
        out["sector_relative_extension"] = np.nan
    stable_key_columns = [
        "benchmark",
        "entry_date",
        "economic_event_group_clean",
        "economic_event_id",
        "symbol",
    ]
    stable_key = out.reindex(columns=stable_key_columns, fill_value="").astype(str).agg("|".join, axis=1)
    out["deterministic_final_key"] = stable_key.map(_stable_hash)
    out["source_order"] = out["source_order"].fillna(out["deterministic_final_key"])
    hash_columns = [
        "benchmark",
        "entry_date",
        "feat_connection_strength",
        "entry_prob",
        "expected_slot_days",
        "sector_relative_extension",
    ]
    out["symbol_independent_hash"] = out[hash_columns].astype(str).agg("|".join, axis=1).map(_stable_hash)
    return out


def _connection_tie_groups(day: pd.DataFrame) -> list[pd.Index]:
    values = pd.to_numeric(day["feat_connection_strength"], errors="coerce").fillna(-np.inf)
    ordered_values = sorted(values.unique(), reverse=True)
    return [day.index[values.eq(value)] for value in ordered_values]


def _tie_order(day: pd.DataFrame, tie_breaker: str, seed: int) -> list[int]:
    result: list[int] = []
    for indices in _connection_tie_groups(day):
        tie = day.loc[indices].copy()
        if len(tie) == 1:
            result.extend(tie.index.tolist())
            continue
        if tie_breaker == "entry_probability":
            tie = tie.sort_values(["entry_prob", "deterministic_final_key"], ascending=[False, True], kind="mergesort")
        elif tie_breaker == "expected_slot_days":
            tie = tie.sort_values(["expected_slot_days", "deterministic_final_key"], ascending=[True, True], kind="mergesort")
        elif tie_breaker == "sector_relative_extension":
            tie = tie.sort_values(["sector_relative_extension", "deterministic_final_key"], ascending=[True, True], kind="mergesort")
        elif tie_breaker == "source_order":
            tie = tie.sort_values(["source_order", "deterministic_final_key"], ascending=[True, True], kind="mergesort")
        elif tie_breaker == "symbol_independent_hash":
            tie = tie.sort_values(["symbol_independent_hash", "deterministic_final_key"], ascending=[True, True], kind="mergesort")
        elif tie_breaker == "random":
            day_key = f"{day['benchmark'].iloc[0]}|{day['analysis_split'].iloc[0]}|{day['entry_date'].iloc[0]}"
            rng = np.random.default_rng(seed + (_stable_hash(day_key) % (2**31 - 1)))
            shuffled = tie.index.to_numpy(copy=True)
            rng.shuffle(shuffled)
            result.extend(shuffled.tolist())
            continue
        else:
            raise ValueError(f"Unknown connection tie-breaker: {tie_breaker}")
        result.extend(tie.index.tolist())
    return result


def _select(df: pd.DataFrame, tie_breaker: str, seed: int = 0) -> pd.Series:
    if "deterministic_final_key" not in df.columns:
        df = _prepare(df)
    selected = pd.Series(False, index=df.index)
    group_columns = ["benchmark", "analysis_split", "entry_date"]
    for _, day in df.groupby(group_columns, sort=False):
        if not bool(day["capacity_known"].all()):
            continue
        capacity = int(day["capacity_slots"].iloc[0])
        order = _tie_order(day, tie_breaker, seed)
        selected.loc[order[: max(capacity, 0)]] = True
    return selected


def _random_selection_matrix(df: pd.DataFrame, seeds: tuple[int, ...]) -> np.ndarray:
    """Evaluate random legal tie allocation with precomputed daily groups."""
    positions = {index: position for position, index in enumerate(df.index)}
    matrix = np.zeros((len(seeds), len(df)), dtype=bool)
    group_columns = ["benchmark", "analysis_split", "entry_date"]
    for _, day in df.groupby(group_columns, sort=False):
        if not bool(day["capacity_known"].all()):
            continue
        capacity = max(int(day["capacity_slots"].iloc[0]), 0)
        if capacity <= 0:
            continue
        tie_groups = [
            np.asarray([positions[index] for index in indices], dtype=int)
            for indices in _connection_tie_groups(day)
        ]
        day_key = f"{day['benchmark'].iloc[0]}|{day['analysis_split'].iloc[0]}|{day['entry_date'].iloc[0]}"
        day_hash = _stable_hash(day_key) % (2**31 - 1)
        for seed_position, seed in enumerate(seeds):
            remaining = capacity
            rng = np.random.default_rng(int(seed) + day_hash)
            for tie_group in tie_groups:
                if remaining <= 0:
                    break
                if len(tie_group) <= remaining:
                    matrix[seed_position, tie_group] = True
                    remaining -= len(tie_group)
                else:
                    chosen = rng.permutation(tie_group)[:remaining]
                    matrix[seed_position, chosen] = True
                    remaining = 0
    return matrix


def _summary(df: pd.DataFrame, baseline_columns: list[str]) -> pd.DataFrame:
    rows = []
    for split, split_df in df.groupby("analysis_split", sort=True):
        for name in baseline_columns:
            chosen = split_df[split_df[name]]
            rows.append(
                {
                    "analysis_split": split,
                    "baseline": name.removeprefix("selected_"),
                    "candidate_rows": len(split_df),
                    "selected_rows": len(chosen),
                    "selected_days": int(chosen[["benchmark", "entry_date"]].drop_duplicates().shape[0]),
                    "mean_selected_active_te1_pct": float(chosen["te1_active_net_return_pct"].mean()) if len(chosen) else np.nan,
                    "median_selected_active_te1_pct": float(chosen["te1_active_net_return_pct"].median()) if len(chosen) else np.nan,
                    "sum_selected_active_te1_pct": float(chosen["te1_active_net_return_pct"].sum()) if len(chosen) else 0.0,
                    "mean_selected_efficiency_pct": float(chosen["active_return_per_slot_day_pct"].mean()) if len(chosen) else np.nan,
                }
            )
    return pd.DataFrame(rows)


def _chronological_validation_blocks(train: pd.DataFrame, n_splits: int = 5) -> list[pd.Index]:
    groups = (
        train[["benchmark", "entry_date"]]
        .drop_duplicates()
        .sort_values(["entry_date", "benchmark"], kind="mergesort")
        .reset_index(drop=True)
    )
    min_train = max(3, int(np.ceil(len(groups) * 0.4)))
    if len(groups) <= min_train:
        return []
    blocks = np.array_split(np.arange(min_train, len(groups)), min(n_splits, len(groups) - min_train))
    return [groups.iloc[block].set_index(["benchmark", "entry_date"]).index for block in blocks if len(block)]


def _tie_breaker_oof(train: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, str, int | None]:
    rows = []
    blocks = _chronological_validation_blocks(train)
    for fold, block in enumerate(blocks):
        block_keys = set(block.tolist())
        validation_mask = train.apply(lambda row: (row["benchmark"], row["entry_date"]) in block_keys, axis=1)
        validation = train.loc[validation_mask].copy()
        for tie_breaker in DETERMINISTIC_TIE_BREAKERS:
            chosen = validation.loc[_select(validation, tie_breaker)]
            rows.append(
                {
                    "oof_fold": fold,
                    "tie_breaker": tie_breaker,
                    "validation_rows": len(validation),
                    "selected_rows": len(chosen),
                    "mean_active_te1_pct": float(chosen["te1_active_net_return_pct"].mean()) if len(chosen) else np.nan,
                    "median_active_te1_pct": float(chosen["te1_active_net_return_pct"].median()) if len(chosen) else np.nan,
                    "sum_active_te1_pct": float(chosen["te1_active_net_return_pct"].sum()) if len(chosen) else 0.0,
                    "benchmark_breakdown": ";".join(
                        f"{benchmark}:{group['te1_active_net_return_pct'].mean():.6f}"
                        for benchmark, group in chosen.groupby("benchmark", sort=True)
                    ),
                }
            )
    detail = pd.DataFrame(rows)
    if detail.empty:
        return detail, detail, "entry_probability", None
    summary = (
        detail.groupby("tie_breaker", sort=True)
        .agg(
            oof_folds=("oof_fold", "nunique"),
            mean_fold_active_te1_pct=("mean_active_te1_pct", "mean"),
            median_fold_active_te1_pct=("median_active_te1_pct", "median"),
            total_selected_rows=("selected_rows", "sum"),
            total_active_te1_pct=("sum_active_te1_pct", "sum"),
        )
        .reset_index()
        .sort_values(["mean_fold_active_te1_pct", "tie_breaker"], ascending=[False, True], kind="mergesort")
    )
    selected_tie = str(summary.iloc[0]["tie_breaker"])
    return detail, summary, selected_tie, None


def _overlap(df: pd.DataFrame, baseline_columns: list[str], reference: str = "selected_connection_entry_probability") -> pd.DataFrame:
    rows = []
    ref = df[reference]
    for name in baseline_columns:
        current = df[name]
        intersection = int((current & ref).sum())
        union = int((current | ref).sum())
        rows.append(
            {
                "reference": reference.removeprefix("selected_"),
                "baseline": name.removeprefix("selected_"),
                "reference_selected_rows": int(ref.sum()),
                "baseline_selected_rows": int(current.sum()),
                "overlap_rows": intersection,
                "overlap_fraction_of_reference": intersection / int(ref.sum()) if ref.sum() else np.nan,
                "jaccard": intersection / union if union else np.nan,
            }
        )
    return pd.DataFrame(rows)


def evaluate_connection_tiebreakers(
    candidates_path: Path | str = DEFAULT_CANDIDATES,
    capacity_path: Path | str = DEFAULT_CAPACITY,
    output_dir: Path | str = DEFAULT_OUTPUT,
) -> dict[str, Path]:
    candidates_path = Path(candidates_path)
    capacity_path = Path(capacity_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = _prepare(_read_candidates(candidates_path))
    candidates = _merge_capacity(candidates, _read_capacity(capacity_path))

    baselines = {
        "selected_connection_entry_probability": ("entry_probability", 0),
        "selected_expected_slot_days": ("expected_slot_days", 0),
        "selected_sector_relative_extension": ("sector_relative_extension", 0),
        "selected_source_order": ("source_order", 0),
        "selected_symbol_independent_hash": ("symbol_independent_hash", 0),
    }
    for name, (tie_breaker, seed) in baselines.items():
        candidates[name] = _select(candidates, tie_breaker, seed)
    random_columns = []
    random_matrix = _random_selection_matrix(candidates, RANDOM_SEEDS)
    random_columns = [f"selected_random_seed_{seed}" for seed in RANDOM_SEEDS]
    random_frame = pd.DataFrame(random_matrix.T, index=candidates.index, columns=random_columns)
    candidates = pd.concat([candidates, random_frame], axis=1)

    baseline_columns = list(baselines) + random_columns
    scores_columns = [
        c
        for c in (
            "benchmark",
            "analysis_split",
            "entry_date",
            "symbol",
            "event_family",
            "feat_sector",
            "feat_connection_strength",
            "entry_prob",
            "expected_slot_days",
            "sector_relative_extension",
            "capacity_slots",
            "capacity_known",
            "te1_exit_date",
            "t_e",
            "te1_active_net_return_pct",
            "active_return_per_slot_day_pct",
        )
        if c in candidates.columns
    ] + baseline_columns
    scores_path = output_dir / "connection_tiebreaker_scores.csv"
    summary_path = output_dir / "connection_tiebreaker_summary.csv"
    overlap_path = output_dir / "connection_tiebreaker_overlap.csv"
    random_path = output_dir / "connection_random_seed_distribution.csv"
    manifest_path = output_dir / "connection_tiebreaker_manifest.json"
    candidates[scores_columns].to_csv(scores_path, index=False)
    _summary(candidates, baseline_columns).to_csv(summary_path, index=False)
    _overlap(candidates, baseline_columns).to_csv(overlap_path, index=False)

    random_rows = []
    for name in random_columns:
        selected = candidates[candidates[name]]
        for split, group in selected.groupby("analysis_split", sort=True):
            random_rows.append(
                {
                    "seed": int(name.removeprefix("selected_random_seed_")),
                    "analysis_split": split,
                    "selected_rows": len(group),
                    "mean_selected_active_te1_pct": float(group["te1_active_net_return_pct"].mean()) if len(group) else np.nan,
                    "sum_selected_active_te1_pct": float(group["te1_active_net_return_pct"].sum()) if len(group) else 0.0,
                    "selected_benchmarks": ",".join(sorted(group["benchmark"].astype(str).unique())),
                }
            )
    pd.DataFrame(random_rows).to_csv(random_path, index=False)

    train_candidates = candidates[candidates["analysis_split"].astype(str).str.lower().eq("train")].copy()
    oof_detail, oof_summary, oof_selected_tie_breaker, _ = _tie_breaker_oof(train_candidates)
    oof_detail_path = output_dir / "connection_tiebreaker_oof_detail.csv"
    oof_summary_path = output_dir / "connection_tiebreaker_oof_summary.csv"
    oof_choice_path = output_dir / "connection_tiebreaker_oof_choice.json"
    oof_detail.to_csv(oof_detail_path, index=False)
    oof_summary.to_csv(oof_summary_path, index=False)
    random_train = pd.DataFrame(random_rows)
    random_train = random_train[random_train["analysis_split"].astype(str).str.lower().eq("train")]
    random_seed = None
    if not random_train.empty:
        seed_means = random_train.groupby("seed")["mean_selected_active_te1_pct"].mean()
        median_value = float(seed_means.median())
        random_seed = int((seed_means - median_value).abs().sort_values(kind="mergesort").index[0])
    oof_choice_path.write_text(
        json.dumps(
            {
                "selection_scope": "chronological_train_oof",
                "primary_target": "te1_active_net_return_pct",
                "selected_deterministic_tie_breaker": oof_selected_tie_breaker,
                "random_median_seed": random_seed,
                "current_test_is_exploratory_only": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    manifest = {
        "label": "connection_strength_plus_explicit_tie_breaker",
        "candidate_sha256": hashlib.sha256(candidates_path.read_bytes()).hexdigest(),
        "capacity_sha256": hashlib.sha256(capacity_path.read_bytes()).hexdigest(),
        "connection_rule": "descending feat_connection_strength; tie only is delegated to the named tie-breaker",
        "tie_breakers": {
            "entry_probability": "entry_prob descending",
            "expected_slot_days": "feat_time_to_resolution_days ascending",
            "sector_relative_extension": "feat_asset_2w_trend - feat_sector_1m_trend ascending",
            "source_order": "original candidate row order ascending",
            "symbol_independent_hash": "SHA-256 of non-symbol candidate/date features",
            "random": {"seeds": list(RANDOM_SEEDS)},
        },
        "terminal_horizon": "te1_exit_date, strictly before t_e",
        "te_is_never_exit": True,
        "dynamic_portfolio_replay": False,
        "oof_selected_tie_breaker": oof_selected_tie_breaker,
        "random_median_seed": random_seed,
        "outputs": {
            "scores": str(scores_path),
            "summary": str(summary_path),
            "overlap": str(overlap_path),
            "random_distribution": str(random_path),
            "oof_detail": str(oof_detail_path),
            "oof_summary": str(oof_summary_path),
            "oof_choice": str(oof_choice_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return {
        "scores": scores_path,
        "summary": summary_path,
        "overlap": overlap_path,
        "random_distribution": random_path,
        "manifest": manifest_path,
    }


if __name__ == "__main__":
    for name, path in evaluate_connection_tiebreakers().items():
        print(f"{name}: {path}")
