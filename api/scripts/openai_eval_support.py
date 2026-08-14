"""OpenAI Responses API adapter for provider bake-offs only."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from app.services.openai_responses import OpenAIResponsesError, response_usage
from app.services.scoring_provider import (
    OPENAI_V2_SCHEMA_NAME,
    openai_responses_request,
)
from scripts.effort_sweep_support import Usage

RESPONSES_URL = "https://api.openai.com/v1/responses"
INPUT_TOKENS_URL = "https://api.openai.com/v1/responses/input_tokens"
REQUEST_TIMEOUT_SECONDS = 45.0
INPUT_FRAMING_ALLOWANCE = 2048
V2_EVAL_SAFETY_IDENTIFIER_FORMAT_VERSION = 1
V2_EVAL_SAFETY_IDENTIFIER = hashlib.sha256(
    b"devmax:v2-recall:synthetic-qualification-traffic:v1"
).hexdigest()


class OpenAIEvalError(RuntimeError):
    """Raised when a bake-off response cannot be used as evaluation evidence."""

    def __init__(
        self,
        message: str,
        *,
        response_id: str = "",
        model: str = "",
        elapsed_ms: int = 0,
        usage: Usage | None = None,
        failure_type: str = "openai_eval_error",
    ) -> None:
        super().__init__(message)
        self.response_id = response_id
        self.model = model
        self.elapsed_ms = elapsed_ms
        self.usage = usage or Usage()
        self.failure_type = failure_type


@dataclass(frozen=True)
class OpenAIEvalResponse:
    data: dict[str, Any]
    usage: Usage
    response_id: str
    model: str
    elapsed_ms: int = 0


def response_request(completion: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """Translate one production prompt into the current Responses API shape."""
    schema_name = OPENAI_V2_SCHEMA_NAME if kind == "v2-recall" else f"devmax_{kind}"
    return openai_responses_request(
        completion,
        schema_name=schema_name,
        # Qualification traffic has no account owner. Use one deterministic,
        # versioned, non-user identifier with production's 64-hex field shape.
        safety_identifier=(
            V2_EVAL_SAFETY_IDENTIFIER if kind == "v2-recall" else None
        ),
    )


def conservative_input_bound(request: dict[str, Any]) -> int:
    """Return a deliberately high local token bound without requiring an API key.

    Every tokenizer token consumes at least one encoded byte from the supplied
    instructions, input, or schema. The fixed allowance covers provider framing
    and structured-output metadata that are not visible in the request body.
    """
    counted = {
        "instructions": request.get("instructions", ""),
        "input": request.get("input", ""),
        "text": request.get("text", {}),
    }
    return len(
        json.dumps(counted, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ) + INPUT_FRAMING_ALLOWANCE


def input_token_count_request(request: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields accepted by the Responses input-token endpoint."""
    return {
        key: request[key]
        for key in ("model", "instructions", "input", "reasoning", "text")
        if key in request
    }


def _output_text(response: dict[str, Any]) -> str:
    for item in response.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") == "refusal":
                raise OpenAIEvalError(
                    f"OpenAI refused the evaluation: {content.get('refusal', '')}",
                    failure_type="refusal",
                )
            if content.get("type") == "output_text":
                return str(content.get("text", ""))
    details = response.get("incomplete_details")
    suffix = f" ({details})" if details else ""
    raise OpenAIEvalError(
        f"OpenAI response contained no output text{suffix}",
        failure_type="missing_output",
    )


def _response_usage(response: dict[str, Any]) -> Usage:
    try:
        input_tokens, output_tokens, cached_tokens, cache_write_tokens = (
            response_usage(response)
        )
    except OpenAIResponsesError as exc:
        raise OpenAIEvalError(str(exc), failure_type="invalid_usage") from exc
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cached_tokens,
        cache_write_tokens=cache_write_tokens,
    )


def _with_response_metadata(
    error: OpenAIEvalError,
    response: dict[str, Any],
    *,
    elapsed_ms: int,
    usage: Usage | None = None,
) -> OpenAIEvalError:
    raw_response_id = response.get("id")
    raw_model = response.get("model")
    return OpenAIEvalError(
        str(error),
        response_id=(
            raw_response_id
            if type(raw_response_id) is str and raw_response_id.strip()
            else ""
        ),
        model=(
            raw_model if type(raw_model) is str and raw_model.strip() else ""
        ),
        elapsed_ms=elapsed_ms,
        usage=usage,
        failure_type=error.failure_type,
    )


def _response_identity(response: dict[str, Any]) -> tuple[str, str]:
    response_id = response.get("id")
    model = response.get("model")
    if type(response_id) is not str or not response_id.strip():
        raise OpenAIEvalError(
            "OpenAI response id was not a non-empty string",
            failure_type="invalid_response_identity",
        )
    if type(model) is not str or not model.strip():
        raise OpenAIEvalError(
            "OpenAI response model was not a non-empty string",
            failure_type="invalid_response_identity",
        )
    return response_id, model


def parse_response(response: dict[str, Any], *, elapsed_ms: int = 0) -> OpenAIEvalResponse:
    """Extract strict JSON and billable usage from a raw Responses result."""
    try:
        usage = _response_usage(response)
    except OpenAIEvalError as exc:
        raise _with_response_metadata(exc, response, elapsed_ms=elapsed_ms) from exc
    try:
        response_id, response_model = _response_identity(response)
    except OpenAIEvalError as exc:
        raise _with_response_metadata(
            exc, response, elapsed_ms=elapsed_ms, usage=usage
        ) from exc
    try:
        status = response.get("status")
        if status != "completed":
            details = response.get("incomplete_details")
            reason = details.get("reason") if isinstance(details, dict) else None
            suffix = f": {reason}" if reason else ""
            raise OpenAIEvalError(
                f"OpenAI evaluation response was not completed{suffix}",
                failure_type="non_completed",
            )
        try:
            data = json.loads(_output_text(response))
        except json.JSONDecodeError as exc:
            raise OpenAIEvalError(
                "OpenAI returned output that was not valid JSON",
                failure_type="invalid_json",
            ) from exc
        if not isinstance(data, dict):
            raise OpenAIEvalError(
                "OpenAI structured output was not an object",
                failure_type="invalid_schema",
            )
    except OpenAIEvalError as exc:
        raise _with_response_metadata(
            exc, response, elapsed_ms=elapsed_ms, usage=usage
        ) from exc

    return OpenAIEvalResponse(
        data=data,
        usage=usage,
        response_id=response_id,
        model=response_model,
        elapsed_ms=elapsed_ms,
    )


def actual_cost(
    records: list[dict[str, Any]],
    *,
    input_per_million: Decimal,
    output_per_million: Decimal,
    cached_input_per_million: Decimal,
    cache_write_per_million: Decimal,
) -> Decimal:
    """Price OpenAI usage exactly when input includes cached and written tokens."""
    input_tokens = 0
    output_tokens = 0
    cached_input_tokens = 0
    cache_write_tokens = 0
    for record in records:
        raw = record.get("usage")
        if not isinstance(raw, dict):
            raise OpenAIEvalError("result record contained no usable token usage")
        values = (
            raw.get("input_tokens"),
            raw.get("output_tokens"),
            raw.get("cache_read_tokens", 0),
            raw.get("cache_write_tokens", 0),
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in values
        ):
            raise OpenAIEvalError("result record contained no usable token usage")
        call_input, call_output, call_cached, call_write = values
        if call_cached + call_write > call_input:
            raise OpenAIEvalError(
                "OpenAI cached and cache-write token usage exceeded total input"
            )
        input_tokens += call_input
        output_tokens += call_output
        cached_input_tokens += call_cached
        cache_write_tokens += call_write
    uncached_input_tokens = input_tokens - cached_input_tokens - cache_write_tokens
    return (
        Decimal(uncached_input_tokens) * input_per_million
        + Decimal(cached_input_tokens) * cached_input_per_million
        + Decimal(cache_write_tokens) * cache_write_per_million
        + Decimal(output_tokens) * output_per_million
    ) / Decimal(1_000_000)


def _api_error(response: httpx.Response, *, elapsed_ms: int = 0) -> OpenAIEvalError:
    body: dict[str, Any] = {}
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            body = parsed
        message = body.get("error", {}).get("message")
    except (ValueError, AttributeError):
        message = None
    detail = str(message or response.reason_phrase)
    try:
        usage = _response_usage(body)
    except OpenAIEvalError:
        usage = Usage()
    raw_response_id = body.get("id") or response.headers.get("x-request-id", "")
    raw_model = body.get("model")
    return OpenAIEvalError(
        f"OpenAI API returned HTTP {response.status_code}: {detail}",
        response_id=(
            raw_response_id
            if type(raw_response_id) is str and raw_response_id.strip()
            else ""
        ),
        model=(
            raw_model if type(raw_model) is str and raw_model.strip() else ""
        ),
        elapsed_ms=elapsed_ms,
        usage=usage,
        failure_type="http_error",
    )


async def count_input_tokens(
    request: dict[str, Any],
    *,
    api_key: str,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Ask OpenAI for the exact input count without generating a response."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async def send(active_client: httpx.AsyncClient) -> int:
        try:
            response = await active_client.post(
                INPUT_TOKENS_URL,
                headers=headers,
                json=input_token_count_request(request),
            )
        except httpx.HTTPError as exc:
            raise OpenAIEvalError(f"OpenAI token-count request failed: {exc}") from exc
        if not response.is_success:
            raise _api_error(response)
        try:
            body = response.json()
            input_tokens = body["input_tokens"]
        except (KeyError, TypeError, ValueError) as exc:
            raise OpenAIEvalError(
                "OpenAI token-count response contained no usable input_tokens"
            ) from exc
        if type(input_tokens) is not int or input_tokens < 0:
            raise OpenAIEvalError(
                "OpenAI token-count input_tokens must be a non-negative exact integer"
            )
        return input_tokens

    if client is not None:
        return await send(client)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as active_client:
        return await send(active_client)


async def complete(
    request: dict[str, Any], *, api_key: str, client: httpx.AsyncClient | None = None
) -> OpenAIEvalResponse:
    """Send one non-retried paid request and parse its structured result."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async def send(active_client: httpx.AsyncClient) -> OpenAIEvalResponse:
        started = time.monotonic()
        try:
            response = await active_client.post(RESPONSES_URL, headers=headers, json=request)
        except httpx.HTTPError as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            raise OpenAIEvalError(
                f"OpenAI request failed: {exc}",
                elapsed_ms=elapsed_ms,
                failure_type="transport_error",
            ) from exc
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if not response.is_success:
            raise _api_error(response, elapsed_ms=elapsed_ms)
        try:
            body = response.json()
        except ValueError as exc:
            raise OpenAIEvalError(
                "OpenAI response body was not valid JSON",
                response_id=response.headers.get("x-request-id", ""),
                elapsed_ms=elapsed_ms,
                failure_type="invalid_json",
            ) from exc
        if not isinstance(body, dict):
            raise OpenAIEvalError(
                "OpenAI response body was not an object",
                response_id=response.headers.get("x-request-id", ""),
                elapsed_ms=elapsed_ms,
                failure_type="invalid_schema",
            )
        return parse_response(body, elapsed_ms=elapsed_ms)

    if client is not None:
        return await send(client)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as active_client:
        return await send(active_client)
