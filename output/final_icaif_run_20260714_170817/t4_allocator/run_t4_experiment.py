"""T4 allocator experiment driver.

Phase 1 (sequential): deterministic arms (t4, fifo) for every seed 42-51 and
benchmark, with allocation/disposition logs and trade logs saved for the
contested-decision analysis.

Phase 2 (process pool): 1,000 seeded random-allocator replications per
(seed, benchmark) - full chronological simulations.

Outputs:
  t4_allocator_seed_results.csv        deterministic arm rows
  t4_random_allocator_distribution.csv one row per random replication
  det_logs/{seed}_{bench}_{arm}_trades.csv / _alloc.csv
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SEEDS = list(range(42, 52))
BENCHES = ("SPY", "QQQ")
N_REPS = 1000
CHUNK = 250
MAX_WORKERS = 16


def det_arms() -> None:
    import t4_alloc_lib as lib
    (HERE / "det_logs").mkdir(exist_ok=True)
    rows = []
    for seed in SEEDS:
        for bench in BENCHES:
            for arm in ("t4", "fifo"):
                row, trades, alloc, disp = lib.run_arm(seed, bench, arm, collect_logs=True)
                rows.append(row)
                trades.to_csv(HERE / "det_logs" / f"{seed}_{bench}_{arm}_trades.csv", index=False)
                if alloc is not None and not alloc.empty:
                    alloc.to_csv(HERE / "det_logs" / f"{seed}_{bench}_{arm}_alloc.csv", index=False)
            print(f"det {seed} {bench} done", flush=True)
    pd.DataFrame(rows).to_csv(HERE / "t4_allocator_seed_results.csv", index=False)


def random_chunk(args: tuple) -> list[dict]:
    seed, bench, rep_lo, rep_hi = args
    import t4_alloc_lib as lib
    out = []
    for rep in range(rep_lo, rep_hi):
        out.append(lib.run_arm(seed, bench, "random", rep=rep))
    return out


def random_phase() -> None:
    tasks = []
    for seed in SEEDS:
        for bench in BENCHES:
            for lo in range(0, N_REPS, CHUNK):
                tasks.append((seed, bench, lo, min(lo + CHUNK, N_REPS)))
    rows: list[dict] = []
    started = time.time()
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(random_chunk, t) for t in tasks]
        for i, future in enumerate(as_completed(futures), 1):
            rows.extend(future.result())
            if i % 8 == 0 or i == len(tasks):
                elapsed = (time.time() - started) / 60
                print(f"[{i}/{len(tasks)}] chunks done, {len(rows)} reps, {elapsed:.1f} min", flush=True)
    pd.DataFrame(rows).to_csv(HERE / "t4_random_allocator_distribution.csv", index=False)


if __name__ == "__main__":
    det_arms()
    random_phase()
    print("EXPERIMENT COMPLETE")
