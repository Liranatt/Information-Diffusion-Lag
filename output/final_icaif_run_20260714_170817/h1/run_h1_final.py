"""Final ICAIF H1 driver.

Runs the canonical H1 candidate-generation script + the full expectation
protocol at 20,000 bootstrap replications, seed 42, on the audit-clean
artifacts, writing into the isolated final output directory. Also runs the
raw-YES polarity ablation into a sibling directory.

The walk-forward fold CSV consumed by the H1 eligibility provider is the one
produced by this run's fresh audit-clean CEM matrix
(runs/final_icaif_matrix_seed42_6x20). Before running, this driver copies it
over data/experiment_walkforward_folds_clean.csv (the module-level path the
runner and the protocol manifest both read), after backing up the previous
occupant (a legacy-universe control run) into this directory.

No repo code is modified; only module-level constants are set and existing
entry points are called.
"""
from __future__ import annotations

import functools
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\Liran\PycharmProjects\cem_clean_repo")
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FRESH_FOLDS = ROOT / "runs" / "final_icaif_matrix_seed42_6x20" / "experiment_walkforward_folds_clean.csv"
INPLACE_FOLDS = ROOT / "data" / "experiment_walkforward_folds_clean.csv"
BACKUP = OUT / "backup_legacy_experiment_walkforward_folds_clean.csv"

N_BOOT = 20_000
SEED = 42


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    assert FRESH_FOLDS.exists(), f"missing {FRESH_FOLDS} — run the matrix first"
    if not BACKUP.exists():
        shutil.copy2(INPLACE_FOLDS, BACKUP)
        print(f"backed up legacy fold CSV -> {BACKUP}")
    if sha256(FRESH_FOLDS) != sha256(INPLACE_FOLDS):
        shutil.copy2(FRESH_FOLDS, INPLACE_FOLDS)
        print("installed fresh audit-clean fold CSV into data/")
    else:
        print("data/ fold CSV already matches the fresh audit-clean matrix")
    print("folds sha256:", sha256(INPLACE_FOLDS))

    import diagnostics.run_raw_expectation_test_tminus1 as runner
    import analysis.h1_expectation_protocol as protocol

    runner.N_BOOTSTRAP = N_BOOT
    runner.run_expectation_protocol = functools.partial(
        protocol.run_expectation_protocol, n_boot=N_BOOT, seed=SEED
    )

    candidates = str(ROOT / "data" / "candidates_audit_clean.parquet")

    print("\n===== PRIMARY (resolved polarity) =====", flush=True)
    runner.main([
        "--candidates-path", candidates,
        "--output-dir", str(OUT / "raw_expectation_tminus1_final"),
        "--zip-path", str(OUT / "raw_expectation_tminus1_final.zip"),
        "--polarity-mode", "resolved",
    ])

    print("\n===== ABLATION (raw YES polarity) =====", flush=True)
    runner.main([
        "--candidates-path", candidates,
        "--output-dir", str(OUT / "raw_expectation_tminus1_final_raw_yes"),
        "--zip-path", str(OUT / "raw_expectation_tminus1_final_raw_yes.zip"),
        "--polarity-mode", "raw_yes",
    ])


if __name__ == "__main__":
    main()
