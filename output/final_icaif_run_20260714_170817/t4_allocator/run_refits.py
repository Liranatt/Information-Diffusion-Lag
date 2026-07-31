"""Refit T1+T2+T3+T4 for seeds 43-51 (seed 42 exists in icaif_base_vs_all_seed42).

Deterministic reproduction of the deleted grid runs; verified afterwards against
the preserved robustness run-level aggregates.
"""
from __future__ import annotations

import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(r"C:\Users\Liran\PycharmProjects\cem_clean_repo")
LOG = Path(__file__).resolve().parent / "logs"
SEEDS = list(range(43, 52))


def run_seed(seed: int) -> tuple[int, int]:
    run_id = f"icaif_t4x_seed{seed}"
    out = ROOT / "runs" / run_id / "experiment_results_clean.csv"
    if out.exists() and out.stat().st_size > 100:
        return seed, 0
    cmd = [sys.executable, "-m", "backtesting.optimize_cem",
           "--experiments", "t1_t2_t3_t4", "--benchmarks", "SPY", "QQQ",
           "--seed", str(seed), "--cem-iters", "6", "--cem-pop", "20",
           "--run-id", run_id, "--no-allocation-log", "--no-forensics-log"]
    with (LOG / f"{run_id}.log").open("w", encoding="utf-8") as log:
        completed = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False)
    return seed, completed.returncode


def main() -> None:
    LOG.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with ThreadPoolExecutor(max_workers=5) as pool:
        for future in as_completed([pool.submit(run_seed, s) for s in SEEDS]):
            seed, rc = future.result()
            print(f"seed {seed}: rc={rc}", flush=True)
    print(f"done in {(time.time() - started) / 60:.1f} min")


if __name__ == "__main__":
    main()
