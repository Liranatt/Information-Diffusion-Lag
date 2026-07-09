#!/usr/bin/env python
"""
CEM Loss Attribution

Decomposes each executed CEM trade's PnL into:
  1. Index component  (benchmark return * notional — market exposure)
  2. Selection component  (excess return * notional — stock picking)

Uses executed trade logs from data/experiment_trade_logs_clean/
and benchmark prices from data/prices.pkl.
"""
from __future__ import annotations

import os
import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT = Path(__file__).resolve().parent.parent
TRADE_LOG_DIR = PROJECT / "data" / "experiment_trade_logs_clean"
PRICES_PATH = PROJECT / "data" / "prices.pkl"
WF_FOLDS_CSV = PROJECT / "data" / "experiment_walkforward_folds_clean.csv"
OUTPUT_DIR = PROJECT / "output" / "cem_loss_attribution"

OOS_MID = "2026-03-22"

SLUG_TO_EXPERIMENT = {
    "baseline": "Baseline",
    "t1_frictionpenalty": "T1 FrictionPenalty",
    "t2_trainwindows": "T2 TrainWindows",
    "t3_kelly": "T3 Kelly",
    "t1_t2": "T1+T2",
    "t1_t3": "T1+T3",
    "t2_t3": "T2+T3",
    "t1_t2_t3": "T1+T2+T3",
    "t4_geopriority": "T4 GeoPriority",
    "t1_t2_t3_t4": "T1+T2+T3+T4",
}
WF_SLUGS = {"t2_trainwindows", "t1_t2", "t2_t3", "t1_t2_t3", "t1_t2_t3_t4"}


# ═══════════════════════════════════════════════════════════════════════════
# Data loading
# ═══════════════════════════════════════════════════════════════════════════

def load_benchmark_closes() -> dict[str, dict[str, float]]:
    with open(PRICES_PATH, "rb") as f:
        prices = pickle.load(f)
    return {sym: {str(t.date()): c for t, h, l, c in prices[sym]}
            for sym in ("SPY", "QQQ")}


def load_fold_windows() -> dict[tuple[str, str], list[dict]]:
    wf = pd.read_csv(WF_FOLDS_CSV)
    windows: dict[tuple[str, str], list[dict]] = {}
    for _, r in wf.iterrows():
        key = (r["experiment"], r["benchmark"])
        windows.setdefault(key, []).append({
            "fold": int(r["fold"]),
            "eval_start": r["eval_start_date"],
            "eval_end_excl": str((pd.Timestamp(r["eval_end_date"]) + pd.Timedelta(days=1)).date()),
        })
    return windows


def assign_fold(entry_date: str, fold_list: list[dict]) -> int | None:
    for fw in fold_list:
        if fw["eval_start"] <= entry_date < fw["eval_end_excl"]:
            return fw["fold"]
    return None


def parse_filename(fname: str) -> tuple[str, str, str]:
    stem = Path(fname).stem
    parts = stem.split("_")
    benchmark = parts[0].upper()
    split = parts[-1]
    config_slug = "_".join(parts[1:-1])
    return benchmark, config_slug, split


# ═══════════════════════════════════════════════════════════════════════════
# Trade processing
# ═══════════════════════════════════════════════════════════════════════════

def process_trade(row, benchmark_sym, config_slug, split,
                  bench_closes, fold_list) -> dict:
    entry_date = str(row["entry_date"])
    exit_date = str(row["exit_date"])

    stock_net_return = float(row["pnl_pct"]) / 100.0

    notional = row.get("_asset_entry_notional")
    if pd.isna(notional) or notional is None or float(notional) <= 0:
        notional = float(row["_qty"]) * float(row["entry_price"])
    else:
        notional = float(notional)

    entry_bench = bench_closes[benchmark_sym].get(entry_date)
    exit_bench = bench_closes[benchmark_sym].get(exit_date)
    if entry_bench and exit_bench and entry_bench > 0:
        benchmark_return = exit_bench / entry_bench - 1.0
    else:
        benchmark_return = 0.0

    actual_stock_pnl = notional * stock_net_return
    index_component_pnl = notional * benchmark_return
    selection_component_pnl = notional * (stock_net_return - benchmark_return)

    period = "train" if split == "train" else ("early" if entry_date < OOS_MID else "late")

    fold_id = ""
    if fold_list is not None:
        f = assign_fold(entry_date, fold_list)
        if f is not None:
            fold_id = f

    holding_days = (pd.Timestamp(exit_date) - pd.Timestamp(entry_date)).days

    return {
        "benchmark": benchmark_sym,
        "config_slug": config_slug,
        "fold_id": fold_id,
        "split": split,
        "period": period,
        "symbol": row["symbol"],
        "candidate_id": row.get("market_id", ""),
        "market_id": row.get("market_id", ""),
        "question": str(row.get("question", "")),
        "entry_date": entry_date,
        "exit_date": exit_date,
        "holding_days": holding_days,
        "trade_notional": round(notional, 2),
        "stock_net_return": round(stock_net_return, 6),
        "benchmark_return": round(benchmark_return, 6),
        "actual_stock_pnl": round(actual_stock_pnl, 2),
        "index_component_pnl": round(index_component_pnl, 2),
        "selection_component_pnl": round(selection_component_pnl, 2),
        "transaction_cost": round(float(row.get("txn_cost", 0)), 2),
        "absolute_loser": actual_stock_pnl < 0,
        "lost_but_beat_index": actual_stock_pnl < 0 and selection_component_pnl > 0,
        "lost_and_underperformed_index": actual_stock_pnl < 0 and selection_component_pnl < 0,
        "positive_but_underperformed_index": actual_stock_pnl > 0 and selection_component_pnl < 0,
        "positive_and_beat_index": actual_stock_pnl > 0 and selection_component_pnl > 0,
        "index_down_trade": index_component_pnl < 0,
        "index_up_trade": index_component_pnl > 0,
        "exit_reason": str(row.get("realized_exit_reason", row.get("exit_reason", ""))),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Summary aggregation
# ═══════════════════════════════════════════════════════════════════════════

def compute_summary(trades: list[dict]) -> dict | None:
    if not trades:
        return None
    n = len(trades)
    sel = np.array([t["selection_component_pnl"] for t in trades])
    idx = np.array([t["index_component_pnl"] for t in trades])
    stock = np.array([t["actual_stock_pnl"] for t in trades])

    n_abs_losers = sum(1 for t in trades if t["absolute_loser"])
    n_lbi = sum(1 for t in trades if t["lost_but_beat_index"])
    n_lui = sum(1 for t in trades if t["lost_and_underperformed_index"])
    n_pui = sum(1 for t in trades if t["positive_but_underperformed_index"])
    n_pbi = sum(1 for t in trades if t["positive_and_beat_index"])

    opp_cost = sum(t["selection_component_pnl"] for t in trades
                   if t["actual_stock_pnl"] > 0 and t["selection_component_pnl"] < 0)

    return {
        "n_trades": n,
        "total_actual_stock_pnl": round(float(stock.sum()), 2),
        "total_index_component_pnl": round(float(idx.sum()), 2),
        "total_selection_component_pnl": round(float(sel.sum()), 2),
        "loss_from_index_down_moves": round(float(np.minimum(idx, 0).sum()), 2),
        "loss_from_bad_selection": round(float(np.minimum(sel, 0).sum()), 2),
        "opportunity_cost_positive_stock_underperformed": round(opp_cost, 2),
        "n_absolute_losing_trades": n_abs_losers,
        "n_lost_but_beat_index": n_lbi,
        "n_lost_and_underperformed_index": n_lui,
        "n_positive_but_underperformed_index": n_pui,
        "n_positive_and_beat_index": n_pbi,
        "pct_losers_that_still_beat_index": round(n_lbi / n_abs_losers * 100, 2) if n_abs_losers > 0 else 0.0,
        "pct_trades_that_added_value_vs_index": round((n_lbi + n_pbi) / n * 100, 2),
        "avg_selection_component_pnl": round(float(sel.mean()), 2),
        "median_selection_component_pnl": round(float(np.median(sel)), 2),
        "avg_index_component_pnl": round(float(idx.mean()), 2),
        "median_index_component_pnl": round(float(np.median(idx)), 2),
    }


def build_summaries(all_trades: list[dict]) -> list[dict]:
    by_config = defaultdict(list)
    for t in all_trades:
        by_config[(t["benchmark"], t["config_slug"])].append(t)

    rows = []
    for (bench, slug), trades in sorted(by_config.items()):
        train = [t for t in trades if t["split"] == "train"]
        test = [t for t in trades if t["split"] == "test"]
        early = [t for t in test if t["period"] == "early"]
        late = [t for t in test if t["period"] == "late"]

        for label, subset, split_val in [
            ("train", train, "train"),
            ("full_oos", test, "test"),
            ("early", early, "test"),
            ("late", late, "test"),
        ]:
            s = compute_summary(subset)
            if s:
                s.update({"benchmark": bench, "config_slug": slug,
                          "split": split_val, "period": label, "fold_id": ""})
                rows.append(s)

        if slug in WF_SLUGS:
            fold_groups: dict[int, list] = defaultdict(list)
            for t in test:
                if t["fold_id"] != "":
                    fold_groups[t["fold_id"]].append(t)
            for fid, ftrades in sorted(fold_groups.items()):
                s = compute_summary(ftrades)
                if s:
                    s.update({"benchmark": bench, "config_slug": slug,
                              "split": "test", "period": f"fold_{fid}", "fold_id": fid})
                    rows.append(s)
    return rows


# ═══════════════════════════════════════════════════════════════════════════
# Markdown report
# ═══════════════════════════════════════════════════════════════════════════

def _md_table(headers, rows):
    lines = ["| " + " | ".join(str(h) for h in headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for r in rows:
        lines.append("| " + " | ".join(str(v) for v in r) + " |")
    return "\n".join(lines)


def write_markdown(all_trades, summaries):
    lines = ["# CEM Loss Attribution Report", ""]

    by_config = defaultdict(list)
    for s in summaries:
        by_config[(s["benchmark"], s["config_slug"])].append(s)

    for (bench, slug), sums in sorted(by_config.items()):
        oos = next((s for s in sums if s["period"] == "full_oos"), None)
        if oos is None:
            continue

        lines += [f"## {slug} / {bench}", ""]

        n = oos["n_trades"]
        total_stock = oos["total_actual_stock_pnl"]
        total_idx = oos["total_index_component_pnl"]
        total_sel = oos["total_selection_component_pnl"]
        loss_idx = oos["loss_from_index_down_moves"]
        loss_sel = oos["loss_from_bad_selection"]
        opp = oos["opportunity_cost_positive_stock_underperformed"]
        n_losers = oos["n_absolute_losing_trades"]
        n_lbi = oos["n_lost_but_beat_index"]
        n_lui = oos["n_lost_and_underperformed_index"]
        n_pui = oos["n_positive_but_underperformed_index"]
        n_pbi = oos["n_positive_and_beat_index"]
        pct_losers_beat = oos["pct_losers_that_still_beat_index"]
        pct_added_value = oos["pct_trades_that_added_value_vs_index"]

        total_abs_loss = sum(t["actual_stock_pnl"] for t in all_trades
                            if t["benchmark"] == bench and t["config_slug"] == slug
                            and t["split"] == "test" and t["actual_stock_pnl"] < 0)

        lines.append(f"**OOS trades: {n}** | Total stock PnL: ${total_stock:,.0f} | "
                     f"Index component: ${total_idx:,.0f} | Selection component: ${total_sel:,.0f}")
        lines.append("")

        hdrs = ["Metric", "Value"]
        metric_rows = [
            ["Loss from index-down moves", f"${loss_idx:,.0f}"],
            ["Loss from bad selection", f"${loss_sel:,.0f}"],
            ["Opportunity cost (positive stock, underperformed)", f"${opp:,.0f}"],
            ["", ""],
            ["Absolute losing trades", f"{n_losers} / {n} ({n_losers/n*100:.1f}%)"],
            ["Losers that beat the index", f"{n_lbi} / {n_losers} ({pct_losers_beat:.1f}%)" if n_losers else "N/A"],
            ["Losers that underperformed index", f"{n_lui}"],
            ["Positive trades that underperformed index", f"{n_pui}"],
            ["Positive trades that beat index", f"{n_pbi}"],
            ["Trades that added value vs index", f"{pct_added_value:.1f}%"],
            ["", ""],
            ["Avg selection component PnL", f"${oos['avg_selection_component_pnl']:,.0f}"],
            ["Median selection component PnL", f"${oos['median_selection_component_pnl']:,.0f}"],
            ["Avg index component PnL", f"${oos['avg_index_component_pnl']:,.0f}"],
            ["Median index component PnL", f"${oos['median_index_component_pnl']:,.0f}"],
        ]
        lines.append(_md_table(hdrs, metric_rows))
        lines.append("")

        if total_abs_loss < 0:
            idx_share = loss_idx / total_abs_loss * 100 if total_abs_loss != 0 else 0
            sel_share = loss_sel / total_abs_loss * 100 if total_abs_loss != 0 else 0
        else:
            idx_share = 0
            sel_share = 0

        lines.append("**Diagnosis:**")
        if abs(loss_idx) > abs(loss_sel):
            lines.append(f"- The larger source of absolute losses is **market/index exposure** "
                         f"(${loss_idx:,.0f} from index-down moves vs ${loss_sel:,.0f} from bad selection).")
        else:
            lines.append(f"- The larger source of absolute losses is **bad stock selection** "
                         f"(${loss_sel:,.0f} from bad selection vs ${loss_idx:,.0f} from index-down moves).")

        if pct_losers_beat > 40:
            lines.append(f"- {pct_losers_beat:.0f}% of losing trades still beat the index — "
                         "many losses are from market exposure, not candidate quality.")
        elif pct_losers_beat > 20:
            lines.append(f"- {pct_losers_beat:.0f}% of losing trades beat the index — "
                         "a mix of market exposure and selection problems.")
        else:
            lines.append(f"- Only {pct_losers_beat:.0f}% of losing trades beat the index — "
                         "most losses compound market exposure with poor selection.")

        if n_pui > n_pbi:
            lines.append(f"- More winners underperformed the index ({n_pui}) than beat it ({n_pbi}) — "
                         "the strategy captures some alpha but leaves opportunity cost on the table.")
        else:
            lines.append(f"- More winners beat the index ({n_pbi}) than underperformed it ({n_pui}) — "
                         "the stock selection adds value among winning trades.")

        if total_sel > 0:
            lines.append(f"- Overall selection component is **positive** (${total_sel:,.0f}), "
                         "meaning the strategy adds value relative to holding the index.")
        else:
            lines.append(f"- Overall selection component is **negative** (${total_sel:,.0f}), "
                         "meaning the strategy would have been better off holding the index.")
        lines.append("")

        # Early vs Late sub-table
        early_s = next((s for s in sums if s["period"] == "early"), None)
        late_s = next((s for s in sums if s["period"] == "late"), None)
        if early_s and late_s:
            period_hdrs = ["Period", "N", "Stock PnL", "Index Comp", "Selection Comp",
                           "% Added Value"]
            period_rows = []
            for label, s in [("Early (pre-Mar 22)", early_s), ("Late (Mar 22+)", late_s)]:
                period_rows.append([
                    label, s["n_trades"],
                    f"${s['total_actual_stock_pnl']:,.0f}",
                    f"${s['total_index_component_pnl']:,.0f}",
                    f"${s['total_selection_component_pnl']:,.0f}",
                    f"{s['pct_trades_that_added_value_vs_index']:.1f}%",
                ])
            lines.append(_md_table(period_hdrs, period_rows))
            lines.append("")

        # Per-fold table for WF configs
        fold_sums = [s for s in sums if s["period"].startswith("fold_")]
        if fold_sums:
            lines.append("**Per-fold OOS:**")
            lines.append("")
            f_hdrs = ["Fold", "N", "Stock PnL", "Index Comp", "Selection Comp", "% Added Value"]
            f_rows = []
            for s in sorted(fold_sums, key=lambda x: x["fold_id"]):
                f_rows.append([
                    s["fold_id"], s["n_trades"],
                    f"${s['total_actual_stock_pnl']:,.0f}",
                    f"${s['total_index_component_pnl']:,.0f}",
                    f"${s['total_selection_component_pnl']:,.0f}",
                    f"{s['pct_trades_that_added_value_vs_index']:.1f}%",
                ])
            lines.append(_md_table(f_hdrs, f_rows))
            lines.append("")

    # Cross-config comparison for full_oos
    lines += ["## Cross-Config OOS Comparison", ""]
    oos_sums = [s for s in summaries if s["period"] == "full_oos"]
    if oos_sums:
        c_hdrs = ["Config", "Bench", "N", "Stock PnL", "Index Comp", "Select Comp",
                  "Sel/Trade", "% Beat Index", "% Losers Beat"]
        c_rows = []
        for s in sorted(oos_sums, key=lambda x: (x["config_slug"], x["benchmark"])):
            c_rows.append([
                s["config_slug"], s["benchmark"], s["n_trades"],
                f"${s['total_actual_stock_pnl']:,.0f}",
                f"${s['total_index_component_pnl']:,.0f}",
                f"${s['total_selection_component_pnl']:,.0f}",
                f"${s['avg_selection_component_pnl']:,.0f}",
                f"{s['pct_trades_that_added_value_vs_index']:.1f}%",
                f"{s['pct_losers_that_still_beat_index']:.1f}%",
            ])
        lines.append(_md_table(c_hdrs, c_rows))
        lines.append("")

    report = "\n".join(lines)
    with open(OUTPUT_DIR / "summary_report.md", "w", encoding="utf-8") as f:
        f.write(report)


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("CEM LOSS ATTRIBUTION")
    print("=" * 70)

    print("\n[1/5] Loading benchmark prices...")
    bench_closes = load_benchmark_closes()
    for sym, closes in bench_closes.items():
        print(f"  {sym}: {len(closes)} trading days")

    print("\n[2/5] Loading fold windows...")
    fold_windows = load_fold_windows()
    print(f"  {len(fold_windows)} (experiment, benchmark) pairs")

    print("\n[3/5] Processing trade logs...")
    all_trades = []
    log_files = sorted(TRADE_LOG_DIR.glob("*.csv"))
    for fpath in log_files:
        benchmark, config_slug, split = parse_filename(fpath.name)
        exp_name = SLUG_TO_EXPERIMENT.get(config_slug)
        fold_list = None
        if config_slug in WF_SLUGS and exp_name:
            fold_list = fold_windows.get((exp_name, benchmark))

        df = pd.read_csv(fpath)
        for _, row in df.iterrows():
            t = process_trade(row, benchmark, config_slug, split,
                              bench_closes, fold_list)
            all_trades.append(t)
        print(f"  {fpath.name}: {len(df)} trades")

    print(f"\n  Total trades processed: {len(all_trades)}")

    # Sanity check: actual_stock_pnl ≈ index_component + selection_component
    diffs = [abs(t["actual_stock_pnl"] - t["index_component_pnl"] - t["selection_component_pnl"])
             for t in all_trades]
    print(f"  Decomposition check: max residual = ${max(diffs):.4f}")

    print("\n[4/5] Computing summaries...")
    summaries = build_summaries(all_trades)
    print(f"  {len(summaries)} summary rows")

    print("\n[5/5] Writing output...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    trade_df = pd.DataFrame(all_trades)
    trade_df.to_csv(OUTPUT_DIR / "cem_trade_loss_attribution.csv", index=False)
    print(f"  Trade CSV: {len(trade_df)} rows")

    sum_cols = ["benchmark", "config_slug", "split", "period", "fold_id",
                "n_trades", "total_actual_stock_pnl", "total_index_component_pnl",
                "total_selection_component_pnl", "loss_from_index_down_moves",
                "loss_from_bad_selection",
                "opportunity_cost_positive_stock_underperformed",
                "n_absolute_losing_trades", "n_lost_but_beat_index",
                "n_lost_and_underperformed_index",
                "n_positive_but_underperformed_index", "n_positive_and_beat_index",
                "pct_losers_that_still_beat_index",
                "pct_trades_that_added_value_vs_index",
                "avg_selection_component_pnl", "median_selection_component_pnl",
                "avg_index_component_pnl", "median_index_component_pnl"]
    sum_df = pd.DataFrame(summaries)
    existing_cols = [c for c in sum_cols if c in sum_df.columns]
    sum_df[existing_cols].to_csv(OUTPUT_DIR / "cem_loss_attribution_summary.csv", index=False)
    print(f"  Summary CSV: {len(sum_df)} rows")

    write_markdown(all_trades, summaries)
    print(f"  Report: summary_report.md")

    # Quick OOS highlights
    print("\n" + "=" * 70)
    print("OOS HIGHLIGHTS (full_oos)")
    print("=" * 70)
    for s in sorted(summaries, key=lambda x: (x["config_slug"], x["benchmark"])):
        if s["period"] != "full_oos":
            continue
        print(f"\n  {s['config_slug']}/{s['benchmark']}:  "
              f"N={s['n_trades']}  "
              f"Stock=${s['total_actual_stock_pnl']:>10,.0f}  "
              f"Index=${s['total_index_component_pnl']:>10,.0f}  "
              f"Selection=${s['total_selection_component_pnl']:>10,.0f}  "
              f"AddedValue={s['pct_trades_that_added_value_vs_index']:.1f}%")

    print("\n" + "=" * 70)
    print("Output files:")
    for fname in ["cem_trade_loss_attribution.csv",
                  "cem_loss_attribution_summary.csv",
                  "summary_report.md"]:
        fpath = OUTPUT_DIR / fname
        ok = fpath.exists()
        sz = fpath.stat().st_size if ok else 0
        print(f"  {'OK' if ok else 'MISSING'} {fname} ({sz:,} bytes)")


if __name__ == "__main__":
    main()
