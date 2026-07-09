"""RandomForest alpha model: train, predict, serialize."""
from __future__ import annotations

import joblib
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from pipeline.data_loader import NUM_FEATURES, CAT_FEATURES, TARGET

MODEL_DIR = Path(__file__).resolve().parents[1] / "data" / "models"


def build_pipeline(
    n_estimators: int = 200,
    max_depth: int = 8,
    min_samples_leaf: int = 10,
    random_state: int = 42,
) -> Pipeline:
    """Create an sklearn Pipeline with preprocessing + RF."""
    preprocessor = ColumnTransformer(
        transformers=[
            ("num", SimpleImputer(strategy="median"), NUM_FEATURES),
            ("cat", OneHotEncoder(handle_unknown="infrequent_if_exist", sparse_output=False), CAT_FEATURES),
        ],
        remainder="drop",
    )
    return Pipeline([
        ("prep", preprocessor),
        ("rf", RandomForestRegressor(
            n_estimators=n_estimators,
            max_depth=max_depth,
            min_samples_leaf=min_samples_leaf,
            random_state=random_state,
            n_jobs=-1,
        )),
    ])


def train(df_train: pd.DataFrame, **rf_kwargs) -> Pipeline:
    """Fit a model on the training split and return the fitted pipeline."""
    pipe = build_pipeline(**rf_kwargs)
    X = df_train[NUM_FEATURES + CAT_FEATURES]
    y = df_train[TARGET]
    pipe.fit(X, y)
    return pipe


def predict(pipe: Pipeline, df: pd.DataFrame) -> pd.Series:
    """Return predicted asset_return for each candidate."""
    X = df[NUM_FEATURES + CAT_FEATURES]
    return pd.Series(pipe.predict(X), index=df.index, name="predicted_return")


def save(pipe: Pipeline, name: str = "rf_alpha") -> Path:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    path = MODEL_DIR / f"{name}.joblib"
    joblib.dump(pipe, path)
    return path


def load(name: str = "rf_alpha") -> Pipeline:
    path = MODEL_DIR / f"{name}.joblib"
    return joblib.load(path)


def evaluate(pipe: Pipeline, df: pd.DataFrame) -> dict:
    """Compute evaluation metrics on a dataset split."""
    preds = predict(pipe, df)
    actual = df[TARGET]
    residuals = actual - preds
    mse = float((residuals ** 2).mean())
    mae = float(residuals.abs().mean())
    corr = float(actual.corr(preds)) if len(df) > 2 else 0.0
    direction_agree = float(((preds > 0) == (actual > 0)).mean())
    return {
        "n": len(df),
        "mse": round(mse, 4),
        "mae": round(mae, 4),
        "corr": round(corr, 4),
        "direction_accuracy": round(direction_agree, 4),
        "mean_predicted": round(float(preds.mean()), 4),
        "mean_actual": round(float(actual.mean()), 4),
    }


def feature_importance(pipe: Pipeline) -> pd.Series:
    """Extract RF feature importances with names."""
    rf = pipe.named_steps["rf"]
    prep = pipe.named_steps["prep"]
    names = list(NUM_FEATURES)
    cat_encoder = prep.transformers_[1][1]
    if hasattr(cat_encoder, "get_feature_names_out"):
        names += list(cat_encoder.get_feature_names_out(CAT_FEATURES))
    else:
        names += CAT_FEATURES
    imp = pd.Series(rf.feature_importances_[:len(names)], index=names)
    return imp.sort_values(ascending=False)
