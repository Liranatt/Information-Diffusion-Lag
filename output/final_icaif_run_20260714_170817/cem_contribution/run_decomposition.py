"""Selection-vs-execution decomposition + simple non-optimized reference.

Arms (all on the identical Jan-Jun 2026 test window, FIFO, no Kelly):

  ALL_ELIGIBLE_SIMPLE_EXEC  permissive gates (every eligible candidate enters at
                            its first >=0.55 point; no surge/run-up caps) +
                            repository-default execution/sizing/capacity.
  SIMPLE_DEFAULT            the pre-specified repository default policy
                            (core/policy.py DEFAULT_POLICY + 10% size, 10 slots).
                            This is the non-optimized reference of Section 2.
  CEM_SEL_SIMPLE_EXEC       CEM-baseline selection params (enter_strong,
                            enter_floor, hold_days, surge cap, run-up cap) +
                            default execution (ATR, lock, theta_out, 10%, 10).
  SIMPLE_SEL_CEM_EXEC       default selection params + CEM-baseline execution.
  CEM_FULL                  the frozen CEM baseline policy (all ten params).

CEM-dependent arms are repeated for every seed 42-51 using that seed's frozen
baseline policy from runs/icaif_ladder_seed{S} (seed-42 policy cross-checked
against runs/icaif_base_vs_all_seed42). Deterministic arms appear once.

Output: cem_decomposition_results.csv (+ printed summary).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from sim_lib import (
    ROOT,
    SIMPLE_POLICY,
    hybrid_policy,
    run_test,
    stat_row,
)

OUT = Path(__file__).resolve().parent
SEEDS = list(range(42, 52))

PERMISSIVE_SELECTION = {
    "enter_strong": 0.55,
    "enter_floor": 0.55,
    "hold_days": 1,
    "max_prob_surge": 999.0,
    "max_price_runup": 999.0,
}


def baseline_policy(seed: int, bench: str) -> dict:
    run_dir = ROOT / "runs" / f"icaif_ladder_seed{seed}"
    results = pd.read_csv(run_dir / "experiment_results_clean.csv")
    row = results[(results["experiment"] == "Baseline") & (results["benchmark"] == bench)].iloc[0]
    return json.loads(row["policy_snapshot_json"])


def main() -> None:
    rows: list[dict] = []

    # Deterministic arms (no CEM, no seed).
    for bench in ("SPY", "QQQ"):
        for label, policy in (
            ("ALL_ELIGIBLE_SIMPLE_EXEC", hybrid_policy(PERMISSIVE_SELECTION, SIMPLE_POLICY)),
            ("SIMPLE_DEFAULT", dict(SIMPLE_POLICY)),
        ):
            trades, equity, stats = run_test(policy, bench)
            rows.append(stat_row(label, bench, stats, equity, {"seed": "", "policy_json": json.dumps(policy, sort_keys=True)}))
            print(f"{label} {bench}: ret {stats['total_return']:+.2f} excess {stats['excess_return']:+.2f} trades {stats['n_trades']}", flush=True)

    # Seed-dependent arms.
    for seed in SEEDS:
        for bench in ("SPY", "QQQ"):
            cem = baseline_policy(seed, bench)
            arms = (
                ("CEM_SEL_SIMPLE_EXEC", hybrid_policy(cem, SIMPLE_POLICY)),
                ("SIMPLE_SEL_CEM_EXEC", hybrid_policy(SIMPLE_POLICY, cem)),
                ("CEM_FULL", dict(cem)),
            )
            for label, policy in arms:
                trades, equity, stats = run_test(policy, bench)
                rows.append(stat_row(label, bench, stats, equity, {"seed": seed, "policy_json": json.dumps(policy, sort_keys=True)}))
            print(f"seed {seed} {bench} done", flush=True)

    frame = pd.DataFrame(rows)
    frame.to_csv(OUT / "cem_decomposition_results.csv", index=False)

    # Cross-check: seed-42 CEM_FULL must equal the canonical run CSV.
    check = pd.read_csv(ROOT / "runs" / "icaif_base_vs_all_seed42" / "experiment_results_clean.csv")
    for bench in ("SPY", "QQQ"):
        canonical = float(check[(check["experiment"] == "Baseline") & (check["benchmark"] == bench)]["test_return_pct"].iloc[0])
        mine = float(frame[(frame["arm"] == "CEM_FULL") & (frame["benchmark"] == bench) & (frame["seed"] == 42)]["test_return_pct"].iloc[0])
        assert abs(canonical - mine) < 1e-4, (bench, canonical, mine)
    print("seed-42 CEM_FULL cross-check passed")

    summary = (
        frame.groupby(["arm", "benchmark"])["test_excess_return_pct"]
        .agg(["count", "median", lambda s: s.quantile(0.25), lambda s: s.quantile(0.75), "min", "max"])
        .rename(columns={"<lambda_0>": "q25", "<lambda_1>": "q75"})
    )
    print(summary.to_string())


if __name__ == "__main__":
    main()
