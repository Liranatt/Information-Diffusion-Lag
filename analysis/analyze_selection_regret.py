"""Trade-level, same-day, and H1 opportunity/regret analysis.

All dollar figures are standardized to a $10,000 notional unless explicitly
marked as ``actual portfolio``.  Standardized dollars make missed trades
comparable across the opportunity universe; they are not a claim that the
portfolio could have funded every missed trade simultaneously.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "assistant_generated_all_artifacts" / "assistant_generated_research_core" / "trade_opportunity_research"
H1 = ROOT / "output" / "final_icaif_run_20260714_170817" / "h1" / "raw_expectation_tminus1_final"
OUT = ROOT / "output" / "selection_regret_20260717"
NOTIONAL = 10_000.0


def _read(name: str) -> pd.DataFrame:
    return pd.read_csv(ART / name)


def _markdown(df: pd.DataFrame, columns: list[str] | None = None, digits: int = 3, limit: int | None = None) -> str:
    if df.empty:
        return "_No rows._"
    view = df.copy() if columns is None else df[[c for c in columns if c in df.columns]].copy()
    if limit is not None:
        view = view.head(limit)
    for col in view.columns:
        if pd.api.types.is_float_dtype(view[col]):
            view[col] = view[col].map(lambda x: f"{x:.{digits}f}" if pd.notna(x) else "n/a")
    view = view.astype(object).where(pd.notna(view), "n/a")
    headers = [str(c).replace("|", "\\|") for c in view.columns]
    rows = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for values in view.itertuples(index=False, name=None):
        rows.append("| " + " | ".join(str(v).replace("|", "\\|") for v in values) + " |")
    return "\n".join(rows)


def _read_h1_table(path: Path, start: str, end: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        i = lines.index(start)
        j = lines.index(end, i + 1)
    except ValueError:
        return pd.DataFrame()
    rows = [line.strip().strip("|").split("|") for line in lines[i + 1 : j] if line.strip().startswith("|")]
    if len(rows) < 3:
        return pd.DataFrame()
    header = [x.strip() for x in rows[0]]
    data = []
    for row in rows[2:]:
        if len(row) == len(header):
            data.append([x.strip() for x in row])
    return pd.DataFrame(data, columns=header)


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    trades = _read("symbol_day_current_priority.csv")
    swaps = _read("same_day_one_swap_opportunities.csv")
    same_day = _read("same_day_capacity_summary.csv")
    h1_report = H1 / "h1_expectation_protocol_report.md"

    trades["selected"] = trades["selected_by_portfolio"].astype(str).str.lower().isin(["true", "1", "yes"])
    for col in [
        "hardcap_return_pct",
        "hardcap_active_vs_benchmark_gross_pct",
        "stock_te1_net_return_pct",
        "te1_active_vs_spy_net_twin_pct",
        "te1_active_vs_qqq_net_twin_pct",
        "selected_pnl",
        "selected_pnl_pct",
        "selected_invested_frac_pct",
        "entry_latency_days",
    ]:
        if col in trades.columns:
            trades[col] = _numeric(trades[col])

    # Standardized opportunity PnL: one independent $10k slot per collapsed
    # symbol-day.  Active PnL is the stock's hard-cap return less the benchmark
    # return over the same window.
    trades["hardcap_pnl_10k"] = trades["hardcap_return_pct"] / 100.0 * NOTIONAL
    trades["active_pnl_10k"] = trades["hardcap_active_vs_benchmark_gross_pct"] / 100.0 * NOTIONAL
    trades["te1_pnl_10k"] = trades["stock_te1_net_return_pct"] / 100.0 * NOTIONAL
    trades["active_positive"] = trades["active_pnl_10k"] > 0
    trades["nominal_positive"] = trades["hardcap_pnl_10k"] > 0

    # This is the full, trade-by-trade collapsed universe with explicit status.
    trade_columns = [
        "benchmark",
        "analysis_split",
        "entry_date",
        "symbol",
        "event_family",
        "feat_sector",
        "feat_connection_strength",
        "entry_prob",
        "entry_latency_days",
        "selected",
        "hardcap_return_pct",
        "hardcap_active_vs_benchmark_gross_pct",
        "hardcap_pnl_10k",
        "active_pnl_10k",
        "stock_te1_net_return_pct",
        "te1_pnl_10k",
        "selected_pnl",
        "selected_pnl_pct",
        "selected_invested_frac_pct",
        "hardcap_exit_reason",
        "question",
    ]
    trades[trade_columns].sort_values(
        ["benchmark", "analysis_split", "entry_date", "selected", "active_pnl_10k"],
        ascending=[True, True, True, False, False],
    ).to_csv(OUT / "trade_by_trade_collapsed.csv", index=False)

    group_rows: list[dict[str, object]] = []
    for (benchmark, split, family), g in trades.groupby(["benchmark", "analysis_split", "event_family"], dropna=False):
        selected = g[g["selected"]]
        missed = g[~g["selected"]]
        selected_active = selected["active_pnl_10k"].dropna()
        missed_active = missed["active_pnl_10k"].dropna()
        selected_actual = selected["selected_pnl"].dropna()
        group_rows.append(
            {
                "benchmark": benchmark,
                "split": split,
                "event_family": family,
                "eligible_symbol_days": len(g),
                "selected_symbol_days": len(selected),
                "missed_symbol_days": len(missed),
                "selected_rate_pct": len(selected) / len(g) * 100.0 if len(g) else math.nan,
                "selected_mean_active_pct": selected["hardcap_active_vs_benchmark_gross_pct"].mean(),
                "missed_mean_active_pct": missed["hardcap_active_vs_benchmark_gross_pct"].mean(),
                "selected_mean_nominal_pct": selected["hardcap_return_pct"].mean(),
                "missed_mean_nominal_pct": missed["hardcap_return_pct"].mean(),
                "selected_active_loss_dollars_10k": selected_active[selected_active < 0].sum(),
                "selected_active_gain_dollars_10k": selected_active[selected_active > 0].sum(),
                "selected_actual_loss_dollars": selected_actual[selected_actual < 0].sum(),
                "selected_actual_pnl_dollars": selected_actual.sum(),
                "missed_positive_active_count": int((missed_active > 0).sum()),
                "missed_positive_active_dollars_10k": missed_active[missed_active > 0].sum(),
                "missed_negative_active_dollars_10k": missed_active[missed_active < 0].sum(),
                "missed_net_active_dollars_10k": missed_active.sum(),
                "missed_positive_nominal_count": int((missed["hardcap_pnl_10k"] > 0).sum()),
                "missed_positive_nominal_dollars_10k": missed.loc[missed["hardcap_pnl_10k"] > 0, "hardcap_pnl_10k"].sum(),
            }
        )
    summary = pd.DataFrame(group_rows).sort_values(["benchmark", "split", "event_family"])
    summary.to_csv(OUT / "trade_selection_regret_summary.csv", index=False)

    # Aggregate totals separate the two meanings of “wrong”: an actual
    # selected loss and an active loss versus the benchmark.
    total_rows = []
    for (benchmark, split), g in trades.groupby(["benchmark", "analysis_split"]):
        selected = g[g["selected"]]
        missed = g[~g["selected"]]
        s_active = selected["active_pnl_10k"].dropna()
        m_active = missed["active_pnl_10k"].dropna()
        s_actual = selected["selected_pnl"].dropna()
        total_rows.append(
            {
                "benchmark": benchmark,
                "split": split,
                "eligible": len(g),
                "selected": len(selected),
                "missed": len(missed),
                "missed_positive_active_count": int((m_active > 0).sum()),
                "money_left_on_table_positive_active_10k": m_active[m_active > 0].sum(),
                "money_left_on_table_all_missed_active_10k": m_active.sum(),
                "selected_active_losses_10k": s_active[s_active < 0].sum(),
                "selected_active_gains_10k": s_active[s_active > 0].sum(),
                "selected_actual_losses": s_actual[s_actual < 0].sum(),
                "selected_actual_pnl": s_actual.sum(),
                "selected_active_mean_pct": selected["hardcap_active_vs_benchmark_gross_pct"].mean(),
                "missed_active_mean_pct": missed["hardcap_active_vs_benchmark_gross_pct"].mean(),
            }
        )
    totals = pd.DataFrame(total_rows).sort_values(["benchmark", "split"])
    totals.to_csv(OUT / "trade_selection_regret_totals.csv", index=False)

    # Same-day oracle: one replacement per contested day, which is a clean
    # standardized measure of capacity/ranking regret.  It is an upper bound,
    # because it uses the realized best missed trade to define the counterfactual.
    swaps["oracle_swap_dollars_10k"] = swaps["oracle_one_swap_improvement_pct"] / 100.0 * NOTIONAL
    swaps.to_csv(OUT / "same_day_regret_detail.csv", index=False)
    same_day_summary = (
        swaps.groupby(["benchmark", "split"], as_index=False)
        .agg(
            choice_days=("entry_date", "count"),
            positive_swap_days=("oracle_one_swap_improvement_pct", lambda s: int((s > 0).sum())),
            mean_swap_pct=("oracle_one_swap_improvement_pct", "mean"),
            median_swap_pct=("oracle_one_swap_improvement_pct", "median"),
            total_swap_dollars_10k=("oracle_swap_dollars_10k", "sum"),
            p90_swap_pct=("oracle_one_swap_improvement_pct", lambda s: s.quantile(0.90)),
        )
        .sort_values(["benchmark", "split"])
    )
    same_day_summary.to_csv(OUT / "same_day_regret_summary.csv", index=False)

    # Trade-level audit lists: all rows are retained in trade_by_trade_collapsed;
    # these two files make the most actionable examples easy to review.
    missed = trades[~trades["selected"]].copy()
    missed["opportunity_rank"] = missed.groupby(["benchmark", "analysis_split"])["active_pnl_10k"].rank(method="first", ascending=False)
    missed.sort_values(["benchmark", "analysis_split", "active_pnl_10k"], ascending=[True, True, False]).head(200).to_csv(
        OUT / "top_missed_active_opportunities.csv", index=False
    )
    selected = trades[trades["selected"]].copy()
    selected["wrong_trade_rank"] = selected.groupby(["benchmark", "analysis_split"])["active_pnl_10k"].rank(method="first", ascending=True)
    selected.sort_values(["benchmark", "analysis_split", "active_pnl_10k"], ascending=[True, True, True]).head(200).to_csv(
        OUT / "top_selected_active_losses.csv", index=False
    )

    h1_primary = _read_h1_table(h1_report, "## Primary results", "### Candidate-observation mean with dependence-aware uncertainty")
    h1_tail = _read_h1_table(h1_report, "## Tail and concentration sensitivity", "Contribution shares above 100% mean that the remaining observations collectively lost money and the upper tail more than accounted for the full positive total.")
    h1_family = _read_h1_table(h1_report, "## Event-family and calendar sensitivity", "Candidate-level monthly mean return is positive in 8 of 16 observed entry months. The three weakest and three strongest months are:")
    h1_primary.to_csv(OUT / "h1_primary_excerpt.csv", index=False)
    h1_tail.to_csv(OUT / "h1_tail_sensitivity_excerpt.csv", index=False)
    h1_family.to_csv(OUT / "h1_family_excerpt.csv", index=False)

    report = [
        "# Trade-by-trade selection regret and H1 interpretation",
        "",
        "Generated 2026-07-17 from the collapsed symbol-day opportunity universe. All opportunity dollars below use a standardized $10,000 slot. This prevents accidental claims that every missed trade could have been funded at once.",
        "",
        "## Executive conclusion",
        "",
        "H1 gives us a valuable but narrower result than ‘the strategy will make money’: the historical conditional stock-event return is positive before capacity selection, especially in geo events. The strategy failed to monetize it because the opportunity is concentrated, duplicated, regime-dependent, and benchmark-sensitive. The largest fix is not finding more signals; it is selecting the right exposure on crowded entry days and measuring active return against the benchmark.",
        "",
        "## What H1 proves",
        "",
        "- Candidate-observation level: 887 observations, mean net return +1.208%, median +0.188%, 51.97% positive, ordinary one-sided mean p=0.0002.",
        "- Dependence-aware candidate bootstrap: economic-event clustering CI [+0.263%, +2.200%], p=0.0087; symbol and week clustering intervals include zero.",
        "- Equal-weight true economic events: 750 events, mean +0.631%, positive-event frequency 50.27%, one-sided mean p=0.0402; month-block bootstrap p=0.1935.",
        "- After removing the best 1% of economic events, mean falls to +0.029% with p=0.4523. After removing the best 5% of candidate observations, mean is -0.260%.",
        "- Event family split: earnings mean +0.020% (n=692) versus geo +4.794% (n=172). Geo drives much of H1, and geo was negative in train but strongly positive in test.",
        "- Timing audit: exact same-session ordering is verified for 0/887 historical rows; the next-stored-close convention gives candidate mean +1.229% but median -0.153%, and event mean +0.550% with p=0.055.",
        "",
        "H1 therefore establishes a promising conditional expectation in this historical universe. It does not establish a deployable portfolio rule, future stationarity, or that the current selector can capture the mean.",
        "",
        "## Money left on the table",
        "",
        "The first table is the full trade-level accounting by benchmark and split. ‘Positive active’ means a missed symbol-day would have beaten the benchmark under the hard-cap counterfactual. ‘Selected active losses’ are selected trades that underperformed the benchmark under the same standardized $10,000 slot.",
        "",
        _markdown(
            totals,
            [
                "benchmark",
                "split",
                "eligible",
                "selected",
                "missed",
                "missed_positive_active_count",
                "money_left_on_table_positive_active_10k",
                "money_left_on_table_all_missed_active_10k",
                "selected_active_losses_10k",
                "selected_actual_losses",
                "selected_actual_pnl",
            ],
            digits=2,
        ),
        "",
        "Interpretation: the positive missed-opportunity total is an upper bound on capital that could have been earned if each missed trade received an independent $10,000 slot. The actual portfolio could not take all of them, so the economically credible number is the same-day replacement regret below.",
        "",
        "## Same-day trade competition",
        "",
        "On each contested date, the existing analysis compares the worst selected trade with the best missed trade. This is a one-swap oracle: it tells us how much one better choice would have improved a $10,000 slot, but it uses future realized returns and is not itself tradable.",
        "",
        _markdown(same_day_summary, digits=2),
        "",
        "The test-period oracle improvement was positive on 30/37 SPY choice days and 31/36 QQQ choice days. The mean one-swap improvement was 6.953 percentage points for SPY and 5.534 points for QQQ, or approximately $695 and $553 per $10,000 replacement respectively. This is strong evidence that capacity/ranking is economically important; it is not proof that a model can realize the full amount.",
        "",
        "## Trade-by-trade diagnosis",
        "",
        "Every collapsed symbol-day is retained in `trade_by_trade_collapsed.csv`, with selection status, connection strength, family, entry latency, hard-cap return, active return, T-1 return, and standardized dollars. The two most actionable slices are:",
        "",
        "- `top_missed_active_opportunities.csv`: missed CRCL, NET, QCOM, USO and other trades show that the selector often left profitable capacity unused.",
        "- `top_selected_active_losses.csv`: selected XLE geo trades and several earnings names such as AS, ARM, RBLX, RDDT, CMG, HD and COF are examples where capital was allocated to weak active outcomes.",
        "",
        "The recurring pattern is not simply ‘winners were missed’. Selected trades are already better than the missed average in most cells, so the current selector contains information. The failure is ranking precision under crowded days: it chooses some good trades, but also spends scarce slots on much worse alternatives.",
        "",
        "## What to improve",
        "",
        "1. Collapse first: one symbol-day equals one exposure. Keep multiple Polymarket questions as supporting evidence, not multiple independent positions.",
        "2. Rank, do not hard-filter: connection strength is the most robust simple ranking signal. A hard connection=1 gate overfits and removes useful trades.",
        "3. Optimize active return: score each candidate against SPY/QQQ and the sector ETF, not nominal stock return alone. Earnings exposure is materially sector beta.",
        "4. Separate families: use hard-cap/profit-lock for earnings/other; investigate T_e-1 only for the SPY geo 2–3 day latency subgroup. Do not transfer that rule to QQQ without new evidence.",
        "5. Penalize concentration: cap symbol, sector, and event-family exposure. In geo, do not treat XLE as an automatic substitute for USO/BNO.",
        "6. Treat latency as a feature: geo entries 4+ days after T_theta were sharply negative in both periods; 2–3 days was the most stable positive subgroup.",
        "7. Use conservative allocation: keep the corrected hard-cap policy, target-fill diagnostics, gap-aware execution, and benchmark rebuys in every backtest.",
        "",
        "## Quant plan",
        "",
        "### Phase 1 — freeze the estimand and data",
        "",
        "- Freeze point-in-time t_e, outcome timestamps, signal timestamp, and executable hourly prices.",
        "- Log every eligible candidate, every rejected candidate, every same-day rank, requested allocation, realized allocation, and benchmark counterfactual.",
        "- Pre-register the primary metric as portfolio excess return versus benchmark, with event-cluster bootstrap intervals; nominal return is secondary.",
        "",
        "### Phase 2 — build a transparent selector",
        "",
        "- Baseline: current selector after symbol-day collapse.",
        "- Candidate score: connection rank, entry probability, latency, run-up, sector exposure, and family; use only features available at entry.",
        "- Compare simple weighted ranking, within-day percentile ranking, and a monotone/shrinkage model. Avoid unrestricted tree/pairwise models until a new holdout proves generalization.",
        "- Portfolio decision: select the top K subject to symbol/sector/family caps and a minimum expected active-return threshold estimated only from training data.",
        "",
        "### Phase 3 — pre-registered walk-forward test",
        "",
        "- Use a genuinely untouched future window; do not tune on the current test again.",
        "- Run SPY and QQQ separately, plus sector-ETF controls.",
        "- Compare current, connection rank, oracle upper bound, random same-day selection, and trade-everything symbol-day baselines.",
        "- Require positive excess in at least 3/4 temporal blocks, positive median block excess, no single month contributing more than 40% of total active PnL, and a block-bootstrap lower bound above zero before considering deployment.",
        "",
        "### Phase 4 — live shadow mode",
        "",
        "- Shadow-log decisions for 8–12 weeks with no capital or very small capped capital.",
        "- Review same-day regret, missed positive active trades, selected active losses, slippage, target-fill rate, and benchmark rebalancing every week.",
        "- Promote only if live shadow results preserve the pre-registered ranking edge after costs and the decision log is complete.",
        "",
        "## Output files",
        "",
        "- `trade_selection_regret_totals.csv` — money-left and selected-loss totals.",
        "- `trade_selection_regret_summary.csv` — family-level decomposition.",
        "- `same_day_regret_summary.csv` and `same_day_regret_detail.csv` — contested-day replacement analysis.",
        "- `trade_by_trade_collapsed.csv` — complete trade-by-trade audit.",
        "- `top_missed_active_opportunities.csv` and `top_selected_active_losses.csv` — actionable examples.",
        "- `h1_primary_excerpt.csv`, `h1_tail_sensitivity_excerpt.csv`, `h1_family_excerpt.csv` — machine-readable H1 extracts.",
    ]
    (OUT / "selection_regret_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"Wrote {OUT / 'selection_regret_report.md'}")
    print(f"Wrote {OUT / 'trade_by_trade_collapsed.csv'} ({len(trades)} rows)")
    print(f"Wrote {OUT / 'same_day_regret_summary.csv'}")
    print(f"Wrote {OUT / 'trade_selection_regret_totals.csv'}")


if __name__ == "__main__":
    main()
