"""Leakage-audited stage-one selection baselines.

This module ranks entries only.  It does not learn exits, sizing, or a policy
that can hold through ``t_e``.  The evaluation horizon is the stored final
legal session before ``t_e`` (``te1_exit_date``).

The three selectors are deliberately small:

* ``connection``: the existing connection-strength rank;
* ``fixed_monotonic``: an equal-weight rank average of connection, entry
  probability, and low run-up, with no fitted parameters;
* ``pairwise_logistic``: a regularized pairwise model fit only on historical
  training conflict pairs and scored as a utility for each candidate.

The retrospective ``oracle_te1`` column is an upper bound for diagnostics,
not a tradable model and never a training feature.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

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
DEFAULT_OUTPUT = PROJECT / "data" / "selection_stage1"

PAIRWISE_TARGET = "right_beats_left_active"
PAIRWISE_C = 0.1


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _read_candidates(path: Path | str) -> pd.DataFrame:
    df = pd.read_csv(path)
    for col in ("entry_date", "t_e", "te1_exit_date"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True).dt.normalize()
    for col in EX_ANTE_FEATURES + (
        "connection_rank_pct",
        "entry_prob_rank_pct",
        "runup_rank_pct",
        "te1_active_net_return_pct",
        "active_return_per_slot_day_pct",
    ):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "legacy_selected" in df.columns:
        df["legacy_selected"] = df["legacy_selected"].map(_as_bool)
    else:
        df["legacy_selected"] = False
    return df


def _read_capacity(path: Path | str) -> pd.DataFrame:
    cap = pd.read_csv(path)
    if "split" in cap.columns and "analysis_split" not in cap.columns:
        cap = cap.rename(columns={"split": "analysis_split"})
    cap["entry_date"] = pd.to_datetime(cap["entry_date"], errors="coerce", utc=True).dt.normalize()
    for col in ("free_slots_before", "selected", "eligible"):
        if col in cap.columns:
            cap[col] = pd.to_numeric(cap[col], errors="coerce")
    key = ["benchmark", "analysis_split", "entry_date"]
    if cap.duplicated(key).any():
        raise ValueError("Capacity summary must have one row per benchmark/split/day")
    return cap


def _merge_capacity(candidates: pd.DataFrame, capacity: pd.DataFrame) -> pd.DataFrame:
    key = ["benchmark", "analysis_split", "entry_date"]
    keep = key + [c for c in ("free_slots_before", "selected", "eligible", "same_day_choice_exists") if c in capacity]
    merged = candidates.merge(capacity[keep], on=key, how="left", suffixes=("", "_capacity"))
    merged["capacity_known"] = merged["free_slots_before"].notna()
    merged["capacity_slots"] = merged["free_slots_before"].fillna(0).clip(lower=0).round().astype(int)
    return merged


def _connection_score(df: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(df["connection_rank_pct"], errors="coerce").fillna(0.0)


def _fixed_monotonic_score(df: pd.DataFrame) -> pd.Series:
    columns = ["connection_rank_pct", "entry_prob_rank_pct", "runup_rank_pct"]
    values = df[columns].apply(pd.to_numeric, errors="coerce").fillna(0.5)
    # All three ranks are oriented so larger is preferred.  Equal weights are
    # fixed before evaluation; no tuning is performed on this dataset.
    return values.mean(axis=1)


class PairwiseLogisticRanker:
    """Pairwise ranker with a fixed, regularized training recipe."""

    def __init__(self, features: tuple[str, ...] = EX_ANTE_FEATURES, c: float = PAIRWISE_C):
        self.features = tuple(features)
        self.c = float(c)
        self.pipeline = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
                ("model", LogisticRegression(C=self.c, max_iter=2000, random_state=0)),
            ]
        )
        self.fitted = False
        self.train_rows = 0
        self.train_positive_rate = float("nan")

    def fit(self, pairs: pd.DataFrame) -> "PairwiseLogisticRanker":
        split = pairs["analysis_split"].astype(str).str.lower()
        train = pairs[split.eq("train")].copy()
        diff_columns = [f"diff_{feature}" for feature in self.features]
        missing = [column for column in diff_columns if column not in train.columns]
        if missing:
            raise ValueError(f"Competition pairs are missing pairwise features: {missing[:3]}")
        train = train.dropna(subset=[PAIRWISE_TARGET])
        if train.empty or train[PAIRWISE_TARGET].nunique() < 2:
            raise ValueError("Training pairs need both outcomes for pairwise logistic ranking")
        x = train[diff_columns].apply(pd.to_numeric, errors="coerce")
        y = pd.to_numeric(train[PAIRWISE_TARGET], errors="coerce").astype(int)
        self.pipeline.fit(x, y)
        self.fitted = True
        self.train_rows = int(len(train))
        self.train_positive_rate = float(y.mean())
        return self

    def score(self, candidates: pd.DataFrame) -> pd.Series:
        if not self.fitted:
            raise RuntimeError("PairwiseLogisticRanker must be fit before scoring")
        # The fitted model sees pair differences.  A candidate's utility is
        # the same linear score evaluated on its feature vector; rename the
        # columns to the training names so sklearn's schema guard remains
        # active.
        x = candidates.loc[:, list(self.features)].apply(pd.to_numeric, errors="coerce")
        x.columns = [f"diff_{feature}" for feature in self.features]
        return pd.Series(self.pipeline.decision_function(x), index=candidates.index, dtype=float)

    def manifest(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "features": list(self.features),
            "target": PAIRWISE_TARGET,
            "regularization_C": self.c,
            "train_rows": self.train_rows,
            "train_positive_rate": self.train_positive_rate,
            "fit_split": "train",
        }
        if self.fitted:
            model = self.pipeline.named_steps["model"]
            result["coefficients_on_standardized_features"] = {
                feature: float(coef)
                for feature, coef in zip(self.features, model.coef_[0])
            }
            result["intercept"] = float(model.intercept_[0])
        return result


def _stable_select(day: pd.DataFrame, score_column: str, baseline: str) -> pd.Series:
    selected = pd.Series(False, index=day.index)
    if not bool(day["capacity_known"].all()):
        # A missing capacity state is not silently treated as unlimited.
        return selected
    capacity = int(day["capacity_slots"].iloc[0])
    if capacity <= 0:
        return selected
    sort_columns = [score_column, "entry_prob", "symbol"]
    ascending = [False, False, True]
    ordered = day.sort_values(sort_columns, ascending=ascending, kind="mergesort")
    selected.loc[ordered.index[:capacity]] = True
    return selected


def _apply_selection(df: pd.DataFrame, score_column: str, baseline: str) -> None:
    output_column = f"selected_{baseline}"
    df[output_column] = False
    group_columns = ["benchmark", "analysis_split", "entry_date"]
    for _, day in df.groupby(group_columns, sort=False):
        df.loc[day.index, output_column] = _stable_select(day, score_column, baseline)


def _add_scores(df: pd.DataFrame, ranker: PairwiseLogisticRanker) -> None:
    df["score_connection"] = _connection_score(df)
    df["score_fixed_monotonic"] = _fixed_monotonic_score(df)
    df["score_pairwise_v1"] = ranker.score(df)
    # This is a retrospective ceiling only.  It is deliberately not passed
    # into a selector or model fit.
    df["score_oracle_te1"] = pd.to_numeric(df["te1_active_net_return_pct"], errors="coerce")


def _summary(df: pd.DataFrame, baselines: tuple[str, ...]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    group_columns = ["analysis_split"]
    for split, split_df in df.groupby(group_columns, sort=True):
        split_name = split if isinstance(split, str) else split[0]
        for baseline in baselines:
            selection_col = f"selected_{baseline}"
            chosen = split_df[split_df[selection_col]]
            legacy = split_df[split_df["legacy_selected"]]
            rows.append(
                {
                    "analysis_split": split_name,
                    "baseline": baseline,
                    "candidate_rows": int(len(split_df)),
                    "candidate_days": int(split_df[["benchmark", "entry_date"]].drop_duplicates().shape[0]),
                    "selected_rows": int(len(chosen)),
                    "selected_days": int(chosen[["benchmark", "entry_date"]].drop_duplicates().shape[0]),
                    "capacity_unknown_rows": int((~split_df["capacity_known"]).sum()),
                    "selection_rate_pct": float(100.0 * len(chosen) / len(split_df)) if len(split_df) else float("nan"),
                    "mean_selected_active_te1_pct": float(chosen["te1_active_net_return_pct"].mean()) if len(chosen) else float("nan"),
                    "median_selected_active_te1_pct": float(chosen["te1_active_net_return_pct"].median()) if len(chosen) else float("nan"),
                    "sum_selected_active_te1_pct": float(chosen["te1_active_net_return_pct"].sum()) if len(chosen) else 0.0,
                    "mean_selected_efficiency_pct": float(chosen["active_return_per_slot_day_pct"].mean()) if len(chosen) else float("nan"),
                    "mean_legacy_active_te1_pct": float(legacy["te1_active_net_return_pct"].mean()) if len(legacy) else float("nan"),
                    "selected_minus_legacy_mean_active_pct": (
                        float(chosen["te1_active_net_return_pct"].mean() - legacy["te1_active_net_return_pct"].mean())
                        if len(chosen) and len(legacy) else float("nan")
                    ),
                }
            )
    return pd.DataFrame(rows)


def evaluate_stage1_baselines(
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

    candidates = _read_candidates(candidates_path)
    pairs = pd.read_csv(pairs_path)
    capacity = _read_capacity(capacity_path)
    df = _merge_capacity(candidates, capacity)

    ranker = PairwiseLogisticRanker().fit(pairs)
    _add_scores(df, ranker)
    _apply_selection(df, "score_connection", "connection")
    _apply_selection(df, "score_fixed_monotonic", "fixed_monotonic")
    _apply_selection(df, "score_pairwise_v1", "pairwise_v1")
    _apply_selection(df, "score_oracle_te1", "oracle_te1")

    baseline_names = ("connection", "fixed_monotonic", "pairwise_v1", "oracle_te1")
    score_columns = [f"score_{name}" for name in baseline_names]
    selection_columns = [f"selected_{name}" for name in baseline_names]
    audit_columns = [
        "benchmark",
        "analysis_split",
        "entry_date",
        "symbol",
        "event_family",
        "feat_sector",
        "legacy_selected",
        "free_slots_before",
        "capacity_slots",
        "capacity_known",
        "same_day_choice_exists",
        "te1_exit_date",
        "t_e",
        "te1_active_net_return_pct",
        "active_return_per_slot_day_pct",
    ]
    output_columns = [
        column
        for column in audit_columns + list(EX_ANTE_FEATURES) + score_columns + selection_columns
        if column in df.columns
    ]
    scores_path = output_dir / "selection_scores.csv"
    summary_path = output_dir / "selection_summary.csv"
    model_path = output_dir / "pairwise_v1_model.json"
    manifest_path = output_dir / "baseline_manifest.json"
    df[output_columns].to_csv(scores_path, index=False)
    _summary(df, baseline_names).to_csv(summary_path, index=False)
    model_path.write_text(json.dumps(ranker.manifest(), indent=2, default=str) + "\n", encoding="utf-8")

    manifest = {
        "candidate_sha256": hashlib.sha256(candidates_path.read_bytes()).hexdigest(),
        "pairs_sha256": hashlib.sha256(pairs_path.read_bytes()).hexdigest(),
        "capacity_sha256": hashlib.sha256(capacity_path.read_bytes()).hexdigest(),
        "terminal_horizon": "te1_exit_date, strictly before t_e",
        "te_is_never_exit": True,
        "feature_columns": list(EX_ANTE_FEATURES),
        "baselines": {
            "connection": {"score": "connection_rank_pct", "fit": False},
            "fixed_monotonic": {
                "score": "mean(connection_rank_pct, entry_prob_rank_pct, runup_rank_pct)",
                "weights": "equal",
                "fit": False,
            },
            "pairwise_v1": ranker.manifest(),
            "oracle_te1": {"diagnostic_only": True, "score": "te1_active_net_return_pct"},
        },
        "capacity_rule": "top score per benchmark/split/entry_date, k=free_slots_before; missing capacity selects none",
        "outputs": {
            "scores": str(scores_path),
            "summary": str(summary_path),
            "pairwise_model": str(model_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return {
        "scores": scores_path,
        "summary": summary_path,
        "pairwise_model": model_path,
        "manifest": manifest_path,
    }


if __name__ == "__main__":
    for name, path in evaluate_stage1_baselines().items():
        print(f"{name}: {path}")
