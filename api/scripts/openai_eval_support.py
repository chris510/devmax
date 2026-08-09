"""OpenAI Responses API adapter for provider bake-offs only."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from scripts.effort_sweep_support import Usage

RESPONSES_URL = "https://api.openai.com/v1/responses"
REQUEST_TIMEOUT_SECONDS = 45.0
INPUT_FRAMING_ALLOWANCE = 2048


class OpenAIEvalError(RuntimeError):
    """Raised when a bake-off response cannot be used as evaluation evidence."""


@dataclass(frozen=True)
class OpenAIEvalResponse:
    data: dict[str, Any]
    usage: Usage
    response_id: str
    model: str
    elapsed_ms: int = 0


def response_request(completion: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """Translate one production prompt into the current Responses API shape."""
    request: dict[str, Any] = {
        "model": completion["model"],
        "instructions": completion["rubric"],
        "input": completion["user_content"],
        "max_output_tokens": completion["max_tokens"],
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": f"devmax_{kind}",
                "strict": True,
                "schema": completion["schema"],
            }
        },
    }
    if completion.get("effort") is not None:
        request["reasoning"] = {"effort": completion["effort"]}
    return request


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


def _output_text(response: dict[str, Any]) -> str:
    for item in response.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") == "refusal":
                raise OpenAIEvalError(
                    f"OpenAI refused the evaluation: {content.get('refusal', '')}"
                )
            if content.get("type") == "output_text":
                return str(content.get("text", ""))
    details = response.get("incomplete_details")
    suffix = f" ({details})" if details else ""
    raise OpenAIEvalError(f"OpenAI response contained no output text{suffix}")


def parse_response(response: dict[str, Any], *, elapsed_ms: int = 0) -> OpenAIEvalResponse:
    """Extract strict JSON and billable usage from a raw Responses result."""
    try:
        data = json.loads(_output_text(response))
    except json.JSONDecodeError as exc:
        raise OpenAIEvalError("OpenAI returned output that was not valid JSON") from exc
    if not isinstance(data, dict):
        raise OpenAIEvalError("OpenAI structured output was not an object")

    raw_usage = response.get("usage") or {}
    input_details = raw_usage.get("input_tokens_details") or {}
    try:
        usage = Usage(
            input_tokens=int(raw_usage["input_tokens"]),
            output_tokens=int(raw_usage["output_tokens"]),
            cache_read_tokens=int(input_details.get("cached_tokens", 0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise OpenAIEvalError("OpenAI response contained no usable token usage") from exc

    return OpenAIEvalResponse(
        data=data,
        usage=usage,
        response_id=str(response.get("id", "")),
        model=str(response.get("model", "")),
        elapsed_ms=elapsed_ms,
    )


def _api_error(response: httpx.Response) -> OpenAIEvalError:
    try:
        body = response.json()
        message = body.get("error", {}).get("message")
    except (ValueError, AttributeError):
        message = None
    detail = str(message or response.reason_phrase)
    return OpenAIEvalError(f"OpenAI API returned HTTP {response.status_code}: {detail}")


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
            raise OpenAIEvalError(f"OpenAI request failed: {exc}") from exc
        if not response.is_success:
            raise _api_error(response)
        return parse_response(
            response.json(), elapsed_ms=int((time.monotonic() - started) * 1000)
        )

    if client is not None:
        return await send(client)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as active_client:
        return await send(active_client)
