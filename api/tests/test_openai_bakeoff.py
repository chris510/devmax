import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app.services import llm, openai_responses, scoring_provider
from app.services.scoring_provider import OPENAI_V2_SCHEMA_NAME
from scripts import openai_bakeoff, structured_evidence_eval, v2_recall_eval
from scripts.effort_sweep_support import (
    JsonlRecorder,
    Usage,
    make_result_record,
    rate_for_model,
)
from scripts.openai_eval_support import (
    INPUT_FRAMING_ALLOWANCE,
    INPUT_TOKENS_URL,
    V2_EVAL_SAFETY_IDENTIFIER,
    V2_EVAL_SAFETY_IDENTIFIER_FORMAT_VERSION,
    OpenAIEvalError,
    complete,
    conservative_input_bound,
    count_input_tokens,
    input_token_count_request,
    parse_response,
    response_request,
)
from scripts.openai_eval_support import (
    actual_cost as openai_actual_cost,
)

QUALIFICATION_EXPIRES_AT = (
    datetime.now(UTC) + timedelta(days=1)
).isoformat()


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
        "status": "completed",
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
            "cache_write_tokens": 20,
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


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_count", [True, -1, "7", 1.0, None])
async def test_exact_input_counter_rejects_coercible_or_negative_counts(
    invalid_count,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"input_tokens": invalid_count})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OpenAIEvalError, match="non-negative exact integer"):
            await count_input_tokens(
                response_request(scoring_completion(), kind="scoring"),
                api_key="test-key",
                client=client,
            )


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
    assert parsed.usage.cache_write_tokens == 20
    assert parsed.response_id == "resp_test"
    assert parsed.elapsed_ms == 0


def test_response_parser_rejects_noncompleted_or_overlapping_input_usage() -> None:
    incomplete = response_payload(text='{"accuracy":5}')
    incomplete["status"] = "incomplete"
    incomplete["incomplete_details"] = {"reason": "max_output_tokens"}
    with pytest.raises(
        OpenAIEvalError, match="not completed: max_output_tokens"
    ) as caught:
        parse_response(incomplete)
    assert caught.value.response_id == "resp_test"
    assert caught.value.model == "gpt-5.6-luna"
    assert caught.value.failure_type == "non_completed"
    assert caught.value.usage.input_tokens == 321
    assert caught.value.usage.output_tokens == 87

    invalid_usage = response_payload(text='{"accuracy":5}')
    invalid_usage["usage"]["input_tokens"] = 30
    with pytest.raises(OpenAIEvalError, match="exceeded total input"):
        parse_response(invalid_usage)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", ""),
        ("id", "   "),
        ("id", None),
        ("id", 7),
        ("model", ""),
        ("model", None),
        ("model", 7),
    ],
)
def test_response_parser_requires_exact_nonempty_identity(field, value) -> None:
    raw = response_payload(text='{"accuracy":5}')
    raw[field] = value

    with pytest.raises(OpenAIEvalError, match=f"response {field}") as caught:
        parse_response(raw)

    assert caught.value.failure_type == "invalid_response_identity"


def test_openai_actual_cost_separates_uncached_cached_and_written_input() -> None:
    cost = openai_actual_cost(
        [
            {
                "usage": {
                    "input_tokens": 321,
                    "output_tokens": 87,
                    "cache_read_tokens": 12,
                    "cache_write_tokens": 20,
                }
            }
        ],
        input_per_million=Decimal("2"),
        output_per_million=Decimal("3"),
        cached_input_per_million=Decimal("0.2"),
        cache_write_per_million=Decimal("1"),
    )

    assert cost == Decimal("0.0008614")


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


def write_v2_recall_case(
    tmp_path, *, expected_recall=4, expected_flow="complete", probes=None
):
    path = tmp_path / "v2-recall-cases.json"
    case = {
        "name": "synthetic V2 Recall case",
        "topic": "Consistent hashing",
        "question": "Why does consistent hashing reduce remapping?",
        "answer": "Only keys in the moved token range change owners.",
        "answer_basis": "A node change moves only adjacent token ranges.",
        "answer_rubric": {"required_mechanism": "Only adjacent ranges move."},
        "expected_recall": expected_recall,
        "expected_flow": expected_flow,
        "review_status": "approved",
        "review_note": "Synthetic unit-test judgement; not a human case-pack label.",
    }
    if probes is not None:
        case["probes"] = probes
    path.write_text(json.dumps([case]))
    return path


def test_v2_recall_kind_uses_the_production_prompt_schema_and_cap(tmp_path) -> None:
    cases = write_v2_recall_case(tmp_path)
    case = json.loads(cases.read_text())[0]

    prepared = openai_bakeoff.prepare_cases(
        [case],
        kind="v2-recall",
        levels=["low"],
        model="gpt-5.6-luna",
        max_output_tokens=openai_bakeoff.DEFAULT_OUTPUT_CAPS["v2-recall"],
    )[0]
    request = response_request(prepared.completion, kind=prepared.kind)

    assert prepared.kind == "v2-recall"
    assert prepared.completion["rubric"] == llm.SCORING_V2_RUBRIC
    assert prepared.completion["schema"] == llm.SCORE_V2_SCHEMA
    assert prepared.completion["retry"] is False
    assert request["max_output_tokens"] == llm.SCORING_OUTPUT_TOKEN_LIMIT
    assert request["text"]["format"]["name"] == "devmax_recall_score_v2"
    assert request["text"]["format"]["schema"] == llm.SCORE_V2_SCHEMA
    assert request["safety_identifier"] == V2_EVAL_SAFETY_IDENTIFIER
    assert request == openai_responses.response_request(
        prepared.completion,
        schema_name=OPENAI_V2_SCHEMA_NAME,
        safety_identifier=V2_EVAL_SAFETY_IDENTIFIER,
    )


def test_v2_eval_production_and_fingerprint_requests_have_wire_parity(
    tmp_path,
) -> None:
    cases = write_v2_recall_case(tmp_path)
    case = json.loads(cases.read_text())[0]
    completion = openai_bakeoff.prepare_cases(
        [case],
        kind="v2-recall",
        levels=["low"],
        model="gpt-5.6-luna",
        max_output_tokens=openai_bakeoff.DEFAULT_OUTPUT_CAPS["v2-recall"],
    )[0].completion
    eval_request = response_request(completion, kind="v2-recall")
    production_request = openai_responses.response_request(
        completion,
        schema_name=OPENAI_V2_SCHEMA_NAME,
        safety_identifier="f" * 64,
    )
    fingerprint_request = scoring_provider.qualification_request(completion)

    def normalized(request: dict) -> dict:
        result = json.loads(json.dumps(request))
        result["input"] = scoring_provider.QUALIFICATION_DYNAMIC_USER_CONTENT
        result["safety_identifier"] = (
            scoring_provider.QUALIFICATION_DYNAMIC_SAFETY_IDENTIFIER
        )
        return result

    assert len(V2_EVAL_SAFETY_IDENTIFIER) == 64
    assert set(V2_EVAL_SAFETY_IDENTIFIER) <= set("0123456789abcdef")
    assert normalized(eval_request) == normalized(production_request)
    assert normalized(eval_request) == fingerprint_request


@pytest.mark.anyio
async def test_v2_recall_dry_run_needs_no_openai_key_or_network(
    monkeypatch, tmp_path, capsys
) -> None:
    cases = write_v2_recall_case(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        openai_bakeoff.sys,
        "argv",
        ["openai_bakeoff.py", "v2-recall", str(cases), "--dry-run"],
    )

    assert await openai_bakeoff.main() == 0
    output = capsys.readouterr().out
    assert "new paid calls     1" in output
    assert "qualification     low:" in output
    assert "no paid Responses calls were made" in output


@pytest.mark.anyio
async def test_v2_paid_run_flushes_manifest_and_all_failure_evidence(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(v2_recall_eval, "stage2_pack_failures", lambda _cases: [])
    cases_path = write_v2_recall_case(tmp_path)
    cases = json.loads(cases_path.read_text())
    failed = {**cases[0], "name": "synthetic V2 failure", "answer": "fail"}
    cases_path.write_text(json.dumps([cases[0], failed]))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    called: list[str] = []

    async def fake_complete(request, *, api_key, client):
        del api_key, client
        called.append(request["input"])
        if "ANSWER: fail" in request["input"]:
            raise OpenAIEvalError(
                "typed incomplete response",
                response_id="resp_failed",
                model="gpt-5.6-luna",
                elapsed_ms=14,
                usage=Usage(input_tokens=102, output_tokens=6),
                failure_type="non_completed",
            )
        return SimpleNamespace(
            data={
                "recall_score": 4,
                "feedback": "Grounded.",
                "follow_up_question": "",
                "needs_more_evidence": False,
                "mastery_summary": "stable",
            },
            usage=Usage(input_tokens=100, output_tokens=8),
            response_id="resp_success",
            model="gpt-5.6-luna",
            elapsed_ms=13,
        )

    monkeypatch.setattr(openai_bakeoff, "complete", fake_complete)
    output = tmp_path / "luna-evidence.jsonl"
    monkeypatch.setattr(
        openai_bakeoff.sys,
        "argv",
        [
            "openai_bakeoff.py",
            "v2-recall",
            str(cases_path),
            "--max-cost-usd",
            "1",
            "--input-price-per-million",
            "0.2",
            "--output-price-per-million",
            "1.2",
            "--cached-input-price-per-million",
            "0.02",
            "--cache-write-price-per-million",
            "0.2",
            "--qualification-expires-at",
            QUALIFICATION_EXPIRES_AT,
            "--output",
            str(output),
        ],
    )

    assert await openai_bakeoff.main() == 1
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(called) == 2
    assert rows[0]["record_type"] == "run_manifest"
    assert len(rows[0]["invocations"]) == 2
    assert rows[0]["safety_identifier"] == {
        "kind": "synthetic_non_user",
        "format_version": V2_EVAL_SAFETY_IDENTIFIER_FORMAT_VERSION,
        "value": V2_EVAL_SAFETY_IDENTIFIER,
    }
    assert rows[0]["preflight"]["approved_max_cost_usd"] == "1"
    assert rows[0]["preflight"]["input_count_method"] == (
        "local_utf8_byte_upper_bound"
    )
    assert set(rows[0]["preflight"]["input_counts"]) == {
        item["fingerprint"] for item in rows[0]["invocations"]
    }
    assert rows[0]["preflight"]["rates_per_million_usd"] == {
        "input": "0.2",
        "output": "1.2",
        "cached_input": "0.02",
        "cache_write": "0.2",
    }
    evidence = rows[1:]
    assert {row["evidence_outcome"] for row in evidence} == {
        "success",
        "failure",
    }
    assert {row["fingerprint"] for row in evidence} == {
        item["fingerprint"] for item in rows[0]["invocations"]
    }
    failed_row = next(row for row in evidence if row["evidence_outcome"] == "failure")
    assert failed_row["failure"]["type"] == "non_completed"
    assert failed_row["provider_response_id"] == "resp_failed"
    assert failed_row["provider_response_model"] == "gpt-5.6-luna"
    assert failed_row["provider_elapsed_ms"] == 14
    assert failed_row["usage"]["input_tokens"] == 102
    assert failed_row["usage"]["output_tokens"] == 6


@pytest.mark.anyio
async def test_paid_v2_recall_requires_explicit_cache_rates_before_network(
    monkeypatch, tmp_path, capsys
) -> None:
    cases = write_v2_recall_case(tmp_path)
    monkeypatch.setattr(v2_recall_eval, "stage2_pack_failures", lambda _cases: [])
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("missing cache rates reached an OpenAI endpoint")

    monkeypatch.setattr(openai_bakeoff, "count_input_tokens", forbidden)
    monkeypatch.setattr(openai_bakeoff, "complete", forbidden)
    monkeypatch.setattr(
        openai_bakeoff.sys,
        "argv",
        [
            "openai_bakeoff.py",
            "v2-recall",
            str(cases),
            "--max-cost-usd",
            "1",
            "--input-price-per-million",
            "0.2",
            "--output-price-per-million",
            "1.2",
        ],
    )

    with pytest.raises(SystemExit, match="2"):
        await openai_bakeoff.main()
    assert "--cached-input-price-per-million is required" in capsys.readouterr().err


@pytest.mark.anyio
async def test_paid_v2_recall_requires_explicit_base_rates_before_network(
    monkeypatch, tmp_path, capsys
) -> None:
    cases = write_v2_recall_case(tmp_path)
    monkeypatch.setattr(v2_recall_eval, "stage2_pack_failures", lambda _cases: [])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    monkeypatch.setattr(
        openai_bakeoff.sys,
        "argv",
        [
            "openai_bakeoff.py",
            "v2-recall",
            str(cases),
            "--max-cost-usd",
            "1",
            "--cached-input-price-per-million",
            "0.02",
            "--cache-write-price-per-million",
            "0.2",
        ],
    )

    with pytest.raises(SystemExit, match="2"):
        await openai_bakeoff.main()
    assert "--input-price-per-million is required" in capsys.readouterr().err


@pytest.mark.anyio
async def test_paid_v2_refuses_a_reused_total_without_per_invocation_counts(
    monkeypatch, tmp_path, capsys
) -> None:
    cases = write_v2_recall_case(tmp_path)
    monkeypatch.setattr(v2_recall_eval, "stage2_pack_failures", lambda _cases: [])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    monkeypatch.setattr(
        openai_bakeoff.sys,
        "argv",
        [
            "openai_bakeoff.py",
            "v2-recall",
            str(cases),
            "--max-cost-usd",
            "1",
            "--input-price-per-million",
            "0.2",
            "--output-price-per-million",
            "1.2",
            "--cached-input-price-per-million",
            "0.02",
            "--cache-write-price-per-million",
            "0.2",
            "--reuse-exact-input-total",
            "100",
        ],
    )

    with pytest.raises(SystemExit, match="2"):
        await openai_bakeoff.main()
    assert "cannot attest per-invocation counts" in capsys.readouterr().err


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


@pytest.mark.anyio
async def test_v2_recall_paid_run_requires_explicit_complete_human_labels(
    monkeypatch, tmp_path
) -> None:
    cases = write_v2_recall_case(tmp_path)
    payload = json.loads(cases.read_text())
    payload[0].pop("review_note")
    cases.write_text(json.dumps(payload))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        openai_bakeoff.sys,
        "argv",
        [
            "openai_bakeoff.py",
            "v2-recall",
            str(cases),
            "--max-cost-usd",
            "1",
        ],
    )

    with pytest.raises(SystemExit, match="2"):
        await openai_bakeoff.main()


@pytest.mark.anyio
async def test_v2_recall_replay_enforces_terminal_scheduler_bucket_gate(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(v2_recall_eval, "stage2_pack_failures", lambda _cases: [])
    probes = [
        {"question": "Probe 1?", "answer": "Answer 1."},
        {"question": "Probe 2?", "answer": "Answer 2."},
    ]
    cases_path = write_v2_recall_case(
        tmp_path, expected_recall=2, expected_flow="complete", probes=probes
    )
    case = json.loads(cases_path.read_text())[0]
    prepared = openai_bakeoff.prepare_cases(
        [case],
        kind="v2-recall",
        levels=[openai_bakeoff.DEFAULT_EFFORT],
        model=openai_bakeoff.DEFAULT_MODEL,
        max_output_tokens=openai_bakeoff.DEFAULT_OUTPUT_CAPS["v2-recall"],
    )[0]
    result = v2_recall_eval.parse_result(
        prepared,
        {
            "recall_score": 3,
            "feedback": "The essential account is grounded.",
            "follow_up_question": "",
            "needs_more_evidence": False,
            "mastery_summary": "recovered the essential account after probes",
        },
        Usage(),
    )
    resume = tmp_path / "v2-crossed.jsonl"
    with JsonlRecorder(resume) as recorder:
        record = make_result_record(
                prepared,
                model=openai_bakeoff.DEFAULT_MODEL,
                result=v2_recall_eval.result_payload(result),
                usage=Usage(),
            )
        record["stage2_pack_fingerprint"] = (
            v2_recall_eval.stage2_pack_fingerprint([case])
        )
        record["qualification_fingerprint"] = (
            v2_recall_eval.deployment_fingerprint(prepared.completion)
        )
        recorder.append(record)

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        openai_bakeoff.sys,
        "argv",
        [
            "openai_bakeoff.py",
            "v2-recall",
            str(cases_path),
            "--resume",
            str(resume),
            "--output",
            str(tmp_path / "v2-replay.jsonl"),
            "--enforce-v2-recall-gate",
        ],
    )

    assert await openai_bakeoff.main() == 1
    output = capsys.readouterr()
    assert "new paid calls     0" in output.out
    assert "V2 Recall gate failed" in output.err
    assert "product decisions" in output.err
    assert "'scheduler': 'good'" in output.err
    assert "'scheduler': 'again'" in output.err


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
