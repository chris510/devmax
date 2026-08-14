"""Provider-routing invariants at the scoring service boundary.

The Responses transport has its own parsing tests. These tests start one level
higher: they pin the frozen session route, replace each physical transmission,
and assert which result is authoritative and which calls are retained for audit.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from app.config import get_settings
from app.services import llm
from app.services.openai_responses import (
    OpenAIResponsesError,
    OpenAIResponsesResult,
)
from app.services.scoring_provider import (
    ROUTE_ANTHROPIC,
    ROUTE_PRIMARY,
    ROUTE_SHADOW,
    OpenAIEligibility,
    ProviderCallTrace,
    ScoringRoute,
    qualification_fingerprint,
    safety_identifier,
)

USER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
OPENAI_MODEL = "gpt-5.6-luna"
OPENAI_EFFORT = "low"
ANTHROPIC_MODEL = "claude-frozen"
ANTHROPIC_EFFORT = "low"
PROBES = [
    ("One more — what moves?", "Only the keys on one arc."),
    ("Last one — why virtual nodes?", "They smooth distribution."),
]
SCORE_ARGS = {
    "topic": "Consistent hashing",
    "mastery_summary": "",
    "question_asked": "What problem does consistent hashing solve?",
    "answer_text": "It keeps most keys in place when a node joins.",
    "probes": PROBES,
}


def v2_payload(recall: int) -> dict[str, Any]:
    return {
        "recall_score": recall,
        "feedback": "The essential account is grounded.",
        "follow_up_question": "",
        "needs_more_evidence": False,
        "mastery_summary": "recalled the essential account",
    }


def v1_payload() -> dict[str, Any]:
    return {
        "accuracy": 4,
        "depth": 3,
        "boundaries": 0,
        "feedback": "Good.",
        "follow_up_question": "",
        "needs_more_evidence": False,
        "mastery_summary": "recalled the mechanism",
    }


def configure_openai(monkeypatch: pytest.MonkeyPatch, *, mode: str) -> Any:
    settings = get_settings()
    values = {
        "openai_api_key": "openai-test-key",
        "openai_v2_scoring_mode": mode,
        "openai_v2_scoring_model": OPENAI_MODEL,
        "openai_v2_scoring_effort": OPENAI_EFFORT,
        "openai_v2_scoring_user_ids": str(USER_ID),
        "openai_v2_scoring_qualification_fingerprint": qualified_fingerprint(),
        "openai_v2_scoring_qualification_expires_at": "2099-01-01T00:00:00Z",
        "openai_safety_identifier_secret": "s" * 32,
    }
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)
    return settings


def qualified_fingerprint() -> str:
    completion = llm.build_score_v2_completion(
        model=OPENAI_MODEL,
        effort=OPENAI_EFFORT,
        **SCORE_ARGS,
    )
    return qualification_fingerprint(completion)


def frozen_route(mode: str, *, fingerprint: str | None = None) -> dict[str, Any]:
    return ScoringRoute(
        mode=mode,  # type: ignore[arg-type]
        anthropic_model=ANTHROPIC_MODEL,
        anthropic_effort=ANTHROPIC_EFFORT,
        openai_model=OPENAI_MODEL if mode != ROUTE_ANTHROPIC else "",
        openai_effort=OPENAI_EFFORT if mode != ROUTE_ANTHROPIC else None,
        qualification_fingerprint=(
            fingerprint or qualified_fingerprint()
            if mode != ROUTE_ANTHROPIC
            else ""
        ),
    ).as_json()


def install_anthropic(
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: dict[str, Any] | None = None,
    error: str = "",
    before_return: Callable[[], Awaitable[None]] | None = None,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def complete(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        if before_return is not None:
            await before_return()
        traces = kwargs["call_traces"]
        if error:
            traces.append(
                ProviderCallTrace(
                    provider=ROUTE_ANTHROPIC,
                    model=str(kwargs["model"]),
                    outcome="transport_error",
                    error_type="LLMError",
                )
            )
            raise llm.LLMError(error)
        traces.append(
            ProviderCallTrace(
                provider=ROUTE_ANTHROPIC,
                model=str(kwargs["model"]),
                response_model=str(kwargs["model"]),
                response_id="msg_test",
                latency_ms=12,
                input_tokens=100,
                output_tokens=20,
                cached_input_tokens=80,
            )
        )
        assert payload is not None
        return payload

    monkeypatch.setattr(llm, "_complete", complete)
    return calls


def install_openai(
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: dict[str, Any] | None = None,
    error: OpenAIResponsesError | None = None,
    before_return: Callable[[], Awaitable[None]] | None = None,
    response_model: str = OPENAI_MODEL,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def complete(completion: dict[str, Any], **kwargs: Any) -> OpenAIResponsesResult:
        calls.append({"completion": completion, **kwargs})
        if before_return is not None:
            await before_return()
        if error is not None:
            raise error
        assert payload is not None
        return OpenAIResponsesResult(
            data=payload,
            response_id="resp_test",
            model=response_model,
            elapsed_ms=9,
            input_tokens=90,
            output_tokens=10,
            cached_input_tokens=70,
            cache_write_tokens=8,
        )

    monkeypatch.setattr(llm, "complete_openai_response", complete)
    return calls


async def forbidden_openai(*_args: Any, **_kwargs: Any) -> OpenAIResponsesResult:
    raise AssertionError("OpenAI must not be called")


async def test_v1_always_uses_anthropic_despite_a_frozen_openai_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_openai(monkeypatch, mode=ROUTE_PRIMARY)
    anthropic_calls = install_anthropic(monkeypatch, payload=v1_payload())
    monkeypatch.setattr(llm, "complete_openai_response", forbidden_openai)

    result = await llm.score_answer(
        **SCORE_ARGS,
        scoring_contract_version=1,
        scoring_route=frozen_route(ROUTE_PRIMARY),
        user_id=USER_ID,
    )

    assert result.scoring_contract_version == 1
    assert result.trace is not None
    assert result.trace.authoritative_provider == ROUTE_ANTHROPIC
    assert [call.provider for call in result.trace.calls] == [ROUTE_ANTHROPIC]
    assert anthropic_calls[0]["model"] == ANTHROPIC_MODEL


async def test_a_frozen_v2_anthropic_route_never_calls_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_openai(monkeypatch, mode=ROUTE_PRIMARY)
    install_anthropic(monkeypatch, payload=v2_payload(4))
    monkeypatch.setattr(llm, "complete_openai_response", forbidden_openai)

    result = await llm.score_answer(
        **SCORE_ARGS,
        scoring_contract_version=2,
        scoring_route=frozen_route(ROUTE_ANTHROPIC),
        user_id=USER_ID,
    )

    assert result.trace is not None
    assert result.trace.route == ROUTE_ANTHROPIC
    assert result.trace.fallback_reason == ""
    assert [call.provider for call in result.trace.calls] == [ROUTE_ANTHROPIC]


async def test_default_off_is_a_kill_switch_for_a_frozen_primary_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_openai(monkeypatch, mode="off")
    install_anthropic(monkeypatch, payload=v2_payload(4))
    monkeypatch.setattr(llm, "complete_openai_response", forbidden_openai)

    result = await llm.score_answer(
        **SCORE_ARGS,
        scoring_contract_version=2,
        scoring_route=frozen_route(ROUTE_PRIMARY),
        user_id=USER_ID,
    )

    assert result.trace is not None
    assert result.trace.route == ROUTE_PRIMARY
    assert result.trace.authoritative_provider == ROUTE_ANTHROPIC
    assert result.trace.fallback_reason == "kill_switch"
    assert len(result.trace.calls) == 1


async def test_removing_the_user_from_the_allowlist_stops_a_frozen_primary_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = configure_openai(monkeypatch, mode=ROUTE_PRIMARY)
    monkeypatch.setattr(settings, "openai_v2_scoring_user_ids", str(uuid.uuid4()))
    install_anthropic(monkeypatch, payload=v2_payload(4))
    monkeypatch.setattr(llm, "complete_openai_response", forbidden_openai)

    result = await llm.score_answer(
        **SCORE_ARGS,
        scoring_contract_version=2,
        scoring_route=frozen_route(ROUTE_PRIMARY),
        user_id=USER_ID,
    )

    assert result.trace is not None
    assert result.trace.authoritative_provider == ROUTE_ANTHROPIC
    assert result.trace.fallback_reason == "allowlist_removed"
    assert [call.provider for call in result.trace.calls] == [ROUTE_ANTHROPIC]


async def test_a_qualification_fingerprint_mismatch_is_claude_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_openai(monkeypatch, mode=ROUTE_PRIMARY)
    install_anthropic(monkeypatch, payload=v2_payload(4))
    monkeypatch.setattr(llm, "complete_openai_response", forbidden_openai)

    result = await llm.score_answer(
        **SCORE_ARGS,
        scoring_contract_version=2,
        scoring_route=frozen_route(ROUTE_PRIMARY, fingerprint="0" * 64),
        user_id=USER_ID,
    )

    assert result.trace is not None
    assert result.trace.fallback_reason == "qualification_fingerprint_mismatch"
    assert result.trace.qualification_fingerprint == qualified_fingerprint()
    assert [call.provider for call in result.trace.calls] == [ROUTE_ANTHROPIC]


async def test_revoking_the_deployed_fingerprint_stops_a_frozen_primary_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = configure_openai(monkeypatch, mode=ROUTE_PRIMARY)
    frozen = frozen_route(ROUTE_PRIMARY)
    monkeypatch.setattr(
        settings,
        "openai_v2_scoring_qualification_fingerprint",
        "0" * 64,
    )
    install_anthropic(monkeypatch, payload=v2_payload(4))
    monkeypatch.setattr(llm, "complete_openai_response", forbidden_openai)

    result = await llm.score_answer(
        **SCORE_ARGS,
        scoring_contract_version=2,
        scoring_route=frozen,
        user_id=USER_ID,
    )

    assert result.trace is not None
    assert result.trace.authoritative_provider == ROUTE_ANTHROPIC
    assert result.trace.fallback_reason == "deployed_qualification_changed"
    assert [call.provider for call in result.trace.calls] == [ROUTE_ANTHROPIC]


async def test_expired_qualification_stops_a_frozen_primary_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = configure_openai(monkeypatch, mode=ROUTE_PRIMARY)
    frozen = frozen_route(ROUTE_PRIMARY)
    monkeypatch.setattr(
        settings,
        "openai_v2_scoring_qualification_expires_at",
        "2020-01-01T00:00:00Z",
    )
    install_anthropic(monkeypatch, payload=v2_payload(4))
    monkeypatch.setattr(llm, "complete_openai_response", forbidden_openai)

    result = await llm.score_answer(
        **SCORE_ARGS,
        scoring_contract_version=2,
        scoring_route=frozen,
        user_id=USER_ID,
    )

    assert result.trace is not None
    assert result.trace.authoritative_provider == ROUTE_ANTHROPIC
    assert result.trace.fallback_reason == "qualification_expired"
    assert [call.provider for call in result.trace.calls] == [ROUTE_ANTHROPIC]


async def test_expiry_is_rechecked_at_the_physical_openai_call_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_openai(monkeypatch, mode=ROUTE_PRIMARY)
    install_anthropic(monkeypatch, payload=v2_payload(4))
    monkeypatch.setattr(llm, "complete_openai_response", forbidden_openai)
    checks = 0

    def eligibility(*_args: Any, **_kwargs: Any) -> OpenAIEligibility:
        nonlocal checks
        checks += 1
        return (
            OpenAIEligibility(True)
            if checks == 1
            else OpenAIEligibility(False, "qualification_expired")
        )

    monkeypatch.setattr(llm, "openai_route_eligibility", eligibility)

    result = await llm.score_answer(
        **SCORE_ARGS,
        scoring_contract_version=2,
        scoring_route=frozen_route(ROUTE_PRIMARY),
        user_id=USER_ID,
    )

    assert checks == 2
    assert result.trace is not None
    assert result.trace.authoritative_provider == ROUTE_ANTHROPIC
    assert result.trace.fallback_reason == "qualification_expired"
    assert [call.provider for call in result.trace.calls] == [ROUTE_ANTHROPIC]


class Rendezvous:
    """Fail if the shadow calls are serialized instead of started together."""

    def __init__(self) -> None:
        self.anthropic_started = asyncio.Event()
        self.openai_started = asyncio.Event()

    async def anthropic(self) -> None:
        self.anthropic_started.set()
        await asyncio.wait_for(self.openai_started.wait(), timeout=1)

    async def openai(self) -> None:
        self.openai_started.set()
        await asyncio.wait_for(self.anthropic_started.wait(), timeout=1)


@pytest.mark.parametrize(
    ("authoritative", "candidate", "within_one", "behavioral_match"),
    [(4, 5, True, True), (2, 4, False, False)],
    ids=["matching-decisions", "mismatching-decisions"],
)
async def test_shadow_runs_concurrently_and_keeps_claude_authoritative(
    monkeypatch: pytest.MonkeyPatch,
    authoritative: int,
    candidate: int,
    within_one: bool,
    behavioral_match: bool,
) -> None:
    configure_openai(monkeypatch, mode=ROUTE_SHADOW)
    rendezvous = Rendezvous()
    install_anthropic(
        monkeypatch,
        payload=v2_payload(authoritative),
        before_return=rendezvous.anthropic,
    )
    install_openai(
        monkeypatch,
        payload=v2_payload(candidate),
        before_return=rendezvous.openai,
    )

    result = await llm.score_answer(
        **SCORE_ARGS,
        scoring_contract_version=2,
        scoring_route=frozen_route(ROUTE_SHADOW),
        user_id=USER_ID,
    )

    assert result.score == authoritative
    assert result.trace is not None
    assert result.trace.authoritative_provider == ROUTE_ANTHROPIC
    assert [call.provider for call in result.trace.calls] == [
        ROUTE_ANTHROPIC,
        "openai",
    ]
    assert result.trace.shadow is not None
    assert result.trace.shadow.within_one is within_one
    assert result.trace.shadow.behavioral_match is behavioral_match


async def test_shadow_candidate_failure_is_traced_but_claude_stays_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_openai(monkeypatch, mode=ROUTE_SHADOW)
    install_anthropic(monkeypatch, payload=v2_payload(4))
    install_openai(
        monkeypatch,
        error=OpenAIResponsesError(
            "OpenAI Responses request was refused",
            code="refusal",
            response_id="resp_refused",
            model=OPENAI_MODEL,
            elapsed_ms=17,
            input_tokens=90,
            output_tokens=4,
            cached_input_tokens=60,
            cache_write_tokens=20,
        ),
    )

    result = await llm.score_answer(
        **SCORE_ARGS,
        scoring_contract_version=2,
        scoring_route=frozen_route(ROUTE_SHADOW),
        user_id=USER_ID,
    )

    assert result.score == 4
    assert result.trace is not None
    assert result.trace.candidate_error == "refusal"
    assert result.trace.shadow is None
    assert [(call.provider, call.outcome) for call in result.trace.calls] == [
        (ROUTE_ANTHROPIC, "success"),
        ("openai", "technical_error"),
    ]
    candidate_call = result.trace.calls[1]
    assert candidate_call.response_id == "resp_refused"
    assert candidate_call.input_tokens == 90
    assert candidate_call.cached_input_tokens == 60
    assert candidate_call.cache_write_tokens == 20


@pytest.mark.parametrize("recall", [4, 0], ids=["ordinary", "surprising"])
async def test_a_valid_primary_luna_result_never_calls_claude(
    monkeypatch: pytest.MonkeyPatch, recall: int
) -> None:
    settings = configure_openai(monkeypatch, mode=ROUTE_PRIMARY)
    anthropic_calls = install_anthropic(
        monkeypatch, error="valid OpenAI output must not fall back"
    )
    openai_calls = install_openai(monkeypatch, payload=v2_payload(recall))

    result = await llm.score_answer(
        **SCORE_ARGS,
        scoring_contract_version=2,
        scoring_route=frozen_route(ROUTE_PRIMARY),
        user_id=USER_ID,
    )

    assert result.score == recall
    assert anthropic_calls == []
    assert len(openai_calls) == 1
    assert openai_calls[0]["safety_identifier"] == safety_identifier(settings, USER_ID)
    assert result.trace is not None
    assert result.trace.authoritative_provider == "openai"
    assert result.trace.fallback_reason == ""
    assert [call.provider for call in result.trace.calls] == ["openai"]
    assert result.trace.calls[0].cache_write_tokens == 8


async def test_an_unqualified_returned_model_gets_one_typed_claude_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_openai(monkeypatch, mode=ROUTE_PRIMARY)
    openai_calls = install_openai(
        monkeypatch,
        payload=v2_payload(4),
        response_model="gpt-5.6-luna-unqualified-snapshot",
    )
    anthropic_calls = install_anthropic(monkeypatch, payload=v2_payload(4))

    result = await llm.score_answer(
        **SCORE_ARGS,
        scoring_contract_version=2,
        scoring_route=frozen_route(ROUTE_PRIMARY),
        user_id=USER_ID,
    )

    assert len(openai_calls) == 1
    assert len(anthropic_calls) == 1
    assert result.trace is not None
    assert result.trace.fallback_reason == "model_mismatch"
    candidate = result.trace.calls[0]
    assert candidate.outcome == "technical_error"
    assert candidate.response_model == "gpt-5.6-luna-unqualified-snapshot"
    assert candidate.input_tokens == 90
    assert candidate.output_tokens == 10
    assert candidate.cached_input_tokens == 70
    assert candidate.cache_write_tokens == 8


RESPONSES_FAILURES = {
    "transport": ("OpenAI Responses transport failed: ConnectError", "transport_error"),
    "refusal": ("OpenAI Responses request was refused", "refusal"),
    "incomplete": (
        "OpenAI Responses response was incomplete: max_output_tokens",
        "incomplete",
    ),
    "malformed": (
        "OpenAI Responses structured output was not valid JSON",
        "invalid_structured_json",
    ),
}


@pytest.mark.parametrize("failure", [*RESPONSES_FAILURES, "semantic-contract"])
async def test_each_typed_primary_failure_gets_exactly_one_no_retry_claude_fallback(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    configure_openai(monkeypatch, mode=ROUTE_PRIMARY)
    anthropic_calls = install_anthropic(monkeypatch, payload=v2_payload(4))
    if failure == "semantic-contract":
        openai_calls = install_openai(
            monkeypatch,
            payload={**v2_payload(4), "needs_more_evidence": "false"},
        )
    else:
        openai_calls = install_openai(
            monkeypatch,
            error=OpenAIResponsesError(
                RESPONSES_FAILURES[failure][0],
                code=RESPONSES_FAILURES[failure][1],
            ),
        )

    result = await llm.score_answer(
        **SCORE_ARGS,
        scoring_contract_version=2,
        scoring_route=frozen_route(ROUTE_PRIMARY),
        user_id=USER_ID,
    )

    assert len(openai_calls) == 1
    assert len(anthropic_calls) == 1
    assert openai_calls[0]["completion"]["retry"] is False
    assert anthropic_calls[0]["retry"] is False
    assert anthropic_calls[0]["purpose"] == "score_v2_fallback"
    assert result.trace is not None
    assert result.trace.authoritative_provider == ROUTE_ANTHROPIC
    assert result.trace.fallback_reason == (
        "invalid_v2_contract"
        if failure == "semantic-contract"
        else RESPONSES_FAILURES[failure][1]
    )
    assert [call.provider for call in result.trace.calls] == [
        "openai",
        ROUTE_ANTHROPIC,
    ]
    assert result.trace.calls[0].outcome == (
        "invalid_contract" if failure == "semantic-contract" else "technical_error"
    )


async def test_primary_fallback_failure_carries_both_physical_call_traces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_openai(monkeypatch, mode=ROUTE_PRIMARY)
    install_openai(
        monkeypatch,
        error=OpenAIResponsesError(
            "OpenAI Responses request timed out", code="timeout"
        ),
    )
    install_anthropic(monkeypatch, error="Claude fallback unavailable")

    with pytest.raises(llm.LLMError) as caught:
        await llm.score_answer(
            **SCORE_ARGS,
            scoring_contract_version=2,
            scoring_route=frozen_route(ROUTE_PRIMARY),
            user_id=USER_ID,
        )

    trace = caught.value.trace
    assert trace is not None
    assert trace.authoritative_provider == ROUTE_ANTHROPIC
    assert trace.fallback_reason == "timeout"
    assert [(call.provider, call.outcome) for call in trace.calls] == [
        ("openai", "technical_error"),
        (ROUTE_ANTHROPIC, "transport_error"),
    ]
