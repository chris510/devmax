import json
from decimal import Decimal

import httpx
import pytest

from app.services import llm
from scripts import openai_bakeoff
from scripts.effort_sweep_support import rate_for_model
from scripts.openai_eval_support import (
    INPUT_FRAMING_ALLOWANCE,
    complete,
    conservative_input_bound,
    parse_response,
    response_request,
)


def scoring_completion() -> dict:
    return {
        "model": "gpt-5.6-luna",
        "effort": "low",
        "rubric": "Grade the answer.",
        "user_content": "QUESTION: Why?\nANSWER: Because.",
        "schema": llm.SCORE_SCHEMA,
        "max_tokens": 2048,
        "provider": "openai-responses",
    }


def response_payload(*, text: str) -> dict:
    return {
        "id": "resp_test",
        "model": "gpt-5.6-luna",
        "output": [
            {
                "type": "reasoning",
                "summary": [],
            },
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            },
        ],
        "usage": {
            "input_tokens": 321,
            "output_tokens": 87,
            "input_tokens_details": {"cached_tokens": 12},
            "output_tokens_details": {"reasoning_tokens": 40},
        },
    }


def test_request_preserves_prompt_schema_effort_and_hard_output_cap() -> None:
    request = response_request(scoring_completion(), kind="scoring")

    assert request["model"] == "gpt-5.6-luna"
    assert request["instructions"] == "Grade the answer."
    assert request["input"].startswith("QUESTION")
    assert request["reasoning"] == {"effort": "low"}
    assert request["max_output_tokens"] == 2048
    assert request["store"] is False
    assert request["text"]["format"] == {
        "type": "json_schema",
        "name": "devmax_scoring",
        "strict": True,
        "schema": llm.SCORE_SCHEMA,
    }


def test_local_input_bound_is_above_every_visible_request_byte() -> None:
    request = response_request(scoring_completion(), kind="scoring")
    visible = json.dumps(
        {
            "instructions": request["instructions"],
            "input": request["input"],
            "text": request["text"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    assert conservative_input_bound(request) == len(visible) + INPUT_FRAMING_ALLOWANCE


def test_openai_luna_rate_is_versioned_in_the_shared_guard() -> None:
    rate = rate_for_model("gpt-5.6-luna")

    assert rate.input_per_million == Decimal("0.2")
    assert rate.output_per_million == Decimal("1.2")
    assert rate.label == "published standard rate"


def test_response_parser_uses_total_output_tokens_including_reasoning() -> None:
    raw = response_payload(text='{"accuracy":5,"mastery_summary":"coached"}')

    parsed = parse_response(raw)

    assert parsed.data == {"accuracy": 5, "mastery_summary": "coached"}
    assert parsed.usage.input_tokens == 321
    assert parsed.usage.output_tokens == 87
    assert parsed.usage.cache_read_tokens == 12
    assert parsed.response_id == "resp_test"
    assert parsed.elapsed_ms == 0


@pytest.mark.anyio
async def test_live_adapter_sends_one_non_retried_responses_call() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=response_payload(
                text=(
                    '{"accuracy":5,"depth":4,"boundaries":4,'
                    '"feedback":"boundary","follow_up_question":"One more — why?",'
                    '"mastery_summary":"solid"}'
                )
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await complete(
            response_request(scoring_completion(), kind="scoring"),
            api_key="test-key",
            client=client,
        )

    assert result.data["accuracy"] == 5
    assert result.elapsed_ms >= 0
    assert len(seen) == 1
    assert seen[0].headers["authorization"] == "Bearer test-key"
    assert json.loads(seen[0].content)["max_output_tokens"] == 2048


def write_scoring_case(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            [
                {
                    "name": "one scoring case",
                    "topic": "Topic",
                    "question": "Question?",
                    "answer": "Answer.",
                    "expected_score": 5,
                    "expected_accuracy": 5,
                    "expected_depth": 4,
                    "expected_boundaries": 4,
                }
            ]
        )
    )
    return path


@pytest.mark.anyio
async def test_dry_run_needs_no_openai_key_or_network(monkeypatch, tmp_path, capsys) -> None:
    cases = write_scoring_case(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        openai_bakeoff.sys,
        "argv",
        ["openai_bakeoff.py", "scoring", str(cases), "--dry-run"],
    )

    assert await openai_bakeoff.main() == 0
    output = capsys.readouterr().out
    assert "new paid calls     1" in output
    assert "no paid Responses calls were made" in output


@pytest.mark.anyio
async def test_paid_run_stops_after_preflight_when_api_key_is_missing(
    monkeypatch, tmp_path, capsys
) -> None:
    cases = write_scoring_case(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        openai_bakeoff.sys,
        "argv",
        [
            "openai_bakeoff.py",
            "scoring",
            str(cases),
            "--max-cost-usd",
            "1",
        ],
    )

    assert await openai_bakeoff.main() == 1
    assert "ChatGPT credits cannot authorize API calls" in capsys.readouterr().err


def test_eval_key_can_load_from_local_dotenv(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=test-from-dotenv\n")

    assert openai_bakeoff.EvalSettings().openai_api_key == "test-from-dotenv"


def test_output_cap_changes_the_resume_fingerprint() -> None:
    case = {
        "name": "one",
        "topic": "Topic",
        "question": "Question?",
        "answer": "Answer.",
    }
    first = openai_bakeoff.prepare_cases(
        [case],
        kind="scoring",
        levels=["low"],
        model="gpt-5.6-luna",
        max_output_tokens=1024,
    )[0]
    second = openai_bakeoff.prepare_cases(
        [case],
        kind="scoring",
        levels=["low"],
        model="gpt-5.6-luna",
        max_output_tokens=2048,
    )[0]

    assert first.fingerprint != second.fingerprint
