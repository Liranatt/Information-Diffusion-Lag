from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "runs"
LOG_DIR = ROOT / "analysis" / "output" / "robustness_grid_logs"
STATUS_PATH = ROOT / "analysis" / "output" / "robustness_grid_status.json"

SEEDS = list(range(42, 52))
BUDGETS = [(6, 20), (10, 20), (6, 30), (10, 30)]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def result_path(run_id: str) -> Path:
    return RUNS_DIR / run_id / "experiment_results_clean.csv"


def is_complete(run_id: str) -> bool:
    path = result_path(run_id)
    if not path.exists() or path.stat().st_size < 100:
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    return "SPY" in text and "QQQ" in text and "T1+T2+T3+T4" in text


def write_status(rows: list[dict]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def main() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    statuses: list[dict] = []
    write_status(statuses)

    for iters, pop in BUDGETS:
        for seed in SEEDS:
            run_id = f"robustness_grid_{iters}x{pop}_seed{seed}"
            if is_complete(run_id):
                statuses.append({"run_id": run_id, "status": "already_complete", "finished_at": now()})
                write_status(statuses)
                continue

            log_path = LOG_DIR / f"{run_id}.log"
            cmd = [
                sys.executable,
                "-m",
                "backtesting.optimize_cem",
                "--experiments",
                "t1_t2_t3_t4",
                "--benchmarks",
                "SPY",
                "QQQ",
                "--seed",
                str(seed),
                "--cem-iters",
                str(iters),
                "--cem-pop",
                str(pop),
                "--run-id",
                run_id,
                "--no-allocation-log",
                "--no-forensics-log",
            ]
            started = time.time()
            row = {"run_id": run_id, "seed": seed, "cem_iters": iters, "cem_pop": pop, "status": "running", "started_at": now()}
            statuses.append(row)
            write_status(statuses)
            with log_path.open("w", encoding="utf-8") as log:
                log.write("COMMAND: " + " ".join(cmd) + "\n\n")
                log.flush()
                completed = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False)
            row.update({
                "returncode": completed.returncode,
                "status": "completed" if completed.returncode == 0 else "failed",
                "elapsed_sec": round(time.time() - started, 1),
                "finished_at": now(),
            })
            write_status(statuses)


if __name__ == "__main__":
    main()
