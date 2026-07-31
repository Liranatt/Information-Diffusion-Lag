"""Extend earnings Polymarket history before legacy ``t0``.

Stage 2G downloaded from the candidate-builder crossing timestamp onward.
That made a 24-hour CEDE probability update impossible whenever ``t0`` was
less than one day before entry, even if the contract had traded publicly for
days.  This downloader obtains the three-day public pre-``t0`` window for each
market and merges it with the original minute history.  A contract that truly
did not exist for 24 hours remains an honest coverage failure.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd

from analysis.download_stage2g_polymarket_history import (
    CONCURRENCY,
    _combine_fragments,
    _one_market,
    _sha256,
    _write_json,
    decision_timestamp,
)


PROJECT = Path(__file__).resolve().parents[1]
OOF = PROJECT / "data" / "selection_stage2f" / "nested_oof_models" / "stage2f_oof_predictions.csv"
ORIGINAL = PROJECT / "data" / "selection_stage2g" / "polymarket_download" / "polymarket_probability_history.csv"
OUTPUT = PROJECT / "data" / "cede" / "extended_earnings_probability_download"
LOOKBACK = pd.Timedelta(days=3)


def _requests() -> pd.DataFrame:
    data = pd.read_csv(OOF, usecols=["market_id", "question", "t0", "entry_date", "event_family"], dtype={"market_id": str})
    data = data[data["event_family"].eq("earnings")].copy()
    data["t0"] = pd.to_datetime(data["t0"], errors="coerce", utc=True)
    data["decision_ts_utc"] = data["entry_date"].map(decision_timestamp)
    if data[["t0", "decision_ts_utc"]].isna().any().any():
        raise ValueError("CEDE extended-history request has missing timestamps")
    rows = []
    for market_id, group in data.groupby("market_id", sort=True):
        first_t0 = group["t0"].min()
        rows.append({
            "market_id": str(market_id),
            "question": str(group.iloc[0]["question"]),
            "requested_start_utc": first_t0 - LOOKBACK,
            "requested_end_utc": first_t0,
            "first_candidate_t0_utc": first_t0,
            "latest_candidate_decision_utc": group["decision_ts_utc"].max(),
            "candidate_count": int(len(group)),
        })
    return pd.DataFrame(rows)


def _merge_history(pre_history: Path, output: Path) -> int:
    """Merge pre-t0 and Stage-2G histories without trusting file ordering."""
    existing = pd.read_csv(ORIGINAL, dtype={"market_id": str})
    earlier = pd.read_csv(pre_history, dtype={"market_id": str})
    combined = pd.concat([existing, earlier], ignore_index=True)
    combined["source_ts_utc"] = pd.to_datetime(combined["source_ts_utc"], utc=True)
    combined["available_at_utc"] = pd.to_datetime(combined["available_at_utc"], utc=True)
    combined = combined.drop_duplicates(["market_id", "available_at_utc"], keep="last").sort_values(["market_id", "available_at_utc"])
    combined.to_csv(output, index=False)
    return len(combined)


async def run(output_dir: Path = OUTPUT) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    fragment_dir, status_dir = output_dir / "market_fragments", output_dir / "market_status"
    fragment_dir.mkdir(parents=True, exist_ok=True)
    status_dir.mkdir(parents=True, exist_ok=True)
    requests = _requests()
    downloaded_at = pd.Timestamp.now(tz="UTC")
    semaphore = asyncio.Semaphore(CONCURRENCY)
    import httpx

    timeout = httpx.Timeout(90, connect=30)
    limits = httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=CONCURRENCY)
    statuses: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=timeout, limits=limits, headers={"User-Agent": "cede-extended-history-research/1.0"}) as client:
        tasks = [
            asyncio.create_task(_one_market(client, semaphore, row._asdict(), fragment_dir, status_dir, downloaded_at))
            for row in requests.itertuples(index=False)
        ]
        for count, task in enumerate(asyncio.as_completed(tasks), start=1):
            statuses.append(await task)
            if count % 10 == 0 or count == len(tasks):
                done = sum(item.get("status") == "complete" for item in statuses)
                print(f"[cede-extended-history] markets {count}/{len(tasks)} complete={done}", flush=True)
    metadata = pd.DataFrame(statuses).sort_values("market_id", kind="mergesort")
    metadata_path = output_dir / "extended_market_metadata.csv"
    metadata.to_csv(metadata_path, index=False)
    failures = metadata[~metadata["status"].eq("complete")]
    failures_path = output_dir / "extended_download_failures.csv"
    failures.to_csv(failures_path, index=False)
    pre_history = output_dir / "pre_t0_probability_history.csv"
    pre_rows = _combine_fragments(statuses, pre_history)
    merged_history = output_dir / "extended_probability_history.csv"
    merged_rows = _merge_history(pre_history, merged_history)
    manifest_path = output_dir / "extended_download_manifest.json"
    _write_json(manifest_path, {
        "source": "public Polymarket Gamma and CLOB APIs",
        "fidelity_minutes": 1,
        "coverage_extension": "three_days_before_each_market_first_candidate_t0",
        "requested_markets": len(requests),
        "complete_markets": int(metadata["status"].eq("complete").sum()),
        "failed_markets": int(len(failures)),
        "pre_t0_rows": pre_rows,
        "merged_rows": merged_rows,
        "downloaded_at_utc": downloaded_at,
        "original_history_sha256": _sha256(ORIGINAL),
        "oof_sha256": _sha256(OOF),
    })
    if len(failures):
        raise RuntimeError(f"Extended download incomplete: {len(failures)} markets failed")
    return {"history": merged_history, "metadata": metadata_path, "manifest": manifest_path}


def main() -> None:
    parser = argparse.ArgumentParser(description="Extend CEDE earnings probability history before legacy t0.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    print(asyncio.run(run(args.output_dir)))


if __name__ == "__main__":
    main()
