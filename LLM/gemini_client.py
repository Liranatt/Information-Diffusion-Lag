from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, TypeVar

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

load_dotenv()

T = TypeVar("T", bound=BaseModel)
RETRY_DELAY_RE = re.compile(r"retry in ([0-9.]+)s", re.IGNORECASE)


def _largest_enum_size(value: Any) -> int:
    if isinstance(value, dict):
        own = len(value.get("enum", ())) if isinstance(value.get("enum"), list) else 0
        return max([own, *(_largest_enum_size(item) for item in value.values())])
    if isinstance(value, list):
        return max((_largest_enum_size(item) for item in value), default=0)
    return 0


def _requires_prompt_schema(schema: dict[str, Any]) -> bool:
    # Gemini rejects the catalog-selection schema when the allowed ticker enum is large. Single-level
    # batched models (event/market keep-decisions, the relevance gate) work fine on the server schema;
    # only the doubly-nested world models need prompt-schema, and those request it explicitly via
    # prefer_prompt_schema, so this stays a narrow heuristic.
    return _largest_enum_size(schema) > 30


class GeminiClient:
    def __init__(self) -> None:
        api_key = os.environ.get("my_traders_api_key")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY or my_traders_api_key must be configured")
        self.model_name = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
        self.thinking_level = os.environ.get("GEMINI_THINKING_LEVEL", "low")
        self.validation_retries = int(os.environ.get("GEMINI_VALIDATION_RETRIES", "2"))
        self.force_prompt_schema = (
            os.environ.get("GEMINI_FORCE_PROMPT_SCHEMA", "").lower()
            in {"1", "true", "yes"}
        )
        self.trace: list[dict[str, Any]] = []
        self._api_key = api_key
        # Bounded concurrency instead of a hard global lock so independent Gemini
        # calls can overlap (API rate limits are handled with backoff in _post).
        self._semaphore = asyncio.Semaphore(
            int(os.environ.get("GEMINI_CONCURRENCY", "5"))
        )
        self.client = httpx.AsyncClient(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            timeout=httpx.Timeout(300),
        )

    async def close(self) -> None:
        await self.client.aclose()

    def _request_body(
        self,
        *,
        system_prompt: str,
        payload: dict[str, Any],
        schema: dict[str, Any],
        use_server_schema: bool,
        max_tokens: int,
        correction: str | None,
    ) -> dict[str, Any]:
        instructions = system_prompt
        if not use_server_schema:
            instructions += (
                "\n\nReturn only JSON that validates exactly against this schema. "
                "The schema is enforced again by the caller:\n"
                + json.dumps(schema, ensure_ascii=False)
            )
        if correction:
            instructions += (
                "\n\nYour previous JSON failed validation. Correct every listed issue "
                "while preserving the requested analysis:\n"
                + correction
            )
        generation_config: dict[str, Any] = {
            "temperature": 0,
            "topP": 0.1,
            # Thinking tokens share the output budget, so the Ollama-sized limits truncate
            # Gemini responses. Keep enough room for reasoning plus the final JSON.
            "maxOutputTokens": max(max_tokens, 8192),
            "responseMimeType": "application/json",
        }
        if self.thinking_level.lower() not in {"", "none", "off"}:
            generation_config["thinkingConfig"] = {
                "thinkingLevel": self.thinking_level
            }
        if use_server_schema:
            generation_config["responseJsonSchema"] = schema
        return {
            "systemInstruction": {"parts": [{"text": instructions}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": json.dumps(
                                payload,
                                ensure_ascii=False,
                                default=str,
                            )
                        }
                    ],
                }
            ],
            "generationConfig": generation_config,
        }

    async def _post(self, body: dict[str, Any]) -> httpx.Response:
        last: httpx.Response | None = None
        for attempt in range(6):
            try:
                response = await self.client.post(
                    f"/models/{self.model_name}:generateContent",
                    headers={"x-goog-api-key": self._api_key},
                    json=body,
                )
            except (httpx.TransportError, httpx.TimeoutException):
                # Transient network error -- back off and retry instead of killing the run.
                if attempt == 5:
                    raise
                await asyncio.sleep(min(2 ** attempt + 1, 45))
                continue
            last = response
            # Success or a non-retryable client error -> return as-is.
            if response.status_code != 429 and response.status_code < 500:
                return response
            if response.status_code == 429:
                text = response.text
                if "PerDay" in text or "requests per day" in text.lower():
                    return response  # daily quota -- retrying will not help
                match = RETRY_DELAY_RE.search(text)
                delay = float(match.group(1)) + 1 if match else min(2 ** attempt, 30)
            else:
                # 5xx transient server error (e.g. 502/503) -- exponential backoff and retry.
                delay = min(2 ** attempt + 1, 45)
            await asyncio.sleep(min(delay, 70))
        return last  # type: ignore[return-value]

    async def structured(
        self,
        *,
        system_prompt: str,
        payload: dict[str, Any],
        response_model: type[T],
        max_tokens: int = 1000,
        prefer_prompt_schema: bool = False,
    ) -> T:
        schema = response_model.model_json_schema()
        # Some response models (nested arrays of objects carrying enums, `maxItems`, and
        # field `default`s -- e.g. the tight asset-world models) are rejected by Gemini's
        # server-side responseJsonSchema with HTTP 400. Callers can opt those into the
        # prompt-schema + client-validation path, which Pydantic still enforces.
        use_server_schema = (
            not self.force_prompt_schema
            and not prefer_prompt_schema
            and not _requires_prompt_schema(schema)
        )
        correction: str | None = None
        last_error: Exception | None = None

        async with self._semaphore:
            for validation_attempt in range(self.validation_retries + 1):
                body = self._request_body(
                    system_prompt=system_prompt,
                    payload=payload,
                    schema=schema,
                    use_server_schema=use_server_schema,
                    max_tokens=max_tokens,
                    correction=correction,
                )
                started = time.perf_counter()
                response = await self._post(body)
                elapsed = time.perf_counter() - started
                if response.is_error:
                    error = RuntimeError(
                        f"Gemini HTTP {response.status_code}: {response.text[:4000]}"
                    )
                    self.trace.append(
                        {
                            "response_model": response_model.__name__,
                            "schema_mode": (
                                "server_json_schema"
                                if use_server_schema
                                else "prompt_schema_client_validation"
                            ),
                            "validation_attempt": validation_attempt + 1,
                            "duration_seconds": elapsed,
                            "http_status": response.status_code,
                            "error": str(error),
                        }
                    )
                    raise error

                body_json = response.json()
                try:
                    raw_output = body_json["candidates"][0]["content"]["parts"][0][
                        "text"
                    ]
                except (KeyError, IndexError, TypeError) as error:
                    raise RuntimeError(
                        "Gemini response is missing candidates[0].content.parts[0].text: "
                        f"{json.dumps(body_json)[:4000]}"
                    ) from error

                trace = {
                    "response_model": response_model.__name__,
                    "schema_mode": (
                        "server_json_schema"
                        if use_server_schema
                        else "prompt_schema_client_validation"
                    ),
                    "validation_attempt": validation_attempt + 1,
                    "duration_seconds": elapsed,
                    "http_status": response.status_code,
                    "usage": body_json.get("usageMetadata", {}),
                    "raw_output": raw_output,
                }
                try:
                    parsed = response_model.model_validate_json(raw_output)
                except ValidationError as error:
                    last_error = error
                    correction = str(error)
                    trace["validation_error"] = correction
                    self.trace.append(trace)
                    continue
                trace["parsed_output"] = parsed.model_dump(mode="json")
                self.trace.append(trace)
                return parsed

        assert last_error is not None
        raise last_error
