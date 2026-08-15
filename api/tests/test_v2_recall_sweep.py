import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.services import llm
from app.services.scoring_provider import ProviderCallTrace
from scripts import v2_recall_eval, v2_recall_sweep
from scripts.effort_sweep_support import JsonlRecorder, Usage, make_result_record

QUALIFICATION_EXPIRES_AT = (
    datetime.now(UTC) + timedelta(days=1)
).isoformat()


def write_case(tmp_path, *, review_status="approved"):
    path = tmp_path / "v2-cases.json"
    path.write_text(
        json.dumps(
            [
                {
                    "name": "synthetic complete Recall case",
                    "topic": "Consistent hashing",
                    "question": "Why does consistent hashing reduce remapping?",
                    "answer": "Only the neighboring token range moves owners.",
                    "answer_basis": "Membership changes move adjacent token ranges.",
                    "answer_rubric": {
                        "required_mechanism": "Only adjacent token ranges move."
                    },
                    "expected_recall": 4,
                    "expected_flow": "complete",
                    "review_status": review_status,
                    "review_note": (
                        "Synthetic test fixture only; not an approved human case-pack label."
                    ),
                    "tags": ["smoke"],
                }
            ]
        )
    )
    return path


def forbid_provider_calls(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("credential-free path reached a provider endpoint")

    monkeypatch.setattr(v2_recall_sweep, "count_prepared_calls", forbidden)
    monkeypatch.setattr(v2_recall_sweep.llm, "_complete", forbidden)


def test_claude_actual_cost_accounts_for_both_cache_buckets() -> None:
    cost = v2_recall_sweep.claude_actual_cost(
        [
            {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "cache_read_tokens": 20,
                    "cache_write_tokens": 5,
                }
            }
        ],
        input_per_million=Decimal("2"),
        output_per_million=Decimal("3"),
        cache_read_per_million=Decimal("0.2"),
        cache_write_per_million=Decimal("2.5"),
    )

    assert cost == Decimal("0.0002465")


def test_preflight_output_ceiling_tracks_the_production_scoring_limit() -> None:
    assert (
        v2_recall_sweep.FALLBACK_OUTPUT_TOKENS
        == llm.SCORING_OUTPUT_TOKEN_LIMIT
        == 8_000
    )


@pytest.mark.anyio
async def test_keyless_dry_run_uses_local_bound_and_no_network(
    monkeypatch, tmp_path, capsys
) -> None:
    cases = write_case(tmp_path)
    settings = v2_recall_sweep.get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    forbid_provider_calls(monkeypatch)
    monkeypatch.setattr(
        v2_recall_sweep.sys,
        "argv",
        ["v2_recall_sweep.py", str(cases), "--tag", "smoke", "--dry-run"],
    )

    assert await v2_recall_sweep.main() == 0
    output = capsys.readouterr().out
    assert "new paid calls     1" in output
    assert "bounded input" in output
    assert "local UTF-8 byte ceiling" in output
    assert "no paid Message calls were made" in output


@pytest.mark.anyio
async def test_keyed_dry_run_stays_local_without_explicit_count_permission(
    monkeypatch, tmp_path, capsys
) -> None:
    cases = write_case(tmp_path)
    settings = v2_recall_sweep.get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    forbid_provider_calls(monkeypatch)
    monkeypatch.setattr(
        v2_recall_sweep.sys,
        "argv",
        ["v2_recall_sweep.py", str(cases), "--dry-run"],
    )

    assert await v2_recall_sweep.main() == 0
    output = capsys.readouterr().out
    assert "bounded input" in output
    assert "local UTF-8 byte ceiling" in output


@pytest.mark.anyio
async def test_explicit_keyed_dry_run_uses_exact_count_but_never_generation(
    monkeypatch, tmp_path, capsys
) -> None:
    cases = write_case(tmp_path)
    settings = v2_recall_sweep.get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    counted = []

    async def count(prepared, *, concurrency, client):
        counted.extend(prepared)
        assert concurrency == 4
        assert client is not None
        return {call.fingerprint: 321 for call in prepared}

    async def forbidden_complete(**_kwargs):
        raise AssertionError("dry-run reached a paid Message call")

    monkeypatch.setattr(v2_recall_sweep, "count_prepared_calls", count)
    monkeypatch.setattr(v2_recall_sweep.llm, "_complete", forbidden_complete)
    monkeypatch.setattr(
        v2_recall_sweep.sys,
        "argv",
        [
            "v2_recall_sweep.py",
            str(cases),
            "--dry-run",
            "--exact-input-counts",
        ],
    )

    assert await v2_recall_sweep.main() == 0
    output = capsys.readouterr().out
    assert len(counted) == 1
    assert counted[0].completion["retry"] is False
    assert "counted input      321" in output
    assert "Anthropic Messages token-count endpoint" in output


@pytest.mark.anyio
async def test_exact_count_permission_requires_anthropic_key(
    monkeypatch, tmp_path, capsys
) -> None:
    cases = write_case(tmp_path)
    settings = v2_recall_sweep.get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    forbid_provider_calls(monkeypatch)
    monkeypatch.setattr(
        v2_recall_sweep.sys,
        "argv",
        [
            "v2_recall_sweep.py",
            str(cases),
            "--dry-run",
            "--exact-input-counts",
        ],
    )

    with pytest.raises(SystemExit, match="2"):
        await v2_recall_sweep.main()
    assert "requires ANTHROPIC_API_KEY" in capsys.readouterr().err


@pytest.mark.anyio
async def test_pending_label_refuses_before_count_or_paid_call(
    monkeypatch, tmp_path
) -> None:
    cases = write_case(tmp_path, review_status="pending")
    forbid_provider_calls(monkeypatch)
    monkeypatch.setattr(
        v2_recall_sweep.sys,
        "argv",
        [
            "v2_recall_sweep.py",
            str(cases),
            "--max-cost-usd",
            "1",
        ],
    )

    with pytest.raises(SystemExit, match="2"):
        await v2_recall_sweep.main()


@pytest.mark.anyio
async def test_insufficient_budget_refuses_before_paid_call(
    monkeypatch, tmp_path
) -> None:
    cases = write_case(tmp_path)
    monkeypatch.setattr(v2_recall_eval, "stage2_pack_failures", lambda _cases: [])
    settings = v2_recall_sweep.get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")

    async def count(prepared, *, concurrency, client):
        del concurrency, client
        return {call.fingerprint: 100 for call in prepared}

    async def forbidden_complete(**_kwargs):
        raise AssertionError("budget refusal reached a paid Message call")

    monkeypatch.setattr(v2_recall_sweep, "count_prepared_calls", count)
    monkeypatch.setattr(v2_recall_sweep.llm, "_complete", forbidden_complete)
    monkeypatch.setattr(
        v2_recall_sweep.sys,
        "argv",
        [
            "v2_recall_sweep.py",
            str(cases),
            "--max-cost-usd",
            "0.000001",
            "--input-price-per-million",
            "2",
            "--output-price-per-million",
            "10",
            "--cache-read-price-per-million",
            "0.2",
            "--cache-write-price-per-million",
            "2.5",
        ],
    )

    with pytest.raises(SystemExit, match="2"):
        await v2_recall_sweep.main()


@pytest.mark.anyio
async def test_paid_v2_requires_explicit_base_rates_before_provider_access(
    monkeypatch, tmp_path, capsys
) -> None:
    cases = write_case(tmp_path)
    monkeypatch.setattr(v2_recall_eval, "stage2_pack_failures", lambda _cases: [])
    forbid_provider_calls(monkeypatch)
    monkeypatch.setattr(
        v2_recall_sweep.sys,
        "argv",
        [
            "v2_recall_sweep.py",
            str(cases),
            "--max-cost-usd",
            "1",
            "--cache-read-price-per-million",
            "0.2",
            "--cache-write-price-per-million",
            "2.5",
        ],
    )

    with pytest.raises(SystemExit, match="2"):
        await v2_recall_sweep.main()
    assert "--input-price-per-million is required" in capsys.readouterr().err


@pytest.mark.anyio
async def test_resume_reuses_exact_fingerprint_and_fresh_ignores_it(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(v2_recall_eval, "stage2_pack_failures", lambda _cases: [])
    cases_path = write_case(tmp_path)
    case = json.loads(cases_path.read_text())[0]
    settings = v2_recall_sweep.get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    prepared = v2_recall_sweep.prepare_cases(
        [case],
        levels=[settings.scoring_effort],
        model=settings.scoring_model,
    )[0]
    result = v2_recall_eval.Result(
        index=0,
        case=prepared.case_name,
        expected_recall=4,
        recall=4,
        expected_flow="complete",
        flow="complete",
        semantic_fingerprint=v2_recall_eval.semantic_fingerprint(
            prepared.case, prepared.completion
        ),
        usage=Usage(input_tokens=100, output_tokens=20),
        feedback="Saved result.",
        needs_more_evidence=False,
        mastery_summary="Stable.",
    )
    record = make_result_record(
        prepared,
        model=settings.scoring_model,
        result=v2_recall_eval.result_payload(result),
        usage=result.usage,
    )
    record["stage2_pack_fingerprint"] = v2_recall_eval.stage2_pack_fingerprint(
        [case]
    )
    record["qualification_fingerprint"] = (
        v2_recall_eval.deployment_fingerprint(prepared.completion)
    )
    resume = tmp_path / "prior.jsonl"
    with JsonlRecorder(resume) as recorder:
        recorder.append(record)

    forbid_provider_calls(monkeypatch)
    output = tmp_path / "resumed.jsonl"
    monkeypatch.setattr(
        v2_recall_sweep.sys,
        "argv",
        [
            "v2_recall_sweep.py",
            str(cases_path),
            "--resume",
            str(resume),
            "--output",
            str(output),
            "--enforce-v2-recall-gate",
        ],
    )
    assert await v2_recall_sweep.main() == 0
    assert json.loads(output.read_text())["fingerprint"] == prepared.fingerprint
    assert "resumed calls      1" in capsys.readouterr().out

    monkeypatch.setattr(
        v2_recall_sweep.sys,
        "argv",
        [
            "v2_recall_sweep.py",
            str(cases_path),
            "--resume",
            str(resume),
            "--fresh",
            "--dry-run",
        ],
    )
    assert await v2_recall_sweep.main() == 0
    fresh_output = capsys.readouterr().out
    assert "resumed calls      0" in fresh_output
    assert "new paid calls     1" in fresh_output


@pytest.mark.anyio
async def test_paid_run_flushes_manifest_and_every_success_or_failure(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(v2_recall_eval, "stage2_pack_failures", lambda _cases: [])
    cases_path = write_case(tmp_path)
    cases = json.loads(cases_path.read_text())
    failed = {**cases[0], "name": "synthetic failed Recall case", "answer": "fail"}
    cases_path.write_text(json.dumps([cases[0], failed]))
    settings = v2_recall_sweep.get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    called: list[str] = []

    async def count(prepared, *, concurrency, client):
        del concurrency, client
        return {call.fingerprint: 100 for call in prepared}

    async def fake_complete(**kwargs):
        user_content = kwargs["user_content"]
        called.append(user_content)
        traces = kwargs["call_traces"]
        if "ANSWER: fail" in user_content:
            traces.append(
                ProviderCallTrace(
                    provider="anthropic",
                    model=kwargs["model"],
                    response_model=kwargs["model"],
                    response_id="msg_failed",
                    latency_ms=12,
                    input_tokens=101,
                    output_tokens=7,
                    outcome="invalid_json",
                    error_type="JSONDecodeError",
                )
            )
            raise llm.LLMError("typed invalid JSON")
        traces.append(
            ProviderCallTrace(
                provider="anthropic",
                model=kwargs["model"],
                response_model=kwargs["model"],
                response_id="msg_success",
                latency_ms=11,
                input_tokens=100,
                output_tokens=8,
            )
        )
        return {
            "recall_score": 4,
            "feedback": "Grounded.",
            "follow_up_question": "",
            "needs_more_evidence": False,
            "mastery_summary": "stable",
        }

    monkeypatch.setattr(v2_recall_sweep, "count_prepared_calls", count)
    monkeypatch.setattr(v2_recall_sweep.llm, "_complete", fake_complete)
    output = tmp_path / "claude-evidence.jsonl"
    monkeypatch.setattr(
        v2_recall_sweep.sys,
        "argv",
        [
            "v2_recall_sweep.py",
            str(cases_path),
            "--max-cost-usd",
            "1",
            "--input-price-per-million",
            "2",
            "--output-price-per-million",
            "10",
            "--cache-read-price-per-million",
            "0.2",
            "--cache-write-price-per-million",
            "2.5",
            "--qualification-expires-at",
            QUALIFICATION_EXPIRES_AT,
            "--output",
            str(output),
        ],
    )

    assert await v2_recall_sweep.main() == 1
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(called) == 2
    assert rows[0]["record_type"] == "run_manifest"
    assert len(rows[0]["invocations"]) == 2
    assert rows[0]["preflight"]["approved_max_cost_usd"] == "1"
    assert rows[0]["preflight"]["input_count_method"] == (
        "anthropic_messages_count_tokens"
    )
    assert rows[0]["preflight"]["input_tokens_total"] == 200
    assert rows[0]["preflight"]["rates_per_million_usd"] == {
        "input": "2",
        "output": "10",
        "cached_input": "0.2",
        "cache_write": "2.5",
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
    assert failed_row["failure"]["type"] == "invalid_json"
    assert failed_row["provider_response_id"] == "msg_failed"
    assert failed_row["provider_response_model"] == settings.scoring_model
    assert failed_row["provider_elapsed_ms"] == 12
    assert failed_row["usage"]["input_tokens"] == 101
    assert failed_row["usage"]["output_tokens"] == 7
