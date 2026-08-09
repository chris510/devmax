import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from scripts import openai_bakeoff, openai_batch_bakeoff
from scripts.effort_sweep_support import batch_rate_for_model

API = Path(__file__).resolve().parent.parent
CANDIDATES = API / "scripts" / "grounded_effort_cases_week1_release_candidates.json"
CARDS = API / "cards.json"


def one_prepared_call():
    case = {
        "name": "one",
        "topic": "Topic",
        "question": "Why?",
        "answer": "Because.",
        "expected_score": 3,
        "expected_accuracy": 5,
        "expected_depth": 1,
        "expected_boundaries": 1,
    }
    return openai_bakeoff.prepare_cases(
        [case],
        kind="scoring",
        levels=["medium"],
        model="gpt-5.6-terra",
        max_output_tokens=2048,
    )[0]


def test_batch_rate_is_half_the_standard_terra_rate() -> None:
    standard = openai_batch_bakeoff.openai_bakeoff.rate_for_model("gpt-5.6-terra")
    batch = batch_rate_for_model("gpt-5.6-terra")

    assert (standard.input_per_million, standard.output_per_million) == (
        Decimal("2"),
        Decimal("12"),
    )
    assert (batch.input_per_million, batch.output_per_million) == (
        Decimal("1"),
        Decimal("6"),
    )


def test_batch_input_preserves_the_responses_request_and_custom_id() -> None:
    prepared = one_prepared_call()

    content, calls = openai_batch_bakeoff.build_batch_input([prepared])
    line = json.loads(content)

    assert line["custom_id"] == calls[0]["custom_id"]
    assert line["method"] == "POST"
    assert line["url"] == "/v1/responses"
    assert line["body"]["model"] == "gpt-5.6-terra"
    assert line["body"]["reasoning"] == {"effort": "medium"}
    assert line["body"]["text"]["format"]["strict"] is True
    assert line["body"]["store"] is False
    assert calls[0]["fingerprint"] == prepared.fingerprint


@pytest.mark.anyio
async def test_submit_uploads_batch_jsonl_then_creates_responses_batch() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/files":
            return httpx.Response(200, json={"id": "file_input", "purpose": "batch"})
        if request.url.path == "/v1/batches":
            return httpx.Response(200, json={"id": "batch_test", "status": "validating"})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        upload, batch = await openai_batch_bakeoff.submit_requests(
            '{"custom_id":"one"}\n',
            api_key="test-key",
            metadata={"devmax_eval": "scoring"},
            client=client,
        )

    assert upload["id"] == "file_input"
    assert batch["id"] == "batch_test"
    assert len(requests) == 2
    assert requests[0].headers["authorization"] == "Bearer test-key"
    assert b'name="purpose"' in requests[0].content
    assert b"batch" in requests[0].content
    assert b'devmax-openai-scoring.jsonl' in requests[0].content
    body = json.loads(requests[1].content)
    assert body == {
        "input_file_id": "file_input",
        "endpoint": "/v1/responses",
        "completion_window": "24h",
        "metadata": {"devmax_eval": "scoring"},
    }


def test_batch_output_is_unordered_but_must_be_complete_and_unique() -> None:
    content = "\n".join(
        [
            json.dumps({"custom_id": "two", "response": {"status_code": 200}}),
            json.dumps({"custom_id": "one", "response": {"status_code": 200}}),
        ]
    )

    parsed = openai_batch_bakeoff.parse_batch_output(content, {"one", "two"})

    assert set(parsed) == {"one", "two"}
    with pytest.raises(ValueError, match="missing 1 request"):
        openai_batch_bakeoff.parse_batch_output(
            json.dumps({"custom_id": "one"}), {"one", "two"}
        )


@pytest.mark.anyio
async def test_candidate_dry_run_is_free_and_reports_review_gate(
    monkeypatch, capsys
) -> None:
    monkeypatch.chdir(API)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        openai_batch_bakeoff.sys,
        "argv",
        [
            "openai_batch_bakeoff.py",
            "submit",
            str(CANDIDATES),
            "--grounding-manifest",
            str(CARDS),
            "--dry-run",
        ],
    )

    assert await openai_batch_bakeoff.main() == 0
    output = capsys.readouterr().out
    assert "new paid calls     42" in output
    assert "human review       42 case label(s) pending" in output
    assert "no file upload or paid Batch request was made" in output


@pytest.mark.anyio
async def test_candidate_paid_submit_is_refused_before_key_or_network(
    monkeypatch,
) -> None:
    monkeypatch.chdir(API)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        openai_batch_bakeoff.sys,
        "argv",
        [
            "openai_batch_bakeoff.py",
            "submit",
            str(CANDIDATES),
            "--grounding-manifest",
            str(CARDS),
            "--max-cost-usd",
            "1",
        ],
    )

    with pytest.raises(SystemExit, match="2"):
        await openai_batch_bakeoff.main()
