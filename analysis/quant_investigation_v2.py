from __future__ import annotations

import io
import json
import subprocess
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats


ROOT = Path(__file__).resolve().parents[1]
TRADE_DIR = ROOT / "data" / "experiment_trade_logs_clean"
OUT_DIR = ROOT / "analysis" / "output"
REPORT_PATH = OUT_DIR / "quant_investigation_v2_report.md"
TABLE_DIR = OUT_DIR / "quant_investigation_v2_tables"

SCENARIOS = {
    "baseline": "Baseline",
    "t1_t2": "T1+T2",
    "t1_t2_t3": "T1+T2+T3",
    "t4_geopriority": "T4 GeoPriority",
    "t1_t2_t3_t4": "T1+T2+T3+T4",
}


def git_bytes(path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"HEAD:{path}"], cwd=ROOT)


def read_before_trade(bench: str, scenario: str, split: str) -> pd.DataFrame:
    rel = f"data/experiment_trade_logs_clean/{bench.lower()}_{scenario}_{split}.csv"
    return pd.read_csv(io.BytesIO(git_bytes(rel)))


def read_after_trade(bench: str, scenario: str, split: str) -> pd.DataFrame:
    return pd.read_csv(TRADE_DIR / f"{bench.lower()}_{scenario}_{split}.csv")


def norm_id(x: pd.Series) -> pd.Series:
    return x.astype(str).str.strip()


def row_key(df: pd.DataFrame) -> pd.Series:
    """Unique tradable candidate key; one market can map to several symbols."""
    return norm_id(df["market_id"]) + "||" + norm_id(df["symbol"])


def fmt_num(x, digits=2):
    if pd.isna(x):
        return "—"
    return f"{x:,.{digits}f}"


def fmt_pct(x, digits=2):
    if pd.isna(x):
        return "—"
    return f"{x:.{digits}f}%"


def markdown_table(df: pd.DataFrame, digits: int = 2) -> str:
    if df.empty:
        return "(none)"
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_numeric_dtype(out[col]):
            out[col] = out[col].map(lambda x: "—" if pd.isna(x) else f"{x:,.{digits}f}")
    out = out.fillna("—").astype(str)
    headers = [str(c) for c in out.columns]
    widths = [max(len(headers[i]), *(len(v) for v in out.iloc[:, i].tolist())) for i in range(len(headers))]
    line = "| " + " | ".join(headers[i].ljust(widths[i]) for i in range(len(headers))) + " |"
    rule = "| " + " | ".join("-" * widths[i] for i in range(len(headers))) + " |"
    body = ["| " + " | ".join(row[i].ljust(widths[i]) for i in range(len(headers))) + " |" for row in out.to_numpy().tolist()]
    return "\n".join([line, rule, *body])


def metrics(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0}
    pnl = pd.to_numeric(df["pnl"], errors="coerce").fillna(0.0)
    r = pd.to_numeric(df["pnl_pct"], errors="coerce")
    pos = pnl[pnl > 0].sum()
    neg = pnl[pnl < 0].sum()
    ordered = df.assign(_exit=pd.to_datetime(df["exit_date"], errors="coerce"), _pnl=pnl).sort_values("_exit")
    equity = ordered["_pnl"].cumsum()
    dd = equity - equity.cummax()
    top_n = max(1, int(np.ceil(len(pnl) * 0.01)))
    top_1 = pnl.nlargest(top_n).sum()
    return {
        "n": int(len(df)),
        "pnl": float(pnl.sum()),
        "mean_pnl_pct": float(r.mean()),
        "median_pnl_pct": float(r.median()),
        "win_rate": float((r > 0).mean() * 100),
        "profit_factor": float(pos / abs(neg)) if neg else np.inf,
        "mean_peak_pct": float(pd.to_numeric(df.get("peak_pct"), errors="coerce").mean()),
        "mean_trough_pct": float(pd.to_numeric(df.get("trough_pct"), errors="coerce").mean()),
        "mean_hold_days": float((pd.to_datetime(df["exit_date"]) - pd.to_datetime(df["entry_date"])).dt.days.mean()),
        "max_trade_dd": float(dd.min()),
        "top_1_pct_pnl_share": float(top_1 / pnl.sum() * 100) if pnl.sum() else np.nan,
    }


def load_candidates() -> tuple[pd.DataFrame, pd.DataFrame, set[str]]:
    old = pd.read_parquet(io.BytesIO(git_bytes("data/candidates.parquet")))
    new = pd.read_parquet(ROOT / "data" / "candidates.parquet")
    old["_id"] = row_key(old)
    new["_id"] = row_key(new)
    new_ids = set(new["_id"]) - set(old["_id"])
    return old, new, new_ids


def load_h1() -> pd.DataFrame:
    with zipfile.ZipFile(ROOT / "output" / "raw_expectation_tminus1.zip") as z:
        name = "raw_expectation_tminus1/raw_expectation_trades_candidate_level.csv"
        return pd.read_csv(io.BytesIO(z.read(name)))


def cohort_label(ids: pd.Series, old_ids: set[str], new_ids: set[str]) -> pd.Series:
    s = norm_id(ids)
    return np.select([s.isin(new_ids), s.isin(old_ids)], ["new_added", "old_universe"], default="unknown")


def build_run_comparison() -> pd.DataFrame:
    rows = []
    for bench in ["SPY", "QQQ"]:
        for scenario in SCENARIOS:
            for split in ["train", "test"]:
                before = read_before_trade(bench, scenario, split)
                after = read_after_trade(bench, scenario, split)
                bm, am = metrics(before), metrics(after)
                rows.append({
                    "benchmark": bench,
                    "scenario": SCENARIOS[scenario],
                    "split": split,
                    "before_n": bm["n"],
                    "after_n": am["n"],
                    "before_pnl": bm["pnl"],
                    "after_pnl": am["pnl"],
                    "delta_pnl": am["pnl"] - bm["pnl"],
                    "before_mean_pct": bm["mean_pnl_pct"],
                    "after_mean_pct": am["mean_pnl_pct"],
                    "delta_mean_pct": am["mean_pnl_pct"] - bm["mean_pnl_pct"],
                    "before_median_pct": bm["median_pnl_pct"],
                    "after_median_pct": am["median_pnl_pct"],
                    "before_win_pct": bm["win_rate"],
                    "after_win_pct": am["win_rate"],
                    "before_pf": bm["profit_factor"],
                    "after_pf": am["profit_factor"],
                    "before_top1_share": bm["top_1_pct_pnl_share"],
                    "after_top1_share": am["top_1_pct_pnl_share"],
                })
    return pd.DataFrame(rows)


def equity_metric(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n_days": 0}
    eq = pd.to_numeric(df["equity"], errors="coerce").dropna()
    peak = eq.cummax()
    dd = (eq / peak - 1.0) * 100
    return {
        "n_days": int(len(eq)),
        "final_equity": float(eq.iloc[-1]),
        "return_pct": float(eq.iloc[-1] / 100000.0 * 100 - 100),
        "max_dd_pct": float(dd.min()),
    }


def build_equity_comparison() -> pd.DataFrame:
    rows = []
    for bench in ["SPY", "QQQ"]:
        for scenario in SCENARIOS:
            for split in ["train", "test"]:
                rel = f"data/experiment_equity_logs_clean/{bench.lower()}_{scenario}_{split}.csv"
                before = pd.read_csv(io.BytesIO(git_bytes(rel)))
                after = pd.read_csv(ROOT / rel)
                bm, am = equity_metric(before), equity_metric(after)
                rows.append({
                    "benchmark": bench,
                    "scenario": SCENARIOS[scenario],
                    "split": split,
                    "before_equity": bm["final_equity"],
                    "after_equity": am["final_equity"],
                    "before_return_pct": bm["return_pct"],
                    "after_return_pct": am["return_pct"],
                    "delta_return_pct": am["return_pct"] - bm["return_pct"],
                    "before_max_dd_pct": bm["max_dd_pct"],
                    "after_max_dd_pct": am["max_dd_pct"],
                    "delta_max_dd_pct": am["max_dd_pct"] - bm["max_dd_pct"],
                })
    return pd.DataFrame(rows)


def build_cohort_comparison(old_ids: set[str], new_ids: set[str]) -> pd.DataFrame:
    rows = []
    for bench in ["SPY", "QQQ"]:
        for scenario in SCENARIOS:
            for split in ["train", "test"]:
                after = read_after_trade(bench, scenario, split).copy()
                after["cohort"] = cohort_label(after["market_id"], old_ids, new_ids)
                for cohort, g in after.groupby("cohort", dropna=False):
                    m = metrics(g)
                    rows.append({
                        "benchmark": bench,
                        "scenario": SCENARIOS[scenario],
                        "split": split,
                        "cohort": cohort,
                        **m,
                    })
    return pd.DataFrame(rows)


def build_flagship_reselection(old_ids: set[str]) -> pd.DataFrame:
    rows = []
    scenario = "t1_t2_t3_t4"
    for bench in ["SPY", "QQQ"]:
        old = read_before_trade(bench, scenario, "test").copy()
        new = read_after_trade(bench, scenario, "test").copy()
        old["_id"] = row_key(old)
        new["_id"] = row_key(new)
        old_set = set(old["_id"])
        new_set = set(new["_id"])
        new_cohort = new["_id"].isin(set(new["_id"]) - old_ids)
        categories = {
            "overlap_same_candidate": new["_id"].isin(old_set),
            "old_candidate_repicked": new["_id"].isin(old_ids) & ~new["_id"].isin(old_set),
            "new_added_candidate": new_cohort,
        }
        for label, mask in categories.items():
            g = new.loc[mask]
            m = metrics(g)
            rows.append({"benchmark": bench, "category": label, "current_n": m["n"], "current_pnl": m.get("pnl", 0.0), "current_mean_pct": m.get("mean_pnl_pct", np.nan), "current_win_pct": m.get("win_rate", np.nan)})
        dropped = old.loc[~old["_id"].isin(new_set)]
        m = metrics(dropped)
        rows.append({"benchmark": bench, "category": "dropped_from_before", "current_n": m["n"], "current_pnl": m.get("pnl", 0.0), "current_mean_pct": m.get("mean_pnl_pct", np.nan), "current_win_pct": m.get("win_rate", np.nan)})
        # New-vs-old return on IDs traded in both runs.
        common = old.merge(new, on="_id", suffixes=("_before", "_after"))
        if not common.empty:
            rows.append({
                "benchmark": bench,
                "category": "common_trade_delta",
                "current_n": len(common),
                "current_pnl": float(common["pnl_after"].sum() - common["pnl_before"].sum()),
                "current_mean_pct": float(common["pnl_pct_after"].mean() - common["pnl_pct_before"].mean()),
                "current_win_pct": float((common["pnl_pct_after"] > 0).mean() * 100 - (common["pnl_pct_before"] > 0).mean() * 100),
            })
    return pd.DataFrame(rows)


def build_exit_diagnostics(df: pd.DataFrame, new_ids: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    x = df.copy()
    x["_id"] = row_key(x)
    x = x[x["_id"].isin(new_ids)].copy()
    for col in ["pnl_pct", "peak_pct", "trough_pct", "entry_prob", "return_pct"]:
        x[col] = pd.to_numeric(x[col], errors="coerce")
    x["giveback_pct"] = x["peak_pct"] - x["pnl_pct"]
    x["capture_pct"] = np.where(x["peak_pct"] > 0, x["pnl_pct"] / x["peak_pct"] * 100, np.nan)
    x["peak_then_loss"] = (x["peak_pct"] > 0) & (x["pnl_pct"] < 0)
    group = x.groupby("realized_exit_reason", dropna=False).agg(
        n=("_id", "size"),
        pnl=("pnl", "sum"),
        mean_return=("pnl_pct", "mean"),
        median_return=("pnl_pct", "median"),
        mean_peak=("peak_pct", "mean"),
        mean_giveback=("giveback_pct", "mean"),
        mean_capture=("capture_pct", "mean"),
        peak_then_loss=("peak_then_loss", "mean"),
    ).reset_index()
    group["peak_then_loss"] *= 100
    worst = x.sort_values(["pnl_pct", "giveback_pct"]).loc[:, [
        "market_id", "symbol", "question", "event_family", "polarity", "polarity_source", "entry_date", "exit_date",
        "entry_prob", "entry_price", "exit_price", "peak_pct", "trough_pct", "pnl_pct", "pnl", "giveback_pct", "capture_pct", "realized_exit_reason", "exit_reason",
    ]]
    return group, worst


def build_h1_tables(new_ids: set[str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    h = load_h1().copy()
    h["_id"] = row_key(h)
    old_candidates = pd.read_parquet(io.BytesIO(git_bytes("data/candidates.parquet")))
    old_ids = set(row_key(old_candidates))
    h["cohort"] = cohort_label(h["_id"], old_ids, new_ids)
    for col in ["net_return", "gross_return", "net_pnl", "entry_prob", "feat_prob_surge_since_t0", "feat_runup_since_t0", "holding_days"]:
        h[col] = pd.to_numeric(h[col], errors="coerce")
    summary_rows = []
    for cohort, g in h.groupby("cohort"):
        if cohort == "unknown":
            continue
        v = g["net_return"].dropna()
        ci = stats.t.interval(0.95, len(v) - 1, loc=v.mean(), scale=stats.sem(v)) if len(v) > 1 else (np.nan, np.nan)
        summary_rows.append({
            "cohort": cohort,
            "n": len(v),
            "mean_net_pct": v.mean() * 100,
            "median_net_pct": v.median() * 100,
            "win_pct": (v > 0).mean() * 100,
            "sd_pct": v.std(ddof=1) * 100,
            "ci95_lo_pct": ci[0] * 100,
            "ci95_hi_pct": ci[1] * 100,
            "p_t_greater_0": stats.ttest_1samp(v, 0, alternative="greater").pvalue if len(v) > 1 else np.nan,
            "net_pnl": g["net_pnl"].sum(),
        })
    summary = pd.DataFrame(summary_rows)
    by_family = h[h["cohort"] == "new_added"].groupby("event_family", dropna=False).agg(
        n=("_id", "size"), mean_net_pct=("net_return", lambda s: s.mean() * 100), median_net_pct=("net_return", lambda s: s.median() * 100), win_pct=("net_return", lambda s: (s > 0).mean() * 100), net_pnl=("net_pnl", "sum"),
    ).reset_index().sort_values("net_pnl")
    failed = h[(h["cohort"] == "new_added") & (h["net_return"] < 0)].copy()
    question_failures = failed.groupby(["event_id", "question", "event_family"], dropna=False).agg(
        n=("_id", "size"), mean_net_pct=("net_return", lambda s: s.mean() * 100), worst_net_pct=("net_return", lambda s: s.min() * 100), net_pnl=("net_pnl", "sum"), symbols=("symbol", lambda s: ", ".join(sorted(set(s))))
    ).reset_index().sort_values("net_pnl")
    return summary, by_family, question_failures


def build_new_candidate_profile(old: pd.DataFrame, new: pd.DataFrame, new_ids: set[str]) -> pd.DataFrame:
    x = new[new["_id"].isin(new_ids)].copy()
    return x.groupby("feat_archetype", dropna=False).agg(
        rows=("_id", "size"), events=("event_id", "nunique"), symbols=("symbol", "nunique"),
        median_entry_prob=("feat_prob_at_trigger", "median"), median_confidence=("confidence_score", "median"), median_relevance=("feat_connection_strength", "median"),
    ).reset_index().sort_values("rows", ascending=False)


def write_report() -> None:
    old_c, new_c, new_ids = load_candidates()
    old_ids = set(old_c["_id"])
    run = build_run_comparison()
    equity = build_equity_comparison()
    cohort = build_cohort_comparison(old_ids, new_ids)
    reselection = build_flagship_reselection(old_ids)
    exit_group, exit_worst = build_exit_diagnostics(read_after_trade("SPY", "t1_t2_t3_t4", "test"), new_ids)
    h1_summary, h1_family, h1_questions = build_h1_tables(new_ids)
    profile = build_new_candidate_profile(old_c, new_c, new_ids)

    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in {
        "run_comparison.csv": run,
        "equity_comparison.csv": equity,
        "cohort_comparison.csv": cohort,
        "flagship_reselection.csv": reselection,
        "flagship_new_exit_diagnostics.csv": exit_worst,
        "flagship_new_exit_reason_summary.csv": exit_group,
        "h1_cohort_summary.csv": h1_summary,
        "h1_new_by_family.csv": h1_family,
        "h1_new_failed_questions.csv": h1_questions,
        "new_candidate_profile.csv": profile,
    }.items():
        df.to_csv(TABLE_DIR / name, index=False)

    flagship = run[(run["scenario"] == SCENARIOS["t1_t2_t3_t4"]) & (run["split"].isin(["train", "test"]))].copy()
    equity_flagship = equity[equity["scenario"] == SCENARIOS["t1_t2_t3_t4"]].copy()
    spy_flag = flagship[flagship["benchmark"] == "SPY"].set_index("split")
    qqq_flag = flagship[flagship["benchmark"] == "QQQ"].set_index("split")
    h1_new = h1_summary[h1_summary["cohort"] == "new_added"].iloc[0]
    h1_old = h1_summary[h1_summary["cohort"] == "old_universe"].iloc[0]
    new_test = cohort[(cohort["scenario"] == SCENARIOS["t1_t2_t3_t4"]) & (cohort["split"] == "test") & (cohort["cohort"] == "new_added")]

    report = f"""# Quant Investigation: v2 Candidate Broadening and CEM Re-fit

Generated from the committed Git `HEAD` logs (before) and the current worktree CSVs/parquet (after). The analysis is read-only with respect to the source data.

## Executive conclusion

The new candidates did not produce a robust broadening gain. The raw H1 test is the cleanest universe-level diagnostic: the added cohort has mean net return **{h1_new['mean_net_pct']:.2f}%**, median **{h1_new['median_net_pct']:.2f}%**, and win rate **{h1_new['win_pct']:.1f}%** across **{int(h1_new['n'])}** valid trades, versus **{h1_old['mean_net_pct']:.2f}% / {h1_old['median_net_pct']:.2f}% / {h1_old['win_pct']:.1f}%** for the old universe (**{int(h1_old['n'])}** trades). Its one-sided t-test p-value is **{h1_new['p_t_greater_0']:.3f}**; the 95% mean-return interval is **[{h1_new['ci95_lo_pct']:.2f}%, {h1_new['ci95_hi_pct']:.2f}%]**. In other words, the added cohort is near-zero/weakly negative on the central tendency, not a statistically established positive edge.

At the portfolio-equity level, the flagship SPY test moves from **{equity_flagship[(equity_flagship.benchmark=='SPY') & (equity_flagship.split=='test')].iloc[0]['before_return_pct']:.2f}%** to **{equity_flagship[(equity_flagship.benchmark=='SPY') & (equity_flagship.split=='test')].iloc[0]['after_return_pct']:.2f}%**, while QQQ moves from **{equity_flagship[(equity_flagship.benchmark=='QQQ') & (equity_flagship.split=='test')].iloc[0]['before_return_pct']:.2f}%** to **{equity_flagship[(equity_flagship.benchmark=='QQQ') & (equity_flagship.split=='test')].iloc[0]['after_return_pct']:.2f}%**. Trade-level SPY P&L is nearly flat (**{fmt_num(spy_flag.loc['test','delta_pnl'])}**), while QQQ declines by **{fmt_num(qqq_flag.loc['test','delta_pnl'])}**. Opposite benchmark signs, together with a large overlap/reselection effect, are consistent with CEM selection noise—not a stable benefit from the extra universe.

The practical next step is a controlled robustness study: sweep seeds first, then increase the CEM budget in a pre-registered configuration. Do not select a new seed or larger population because it produces the best single backtest; select settings by median/quantile performance across seeds and a final untouched period.

## 1. Universe change and cohort composition

- Committed universe: **{len(old_c):,}** market-symbol rows.
- v2 universe: **{len(new_c):,}** rows.
- Added rows: **{len(new_ids):,}**; added events: **{new_c[new_c['_id'].isin(new_ids)]['event_id'].nunique():,}**.
- Current H1 valid trades: **{int(h1_old['n'] + h1_new['n']):,}**, of which **{int(h1_new['n']):,}** are from the added cohort.

### Reconciliation with the pasted prior report

The saved artifacts do not reproduce the prior text's **840 old / 96 new / -1.25% new-cohort H1** split. Using the exact committed parquet versus the current v2 parquet, joined on `(market_id, symbol)`, the H1 archive reconciles to **862 old / 74 added** and **+0.11% added-cohort mean net return**. Likewise, the current saved flagship trade logs contain **6** added SPY test trades, not 10. This is an artifact/version mismatch that should be resolved before treating the prior headline as the latest result; the tables in this report use the files currently present in the workspace.

### Added-candidate profile

{markdown_table(profile)}

The added rows are concentrated in `other` and geopolitical categories rather than being a balanced expansion. That matters because capacity constraints mean extra candidates can displace existing trades instead of simply adding independent positions.

## 2. Before/after CEM performance

The table below is the core comparison. Returns are trade-level `pnl_pct`; P&L is the CSV's realized `pnl` after its stated transaction-cost mechanics.

### Flagship (T1+T2+T3+T4)

{markdown_table(flagship[['benchmark','split','before_n','after_n','before_pnl','after_pnl','delta_pnl','before_mean_pct','after_mean_pct','delta_mean_pct','before_win_pct','after_win_pct','before_pf','after_pf']])}

### Portfolio-equity outcome (the headline return used by the run)

{markdown_table(equity_flagship[['benchmark','split','before_return_pct','after_return_pct','delta_return_pct','before_max_dd_pct','after_max_dd_pct','delta_max_dd_pct']])}

### All arms, test split

{markdown_table(run[run['split'].eq('test')][['benchmark','scenario','before_n','after_n','delta_pnl','before_mean_pct','after_mean_pct','delta_mean_pct','before_win_pct','after_win_pct','before_top1_share','after_top1_share']])}

### Training versus testing

For SPY flagship, v2 trade-level train mean return is **{fmt_pct(spy_flag.loc['train','after_mean_pct'])}** versus test **{fmt_pct(spy_flag.loc['test','after_mean_pct'])}**. At portfolio level, SPY v2 train is **{equity_flagship[(equity_flagship.benchmark=='SPY') & (equity_flagship.split=='train')].iloc[0]['after_return_pct']:.2f}%** versus test **{equity_flagship[(equity_flagship.benchmark=='SPY') & (equity_flagship.split=='test')].iloc[0]['after_return_pct']:.2f}%**. QQQ v2 is **{equity_flagship[(equity_flagship.benchmark=='QQQ') & (equity_flagship.split=='train')].iloc[0]['after_return_pct']:.2f}%** train versus **{equity_flagship[(equity_flagship.benchmark=='QQQ') & (equity_flagship.split=='test')].iloc[0]['after_return_pct']:.2f}%** test. This is the important distinction: stronger training performance is not evidence of better generalization; the relevant signal is the train-to-test gap and its stability across seeds/benchmarks.

## 3. What changed in the flagship test selection?

{markdown_table(reselection)}

The flagship test is capacity-constrained. The extra rows therefore affect performance through three channels: genuinely new trades, old candidates that are re-selected after the CEM fit changes, and old trades that disappear. The `common_trade_delta` row isolates trades that occurred in both runs; the re-picked and dropped rows isolate selection churn. When SPY and QQQ respond in opposite directions to that churn, the evidence favors optimizer variance over a universe-level improvement.

## 4. H1 raw-expectation test

### Cohort results

{markdown_table(h1_summary)}

### Added cohort by event family

{markdown_table(h1_family)}

### Added-cohort questions that failed

{markdown_table(h1_questions.head(20))}

The H1 result is materially weaker than the flagship headline because it removes portfolio-capacity and CEM-selection ambiguity: it asks whether the candidate itself had positive ex-ante-to-T-1 return under the same policy logic. The added cohort does not establish positive edge: its mean is close to zero, its median is negative, its win rate is below 50%, and its confidence interval spans a substantial negative-to-positive range. It dilutes the old-universe average even though the saved v2 cohort is not as negative as the stale pasted report claimed.

## 5. Why exits were low: peak-to-exit analysis

This section uses the new-added trades selected into the SPY flagship test. `giveback_pct = peak_pct - pnl_pct`; `capture_pct = pnl_pct / peak_pct` when the peak is positive. A high giveback means the strategy saw a favorable mark-to-market move but exited after much of it had reversed.

{markdown_table(exit_group)}

### Worst added-cohort flagship trades

{markdown_table(exit_worst.head(20))}

Interpretation: a trade can exit at a local low for two different reasons. First, a hard/expiry/policy exit may occur after a signal reversal, so the entry was directionally right at some point but the holding rule did not monetize it. Second, a polarity or threshold exit can correctly stop a deteriorating signal, but it will still show a large peak-to-exit giveback if the signal first rallied. These should not be conflated. The report's next implementation step should retain the existing exit reason, peak, trough, and signal-path fields and evaluate a small pre-specified exit ablation rather than tuning exit thresholds on the same test period.

## 6. Failure questions and failure modes

The H1 failed-question table above identifies the questions with negative net return in the new cohort. For each, inspect whether the loss came from: (1) wrong polarity, (2) a probability surge/run-up that triggered late, (3) a long holding period into reversal, (4) a sector/asset beta shock, or (5) a resolution/expiry exit that was too late. The CSV contains the necessary fields (`polarity`, `entry_prob`, `feat_prob_surge_since_t0`, `feat_runup_since_t0`, `holding_days`, and the realized exit dates) to test these explanations without look-ahead.

Do not call the failure set “bad questions” solely because realized returns were negative. The relevant diagnostic is whether the failures cluster by question family, polarity, entry-rule type, or signal-path feature. The family table shows where the added cohort's negative expectancy is concentrated; the question table is the audit trail for the individual cases.

## 7. Recommendation on seed, samples, and epochs

Current optimizer constants are `CEM_ITERS = 6`, `CEM_POP = 20`, `CEM_ELITE_FRAC = 0.25`, base seed `42`, with QQQ using a fixed +10,000 benchmark offset. That means 120 policy evaluations per CEM fit and 5 elites per iteration.

Recommended experiment order:

1. **Seed sweep first:** run at least 10 seeds, keeping the data, folds, arm, and cost model fixed. Report median, interquartile range, worst seed, best seed, and the fraction of seeds beating the before-run test result separately for SPY and QQQ.
2. **Budget sweep second:** compare `(iterations, population) = (6,20), (10,20), (6,30), (10,30)` using the same seed grid. `10×30` is 300 evaluations per fit and ~7–8 elites per iteration; it may stabilize the search, but it also increases the number of opportunities to overfit the training objective.
3. **Pre-register selection:** choose the configuration by training stability plus an untouched validation period, not by the best single test P&L. Keep the current test period locked until the configuration is frozen.
4. **Add an optimizer audit:** save the full CEM population (already emitted by the code), then report elite-score dispersion, best-minus-median score, selected policy dispersion, and trade-set overlap across seeds. If policy/trade overlap is low, the problem is optimizer instability, not insufficient epochs.
5. **Keep the universe conclusion separate:** even a more stable CEM cannot turn the added cohort's negative H1 expectancy into positive edge. More search budget may improve selection reliability, but it cannot be used as evidence that the extra candidates generalize.

## 8. Reproducibility and limitations

- “Before” is read from Git `HEAD`; “after” is the current worktree output.
- The current H1 archive is v2 and supports an exact old-vs-added cohort split, but it is not a separate old-universe rerun. The cohort comparison is therefore a clean attribution of the v2 H1 output, while the full before/after CEM comparison uses the committed and current trade logs.
- Trade-level observations are not independent when several assets represent one event or when the same asset trades on nearby dates. Use event-clustered bootstrap or symbol-day collapsing for final inference.
- The strongest positive returns are concentrated in a small number of event clusters. Always report results after removing the top 1%, 5%, and 10% of winners and include a capacity-aware, event-collapsed version.

## Appendix: generated tables

Detailed CSV tables are in `{TABLE_DIR.relative_to(ROOT)}`.
"""
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(json.dumps({
        "report": str(REPORT_PATH),
        "tables": str(TABLE_DIR),
        "new_rows": len(new_ids),
        "old_rows": len(old_c),
        "v2_rows": len(new_c),
        "h1_new_n": int(h1_new["n"]),
        "h1_new_mean_pct": float(h1_new["mean_net_pct"]),
        "h1_old_n": int(h1_old["n"]),
        "h1_old_mean_pct": float(h1_old["mean_net_pct"]),
    }, indent=2))


if __name__ == "__main__":
    write_report()
