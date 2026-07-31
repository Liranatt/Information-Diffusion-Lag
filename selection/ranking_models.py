"""Small candidate-ranking models for the completed Stage 2B selection study."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler, SplineTransformer
from sklearn.isotonic import IsotonicRegression

from .baselines import _apply_selection, _merge_capacity, _read_candidates, _read_capacity
from .pairwise_v2 import V2_FEATURES, _candidate_features


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATES = PROJECT / "data" / "selection_stage1" / "decision_candidates.csv"
DEFAULT_CAPACITY = (
    PROJECT
    / "assistant_generated_all_artifacts"
    / "assistant_generated_research_core"
    / "trade_opportunity_research"
    / "same_day_capacity_summary.csv"
)
DEFAULT_OUTPUT = PROJECT / "data" / "selection_stage2b" / "ranking_models"
TARGET = "te1_active_net_return_pct"
MODEL_SEEDS = (0, 1, 2, 3)

MONOTONIC_DIRECTIONS = {
    "feat_connection_strength": 1,
    "feat_time_to_resolution_days": -1,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _chronological_folds(df: pd.DataFrame, n_splits: int = 5) -> list[tuple[np.ndarray, np.ndarray]]:
    groups = (
        df[["benchmark", "entry_date"]]
        .drop_duplicates()
        .sort_values(["entry_date", "benchmark"], kind="mergesort")
        .reset_index(drop=True)
    )
    min_train = max(3, int(np.ceil(len(groups) * 0.4)))
    if len(groups) <= min_train:
        return []
    blocks = np.array_split(np.arange(min_train, len(groups)), min(n_splits, len(groups) - min_train))
    group_values = list(zip(df["benchmark"], df["entry_date"]))
    folds = []
    for block in blocks:
        if len(block) == 0:
            continue
        validation_groups = set(zip(groups.iloc[block]["benchmark"], groups.iloc[block]["entry_date"]))
        validation_mask = np.asarray([value in validation_groups for value in group_values])
        first_date = groups.iloc[block]["entry_date"].min()
        train_groups = set(zip(groups.loc[groups["entry_date"] < first_date, "benchmark"], groups.loc[groups["entry_date"] < first_date, "entry_date"]))
        train_mask = np.asarray([value in train_groups for value in group_values])
        if train_mask.any() and validation_mask.any():
            folds.append((train_mask, validation_mask))
    return folds


def _target(y: pd.Series) -> np.ndarray:
    values = pd.to_numeric(y, errors="coerce").to_numpy(dtype=float, copy=True)
    finite = np.isfinite(values)
    if not finite.any():
        raise ValueError("Ranking target has no finite observations")
    low, high = np.nanquantile(values[finite], [0.01, 0.99])
    values[~finite] = float(np.nanmedian(values[finite]))
    return np.clip(values, low, high)


class _ConstantShape:
    def __init__(self, value: float):
        self.value = float(value)

    def predict(self, values: np.ndarray) -> np.ndarray:
        return np.full(len(values), self.value, dtype=float)


class MonotonicAdditiveRanker:
    """Backfitted one-dimensional shapes with only two hard constraints."""

    def __init__(self, features: tuple[str, ...] = V2_FEATURES, iterations: int = 4):
        self.features = tuple(features)
        self.iterations = int(iterations)
        self.models: dict[str, Any] = {}
        self.medians: dict[str, float] = {}
        self.lowers: dict[str, float] = {}
        self.uppers: dict[str, float] = {}
        self.intercept = 0.0
        self.fitted = False

    def _prepare(self, x: pd.DataFrame, fit: bool = False) -> pd.DataFrame:
        frame = x.loc[:, list(self.features)].apply(pd.to_numeric, errors="coerce").copy()
        if fit:
            for feature in self.features:
                values = frame[feature].to_numpy(dtype=float)
                finite = values[np.isfinite(values)]
                self.medians[feature] = float(np.median(finite)) if len(finite) else 0.0
                self.lowers[feature] = float(np.quantile(finite, 0.01)) if len(finite) else 0.0
                self.uppers[feature] = float(np.quantile(finite, 0.99)) if len(finite) else 0.0
        for feature in self.features:
            frame[feature] = frame[feature].fillna(self.medians[feature]).clip(
                self.lowers[feature], self.uppers[feature]
            )
        return frame

    def fit(self, x: pd.DataFrame, y: pd.Series) -> "MonotonicAdditiveRanker":
        frame = self._prepare(x, fit=True)
        target = _target(y)
        self.intercept = float(np.mean(target))
        contributions = np.zeros((len(frame), len(self.features)), dtype=float)
        for _ in range(self.iterations):
            for index, feature in enumerate(self.features):
                residual = target - self.intercept - contributions.sum(axis=1) + contributions[:, index]
                values = frame[feature].to_numpy(dtype=float)
                if len(np.unique(values)) < 2:
                    model = _ConstantShape(float(np.mean(residual)))
                elif feature in MONOTONIC_DIRECTIONS:
                    model = IsotonicRegression(
                        increasing=MONOTONIC_DIRECTIONS[feature] > 0,
                        out_of_bounds="clip",
                    )
                    model.fit(values, residual)
                else:
                    model = Pipeline(
                        [
                            ("spline", SplineTransformer(n_knots=4, degree=2, include_bias=False)),
                            ("ridge", Ridge(alpha=10.0)),
                        ]
                    )
                    model.fit(values.reshape(-1, 1), residual)
                self.models[feature] = model
                contributions[:, index] = self._predict_shape(feature, values)
        self.fitted = True
        return self

    def _predict_shape(self, feature: str, values: np.ndarray) -> np.ndarray:
        model = self.models[feature]
        if isinstance(model, (IsotonicRegression, _ConstantShape)):
            return np.asarray(model.predict(values), dtype=float)
        return np.asarray(model.predict(values.reshape(-1, 1)), dtype=float)

    def predict(self, x: pd.DataFrame) -> np.ndarray:
        if not self.fitted:
            raise RuntimeError("MonotonicAdditiveRanker must be fitted first")
        frame = self._prepare(x)
        score = np.full(len(frame), self.intercept, dtype=float)
        for feature in self.features:
            score += self._predict_shape(feature, frame[feature].to_numpy(dtype=float))
        return score

    def shapes(self, points: int = 50, fold: int | str = "final") -> pd.DataFrame:
        rows = []
        for feature in self.features:
            grid = np.linspace(self.lowers[feature], self.uppers[feature], points)
            values = self._predict_shape(feature, grid)
            for x_value, contribution in zip(grid, values):
                rows.append(
                    {
                        "fold": fold,
                        "feature": feature,
                        "feature_value": float(x_value),
                        "shape_contribution": float(contribution),
                        "monotonic_constraint": MONOTONIC_DIRECTIONS.get(feature, 0),
                    }
                )
        return pd.DataFrame(rows)

    def manifest(self) -> dict[str, Any]:
        return {
            "features": list(self.features),
            "monotonic_directions": MONOTONIC_DIRECTIONS,
            "unconstrained_features": [f for f in self.features if f not in MONOTONIC_DIRECTIONS],
            "iterations": self.iterations,
            "target": TARGET,
            "train_only_feature_preprocessing": True,
        }


def _pooled_features(df: pd.DataFrame) -> pd.DataFrame:
    base = _candidate_features(df).apply(pd.to_numeric, errors="coerce")
    group = [df["benchmark"], df["analysis_split"], df["entry_date"]]
    mean_context = base.groupby(group, sort=False).transform("mean")
    max_context = base.groupby(group, sort=False).transform("max")
    mean_context.columns = [f"same_day_mean_{feature}" for feature in V2_FEATURES]
    max_context.columns = [f"same_day_max_{feature}" for feature in V2_FEATURES]
    state = pd.DataFrame(
        {
            "free_slots": pd.to_numeric(df["capacity_slots"], errors="coerce"),
            "same_day_candidate_count": pd.to_numeric(df["same_day_candidate_count"], errors="coerce"),
            "recent_5d_candidate_count": pd.to_numeric(df["recent_5d_candidate_count"], errors="coerce"),
        },
        index=df.index,
    )
    return pd.concat([base, mean_context, max_context, state], axis=1)


def _mlp_pipeline(seed: int) -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", RobustScaler(quantile_range=(10.0, 90.0))),
            (
                "mlp",
                MLPRegressor(
                    hidden_layer_sizes=(16, 8),
                    alpha=0.01,
                    max_iter=400,
                    random_state=seed,
                    early_stopping=False,
                ),
            ),
        ]
    )


def _selection_diagnostics(frame: pd.DataFrame, score_column: str, fold: int | str, model: str) -> dict[str, Any]:
    selected = frame[frame[f"selected_{model}"]]
    return {
        "model": model,
        "fold": fold,
        "rows": int(len(frame)),
        "selected_rows": int(len(selected)),
        "mean_selected_active_te1_pct": float(selected[TARGET].mean()) if len(selected) else np.nan,
        "median_selected_active_te1_pct": float(selected[TARGET].median()) if len(selected) else np.nan,
        "sum_selected_active_te1_pct": float(selected[TARGET].sum()) if len(selected) else 0.0,
        "mean_selected_efficiency_pct": float(selected["active_return_per_slot_day_pct"].mean()) if len(selected) else np.nan,
    }


def _run_monotonic(candidates: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, MonotonicAdditiveRanker]:
    train = candidates[candidates["analysis_split"].astype(str).str.lower().eq("train")]
    oof_rows = []
    shape_rows = []
    folds = _chronological_folds(train)
    for fold, (train_mask, validation_mask) in enumerate(folds):
        model = MonotonicAdditiveRanker()
        model.fit(_candidate_features(train.loc[train_mask]), train.loc[train_mask, TARGET])
        validation = train.loc[validation_mask].copy()
        validation["score_monotonic"] = model.predict(_candidate_features(validation))
        _apply_selection(validation, "score_monotonic", "monotonic")
        oof_rows.append(validation)
        shape_rows.append(model.shapes(fold=fold))
    final_model = MonotonicAdditiveRanker().fit(_candidate_features(train), train[TARGET])
    return pd.concat(oof_rows, ignore_index=False) if oof_rows else pd.DataFrame(), pd.concat(shape_rows, ignore_index=True) if shape_rows else pd.DataFrame(), final_model.shapes(), final_model


def _run_pooled_seed(candidates: pd.DataFrame, seed: int) -> tuple[pd.DataFrame, Pipeline]:
    train = candidates[candidates["analysis_split"].astype(str).str.lower().eq("train")]
    oof_rows = []
    for fold, (train_mask, validation_mask) in enumerate(_chronological_folds(train)):
        model = _mlp_pipeline(seed)
        model.fit(_pooled_features(train.loc[train_mask]), _target(train.loc[train_mask, TARGET]))
        validation = train.loc[validation_mask].copy()
        validation["score_pooled"] = model.predict(_pooled_features(validation))
        _apply_selection(validation, "score_pooled", "pooled")
        validation["pooled_seed"] = seed
        validation["oof_fold"] = fold
        oof_rows.append(validation)
    final_model = _mlp_pipeline(seed)
    final_model.fit(_pooled_features(train), _target(train[TARGET]))
    return pd.concat(oof_rows, ignore_index=False) if oof_rows else pd.DataFrame(), final_model


def evaluate_small_ranking_models(
    candidates_path: Path | str = DEFAULT_CANDIDATES,
    capacity_path: Path | str = DEFAULT_CAPACITY,
    output_dir: Path | str = DEFAULT_OUTPUT,
) -> dict[str, Path]:
    candidates_path = Path(candidates_path)
    capacity_path = Path(capacity_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = _read_candidates(candidates_path)
    candidates = _merge_capacity(candidates, _read_capacity(capacity_path))

    monotonic_oof, fold_shapes, final_shapes, monotonic_model = _run_monotonic(candidates)
    train = candidates[candidates["analysis_split"].astype(str).str.lower().eq("train")]
    candidates["score_monotonic"] = monotonic_model.predict(_candidate_features(candidates))
    _apply_selection(candidates, "score_monotonic", "monotonic")
    monotonic_summary = pd.DataFrame([_selection_diagnostics(monotonic_oof, "score_monotonic", "grouped_oof", "monotonic")]) if not monotonic_oof.empty else pd.DataFrame()

    pooled_seed_rows = []
    pooled_oof_by_seed = []
    for seed in MODEL_SEEDS:
        oof, _ = _run_pooled_seed(candidates, seed)
        pooled_oof_by_seed.append(oof)
        if not oof.empty:
            pooled_seed_rows.append(_selection_diagnostics(oof, "score_pooled", f"grouped_oof_seed_{seed}", "pooled"))
            pooled_seed_rows[-1]["seed"] = seed
    pooled_seed_summary = pd.DataFrame(pooled_seed_rows)
    if pooled_seed_summary.empty:
        chosen_seed = MODEL_SEEDS[0]
    else:
        chosen_seed = int(
            pooled_seed_summary.sort_values(
                ["mean_selected_active_te1_pct", "seed"], ascending=[False, True], kind="mergesort"
            ).iloc[0]["seed"]
        )
    pooled_oof = pooled_oof_by_seed[MODEL_SEEDS.index(chosen_seed)]
    _, pooled_model = _run_pooled_seed(candidates, chosen_seed)
    candidates["score_pooled"] = pooled_model.predict(_pooled_features(candidates))
    _apply_selection(candidates, "score_pooled", "pooled")

    outputs = {
        "monotonic_scores": output_dir / "monotonic_scores.csv",
        "monotonic_selected": output_dir / "monotonic_selected_rows.csv",
        "monotonic_oof": output_dir / "monotonic_oof.csv",
        "monotonic_oof_summary": output_dir / "monotonic_oof_summary.csv",
        "monotonic_shapes": output_dir / "monotonic_shape_functions.csv",
        "monotonic_fold_shapes": output_dir / "monotonic_fold_shape_functions.csv",
        "monotonic_model": output_dir / "monotonic_model.json",
        "pooled_scores": output_dir / "pooled_scores.csv",
        "pooled_selected": output_dir / "pooled_selected_rows.csv",
        "pooled_oof": output_dir / "pooled_oof.csv",
        "pooled_seed_summary": output_dir / "pooled_seed_summary.csv",
        "pooled_model": output_dir / "pooled_model.json",
        "manifest": output_dir / "ranking_models_manifest.json",
    }
    candidate_columns = [
        c
        for c in (
            "benchmark", "analysis_split", "entry_date", "symbol", "event_family", "feat_sector",
            "capacity_slots", "te1_exit_date", "t_e", TARGET, "active_return_per_slot_day_pct",
            "score_monotonic", "selected_monotonic", "score_pooled", "selected_pooled",
        )
        if c in candidates.columns
    ]
    candidates[candidate_columns].to_csv(outputs["monotonic_scores"], index=False)
    candidates[candidate_columns].to_csv(outputs["pooled_scores"], index=False)
    candidates[candidates["selected_monotonic"]].to_csv(outputs["monotonic_selected"], index=False)
    candidates[candidates["selected_pooled"]].to_csv(outputs["pooled_selected"], index=False)
    monotonic_oof.to_csv(outputs["monotonic_oof"], index=False)
    monotonic_summary.to_csv(outputs["monotonic_oof_summary"], index=False)
    final_shapes.to_csv(outputs["monotonic_shapes"], index=False)
    fold_shapes.to_csv(outputs["monotonic_fold_shapes"], index=False)
    pooled_oof.to_csv(outputs["pooled_oof"], index=False)
    pooled_seed_summary.to_csv(outputs["pooled_seed_summary"], index=False)

    mono_manifest = monotonic_model.manifest()
    outputs["monotonic_model"].write_text(json.dumps(mono_manifest, indent=2, default=str) + "\n", encoding="utf-8")
    pooled_manifest = {
        "architecture": "candidate compact vector plus same-day mean/max pooling plus capacity/arrival state",
        "hidden_layer_sizes": [16, 8],
        "seeds": list(MODEL_SEEDS),
        "selected_seed_by_train_grouped_oof": chosen_seed,
        "features": list(_pooled_features(candidates).columns),
        "raw_identifiers_used_as_features": False,
        "target": TARGET,
    }
    outputs["pooled_model"].write_text(json.dumps(pooled_manifest, indent=2, default=str) + "\n", encoding="utf-8")
    manifest = {
        "label": "small_stage2b_ranking_models",
        "candidate_sha256": _sha256(candidates_path),
        "capacity_sha256": _sha256(capacity_path),
        "target": TARGET,
        "terminal_horizon": "te1_exit_date, strictly before t_e",
        "te_is_never_exit": True,
        "test_selection_prohibited": True,
        "monotonic_model": str(outputs["monotonic_model"]),
        "pooled_model": str(outputs["pooled_model"]),
        "outputs": {name: str(path) for name, path in outputs.items()},
    }
    outputs["manifest"].write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return outputs


if __name__ == "__main__":
    for name, path in evaluate_small_ranking_models().items():
        print(f"{name}: {path}")
