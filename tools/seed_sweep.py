"""Run the C0 DD_PENALTY sweep without selecting on OOS results."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_LAMBDAS = [0.003, 0.01, 0.03, 0.10, 0.30]


def _lambda_slug(value: float) -> str:
    return f"{value:g}".replace(".", "p")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sweep DD_PENALTY lambdas. Selection uses cem_objective only "
            "(train OOF for online-refit arms); OOS metrics are reported as a frontier."
        )
    )
    parser.add_argument("--lambdas", nargs="+", type=float, default=DEFAULT_LAMBDAS)
    parser.add_argument("--seeds", nargs="+", type=int, default=[1337])
    parser.add_argument("--run-prefix", default="c0_dd_sweep")
    parser.add_argument("--experiments", nargs="+")
    parser.add_argument("--benchmarks", nargs="+")
    parser.add_argument("--no-allocation-log", action="store_true")
    return parser.parse_args(argv)


def _run_one(args: argparse.Namespace, lam: float, seed: int) -> pd.DataFrame:
    run_id = f"{args.run_prefix}_lambda_{_lambda_slug(lam)}_seed_{seed}"
    cmd = [
        sys.executable,
        str(PROJECT / "optimize_cem.py"),
        "--dd-penalty",
        f"{lam:g}",
        "--seed",
        str(seed),
        "--run-id",
        run_id,
    ]
    if args.experiments:
        cmd.extend(["--experiments", *args.experiments])
    if args.benchmarks:
        cmd.extend(["--benchmarks", *[b.upper() for b in args.benchmarks]])
    if args.no_allocation_log:
        cmd.append("--no-allocation-log")

    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=PROJECT, check=True)

    results_path = PROJECT / "runs" / run_id / "experiment_results_clean.csv"
    df = pd.read_csv(results_path)
    df.insert(0, "run_id", run_id)
    df.insert(1, "seed", seed)
    df["dd_penalty"] = lam
    return df


def _write_frontier(rows: pd.DataFrame, run_prefix: str) -> None:
    out_dir = PROJECT / "runs" / run_prefix
    out_dir.mkdir(parents=True, exist_ok=True)

    keep = [
        "seed",
        "dd_penalty",
        "experiment",
        "benchmark",
        "cem_objective_scope",
        "cem_objective",
        "test_sharpe",
        "test_max_dd_pct",
        "test_return_pct",
        "test_excess_return_pct",
        "test_trades",
        "policy_scope",
        "run_id",
    ]
    keep = [col for col in keep if col in rows.columns]
    frontier = rows[keep].sort_values(
        ["seed", "experiment", "benchmark", "dd_penalty"],
        kind="mergesort",
    )
    frontier_path = out_dir / "dd_penalty_oos_frontier.csv"
    frontier.to_csv(frontier_path, index=False)

    selected = (
        rows.sort_values(
            ["seed", "experiment", "benchmark", "cem_objective"],
            ascending=[True, True, True, False],
            kind="mergesort",
        )
        .groupby(["seed", "experiment", "benchmark"], as_index=False)
        .head(1)
    )
    selected_path = out_dir / "dd_penalty_selected_by_train_objective.csv"
    selected[keep].to_csv(selected_path, index=False)

    print(f"wrote OOS frontier: {frontier_path}", flush=True)
    print(f"wrote train-objective selection: {selected_path}", flush=True)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    frames = [
        _run_one(args, lam, seed)
        for seed in args.seeds
        for lam in args.lambdas
    ]
    rows = pd.concat(frames, ignore_index=True)
    _write_frontier(rows, args.run_prefix)


if __name__ == "__main__":
    main()
