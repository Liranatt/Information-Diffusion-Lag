"""Download minute Polymarket histories for the non-earnings CEDE universe.

This deliberately uses the same public Gamma/CLOB API and timestamp convention
as Stage 2G.  It collects the semantic geo/other development legs and the
separate tariff/macro experiment, leaving their source family explicit rather
than silently relabeling all ``other`` events as macro.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
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
SEMANTIC = PROJECT / "data" / "selection_stage2c" / "semantics" / "semantic_development_candidates.csv"
TARIFF = PROJECT / "data" / "tariff_run" / "tariff_candidates.parquet"
OUTPUT = PROJECT / "data" / "multifamily_probability_download"


def _request_rows() -> pd.DataFrame:
    semantic = pd.read_csv(
        SEMANTIC,
        usecols=["event_id", "market_id", "symbol", "question", "t0", "t_theta", "t_e", "entry_date", "event_family"],
        dtype={"market_id": str},
    )
    semantic = semantic[semantic["event_family"].isin(["geo", "other"])].copy()
    semantic["research_family"] = semantic["event_family"].map({"geo": "geopolitics", "other": "other"})

    tariff = pd.read_parquet(TARIFF)
    tariff = tariff[["event_id", "market_id", "symbol", "question", "t0", "t_theta", "t_e"]].copy()
    tariff["market_id"] = tariff["market_id"].astype(str)
    # H1's conservative entry date is the first session on/after t_theta.
    tariff["entry_date"] = pd.to_datetime(tariff["t_theta"], utc=True).dt.normalize()
    tariff["research_family"] = "macro"
    data = pd.concat([semantic, tariff], ignore_index=True)
    data["t0"] = pd.to_datetime(data["t0"], utc=True)
    data["decision_ts_utc"] = data["entry_date"].map(decision_timestamp)
    if data[["t0", "decision_ts_utc"]].isna().any().any():
        raise ValueError("Non-earnings downloader has missing history bounds")
    rows: list[dict[str, Any]] = []
    for market_id, group in data.groupby("market_id", sort=True):
        rows.append({
            "market_id": str(market_id),
            "question": str(group.iloc[0]["question"]),
            "requested_start_utc": group["t0"].min(),
            "requested_end_utc": group["decision_ts_utc"].max(),
            "candidate_count": int(len(group)),
            "research_families": "|".join(sorted(group["research_family"].unique())),
            "economic_event_ids": "|".join(sorted(group["event_id"].astype(str).unique())),
        })
    return pd.DataFrame(rows)


async def run(output_dir: Path = OUTPUT) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    fragment_dir, status_dir = output_dir / "market_fragments", output_dir / "market_status"
    fragment_dir.mkdir(parents=True, exist_ok=True)
    status_dir.mkdir(parents=True, exist_ok=True)
    requests = _request_rows()
    downloaded_at = pd.Timestamp.now(tz="UTC")
    semaphore = asyncio.Semaphore(CONCURRENCY)
    import httpx

    timeout = httpx.Timeout(90, connect=30)
    limits = httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=CONCURRENCY)
    statuses: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=timeout, limits=limits, headers={"User-Agent": "cede-multifamily-research/1.0"}) as client:
        tasks = [
            asyncio.create_task(_one_market(client, semaphore, row._asdict(), fragment_dir, status_dir, downloaded_at))
            for row in requests.itertuples(index=False)
        ]
        for count, task in enumerate(asyncio.as_completed(tasks), start=1):
            statuses.append(await task)
            if count % 5 == 0 or count == len(tasks):
                done = sum(item.get("status") == "complete" for item in statuses)
                print(f"[multifamily-polymarket] markets {count}/{len(tasks)} complete={done}", flush=True)
    metadata = pd.DataFrame(statuses).sort_values("market_id", kind="mergesort")
    metadata_path = output_dir / "polymarket_market_metadata.csv"
    metadata.to_csv(metadata_path, index=False)
    failures = metadata[~metadata["status"].eq("complete")]
    failures_path = output_dir / "polymarket_download_failures.csv"
    failures.to_csv(failures_path, index=False)
    history_path = output_dir / "polymarket_probability_history.csv"
    rows = _combine_fragments(statuses, history_path)
    manifest_path = output_dir / "polymarket_download_manifest.json"
    _write_json(manifest_path, {
        "source": "public Polymarket Gamma and CLOB APIs",
        "fidelity_minutes": 1,
        "availability_assumption": "public_CLOB_history_timestamp",
        "semantic_sha256": _sha256(SEMANTIC),
        "tariff_sha256": _sha256(TARIFF),
        "requested_markets": len(requests),
        "complete_markets": int(metadata["status"].eq("complete").sum()),
        "failed_markets": int(len(failures)),
        "history_rows": rows,
        "downloaded_at_utc": downloaded_at,
    })
    if len(failures):
        raise RuntimeError(f"Download incomplete: {len(failures)} failed markets")
    return {"history": history_path, "metadata": metadata_path, "manifest": manifest_path}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    print(asyncio.run(run(args.output_dir)))


if __name__ == "__main__":
    main()
