"""Focused transport and parsing tests for the production-neutral Responses adapter."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.services import openai_responses

COMPLETION = {
    "model": "gpt-test",
    "effort": "low",
    "rubric": "Grade only against the supplied authority.",
    "user_content": "QUESTION: Why?\nANSWER: Because.",
    "schema": {
        "type": "object",
        "properties": {"recall_score": {"type": "integer"}},
        "required": ["recall_score"],
        "additionalProperties": False,
    },
    "max_tokens": 2048,
    # Existing prepared V2 calls carry this, but it is a transport policy rather
    # than a Responses request field and must not be forwarded.
    "retry": False,
}


def response_payload(
    data: dict[str, Any] | str = '{"recall_score":4}',
    *,
    status: str = "completed",
) -> dict[str, Any]:
    text = data if isinstance(data, str) else json.dumps(data)
    return {
        "id": "resp_123",
        "model": "gpt-test-2026-01-01",
        "status": status,
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        "usage": {
            "input_tokens": 101,
            "output_tokens": 17,
            "input_tokens_details": {"cached_tokens": 40},
            "cache_write_tokens": 15,
        },
    }


def test_response_request_translates_the_prepared_completion() -> None:
    assert openai_responses.response_request(
        COMPLETION,
        schema_name="devmax_score_v2",
        safety_identifier="safe-user-hash",
    ) == {
        "model": "gpt-test",
        "instructions": COMPLETION["rubric"],
        "input": COMPLETION["user_content"],
        "max_output_tokens": 2048,
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "devmax_score_v2",
                "strict": True,
                "schema": COMPLETION["schema"],
            }
        },
        "reasoning": {"effort": "low"},
        "safety_identifier": "safe-user-hash",
    }


def test_response_request_omits_optional_fields_when_unset() -> None:
    completion = {**COMPLETION, "effort": None}
    request = openai_responses.response_request(
        completion,
        schema_name="devmax_score_v2",
    )

    assert "reasoning" not in request
    assert "safety_identifier" not in request


async def test_complete_sends_once_and_returns_data_and_usage() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = response_payload()
        payload["model"] = COMPLETION["model"]
        return httpx.Response(200, json=payload, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await openai_responses.complete(
            COMPLETION,
            api_key="test-key",
            schema_name="devmax_score_v2",
            safety_identifier="safe-user-hash",
            client=client,
        )

    assert len(requests) == 1
    request = requests[0]
    assert request.url == openai_responses.RESPONSES_URL
    assert request.headers["Authorization"] == "Bearer test-key"
    assert json.loads(request.content) == openai_responses.response_request(
        COMPLETION,
        schema_name="devmax_score_v2",
        safety_identifier="safe-user-hash",
    )
    assert result.data == {"recall_score": 4}
    assert result.response_id == "resp_123"
    assert result.model == "gpt-test"
    assert result.elapsed_ms >= 0
    assert result.input_tokens == 101
    assert result.output_tokens == 17
    assert result.cached_input_tokens == 40
    assert result.cache_write_tokens == 15


async def test_complete_rejects_an_unqualified_returned_model_with_billable_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_payload(), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(openai_responses.OpenAIResponsesError) as caught:
            await openai_responses.complete(
                COMPLETION,
                api_key="test-key",
                schema_name="devmax_score_v2",
                client=client,
            )

    assert caught.value.code == "model_mismatch"
    assert caught.value.response_id == "resp_123"
    assert caught.value.model == "gpt-test-2026-01-01"
    assert caught.value.input_tokens == 101
    assert caught.value.output_tokens == 17
    assert caught.value.cached_input_tokens == 40
    assert caught.value.cache_write_tokens == 15


async def test_production_client_is_cached() -> None:
    openai_responses._client.cache_clear()
    first = openai_responses._client()
    second = openai_responses._client()
    try:
        assert first is second
    finally:
        await first.aclose()
        openai_responses._client.cache_clear()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"error": {"message": "invalid model"}}, "invalid model"),
        ("not-json", "Bad Request"),
    ],
)
async def test_http_errors_are_explicit(body: Any, expected: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, dict):
            return httpx.Response(400, json=body, request=request)
        return httpx.Response(400, text=body, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(openai_responses.OpenAIResponsesError, match=expected):
            await openai_responses.complete(
                COMPLETION,
                api_key="test-key",
                schema_name="devmax_score_v2",
                client=client,
            )


async def test_billable_http_failure_preserves_request_model_usage_and_status() -> None:
    body = response_payload()
    body["error"] = {"message": "rate limited"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json=body,
            headers={"x-request-id": "header-request-id"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(openai_responses.OpenAIResponsesError) as caught:
            await openai_responses.complete(
                COMPLETION,
                api_key="test-key",
                schema_name="devmax_score_v2",
                client=client,
            )

    assert caught.value.code == "http_429"
    assert caught.value.response_id == "resp_123"
    assert caught.value.model == "gpt-test-2026-01-01"
    assert caught.value.elapsed_ms >= 0
    assert caught.value.input_tokens == 101
    assert caught.value.output_tokens == 17
    assert caught.value.cached_input_tokens == 40
    assert caught.value.cache_write_tokens == 15


async def test_http_failure_uses_header_request_id_when_body_has_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"error": {"message": "unavailable"}},
            headers={"x-request-id": "header-request-id"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(openai_responses.OpenAIResponsesError) as caught:
            await openai_responses.complete(
                COMPLETION,
                api_key="test-key",
                schema_name="devmax_score_v2",
                client=client,
            )

    assert caught.value.code == "http_503"
    assert caught.value.response_id == "header-request-id"


async def test_timeout_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("too slow", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(
            openai_responses.OpenAIResponsesError, match="timed out"
        ) as caught:
            await openai_responses.complete(
                COMPLETION,
                api_key="test-key",
                schema_name="devmax_score_v2",
                client=client,
            )

    assert calls == 1
    assert caught.value.code == "timeout"


def test_refusal_is_explicit() -> None:
    payload = response_payload()
    payload["output"][0]["content"] = [
        {"type": "refusal", "refusal": "I cannot help with that."}
    ]

    with pytest.raises(
        openai_responses.OpenAIResponsesError, match="refused"
    ) as caught:
        openai_responses.parse_response(payload)
    assert caught.value.code == "refusal"


def test_refusal_wins_even_if_output_text_appears_first() -> None:
    payload = response_payload()
    payload["output"][0]["content"].append(
        {"type": "refusal", "refusal": "I cannot help with that."}
    )

    with pytest.raises(openai_responses.OpenAIResponsesError) as caught:
        openai_responses.parse_response(payload)

    assert caught.value.code == "refusal"


def test_incomplete_response_reports_the_reason() -> None:
    payload = response_payload(status="incomplete")
    payload["incomplete_details"] = {"reason": "max_output_tokens"}

    with pytest.raises(
        openai_responses.OpenAIResponsesError,
        match="incomplete: max_output_tokens",
    ) as caught:
        openai_responses.parse_response(payload, elapsed_ms=37)
    assert caught.value.code == "incomplete"
    assert caught.value.response_id == "resp_123"
    assert caught.value.model == "gpt-test-2026-01-01"
    assert caught.value.elapsed_ms == 37
    assert caught.value.input_tokens == 101
    assert caught.value.output_tokens == 17
    assert caught.value.cached_input_tokens == 40
    assert caught.value.cache_write_tokens == 15


@pytest.mark.parametrize("status", [None, "failed", "cancelled"])
def test_every_non_completed_status_fails_closed(status: str | None) -> None:
    payload = response_payload()
    payload["status"] = status

    with pytest.raises(openai_responses.OpenAIResponsesError) as caught:
        openai_responses.parse_response(payload)

    assert caught.value.code == "non_completed"


def test_non_json_structured_output_is_explicit() -> None:
    with pytest.raises(openai_responses.OpenAIResponsesError, match="not valid JSON"):
        openai_responses.parse_response(response_payload("not json"))


def test_missing_output_is_explicit() -> None:
    payload = response_payload()
    payload["output"] = None

    with pytest.raises(openai_responses.OpenAIResponsesError, match="no output text"):
        openai_responses.parse_response(payload)


async def test_non_json_http_response_is_explicit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(
            openai_responses.OpenAIResponsesError,
            match="response body was not valid JSON",
        ):
            await openai_responses.complete(
                COMPLETION,
                api_key="test-key",
                schema_name="devmax_score_v2",
                client=client,
            )


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"input_tokens": 10},
        {"input_tokens": "10", "output_tokens": 2},
        {"input_tokens": 10, "output_tokens": 2, "input_tokens_details": []},
        {
            "input_tokens": 10,
            "output_tokens": 2,
            "input_tokens_details": {"cached_tokens": 11},
        },
        {
            "input_tokens": 10,
            "output_tokens": 2,
            "input_tokens_details": {"cached_tokens": 6},
            "cache_write_tokens": 5,
        },
        {"input_tokens": 0, "output_tokens": 2},
        {"input_tokens": 10, "output_tokens": 0},
    ],
)
def test_missing_or_malformed_usage_is_explicit(usage: Any) -> None:
    payload = response_payload()
    payload["usage"] = usage

    with pytest.raises(openai_responses.OpenAIResponsesError, match="token usage"):
        openai_responses.parse_response(payload)


def test_missing_cached_usage_defaults_to_zero() -> None:
    payload = response_payload()
    payload["usage"].pop("input_tokens_details")

    result = openai_responses.parse_response(payload)

    assert result.cached_input_tokens == 0
    assert result.cache_write_tokens == 15


def test_nested_cache_write_usage_is_supported() -> None:
    payload = response_payload()
    payload["usage"].pop("cache_write_tokens")
    payload["usage"]["input_tokens_details"]["cache_write_tokens"] = 15

    result = openai_responses.parse_response(payload)

    assert result.cached_input_tokens == 40
    assert result.cache_write_tokens == 15


def test_conflicting_cache_write_usage_is_rejected() -> None:
    payload = response_payload()
    payload["usage"]["input_tokens_details"]["cache_write_tokens"] = 14

    with pytest.raises(
        openai_responses.OpenAIResponsesError, match="conflicting token usage"
    ):
        openai_responses.parse_response(payload)


@pytest.mark.parametrize("field", ["id", "model"])
def test_missing_response_metadata_is_explicit(field: str) -> None:
    payload = response_payload()
    payload[field] = ""

    with pytest.raises(openai_responses.OpenAIResponsesError) as caught:
        openai_responses.parse_response(payload)

    assert caught.value.code == "missing_metadata"
