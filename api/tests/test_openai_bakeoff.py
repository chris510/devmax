import json
from decimal import Decimal

import httpx
import pytest

from app.services import llm
from scripts import openai_bakeoff, structured_evidence_eval
from scripts.effort_sweep_support import (
    JsonlRecorder,
    Usage,
    make_result_record,
    rate_for_model,
)
from scripts.openai_eval_support import (
    INPUT_FRAMING_ALLOWANCE,
    INPUT_TOKENS_URL,
    complete,
    conservative_input_bound,
    count_input_tokens,
    input_token_count_request,
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


def test_token_count_request_excludes_generation_only_fields() -> None:
    request = response_request(scoring_completion(), kind="scoring")

    counted = input_token_count_request(request)

    assert counted == {
        "model": request["model"],
        "instructions": request["instructions"],
        "input": request["input"],
        "reasoning": request["reasoning"],
        "text": request["text"],
    }
    assert "max_output_tokens" not in counted
    assert "store" not in counted


@pytest.mark.anyio
async def test_exact_input_counter_sends_one_non_generating_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"object": "response.input_tokens", "input_tokens": 987},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        count = await count_input_tokens(
            response_request(scoring_completion(), kind="scoring"),
            api_key="test-key",
            client=client,
        )

    assert count == 987
    assert len(seen) == 1
    assert str(seen[0].url) == INPUT_TOKENS_URL
    assert seen[0].headers["authorization"] == "Bearer test-key"
    payload = json.loads(seen[0].content)
    assert "max_output_tokens" not in payload
    assert "store" not in payload


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


def write_evidence_case(tmp_path):
    path = tmp_path / "evidence-cases.json"
    path.write_text(
        json.dumps(
            [
                {
                    "name": "selection only",
                    "topic": "Decision-driven estimation",
                    "answer": "If one heap fits, keep it; otherwise shard.",
                    "expected_depth": 1,
                    "expected_boundaries": 1,
                    "review_status": "approved",
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
async def test_evidence_dry_run_needs_no_key_or_network(
    monkeypatch, tmp_path, capsys
) -> None:
    cases = write_evidence_case(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        openai_bakeoff.sys,
        "argv",
        [
            "openai_bakeoff.py",
            "evidence",
            str(cases),
            "--model",
            "gpt-5.6-terra",
            "--levels",
            "medium",
            "--dry-run",
        ],
    )

    assert await openai_bakeoff.main() == 0
    output = capsys.readouterr().out
    assert "selected calls     1" in output
    assert "new paid calls     1" in output
    assert "no paid Responses calls were made" in output


@pytest.mark.anyio
async def test_evidence_replay_is_keyless_and_enforces_gate(
    monkeypatch, tmp_path, capsys
) -> None:
    cases_path = write_evidence_case(tmp_path)
    case = json.loads(cases_path.read_text())[0]
    prepared = openai_bakeoff.prepare_cases(
        [case],
        kind="evidence",
        levels=["medium"],
        model="gpt-5.6-terra",
        max_output_tokens=512,
    )[0]
    result = structured_evidence_eval.parse_result(
        case,
        {
            "depth": {
                "choice_or_target_span": "",
                "cost_or_tension_span": "",
                "connection_span": "",
            },
            "boundaries": {
                "trigger_or_mistake_span": "",
                "harm_or_incorrect_behavior_span": "",
                "connection_span": "",
            },
        },
        usage=Usage(),
    )
    resume = tmp_path / "evidence.jsonl"
    with JsonlRecorder(resume) as recorder:
        recorder.append(
            make_result_record(
                prepared,
                model="gpt-5.6-terra",
                result=structured_evidence_eval.result_payload(result),
                usage=Usage(),
            )
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        openai_bakeoff.sys,
        "argv",
        [
            "openai_bakeoff.py",
            "evidence",
            str(cases_path),
            "--model",
            "gpt-5.6-terra",
            "--levels",
            "medium",
            "--resume",
            str(resume),
            "--output",
            str(tmp_path / "replay.jsonl"),
            "--enforce-evidence-gate",
        ],
    )

    assert await openai_bakeoff.main() == 0
    output = capsys.readouterr().out
    assert "resumed calls      1" in output
    assert "new paid calls     0" in output
    assert "structured-evidence gate passed" in output


@pytest.mark.anyio
async def test_exact_count_dry_run_uses_provider_counts_without_generation(
    monkeypatch, tmp_path, capsys
) -> None:
    cases = write_scoring_case(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    counted: list[dict] = []

    async def exact_count(request, *, api_key, client):
        counted.append(request)
        assert api_key == "test-key"
        return 321

    async def forbidden_complete(*_args, **_kwargs):
        raise AssertionError("dry-run reached a paid Responses call")

    monkeypatch.setattr(openai_bakeoff, "count_input_tokens", exact_count)
    monkeypatch.setattr(openai_bakeoff, "complete", forbidden_complete)
    monkeypatch.setattr(
        openai_bakeoff.sys,
        "argv",
        [
            "openai_bakeoff.py",
            "scoring",
            str(cases),
            "--exact-input-counts",
            "--dry-run",
        ],
    )

    assert await openai_bakeoff.main() == 0
    output = capsys.readouterr().out
    assert len(counted) == 1
    assert "counted input      321" in output
    assert "OpenAI Responses input-token endpoint" in output
    assert "no paid Responses calls were made" in output


@pytest.mark.anyio
async def test_exact_count_dry_run_refuses_without_api_key(
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
            "--exact-input-counts",
            "--dry-run",
        ],
    )

    assert await openai_bakeoff.main() == 1
    assert "exact input counts transmit prepared payloads" in capsys.readouterr().err


@pytest.mark.anyio
async def test_reused_exact_total_makes_no_count_or_generation_calls(
    monkeypatch, tmp_path, capsys
) -> None:
    cases = write_scoring_case(tmp_path)
    monkeypatch.chdir(tmp_path)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("reused exact total reached an OpenAI endpoint")

    monkeypatch.setattr(openai_bakeoff, "count_input_tokens", forbidden)
    monkeypatch.setattr(openai_bakeoff, "complete", forbidden)
    monkeypatch.setattr(
        openai_bakeoff.sys,
        "argv",
        [
            "openai_bakeoff.py",
            "scoring",
            str(cases),
            "--reuse-exact-input-total",
            "321",
            "--dry-run",
        ],
    )

    assert await openai_bakeoff.main() == 0
    output = capsys.readouterr().out
    assert "counted input      321" in output
    assert "reused exact input-token dry-run total" in output
    assert "no paid Responses calls were made" in output


@pytest.mark.anyio
async def test_scoring_replay_can_enforce_the_shared_reviewed_gate(
    monkeypatch, tmp_path, capsys
) -> None:
    cases = write_scoring_case(tmp_path)
    case = json.loads(cases.read_text())[0]
    prepared = openai_bakeoff.prepare_cases(
        [case],
        kind="scoring",
        levels=[openai_bakeoff.DEFAULT_EFFORT],
        model=openai_bakeoff.DEFAULT_MODEL,
        max_output_tokens=openai_bakeoff.DEFAULT_OUTPUT_CAPS["scoring"],
    )[0]
    record = make_result_record(
        prepared,
        model=openai_bakeoff.DEFAULT_MODEL,
        result={
            "expected_score": 5,
            "score": 1,
            "expected_axes": [5, 4, 4],
            "axes": [1, 0, 0],
            "feedback": "The answer was incorrect.",
        },
        usage=Usage(),
    )
    resume = tmp_path / "failed.jsonl"
    with JsonlRecorder(resume) as recorder:
        recorder.append(record)

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        openai_bakeoff.sys,
        "argv",
        [
            "openai_bakeoff.py",
            "scoring",
            str(cases),
            "--resume",
            str(resume),
            "--output",
            str(tmp_path / "replay.jsonl"),
            "--enforce-reviewed-gate",
        ],
    )

    assert await openai_bakeoff.main() == 1
    output = capsys.readouterr()
    assert "new paid calls     0" in output.out
    assert "reviewed gate failed" in output.err
    assert "false Accuracy failure" in output.err


@pytest.mark.anyio
async def test_secondary_bucket_gate_is_scoring_only(monkeypatch, tmp_path) -> None:
    cases = write_scoring_case(tmp_path)
    monkeypatch.setattr(
        openai_bakeoff.sys,
        "argv",
        [
            "openai_bakeoff.py",
            "evidence",
            str(cases),
            "--enforce-secondary-bucket-gate",
            "--dry-run",
        ],
    )

    with pytest.raises(SystemExit, match="2"):
        await openai_bakeoff.main()


@pytest.mark.anyio
async def test_scoring_replay_enforces_secondary_bucket_gate(
    monkeypatch, tmp_path, capsys
) -> None:
    cases = write_scoring_case(tmp_path)
    case = json.loads(cases.read_text())[0]
    prepared = openai_bakeoff.prepare_cases(
        [case],
        kind="scoring",
        levels=[openai_bakeoff.DEFAULT_EFFORT],
        model=openai_bakeoff.DEFAULT_MODEL,
        max_output_tokens=openai_bakeoff.DEFAULT_OUTPUT_CAPS["scoring"],
    )[0]
    record = make_result_record(
        prepared,
        model=openai_bakeoff.DEFAULT_MODEL,
        result={
            "expected_score": 3,
            "score": 4,
            "expected_axes": [5, 2, 2],
            "axes": [5, 3, 2],
            "feedback": "The answer was correct.",
        },
        usage=Usage(),
    )
    resume = tmp_path / "crossed.jsonl"
    with JsonlRecorder(resume) as recorder:
        recorder.append(record)

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        openai_bakeoff.sys,
        "argv",
        [
            "openai_bakeoff.py",
            "scoring",
            str(cases),
            "--resume",
            str(resume),
            "--output",
            str(tmp_path / "replay.jsonl"),
            "--enforce-reviewed-gate",
            "--enforce-secondary-bucket-gate",
        ],
    )

    assert await openai_bakeoff.main() == 1
    output = capsys.readouterr()
    assert "reviewed gate passed" in output.out
    assert "secondary bucket gate failed" in output.err
    assert "false Depth pass" in output.err


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


@pytest.mark.anyio
async def test_paid_run_refuses_an_explicit_candidate_before_api_key(
    monkeypatch, tmp_path
) -> None:
    cases = write_scoring_case(tmp_path)
    payload = json.loads(cases.read_text())
    payload[0]["review_status"] = "candidate"
    cases.write_text(json.dumps(payload))
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

    with pytest.raises(SystemExit, match="2"):
        await openai_bakeoff.main()


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
