"""Final statistical report for the ICAIF paper.

Collects every number the paper relies on into final_statistical_results.csv
(one row per estimate: estimand, sample, observation level, dependence unit, N,
estimate, CI, p-value + convention, source file) and writes
final_statistical_report.md answering the pre-declared questions.

Additional computations done here (with the same 20k/seed-42 conventions as the
H1 protocol): event-family event-clustered bootstrap CIs, the 2026-only
candidate subsample, and the NO-bullish subgroup.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(r"C:\Users\Liran\PycharmProjects\cem_clean_repo")
RUN = ROOT / "output" / "final_icaif_run_20260714_170817"
H1 = RUN / "h1" / "raw_expectation_tminus1_final"
H1_ABLATION = RUN / "h1" / "raw_expectation_tminus1_final_raw_yes"
BENCH = RUN / "benchmark_excess"
MATRIX = RUN / "cem_matrix"
ROBUST = RUN / "robustness"
OUT = Path(__file__).resolve().parent

N_BOOT = 20_000
SEED = 42


def economic_event_ids(frame: pd.DataFrame) -> pd.Series:
    event = frame["event_id"].astype("string").fillna("").str.strip()
    market = frame["market_id"].astype("string").fillna("").str.strip()
    return event.where(event.ne(""), "market-fallback:" + market)


def cluster_bootstrap(values: np.ndarray, labels: np.ndarray, seed: int) -> dict:
    values = np.asarray(values, dtype=float)
    labels = np.asarray(labels).astype(str)
    ok = np.isfinite(values)
    values, labels = values[ok], labels[ok]
    unique, inverse = np.unique(labels, return_inverse=True)
    groups = len(unique)
    if len(values) < 2 or groups < 2:
        return {"n_clusters": groups, "p": math.nan, "ci_lo": math.nan, "ci_hi": math.nan}
    sums = np.bincount(inverse, weights=values, minlength=groups)
    sizes = np.bincount(inverse, minlength=groups).astype(float)
    observed = float(np.mean(values))
    centered = sums - observed * sizes
    rng = np.random.default_rng(seed)
    nulls, raws = [], []
    for start in range(0, N_BOOT, 500):
        count = min(500, N_BOOT - start)
        draw = rng.integers(0, groups, size=(count, groups))
        denom = sizes[draw].sum(axis=1)
        nulls.append(centered[draw].sum(axis=1) / denom)
        raws.append(sums[draw].sum(axis=1) / denom)
    null = np.concatenate(nulls)
    raw = np.concatenate(raws)
    return {
        "n_clusters": groups,
        "p": float((np.sum(null >= observed) + 1) / (len(null) + 1)),
        "ci_lo": float(np.quantile(raw, 0.025)),
        "ci_hi": float(np.quantile(raw, 0.975)),
    }


def row(**kw) -> dict:
    base = dict(
        estimand="", sample="", observation_level="", dependence_unit="",
        n=np.nan, n_clusters=np.nan, estimate=np.nan, estimate_units="",
        median=np.nan, positive_frequency=np.nan, ci_lo=np.nan, ci_hi=np.nan,
        p_value=np.nan, p_convention="", source_file="",
    )
    base.update(kw)
    return base


def main() -> None:
    rows: list[dict] = []

    # ── H1 central + dependence ────────────────────────────────────────────
    inf = pd.read_csv(H1 / "h1_raw_expectation_cluster_inference.csv")
    src_inf = str(H1 / "h1_raw_expectation_cluster_inference.csv")
    for _, r in inf.iterrows():
        is_iid = r["cluster_type"] == "iid_reference"
        rows.append(row(
            estimand="H1 mean net return",
            sample="full 2024-08..2026-06, audit-clean",
            observation_level=r["level"],
            dependence_unit=("iid (t-test reference)" if is_iid else r["cluster_type"]),
            n=int(r["n"]), n_clusters=r.get("n_clusters", np.nan),
            estimate=float(r["mean_raw_net_return"]), estimate_units="net return (frac)",
            median=float(r["median_raw_net_return"]),
            positive_frequency=float(r["win_rate"]),
            ci_lo=r.get("cluster_ci_lo", np.nan), ci_hi=r.get("cluster_ci_hi", np.nan),
            p_value=(float(r["p_t_one_sided"]) if is_iid else float(r["cluster_bootstrap_p"])),
            p_convention=("one-sided t (reference only)" if is_iid
                          else "null-centered one-sided cluster bootstrap, (k+1)/(B+1); CI = uncentered percentile"),
            source_file=src_inf,
        ))

    # symbol-day collapsed (runner robustness output)
    rob = pd.read_csv(H1 / "raw_expectation_robustness.csv")
    sd = rob[rob["version"] == "symbol_day_collapsed"].iloc[0]
    rows.append(row(
        estimand="H1 mean net return",
        sample="full sample", observation_level="symbol_day_collapsed",
        dependence_unit="market cluster (runner bootstrap)",
        n=int(sd["n_trades"]), estimate=float(sd["mean_net_return"]),
        estimate_units="net return (frac)", median=float(sd["median_net_return"]),
        positive_frequency=float(sd["win_rate_net_return_gt_0"]),
        p_value=float(sd["event_cluster_bootstrap_p_value"]),
        p_convention="null-centered one-sided cluster bootstrap (k/B)",
        source_file=str(H1 / "raw_expectation_robustness.csv"),
    ))

    # ── H1 sensitivities (tail / cost / timing / family) ───────────────────
    sens = pd.read_csv(H1 / "h1_sensitivity.csv")
    src_sens = str(H1 / "h1_sensitivity.csv")
    for fam, label in (("tail", "tail sensitivity"), ("cost", "cost sensitivity"),
                       ("entry_timing", "timing sensitivity")):
        for _, r in sens[sens["family"] == fam].iterrows():
            rows.append(row(
                estimand=f"H1 mean net return ({label}: {r['variant']})",
                sample="full sample", observation_level=r["level"],
                dependence_unit="iid t-test", n=int(r["n"]),
                estimate=float(r["mean_raw_net_return"]), estimate_units="net return (frac)",
                median=r.get("median_raw_net_return", np.nan),
                positive_frequency=r.get("win_rate", np.nan),
                p_value=r.get("p_t_one_sided", np.nan), p_convention="one-sided t",
                source_file=src_sens,
            ))

    # family heterogeneity with event-clustered CIs (computed here)
    trades = pd.read_csv(H1 / "raw_expectation_trades_candidate_level.csv")
    trades["economic_event_id"] = economic_event_ids(trades)
    for offset, fam in enumerate(["earnings", "geo", "macro", "other"]):
        g = trades[trades["event_family"] == fam]
        if len(g) < 2:
            continue
        boot = cluster_bootstrap(g["net_return"].to_numpy(float),
                                 g["economic_event_id"].to_numpy(), SEED + 100 + offset)
        rows.append(row(
            estimand=f"H1 mean net return (event family: {fam})",
            sample="full sample", observation_level="candidate_observation",
            dependence_unit="economic_event_id cluster",
            n=len(g), n_clusters=boot["n_clusters"],
            estimate=float(g["net_return"].mean()), estimate_units="net return (frac)",
            median=float(g["net_return"].median()),
            positive_frequency=float((g["net_return"] > 0).mean()),
            ci_lo=boot["ci_lo"], ci_hi=boot["ci_hi"], p_value=boot["p"],
            p_convention="null-centered one-sided cluster bootstrap, (k+1)/(B+1)",
            source_file="computed here from raw_expectation_trades_candidate_level.csv",
        ))

    # 2026-only subsample (walk-forward point-in-time eligibility; online refits)
    sub = trades[trades["entry_date"] >= "2026-01-01"].copy()
    sub["entry_month"] = sub["entry_date"].str[:7]
    boot_e = cluster_bootstrap(sub["net_return"].to_numpy(float),
                               sub["economic_event_id"].to_numpy(), SEED + 200)
    boot_m = cluster_bootstrap(sub["net_return"].to_numpy(float),
                               sub["entry_month"].to_numpy(), SEED + 201)
    for unit, boot in (("economic_event_id cluster", boot_e), ("entry_month_block", boot_m)):
        rows.append(row(
            estimand="H1 mean net return (2026 subsample, entries >= 2026-01-01)",
            sample="Jan-Jun 2026 entries", observation_level="candidate_observation",
            dependence_unit=unit, n=len(sub), n_clusters=boot["n_clusters"],
            estimate=float(sub["net_return"].mean()), estimate_units="net return (frac)",
            median=float(sub["net_return"].median()),
            positive_frequency=float((sub["net_return"] > 0).mean()),
            ci_lo=boot["ci_lo"], ci_hi=boot["ci_hi"], p_value=boot["p"],
            p_convention="null-centered one-sided cluster bootstrap, (k+1)/(B+1)",
            source_file="computed here from raw_expectation_trades_candidate_level.csv",
        ))

    # polarity ablation + NO-bullish subgroup
    ablation = pd.read_csv(H1_ABLATION / "raw_expectation_trades_candidate_level.csv")
    rows.append(row(
        estimand="H1 mean net return (raw-YES polarity ablation)",
        sample="full sample", observation_level="candidate_observation",
        dependence_unit="descriptive", n=len(ablation),
        estimate=float(ablation["net_return"].mean()), estimate_units="net return (frac)",
        median=float(ablation["net_return"].median()),
        positive_frequency=float((ablation["net_return"] > 0).mean()),
        source_file=str(H1_ABLATION / "raw_expectation_trades_candidate_level.csv"),
    ))
    neg = trades[trades["polarity"] == -1]
    rows.append(row(
        estimand="H1 mean net return (NO-bullish subgroup, polarity -1)",
        sample="full sample", observation_level="candidate_observation",
        dependence_unit="descriptive", n=len(neg),
        estimate=float(neg["net_return"].mean()), estimate_units="net return (frac)",
        median=float(neg["net_return"].median()),
        positive_frequency=float((neg["net_return"] > 0).mean()),
        source_file=str(H1 / "raw_expectation_trades_candidate_level.csv"),
    ))

    # ── Benchmark-relative ────────────────────────────────────────────────
    binf = pd.read_csv(BENCH / "benchmark_excess_inference.csv")
    src_binf = str(BENCH / "benchmark_excess_inference.csv")
    for _, r in binf.iterrows():
        rows.append(row(
            estimand=f"H1 mean excess return vs {r['benchmark']}",
            sample="full sample, matched entry/exit dates, $10k both legs",
            observation_level=r["level"], dependence_unit=r["scheme"],
            n=int(r["n"]), n_clusters=r.get("n_clusters", np.nan),
            estimate=float(r["observed_mean_excess_return"]), estimate_units="excess return (frac)",
            median=float(r["observed_median_excess_return"]),
            positive_frequency=float(r["positive_excess_frequency"]),
            ci_lo=float(r["ci_lo"]), ci_hi=float(r["ci_hi"]),
            p_value=float(r["p_boot_null_centered"]),
            p_convention="null-centered one-sided cluster bootstrap, (k+1)/(B+1); CI = uncentered percentile",
            source_file=src_binf,
        ))

    # ── CEM matrix ─────────────────────────────────────────────────────────
    matrix = pd.read_csv(MATRIX / "cem_matrix_final.csv")
    src_matrix = str(MATRIX / "cem_matrix_final.csv")
    for _, r in matrix.iterrows():
        rows.append(row(
            estimand=f"CEM test excess return: {r['experiment']}",
            sample=f"Jan-Jun 2026 test, seed 42, 6x20, {r['benchmark']}",
            observation_level="portfolio", dependence_unit="single path (no test)",
            n=int(r["test_trades"]), estimate=float(r["test_excess_return_pct"]),
            estimate_units="pct points of initial capital",
            source_file=src_matrix,
        ))

    # flagship block inference + cleaning delta
    block = pd.read_csv(MATRIX / "cem_oos_block_inference_final.csv")
    for _, r in block.iterrows():
        rows.append(row(
            estimand=f"CEM flagship mean daily excess return ({r['universe']})",
            sample=f"Jan-Jun 2026 daily equity, {r['benchmark']}, seed 42, 6x20",
            observation_level="daily portfolio return", dependence_unit="5-day moving block",
            n=int(r["n_daily_returns"]), estimate=float(r["mean_daily"]),
            estimate_units="daily excess return (frac)",
            ci_lo=float(r["boot_ci_lo"]), ci_hi=float(r["boot_ci_hi"]),
            p_value=float(r["boot_p_one_sided_null_centered"]),
            p_convention="null-centered one-sided moving-block bootstrap, (k+1)/(B+1); HAC p in source",
            source_file=str(MATRIX / "cem_oos_block_inference_final.csv"),
        ))
    delta = pd.read_csv(MATRIX / "cem_cleaning_delta_final.csv")
    for _, r in delta.iterrows():
        rows.append(row(
            estimand="CEM cleaning delta (audit-clean minus original daily return)",
            sample=f"matched Jan-Jun 2026 days, {r['benchmark']}",
            observation_level="daily portfolio return", dependence_unit="5-day moving block",
            n=int(r["n_matched_days"]), estimate=float(r["mean_daily"]),
            estimate_units="daily return difference (frac)",
            ci_lo=float(r["boot_ci_lo"]), ci_hi=float(r["boot_ci_hi"]),
            p_value=float(r["boot_p_one_sided_null_centered"]),
            p_convention="null-centered one-sided moving-block bootstrap",
            source_file=str(MATRIX / "cem_cleaning_delta_final.csv"),
        ))

    # ── Robustness grid + paired comparison (require grid completion) ──────
    budget_path = ROBUST / "icaif_robustness_budget_summary.csv"
    paired_path = ROBUST / "icaif_paired_summary.csv"
    if budget_path.exists():
        budget = pd.read_csv(budget_path)
        for _, r in budget.iterrows():
            rows.append(row(
                estimand="CEM flagship median test excess return across seeds",
                sample=f"Jan-Jun 2026 test, budget {r['budget']}, {r['benchmark']}, seeds 42-51",
                observation_level="portfolio (per-seed)", dependence_unit="seed distribution",
                n=int(r["n_seeds"]), estimate=float(r["median_test_excess_pct"]),
                estimate_units="pct points",
                ci_lo=float(r["q25_test_excess_pct"]), ci_hi=float(r["q75_test_excess_pct"]),
                p_convention="IQR shown in ci_lo/ci_hi (not a CI)",
                positive_frequency=float(r["positive_excess_seed_count"]) / float(r["n_seeds"]),
                source_file=str(budget_path),
            ))
    if paired_path.exists():
        paired = pd.read_csv(paired_path)
        for _, r in paired.iterrows():
            rows.append(row(
                estimand="Paired excess-return delta: T1+T2+T3+T4 minus Baseline (6x20)",
                sample=f"Jan-Jun 2026 test, same seed, {r['benchmark']}",
                observation_level="portfolio (per-seed pair)", dependence_unit="seed pairs",
                n=int(r["n_pairs"]), estimate=float(r["median_delta_excess_pct"]),
                estimate_units="pct points",
                ci_lo=float(r["q25_delta_excess_pct"]), ci_hi=float(r["q75_delta_excess_pct"]),
                p_convention="IQR shown in ci_lo/ci_hi (not a CI)",
                positive_frequency=float(r["pct_seeds_all_beats_baseline"]) / 100.0,
                source_file=str(paired_path),
            ))

    frame = pd.DataFrame(rows)
    frame.to_csv(OUT / "final_statistical_results.csv", index=False)
    print(f"wrote {len(frame)} rows -> final_statistical_results.csv")

    # ── Report ─────────────────────────────────────────────────────────────
    def pct(x):
        return "n/a" if pd.isna(x) else f"{x*100:+.3f}%"

    def get(estimand_sub, unit_sub=None, level=None):
        g = frame[frame["estimand"].str.contains(estimand_sub, regex=False)]
        if unit_sub:
            g = g[g["dependence_unit"].str.contains(unit_sub, regex=False)]
        if level:
            g = g[g["observation_level"] == level]
        return g

    cand_event = get("H1 mean net return", "economic_event_id", "candidate_observation").iloc[0]
    cand_sym = get("H1 mean net return", "symbol", "candidate_observation").iloc[0]
    cand_month = get("H1 mean net return", "entry_month", "candidate_observation").iloc[0]
    ev_month = get("H1 mean net return", "entry_month", "economic_event").iloc[0]
    ev_iid = get("H1 mean net return", "iid", "economic_event").iloc[0]

    lines = [
        "# Final Statistical Report — ICAIF paper (2026-07-14 run)",
        "",
        "All bootstraps: 20,000 replications, fixed seeds (42 + documented offsets).",
        "Two p-value conventions appear and are never mixed: (a) null-centered one-sided",
        "bootstrap p = (#null-draws >= observed + 1)/(B + 1); (b) one-sided t-tests as an",
        "iid reference only. Every CI is an ordinary uncentered percentile bootstrap",
        "interval of the mean unless marked as an IQR.",
        "",
        "## H1",
        "",
        f"**Is the raw candidate mean positive?** Yes: +1.208% mean, +0.188% median,"
        f" 51.97% positive over N=887 candidates (750 economic events, 387 symbols,"
        f" 46 entry weeks, 16 entry months).",
        "",
        "**Under which dependence assumptions does its interval exclude zero?** Only under",
        f"economic-event clustering: CI [{pct(cand_event['ci_lo'])}, {pct(cand_event['ci_hi'])}], p={cand_event['p_value']:.4f}.",
        f"Symbol clustering (CI [{pct(cand_sym['ci_lo'])}, {pct(cand_sym['ci_hi'])}], p={cand_sym['p_value']:.4f}),",
        f"entry-week blocks (p={get('H1 mean net return','entry_week','candidate_observation').iloc[0]['p_value']:.4f}) and",
        f"entry-month blocks (CI [{pct(cand_month['ci_lo'])}, {pct(cand_month['ci_hi'])}], p={cand_month['p_value']:.4f}) all cross zero.",
        "",
        f"**Does the equal-event estimate remain positive?** The point estimate is positive but small:",
        f"mean {pct(ev_iid['estimate'])}, median {pct(ev_iid['median'])} over N=750 events; its month-block interval",
        f"[{pct(ev_month['ci_lo'])}, {pct(ev_month['ci_hi'])}] crosses zero (p={ev_month['p_value']:.4f}).",
        "",
        "**How dependent is the result on the upper tail?** Strongly. Dropping the top 1% of",
        "candidates lowers the mean to +0.662%; dropping the top 5% turns it negative (-0.260%).",
        "At event level the top 1% removal leaves +0.029% (p=0.452). Symmetric trims are milder",
        "(candidate 5%: +0.584%; event 5%: +0.070%). The top 5% of candidates carry ~120% of the",
        "total return (the remainder loses money in aggregate).",
        "",
        "**Which event families drive the result?** Geopolitical: mean +4.794%, median +2.443%,",
        "61.6% positive (N=172), event-clustered CI excludes zero. Earnings are centered at zero",
        "(mean +0.020%, N=692, CI crosses zero). 'Other' is +10.118% on N=23 with a very wide CI.",
        "",
        "**Does the result survive matched SPY and sector controls?** Not at the 95% level.",
        "The SPY candidate/event-cluster excess is +0.914% with CI crossing zero (p=0.0508);",
        "all other SPY and sector-ETF schemes have p >= 0.11. The sector-ETF candidate excess",
        "is +0.191% (p=0.264). NO 95% interval excludes zero in the benchmark-relative analysis.",
        "The sector comparison covers the 680 sector-mapped (single-stock) rows; ETF-mapped",
        "geo/macro candidates have no sector assignment and are excluded rather than proxied.",
        "",
        "**2026 subsample (point-in-time eligibility, online refits):** N=528 entries in",
        f"Jan-Jun 2026, mean {pct(get('2026 subsample','economic_event_id').iloc[0]['estimate'])},"
        f" median {pct(get('2026 subsample','economic_event_id').iloc[0]['median'])};"
        f" event-cluster p={get('2026 subsample','economic_event_id').iloc[0]['p_value']:.4f},"
        f" month-block p={get('2026 subsample','entry_month').iloc[0]['p_value']:.4f}.",
        "Eligibility parameters for these observations were fitted only on outcomes completed",
        "before each observation's walk-forward fold started, but folds 4-5 were refit inside",
        "the test period, and the period has been repeatedly inspected during research —",
        "descriptive evidence, not a confirmatory test.",
        "",
        "**Provenance caveat (applies to every full-sample H1 row):** the eligibility",
        "parameters are CEM-derived (T1+T2+T3+T4/SPY walk-forward schedule) and pre-fold-1",
        "candidates use a policy fitted on later data. The full-sample estimate is therefore",
        "descriptive, not an independent confirmatory hypothesis test.",
        "",
        "## CEM",
        "",
    ]

    if budget_path.exists():
        budget = pd.read_csv(budget_path)
        lines += [
            "**Are test returns and excess returns stable across seeds?**",
            "",
            budget.round(2).to_string(index=False),
            "",
            "**Budget sensitivity:** medians move by a few points across the four budgets and",
            "IQRs overlap heavily; no budget is selected or ranked by test performance.",
            "",
        ]
    if paired_path.exists():
        paired = pd.read_csv(paired_path)
        lines += [
            "**Does All Treatments consistently improve on Baseline at equal seed/budget (6x20)?**",
            "",
            paired.round(2).to_string(index=False),
            "",
        ]

    block_spy = block[(block["benchmark"] == "SPY") & (block["universe"] == "audit_clean")].iloc[0]
    block_qqq = block[(block["benchmark"] == "QQQ") & (block["universe"] == "audit_clean")].iloc[0]
    lines += [
        "**Is the evidence sufficient for a profitability claim?** No. It supports historical",
        "robustness only. The seed-42 flagship daily excess is nominally positive on SPY",
        f"(block-bootstrap p={block_spy['boot_p_one_sided_null_centered']:.4f}, HAC p={block_spy['hac_p_one_sided']:.4f})",
        f"but not on QQQ (p={block_qqq['boot_p_one_sided_null_centered']:.4f}); the test window is a single,",
        "short (111 trading days), repeatedly inspected period; and the walk-forward arms refit",
        "policies inside that window. The cleaning delta (audit-clean vs original universe) is",
        f"directionally positive but not significant (SPY p={delta[delta['benchmark']=='SPY'].iloc[0]['boot_p_one_sided_null_centered']:.4f},"
        f" QQQ p={delta[delta['benchmark']=='QQQ'].iloc[0]['boot_p_one_sided_null_centered']:.4f}).",
        "No portfolio-level statistical-significance claim is made in the paper.",
        "",
        "## Sources",
        "",
        "Every row of final_statistical_results.csv names its source CSV; computed rows name",
        "the trade file and the computation (20k null-centered cluster bootstrap, seeds 142-143,",
        "242-243 for families and the 2026 subsample as recorded in build_final_stats.py).",
    ]
    (OUT / "final_statistical_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("wrote final_statistical_report.md")


if __name__ == "__main__":
    main()
