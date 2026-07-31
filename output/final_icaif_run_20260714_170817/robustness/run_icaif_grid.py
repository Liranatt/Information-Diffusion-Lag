"""Parallel driver for the final ICAIF robustness grid on the audit-clean universe.

Runs backtesting.optimize_cem cells into isolated runs/<run-id>/ namespaces:
  - t1_t2_t3_t4 x budgets {6x20, 6x30, 10x20, 10x30} x seeds 42..51  (40 cells)
  - baseline    x 6x20                              x seeds 42..51  (10 cells)

Every cell runs both SPY and QQQ benchmarks. The default --candidates-path of
the current optimize_cem.py is data/candidates_audit_clean.parquet, which is
exactly the universe this paper uses; nothing is passed so the default applies.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r"C:\Users\Liran\PycharmProjects\cem_clean_repo")
RUNS_DIR = ROOT / "runs"
OUT_DIR = Path(__file__).resolve().parent
LOG_DIR = OUT_DIR / "grid_logs"
STATUS_PATH = OUT_DIR / "icaif_grid_status.json"

SEEDS = list(range(42, 52))
BUDGETS = [(6, 20), (6, 30), (10, 20), (10, 30)]
MAX_WORKERS = 6

CELLS: list[dict] = []
for iters, pop in BUDGETS:
    for seed in SEEDS:
        CELLS.append({
            "run_id": f"icaif_grid_{iters}x{pop}_seed{seed}",
            "experiment": "t1_t2_t3_t4",
            "iters": iters, "pop": pop, "seed": seed,
        })
for seed in SEEDS:
    CELLS.append({
        "run_id": f"icaif_base_6x20_seed{seed}",
        "experiment": "baseline",
        "iters": 6, "pop": 20, "seed": seed,
    })


def is_complete(run_id: str, experiment_label: str) -> bool:
    path = RUNS_DIR / run_id / "experiment_results_clean.csv"
    if not path.exists() or path.stat().st_size < 100:
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    return "SPY" in text and "QQQ" in text and experiment_label in text


def run_cell(cell: dict) -> dict:
    label = "T1+T2+T3+T4" if cell["experiment"] == "t1_t2_t3_t4" else "Baseline"
    if is_complete(cell["run_id"], label):
        return {**cell, "status": "already_complete"}
    cmd = [
        sys.executable, "-m", "backtesting.optimize_cem",
        "--experiments", cell["experiment"],
        "--benchmarks", "SPY", "QQQ",
        "--seed", str(cell["seed"]),
        "--cem-iters", str(cell["iters"]),
        "--cem-pop", str(cell["pop"]),
        "--run-id", cell["run_id"],
        "--no-allocation-log", "--no-forensics-log",
    ]
    log_path = LOG_DIR / f"{cell['run_id']}.log"
    started = time.time()
    with log_path.open("w", encoding="utf-8") as log:
        log.write("COMMAND: " + " ".join(cmd) + "\n\n")
        log.flush()
        completed = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False)
    return {
        **cell,
        "returncode": completed.returncode,
        "status": "completed" if completed.returncode == 0 else "failed",
        "elapsed_sec": round(time.time() - started, 1),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(run_cell, cell): cell for cell in CELLS}
        for future in as_completed(futures):
            row = future.result()
            results.append(row)
            STATUS_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(f"[{len(results)}/{len(CELLS)}] {row['run_id']}: {row['status']}", flush=True)
    failed = [r for r in results if r.get("status") == "failed"]
    print(f"DONE. {len(results)} cells, {len(failed)} failed.")
    if failed:
        for r in failed:
            print("FAILED:", r["run_id"])
        sys.exit(1)


if __name__ == "__main__":
    main()
