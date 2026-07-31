"""Pairwise Logistic V2 with robust, grouped out-of-fold validation.

V2 is intentionally a compact selection model.  It excludes probability
surge, raw symbols, event identifiers, and the broad fundamental feature set
used by the first pass.  Every preprocessing statistic is fitted inside the
training portion of a chronological fold.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.calibration import calibration_curve
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler

from .baselines import _apply_selection, _merge_capacity, _read_capacity, _summary
from .decision_dataset import EX_ANTE_FEATURES


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATES = PROJECT / "data" / "selection_stage1" / "decision_candidates.csv"
DEFAULT_PAIRS = PROJECT / "data" / "selection_stage1" / "competition_pairs.csv"
DEFAULT_CAPACITY = (
    PROJECT
    / "assistant_generated_all_artifacts"
    / "assistant_generated_research_core"
    / "trade_opportunity_research"
    / "same_day_capacity_summary.csv"
)
DEFAULT_OUTPUT = PROJECT / "data" / "selection_stage2b" / "pairwise_v2"

V2_FEATURES = (
    "feat_connection_strength",
    "entry_prob",
    "connection_rank_pct",
    "feat_time_to_resolution_days",
    "feat_asset_2w_trend",
    "feat_sector_1m_trend",
    "stock_sector_extension",
)
V2_BASE_FEATURES = tuple(feature for feature in V2_FEATURES if feature != "stock_sector_extension")
V2_C_VALUES = (0.03, 0.1, 0.3, 1.0)
V2_WINSOR_QUANTILES = (0.01, 0.99)
V2_N_OUTER_FOLDS = 5
V2_N_INNER_FOLDS = 3


class TrainWinsorizer(BaseEstimator, TransformerMixin):
    """Clip each feature using quantiles learned from the training rows."""

    def __init__(self, lower_quantile: float = 0.01, upper_quantile: float = 0.99):
        self.lower_quantile = lower_quantile
        self.upper_quantile = upper_quantile

    def fit(self, x: pd.DataFrame, y: Any = None) -> "TrainWinsorizer":
        frame = pd.DataFrame(x).apply(pd.to_numeric, errors="coerce")
        self.feature_names_in_ = np.asarray(frame.columns, dtype=object)
        self.lower_ = frame.quantile(self.lower_quantile).to_numpy(dtype=float)
        self.upper_ = frame.quantile(self.upper_quantile).to_numpy(dtype=float)
        medians = frame.median().to_numpy(dtype=float)
        self.lower_ = np.where(np.isfinite(self.lower_), self.lower_, medians)
        self.upper_ = np.where(np.isfinite(self.upper_), self.upper_, medians)
        self.lower_ = np.where(np.isfinite(self.lower_), self.lower_, 0.0)
        self.upper_ = np.where(np.isfinite(self.upper_), self.upper_, 0.0)
        return self

    def transform(self, x: pd.DataFrame) -> pd.DataFrame:
        frame = pd.DataFrame(x, columns=self.feature_names_in_).apply(pd.to_numeric, errors="coerce")
        values = frame.to_numpy(dtype=float)
        values = np.maximum(values, self.lower_)
        values = np.minimum(values, self.upper_)
        return pd.DataFrame(values, columns=self.feature_names_in_, index=frame.index)

    def get_feature_names_out(self, input_features: Iterable[str] | None = None) -> np.ndarray:
        return np.asarray(self.feature_names_in_, dtype=object)


class MissingnessAugmenter(BaseEstimator, TransformerMixin):
    """Append one missingness indicator for every compact model feature."""

    def fit(self, x: pd.DataFrame, y: Any = None) -> "MissingnessAugmenter":
        frame = pd.DataFrame(x)
        self.feature_names_in_ = np.asarray(frame.columns, dtype=object)
        return self

    def transform(self, x: pd.DataFrame) -> pd.DataFrame:
        frame = pd.DataFrame(x, columns=self.feature_names_in_)
        indicators = frame.isna().astype(float)
        indicators.columns = [f"missing_{name}" for name in self.feature_names_in_]
        return pd.concat([frame, indicators], axis=1)

    def get_feature_names_out(self, input_features: Iterable[str] | None = None) -> np.ndarray:
        names = list(self.feature_names_in_)
        return np.asarray(names + [f"missing_{name}" for name in names], dtype=object)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_dates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ("entry_date", "t_e", "te1_exit_date"):
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce", utc=True).dt.normalize()
    return out


def _candidate_features(candidates: pd.DataFrame) -> pd.DataFrame:
    frame = candidates.copy()
    for feature in V2_BASE_FEATURES:
        if feature in frame.columns:
            frame[feature] = pd.to_numeric(frame[feature], errors="coerce")
        else:
            frame[feature] = np.nan
    frame["stock_sector_extension"] = frame["feat_asset_2w_trend"] - frame["feat_sector_1m_trend"]
    return frame.loc[:, list(V2_FEATURES)]


def _pair_features(pairs: pd.DataFrame) -> pd.DataFrame:
    frame = pd.DataFrame(index=pairs.index)
    for feature in V2_BASE_FEATURES:
        if feature == "connection_rank_pct":
            column = "diff_connection_rank_pct"
        else:
            column = f"diff_{feature}"
        frame[feature] = pd.to_numeric(pairs.get(column), errors="coerce")
    frame["stock_sector_extension"] = (
        pd.to_numeric(pairs.get("diff_feat_asset_2w_trend"), errors="coerce")
        - pd.to_numeric(pairs.get("diff_feat_sector_1m_trend"), errors="coerce")
    )
    return frame.loc[:, list(V2_FEATURES)]


def _decision_group(pairs: pd.DataFrame) -> pd.Series:
    date = pd.to_datetime(pairs["entry_date"], errors="coerce", utc=True).dt.strftime("%Y-%m-%d")
    # Grouping by benchmark and entry date keeps every pair from one daily
    # opportunity set together.  t_e is retained separately for diagnostics.
    return pairs["benchmark"].astype(str) + "|" + date.fillna("missing")


def _add_pair_metadata(pairs: pd.DataFrame) -> pd.DataFrame:
    out = _parse_dates(pairs)
    out["decision_group"] = _decision_group(out)
    event_left = out.get("left_event_family", pd.Series(index=out.index, dtype=object)).fillna("").astype(str)
    event_right = out.get("right_event_family", pd.Series(index=out.index, dtype=object)).fillna("").astype(str)
    out["event_family_group"] = np.where(event_left.eq(event_right), event_left, "mixed")
    size = pd.to_numeric(out.get("same_day_candidate_count"), errors="coerce")
    out["competition_size"] = size
    out["competition_size_bin"] = pd.cut(
        size,
        bins=[-np.inf, 4, 9, np.inf],
        labels=["<5", "5-9", "10+"],
    ).astype(object).fillna("unknown")
    return out


def _pair_weights(pairs: pd.DataFrame) -> pd.Series:
    """Give each decision group unit mass and each left candidate equal mass."""
    group = pairs["decision_group"]
    left = pairs["left_symbol"].astype(str)
    left_key = group.astype(str) + "|" + left
    left_pair_count = left_key.groupby(left_key).transform("size")
    selected_left_count = left_key.groupby(group).transform("nunique")
    return 1.0 / selected_left_count / left_pair_count


def _weighted_log_loss(y: pd.Series, probability: np.ndarray, weight: pd.Series) -> float:
    return float(log_loss(y, probability, labels=[0, 1], sample_weight=weight))


def _make_pipeline(c: float) -> Pipeline:
    return Pipeline(
        [
            ("winsor", TrainWinsorizer(*V2_WINSOR_QUANTILES)),
            ("missing", MissingnessAugmenter()),
            ("imputer", __import__("sklearn.impute", fromlist=["SimpleImputer"]).SimpleImputer(strategy="median")),
            ("scaler", RobustScaler(quantile_range=(10.0, 90.0))),
            (
                "model",
                LogisticRegression(C=float(c), max_iter=3000, random_state=0),
            ),
        ]
    )


def _fit_predict(train: pd.DataFrame, validation: pd.DataFrame, c: float) -> tuple[Pipeline, np.ndarray]:
    x_train = _pair_features(train)
    x_validation = _pair_features(validation)
    y_train = pd.to_numeric(train["right_beats_left_active"], errors="coerce").astype(int)
    weights = train["sample_weight"].to_numpy(dtype=float)
    if y_train.nunique() < 2:
        raise ValueError("Pairwise V2 training fold needs both pairwise outcomes")
    pipeline = _make_pipeline(c)
    pipeline.fit(x_train, y_train, model__sample_weight=weights)
    probability = pipeline.predict_proba(x_validation)[:, 1]
    return pipeline, probability


def _chronological_folds(pairs: pd.DataFrame, n_splits: int) -> list[tuple[np.ndarray, np.ndarray]]:
    groups = (
        pairs[["decision_group", "entry_date"]]
        .drop_duplicates("decision_group")
        .sort_values(["entry_date", "decision_group"], kind="mergesort")
        .reset_index(drop=True)
    )
    if len(groups) < 4:
        return []
    min_train = max(3, int(np.ceil(len(groups) * 0.4)))
    remaining = len(groups) - min_train
    fold_count = min(int(n_splits), remaining)
    if fold_count <= 0:
        return []
    validation_blocks = np.array_split(np.arange(min_train, len(groups)), fold_count)
    folds: list[tuple[np.ndarray, np.ndarray]] = []
    group_values = pairs["decision_group"].to_numpy()
    for block_indices in validation_blocks:
        if len(block_indices) == 0:
            continue
        block = groups.iloc[block_indices]
        validation_groups = set(block["decision_group"])
        first_date = block["entry_date"].min()
        train_groups = set(groups.loc[groups["entry_date"] < first_date, "decision_group"])
        train_mask = np.array([value in train_groups for value in group_values])
        validation_mask = np.array([value in validation_groups for value in group_values])
        if train_mask.any() and validation_mask.any():
            folds.append((train_mask, validation_mask))
    return folds


def _score_for_c(pairs: pd.DataFrame, c: float, n_splits: int = V2_N_INNER_FOLDS) -> float:
    folds = _chronological_folds(pairs, n_splits)
    losses = []
    for train_mask, validation_mask in folds:
        train = pairs.loc[train_mask]
        validation = pairs.loc[validation_mask]
        y_validation = pd.to_numeric(validation["right_beats_left_active"], errors="coerce").astype(int)
        if y_validation.nunique() < 2:
            continue
        _, probability = _fit_predict(train, validation, c)
        losses.append(_weighted_log_loss(y_validation, probability, validation["sample_weight"]))
    return float(np.mean(losses)) if losses else float("inf")


def _select_c(train: pd.DataFrame) -> tuple[float, pd.DataFrame]:
    rows = []
    for c in V2_C_VALUES:
        rows.append({"C": c, "chronological_inner_log_loss": _score_for_c(train, c)})
    scores = pd.DataFrame(rows)
    best = scores.sort_values(["chronological_inner_log_loss", "C"], kind="mergesort").iloc[0]
    return float(best["C"]), scores


def _metric_row(scope: str, group_name: str, group_value: str, frame: pd.DataFrame) -> dict[str, Any]:
    y = pd.to_numeric(frame["right_beats_left_active"], errors="coerce").astype(int)
    p = pd.to_numeric(frame["predicted_probability"], errors="coerce")
    weights = frame["sample_weight"].to_numpy(dtype=float)
    row: dict[str, Any] = {
        "scope": scope,
        "group_name": group_name,
        "group_value": group_value,
        "pairs": int(len(frame)),
        "weighted_pairs": float(weights.sum()),
        "positive_rate": float(np.average(y, weights=weights)),
        "mean_predicted_probability": float(np.average(p, weights=weights)),
        "accuracy_at_0_5": float(accuracy_score(y, p >= 0.5, sample_weight=weights)),
        "log_loss": _weighted_log_loss(y, p, frame["sample_weight"]),
        "brier_score": float(brier_score_loss(y, p, sample_weight=weights)),
        "auc": float(roc_auc_score(y, p, sample_weight=weights)) if y.nunique() > 1 else float("nan"),
    }
    return row


def _metrics(frame: pd.DataFrame, scope: str) -> pd.DataFrame:
    rows = [_metric_row(scope, "overall", "all", frame)] if not frame.empty else []
    for column in ("benchmark", "event_family_group", "competition_size_bin"):
        for value, group in frame.groupby(column, dropna=False, sort=True):
            if len(group):
                rows.append(_metric_row(scope, column, str(value), group))
    return pd.DataFrame(rows)


def _calibration(frame: pd.DataFrame, scope: str, n_bins: int = 10) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    y = pd.to_numeric(frame["right_beats_left_active"], errors="coerce").astype(int)
    p = pd.to_numeric(frame["predicted_probability"], errors="coerce")
    if y.nunique() < 2:
        return pd.DataFrame()
    observed, predicted = calibration_curve(y, p, n_bins=n_bins, strategy="quantile")
    return pd.DataFrame(
        {
            "scope": scope,
            "bin": np.arange(len(observed)),
            "mean_predicted_probability": predicted,
            "observed_positive_rate": observed,
        }
    )


def _attributions(model: Pipeline, candidates: pd.DataFrame) -> pd.DataFrame:
    x = _candidate_features(candidates)
    current = x
    for name in ("winsor", "missing", "imputer", "scaler"):
        current = model.named_steps[name].transform(current)
    feature_names = model.named_steps["missing"].get_feature_names_out()
    coefficients = model.named_steps["model"].coef_[0]
    transformed = np.asarray(current, dtype=float)
    return pd.DataFrame(
        transformed * coefficients,
        columns=[f"attribution_{name}" for name in feature_names],
        index=candidates.index,
    )


def _fit_oof(pairs: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, float, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train = pairs[pairs["analysis_split"].astype(str).str.lower().eq("train")].copy()
    oof = train.copy()
    oof["oof_probability"] = np.nan
    oof["oof_fold"] = np.nan
    oof["oof_C"] = np.nan
    search_rows = []
    coefficient_rows = []
    preprocessing_rows = []
    folds = _chronological_folds(train, V2_N_OUTER_FOLDS)
    for fold_number, (train_mask, validation_mask) in enumerate(folds):
        inner_train = train.loc[train_mask].copy()
        validation = train.loc[validation_mask].copy()
        chosen_c, inner_scores = _select_c(inner_train)
        for _, row in inner_scores.iterrows():
            search_rows.append(
                {
                    "outer_fold": fold_number,
                    "C": row["C"],
                    "inner_log_loss": row["chronological_inner_log_loss"],
                    "chosen": bool(float(row["C"]) == chosen_c),
                }
            )
        fold_model, probability = _fit_predict(inner_train, validation, chosen_c)
        transformed_names = fold_model.named_steps["missing"].get_feature_names_out()
        coefficients = fold_model.named_steps["model"].coef_[0]
        for feature, coefficient in zip(transformed_names, coefficients):
            coefficient_rows.append(
                {
                    "oof_fold": fold_number,
                    "C": chosen_c,
                    "feature": feature,
                    "coefficient": float(coefficient),
                }
            )
        base_names = list(V2_FEATURES)
        winsor = fold_model.named_steps["winsor"]
        imputer = fold_model.named_steps["imputer"]
        scaler = fold_model.named_steps["scaler"]
        for idx, feature in enumerate(base_names):
            preprocessing_rows.append(
                {
                    "oof_fold": fold_number,
                    "feature": feature,
                    "winsor_lower": float(winsor.lower_[idx]),
                    "winsor_upper": float(winsor.upper_[idx]),
                    "imputer_median": float(imputer.statistics_[idx]),
                    "robust_center": float(scaler.center_[idx]),
                    "robust_scale": float(scaler.scale_[idx]),
                }
            )
        oof.loc[validation.index, "oof_probability"] = probability
        oof.loc[validation.index, "oof_fold"] = fold_number
        oof.loc[validation.index, "oof_C"] = chosen_c
    valid = oof[oof["oof_probability"].notna()].copy()
    valid["predicted_probability"] = valid["oof_probability"]
    metrics = _metrics(valid, "train_grouped_oof")
    return (
        oof,
        metrics,
        _select_c(train)[0],
        pd.DataFrame(search_rows),
        pd.DataFrame(coefficient_rows),
        pd.DataFrame(preprocessing_rows),
    )


def _score_deciles(oof: pd.DataFrame) -> pd.DataFrame:
    valid = oof[oof["oof_probability"].notna()].copy()
    if valid.empty:
        return pd.DataFrame()
    valid["score_decile"] = valid.groupby("oof_fold")["oof_probability"].transform(
        lambda values: pd.qcut(values.rank(method="first"), q=min(10, len(values)), labels=False) + 1
    )
    return (
        valid.groupby(["oof_fold", "score_decile"], sort=True)
        .agg(
            pairs=("right_beats_left_active", "size"),
            mean_score=("oof_probability", "mean"),
            observed_positive_rate=("right_beats_left_active", "mean"),
        )
        .reset_index()
    )


def _largest_contributions(attribution: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    attribution_columns = [c for c in attribution.columns if c.startswith("attribution_")]
    rows = []
    for index, row in attribution.iterrows():
        ranked = row[attribution_columns].abs().sort_values(ascending=False).head(top_n)
        for feature, absolute_value in ranked.items():
            rows.append(
                {
                    "candidate_index": index,
                    "feature": feature.removeprefix("attribution_"),
                    "contribution": float(row[feature]),
                    "absolute_contribution": float(absolute_value),
                }
            )
    return pd.DataFrame(rows)


def evaluate_pairwise_v2(
    candidates_path: Path | str = DEFAULT_CANDIDATES,
    pairs_path: Path | str = DEFAULT_PAIRS,
    capacity_path: Path | str = DEFAULT_CAPACITY,
    output_dir: Path | str = DEFAULT_OUTPUT,
) -> dict[str, Path]:
    candidates_path = Path(candidates_path)
    pairs_path = Path(pairs_path)
    capacity_path = Path(capacity_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    candidates = _parse_dates(pd.read_csv(candidates_path))
    pairs = _add_pair_metadata(pd.read_csv(pairs_path))
    pairs["right_beats_left_active"] = pd.to_numeric(pairs["right_beats_left_active"], errors="coerce")
    pairs["sample_weight"] = _pair_weights(pairs)

    oof, oof_metrics, selected_c, search, fold_coefficients, fold_preprocessing = _fit_oof(pairs)
    train_pairs = pairs[pairs["analysis_split"].astype(str).str.lower().eq("train")]
    final_model = _make_pipeline(selected_c)
    final_model.fit(
        _pair_features(train_pairs),
        train_pairs["right_beats_left_active"].astype(int),
        model__sample_weight=train_pairs["sample_weight"].to_numpy(dtype=float),
    )

    candidate_features = _candidate_features(candidates)
    candidates["score_pairwise_v2"] = final_model.decision_function(candidate_features)
    candidates["pairwise_v2_probability"] = final_model.predict_proba(candidate_features)[:, 1]
    scored = _merge_capacity(candidates, _read_capacity(capacity_path))
    _apply_selection(scored, "score_pairwise_v2", "pairwise_v2")

    pair_predictions = pairs.copy()
    pair_predictions["predicted_probability"] = final_model.predict_proba(_pair_features(pairs))[:, 1]
    pair_predictions["predicted_score"] = final_model.decision_function(_pair_features(pairs))
    test_pairs = pair_predictions[pair_predictions["analysis_split"].astype(str).str.lower().eq("test")].copy()
    test_metrics = _metrics(test_pairs, "test_heldout") if not test_pairs.empty else pd.DataFrame()
    metrics = pd.concat([oof_metrics, test_metrics], ignore_index=True)
    calibration_frames = [
        _calibration(
            oof[oof["oof_probability"].notna()].assign(predicted_probability=lambda x: x["oof_probability"]),
            "train_grouped_oof",
        ),
        _calibration(test_pairs, "test_heldout"),
    ]
    for fold, fold_frame in oof[oof["oof_probability"].notna()].groupby("oof_fold", sort=True):
        calibration_frames.append(
            _calibration(
                fold_frame.assign(predicted_probability=lambda x: x["oof_probability"]),
                f"train_oof_fold_{int(fold)}",
            )
        )
    calibration = pd.concat(calibration_frames, ignore_index=True)

    attribution = _attributions(final_model, candidates)
    score_export = pd.concat(
        [
            candidates.loc[:, [c for c in ("benchmark", "analysis_split", "entry_date", "symbol", "event_family", "feat_sector", "t_e", "te1_exit_date", "score_pairwise_v2", "pairwise_v2_probability", "te1_active_net_return_pct") if c in candidates.columns]],
            candidate_features,
            attribution,
        ],
        axis=1,
    )
    extreme = pd.concat(
        [score_export.nlargest(25, "score_pairwise_v2").assign(extreme="largest_positive"), score_export.nsmallest(25, "score_pairwise_v2").assign(extreme="largest_negative")],
        ignore_index=True,
    )
    largest_contributions = _largest_contributions(attribution)

    summary = _summary(scored, ("pairwise_v2",))
    outputs = {
        "scores": output_dir / "pairwise_v2_scores.csv",
        "selected": output_dir / "pairwise_v2_selected_rows.csv",
        "oof_pairs": output_dir / "pairwise_v2_oof_pairs.csv",
        "metrics": output_dir / "pairwise_v2_metrics.csv",
        "calibration": output_dir / "pairwise_v2_calibration.csv",
        "regularization_search": output_dir / "pairwise_v2_regularization_search.csv",
        "fold_coefficients": output_dir / "pairwise_v2_fold_coefficients.csv",
        "fold_preprocessing": output_dir / "pairwise_v2_fold_preprocessing.csv",
        "score_deciles": output_dir / "pairwise_v2_oof_score_deciles.csv",
        "attribution": output_dir / "pairwise_v2_score_extremes.csv",
        "largest_contributions": output_dir / "pairwise_v2_largest_contributions.csv",
        "summary": output_dir / "pairwise_v2_summary.csv",
        "model": output_dir / "pairwise_v2_model.json",
        "manifest": output_dir / "pairwise_v2_manifest.json",
    }
    score_export.to_csv(outputs["scores"], index=False)
    scored[scored["selected_pairwise_v2"]].to_csv(outputs["selected"], index=False)
    oof.to_csv(outputs["oof_pairs"], index=False)
    metrics.to_csv(outputs["metrics"], index=False)
    calibration.to_csv(outputs["calibration"], index=False)
    search.to_csv(outputs["regularization_search"], index=False)
    fold_coefficients.to_csv(outputs["fold_coefficients"], index=False)
    fold_preprocessing.to_csv(outputs["fold_preprocessing"], index=False)
    _score_deciles(oof).to_csv(outputs["score_deciles"], index=False)
    extreme.to_csv(outputs["attribution"], index=False)
    largest_contributions.to_csv(outputs["largest_contributions"], index=False)
    summary.to_csv(outputs["summary"], index=False)

    model_manifest = {
        "features": list(V2_FEATURES),
        "excluded_features": ["feat_prob_surge_since_t0"],
        "not_available_in_source": ["probability_price_disagreement", "supporting_market_agreement", "supporting_market_dispersion"],
        "regularization_C": selected_c,
        "regularization_candidates": list(V2_C_VALUES),
        "winsor_quantiles": list(V2_WINSOR_QUANTILES),
        "robust_scaler_quantile_range": [10.0, 90.0],
        "missingness_indicators": True,
        "pair_weighting": "unit mass per benchmark/entry-date group and equal mass per left candidate",
        "training_split": "train",
        "coefficients_on_transformed_features": {
            feature: float(coefficient)
            for feature, coefficient in zip(
                final_model.named_steps["missing"].get_feature_names_out(),
                final_model.named_steps["model"].coef_[0],
            )
        },
        "intercept": float(final_model.named_steps["model"].intercept_[0]),
    }
    outputs["model"].write_text(json.dumps(model_manifest, indent=2, default=str) + "\n", encoding="utf-8")

    manifest = {
        "label": "pairwise_logistic_v2",
        "candidate_sha256": _hash(candidates_path),
        "pairs_sha256": _hash(pairs_path),
        "capacity_sha256": _hash(capacity_path),
        "terminal_horizon": "te1_exit_date, strictly before t_e",
        "te_is_never_exit": True,
        "dynamic_portfolio_replay": False,
        "model": str(outputs["model"]),
        "metrics": str(outputs["metrics"]),
        "oof_folds": V2_N_OUTER_FOLDS,
        "outputs": {name: str(path) for name, path in outputs.items()},
    }
    outputs["manifest"].write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return outputs


if __name__ == "__main__":
    for name, path in evaluate_pairwise_v2().items():
        print(f"{name}: {path}")
