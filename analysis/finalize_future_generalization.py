"""Finalize the frozen-protocol report from completed simulation outputs."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.future_generalization_protocol import (
    ART,
    BLOCKS,
    CAPACITIES,
    INPUT,
    NOTIONAL,
    PRIMARY_COST_MULTIPLIER,
    RANDOM_REPS,
    RANDOM_SEED,
    _markdown,
    _numeric,
    _prepare,
    _select_portfolio,
)


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "future_generalization_20260717"


def main() -> None:
    results = pd.read_csv(OUT / "frozen_protocol_results.csv")
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

    future_block, start, end = BLOCKS[-1]
    block_df = df[(df["entry_date"] >= start) & (df["entry_date"] <= end)]
    placebo_rows: list[dict[str, object]] = []
    for benchmark in sorted(df["benchmark"].dropna().unique()):
        for exit_arm in ("hardcap", "te1"):
            prepared = _prepare(block_df, benchmark, exit_arm, PRIMARY_COST_MULTIPLIER)
            for capacity in CAPACITIES:
                ranked = _select_portfolio(prepared, capacity, "ranked", benchmark, future_block)
                ranked_active = float(ranked["active_pnl_dollars"].sum() / NOTIONAL * 100.0) if not ranked.empty else float("nan")
                for rep in range(RANDOM_REPS):
                    random_selected = _select_portfolio(
                        prepared,
                        capacity,
                        "random",
                        benchmark,
                        future_block,
                        random_seed=RANDOM_SEED + rep,
                    )
                    random_active = float(random_selected["active_pnl_dollars"].sum() / NOTIONAL * 100.0) if not random_selected.empty else float("nan")
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

    primary = results[(results["selection_mode"] == "ranked") & (results["cost_multiplier"] == PRIMARY_COST_MULTIPLIER)]
    latest = primary[primary["block"] == future_block]
    cost_stress = results[(results["selection_mode"] == "ranked") & (results["block"] == future_block)]
    controls = results[
        (results["block"] == future_block)
        & (results["cost_multiplier"] == PRIMARY_COST_MULTIPLIER)
        & (results["selection_mode"].isin(["reverse", "random"]))
    ]

    block_summary = (
        primary.groupby(["benchmark", "exit_arm", "block"], as_index=False)
        .agg(
            capacity_mean_active_pct=("active_return_pct", "mean"),
            capacity_median_active_pct=("active_return_pct", "median"),
            positive_capacity_count=("active_return_pct", lambda s: int((s > 0).sum())),
            capacities=("capacity", lambda s: ",".join(map(str, sorted(s.unique())))),
        )
    )
    block_summary.to_csv(OUT / "chronological_block_summary.csv", index=False)

    report = [
        "# Frozen future-generalization protocol results",
        "",
        "Generated 2026-07-17 from the completed frozen simulation. `2026H1_pseudo_future` is the latest available historical block; it is a pseudo-future lockbox, not genuinely future data.",
        "",
        "## Protocol",
        "",
        "- One position per symbol-day.",
        "- Fixed lexicographic rank: connection strength ↓, entry probability ↓, pre-entry run-up ↑, symbol tie-break.",
        "- No fitted weights, thresholds, family-specific rules, benchmark-specific parameters, CEM, or ML.",
        "- Capacities 5/10/15; exits hardcap and T_e−1; costs 0×/1×/2×/3×.",
        "",
        "## Chronological sign stability at primary cost",
        "",
        _markdown(block_summary, digits=3),
        "",
        "A positive result in the latest block is not enough; the frozen rule must be directionally consistent across blocks and capacities.",
        "",
        "## Latest pseudo-future block",
        "",
        _markdown(
            latest,
            [
                "benchmark",
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
        "## Latest-block cost stress",
        "",
        _markdown(
            cost_stress,
            [
                "benchmark",
                "exit_arm",
                "capacity",
                "cost_multiplier",
                "n_trades",
                "strategy_return_pct",
                "active_return_pct",
                "median_active_pct",
            ],
            digits=3,
        ),
        "",
        "## Latest-block controls",
        "",
        _markdown(
            controls,
            [
                "benchmark",
                "selection_mode",
                "exit_arm",
                "capacity",
                "n_trades",
                "active_return_pct",
                "median_active_pct",
            ],
            digits=3,
        ),
        "",
        "## Corrected placebo distribution",
        "",
        _markdown(placebo_summary, digits=3),
        "",
        "## Reading the result",
        "",
        "The primary rule is considered future-compatible only if its sign survives chronological blocks, capacity changes, cost stress, and comparison with random same-day selection. A single positive pseudo-future arm is evidence of possibility, not evidence of deployment readiness.",
        "",
        "The next genuine test must be run on observations after the current data end. This protocol is now frozen; the future period must not be used to choose capacity, exit arm, or ranking logic.",
        "",
        "## Outputs",
        "",
        "- `frozen_protocol_results.csv` — all completed arms.",
        "- `chronological_block_summary.csv` — sign stability by block.",
        "- `pseudo_future_placebo_summary.csv` — corrected random same-day control distribution.",
        "- `frozen_protocol_report.md` — original protocol specification.",
    ]
    (OUT / "frozen_protocol_results_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"Wrote {OUT / 'frozen_protocol_results_report.md'}")
    print(f"Wrote {OUT / 'chronological_block_summary.csv'}")
    print(f"Wrote {OUT / 'pseudo_future_placebo_summary.csv'}")


if __name__ == "__main__":
    main()
