"""Contested-decision accounting and retrospective allocator diagnostics.

RETROSPECTIVE ONLY: realized candidate returns are used to grade past
allocation decisions; none of this information was available to the policy.

Definitions (per seed and benchmark, from the deterministic arm logs):
  participant       a candidate reaching the allocator on a date (selected or
                    skipped row in the allocation log).
  contested date    a date with at least one capacity/duplicate/collapse skip
                    or a preemption in EITHER the T4 or the FIFO arm, or where
                    the two arms admitted different candidate sets.
  identity change   a (market_id, symbol) admitted by exactly one of the arms
                    over the whole test period.

Candidate outcome measure: the standalone kernel trade return (return_pct, the
close-to-exit percentage return of the candidate's own trade path under the
same fitted schedule) minus round-trip modeled costs approximated at 15 bp.
Portfolio-level P&L differs (sizing, compounding); this measure grades WHICH
candidate was picked, not how much was bet.

Outputs: t4_contested_decisions.csv (one row per contested date) and printed
aggregates with date- and event-clustered bootstrap CIs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import t4_alloc_lib as lib
from sim_lib import load_universe

SEEDS = list(range(42, 52))
BENCHES = ("SPY", "QQQ")
COST_BP = 0.0015  # approximate round-trip cost drag on a standalone trade
N_BOOT = 10_000

CONTEST_REASONS = {"max_concurrent", "duplicate_symbol", "same_day_symbol_collapsed"}


def standalone_returns(seed: int, bench: str) -> dict[tuple, float]:
    """(market_id, symbol) -> standalone net-ish return of the generated trade."""
    from core.kernel import simulate_one
    from backtesting.optimize_cem import truncate_paths, as_utc_day

    df, prices, probs, oos_start, oos_end = load_universe()
    oos_df = df[(df["t_theta"] >= oos_start) & (df["t_theta"] <= oos_end)]
    schedule = lib.load_schedule(seed, bench)
    sim_prices, sim_probs = truncate_paths(prices, probs, oos_end)
    out: dict[tuple, float] = {}
    for _, row in oos_df.iterrows():
        policy = schedule(as_utc_day(row["t_theta"]))
        trade = simulate_one(row, sim_prices, sim_probs, policy)
        if trade is None:
            continue
        out[(str(row["market_id"]), str(row["symbol"]))] = trade["return_pct"] / 100.0 - COST_BP
    return out


def cluster_ci(values: np.ndarray, labels: np.ndarray, seed: int = 42) -> tuple[float, float, float]:
    """Mean + cluster-bootstrap 95% CI (clusters resampled with replacement)."""
    values = np.asarray(values, float)
    labels = np.asarray(labels).astype(str)
    ok = np.isfinite(values)
    values, labels = values[ok], labels[ok]
    unique, inverse = np.unique(labels, return_inverse=True)
    if len(values) < 2 or len(unique) < 2:
        return float(np.mean(values)) if len(values) else np.nan, np.nan, np.nan
    sums = np.bincount(inverse, weights=values)
    sizes = np.bincount(inverse).astype(float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(unique), size=(N_BOOT, len(unique)))
    means = sums[draws].sum(axis=1) / sizes[draws].sum(axis=1)
    return float(np.mean(values)), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def admitted_set(trades: pd.DataFrame) -> set[tuple]:
    return {(str(m), str(s)) for m, s in zip(trades["market_id"], trades["symbol"])}


def main() -> None:
    df, *_ = load_universe()
    event_map = {(str(m), str(s)): str(e) for m, s, e in
                 zip(df["market_id"], df["symbol"], df.get("economic_event_id", df["market_id"]))}

    date_rows: list[dict] = []
    summary_rows: list[dict] = []
    for seed in SEEDS:
        for bench in BENCHES:
            ret = standalone_returns(seed, bench)
            t4_alloc = pd.read_csv(HERE / "det_logs" / f"{seed}_{bench}_t4_alloc.csv")
            fifo_alloc = pd.read_csv(HERE / "det_logs" / f"{seed}_{bench}_fifo_alloc.csv")
            t4_trades = pd.read_csv(HERE / "det_logs" / f"{seed}_{bench}_t4_trades.csv")
            fifo_trades = pd.read_csv(HERE / "det_logs" / f"{seed}_{bench}_fifo_trades.csv")

            t4_admit_by_date = {d: {(str(r["market_id"]), str(r["symbol"])) for _, r in g.iterrows()}
                                for d, g in t4_alloc[t4_alloc["decision"] == "selected"].groupby("date")}
            fifo_admit_by_date = {d: {(str(r["market_id"]), str(r["symbol"])) for _, r in g.iterrows()}
                                  for d, g in fifo_alloc[fifo_alloc["decision"] == "selected"].groupby("date")}

            def skips(frame, d):
                g = frame[(frame["date"] == d) & (frame["decision"] == "skipped")]
                return g[g["skip_reason"].isin(CONTEST_REASONS)]

            all_dates = sorted(set(t4_alloc["date"]) | set(fifo_alloc["date"]))
            n_competitions = 0
            for d in all_dates:
                t4_sel = t4_admit_by_date.get(d, set())
                fifo_sel = fifo_admit_by_date.get(d, set())
                t4_skips = skips(t4_alloc, d)
                fifo_skips = skips(fifo_alloc, d)
                participants = t4_sel | fifo_sel | {
                    (str(r["market_id"]), str(r["symbol"])) for _, r in
                    pd.concat([t4_skips, fifo_skips]).iterrows()}
                contested = bool(len(t4_skips) or len(fifo_skips) or (t4_sel != fifo_sel))
                if not contested:
                    continue
                n_competitions += len(participants)
                part_returns = {p: ret.get(p) for p in participants}
                valid = {p: v for p, v in part_returns.items() if v is not None}
                t4_vals = [valid[p] for p in t4_sel if p in valid]
                fifo_vals = [valid[p] for p in fifo_sel if p in valid]
                rejected_t4 = [valid[p] for p in valid if p not in t4_sel]
                best = max(valid.values()) if valid else np.nan
                t4_has_best = bool(t4_vals) and np.isclose(max(t4_vals), best)
                ranks = pd.Series({p: v for p, v in valid.items()}).rank(ascending=False)
                t4_rank = float(np.mean([ranks[p] for p in t4_sel if p in ranks.index])) if t4_vals else np.nan
                date_rows.append({
                    "seed": seed, "benchmark": bench, "date": d,
                    "n_participants": len(participants),
                    "n_valid_returns": len(valid),
                    "n_selected_t4": len(t4_sel), "n_selected_fifo": len(fifo_sel),
                    "identity_diff_count": len(t4_sel ^ fifo_sel),
                    "t4_selected_mean_ret": float(np.mean(t4_vals)) if t4_vals else np.nan,
                    "fifo_selected_mean_ret": float(np.mean(fifo_vals)) if fifo_vals else np.nan,
                    "batch_mean_ret": float(np.mean(list(valid.values()))) if valid else np.nan,
                    "t4_minus_rejected": (float(np.mean(t4_vals)) - float(np.mean(rejected_t4)))
                        if t4_vals and rejected_t4 else np.nan,
                    "t4_selected_includes_best": t4_has_best,
                    "t4_mean_rank": t4_rank,
                    "event_ids": "|".join(sorted({event_map.get(p, p[0]) for p in participants})),
                })

            t4_set = admitted_set(t4_trades)
            fifo_set = admitted_set(fifo_trades)
            res = pd.read_csv(HERE / "t4_allocator_seed_results.csv")
            r_t4 = res[(res["seed"] == seed) & (res["benchmark"] == bench) & (res["arm"] == "t4")].iloc[0]
            summary_rows.append({
                "seed": seed, "benchmark": bench,
                "contested_dates": sum(1 for r in date_rows if r["seed"] == seed and r["benchmark"] == bench),
                "candidate_competitions": n_competitions,
                "positions_identity_changed": len(t4_set ^ fifo_set),
                "t4_only_positions": len(t4_set - fifo_set),
                "fifo_only_positions": len(fifo_set - t4_set),
                "preemptions": int(r_t4["preemptions"]),
                "same_symbol_collapses": int(r_t4["skip_same_day_symbol_collapsed"]),
                "capacity_skips_t4": int(r_t4["skip_max_concurrent"]),
            })
            print(f"{seed} {bench}: contested done", flush=True)

    contested = pd.DataFrame(date_rows)
    contested.to_csv(HERE / "t4_contested_decisions.csv", index=False)
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(HERE / "t4_contested_summary.csv", index=False)
    print(summary.to_string(index=False))

    # Aggregate retrospective quality with dependence-aware CIs.
    agg_rows = []
    for bench in BENCHES:
        g = contested[contested["benchmark"] == bench]
        diff = (g["t4_selected_mean_ret"] - g["fifo_selected_mean_ret"]).to_numpy(float)
        date_labels = (g["seed"].astype(str) + ":" + g["date"].astype(str)).to_numpy()
        event_labels = (g["seed"].astype(str) + ":" + g["event_ids"].astype(str)).to_numpy()
        m1, lo1, hi1 = cluster_ci(diff, date_labels)
        m2, lo2, hi2 = cluster_ci(diff, event_labels, seed=43)
        sel = g["t4_selected_mean_ret"].to_numpy(float)
        m3, lo3, hi3 = cluster_ci(g["t4_minus_rejected"].to_numpy(float), date_labels, seed=44)
        agg_rows.append({
            "benchmark": bench,
            "n_contested_dates": int(len(g)),
            "t4_selected_mean": float(np.nanmean(sel)),
            "t4_selected_median": float(np.nanmedian(sel)),
            "t4_minus_fifo_mean": m1, "t4_minus_fifo_ci_date": f"[{lo1:+.4f},{hi1:+.4f}]",
            "t4_minus_fifo_ci_event": f"[{lo2:+.4f},{hi2:+.4f}]",
            "t4_minus_rejected_mean": m3, "t4_minus_rejected_ci_date": f"[{lo3:+.4f},{hi3:+.4f}]",
            "frac_t4_includes_best": float(np.nanmean(g["t4_selected_includes_best"].astype(float))),
            "t4_mean_rank": float(np.nanmean(g["t4_mean_rank"])),
            "mean_participants": float(g["n_participants"].mean()),
        })
    agg = pd.DataFrame(agg_rows)
    agg.to_csv(HERE / "t4_contested_aggregates.csv", index=False)
    print(agg.to_string(index=False))


if __name__ == "__main__":
    main()
