"""Seed sweep of the treatment ladder at 6x20 on the audit-clean universe.

Runs, for seeds 42..51: Baseline, T2-only, T1+T2, T1+T2+T3 (SPY+QQQ each) into
runs/icaif_ladder_seed{S}. T1+T2+T3+T4 seed-level results already exist in the
final bundle's robustness run-level CSV and are not rerun.
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
OUT = Path(__file__).resolve().parent
LOG_DIR = OUT / "logs"
STATUS = OUT / "ladder_status.json"
SEEDS = list(range(42, 52))
MAX_WORKERS = 5


def is_complete(run_id: str) -> bool:
    path = ROOT / "runs" / run_id / "experiment_results_clean.csv"
    if not path.exists() or path.stat().st_size < 100:
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    return all(k in text for k in ("Baseline", "T2 TrainWindows", "T1+T2+T3", "QQQ"))


def run_seed(seed: int) -> dict:
    run_id = f"icaif_ladder_seed{seed}"
    if is_complete(run_id):
        return {"run_id": run_id, "status": "already_complete"}
    cmd = [
        sys.executable, "-m", "backtesting.optimize_cem",
        "--experiments", "baseline", "t2_trainwindows", "t1_t2", "t1_t2_t3",
        "--benchmarks", "SPY", "QQQ",
        "--seed", str(seed),
        "--cem-iters", "6", "--cem-pop", "20",
        "--run-id", run_id,
        "--no-allocation-log", "--no-forensics-log",
    ]
    started = time.time()
    with (LOG_DIR / f"{run_id}.log").open("w", encoding="utf-8") as log:
        log.write("COMMAND: " + " ".join(cmd) + "\n\n")
        log.flush()
        completed = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False)
    return {
        "run_id": run_id,
        "returncode": completed.returncode,
        "status": "completed" if completed.returncode == 0 else "failed",
        "elapsed_sec": round(time.time() - started, 1),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(run_seed, s) for s in SEEDS]
        for future in as_completed(futures):
            row = future.result()
            results.append(row)
            STATUS.write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(f"[{len(results)}/{len(SEEDS)}] {row['run_id']}: {row['status']}", flush=True)
    failed = [r for r in results if r.get("status") == "failed"]
    print("DONE.", len(failed), "failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
