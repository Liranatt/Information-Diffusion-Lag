"""Download minute-resolution Polymarket histories for the Stage 2F OOF markets.

The downloader is resumable at the market level.  It resolves numeric Gamma
market IDs through Gamma and legacy condition IDs through the CLOB API, then
stores the YES-token history from market creation through the latest candidate
entry cutoff.  The combined output remains a plain CSV as requested.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from datetime import time
from pathlib import Path
from typing import Any

import httpx
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
OOF = PROJECT / "data" / "selection_stage2f" / "nested_oof_models" / "stage2f_oof_predictions.csv"
SEMANTIC_CANDIDATES = PROJECT / "data" / "selection_stage2c" / "semantics" / "semantic_development_candidates.csv"
OUTPUT = PROJECT / "data" / "selection_stage2g" / "polymarket_download"
GAMMA_MARKET_URL = "https://gamma-api.polymarket.com/markets/{market_id}"
CLOB_MARKET_URL = "https://clob.polymarket.com/markets/{condition_id}"
CLOB_HISTORY_URL = "https://clob.polymarket.com/prices-history"
FIDELITY_MINUTES = 1
CHUNK_DAYS = 4
CONCURRENCY = 8
RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}

# The OOF period ends in December 2025.  The only NYSE early-close session in
# the candidate range is the Friday after Thanksgiving.
NYSE_EARLY_CLOSES = {pd.Timestamp("2025-11-28").date(): time(13, 0)}


def decision_timestamp(entry_date: Any) -> pd.Timestamp:
    """Scheduled NYSE close for the entry session, converted to UTC."""
    day = pd.to_datetime(entry_date, utc=True).date()
    close = NYSE_EARLY_CLOSES.get(day, time(16, 0))
    local = pd.Timestamp.combine(day, close).tz_localize("America/New_York")
    return local.tz_convert("UTC")


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    return []


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def _market_requests() -> pd.DataFrame:
    columns = ["benchmark", "event_id", "market_id", "symbol", "question", "t0", "t_theta", "t_e", "entry_date", "event_family"]
    data = pd.read_csv(SEMANTIC_CANDIDATES, usecols=columns, dtype={"market_id": str})
    data = data[data["event_family"].astype(str).str.lower().eq("earnings")].copy()
    data["stage2e_candidate_id"] = data.apply(
        lambda row: hashlib.sha256(
            "|".join(str(row.get(column, "")) for column in ("benchmark", "event_id", "market_id", "symbol", "t_theta", "t_e")).encode("utf-8")
        ).hexdigest()[:24],
        axis=1,
    )
    data["t0"] = pd.to_datetime(data["t0"], errors="coerce", utc=True)
    data["decision_ts_utc"] = data["entry_date"].map(decision_timestamp)
    if data[["t0", "decision_ts_utc"]].isna().any().any():
        raise AssertionError("Stage 2G downloader input has missing t0 or decision timestamps")
    rows = []
    for market_id, group in data.groupby("market_id", sort=True):
        rows.append(
            {
                "market_id": str(market_id),
                "question": str(group.iloc[0]["question"]),
                "requested_start_utc": group["t0"].min(),
                "requested_end_utc": group["decision_ts_utc"].max(),
                "candidate_count": len(group),
                "candidate_ids": "|".join(sorted(group["stage2e_candidate_id"].astype(str))),
            }
        )
    requests = pd.DataFrame(rows)
    if len(data) != 492 or len(requests) != 273:
        raise AssertionError(f"Expected 492 earnings candidates across 273 markets, found {len(data)} across {len(requests)}")
    return requests


async def _get_json(client: httpx.AsyncClient, url: str, *, params: dict[str, Any] | None = None) -> Any:
    last_error: Exception | None = None
    for attempt in range(6):
        try:
            response = await client.get(url, params=params)
            if response.status_code in RETRY_STATUSES:
                raise httpx.HTTPStatusError(
                    f"retryable status {response.status_code}", request=response.request, response=response
                )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            last_error = error
            if attempt == 5:
                break
            await asyncio.sleep(min(2**attempt, 20))
    raise RuntimeError(f"Polymarket request failed after retries: {url}: {last_error}")


async def _metadata(client: httpx.AsyncClient, market_id: str) -> dict[str, Any]:
    if market_id.startswith("0x"):
        payload = await _get_json(client, CLOB_MARKET_URL.format(condition_id=market_id))
        tokens = payload.get("tokens") or []
        yes = next((str(token["token_id"]) for token in tokens if str(token.get("outcome", "")).lower() == "yes"), None)
        if not yes:
            raise RuntimeError(f"CLOB market {market_id} has no YES token")
        return {
            "mapping_source": "clob_condition_id",
            "yes_token_id": yes,
            "condition_id": payload.get("condition_id"),
            "api_question": payload.get("question"),
            "api_end_date": payload.get("end_date_iso"),
        }

    payload = await _get_json(client, GAMMA_MARKET_URL.format(market_id=market_id))
    outcomes = _json_list(payload.get("outcomes"))
    tokens = _json_list(payload.get("clobTokenIds"))
    if len(outcomes) != len(tokens):
        raise RuntimeError(f"Gamma market {market_id} has mismatched outcomes/token IDs")
    yes = next((str(token) for outcome, token in zip(outcomes, tokens) if str(outcome).lower() == "yes"), None)
    if not yes:
        raise RuntimeError(f"Gamma market {market_id} has no YES token")
    return {
        "mapping_source": "gamma_market_id",
        "yes_token_id": yes,
        "condition_id": payload.get("conditionId"),
        "api_question": payload.get("question"),
        "api_created_at": payload.get("createdAt"),
        "api_end_date": payload.get("endDate"),
    }


async def _history(
    client: httpx.AsyncClient,
    token_id: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> list[tuple[pd.Timestamp, float]]:
    points: dict[int, float] = {}
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + pd.Timedelta(days=CHUNK_DAYS), end)
        payload = await _get_json(
            client,
            CLOB_HISTORY_URL,
            params={
                "market": token_id,
                "startTs": int(cursor.timestamp()),
                "endTs": int(chunk_end.timestamp()),
                "fidelity": FIDELITY_MINUTES,
            },
        )
        for item in payload.get("history") or []:
            epoch = int(float(item["t"]))
            if int(start.timestamp()) <= epoch < int(end.timestamp()):
                points[epoch] = min(max(float(item["p"]), 0.0), 1.0)
        cursor = chunk_end
    return [(pd.Timestamp(epoch, unit="s", tz="UTC"), points[epoch]) for epoch in sorted(points)]


def _write_fragment(path: Path, market: dict[str, Any], points: list[tuple[pd.Timestamp, float]], downloaded_at: pd.Timestamp) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "market_id",
        "yes_token_id",
        "source_ts_utc",
        "available_at_utc",
        "probability_yes",
        "fidelity_minutes",
        "availability_assumption",
        "downloaded_at_utc",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for timestamp, probability in points:
            writer.writerow(
                {
                    "market_id": market["market_id"],
                    "yes_token_id": market["yes_token_id"],
                    "source_ts_utc": timestamp.isoformat(),
                    "available_at_utc": timestamp.isoformat(),
                    "probability_yes": probability,
                    "fidelity_minutes": FIDELITY_MINUTES,
                    "availability_assumption": "public_CLOB_history_timestamp",
                    "downloaded_at_utc": downloaded_at.isoformat(),
                }
            )


async def _one_market(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    request: dict[str, Any],
    fragment_dir: Path,
    status_dir: Path,
    downloaded_at: pd.Timestamp,
) -> dict[str, Any]:
    market_id = str(request["market_id"])
    fragment = fragment_dir / f"{market_id}.csv"
    status_path = status_dir / f"{market_id}.json"
    if fragment.exists() and status_path.exists():
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("status") == "complete":
            return status
    async with semaphore:
        try:
            metadata = await _metadata(client, market_id)
            market = {**request, **metadata}
            start = pd.Timestamp(request["requested_start_utc"])
            end = pd.Timestamp(request["requested_end_utc"])
            points = await _history(client, metadata["yes_token_id"], start, end)
            _write_fragment(fragment, market, points, downloaded_at)
            status = {
                **market,
                "status": "complete",
                "row_count": len(points),
                "first_source_ts_utc": points[0][0] if points else None,
                "last_source_ts_utc": points[-1][0] if points else None,
                "fragment": str(fragment),
                "fragment_sha256": _sha256(fragment),
                "question_matches": str(request["question"]).strip() == str(metadata.get("api_question", "")).strip(),
                "error": None,
            }
        except Exception as error:  # noqa: BLE001 - preserve per-market failures
            status = {**request, "status": "failed", "row_count": 0, "error": repr(error)}
        _write_json(status_path, status)
        return status


def _combine_fragments(statuses: list[dict[str, Any]], output: Path) -> int:
    complete = [status for status in statuses if status.get("status") == "complete"]
    header_written = False
    row_count = 0
    with output.open("w", newline="", encoding="utf-8") as destination:
        writer: csv.DictWriter | None = None
        for status in sorted(complete, key=lambda item: str(item["market_id"])):
            with Path(status["fragment"]).open("r", newline="", encoding="utf-8") as source:
                reader = csv.DictReader(source)
                if writer is None:
                    writer = csv.DictWriter(destination, fieldnames=reader.fieldnames)
                if not header_written:
                    writer.writeheader()
                    header_written = True
                for row in reader:
                    writer.writerow(row)
                    row_count += 1
    return row_count


async def run(output_dir: Path = OUTPUT) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    fragment_dir = output_dir / "market_fragments"
    status_dir = output_dir / "market_status"
    fragment_dir.mkdir(parents=True, exist_ok=True)
    status_dir.mkdir(parents=True, exist_ok=True)
    requests = _market_requests()
    downloaded_at = pd.Timestamp.now(tz="UTC")
    semaphore = asyncio.Semaphore(CONCURRENCY)
    timeout = httpx.Timeout(90, connect=30)
    limits = httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=CONCURRENCY)
    statuses: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=timeout, limits=limits, headers={"User-Agent": "cem-stage2g-research/1.0"}) as client:
        tasks = [
            asyncio.create_task(
                _one_market(client, semaphore, row._asdict(), fragment_dir, status_dir, downloaded_at)
            )
            for row in requests.itertuples(index=False)
        ]
        for completed, task in enumerate(asyncio.as_completed(tasks), start=1):
            statuses.append(await task)
            if completed % 10 == 0 or completed == len(tasks):
                ok = sum(status.get("status") == "complete" for status in statuses)
                points = sum(int(status.get("row_count", 0)) for status in statuses)
                print(f"[polymarket] markets {completed}/{len(tasks)} complete={ok} points={points:,}", flush=True)

    metadata = pd.DataFrame(statuses).sort_values("market_id", kind="mergesort")
    metadata_path = output_dir / "polymarket_market_metadata.csv"
    metadata.to_csv(metadata_path, index=False)
    failures = metadata[~metadata["status"].eq("complete")].copy()
    failures_path = output_dir / "polymarket_download_failures.csv"
    failures.to_csv(failures_path, index=False)
    combined_path = output_dir / "polymarket_probability_history.csv"
    combined_rows = _combine_fragments(statuses, combined_path)
    manifest_path = output_dir / "polymarket_download_manifest.json"
    _write_json(
        manifest_path,
        {
            "source": "public Polymarket Gamma and CLOB APIs",
            "fidelity_minutes": FIDELITY_MINUTES,
            "availability_assumption": "CLOB history source timestamp equals public availability timestamp",
            "decision_cutoff": "source_ts strictly before scheduled NYSE entry close; 2025-11-28 uses 13:00 America/New_York",
            "requested_markets": len(requests),
            "development_earnings_candidates": 492,
            "oof_earnings_candidates": 415,
            "complete_markets": int(metadata["status"].eq("complete").sum()),
            "failed_markets": len(failures),
            "combined_rows": combined_rows,
            "downloaded_at_utc": downloaded_at,
            "oof_sha256": _sha256(OOF),
            "semantic_candidates_sha256": _sha256(SEMANTIC_CANDIDATES),
            "combined_csv_sha256": _sha256(combined_path),
            "outputs": {
                "history": str(combined_path),
                "metadata": str(metadata_path),
                "failures": str(failures_path),
            },
        },
    )
    if len(failures):
        raise RuntimeError(f"Polymarket download incomplete: {len(failures)} markets failed; see {failures_path}")
    return {"history": combined_path, "metadata": metadata_path, "manifest": manifest_path}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    outputs = asyncio.run(run(args.output_dir))
    for name, path in outputs.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
