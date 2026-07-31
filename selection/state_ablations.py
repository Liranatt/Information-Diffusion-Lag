"""Incremental portfolio-state ablations using chronological training OOF."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler

from .pairwise_v2 import V2_FEATURES
from .ranking_models import _chronological_folds, _target


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATES = PROJECT / "data" / "selection_stage2b" / "ranking_models" / "monotonic_oof.csv"
DEFAULT_OUTPUT = PROJECT / "data" / "selection_stage2b" / "state_ablations"


STATE_FEATURES = {
    "state_0_candidate_only": list(V2_FEATURES),
    "state_1_free_slots_and_gross_exposure": list(V2_FEATURES) + ["free_slots_before", "capacity_slots"],
    "state_2_plus_expected_remaining_slot_days": list(V2_FEATURES) + ["free_slots_before", "capacity_slots"],
    "state_3_plus_sector_and_event_exposure": list(V2_FEATURES) + ["free_slots_before", "capacity_slots", "sector_exposure", "event_family_exposure"],
    "state_4_plus_recent_arrival_pressure": list(V2_FEATURES) + ["free_slots_before", "capacity_slots", "sector_exposure", "event_family_exposure", "recent_5d_candidate_count"],
    "state_5_plus_dispersion_volatility_drawdown": list(V2_FEATURES) + ["free_slots_before", "capacity_slots", "sector_exposure", "event_family_exposure", "recent_5d_candidate_count", "market_dispersion", "market_volatility", "current_drawdown"],
}


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["entry_date"] = pd.to_datetime(frame["entry_date"], utc=True, errors="coerce").dt.normalize()
    for column in set(sum(STATE_FEATURES.values(), [])) | {"te1_active_net_return_pct", "active_return_per_slot_day_pct"}:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "stock_sector_extension" not in frame.columns:
        frame["stock_sector_extension"] = pd.to_numeric(frame.get("feat_asset_2w_trend", np.nan), errors="coerce") - pd.to_numeric(
            frame.get("feat_sector_1m_trend", np.nan), errors="coerce"
        )
    return frame


def _select_by_score(frame: pd.DataFrame, score: pd.Series) -> pd.DataFrame:
    temp = frame.copy()
    temp["_score"] = score.to_numpy()
    selected = []
    for _, group in temp.groupby(["benchmark", "entry_date"], sort=True):
        capacity_series = group["free_slots_before"] if "free_slots_before" in group.columns else group.get("capacity_slots")
        if capacity_series is None:
            capacity_series = pd.Series(dtype=float)
        capacity_values = pd.to_numeric(capacity_series, errors="coerce").dropna()
        capacity = max(int(round(float(capacity_values.iloc[0]))) if not capacity_values.empty else 0, 0)
        order = group.sort_values(["_score", "entry_prob", "symbol"], ascending=[False, False, True], kind="mergesort")
        selected.extend(order.head(capacity).index.tolist())
    return temp.loc[selected]


def run_state_ablations(
    candidates_path: Path | str = DEFAULT_CANDIDATES,
    output_dir: Path | str = DEFAULT_OUTPUT,
) -> dict[str, Path]:
    candidates_path, output_dir = Path(candidates_path), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    frame = _read(candidates_path)
    train = frame[frame["analysis_split"].astype(str).str.lower().eq("train")].copy()
    rows, prediction_rows = [], []
    for state_name, features in STATE_FEATURES.items():
        available = [column for column in features if column in train.columns]
        missing = [column for column in features if column not in train.columns]
        if not available:
            continue
        oof_prediction = pd.Series(np.nan, index=train.index, dtype=float)
        for fold, (train_mask, validation_mask) in enumerate(_chronological_folds(train)):
            model = Pipeline([
                ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
                ("scaler", RobustScaler(quantile_range=(10.0, 90.0))),
                ("ridge", Ridge(alpha=10.0)),
            ])
            model.fit(train.loc[train_mask, available], _target(train.loc[train_mask, "te1_active_net_return_pct"]))
            oof_prediction.loc[train.index[validation_mask]] = model.predict(train.loc[validation_mask, available])
        valid = oof_prediction.notna()
        y = pd.to_numeric(train.loc[valid, "te1_active_net_return_pct"], errors="coerce")
        pred = oof_prediction.loc[valid]
        selected = _select_by_score(train.loc[valid], pred)
        active = pd.to_numeric(selected["te1_active_net_return_pct"], errors="coerce")
        spearman = float(pd.Series(y.to_numpy()).corr(pd.Series(pred.to_numpy()), method="spearman")) if len(y) > 1 else np.nan
        rows.append({
            "state": state_name,
            "available_features": "|".join(available),
            "missing_features": "|".join(missing),
            "oof_rows": int(valid.sum()),
            "oof_mae": float(np.mean(np.abs(y.to_numpy() - pred.to_numpy()))) if len(y) else np.nan,
            "oof_spearman": spearman,
            "selected_oof_rows": int(len(selected)),
            "selected_mean_target_a_pct": float(active.mean()) if active.notna().any() else np.nan,
            "selected_median_target_a_pct": float(active.median()) if active.notna().any() else np.nan,
            "selection_scope": "chronological_training_oof_only",
        })
        if valid.any():
            prediction_rows.append(train.loc[valid, ["benchmark", "entry_date", "symbol", "te1_active_net_return_pct"]].assign(state=state_name, oof_score=pred.to_numpy()))
    summary_path = output_dir / "state_ablation_summary.csv"
    predictions_path = output_dir / "state_ablation_oof_predictions.csv"
    pd.DataFrame(rows).to_csv(summary_path, index=False)
    pd.concat(prediction_rows, ignore_index=True).to_csv(predictions_path, index=False)
    manifest = {
        "label": "incremental_portfolio_state_ablations",
        "states": list(STATE_FEATURES),
        "selection_scope": "chronological training OOF only",
        "raw_symbol_or_event_ids_used": False,
        "unavailable_state_fields_are_reported_not_fabricated": True,
        "current_2026_test_is_exploratory": True,
        "outputs": {"summary": str(summary_path), "predictions": str(predictions_path)},
    }
    manifest_path = output_dir / "state_ablation_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"summary": summary_path, "predictions": predictions_path, "manifest": manifest_path}


if __name__ == "__main__":
    for name, path in run_state_ablations().items():
        print(f"{name}: {path}")
