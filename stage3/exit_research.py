"""Start Stage 3 exit research on development folds only.

This first Stage 3 step freezes the Stage 2B selector, collects its exact
development-fold trades, and creates the legal exit-research sample.  It does
not open the later lockbox, change the selector, or train an exit model yet.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_STAGE2 = PROJECT / "data" / "selection_stage2b" / "nested_final_validation"
DEFAULT_CANDIDATES = PROJECT / "data" / "selection_stage1" / "decision_candidates.csv"
DEFAULT_PRICES = PROJECT / "assistant_generated_all_artifacts" / "assistant_generated_research_large_supplements" / "prices_open_merged.pkl"
DEFAULT_OUTPUT = PROJECT / "data" / "stage3_exit_research"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _slug(value: str) -> str:
    return str(value).replace(".", "_")


def run_stage3_exit_development(
    stage2_dir: Path | str = DEFAULT_STAGE2,
    candidates_path: Path | str = DEFAULT_CANDIDATES,
    prices_path: Path | str = DEFAULT_PRICES,
    output_dir: Path | str = DEFAULT_OUTPUT,
) -> dict[str, Path]:
    stage2_dir, candidates_path, prices_path, output_dir = map(Path, (stage2_dir, candidates_path, prices_path, output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    frozen_path = stage2_dir / "research_frozen_selector.json"
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    if frozen.get("label") != "research_frozen_selector":
        raise ValueError("Stage 3 requires a research_frozen_selector manifest")
    if frozen.get("lockbox_opened", True):
        raise ValueError("Stage 3 refuses to run after lockbox access")
    tie_breaker = str(frozen["tie_breaker"])
    threshold_label = str(frozen.get("admission_threshold_label", "none"))
    trades_rows = []
    replay_root = stage2_dir / "replays"
    for fold_dir in sorted(replay_root.glob("outer_fold_*")):
        fold = int(fold_dir.name.rsplit("_", 1)[-1])
        config_dir = fold_dir / f"{tie_breaker}__threshold_{_slug(threshold_label)}"
        for trade_path in sorted(config_dir.glob("trades_*.csv")):
            benchmark = trade_path.stem.removeprefix("trades_").upper()
            trades = pd.read_csv(trade_path)
            if trades.empty:
                continue
            trades["outer_fold"] = fold
            trades["benchmark"] = benchmark
            trades_rows.append(trades)
    if not trades_rows:
        raise ValueError("No frozen-selector development replay trades found")
    trades = pd.concat(trades_rows, ignore_index=True)
    for column in ("entry_date", "exit_date", "candidate_t_e", "candidate_t_theta"):
        if column in trades.columns:
            trades[column] = pd.to_datetime(trades[column], errors="coerce", utc=True).dt.normalize()
    if "candidate_t_e" not in trades.columns:
        raise ValueError("Frozen development trades are missing candidate_t_e")
    trades["te_is_never_exit_assertion"] = pd.to_datetime(trades["exit_date"], utc=True, errors="coerce") < pd.to_datetime(
        trades["candidate_t_e"], utc=True, errors="coerce"
    )
    if not trades["te_is_never_exit_assertion"].all():
        raise AssertionError("Stage 3 development sample contains an exit at or after T_e")
    candidates = pd.read_csv(candidates_path)
    for column in ("entry_date", "te1_exit_date", "t_e"):
        if column in candidates.columns:
            candidates[column] = pd.to_datetime(candidates[column], errors="coerce", utc=True).dt.normalize()
    candidates = candidates[candidates["analysis_split"].astype(str).str.lower().eq("train")].copy()
    label_columns = [
        "benchmark", "entry_date", "symbol", "event_family", "te1_active_net_return_pct",
        "active_return_per_slot_day_pct", "te1_exit_date", "t_e",
    ]
    labels = candidates[[column for column in label_columns if column in candidates.columns]].drop_duplicates(
        ["benchmark", "entry_date", "symbol"]
    )
    trades = trades.merge(labels, on=["benchmark", "entry_date", "symbol"], how="left", suffixes=("", "_label"))
    # The execution engine may shift the actual fill date from the opportunity
    # date.  Recover missing terminal labels by the stable candidate horizon
    # (benchmark, symbol, candidate T_e) without using any post-entry data.
    missing_terminal = trades["te1_exit_date"].isna() if "te1_exit_date" in trades.columns else pd.Series(True, index=trades.index)
    if missing_terminal.any() and "t_e" in labels.columns:
        fallback = labels.rename(columns={"t_e": "candidate_t_e"}).drop(columns=["entry_date"], errors="ignore")
        fallback = fallback.drop_duplicates(["benchmark", "symbol", "candidate_t_e"])
        fallback_columns = [column for column in ("benchmark", "symbol", "candidate_t_e", "event_family", "te1_active_net_return_pct", "active_return_per_slot_day_pct", "te1_exit_date") if column in fallback.columns]
        recovered = trades.loc[missing_terminal, ["benchmark", "symbol", "candidate_t_e"]].merge(
            fallback[fallback_columns], on=["benchmark", "symbol", "candidate_t_e"], how="left", suffixes=("", "_fallback")
        )
        for column in ("event_family", "te1_active_net_return_pct", "active_return_per_slot_day_pct", "te1_exit_date"):
            fallback_column = f"{column}_fallback"
            if fallback_column in recovered.columns:
                trades.loc[missing_terminal, column] = recovered[fallback_column].to_numpy()
    trades["reference_exit_is_before_te1"] = np.where(
        trades["te1_exit_date"].notna(),
        pd.to_datetime(trades["exit_date"], utc=True, errors="coerce") < pd.to_datetime(trades["te1_exit_date"], utc=True, errors="coerce"),
        np.nan,
    )
    # Derive the legal terminal session independently of outcome labels.  This
    # uses only price timestamps strictly before the scheduled T_e; it does not
    # peek at a future exit or open the lockbox.
    prices = pickle.loads(prices_path.read_bytes())
    legal_te1 = []
    for symbol, t_e in zip(trades["symbol"], trades["candidate_t_e"]):
        observations = prices.get(str(symbol), [])
        prior_dates = [pd.to_datetime(item[0], utc=True).normalize() for item in observations if pd.to_datetime(item[0], utc=True).normalize() < pd.Timestamp(t_e)]
        legal_te1.append(max(prior_dates) if prior_dates else pd.NaT)
    trades["legal_te1_exit_date"] = legal_te1
    trades["legal_te1_date_before_te_assertion"] = pd.to_datetime(trades["legal_te1_exit_date"], utc=True, errors="coerce") < pd.to_datetime(
        trades["candidate_t_e"], utc=True, errors="coerce"
    )
    trades["terminal_te1_label_available"] = trades["te1_active_net_return_pct"].notna()
    trades["exit_research_scope"] = "development_outer_folds_only"
    sample_columns = [
        "outer_fold", "benchmark", "symbol", "entry_date", "exit_date", "candidate_t_e", "te1_exit_date",
        "legal_te1_exit_date",
        "event_family", "realized_exit_reason", "pnl_pct", "te1_active_net_return_pct",
        "active_return_per_slot_day_pct", "reference_exit_is_before_te1", "terminal_te1_label_available",
        "te_is_never_exit_assertion", "exit_research_scope",
    ]
    sample_columns = [column for column in sample_columns if column in trades.columns]
    sample_path = output_dir / "stage3_exit_development_trades.csv"
    trades[sample_columns].to_csv(sample_path, index=False)
    fold_manifest_path = stage2_dir / "nested_outer_fold_manifest.csv"
    folds = pd.read_csv(fold_manifest_path)
    folds["stage3_scope"] = "development_outer_folds_only"
    folds["lockbox_opened"] = False
    folds.to_csv(output_dir / "stage3_development_folds.csv", index=False)
    plan = f"""# Stage 3 Exit Research — Development Folds Only

The Stage 2B research-frozen selector is:

- connection strength descending;
- `{tie_breaker}` tie-breaker;
- minimum connection strength threshold `{threshold_label}`.

This selector is immutable during exit research. The sample contains only
Stage 2B outer development-fold replay trades. The later lockbox was not
opened and is reserved for one final evaluation of the complete frozen
modular pipeline.

The first exit-research diagnostic compares the frozen reference exit path
with the legal terminal `T_e - 1` horizon. Where a terminal return label is
available it is joined; the terminal date itself is derived from price
timestamps strictly before `T_e`. A future
exit model may learn earlier exits, safety exits, and terminal holding, but
every action must satisfy `exit_date < T_e`; `T_e` itself is never an exit.

No selector, ranking rule, tie-breaker, or admission threshold may be changed
after this point.
"""
    plan_path = output_dir / "stage3_exit_research_plan.md"
    plan_path.write_text(plan, encoding="utf-8")
    manifest = {
        "label": "stage3_exit_research_development_only",
        "frozen_selector_path": str(frozen_path),
        "frozen_selector": frozen,
        "source_candidates_sha256": _hash(candidates_path),
        "prices_sha256": _hash(prices_path),
        "stage2_nested_manifest_sha256": _hash(stage2_dir / "nested_final_validation_manifest.json"),
        "development_outer_fold_count": int(folds["outer_fold"].nunique()),
        "development_trade_count": int(len(trades)),
        "terminal_te1_label_coverage": float(trades["terminal_te1_label_available"].mean()),
        "legal_te1_date_coverage": float(trades["legal_te1_exit_date"].notna().mean()),
        "all_legal_te1_dates_before_te": bool(trades["legal_te1_date_before_te_assertion"].all()),
        "te_is_never_exit": True,
        "all_development_exits_before_te": bool(trades["te_is_never_exit_assertion"].all()),
        "lockbox_opened": False,
        "lockbox_reserved_for_final_modular_pipeline_evaluation": True,
        "selector_changes_allowed": False,
        "outputs": {
            "trades": str(sample_path),
            "folds": str(output_dir / "stage3_development_folds.csv"),
            "plan": str(plan_path),
        },
    }
    manifest_path = output_dir / "stage3_exit_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return {"trades": sample_path, "folds": output_dir / "stage3_development_folds.csv", "plan": plan_path, "manifest": manifest_path}


if __name__ == "__main__":
    for name, path in run_stage3_exit_development().items():
        print(f"{name}: {path}")
