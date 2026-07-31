"""Frozen, low-degree-of-freedom pseudo-future evaluation.

This evaluator deliberately does not fit weights, thresholds, family rules, or
benchmark-specific parameters.  It evaluates every predeclared capacity,
exit, and cost arm and reports the latest chronological block as a pseudo-
future lockbox.  The output is diagnostic, not a guarantee of future profit.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "assistant_generated_all_artifacts" / "assistant_generated_research_core" / "trade_opportunity_research"
OUT = ROOT / "output" / "future_generalization_20260717"
INPUT = ART / "symbol_day_current_priority.csv"

CAPACITIES = (5, 10, 15)
EXIT_ARMS = ("hardcap", "te1")
COST_MULTIPLIERS = (0.0, 1.0, 2.0, 3.0)
PRIMARY_COST_MULTIPLIER = 1.0
NOTIONAL = 100_000.0
RANDOM_REPS = 50
RANDOM_SEED = 20260717

BLOCKS = (
    ("2024H2", "2024-08-01", "2024-12-31"),
    ("2025H1", "2025-01-01", "2025-06-30"),
    ("2025H2", "2025-07-01", "2025-12-31"),
    ("2026H1_pseudo_future", "2026-01-01", "2026-06-30"),
)


def _markdown(df: pd.DataFrame, columns: list[str] | None = None, digits: int = 3) -> str:
    if df.empty:
        return "_No rows._"
    view = df.copy() if columns is None else df[[c for c in columns if c in df.columns]].copy()
    for col in view.columns:
        if pd.api.types.is_float_dtype(view[col]):
            view[col] = view[col].map(lambda x: f"{x:.{digits}f}" if pd.notna(x) else "n/a")
    view = view.astype(object).where(pd.notna(view), "n/a")
    headers = [str(c).replace("|", "\\|") for c in view.columns]
    rows = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for values in view.itertuples(index=False, name=None):
        rows.append("| " + " | ".join(str(v).replace("|", "\\|") for v in values) + " |")
    return "\n".join(rows)


def _numeric(df: pd.DataFrame, columns: list[str]) -> None:
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")


def _stable_seed(*values: object) -> int:
    raw = "|".join(map(str, values)).encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:8], 16)


def _drawdown_from_trade_pnl(selected: pd.DataFrame) -> float:
    if selected.empty:
        return float("nan")
    by_exit = selected.groupby("exit_date", as_index=False)["pnl_dollars"].sum().sort_values("exit_date")
    equity = NOTIONAL + by_exit["pnl_dollars"].cumsum()
    return float((equity / equity.cummax() - 1.0).min() * 100.0)


def _top_concentration(selected: pd.DataFrame) -> float:
    if selected.empty:
        return float("nan")
    by_symbol = selected.groupby("symbol")["active_pnl_dollars"].sum()
    denom = by_symbol.abs().sum()
    if denom == 0:
        return 0.0
    return float(by_symbol.abs().max() / denom * 100.0)


def _prepare(df: pd.DataFrame, benchmark: str, exit_arm: str, cost_multiplier: float) -> pd.DataFrame:
    out = df[df["benchmark"].eq(benchmark)].copy()
    out["entry_date"] = pd.to_datetime(out["entry_date"], errors="coerce").dt.normalize()
    out["hardcap_exit_date"] = pd.to_datetime(out["hardcap_exit_date"], errors="coerce").dt.normalize()
    out["te1_exit_date"] = pd.to_datetime(out["te1_exit_date_dt"], errors="coerce").dt.normalize()
    # Cost is estimated from the artifact's gross-versus-net T-1 pair.  The
    # multiple is stress only; no multiplier is fitted to outcomes.
    cost_pct = out["stock_te1_gross_return_pct"] - out["stock_te1_net_return_pct"]
    cost_pct = cost_pct.fillna(0.0).clip(lower=0.0)
    if exit_arm == "hardcap":
        out["gross_return_pct"] = out["hardcap_return_pct"]
        out["gross_active_pct"] = out["hardcap_active_vs_benchmark_gross_pct"]
        out["exit_date"] = out["hardcap_exit_date"]
    else:
        out["gross_return_pct"] = out["stock_te1_gross_return_pct"]
        gross_col = f"te1_active_vs_{benchmark.lower()}_gross_pct"
        out["gross_active_pct"] = out[gross_col]
        out["exit_date"] = out["te1_exit_date"]
    out["net_return_pct"] = out["gross_return_pct"] - cost_multiplier * cost_pct
    out["net_active_pct"] = out["gross_active_pct"] - cost_multiplier * cost_pct
    out = out.dropna(subset=["entry_date", "exit_date", "net_return_pct", "net_active_pct"])
    out = out[out["exit_date"] >= out["entry_date"]].copy()
    return out


def _sort_ranked(day: pd.DataFrame, reverse: bool = False) -> pd.DataFrame:
    # No fitted score: this is a fixed hierarchy chosen before evaluation.
    # Missing quality values rank below observed values.
    cols = ["feat_connection_strength", "entry_prob", "feat_runup_since_t0", "symbol"]
    ranked = day.copy()
    ranked["_conn"] = ranked["feat_connection_strength"].fillna(-np.inf)
    ranked["_prob"] = ranked["entry_prob"].fillna(-np.inf)
    ranked["_runup"] = ranked["feat_runup_since_t0"].fillna(np.inf)
    if reverse:
        return ranked.sort_values(["_conn", "_prob", "_runup", "symbol"], ascending=[True, True, False, True], kind="mergesort")
    return ranked.sort_values(["_conn", "_prob", "_runup", "symbol"], ascending=[False, False, True, True], kind="mergesort")


def _select_portfolio(
    df: pd.DataFrame,
    capacity: int,
    mode: str,
    benchmark: str,
    block: str,
    random_seed: int | None = None,
) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    selected_rows: list[dict[str, object]] = []
    open_positions: list[tuple[str, pd.Timestamp]] = []
    for entry_date, day in df.sort_values(["entry_date", "symbol"]).groupby("entry_date", sort=True):
        open_positions = [(symbol, exit_date) for symbol, exit_date in open_positions if exit_date >= entry_date]
        held_symbols = {symbol for symbol, _ in open_positions}
        available = max(0, capacity - len(open_positions))
        if available == 0:
            continue
        if mode == "ranked":
            candidates = _sort_ranked(day)
        elif mode == "reverse":
            candidates = _sort_ranked(day, reverse=True)
        elif mode == "random":
            seed = _stable_seed(benchmark, block, capacity, entry_date) if random_seed is None else _stable_seed(random_seed, benchmark, block, capacity, entry_date)
            candidates = day.sample(frac=1.0, random_state=seed)
        else:
            raise ValueError(mode)
        candidates = candidates[~candidates["symbol"].isin(held_symbols)]
        chosen = candidates.head(available)
        for _, row in chosen.iterrows():
            item = row.to_dict()
            item["selection_mode"] = mode
            item["capacity"] = capacity
            item["benchmark"] = benchmark
            item["block"] = block
            item["pnl_dollars"] = NOTIONAL / capacity * float(row["net_return_pct"]) / 100.0
            item["active_pnl_dollars"] = NOTIONAL / capacity * float(row["net_active_pct"]) / 100.0
            selected_rows.append(item)
            open_positions.append((str(row["symbol"]), pd.Timestamp(row["exit_date"])))
    return pd.DataFrame(selected_rows)


def _metrics(selected: pd.DataFrame, mode: str, benchmark: str, block: str, exit_arm: str, capacity: int, cost_multiplier: float) -> dict[str, object]:
    if selected.empty:
        return {
            "benchmark": benchmark,
            "block": block,
            "selection_mode": mode,
            "exit_arm": exit_arm,
            "capacity": capacity,
            "cost_multiplier": cost_multiplier,
            "n_trades": 0,
            "strategy_return_pct": 0.0,
            "active_return_pct": 0.0,
            "win_rate_pct": math.nan,
            "median_active_pct": math.nan,
            "max_dd_pct": math.nan,
            "top_symbol_abs_active_share_pct": math.nan,
        }
    active = pd.to_numeric(selected["net_active_pct"], errors="coerce")
    return {
        "benchmark": benchmark,
        "block": block,
        "selection_mode": mode,
        "exit_arm": exit_arm,
        "capacity": capacity,
        "cost_multiplier": cost_multiplier,
        "n_trades": int(len(selected)),
        "strategy_return_pct": float(selected["pnl_dollars"].sum() / NOTIONAL * 100.0),
        "active_return_pct": float(selected["active_pnl_dollars"].sum() / NOTIONAL * 100.0),
        "win_rate_pct": float((active > 0).mean() * 100.0),
        "median_active_pct": float(active.median()),
        "max_dd_pct": _drawdown_from_trade_pnl(selected),
        "top_symbol_abs_active_share_pct": _top_concentration(selected),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(INPUT)
    _numeric(
        df,
        [
            "feat_connection_strength",
            "entry_prob",
            "feat_runup_since_t0",
            "hardcap_return_pct",
            "hardcap_active_vs_benchmark_gross_pct",
            "stock_te1_gross_return_pct",
            "stock_te1_net_return_pct",
            "te1_active_vs_spy_gross_pct",
            "te1_active_vs_qqq_gross_pct",
        ],
    )
    protocol = {
        "protocol_name": "fixed_lexicographic_symbol_day_selector",
        "protocol_date": "2026-07-17",
        "input": str(INPUT),
        "primary_rule": [
            "collapse to one symbol-day",
            "rank connection strength descending",
            "rank entry probability descending",
            "rank pre-entry run-up ascending",
            "symbol ascending deterministic tie-break",
        ],
        "capacities": list(CAPACITIES),
        "exit_arms": list(EXIT_ARMS),
        "cost_multipliers": list(COST_MULTIPLIERS),
        "blocks": [list(x) for x in BLOCKS],
        "random_repetitions": RANDOM_REPS,
        "random_seed": RANDOM_SEED,
        "no_fitted_weights": True,
        "no_test_selection": True,
    }
    (OUT / "protocol_manifest.json").write_text(json.dumps(protocol, indent=2) + "\n", encoding="utf-8")

    result_rows: list[dict[str, object]] = []
    selected_artifacts: list[pd.DataFrame] = []
    for benchmark in sorted(df["benchmark"].dropna().unique()):
        for block_name, start, end in BLOCKS:
            block_start = pd.Timestamp(start)
            block_end = pd.Timestamp(end)
            block_df = df[(df["entry_date"] >= start) & (df["entry_date"] <= end)] if "entry_date" in df else df.iloc[0:0]
            if block_df.empty:
                continue
            for exit_arm in EXIT_ARMS:
                for cost_multiplier in COST_MULTIPLIERS:
                    prepared = _prepare(block_df, benchmark, exit_arm, cost_multiplier)
                    for capacity in CAPACITIES:
                        selected = _select_portfolio(prepared, capacity, "ranked", benchmark, block_name)
                        result_rows.append(_metrics(selected, "ranked", benchmark, block_name, exit_arm, capacity, cost_multiplier))
                        if cost_multiplier == PRIMARY_COST_MULTIPLIER:
                            selected = selected.copy()
                            selected["exit_arm"] = exit_arm
                            selected["cost_multiplier"] = cost_multiplier
                            selected_artifacts.append(selected)
                        if cost_multiplier == PRIMARY_COST_MULTIPLIER:
                            reverse = _select_portfolio(prepared, capacity, "reverse", benchmark, block_name)
                            result_rows.append(_metrics(reverse, "reverse", benchmark, block_name, exit_arm, capacity, cost_multiplier))
                            # A fixed-seed random control is reported separately;
                            # it is not used to tune or choose the ranked rule.
                            random_selected = _select_portfolio(prepared, capacity, "random", benchmark, block_name)
                            result_rows.append(_metrics(random_selected, "random", benchmark, block_name, exit_arm, capacity, cost_multiplier))

    results = pd.DataFrame(result_rows)
    results.to_csv(OUT / "frozen_protocol_results.csv", index=False)
    pd.concat(selected_artifacts, ignore_index=True).to_csv(OUT / "frozen_ranked_primary_trades.csv", index=False)

    # Placebo distribution on the latest pseudo-future block: randomize the
    # same-day ordering repeatedly and compare the frozen ranked rule against it.
    placebo_rows: list[dict[str, object]] = []
    future_block = "2026H1_pseudo_future"
    _, start, end = next(x for x in BLOCKS if x[0] == future_block)
    for benchmark in sorted(df["benchmark"].dropna().unique()):
        block_df = df[(df["entry_date"] >= start) & (df["entry_date"] <= end)]
        for exit_arm in EXIT_ARMS:
            prepared = _prepare(block_df, benchmark, exit_arm, PRIMARY_COST_MULTIPLIER)
            for capacity in CAPACITIES:
                ranked = _select_portfolio(prepared, capacity, "ranked", benchmark, future_block)
                ranked_active = float(ranked["active_pnl_dollars"].sum() / NOTIONAL * 100.0) if not ranked.empty else math.nan
                for rep in range(RANDOM_REPS):
                    # Use a distinct stable seed per repetition by perturbing
                    # the row order before the deterministic random selector.
                    randomized = prepared.sample(frac=1.0, random_state=RANDOM_SEED + rep)
                    random_selected = _select_portfolio(randomized, capacity, "random", benchmark, future_block)
                    random_active = float(random_selected["active_pnl_dollars"].sum() / NOTIONAL * 100.0) if not random_selected.empty else math.nan
                    placebo_rows.append(
                        {
                            "benchmark": benchmark,
                            "block": future_block,
                            "exit_arm": exit_arm,
                            "capacity": capacity,
                            "rep": rep,
                            "ranked_active_return_pct": ranked_active,
                            "random_active_return_pct": random_active,
                            "ranked_minus_random_pp": ranked_active - random_active,
                        }
                    )
    placebo = pd.DataFrame(placebo_rows)
    placebo.to_csv(OUT / "pseudo_future_placebo.csv", index=False)
    placebo_summary = (
        placebo.groupby(["benchmark", "exit_arm", "capacity"], as_index=False)
        .agg(
            ranked_active_return_pct=("ranked_active_return_pct", "first"),
            random_mean_active_return_pct=("random_active_return_pct", "mean"),
            random_p05_active_return_pct=("random_active_return_pct", lambda s: s.quantile(0.05)),
            random_p95_active_return_pct=("random_active_return_pct", lambda s: s.quantile(0.95)),
            ranked_minus_random_mean_pp=("ranked_minus_random_pp", "mean"),
            random_beaten_fraction=("ranked_minus_random_pp", lambda s: float((s > 0).mean())),
        )
    )
    placebo_summary.to_csv(OUT / "pseudo_future_placebo_summary.csv", index=False)

    # Latest-block diagnostics by family and sector for the primary ranked rule.
    latest = pd.concat(selected_artifacts, ignore_index=True)
    latest = latest[latest["block"].eq(future_block)]
    if not latest.empty:
        latest.groupby(["benchmark", "exit_arm", "capacity", "event_family"], dropna=False).agg(
            n=("symbol", "size"),
            return_pct=("net_return_pct", "mean"),
            active_return_pct=("net_active_pct", "mean"),
            total_active_pnl=("active_pnl_dollars", "sum"),
        ).reset_index().to_csv(OUT / "pseudo_future_family_diagnostics.csv", index=False)
        latest.groupby(["benchmark", "exit_arm", "capacity", "feat_sector"], dropna=False).agg(
            n=("symbol", "size"),
            return_pct=("net_return_pct", "mean"),
            active_return_pct=("net_active_pct", "mean"),
            total_active_pnl=("active_pnl_dollars", "sum"),
        ).reset_index().to_csv(OUT / "pseudo_future_sector_diagnostics.csv", index=False)

    primary = results[(results["selection_mode"] == "ranked") & (results["cost_multiplier"] == PRIMARY_COST_MULTIPLIER)]
    latest_primary = primary[primary["block"] == future_block]
    report = [
        "# Frozen future-generalization protocol",
        "",
        "Generated 2026-07-17. This is a pseudo-future historical test: the latest available block (2026H1) is treated as a lockbox-style evaluation period, but it is not genuinely future data because it already exists historically.",
        "",
        "## Frozen rule",
        "",
        "- One exposure per symbol-day.",
        "- Lexicographic rank only: connection strength descending, entry probability descending, pre-entry run-up ascending, symbol ascending.",
        "- No fitted weights, thresholds, event-family rules, benchmark-specific parameters, CEM, or ML ranker.",
        "- Capacities 5, 10, and 15 are all reported.",
        "- Exit arms `hardcap` and `te1` are both reported.",
        "- Cost multipliers 0×, 1×, 2×, and 3× are all reported.",
        "",
        "## Chronological primary results",
        "",
        _markdown(
            primary,
            [
                "benchmark",
                "block",
                "exit_arm",
                "capacity",
                "n_trades",
                "strategy_return_pct",
                "active_return_pct",
                "win_rate_pct",
                "median_active_pct",
                "max_dd_pct",
                "top_symbol_abs_active_share_pct",
            ],
            digits=3,
        ),
        "",
        "The latest pseudo-future block is shown separately below and was not used to choose capacity or exit arm.",
        "",
        _markdown(
            latest_primary,
            [
                "benchmark",
                "block",
                "exit_arm",
                "capacity",
                "n_trades",
                "strategy_return_pct",
                "active_return_pct",
                "win_rate_pct",
                "median_active_pct",
                "max_dd_pct",
                "top_symbol_abs_active_share_pct",
            ],
            digits=3,
        ),
        "",
        "## Pseudo-future placebo comparison",
        "",
        _markdown(placebo_summary, digits=3),
        "",
        "A robust selector should beat the random same-day distribution without relying on one capacity or one exit arm. The placebo table is descriptive and is not used to change the frozen rule.",
        "",
        "## Interpretation rules",
        "",
        "- A positive latest-block result alone is insufficient.",
        "- We require the same sign across multiple chronological blocks, positive median active return, and no collapse under 2×/3× cost stress.",
        "- If only one capacity, one exit, one family, or one symbol contributes the result, the protocol is considered non-generalizing.",
        "- The next real test must use observations after the latest available event date and must not change this protocol based on the pseudo-future table.",
        "",
        "## Outputs",
        "",
        "- `protocol_manifest.json` — frozen specification.",
        "- `frozen_protocol_results.csv` — every ranked, reverse, and random arm across blocks, capacities, exits, and cost multipliers.",
        "- `frozen_ranked_primary_trades.csv` — trade-level selected rows for the primary-cost arms.",
        "- `pseudo_future_placebo_summary.csv` — random same-day control distribution.",
        "- `pseudo_future_family_diagnostics.csv` and `pseudo_future_sector_diagnostics.csv` — latest-block diagnostics.",
    ]
    (OUT / "frozen_protocol_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"Wrote {OUT / 'frozen_protocol_report.md'}")
    print(f"Wrote {OUT / 'frozen_protocol_results.csv'} ({len(results)} rows)")
    print(f"Wrote {OUT / 'pseudo_future_placebo_summary.csv'}")


if __name__ == "__main__":
    main()
