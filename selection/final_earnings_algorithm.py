"""Freeze the final, bounded earnings algorithm after Stage 3A.

The algorithm deliberately has one deployable research scope: a QQQ-relative
earnings rotation.  Stage 3A did not validate a SPY-relative version, so the
SPY branch returns no trades rather than extrapolating a failed test.

Target B is trained once on all completed historical development observations
with the fixed six-feature ridge specification.  It is not automatically
retuned or expanded with new features.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from selection.stage2c_research import QUALITY_FEATURES, _fit_quality_model, _predict_quality


PROJECT = Path(__file__).resolve().parents[1]
OOF = PROJECT / "data" / "selection_stage2f" / "nested_oof_models" / "stage2f_oof_predictions.csv"
OUTPUT = PROJECT / "data" / "final_earnings_algorithm" / "v1"

BENCHMARK = "QQQ"
ENTRY_THRESHOLD = 0.70
MAX_EVENT_WINDOW_RUNUP = 0.10
MAX_CONCURRENT = 10
POSITION_SIZE_PCT = 0.09
GROSS_EVENT_EXPOSURE = 0.90


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def export(output: Path = OUTPUT) -> dict[str, Any]:
    """Fit the frozen Target B deployment model and write its explicit rules."""
    development = pd.read_csv(OOF)
    if len(development) != 415:
        raise AssertionError(f"Expected the fixed 415-row OOF development universe, got {len(development)}")
    if not development["mapping_type"].eq("direct_issuer").all():
        raise AssertionError("The final algorithm may only train on direct-issuer earnings candidates")
    model = _fit_quality_model(development, "active_return_per_slot_day_pct")
    if model.get("features") != list(QUALITY_FEATURES) or model.get("ridge_penalty") != 5.0:
        raise AssertionError("Target B model specification drifted")
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "target_b_ridge_model.json", model)
    specification = {
        "algorithm_id": "earnings_target_b_qqq_v1",
        "status": "paper_or_micro_only",
        "scope": {
            "event_family": "earnings",
            "mapping_type": "direct_issuer",
            "benchmark": BENCHMARK,
            "spy_branch": "no_trade: Stage 3A did not show positive aggregate active return after timestamp-safe entry",
        },
        "entry": {
            "effective_polymarket_probability_minimum": ENTRY_THRESHOLD,
            "clock": "use only a raw Polymarket observation available strictly before the fill; fill at the next regular-session open",
            "target_b_score": "strictly_positive",
            "max_price_runup_since_event_t0": MAX_EVENT_WINDOW_RUNUP,
            "minimum_legal_sessions_before_resolution": 2,
            "exclude": ["ambiguous_polarity", "invalid_mapping", "missing_price_or_probability_history", "geo", "macro", "indirect_mapping"],
            "rank": ["target_b_score_descending", "expected_slot_days_ascending", "source_order_ascending", "symbol_ascending"],
        },
        "positioning": {
            "max_concurrent_positions": MAX_CONCURRENT,
            "max_position_size_pct": POSITION_SIZE_PCT,
            "max_gross_event_exposure": GROSS_EVENT_EXPOSURE,
        },
        "exit": {
            "name": "volatility_scaled_stop_execution_safe",
            "from_session": 2,
            "standing_stop_loss_pct": "min(max(2%, 2 * pre_entry_ATR20_pct), 8%)",
            "gap_fill": "if opening price is below the stop, exit at the opening price; otherwise exit at the stop when touched",
            "take_profit": "none; do not sell merely because an absolute profit percentage is reached",
            "time_exit": "market-on-close of the final legal session before T_e",
        },
        "measurement": {
            "success": "net active return versus QQQ over the exact same entry-to-exit interval",
            "costs": "include benchmark rotation and asset transaction costs",
            "forbidden_live_features": ["future_probability_observations", "post_entry_path_labels", "probability_trajectory_features", "YTD_or_3m_6m_12m_filters"],
        },
        "model_artifact": "target_b_ridge_model.json",
        "source_oof_rows": int(len(development)),
        "source_oof_sha256": _hash(OOF),
        "stage3a_decision": {
            "qqq_volatility_stop_combined_excess_return_pct": 3.4724,
            "qqq_volatility_stop_paired_median_fold_improvement_pct": 0.7337,
            "qqq_volatility_stop_positive_improvement_folds": 3,
            "spy_status": "not_deployed",
        },
    }
    _write_json(output / "algorithm_specification.json", specification)
    return specification


def score_and_rank(candidates: pd.DataFrame, model: dict[str, Any]) -> pd.DataFrame:
    """Apply the fixed Target B admission/ranking rule to live candidate rows.

    The caller must construct the six frozen features and enforce the timestamp
    and legal-session checks before calling this function.  A non-QQQ mandate
    is intentionally rejected rather than silently changing the benchmark.
    """
    required = {"benchmark", "mapping_type", "mapping_valid", "effective_probability", "feat_runup_since_t0", *QUALITY_FEATURES}
    missing = sorted(required.difference(candidates.columns))
    if missing:
        raise ValueError(f"Candidate rows are missing required fields: {missing}")
    out = candidates.copy()
    out["target_b_score"] = _predict_quality(out, model)
    mapping_valid = out["mapping_valid"].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"})
    probability = pd.to_numeric(out["effective_probability"], errors="coerce")
    runup = pd.to_numeric(out["feat_runup_since_t0"], errors="coerce")
    out["admit"] = (
        out["benchmark"].eq(BENCHMARK)
        & out["mapping_type"].eq("direct_issuer")
        & mapping_valid
        & probability.ge(ENTRY_THRESHOLD)
        & runup.le(MAX_EVENT_WINDOW_RUNUP)
        & out["target_b_score"].gt(0.0)
    )
    source = pd.to_numeric(out.get("source_order", np.nan), errors="coerce").fillna(10**9)
    out["_source_order"] = source
    out = out.sort_values(
        ["admit", "target_b_score", "expected_slot_days", "_source_order", "symbol"],
        ascending=[False, False, True, True, True],
        kind="mergesort",
    ).drop(columns=["_source_order"])
    return out


if __name__ == "__main__":
    print(json.dumps(export(), indent=2))
