"""Production-neutral transport adapter for OpenAI's Responses API.

The scoring service prepares provider-independent completions containing a model,
rubric, user content, JSON schema, output-token cap, and optional reasoning effort.
This module translates that shape and returns normalized structured data plus the
provider metadata needed for logging. It deliberately owns no routing or product
semantics.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from app.services.scoring_provider import openai_responses_request

RESPONSES_URL = "https://api.openai.com/v1/responses"
REQUEST_TIMEOUT_SECONDS = 45.0


class OpenAIResponsesError(RuntimeError):
    """Raised when a Responses request cannot produce usable structured data."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "responses_error",
        response_id: str = "",
        model: str = "",
        elapsed_ms: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_input_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.response_id = response_id
        self.model = model
        self.elapsed_ms = elapsed_ms
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cached_input_tokens = cached_input_tokens
        self.cache_write_tokens = cache_write_tokens


@dataclass(frozen=True)
class OpenAIResponsesResult:
    """Normalized result and billable usage from one Responses transmission."""

    data: dict[str, Any]
    response_id: str
    model: str
    elapsed_ms: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int = 0


def response_request(
    completion: Mapping[str, Any],
    *,
    schema_name: str,
    safety_identifier: str | None = None,
) -> dict[str, Any]:
    """Translate an existing prepared completion into a Responses request."""
    return openai_responses_request(
        completion,
        schema_name=schema_name,
        safety_identifier=safety_identifier,
    )


@lru_cache
def _client() -> httpx.AsyncClient:
    """Reuse one connection pool; the transport's retry count is explicitly zero."""
    return httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT_SECONDS,
        transport=httpx.AsyncHTTPTransport(retries=0),
    )


def _http_error(response: httpx.Response, *, elapsed_ms: int) -> OpenAIResponsesError:
    try:
        body = response.json()
    except ValueError:
        body = None

    message = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
    detail = str(message or response.reason_phrase)
    metadata = _response_error(
        body if isinstance(body, dict) else {},
        f"OpenAI Responses API returned HTTP {response.status_code}: {detail}",
        code=f"http_{response.status_code}",
        elapsed_ms=elapsed_ms,
    )
    return OpenAIResponsesError(
        str(metadata),
        code=metadata.code,
        response_id=(
            metadata.response_id or response.headers.get("x-request-id", "")
        ),
        model=metadata.model,
        elapsed_ms=metadata.elapsed_ms,
        input_tokens=metadata.input_tokens,
        output_tokens=metadata.output_tokens,
        cached_input_tokens=metadata.cached_input_tokens,
        cache_write_tokens=metadata.cache_write_tokens,
    )


def _output_text(response: Mapping[str, Any], *, elapsed_ms: int) -> str:
    output = response.get("output")
    if not isinstance(output, list):
        raise _response_error(
            response,
            "OpenAI Responses response contained no output text",
            code="missing_output",
            elapsed_ms=elapsed_ms,
        )
    output_text = ""
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        contents = item.get("content")
        if not isinstance(contents, list):
            continue
        for content in contents:
            if not isinstance(content, dict):
                continue
            if content.get("type") == "refusal":
                raise _response_error(
                    response,
                    "OpenAI Responses request was refused",
                    code="refusal",
                    elapsed_ms=elapsed_ms,
                )
            if content.get("type") == "output_text":
                output_text += str(content.get("text", ""))
    if output_text:
        return output_text
    raise _response_error(
        response,
        "OpenAI Responses response contained no output text",
        code="missing_output",
        elapsed_ms=elapsed_ms,
    )


def response_usage(response: Mapping[str, Any]) -> tuple[int, int, int, int]:
    """Normalize Responses token buckets for production and qualification."""
    raw = response.get("usage")
    if not isinstance(raw, dict):
        raise OpenAIResponsesError(
            "OpenAI Responses response contained no usable token usage",
            code="invalid_usage",
        )

    details = raw.get("input_tokens_details")
    if details is None:
        cached: object = 0
        nested_cache_write: object | None = None
    elif isinstance(details, dict):
        cached = details.get("cached_tokens", 0)
        nested_cache_write = details.get("cache_write_tokens")
    else:
        raise OpenAIResponsesError(
            "OpenAI Responses response contained no usable token usage",
            code="invalid_usage",
        )

    root_cache_write = raw.get("cache_write_tokens")
    if (
        nested_cache_write is not None
        and root_cache_write is not None
        and nested_cache_write != root_cache_write
    ):
        raise OpenAIResponsesError(
            "OpenAI Responses response contained conflicting token usage",
            code="invalid_usage",
        )
    cache_write = (
        nested_cache_write
        if nested_cache_write is not None
        else root_cache_write if root_cache_write is not None else 0
    )
    values = (
        raw.get("input_tokens"),
        raw.get("output_tokens"),
        cached,
        cache_write,
    )
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
        raise OpenAIResponsesError(
            "OpenAI Responses response contained no usable token usage",
            code="invalid_usage",
        )
    input_tokens, output_tokens, cached_input_tokens, cache_write_tokens = values
    if cached_input_tokens + cache_write_tokens > input_tokens:
        raise OpenAIResponsesError(
            "OpenAI Responses cached and cache-write token usage exceeded total input",
            code="invalid_usage",
        )
    return input_tokens, output_tokens, cached_input_tokens, cache_write_tokens


def _response_error(
    response: Mapping[str, Any],
    message: str,
    *,
    code: str,
    elapsed_ms: int,
) -> OpenAIResponsesError:
    """Keep billable response metadata on typed failures without logging content."""
    try:
        (
            input_tokens,
            output_tokens,
            cached_input_tokens,
            cache_write_tokens,
        ) = response_usage(response)
    except OpenAIResponsesError:
        input_tokens = output_tokens = cached_input_tokens = cache_write_tokens = 0
    response_id = response.get("id")
    model = response.get("model")
    return OpenAIResponsesError(
        message,
        code=code,
        response_id=response_id if isinstance(response_id, str) else "",
        model=model if isinstance(model, str) else "",
        elapsed_ms=elapsed_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_write_tokens=cache_write_tokens,
    )


def parse_response(
    response: Mapping[str, Any], *, elapsed_ms: int = 0
) -> OpenAIResponsesResult:
    """Parse one raw Responses payload without applying scoring semantics."""
    status = response.get("status")
    if status != "completed":
        details = response.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, dict) else None
        suffix = f": {reason}" if reason else ""
        code = "incomplete" if status == "incomplete" else "non_completed"
        label = "incomplete" if status == "incomplete" else "not completed"
        raise _response_error(
            response,
            f"OpenAI Responses response was {label}{suffix}",
            code=code,
            elapsed_ms=elapsed_ms,
        )

    try:
        data = json.loads(_output_text(response, elapsed_ms=elapsed_ms))
    except json.JSONDecodeError as exc:
        raise _response_error(
            response,
            "OpenAI Responses structured output was not valid JSON",
            code="invalid_structured_json",
            elapsed_ms=elapsed_ms,
        ) from exc
    if not isinstance(data, dict):
        raise _response_error(
            response,
            "OpenAI Responses structured output was not an object",
            code="invalid_structured_json",
            elapsed_ms=elapsed_ms,
        )

    try:
        (
            input_tokens,
            output_tokens,
            cached_input_tokens,
            cache_write_tokens,
        ) = response_usage(response)
    except OpenAIResponsesError as exc:
        response_id = response.get("id")
        model = response.get("model")
        raise OpenAIResponsesError(
            str(exc),
            code=exc.code,
            response_id=response_id if isinstance(response_id, str) else "",
            model=model if isinstance(model, str) else "",
            elapsed_ms=elapsed_ms,
        ) from exc
    response_id = response.get("id")
    model = response.get("model")
    if input_tokens == 0 or output_tokens == 0:
        raise OpenAIResponsesError(
            "OpenAI Responses completed response reported unusable zero token usage",
            code="invalid_usage",
            response_id=response_id if isinstance(response_id, str) else "",
            model=model if isinstance(model, str) else "",
            elapsed_ms=elapsed_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_write_tokens=cache_write_tokens,
        )
    if not isinstance(response_id, str) or not response_id.strip():
        raise OpenAIResponsesError(
            "OpenAI Responses response omitted its request identifier",
            code="missing_metadata",
            model=model if isinstance(model, str) else "",
            elapsed_ms=elapsed_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_write_tokens=cache_write_tokens,
        )
    if not isinstance(model, str) or not model.strip():
        raise OpenAIResponsesError(
            "OpenAI Responses response omitted its model identifier",
            code="missing_metadata",
            response_id=response_id,
            elapsed_ms=elapsed_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_write_tokens=cache_write_tokens,
        )
    return OpenAIResponsesResult(
        data=data,
        response_id=response_id,
        model=model,
        elapsed_ms=elapsed_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_write_tokens=cache_write_tokens,
    )


async def complete(
    completion: Mapping[str, Any],
    *,
    api_key: str,
    schema_name: str,
    safety_identifier: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> OpenAIResponsesResult:
    """Send exactly one non-retried Responses request and parse its result."""
    request = response_request(
        completion,
        schema_name=schema_name,
        safety_identifier=safety_identifier,
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    active_client = client or _client()
    started = time.monotonic()
    try:
        response = await active_client.post(RESPONSES_URL, headers=headers, json=request)
    except httpx.TimeoutException as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        raise OpenAIResponsesError(
            "OpenAI Responses request timed out", code="timeout", elapsed_ms=elapsed_ms
        ) from exc
    except httpx.HTTPError as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        raise OpenAIResponsesError(
            f"OpenAI Responses transport failed: {type(exc).__name__}",
            code="transport_error",
            elapsed_ms=elapsed_ms,
        ) from exc

    elapsed_ms = int((time.monotonic() - started) * 1000)
    if not response.is_success:
        raise _http_error(response, elapsed_ms=elapsed_ms)
    try:
        payload = response.json()
    except ValueError as exc:
        raise OpenAIResponsesError(
            "OpenAI Responses response body was not valid JSON",
            code="invalid_response_body",
            response_id=response.headers.get("x-request-id", ""),
            elapsed_ms=elapsed_ms,
        ) from exc
    if not isinstance(payload, dict):
        raise OpenAIResponsesError(
            "OpenAI Responses response body was not an object",
            code="invalid_response_body",
            response_id=response.headers.get("x-request-id", ""),
            elapsed_ms=elapsed_ms,
        )
    result = parse_response(payload, elapsed_ms=elapsed_ms)
    expected_model = completion.get("model")
    if not isinstance(expected_model, str) or result.model != expected_model:
        raise OpenAIResponsesError(
            "OpenAI Responses returned a model outside the qualified snapshot",
            code="model_mismatch",
            response_id=result.response_id,
            model=result.model,
            elapsed_ms=result.elapsed_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cached_input_tokens=result.cached_input_tokens,
            cache_write_tokens=result.cache_write_tokens,
        )
    return result
