import asyncio
import json
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlmodel import select

from app.config import get_settings
from app.models import (
    FOUNDER_USER_ID,
    AppleIdentity,
    AuthSession,
    Card,
    DeviceToken,
    LLMUsage,
    MaterialSource,
    MaterialTopicProposal,
    PendingCapture,
    Session,
    Settings,
    StudyPlan,
    StudyPlanCardLink,
    StudyPlanCardProposal,
    StudyPlanCardProposalAcceptance,
    StudyPlanDuplication,
    StudyPlanGuideDraft,
    StudyPlanItem,
    StudyPlanItemDependency,
    StudyPlanPhase,
    StudyPlanPracticeDebrief,
    StudyPlanRevision,
    StudyPlanWeek,
    User,
)
from app.services import authentication


async def _account(db, *, topic: str) -> tuple[User, dict[str, str], Card]:
    user = User()
    db.add(user)
    await db.flush()
    db.add(Settings(user_id=user.id, timezone="UTC"))
    card = Card(
        user_id=user.id,
        topic=topic,
        category="Unsorted",
        canonical_question=f"Explain {topic}.",
        next_review_at=date.today(),
    )
    db.add(card)
    pair = await authentication.issue_session(db, user.id, get_settings())
    await db.commit()
    return user, {"Authorization": f"Bearer {pair.access_token}"}, card


def _canonical_rows(rows) -> str:
    return json.dumps(
        [row.model_dump(mode="json") for row in rows],
        sort_keys=True,
        separators=(",", ":"),
    )


async def _founder_study_data_snapshot(db) -> dict[str, str]:
    """Canonical bytes for every founder aggregate the claim must not touch."""
    db.expire_all()
    users = (
        await db.exec(select(User).where(User.id == FOUNDER_USER_ID).order_by(User.id))
    ).all()
    cards = (
        await db.exec(select(Card).where(Card.user_id == FOUNDER_USER_ID).order_by(Card.id))
    ).all()
    sessions = (
        await db.exec(
            select(Session)
            .join(Card, Card.id == Session.card_id)
            .where(Card.user_id == FOUNDER_USER_ID)
            .order_by(Session.id)
        )
    ).all()
    plans = (
        await db.exec(
            select(StudyPlan)
            .where(StudyPlan.user_id == FOUNDER_USER_ID)
            .order_by(StudyPlan.id)
        )
    ).all()
    settings = (
        await db.exec(select(Settings).where(Settings.user_id == FOUNDER_USER_ID))
    ).all()
    devices = (
        await db.exec(
            select(DeviceToken)
            .where(DeviceToken.user_id == FOUNDER_USER_ID)
            .order_by(DeviceToken.token)
        )
    ).all()
    captures = (
        await db.exec(
            select(PendingCapture)
            .where(PendingCapture.user_id == FOUNDER_USER_ID)
            .order_by(PendingCapture.id)
        )
    ).all()
    drafts = (
        await db.exec(
            select(StudyPlanGuideDraft)
            .where(StudyPlanGuideDraft.user_id == FOUNDER_USER_ID)
            .order_by(StudyPlanGuideDraft.id)
        )
    ).all()
    sources = (
        await db.exec(
            select(MaterialSource)
            .where(MaterialSource.user_id == FOUNDER_USER_ID)
            .order_by(MaterialSource.id)
        )
    ).all()
    material_proposals = (
        await db.exec(
            select(MaterialTopicProposal)
            .join(MaterialSource, MaterialSource.id == MaterialTopicProposal.source_id)
            .where(MaterialSource.user_id == FOUNDER_USER_ID)
            .order_by(MaterialTopicProposal.id)
        )
    ).all()
    usage = (
        await db.exec(
            select(LLMUsage)
            .where(LLMUsage.user_id == FOUNDER_USER_ID)
            .order_by(LLMUsage.id)
        )
    ).all()
    plan_ids = [plan.id for plan in plans]
    phases = (
        await db.exec(
            select(StudyPlanPhase)
            .where(StudyPlanPhase.plan_id.in_(plan_ids))
            .order_by(StudyPlanPhase.id)
        )
    ).all()
    weeks = (
        await db.exec(
            select(StudyPlanWeek)
            .where(StudyPlanWeek.plan_id.in_(plan_ids))
            .order_by(StudyPlanWeek.id)
        )
    ).all()
    items = (
        await db.exec(
            select(StudyPlanItem)
            .where(StudyPlanItem.plan_id.in_(plan_ids))
            .order_by(StudyPlanItem.id)
        )
    ).all()
    dependencies = (
        await db.exec(
            select(StudyPlanItemDependency)
            .where(StudyPlanItemDependency.plan_id.in_(plan_ids))
            .order_by(StudyPlanItemDependency.id)
        )
    ).all()
    revisions = (
        await db.exec(
            select(StudyPlanRevision)
            .where(StudyPlanRevision.plan_id.in_(plan_ids))
            .order_by(StudyPlanRevision.id)
        )
    ).all()
    debriefs = (
        await db.exec(
            select(StudyPlanPracticeDebrief)
            .where(StudyPlanPracticeDebrief.plan_id.in_(plan_ids))
            .order_by(StudyPlanPracticeDebrief.id)
        )
    ).all()
    card_proposals = (
        await db.exec(
            select(StudyPlanCardProposal)
            .where(StudyPlanCardProposal.plan_id.in_(plan_ids))
            .order_by(StudyPlanCardProposal.id)
        )
    ).all()
    acceptances = (
        await db.exec(
            select(StudyPlanCardProposalAcceptance)
            .join(
                StudyPlanCardProposal,
                StudyPlanCardProposal.id == StudyPlanCardProposalAcceptance.proposal_id,
            )
            .where(StudyPlanCardProposal.plan_id.in_(plan_ids))
            .order_by(StudyPlanCardProposalAcceptance.id)
        )
    ).all()
    links = (
        await db.exec(
            select(StudyPlanCardLink)
            .where(StudyPlanCardLink.plan_id.in_(plan_ids))
            .order_by(StudyPlanCardLink.id)
        )
    ).all()
    duplications = (
        await db.exec(
            select(StudyPlanDuplication)
            .where(StudyPlanDuplication.source_plan_id.in_(plan_ids))
            .order_by(StudyPlanDuplication.id)
        )
    ).all()
    return {
        "users": _canonical_rows(users),
        "cards": _canonical_rows(cards),
        "sessions": _canonical_rows(sessions),
        "plans": _canonical_rows(plans),
        "settings": _canonical_rows(settings),
        "device_tokens": _canonical_rows(devices),
        "pending_captures": _canonical_rows(captures),
        "study_plan_guide_drafts": _canonical_rows(drafts),
        "material_sources": _canonical_rows(sources),
        "material_topic_proposals": _canonical_rows(material_proposals),
        "llm_usage": _canonical_rows(usage),
        "study_plan_phases": _canonical_rows(phases),
        "study_plan_weeks": _canonical_rows(weeks),
        "study_plan_items": _canonical_rows(items),
        "study_plan_item_dependencies": _canonical_rows(dependencies),
        "study_plan_revisions": _canonical_rows(revisions),
        "study_plan_practice_debriefs": _canonical_rows(debriefs),
        "study_plan_card_proposals": _canonical_rows(card_proposals),
        "study_plan_card_proposal_acceptances": _canonical_rows(acceptances),
        "study_plan_card_links": _canonical_rows(links),
        "study_plan_duplications": _canonical_rows(duplications),
    }


def _stub_apple(
    monkeypatch,
    *,
    subjects: dict[str, str] | None = None,
    emails: dict[str, str] | None = None,
    code_subjects: dict[str, str] | None = None,
    missing_refresh_codes: set[str] | None = None,
):
    subjects = subjects or {"signed-token": "founder-apple-subject"}
    emails = emails or {}
    code_subjects = code_subjects or {}
    missing_refresh_codes = missing_refresh_codes or set()

    async def verified(identity_token, _nonce, _config):
        subject = subjects.get(identity_token)
        if subject is None:
            raise authentication.AuthenticationError
        return authentication.AppleClaims(subject=subject, email=emails.get(identity_token))

    async def exchanged(code, _nonce, _config):
        if code.startswith("bad"):
            raise authentication.AuthenticationError
        return authentication.AppleCodeExchange(
            subject=code_subjects.get(code, subjects["signed-token"]),
            refresh_token=None if code in missing_refresh_codes else f"apple-refresh-{code}",
        )

    monkeypatch.setattr(authentication, "verify_apple_identity_token", verified)
    monkeypatch.setattr(authentication, "exchange_apple_code", exchanged)
    monkeypatch.setattr(
        authentication,
        "_encrypt_apple_token",
        lambda token, _config: f"encrypted:{token}",
    )
async def _claim_body(client, **overrides) -> dict[str, str]:
    nonce = (await client.post("/auth/nonce")).json()["nonce"]
    return {
        "identity_token": "signed-token",
        "authorization_code": "single-use-code",
        "nonce": nonce,
        "display_name": "Casey",
        **overrides,
    }


def _configured_apple_settings():
    return get_settings().model_copy(
        update={
            "apple_client_id": "com.example.devmax",
            "apple_team_id": "apple-team",
            "apple_key_id": "apple-key",
            "apple_private_key": "private-key",
            "auth_encryption_key": "encryption-key",
        }
    )


class _AppleTokenResponse:
    def __init__(self, payload=None, *, malformed: bool = False, status_code: int = 200):
        self.payload = payload
        self.malformed = malformed
        self.status_code = status_code

    def json(self):
        if self.malformed:
            raise ValueError
        return self.payload


class _AppleTokenClient:
    def __init__(self, response, calls):
        self.response = response
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def _stub_apple_token_endpoint(monkeypatch, response):
    calls = []
    client = _AppleTokenClient(response, calls)
    monkeypatch.setattr(authentication.httpx, "AsyncClient", lambda **_kwargs: client)
    monkeypatch.setattr(authentication, "_apple_client_secret", lambda _config: "client-secret")
    return calls


async def test_apple_code_exchange_verifies_returned_identity_with_original_nonce(
    monkeypatch,
):
    calls = _stub_apple_token_endpoint(
        monkeypatch,
        _AppleTokenResponse(
            {"id_token": "token-endpoint-identity", "refresh_token": "apple-refresh"}
        ),
    )
    verified = []

    async def verify(identity_token, nonce, _config):
        verified.append((identity_token, nonce))
        return authentication.AppleClaims(subject="stable-subject", email=None)

    monkeypatch.setattr(authentication, "verify_apple_identity_token", verify)
    result = await authentication.exchange_apple_code(
        "authorization-code", "original-raw-nonce", _configured_apple_settings()
    )

    assert result == authentication.AppleCodeExchange(
        subject="stable-subject", refresh_token="apple-refresh"
    )
    assert verified == [("token-endpoint-identity", "original-raw-nonce")]
    assert calls[0][0] == authentication.APPLE_TOKEN_URL
    assert calls[0][1]["data"]["code"] == "authorization-code"


@pytest.mark.parametrize("id_token", [None, "", 42])
async def test_apple_code_exchange_rejects_a_missing_or_invalid_identity_token(
    monkeypatch, id_token
):
    _stub_apple_token_endpoint(
        monkeypatch,
        _AppleTokenResponse({"id_token": id_token, "refresh_token": "apple-refresh"}),
    )
    with pytest.raises(authentication.AuthenticationError):
        await authentication.exchange_apple_code(
            "authorization-code", "original-raw-nonce", _configured_apple_settings()
        )


async def test_apple_code_exchange_rejects_an_unverifiable_identity_token(monkeypatch):
    _stub_apple_token_endpoint(
        monkeypatch,
        _AppleTokenResponse(
            {"id_token": "invalid-identity", "refresh_token": "apple-refresh"}
        ),
    )

    async def invalid(*_args):
        raise authentication.AuthenticationError

    monkeypatch.setattr(authentication, "verify_apple_identity_token", invalid)
    with pytest.raises(authentication.AuthenticationError):
        await authentication.exchange_apple_code(
            "authorization-code", "original-raw-nonce", _configured_apple_settings()
        )


async def test_apple_code_exchange_treats_malformed_json_as_provider_unavailable(
    monkeypatch,
):
    _stub_apple_token_endpoint(monkeypatch, _AppleTokenResponse(malformed=True))
    with pytest.raises(authentication.AuthenticationUnavailable):
        await authentication.exchange_apple_code(
            "authorization-code", "original-raw-nonce", _configured_apple_settings()
        )

    _stub_apple_token_endpoint(monkeypatch, _AppleTokenResponse(["not", "an", "object"]))
    with pytest.raises(authentication.AuthenticationUnavailable):
        await authentication.exchange_apple_code(
            "authorization-code", "original-raw-nonce", _configured_apple_settings()
        )


async def test_nonce_is_public_and_single_use(client, db, monkeypatch):
    from app.routers import authentication as authentication_router

    # Exercise ordinary post-cutover signup while an existing founder identity
    # proves the public path remains independent from the migration endpoint.
    db.add(AppleIdentity(user_id=FOUNDER_USER_ID, subject="founder-subject"))
    await db.commit()
    public_config = get_settings().model_copy(update={"founder_claim_token": ""})
    monkeypatch.setattr(authentication_router, "get_settings", lambda: public_config)
    first = await client.post("/auth/nonce")
    assert first.status_code == 200
    nonce = first.json()["nonce"]

    async def verified(_identity_token, supplied_nonce, _config):
        assert supplied_nonce == nonce
        return authentication.AppleClaims(subject="apple-subject-1", email="relay@example.com")

    async def exchanged(_code, _nonce, _config):
        return authentication.AppleCodeExchange(
            subject="apple-subject-1", refresh_token="apple-refresh"
        )

    monkeypatch.setattr(authentication, "verify_apple_identity_token", verified)
    monkeypatch.setattr(authentication, "exchange_apple_code", exchanged)
    monkeypatch.setattr(
        authentication,
        "_encrypt_apple_token",
        lambda token, _config: f"encrypted:{token}",
    )

    body = {
        "identity_token": "signed-token",
        "authorization_code": "single-use-code",
        "nonce": nonce,
        "display_name": "Casey",
    }
    signed_in = await client.post("/auth/apple", json=body)
    assert signed_in.status_code == 200
    tokens = signed_in.json()
    assert tokens["token_type"] == "bearer"

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200

    identity = (
        await db.exec(select(AppleIdentity).where(AppleIdentity.subject == "apple-subject-1"))
    ).one()
    assert identity.subject == "apple-subject-1"
    assert identity.email == "relay@example.com"
    assert identity.display_name == "Casey"
    assert (await db.exec(select(Settings).where(Settings.user_id == identity.user_id))).one()

    replay = await client.post("/auth/apple", json=body)
    assert replay.status_code == 401


async def test_public_apple_signup_waits_until_the_claim_window_closes(
    client, db, monkeypatch
):
    from app.routers import authentication as authentication_router

    _stub_apple(
        monkeypatch,
        subjects={"signed-token": "new-public-subject"},
        code_subjects={"single-use-code": "new-public-subject"},
    )
    blocked = await client.post("/auth/apple", json=await _claim_body(client))
    assert blocked.status_code == 401
    assert len((await db.exec(select(User))).all()) == 1

    public_config = get_settings().model_copy(update={"founder_claim_token": ""})
    monkeypatch.setattr(authentication_router, "get_settings", lambda: public_config)
    admitted = await client.post("/auth/apple", json=await _claim_body(client))
    assert admitted.status_code == 200
    assert len((await db.exec(select(User))).all()) == 2


async def test_founder_claim_links_identity_without_touching_existing_study_data(
    client, db, monkeypatch
):
    from tests.conftest import FOUNDER_CLAIM_HEADERS

    settings = (
        await db.exec(select(Settings).where(Settings.user_id == FOUNDER_USER_ID))
    ).one()
    settings.reviews_per_day = 5
    settings.timezone = "America/New_York"
    db.add(settings)
    card = Card(
        user_id=FOUNDER_USER_ID,
        topic="Founder history",
        category="System Design",
        canonical_question="Explain the founder history.",
        answer_anchor="A stable answer.",
        mastery_summary="Previously scored evidence.",
        ease_factor=2.35,
        interval_days=13,
        repetitions=4,
        next_review_at=date.today(),
        missed_count=2,
    )
    db.add(card)
    await db.flush()
    db.add(
        Session(
            card_id=card.id,
            question_asked=card.canonical_question or "",
            answer_text="An old answer.",
            score=4,
            accuracy=4,
            depth=3,
            boundaries=3,
            feedback="Historical feedback.",
            status="complete",
        )
    )
    db.add(DeviceToken(token="founder-device-token", user_id=FOUNDER_USER_ID))
    db.add(PendingCapture(user_id=FOUNDER_USER_ID, topic="Founder pending capture"))
    db.add(LLMUsage(user_id=FOUNDER_USER_ID, operation="historical_scoring"))
    guide_draft = StudyPlanGuideDraft(
        user_id=FOUNDER_USER_ID,
        guide_text="Existing draft text.",
        requested_weeks=12,
        weekly_capacity_minutes=1200,
        start_date=date.today(),
    )
    db.add(guide_draft)
    await db.flush()
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Founder source",
        source_text="Existing source text.",
        plan_draft_id=guide_draft.id,
    )
    db.add(source)
    await db.flush()
    db.add(
        MaterialTopicProposal(
            source_id=source.id,
            position=1,
            topic="Founder source topic",
            answer_anchor="Historical source authority.",
        )
    )

    plan = StudyPlan(
        user_id=FOUNDER_USER_ID,
        title="Founder plan",
        subject="System design",
        subject_slug="system-design",
        guide_text="Existing guide text.",
        status="active",
        mode="flexible",
        start_date=date.today(),
        default_weekly_capacity_minutes=1200,
        current_week_index=3,
        forecast_end_plan_week=12,
    )
    duplicate_plan = StudyPlan(
        user_id=FOUNDER_USER_ID,
        title="Founder plan copy",
        subject="System design",
        subject_slug="system-design",
        guide_text="Existing guide text.",
        status="paused",
        mode="flexible",
        start_date=date.today(),
        default_weekly_capacity_minutes=1200,
        current_week_index=1,
        forecast_end_plan_week=12,
    )
    db.add(plan)
    db.add(duplicate_plan)
    await db.flush()
    phase = StudyPlanPhase(
        plan_id=plan.id,
        index=1,
        full_title="Founder phase",
        overview_title="Phase",
    )
    db.add(phase)
    await db.flush()
    week = StudyPlanWeek(
        plan_id=plan.id,
        phase_id=phase.id,
        index=1,
        full_title="Founder week",
        overview_title="Week",
    )
    db.add(week)
    await db.flush()
    first_item = StudyPlanItem(
        plan_id=plan.id,
        phase_id=phase.id,
        week_id=week.id,
        guide_order=1,
        type="learn",
        priority="core",
        full_title="Founder learn item",
        estimate_minutes=60,
        notes="Historical item note.",
    )
    second_item = StudyPlanItem(
        plan_id=plan.id,
        phase_id=phase.id,
        week_id=week.id,
        guide_order=2,
        type="practice",
        priority="core",
        full_title="Founder practice item",
        estimate_minutes=60,
    )
    db.add(first_item)
    db.add(second_item)
    await db.flush()
    db.add(
        StudyPlanItemDependency(
            plan_id=plan.id,
            prerequisite_item_id=first_item.id,
            dependent_item_id=second_item.id,
            rationale="Historical dependency.",
        )
    )
    db.add(
        StudyPlanRevision(
            plan_id=plan.id,
            kind="created",
            base_plan_revision=0,
            after={"historical": True},
            summary="Existing plan created.",
        )
    )
    db.add(
        StudyPlanPracticeDebrief(
            plan_id=plan.id,
            plan_item_id=second_item.id,
            draft_text="Historical debrief draft.",
        )
    )
    card_proposal = StudyPlanCardProposal(
        plan_id=plan.id,
        source_plan_item_id=first_item.id,
        topic="Founder proposed topic",
        canonical_question="Explain the proposed topic.",
        normalized_topic="founder proposed topic",
    )
    db.add(card_proposal)
    await db.flush()
    acceptance = StudyPlanCardProposalAcceptance(
        proposal_id=card_proposal.id,
        idempotency_key="historical-acceptance",
        request_hash="historical-hash",
        proposal_revision=1,
        status="committed",
        created_card_ids=[str(card.id)],
        committed_at=datetime.now(UTC),
    )
    db.add(acceptance)
    await db.flush()
    db.add(
        StudyPlanCardLink(
            plan_id=plan.id,
            plan_item_id=first_item.id,
            card_id=card.id,
            acceptance_id=acceptance.id,
        )
    )
    db.add(
        StudyPlanDuplication(
            source_plan_id=plan.id,
            duplicated_plan_id=duplicate_plan.id,
        )
    )
    await db.commit()
    before = await _founder_study_data_snapshot(db)
    _stub_apple(
        monkeypatch,
        emails={
            "signed-token": "first@example.com",
            "retry-token": "updated@example.com",
        },
        subjects={
            "signed-token": "founder-apple-subject",
            "retry-token": "founder-apple-subject",
        },
        code_subjects={
            "single-use-code": "founder-apple-subject",
            "retry-code": "founder-apple-subject",
        },
    )

    body = await _claim_body(client)
    claimed = await client.post(
        "/auth/founder/apple-claim", headers=FOUNDER_CLAIM_HEADERS, json=body
    )
    assert claimed.status_code == 200
    first_tokens = claimed.json()
    me = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {first_tokens['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json() == {
        "id": str(FOUNDER_USER_ID),
        "onboarding_completed": True,
        "is_founder": True,
        "display_name": "Casey",
        "email": "first@example.com",
        "apple_user_identifier": "founder-apple-subject",
        "ai_consent_status": "pending",
        "ai_consent_version": "",
        "ai_consent_updated_at": None,
        "ai_processing_allowed": False,
        "ai_consent_prompt_required": True,
    }
    assert await _founder_study_data_snapshot(db) == before

    # A fresh Apple proof for the same subject safely recovers a lost response.
    retry_body = await _claim_body(
        client,
        identity_token="retry-token",
        authorization_code="retry-code",
        display_name="Casey Updated",
    )
    retried = await client.post(
        "/auth/founder/apple-claim", headers=FOUNDER_CLAIM_HEADERS, json=retry_body
    )
    assert retried.status_code == 200
    assert retried.json()["access_token"] != first_tokens["access_token"]
    identity = (
        await db.exec(select(AppleIdentity).where(AppleIdentity.user_id == FOUNDER_USER_ID))
    ).one()
    assert identity.subject == "founder-apple-subject"
    assert identity.email == "updated@example.com"
    assert identity.display_name == "Casey Updated"
    assert identity.apple_refresh_token == "encrypted:apple-refresh-retry-code"
    assert len(
        (await db.exec(select(AuthSession).where(AuthSession.user_id == FOUNDER_USER_ID))).all()
    ) == 2
    assert await _founder_study_data_snapshot(db) == before

    # The original nonce/code request is still a replay, not an idempotent retry.
    replay = await client.post(
        "/auth/founder/apple-claim", headers=FOUNDER_CLAIM_HEADERS, json=body
    )
    assert replay.status_code == 401


async def test_founder_claim_requires_its_own_configured_secret(client, monkeypatch):
    from app import auth as auth_module
    from tests.conftest import API_HEADERS, FOUNDER_CLAIM_HEADERS

    body = await _claim_body(client)
    assert (await client.post("/auth/founder/apple-claim", json=body)).status_code == 401
    assert (
        await client.post("/auth/founder/apple-claim", headers=API_HEADERS, json=body)
    ).status_code == 401
    assert (
        await client.post(
            "/auth/founder/apple-claim",
            headers={"Authorization": "Bearer not-a-claim-secret"},
            json=body,
        )
    ).status_code == 401

    disabled = get_settings().model_copy(update={"founder_claim_token": ""})
    monkeypatch.setattr(auth_module, "get_settings", lambda: disabled)
    assert (
        await client.post(
            "/auth/founder/apple-claim", headers=FOUNDER_CLAIM_HEADERS, json=body
        )
    ).status_code == 401


async def test_founder_claim_rejects_bad_nonce_and_bad_code(client, db, monkeypatch):
    from tests.conftest import FOUNDER_CLAIM_HEADERS

    _stub_apple(monkeypatch)
    bad_nonce = {
        "identity_token": "signed-token",
        "authorization_code": "single-use-code",
        "nonce": "not-an-issued-nonce",
    }
    assert (
        await client.post(
            "/auth/founder/apple-claim", headers=FOUNDER_CLAIM_HEADERS, json=bad_nonce
        )
    ).status_code == 401

    bad_code = await _claim_body(client, authorization_code="bad-code")
    assert (
        await client.post(
            "/auth/founder/apple-claim", headers=FOUNDER_CLAIM_HEADERS, json=bad_code
        )
    ).status_code == 401
    assert not (
        await db.exec(select(AppleIdentity).where(AppleIdentity.user_id == FOUNDER_USER_ID))
    ).all()
    assert not (
        await db.exec(select(AuthSession).where(AuthSession.user_id == FOUNDER_USER_ID))
    ).all()


async def test_founder_claim_binds_the_authorization_code_subject_and_refresh_token(
    client, db, monkeypatch
):
    from tests.conftest import FOUNDER_CLAIM_HEADERS

    _stub_apple(
        monkeypatch,
        code_subjects={"single-use-code": "different-code-subject"},
    )
    mismatch = await client.post(
        "/auth/founder/apple-claim",
        headers=FOUNDER_CLAIM_HEADERS,
        json=await _claim_body(client),
    )
    assert mismatch.status_code == 401

    _stub_apple(monkeypatch, missing_refresh_codes={"single-use-code"})
    no_refresh = await client.post(
        "/auth/founder/apple-claim",
        headers=FOUNDER_CLAIM_HEADERS,
        json=await _claim_body(client),
    )
    assert no_refresh.status_code == 401
    assert not (
        await db.exec(select(AppleIdentity).where(AppleIdentity.user_id == FOUNDER_USER_ID))
    ).all()


async def test_founder_claim_rejects_non_founder_and_subject_owned_elsewhere(
    client, db, monkeypatch
):
    from tests.conftest import FOUNDER_CLAIM_HEADERS

    _stub_apple(monkeypatch, subjects={"signed-token": "already-owned-subject"})
    other = User()
    db.add(other)
    await db.flush()
    other_id = other.id
    db.add(
        AppleIdentity(user_id=other_id, subject="already-owned-subject")
    )
    await db.commit()
    taken = await client.post(
        "/auth/founder/apple-claim",
        headers=FOUNDER_CLAIM_HEADERS,
        json=await _claim_body(client),
    )
    assert taken.status_code == 401
    assert not (
        await db.exec(select(AppleIdentity).where(AppleIdentity.user_id == FOUNDER_USER_ID))
    ).all()

    other_identity = (
        await db.exec(select(AppleIdentity).where(AppleIdentity.user_id == other_id))
    ).one()
    await db.delete(other_identity)
    founder = await db.get(User, FOUNDER_USER_ID)
    assert founder is not None
    founder.is_founder = False
    db.add(founder)
    await db.commit()
    non_founder = await client.post(
        "/auth/founder/apple-claim",
        headers=FOUNDER_CLAIM_HEADERS,
        json=await _claim_body(client),
    )
    assert non_founder.status_code == 401
    assert not (
        await db.exec(select(AppleIdentity).where(AppleIdentity.user_id == FOUNDER_USER_ID))
    ).all()


async def test_founder_claim_rejects_a_different_subject_after_binding(
    client, db, monkeypatch
):
    from tests.conftest import FOUNDER_CLAIM_HEADERS

    _stub_apple(
        monkeypatch,
        subjects={"signed-token": "founder-subject", "other-token": "other-subject"},
        code_subjects={
            "single-use-code": "founder-subject",
            "other-code": "other-subject",
        },
    )
    first = await client.post(
        "/auth/founder/apple-claim",
        headers=FOUNDER_CLAIM_HEADERS,
        json=await _claim_body(client),
    )
    assert first.status_code == 200
    different = await client.post(
        "/auth/founder/apple-claim",
        headers=FOUNDER_CLAIM_HEADERS,
        json=await _claim_body(
            client,
            identity_token="other-token",
            authorization_code="other-code",
        ),
    )
    assert different.status_code == 401
    identities = (
        await db.exec(select(AppleIdentity).where(AppleIdentity.user_id == FOUNDER_USER_ID))
    ).all()
    assert [identity.subject for identity in identities] == ["founder-subject"]


async def _postgres_auth_factory(db):
    if db.bind.dialect.name != "postgresql":
        pytest.skip("row-lock concurrency is verified against Postgres")

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlmodel.ext.asyncio.session import AsyncSession

    from tests.conftest import TEST_DATABASE_URL

    # The ordinary Postgres fixture deliberately uses StaticPool. A separate
    # engine is required here so the two transactions really hold independent
    # connections and exercise SELECT FOR UPDATE rather than sharing one socket.
    engine = create_async_engine(TEST_DATABASE_URL)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _concurrent_apple_stubs(monkeypatch, subjects: dict[str, str]):
    both_verified = asyncio.Event()
    arrivals = 0

    async def verified(identity_token, _nonce, _config):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            both_verified.set()
        await asyncio.wait_for(both_verified.wait(), timeout=5)
        return authentication.AppleClaims(subject=subjects[identity_token], email=None)

    async def exchanged(code, _nonce, _config):
        return authentication.AppleCodeExchange(
            subject=subjects[code], refresh_token=f"refresh-{code}"
        )

    monkeypatch.setattr(authentication, "verify_apple_identity_token", verified)
    monkeypatch.setattr(authentication, "exchange_apple_code", exchanged)
    monkeypatch.setattr(
        authentication,
        "_encrypt_apple_token",
        lambda token, _config: f"encrypted:{token}",
    )


async def test_postgres_concurrent_different_subject_claims_choose_exactly_one_founder(
    db, monkeypatch
):
    engine, factory = await _postgres_auth_factory(db)
    _concurrent_apple_stubs(
        monkeypatch,
        {
            "identity-a": "subject-a",
            "code-a": "subject-a",
            "identity-b": "subject-b",
            "code-b": "subject-b",
        },
    )
    try:
        async with factory() as first_nonce_db:
            first_nonce = await authentication.issue_nonce(first_nonce_db)
        async with factory() as second_nonce_db:
            second_nonce = await authentication.issue_nonce(second_nonce_db)

        async def claim(identity_token, code, nonce):
            async with factory() as claim_db:
                try:
                    await authentication.claim_founder_with_apple(
                        claim_db,
                        identity_token=identity_token,
                        authorization_code=code,
                        nonce=nonce,
                        display_name=None,
                        config=get_settings(),
                    )
                    return "claimed"
                except authentication.AuthenticationError:
                    await claim_db.rollback()
                    return "rejected"

        results = await asyncio.gather(
            claim("identity-a", "code-a", first_nonce),
            claim("identity-b", "code-b", second_nonce),
        )
        assert sorted(results) == ["claimed", "rejected"]

        db.expire_all()
        identities = (await db.exec(select(AppleIdentity))).all()
        assert len(identities) == 1
        assert identities[0].user_id == FOUNDER_USER_ID
        assert identities[0].subject in {"subject-a", "subject-b"}
        assert len((await db.exec(select(User))).all()) == 1
        assert len((await db.exec(select(AuthSession))).all()) == 1
    finally:
        await engine.dispose()


async def test_postgres_concurrent_same_subject_claims_are_recoverably_idempotent(
    db, monkeypatch
):
    engine, factory = await _postgres_auth_factory(db)
    _concurrent_apple_stubs(
        monkeypatch,
        {
            "identity-a": "same-subject",
            "code-a": "same-subject",
            "identity-b": "same-subject",
            "code-b": "same-subject",
        },
    )
    try:
        async with factory() as first_nonce_db:
            first_nonce = await authentication.issue_nonce(first_nonce_db)
        async with factory() as second_nonce_db:
            second_nonce = await authentication.issue_nonce(second_nonce_db)

        async def claim(identity_token, code, nonce):
            async with factory() as claim_db:
                _, pair = await authentication.claim_founder_with_apple(
                    claim_db,
                    identity_token=identity_token,
                    authorization_code=code,
                    nonce=nonce,
                    display_name=None,
                    config=get_settings(),
                )
                return pair.access_token

        access_tokens = await asyncio.gather(
            claim("identity-a", "code-a", first_nonce),
            claim("identity-b", "code-b", second_nonce),
        )
        assert access_tokens[0] != access_tokens[1]

        db.expire_all()
        identities = (await db.exec(select(AppleIdentity))).all()
        assert len(identities) == 1
        assert identities[0].user_id == FOUNDER_USER_ID
        assert identities[0].subject == "same-subject"
        assert len((await db.exec(select(User))).all()) == 1
        assert len((await db.exec(select(AuthSession))).all()) == 2
    finally:
        await engine.dispose()


async def test_postgres_public_sign_in_cannot_race_the_founder_subject_into_a_new_user(
    db, monkeypatch
):
    engine, factory = await _postgres_auth_factory(db)
    _concurrent_apple_stubs(
        monkeypatch,
        {
            "claim-identity": "founder-subject",
            "claim-code": "founder-subject",
            "public-identity": "founder-subject",
            "public-code": "founder-subject",
        },
    )
    try:
        async with factory() as claim_nonce_db:
            claim_nonce = await authentication.issue_nonce(claim_nonce_db)
        async with factory() as public_nonce_db:
            public_nonce = await authentication.issue_nonce(public_nonce_db)

        async def claim():
            async with factory() as claim_db:
                await authentication.claim_founder_with_apple(
                    claim_db,
                    identity_token="claim-identity",
                    authorization_code="claim-code",
                    nonce=claim_nonce,
                    display_name=None,
                    config=get_settings(),
                )
                return "claimed"

        async def public_sign_in():
            async with factory() as sign_in_db:
                try:
                    user, _ = await authentication.sign_in_with_apple(
                        sign_in_db,
                        identity_token="public-identity",
                        authorization_code="public-code",
                        nonce=public_nonce,
                        display_name=None,
                        config=get_settings(),
                    )
                    return user.id
                except authentication.AuthenticationError:
                    await sign_in_db.rollback()
                    return None

        claimed, public_user_id = await asyncio.gather(claim(), public_sign_in())
        assert claimed == "claimed"
        assert public_user_id in {None, FOUNDER_USER_ID}

        db.expire_all()
        identities = (await db.exec(select(AppleIdentity))).all()
        assert [(row.user_id, row.subject) for row in identities] == [
            (FOUNDER_USER_ID, "founder-subject")
        ]
        assert len((await db.exec(select(User))).all()) == 1
    finally:
        await engine.dispose()


def test_apple_receives_a_sha256_nonce_while_devmax_keeps_the_raw_value():
    raw = "single-use-raw-nonce"
    assert authentication.apple_nonce(raw) == authentication.token_hash(raw)
    assert authentication.apple_nonce(raw) != raw


async def test_refresh_rotates_and_replay_revokes_the_family(client, db):
    user = User()
    db.add(user)
    await db.flush()
    pair = await authentication.issue_session(db, user.id, get_settings())
    await db.commit()

    rotated = await client.post("/auth/refresh", json={"refresh_token": pair.refresh_token})
    assert rotated.status_code == 200
    replacement = rotated.json()
    assert replacement["refresh_token"] != pair.refresh_token

    replay = await client.post("/auth/refresh", json={"refresh_token": pair.refresh_token})
    assert replay.status_code == 401

    # Replaying the predecessor revokes its replacement too, not only the old row.
    me = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {replacement['access_token']}"},
    )
    assert me.status_code == 401
    family = (await db.exec(select(AuthSession).where(AuthSession.user_id == user.id))).all()
    assert len(family) == 2
    assert all(row.revoked_at is not None for row in family)


async def test_cards_sessions_settings_and_plans_are_isolated(client, db):
    first, first_headers, first_card = await _account(db, topic="First user's topic")
    second, second_headers, second_card = await _account(db, topic="Second user's topic")

    first_due = (await client.get("/cards/due", headers=first_headers)).json()
    second_due = (await client.get("/cards/due", headers=second_headers)).json()
    assert [row["topic"] for row in first_due] == ["First user's topic"]
    assert [row["topic"] for row in second_due] == ["Second user's topic"]

    assert (await client.get(f"/cards/{second_card.id}", headers=first_headers)).status_code == 404
    assert (
        await client.post(f"/cards/{second_card.id}/sessions", headers=first_headers)
    ).status_code == 404

    started = await client.post(f"/cards/{first_card.id}/sessions", headers=first_headers)
    assert started.status_code == 200
    session_id = started.json()["session_id"]
    assert (
        await client.patch(
            f"/sessions/{session_id}/draft",
            headers=second_headers,
            json={"draft_text": "not mine"},
        )
    ).status_code == 404

    first_settings = await client.put(
        "/settings",
        headers=first_headers,
        json={
            "reviews_per_day": 5,
            "timezone": "America/New_York",
            "windows": [{"label": "Morning", "from": "07:00", "to": "08:00", "on": True}],
        },
    )
    assert first_settings.status_code == 200
    assert (await client.get("/settings", headers=second_headers)).json()["reviews_per_day"] == 2

    plan = StudyPlan(
        user_id=second.id,
        title="Private plan",
        subject="Law",
        subject_slug="law",
        guide_text="source",
        status="paused",
        mode="flexible",
        start_date=date.today(),
        default_weekly_capacity_minutes=60,
        current_week_index=1,
        forecast_end_plan_week=1,
    )
    db.add(plan)
    await db.commit()
    assert (await client.get(f"/study-plans/{plan.id}", headers=first_headers)).status_code == 404

    # The account objects themselves remain distinct throughout the request walk.
    assert first.id != second.id


async def test_legacy_api_key_can_only_see_the_founder_account(client, db):
    _, _, foreign = await _account(db, topic="Not founder data")
    from tests.conftest import API_HEADERS

    assert (await client.get(f"/cards/{foreign.id}", headers=API_HEADERS)).status_code == 404


async def test_legacy_api_key_can_be_disabled_without_disabling_bearer_auth(
    client, db, monkeypatch
):
    from app import auth as auth_module
    from tests.conftest import API_HEADERS

    pair = await authentication.issue_session(db, FOUNDER_USER_ID, get_settings())
    await db.commit()
    disabled = get_settings().model_copy(update={"legacy_api_key_auth_enabled": False})
    monkeypatch.setattr(auth_module, "get_settings", lambda: disabled)

    assert (await client.get("/auth/me", headers=API_HEADERS)).status_code == 401
    bearer = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {pair.access_token}"}
    )
    assert bearer.status_code == 200
    assert bearer.json()["id"] == str(FOUNDER_USER_ID)


async def test_onboarding_completion_and_export_are_account_scoped(client, db):
    user, headers, card = await _account(db, topic="Exported topic")
    profile = await client.post("/auth/onboarding/complete", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["onboarding_completed"] is True

    exported = await client.get("/auth/export", headers=headers)
    assert exported.status_code == 200
    body = exported.json()
    assert body["account"]["id"] == str(user.id)
    assert [row["id"] for row in body["cards"]] == [str(card.id)]
    assert all(row["topic"] != "Consistent hashing" for row in body["cards"])


async def test_account_deletion_revokes_apple_before_cascading(client, db, monkeypatch):
    if db.bind.dialect.name == "sqlite":
        await db.exec(text("PRAGMA foreign_keys=ON"))
    user, headers, _ = await _account(db, topic="Deleted topic")
    db.add(
        AppleIdentity(
            user_id=user.id,
            subject="delete-subject",
            apple_refresh_token="encrypted-refresh",
        )
    )
    await db.commit()
    revoked = []

    async def revoke(token, _config):
        revoked.append(token)

    monkeypatch.setattr(
        authentication, "decrypt_apple_token", lambda _token, _config: "apple-refresh"
    )
    monkeypatch.setattr(authentication, "revoke_apple_authorization", revoke)
    response = await client.delete("/auth/account", headers=headers)
    assert response.status_code == 204
    assert revoked == ["apple-refresh"]
    assert await db.get(User, user.id) is None
    assert not (await db.exec(select(Card).where(Card.user_id == user.id))).all()


async def test_invalid_bearer_token_is_rejected(client):
    response = await client.get("/cards/due", headers={"Authorization": f"Bearer {uuid.uuid4()}"})
    assert response.status_code == 401
