"""Tariff-universe H1 driver (supplementary to the 2026-07-14 final ICAIF run).

Runs the identical raw-expectation T-1 protocol (20,000 bootstrap replications,
seed 42) that produced output/final_icaif_run_20260714_170817/h1, but on the
13 tariff candidate rows built by tariff_run.py (data/tariff_run/
tariff_candidates.parquet) — the rows recoverable after removing the gate's
positive-sentiment skip. Both variants are produced: resolved polarity and the
raw-YES ablation.

Comparability with the final run (verified before writing this driver):
  - fold policies: output/final_icaif_run_20260714_170817/cem_matrix/
    experiment_walkforward_folds_matrix.csv, sha256 0387179e... == the
    'experiment_walkforward_folds_clean.csv' hash in the final H1 manifest.
  - probabilities: data/probs.pkl (committed V2 artifact; already contains all
    32 tariff CLOB paths) — same file the final H1 read.
  - prices: data/tariff_run/prices_tariff.pkl, a verified superset of
    data/prices.pkl (adds the country ETFs; identical bars for shared symbols).
  - polarity: labels_h1.json = committed data/polarity_labels.json (precedence
    on overlap) + the tariff-run LLM labels for the new pairs.

No repo code is modified; only module-level constants are set (the same
pattern as run_h1_final.py).
"""
from __future__ import annotations

import functools
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\Liran\PycharmProjects\cem_clean_repo")
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

N_BOOT = 20_000
SEED = 42

import core.polarity as core_polarity  # noqa: E402

core_polarity._LABELS_PATH = OUT / "labels_h1.json"
core_polarity.clear_polarity_caches()

import diagnostics.run_raw_expectation_test_tminus1 as runner  # noqa: E402
import analysis.h1_expectation_protocol as protocol  # noqa: E402

runner.N_BOOTSTRAP = N_BOOT
runner.run_expectation_protocol = functools.partial(
    protocol.run_expectation_protocol, n_boot=N_BOOT, seed=SEED
)
runner.PRICES_PATH = ROOT / "data" / "tariff_run" / "prices_tariff.pkl"
runner.PROBS_PATH = OUT / "probs_h1.pkl"
runner.WF_FOLDS_CSV = (
    ROOT / "output" / "final_icaif_run_20260714_170817" / "cem_matrix"
    / "experiment_walkforward_folds_matrix.csv"
)

candidates = str(ROOT / "data" / "tariff_run" / "tariff_candidates.parquet")


def main() -> None:
    print("\n===== TARIFF PRIMARY (resolved polarity) =====", flush=True)
    runner.main([
        "--candidates-path", candidates,
        "--output-dir", str(OUT / "raw_expectation_tminus1_tariff"),
        "--zip-path", str(OUT / "raw_expectation_tminus1_tariff.zip"),
        "--polarity-mode", "resolved",
    ])

    print("\n===== TARIFF ABLATION (raw YES polarity) =====", flush=True)
    runner.main([
        "--candidates-path", candidates,
        "--output-dir", str(OUT / "raw_expectation_tminus1_tariff_raw_yes"),
        "--zip-path", str(OUT / "raw_expectation_tminus1_tariff_raw_yes.zip"),
        "--polarity-mode", "raw_yes",
    ])


if __name__ == "__main__":
    main()
