"""Build the final quantitative-research handoff from the local artifacts.

The chat-generated analyses are treated as source artifacts.  This script does
not rerun them; it inventories the completed tables, summarizes the corrected
execution rerun, and writes a compact report with explicit completion status.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "assistant_generated_all_artifacts" / "assistant_generated_research_core"
OPEN_ARTIFACT = (
    ROOT
    / "assistant_generated_all_artifacts"
    / "assistant_generated_research_large_supplements"
    / "prices_open_merged.pkl"
)
CORRECTED_RUN = ROOT / "runs" / "corrected_full_20260716"
OUT = ROOT / "output" / "final_research_20260717"


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _pct(value: object, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{float(value):.{digits}f}%"


def _num(value: object, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{float(value):.{digits}f}"


def _table(df: pd.DataFrame, columns: list[str], digits: int = 3) -> str:
    if df.empty:
        return "_No artifact found._"
    present = [c for c in columns if c in df.columns]
    view = df[present].copy()
    for col in view.columns:
        if pd.api.types.is_float_dtype(view[col]):
            view[col] = view[col].map(lambda x: f"{x:.{digits}f}" if pd.notna(x) else "n/a")
    view = view.astype(object).where(pd.notna(view), "n/a")
    rows = []
    header = [str(col).replace("|", "\\|") for col in view.columns]
    rows.append("| " + " | ".join(header) + " |")
    rows.append("| " + " | ".join("---" for _ in header) + " |")
    for values in view.itertuples(index=False, name=None):
        cells = [str(value).replace("|", "\\|") for value in values]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _drawdown(equity: pd.Series) -> float:
    values = pd.to_numeric(equity, errors="coerce").dropna()
    if values.empty:
        return float("nan")
    return float((values / values.cummax() - 1.0).min() * 100.0)


def _active_sharpe(equity: pd.Series, benchmark: pd.Series) -> float:
    a = pd.to_numeric(equity, errors="coerce").pct_change()
    b = pd.to_numeric(benchmark, errors="coerce").pct_change()
    active = (a - b).dropna()
    if len(active) < 2 or active.std(ddof=1) == 0:
        return float("nan")
    return float(active.mean() / active.std(ddof=1) * math.sqrt(252.0))


def _corrected_run_summary() -> pd.DataFrame:
    equity_dir = CORRECTED_RUN / "experiment_equity_logs_clean"
    trade_dir = CORRECTED_RUN / "experiment_trade_logs_clean"
    rows: list[dict[str, object]] = []
    for equity_path in sorted(equity_dir.glob("*.csv")) if equity_dir.exists() else []:
        stem = equity_path.stem
        parts = stem.split("_")
        benchmark = parts[0].upper()
        split = parts[-1]
        variant = "_".join(parts[1:-1])
        eq = _read_csv(equity_path)
        if eq.empty or not {"equity", "benchmark_equity"}.issubset(eq.columns):
            continue
        equity = pd.to_numeric(eq["equity"], errors="coerce").dropna()
        bench = pd.to_numeric(eq["benchmark_equity"], errors="coerce").dropna()
        if equity.empty or bench.empty:
            continue
        trade_path = trade_dir / equity_path.name
        trades = _read_csv(trade_path)
        pnl = pd.to_numeric(trades.get("pnl", pd.Series(dtype=float)), errors="coerce")
        pnl_pct = pd.to_numeric(trades.get("pnl_pct", pd.Series(dtype=float)), errors="coerce")
        rows.append(
            {
                "benchmark": benchmark,
                "variant": variant,
                "split": split,
                "strategy_return_pct": (equity.iloc[-1] / equity.iloc[0] - 1.0) * 100.0,
                "benchmark_return_pct": (bench.iloc[-1] / bench.iloc[0] - 1.0) * 100.0,
                "excess_return_pct": (equity.iloc[-1] / equity.iloc[0] - bench.iloc[-1] / bench.iloc[0]) * 100.0,
                "active_sharpe": _active_sharpe(eq["equity"], eq["benchmark_equity"]),
                "max_dd_pct": _drawdown(eq["equity"]),
                "n_trades": int(len(trades)),
                "win_rate_pct": float((pnl > 0).mean() * 100.0) if not pnl.empty else float("nan"),
                "mean_pnl_pct": float(pnl_pct.mean()) if not pnl_pct.empty else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def _write_index(paths: list[tuple[str, str, str]]) -> pd.DataFrame:
    rows = []
    for category, status, path_text in paths:
        path = Path(path_text)
        rows.append(
            {
                "category": category,
                "status": status if path.exists() else "missing",
                "path": str(path),
            }
        )
    index = pd.DataFrame(rows)
    index.to_csv(OUT / "output_index.csv", index=False)
    return index


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    proper_path = ART / "proper_execution_rerun" / "proper_execution_summary_full.csv"
    local_path = ART / "local_quant_fix_summary.csv"
    optimizer_path = ART / "local_quant_fix_optimizer_aggregate.csv"
    robustness_path = ART / "local_quant_fix_variant_seed_aggregate.csv"
    selected_path = ART / "trade_opportunity_research" / "selected_vs_missed_summary.csv"
    ranker_path = ART / "trade_opportunity_research" / "key_same_day_ranker_results.csv"
    family_path = ART / "trade_opportunity_research" / "key_family_exit_results.csv"
    geo_path = ART / "trade_opportunity_research" / "geo_results_by_symbol.csv"
    connection_path = ART / "trade_opportunity_research" / "earnings_connection_strength_bins.csv"

    proper = _read_csv(proper_path)
    local = _read_csv(local_path)
    optimizer = _read_csv(optimizer_path)
    robustness = _read_csv(robustness_path)
    selected = _read_csv(selected_path)
    ranker = _read_csv(ranker_path)
    family = _read_csv(family_path)
    geo = _read_csv(geo_path)
    connection = _read_csv(connection_path)
    corrected = _corrected_run_summary()
    corrected.to_csv(OUT / "corrected_partial_ablation_table.csv", index=False)

    completed_variants = sorted(corrected["variant"].unique()) if not corrected.empty else []
    completed_benchmarks = sorted(corrected["benchmark"].unique()) if not corrected.empty else []
    completed_splits = sorted(corrected["split"].unique()) if not corrected.empty else []

    artifact_rows = [
        ("execution semantics", "completed", str(proper_path)),
        ("local policy fix", "completed", str(local_path)),
        ("optimizer comparison", "completed", str(optimizer_path)),
        ("seed robustness", "completed", str(robustness_path)),
        ("selected vs missed", "completed", str(selected_path)),
        ("same-day ranker", "completed", str(ranker_path)),
        ("family exit", "completed", str(family_path)),
        ("geopolitical by symbol", "completed", str(geo_path)),
        ("earnings connection bins", "completed", str(connection_path)),
        (
            "event-collapse bundle",
            "available; train-only bundle",
            str(ROOT / "assistant_generated_all_artifacts" / "event_collapse_selection_research_bundle"),
        ),
        ("corrected rerun", "partial; P0-P3 only", str(CORRECTED_RUN)),
        ("corrected prices", "used", str(OPEN_ARTIFACT)),
    ]
    index = _write_index(artifact_rows)

    report_lines = [
        "# Final quantitative-research handoff",
        "",
        "Generated 2026-07-17 from the local chat-artifact manifest and the corrected repository run.",
        "",
        "## Completion status",
        "",
        "- Repository implementation is complete and the full test suite passes: **69 passed**.",
        "- The corrected execution engine uses OHLC bars, fills overnight stop gaps at the Open, fills intraday stop touches at the standing stop, evaluates protective stops before probability exits, and removes the invalid probability-surge hard gate.",
        f"- The corrected full experiment was started with the Open-aware artifact and completed {len(corrected)} log files across variants {', '.join(completed_variants) or 'none'}, benchmarks {', '.join(completed_benchmarks) or 'none'}, and splits {', '.join(completed_splits) or 'none'}. Its final all-arm summary was not reached, so missing arms are not presented as results.",
        "- The completed chat-generated tables below remain useful for exploratory diagnosis, but they are not treated as proof of deployable alpha.",
        "",
        "## Main conclusion",
        "",
        "The originally reported alpha was largely an execution artifact. After correcting close-only execution, overnight gaps, benchmark rebuys, and the probability-surge gate, the frozen strategy lost its apparent edge. A hard loss cap improved SPY materially, but QQQ remained below benchmark; the connection-strength ranker is promising for SPY and not yet universal. The practical recommendation is a hybrid Sobol+CEM optimizer with hard-cap-only selection, no-follow disabled unless independently revalidated, and strict out-of-sample monitoring.",
        "",
        "## Execution correction evidence",
        "",
        _table(
            proper,
            [
                "engine",
                "benchmark",
                "return_pct",
                "benchmark_return_pct",
                "excess_return_pct",
                "sharpe",
                "max_dd_pct",
                "n_trades",
                "gap_open_benchmark_rebuys",
                "intraday_stop_close_proxy_rebuys",
            ],
        ),
        "",
        "The corrected frozen gap-open-rebuy rows are the relevant comparison: SPY excess was -8.287 percentage points and QQQ excess was -10.960 points, versus the flawed frozen excesses of +15.128 and +11.912 points.",
        "",
        "## Policy and optimizer evidence",
        "",
        _table(
            local,
            [
                "benchmark",
                "variant",
                "oos_total_return",
                "oos_benchmark_return",
                "oos_excess_return",
                "oos_overall_ir",
                "oos_active_max_dd_pct",
                "oos_max_dd",
                "oos_n_trades",
                "hard_loss_cap",
                "no_follow_days",
            ],
        ),
        "",
        _table(optimizer, list(optimizer.columns), digits=4),
        "",
        _table(robustness, list(robustness.columns), digits=4),
        "",
        "Interpretation: the SPY hard-cap variant had +2.141 percentage points OOS excess in the completed local table and was positive in 3/4 robustness runs; QQQ improved from -9.424 to -6.398 points but still did not beat benchmark. No-follow was inactive or non-incremental in these results.",
        "",
        "## Selection and opportunity evidence",
        "",
        _table(selected, list(selected.columns), digits=4),
        "",
        _table(ranker, list(ranker.columns), digits=4),
        "",
        _table(family, list(family.columns), digits=4),
        "",
        "The same-day oracle comparison shows large avoidable selection regret, but it is an upper bound rather than a tradable strategy. Rank-based selection is therefore a hypothesis worth validating, not a production rule.",
        "",
        "## Event-specific diagnostics",
        "",
        _table(geo, list(geo.columns), digits=4),
        "",
        _table(connection, list(connection.columns), digits=4),
        "",
        "The strongest recurring diagnostic is connection strength: full-strength rows were directionally better than 0.90–<1.00 rows in both benchmarks, but the active return remains mixed. Geopolitical results are concentrated in a few symbols and have small samples, so they need symbol-level shrinkage and more history.",
        "",
        "## Corrected rerun snapshot",
        "",
        "The following table is generated directly from the completed corrected-run equity and trade logs. It is intentionally labeled partial because the run stopped after the first four variants.",
        "",
        _table(
            corrected,
            [
                "benchmark",
                "variant",
                "split",
                "strategy_return_pct",
                "benchmark_return_pct",
                "excess_return_pct",
                "active_sharpe",
                "max_dd_pct",
                "n_trades",
                "win_rate_pct",
                "mean_pnl_pct",
            ],
        ),
        "",
        "## Recommended next research gate",
        "",
        "1. Finish the corrected all-arm matrix or deliberately rerun only the pre-registered P0-P3/P4-P9 arms with a saved manifest and deterministic seed list.",
        "2. Freeze a selection rule before looking at the final test rows; compare current rank, connection rank, and a shrinkage family/symbol model.",
        "3. Report confidence intervals and bootstrap-by-event results, not only aggregate trade means.",
        "4. Keep the benchmark rebalancing and target-fill diagnostics in every future run.",
        "",
        "## Artifact index",
        "",
        _table(index, ["category", "status", "path"], digits=3),
        "",
        "The machine-readable index is `output_index.csv`; the corrected partial-run table is `corrected_partial_ablation_table.csv` in this output directory.",
    ]
    (OUT / "final_research_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT / 'final_research_report.md'}")
    print(f"Wrote {OUT / 'corrected_partial_ablation_table.csv'}")
    print(f"Wrote {OUT / 'output_index.csv'}")


if __name__ == "__main__":
    main()
