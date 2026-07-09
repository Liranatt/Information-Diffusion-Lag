import pandas as pd
from typing import Any

# Walk-forward configuration (from original experiments)
WF_STEP_MON = 1
WF_EVAL_MON = 2
WF_MIN_TRAIN_CANDS = 50
WF_MIN_EVAL_CANDS = 10


def as_utc_day(value: Any) -> pd.Timestamp:
    """Convert a date-like value to a normalized UTC Timestamp."""
    ts = pd.Timestamp(value)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.normalize()


def _as_utc_timestamp(value: Any) -> pd.Timestamp:
    """Convert a timestamp-like value to UTC without discarding its time."""
    ts = pd.Timestamp(value)
    if ts.tz is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _frame_bounds(df: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    ts = pd.to_datetime(df["t_theta"], utc=True)
    return as_utc_day(ts.min()), as_utc_day(ts.max())


def assert_rows_completed_before(
    df: pd.DataFrame,
    cutoff: Any,
    *,
    context: str,
) -> None:
    """Fail closed if a fit contains any label incomplete at its cutoff."""
    if df.empty:
        raise ValueError(f"{context}: no rows are available for fitting.")

    cutoff_ts = _as_utc_timestamp(cutoff)
    completed_at = pd.to_datetime(df["t_e"], utc=True)
    invalid = completed_at >= cutoff_ts
    if invalid.any():
        first_bad = completed_at.loc[invalid].min()
        raise ValueError(
            f"{context}: {int(invalid.sum())} fit row(s) have t_e >= "
            f"{cutoff_ts.isoformat()}; earliest invalid t_e={first_bad.isoformat()}. "
            "Training eligibility must be determined by t_e < fold start."
        )


def create_expanding_wf_folds(
    train_df: pd.DataFrame,
) -> list[dict[str, Any]]:
    """
    Construct expanding, label-complete walk-forward folds from the dataframe.

    Fold i:
      fit rows  = every candidate with t_e < fold_i.eval_start
      eval rows = candidates with t_theta in [eval_start, eval_end_exclusive)
    """
    if train_df.empty:
        return []

    theta = pd.to_datetime(train_df["t_theta"], utc=True)
    t_e = pd.to_datetime(train_df["t_e"], utc=True)

    first_candidate_day = as_utc_day(theta.min())
    last_eval_exclusive = as_utc_day(theta.max()) + pd.Timedelta(days=1)

    folds: list[dict[str, Any]] = []
    eval_start = first_candidate_day

    while eval_start < last_eval_exclusive:
        eval_end_exclusive = min(
            as_utc_day(eval_start + pd.DateOffset(months=WF_EVAL_MON)),
            last_eval_exclusive,
        )
        if eval_end_exclusive <= eval_start:
            break

        fit_df = train_df.loc[t_e < eval_start].copy()
        eval_df = train_df.loc[
            (theta >= eval_start) & (theta < eval_end_exclusive)
        ].copy()

        if len(fit_df) >= WF_MIN_TRAIN_CANDS and len(eval_df) >= WF_MIN_EVAL_CANDS:
            assert_rows_completed_before(
                fit_df,
                eval_start,
                context=f"walk-forward fold {len(folds) + 1}",
            )
            fit_start, _ = _frame_bounds(fit_df)
            folds.append(
                {
                    "fold": len(folds) + 1,
                    "fit_df": fit_df,
                    "fit_start": fit_start,
                    "fit_cutoff": eval_start,
                    "fit_end": eval_start - pd.Timedelta(days=1),
                    "eval_df": eval_df,
                    "eval_start": eval_start,
                    "eval_end": eval_end_exclusive - pd.Timedelta(days=1),
                    "eval_end_exclusive": eval_end_exclusive,
                }
            )

        eval_start = as_utc_day(eval_start + pd.DateOffset(months=WF_STEP_MON))

    return folds
