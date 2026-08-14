"""API-level route freezing and physical scoring-call audit tests."""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.models import AI_CONSENT_GRANTED, FOUNDER_USER_ID, LLMUsage, Session, User
from app.routers import sessions as sessions_router
from app.services import ai_consent, llm, usage
from app.services.llm import ScoreResult
from app.services.scoring_provider import (
    ProviderCallTrace,
    ScoringTrace,
    compare_shadow_results,
    qualification_fingerprint,
    route_for_session,
)
from tests.conftest import API_HEADERS, TEST_DATABASE_URL, make_card

FINGERPRINT = qualification_fingerprint(
    llm.build_score_v2_completion(
        model="gpt-5.6-luna",
        effort="low",
        topic="dynamic",
        mastery_summary="",
        question_asked="dynamic",
        answer_text="dynamic",
        probes=[],
    )
)
SHADOW_STAGE_ID = "33333333-3333-4333-8333-333333333333"
QUALIFICATION_EXPIRY = "2099-01-01T00:00:00Z"


def enable_v2_openai(monkeypatch: pytest.MonkeyPatch, *, mode: str) -> Any:
    settings = get_settings()
    values = {
        "scoring_contract_version": 2,
        "openai_api_key": "openai-test-key",
        "openai_v2_scoring_model": "gpt-5.6-luna",
        "openai_v2_scoring_effort": "low",
        "openai_v2_scoring_mode": mode,
        "openai_v2_scoring_user_ids": str(FOUNDER_USER_ID),
        "openai_v2_scoring_qualification_fingerprint": FINGERPRINT,
        "openai_v2_scoring_qualification_expires_at": QUALIFICATION_EXPIRY,
        "openai_v2_scoring_shadow_stage_id": SHADOW_STAGE_ID,
        "openai_safety_identifier_secret": "s" * 32,
        "ai_consent_enforcement_enabled": True,
    }
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)
    return settings


def complete_v2(*, trace: ScoringTrace | None = None) -> ScoreResult:
    return ScoreResult(
        status="complete",
        score=4,
        accuracy=4,
        feedback="The essential account is grounded.",
        mastery_summary="recalled the essential account",
        scoring_contract_version=2,
        trace=trace,
    )


async def grant_current_consent(db) -> None:
    user = await db.get(User, FOUNDER_USER_ID)
    assert user is not None
    user.ai_consent_status = AI_CONSENT_GRANTED
    user.ai_consent_version = ai_consent.POLICY_VERSION
    user.ai_consent_granted_at = datetime.now(UTC)
    db.add(user)
    await db.commit()


async def usage_rows(db) -> list[LLMUsage]:
    return list(
        (
            await db.exec(
                select(LLMUsage).where(LLMUsage.operation == "score_v2")
            )
        ).all()
    )


async def intent_rows(db) -> list[LLMUsage]:
    return list(
        (
            await db.exec(
                select(LLMUsage).where(
                    LLMUsage.operation == usage.SCORING_INTENT_OPERATION
                )
            )
        ).all()
    )


async def test_session_creation_freezes_route_and_submit_reuses_that_binding(
    client, db, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = enable_v2_openai(monkeypatch, mode="primary")
    await grant_current_consent(db)
    expected_route = route_for_session(
        settings,
        user_id=FOUNDER_USER_ID,
        scoring_contract_version=2,
    ).as_json()
    card = make_card(canonical_question="What is the essential account?")
    db.add(card)
    await db.commit()

    started = (
        await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)
    ).json()
    session_id = uuid.UUID(started["session_id"])
    session = await db.get(Session, session_id)
    assert session is not None
    assert session.scoring_route == expected_route

    score_calls: list[dict[str, Any]] = []

    async def score_answer(**kwargs: Any) -> ScoreResult:
        score_calls.append(kwargs)
        return complete_v2(
            trace=ScoringTrace(
                route="primary",
                authoritative_provider="openai",
                qualification_fingerprint=FINGERPRINT,
                calls=(
                    ProviderCallTrace(
                        provider="openai",
                        model=expected_route["openai_model"],
                        response_id="resp_route",
                    ),
                ),
            )
        )

    reservations: list[int] = []

    async def ensure_available(*_args: Any, requested_calls: int = 1, **_kwargs: Any) -> None:
        reservations.append(requested_calls)

    monkeypatch.setattr(llm, "score_answer", score_answer)
    monkeypatch.setattr(usage, "ensure_available", ensure_available)
    response = await client.post(
        f"/sessions/{session_id}/answers",
        headers=API_HEADERS,
        json={"text": "It minimizes remapping when membership changes."},
    )

    assert response.status_code == 200
    assert len(score_calls) == 1
    assert score_calls[0]["scoring_contract_version"] == 2
    assert score_calls[0]["scoring_route"] == expected_route
    assert score_calls[0]["user_id"] == FOUNDER_USER_ID
    assert reservations == [2]


@pytest.mark.parametrize("mode", ["shadow", "primary"])
@pytest.mark.parametrize(
    "reversion",
    ["qualification_fingerprint_mismatch", "deployed_qualification_changed"],
)
async def test_runtime_reversion_plans_a_claude_only_intent(
    client,
    db,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    reversion: str,
) -> None:
    settings = enable_v2_openai(monkeypatch, mode=mode)
    await grant_current_consent(db)
    card = make_card(canonical_question="What is the essential account?")
    db.add(card)
    await db.commit()
    started = (
        await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)
    ).json()
    session_id = uuid.UUID(started["session_id"])

    observed_fingerprint = FINGERPRINT
    if reversion == "qualification_fingerprint_mismatch":
        observed_fingerprint = "b" * 64
        monkeypatch.setattr(
            sessions_router,
            "qualification_fingerprint",
            lambda _completion: observed_fingerprint,
        )
    else:
        monkeypatch.setattr(
            settings,
            "openai_v2_scoring_qualification_fingerprint",
            "b" * 64,
        )

    trace = ScoringTrace(
        route=mode,
        authoritative_provider="anthropic",
        qualification_fingerprint=observed_fingerprint,
        fallback_reason=reversion,
        calls=(
            ProviderCallTrace(
                provider="anthropic",
                model=settings.scoring_model,
                response_model=settings.scoring_model,
                response_id=f"msg_{mode}_{reversion}",
                input_tokens=20,
                output_tokens=5,
            ),
        ),
    )

    async def score_answer(**_kwargs: Any) -> ScoreResult:
        return complete_v2(trace=trace)

    monkeypatch.setattr(llm, "score_answer", score_answer)
    response = await client.post(
        f"/sessions/{session_id}/answers",
        headers=API_HEADERS,
        json={"text": "It preserves the essential account."},
    )

    assert response.status_code == 200
    intents = await intent_rows(db)
    assert len(intents) == 1
    intent = intents[0].details
    assert intent["status"] == "finalized"
    assert intent["reserved_calls"] == 1
    assert intent["shadow_stage_id"] == ""
    assert intent["shadow_stage_ordinal"] is None
    assert intent["expected_calls"] == [
        {
            "provider": "anthropic",
            "model": settings.scoring_model,
            "requirement": "required",
        }
    ]
    rows = await usage_rows(db)
    assert len(rows) == 1
    assert rows[0].details["call"]["provider"] == "anthropic"
    assert rows[0].details["fallback_reason"] == reversion


async def test_shadow_records_one_usage_row_per_physical_call(
    client, db, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable_v2_openai(monkeypatch, mode="shadow")
    await grant_current_consent(db)
    card = make_card(canonical_question="What is the essential account?")
    db.add(card)
    await db.commit()
    started = (
        await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)
    ).json()
    session_id = uuid.UUID(started["session_id"])
    comparison = compare_shadow_results(
        authoritative_status="complete",
        authoritative_recall=4,
        candidate_status="complete",
        candidate_recall=5,
    )
    trace = ScoringTrace(
        route="shadow",
        authoritative_provider="anthropic",
        qualification_fingerprint=FINGERPRINT,
        calls=(
            ProviderCallTrace(
                provider="anthropic",
                model="claude-sonnet-5",
                response_id="msg_shadow",
                latency_ms=20,
                input_tokens=100,
                output_tokens=20,
                cached_input_tokens=80,
                cache_write_tokens=10,
            ),
            ProviderCallTrace(
                provider="openai",
                model="gpt-5.6-luna",
                    response_model="gpt-5.6-luna",
                response_id="resp_shadow",
                latency_ms=11,
                input_tokens=90,
                output_tokens=10,
                cached_input_tokens=70,
                cache_write_tokens=5,
            ),
        ),
        shadow=comparison,
    )

    async def score_answer(**_kwargs: Any) -> ScoreResult:
        return complete_v2(trace=trace)

    reservations: list[int] = []

    async def ensure_available(*_args: Any, requested_calls: int = 1, **_kwargs: Any) -> None:
        reservations.append(requested_calls)

    monkeypatch.setattr(llm, "score_answer", score_answer)
    monkeypatch.setattr(usage, "ensure_available", ensure_available)
    response = await client.post(
        f"/sessions/{session_id}/answers",
        headers=API_HEADERS,
        json={"text": "It minimizes remapping when membership changes."},
    )

    assert response.status_code == 200
    assert reservations == [2]
    rows = await usage_rows(db)
    assert len(rows) == 2
    details_by_provider = {row.details["call"]["provider"]: row.details for row in rows}
    assert set(details_by_provider) == {"anthropic", "openai"}
    for details in details_by_provider.values():
        assert details["route"] == "shadow"
        assert details["authoritative_provider"] == "anthropic"
        assert details["qualification_fingerprint"] == FINGERPRINT
        assert details["qualification_expires_at"] == QUALIFICATION_EXPIRY
        assert details["session_id"] == str(session_id)
        assert details["scoring_contract_version"] == 2
        assert details["probes_used"] == 0
        assert details["shadow"]["behavioral_match"] is True
        assert details["ai_consent_verified"] is True
        assert details["openai_allowlist_verified"] is True
    assert len(
        {details["scoring_event_id"] for details in details_by_provider.values()}
    ) == 1
    assert details_by_provider["openai"]["call"] == {
        "provider": "openai",
        "model": "gpt-5.6-luna",
        "response_model": "gpt-5.6-luna",
        "response_id": "resp_shadow",
        "latency_ms": 11,
        "input_tokens": 90,
        "output_tokens": 10,
        "cached_input_tokens": 70,
        "cache_write_tokens": 5,
        "outcome": "success",
        "error_type": "",
    }
    intents = await intent_rows(db)
    assert len(intents) == 1
    intent = intents[0].details
    frozen = await db.get(Session, session_id)
    assert frozen is not None
    assert intent["audit_type"] == "scoring_event_intent"
    assert intent["shadow_stage_id"] == SHADOW_STAGE_ID
    assert intent["shadow_stage_ordinal"] == 1
    assert intent["qualification_expires_at"] == QUALIFICATION_EXPIRY
    assert intent["scoring_event_id"] == details_by_provider["openai"][
        "scoring_event_id"
    ]
    assert intent["event_started_at"] == details_by_provider["openai"][
        "event_started_at"
    ]
    assert intent["expected_calls"] == [
        {
            "provider": "anthropic",
            "model": frozen.scoring_route["anthropic_model"],
            "requirement": "required",
        },
        {
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "requirement": "required",
        },
    ]
    assert not any(
        key in intent
        for key in ("answer", "question", "transcript", "prompt", "content")
    )


async def test_scoring_failure_commits_call_audit_without_mutating_session_or_card(
    client, db, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable_v2_openai(monkeypatch, mode="primary")
    await grant_current_consent(db)
    card = make_card(
        canonical_question="What is the essential account?",
        mastery_summary="prior signal",
        ease_factor=2.36,
        interval_days=6,
        repetitions=3,
    )
    db.add(card)
    await db.commit()
    started = (
        await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)
    ).json()
    session_id = uuid.UUID(started["session_id"])
    session = await db.get(Session, session_id)
    assert session is not None
    card_before = card.model_dump()
    session_before = session.model_dump()
    trace = ScoringTrace(
        route="primary",
        authoritative_provider="anthropic",
        qualification_fingerprint=FINGERPRINT,
        calls=(
            ProviderCallTrace(
                provider="openai",
                model="gpt-5.6-luna",
                response_id="resp_failed",
                input_tokens=50,
                output_tokens=3,
                cached_input_tokens=20,
                cache_write_tokens=10,
                outcome="technical_error",
                error_type="OpenAIResponsesError",
            ),
            ProviderCallTrace(
                provider="anthropic",
                model="claude-sonnet-5",
                outcome="transport_error",
                error_type="LLMError",
            ),
        ),
        fallback_reason="OpenAIResponsesError",
    )

    async def fail(**_kwargs: Any) -> ScoreResult:
        raise llm.LLMError("both scoring providers failed", trace=trace)

    reservations: list[int] = []

    async def ensure_available(*_args: Any, requested_calls: int = 1, **_kwargs: Any) -> None:
        reservations.append(requested_calls)

    monkeypatch.setattr(llm, "score_answer", fail)
    monkeypatch.setattr(usage, "ensure_available", ensure_available)
    response = await client.post(
        f"/sessions/{session_id}/answers",
        headers=API_HEADERS,
        json={"text": "answer retained by the client"},
    )

    assert response.status_code == 503
    assert reservations == [2]
    await db.refresh(card)
    await db.refresh(session)

    def normalize(value: Any) -> Any:
        return value.replace(tzinfo=None) if isinstance(value, datetime) else value

    assert {key: normalize(value) for key, value in card.model_dump().items()} == {
        key: normalize(value) for key, value in card_before.items()
    }
    assert {key: normalize(value) for key, value in session.model_dump().items()} == {
        key: normalize(value) for key, value in session_before.items()
    }
    rows = await usage_rows(db)
    assert len(rows) == 2
    details_by_provider = {row.details["call"]["provider"]: row.details for row in rows}
    assert details_by_provider["openai"]["call"]["outcome"] == "technical_error"
    assert details_by_provider["openai"]["call"]["input_tokens"] == 50
    assert details_by_provider["openai"]["call"]["cache_write_tokens"] == 10
    assert details_by_provider["anthropic"]["call"]["outcome"] == "transport_error"
    assert all(
        details["fallback_reason"] == "OpenAIResponsesError"
        for details in details_by_provider.values()
    )
    assert all(
        details["session_id"] == str(session_id)
        for details in details_by_provider.values()
    )
    assert len(
        {details["scoring_event_id"] for details in details_by_provider.values()}
    ) == 1
    intents = await intent_rows(db)
    assert len(intents) == 1
    assert intents[0].details["scoring_event_id"] == details_by_provider[
        "openai"
    ]["scoring_event_id"]
    assert intents[0].details["expected_calls"][1]["requirement"] == (
        "conditional_fallback"
    )


async def test_v2_claude_only_event_has_one_finalized_intent(
    client, db, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = enable_v2_openai(monkeypatch, mode="off")
    await grant_current_consent(db)
    card = make_card(canonical_question="What is the essential account?")
    db.add(card)
    await db.commit()
    started = (
        await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)
    ).json()
    session_id = uuid.UUID(started["session_id"])
    trace = ScoringTrace(
        route="anthropic",
        authoritative_provider="anthropic",
        qualification_fingerprint="",
        calls=(
            ProviderCallTrace(
                provider="anthropic",
                model=settings.scoring_model,
                response_model=settings.scoring_model,
                response_id="msg_claude_v2",
                input_tokens=20,
                output_tokens=5,
            ),
        ),
    )

    async def score_answer(**_kwargs: Any) -> ScoreResult:
        return complete_v2(trace=trace)

    monkeypatch.setattr(llm, "score_answer", score_answer)
    response = await client.post(
        f"/sessions/{session_id}/answers",
        headers=API_HEADERS,
        json={"text": "It preserves the essential account."},
    )

    assert response.status_code == 200
    intents = await intent_rows(db)
    assert len(intents) == 1
    assert intents[0].details["status"] == "finalized"
    assert intents[0].details["reserved_calls"] == 1
    assert intents[0].details["terminal_call_count"] == 1
    assert intents[0].details["shadow_stage_id"] == ""
    assert intents[0].details["shadow_stage_ordinal"] is None
    assert intents[0].details["expected_calls"] == [
        {
            "provider": "anthropic",
            "model": settings.scoring_model,
            "requirement": "required",
        }
    ]


async def test_pending_intent_reservation_counts_toward_daily_quota(db) -> None:
    db.add(
        LLMUsage(
            user_id=FOUNDER_USER_ID,
            operation=usage.SCORING_INTENT_OPERATION,
            details={"status": "pending", "reserved_calls": 2},
        )
    )
    await db.commit()
    settings = get_settings().model_copy(
        update={
            "llm_calls_per_day": 2,
            "ai_consent_enforcement_enabled": False,
        }
    )

    with pytest.raises(HTTPException) as exc:
        await usage.ensure_available(
            db,
            FOUNDER_USER_ID,
            "score_v2",
            settings,
            requested_calls=1,
        )
    assert exc.value.status_code == 429
    await db.rollback()


async def test_postgres_stage_ordinals_are_contiguous_under_concurrent_events(
    db,
) -> None:
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("stage ordinal serialization requires Postgres")

    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    first_has_boundary = asyncio.Event()
    release_first = asyncio.Event()
    settings = get_settings().model_copy(
        update={"ai_consent_enforcement_enabled": False}
    )

    async def create_intent(*, pause: bool) -> uuid.UUID:
        async with factory() as request_db:
            await ai_consent.require_ai_processing(
                request_db, FOUNDER_USER_ID, settings
            )
            if pause:
                first_has_boundary.set()
                await release_first.wait()
            intent_id = await usage.record_scoring_intent(
                request_db,
                FOUNDER_USER_ID,
                details={
                    "status": "pending",
                    "reserved_calls": 2,
                    "terminal_call_count": 0,
                    "scoring_event_id": str(uuid.uuid4()),
                    "shadow_stage_id": SHADOW_STAGE_ID,
                    "shadow_stage_ordinal": None,
                    "expected_calls": [
                        {
                            "provider": "anthropic",
                            "model": "claude-test",
                            "requirement": "required",
                        },
                        {
                            "provider": "openai",
                            "model": "gpt-test",
                            "requirement": "required",
                        },
                    ],
                },
            )
            await request_db.commit()
            return intent_id

    first = asyncio.create_task(create_intent(pause=True))
    second = None
    try:
        await asyncio.wait_for(first_has_boundary.wait(), timeout=3)
        second = asyncio.create_task(create_intent(pause=False))
        await asyncio.sleep(0.1)
        assert not second.done()
    finally:
        release_first.set()

    await asyncio.wait_for(first, timeout=3)
    await asyncio.wait_for(second, timeout=3)
    async with factory() as verify_db:
        details = (
            await verify_db.exec(
                select(LLMUsage.details).where(
                    LLMUsage.operation == usage.SCORING_INTENT_OPERATION
                )
            )
        ).all()
        ordinals = sorted(
            item["shadow_stage_ordinal"]
            for item in details
            if item.get("shadow_stage_id") == SHADOW_STAGE_ID
        )
        assert ordinals == [1, 2]
    await engine.dispose()


async def test_partial_shadow_trace_stays_incomplete_and_reserves_missing_call(
    db,
) -> None:
    event_id = str(uuid.uuid4())
    intent_id = await usage.record_scoring_intent(
        db,
        FOUNDER_USER_ID,
        details={
            "status": "pending",
            "reserved_calls": 2,
            "terminal_call_count": 0,
            "scoring_event_id": event_id,
            "expected_calls": [
                {
                    "provider": "anthropic",
                    "model": "claude-test",
                    "requirement": "required",
                },
                {
                    "provider": "openai",
                    "model": "gpt-test",
                    "requirement": "required",
                },
            ],
        },
    )

    with pytest.raises(usage.ScoringAuditIncomplete):
        await usage.record_physical_calls(
            db,
            FOUNDER_USER_ID,
            "score_v2",
            intent_id=intent_id,
            call_details=[
                {
                    "scoring_event_id": event_id,
                    "call": {"provider": "anthropic"},
                }
            ],
        )

    intent = await db.get(LLMUsage, intent_id, populate_existing=True)
    assert intent is not None
    assert intent.details["status"] == "incomplete"
    assert intent.details["terminal_call_count"] == 1
    assert intent.details.get("finalized_at") is None
    assert len(await usage_rows(db)) == 1
    settings = get_settings().model_copy(
        update={
            "llm_calls_per_day": 2,
            "ai_consent_enforcement_enabled": False,
        }
    )
    with pytest.raises(HTTPException) as exc:
        await usage.ensure_available(
            db,
            FOUNDER_USER_ID,
            "score_v2",
            settings,
            requested_calls=1,
        )
    assert exc.value.status_code == 429
    await db.rollback()


async def test_finalized_primary_intent_releases_unused_fallback_reservation(db) -> None:
    event_id = str(uuid.uuid4())
    intent_id = await usage.record_scoring_intent(
        db,
        FOUNDER_USER_ID,
        details={
            "status": "pending",
            "reserved_calls": 2,
            "scoring_event_id": event_id,
            "expected_calls": [
                {
                    "provider": "openai",
                    "model": "gpt-test",
                    "requirement": "required",
                },
                {
                    "provider": "anthropic",
                    "model": "claude-test",
                    "requirement": "conditional_fallback",
                },
            ],
        },
    )
    independently_committed = await usage.record_physical_calls(
        db,
        FOUNDER_USER_ID,
        "score_v2",
        intent_id=intent_id,
        call_details=[
            {
                "scoring_event_id": event_id,
                "authoritative_provider": "openai",
                "fallback_reason": "",
                "call": {
                    "provider": "openai",
                    "outcome": "success",
                    "error_type": "",
                },
            }
        ],
    )
    if not independently_committed:
        await db.commit()

    intent = await db.get(LLMUsage, intent_id, populate_existing=True)
    assert intent is not None
    assert intent.details["status"] == "finalized"
    assert intent.details["terminal_call_count"] == 1
    settings = get_settings().model_copy(
        update={
            "llm_calls_per_day": 2,
            "ai_consent_enforcement_enabled": False,
        }
    )
    await usage.ensure_available(
        db,
        FOUNDER_USER_ID,
        "score_v2",
        settings,
        requested_calls=1,
    )
    await db.rollback()


@pytest.mark.parametrize(
    "call_details",
    [
        [
            {
                "authoritative_provider": "openai",
                "fallback_reason": "",
                "call": {
                    "provider": "openai",
                    "outcome": "technical_error",
                    "error_type": "timeout",
                },
            }
        ],
        [
            {
                "authoritative_provider": "anthropic",
                "fallback_reason": "unnecessary_fallback",
                "call": {
                    "provider": "openai",
                    "outcome": "success",
                    "error_type": "",
                },
            },
            {
                "authoritative_provider": "anthropic",
                "fallback_reason": "unnecessary_fallback",
                "call": {
                    "provider": "anthropic",
                    "outcome": "success",
                    "error_type": "",
                },
            },
        ],
    ],
)
async def test_invalid_primary_terminal_semantics_stay_incomplete(
    db, call_details
) -> None:
    event_id = str(uuid.uuid4())
    intent_id = await usage.record_scoring_intent(
        db,
        FOUNDER_USER_ID,
        details={
            "status": "pending",
            "reserved_calls": 2,
            "terminal_call_count": 0,
            "scoring_event_id": event_id,
            "expected_calls": [
                {
                    "provider": "openai",
                    "model": "gpt-test",
                    "requirement": "required",
                },
                {
                    "provider": "anthropic",
                    "model": "claude-test",
                    "requirement": "conditional_fallback",
                },
            ],
        },
    )
    details = [
        {**item, "scoring_event_id": event_id} for item in call_details
    ]

    with pytest.raises(usage.ScoringAuditIncomplete):
        await usage.record_physical_calls(
            db,
            FOUNDER_USER_ID,
            "score_v2",
            intent_id=intent_id,
            call_details=details,
        )

    intent = await db.get(LLMUsage, intent_id, populate_existing=True)
    assert intent is not None
    assert intent.details["status"] == "incomplete"
    assert intent.details["terminal_call_count"] == len(call_details)


async def test_intent_finalization_failure_returns_503_without_product_mutation(
    client, db, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = enable_v2_openai(monkeypatch, mode="shadow")
    await grant_current_consent(db)
    card = make_card(
        canonical_question="What is the essential account?",
        mastery_summary="prior signal",
    )
    db.add(card)
    await db.commit()
    started = (
        await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)
    ).json()
    session_id = uuid.UUID(started["session_id"])
    session = await db.get(Session, session_id)
    assert session is not None
    card_before = card.model_dump()
    session_before = session.model_dump()
    trace = ScoringTrace(
        route="shadow",
        authoritative_provider="anthropic",
        qualification_fingerprint=FINGERPRINT,
        calls=(
            ProviderCallTrace(
                provider="anthropic",
                model=settings.scoring_model,
                response_id="msg_audit_failure",
                input_tokens=10,
                output_tokens=2,
            ),
            ProviderCallTrace(
                provider="openai",
                model=settings.openai_v2_scoring_model,
                response_id="resp_audit_failure",
                input_tokens=10,
                output_tokens=2,
            ),
        ),
    )

    async def score_answer(**_kwargs: Any) -> ScoreResult:
        return complete_v2(trace=trace)

    async def fail_finalize(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(llm, "score_answer", score_answer)
    monkeypatch.setattr(usage, "_finalize_scoring_intent", fail_finalize)
    response = await client.post(
        f"/sessions/{session_id}/answers",
        headers=API_HEADERS,
        json={"text": "answer retained by the client"},
    )

    assert response.status_code == 503
    await db.refresh(card)
    await db.refresh(session)
    assert card.model_dump() == card_before
    assert session.model_dump() == session_before
    assert await usage_rows(db) == []
