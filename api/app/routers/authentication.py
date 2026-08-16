import hashlib
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import bearer_token, current_user_id, require_user
from app.config import get_settings
from app.db import get_session
from app.models import (
    LESSON_CHECK_TRANSFER,
    AIConsentEvent,
    AppleIdentity,
    AppleNotificationReceipt,
    AuthSession,
    Card,
    DeviceToken,
    LessonCheck,
    LessonProposalAudit,
    LLMUsage,
    MaterialSource,
    MaterialTopicProposal,
    PendingCapture,
    Session,
    SessionProbe,
    Settings,
    StudyPilotAssignment,
    StudyPilotEnrollment,
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
from app.pilot_contract import TRANSFER_OPENED_AT_KEY
from app.routers.deps import as_utc
from app.schemas import (
    AccountExport,
    AIConsentIn,
    AIConsentOut,
    AppleServerNotificationIn,
    AppleSignInIn,
    AuthNonceOut,
    CurrentUserOut,
    RefreshTokenIn,
    TokenOut,
)
from app.services import ai_consent, authentication

router = APIRouter(prefix="/auth", tags=["authentication"])


def _out(pair: authentication.TokenPair) -> TokenOut:
    return TokenOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        access_expires_at=pair.access_expires_at,
        refresh_expires_at=pair.refresh_expires_at,
    )


def _export_row(
    row, *, exclude: set[str] | None = None, **safe_overrides
) -> dict[str, object]:
    data = row.model_dump(exclude=exclude or set())
    data.update(safe_overrides)
    return data


def _device_fingerprint(token: str) -> str:
    # 96 bits is enough to distinguish a user's registrations while remaining
    # a one-way, deliberately truncated identifier rather than an APNs credential.
    return f"sha256:{hashlib.sha256(token.encode('utf-8')).hexdigest()[:24]}"


@router.post("/nonce", response_model=AuthNonceOut)
async def nonce(db: AsyncSession = Depends(get_session)) -> AuthNonceOut:
    return AuthNonceOut(nonce=await authentication.issue_nonce(db))


@router.post("/apple", response_model=TokenOut)
async def apple_sign_in(body: AppleSignInIn, db: AsyncSession = Depends(get_session)) -> TokenOut:
    try:
        _, pair = await authentication.sign_in_with_apple(
            db,
            identity_token=body.identity_token,
            authorization_code=body.authorization_code,
            nonce=body.nonce,
            display_name=body.display_name,
            config=get_settings(),
        )
    except authentication.AuthenticationUnavailable as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail="sign_in_unavailable") from exc
    except authentication.AuthenticationError as exc:
        await db.rollback()
        raise HTTPException(status_code=401, detail="unauthorized") from exc
    return _out(pair)


@router.post("/founder/apple-claim", response_model=TokenOut)
async def founder_apple_claim(
    body: AppleSignInIn, db: AsyncSession = Depends(get_session)
) -> TokenOut:
    """One-time founder migration; middleware requires its dedicated secret."""
    try:
        _, pair = await authentication.claim_founder_with_apple(
            db,
            identity_token=body.identity_token,
            authorization_code=body.authorization_code,
            nonce=body.nonce,
            display_name=body.display_name,
            config=get_settings(),
        )
    except authentication.AuthenticationUnavailable as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail="sign_in_unavailable") from exc
    except authentication.AuthenticationError as exc:
        await db.rollback()
        raise HTTPException(status_code=401, detail="unauthorized") from exc
    return _out(pair)


@router.post("/refresh", response_model=TokenOut)
async def refresh(body: RefreshTokenIn, db: AsyncSession = Depends(get_session)) -> TokenOut:
    try:
        return _out(
            await authentication.rotate_refresh_token(db, body.refresh_token, get_settings())
        )
    except authentication.AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="unauthorized") from exc


@router.get("/me", response_model=CurrentUserOut, dependencies=[Depends(require_user)])
async def me(db: AsyncSession = Depends(get_session)) -> CurrentUserOut:
    config = get_settings()
    user = await db.get(User, current_user_id())
    identity = (
        await db.exec(select(AppleIdentity).where(AppleIdentity.user_id == current_user_id()))
    ).first()
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return CurrentUserOut(
        id=user.id,
        onboarding_completed=user.onboarding_completed,
        is_founder=user.is_founder,
        display_name=identity.display_name if identity and identity.display_name else "",
        email=identity.email if identity and identity.email else "",
        apple_user_identifier=identity.subject if identity else "",
        ai_consent_status=user.ai_consent_status,
        ai_consent_version=user.ai_consent_version,
        ai_consent_updated_at=user.ai_consent_updated_at,
        ai_processing_allowed=ai_consent.processing_allowed(
            user, config.ai_consent_required_policy_version
        ),
        ai_consent_prompt_required=ai_consent.prompt_required(
            user, config.ai_consent_required_policy_version
        ),
    )


@router.put(
    "/ai-consent", response_model=AIConsentOut, dependencies=[Depends(require_user)]
)
async def update_ai_consent(
    body: AIConsentIn, db: AsyncSession = Depends(get_session)
) -> AIConsentOut:
    config = get_settings()
    user, changed_at = await ai_consent.record(
        db,
        current_user_id(),
        body.action,
        body.policy_version,
        config.ai_consent_required_policy_version,
    )
    recorded_policy = ai_consent.policy_for(user.ai_consent_version)
    return AIConsentOut(
        provider=recorded_policy.provider,
        policy_version=recorded_policy.version,
        status=user.ai_consent_status,
        updated_at=changed_at,
        processing_allowed=ai_consent.processing_allowed(
            user, config.ai_consent_required_policy_version
        ),
        prompt_required=ai_consent.prompt_required(
            user, config.ai_consent_required_policy_version
        ),
    )


@router.post("/apple/notifications", status_code=204)
async def apple_notifications(
    body: AppleServerNotificationIn, db: AsyncSession = Depends(get_session)
) -> Response:
    """Invalidate access on verified Apple account-change events without deleting study data."""
    try:
        event = await authentication.verify_apple_server_notification(
            body.payload, get_settings()
        )
    except authentication.AuthenticationUnavailable as exc:
        raise HTTPException(status_code=503, detail="notification_unavailable") from exc
    except authentication.AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="unauthorized") from exc

    # The signed JTI, not Apple's second-resolution event timestamp, is the
    # notification's idempotency key. This fast path avoids taking the identity
    # lock for ordinary provider retries.
    receipt = (
        await db.exec(
            select(AppleNotificationReceipt).where(
                AppleNotificationReceipt.jti == event.jti
            )
        )
    ).first()
    if receipt is not None:
        return Response(status_code=204)

    identity = (
        await db.exec(
            select(AppleIdentity)
            .where(AppleIdentity.subject == event.subject)
            .with_for_update()
        )
    ).first()
    if identity is None:
        return Response(status_code=204)

    # Concurrent retries for one subject serialize on the identity. Re-check
    # after acquiring it so exactly one request applies and records the JTI.
    receipt = (
        await db.exec(
            select(AppleNotificationReceipt).where(
                AppleNotificationReceipt.jti == event.jti
            )
        )
    ).first()
    if receipt is not None:
        return Response(status_code=204)

    now = datetime.now(UTC)
    applied = False
    if event.event_type in {"consent-revoked", "account-deleted"}:
        # Only a successful authorization can make a security event stale.
        # Email notifications use last_apple_event_at for their own ordering but
        # must never create an authorization boundary. Equality is applied:
        # Apple's timestamps have one-second precision and distinct same-second
        # notifications are disambiguated by JTI.
        authorized_at = (
            as_utc(identity.last_apple_authorized_at)
            if identity.last_apple_authorized_at is not None
            else None
        )
        if authorized_at is None or event.occurred_at >= authorized_at:
            identity.apple_refresh_token = None
            if (
                identity.authorization_revoked_at is None
                or event.occurred_at > as_utc(identity.authorization_revoked_at)
            ):
                identity.authorization_revoked_at = event.occurred_at
            sessions = (
                await db.exec(
                    select(AuthSession).where(
                        AuthSession.user_id == identity.user_id,
                        col(AuthSession.revoked_at).is_(None),
                    )
                )
            ).all()
            for session in sessions:
                session.revoked_at = now
                session.updated_at = now
                db.add(session)
            applied = True
    else:
        boundaries = [
            boundary
            for boundary in (
                identity.last_apple_authorized_at,
                identity.last_apple_event_at,
            )
            if boundary is not None
        ]
        if not boundaries or event.occurred_at >= max(map(as_utc, boundaries)):
            if event.event_type == "email-disabled":
                identity.email = None
            applied = True

    if (
        identity.last_apple_event_at is None
        or event.occurred_at > as_utc(identity.last_apple_event_at)
    ):
        identity.last_apple_event_at = event.occurred_at
    identity.updated_at = now
    db.add(identity)
    db.add(
        AppleNotificationReceipt(
            identity_id=identity.id,
            jti=event.jti,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            applied=applied,
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        # The global unique JTI remains the final arbiter if an impossible
        # cross-subject replay races two different identity locks.
        await db.rollback()
    return Response(status_code=204)


@router.post(
    "/onboarding/complete", response_model=CurrentUserOut, dependencies=[Depends(require_user)]
)
async def complete_onboarding(db: AsyncSession = Depends(get_session)) -> CurrentUserOut:
    user = await db.get(User, current_user_id())
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    user.onboarding_completed = True
    user.updated_at = datetime.now(UTC)
    db.add(user)
    await db.commit()
    return await me(db)


@router.get("/export", response_model=AccountExport, dependencies=[Depends(require_user)])
async def export_account(db: AsyncSession = Depends(get_session)) -> AccountExport:
    user_id = current_user_id()
    user = await db.get(User, user_id)
    if user is None:  # pragma: no cover - require_user already established it
        raise HTTPException(status_code=401, detail="unauthorized")
    settings = (
        await db.exec(select(Settings).where(Settings.user_id == user_id))
    ).first()
    identity = (
        await db.exec(select(AppleIdentity).where(AppleIdentity.user_id == user_id))
    ).first()
    notification_receipts = (
        (
            await db.exec(
                select(AppleNotificationReceipt)
                .where(AppleNotificationReceipt.identity_id == identity.id)
                .order_by(
                    AppleNotificationReceipt.created_at,
                    AppleNotificationReceipt.id,
                )
            )
        ).all()
        if identity is not None
        else []
    )
    auth_sessions = (
        await db.exec(
            select(AuthSession)
            .where(AuthSession.user_id == user_id)
            .order_by(AuthSession.created_at, AuthSession.id)
        )
    ).all()
    auth_session_ids = {row.id for row in auth_sessions}
    devices = (
        await db.exec(
            select(DeviceToken)
            .where(DeviceToken.user_id == user_id)
            .order_by(DeviceToken.created_at, DeviceToken.token)
        )
    ).all()

    guide_drafts = (
        await db.exec(
            select(StudyPlanGuideDraft)
            .where(StudyPlanGuideDraft.user_id == user_id)
            .order_by(StudyPlanGuideDraft.created_at, StudyPlanGuideDraft.id)
        )
    ).all()
    guide_draft_ids = {row.id for row in guide_drafts}
    sources = (
        await db.exec(
            select(MaterialSource)
            .where(MaterialSource.user_id == user_id)
            .order_by(MaterialSource.created_at, MaterialSource.id)
        )
    ).all()
    source_ids = {row.id for row in sources}
    material_proposals = (
        (
            await db.exec(
                select(MaterialTopicProposal)
                .where(MaterialTopicProposal.source_id.in_(source_ids))
                .order_by(
                    MaterialTopicProposal.source_id,
                    MaterialTopicProposal.position,
                    MaterialTopicProposal.id,
                )
            )
        ).all()
        if source_ids
        else []
    )
    material_proposal_ids = {row.id for row in material_proposals}

    cards = (
        await db.exec(
            select(Card)
            .where(Card.user_id == user_id)
            .order_by(Card.created_at, Card.id)
        )
    ).all()
    card_ids = {row.id for row in cards}
    card_id_strings = {str(card_id) for card_id in card_ids}
    captures = (
        await db.exec(
            select(PendingCapture)
            .where(PendingCapture.user_id == user_id)
            .order_by(PendingCapture.created_at, PendingCapture.id)
        )
    ).all()
    sessions = (
        await db.exec(
            select(Session)
            .join(Card, Card.id == Session.card_id)
            .where(Card.user_id == user_id)
            .order_by(Session.started_at, Session.id)
        )
    ).all()
    session_ids = {row.id for row in sessions}
    probes = (
        (
            await db.exec(
                select(SessionProbe)
                .where(SessionProbe.session_id.in_(session_ids))
                .order_by(SessionProbe.session_id, SessionProbe.idx, SessionProbe.id)
            )
        ).all()
        if session_ids
        else []
    )

    plans = (
        await db.exec(
            select(StudyPlan)
            .where(StudyPlan.user_id == user_id)
            .order_by(StudyPlan.created_at, StudyPlan.id)
        )
    ).all()
    plan_ids = {row.id for row in plans}
    phases = (
        (
            await db.exec(
                select(StudyPlanPhase)
                .where(StudyPlanPhase.plan_id.in_(plan_ids))
                .order_by(StudyPlanPhase.plan_id, StudyPlanPhase.index, StudyPlanPhase.id)
            )
        ).all()
        if plan_ids
        else []
    )
    phase_ids = {row.id for row in phases}
    weeks = (
        (
            await db.exec(
                select(StudyPlanWeek)
                .where(
                    StudyPlanWeek.plan_id.in_(plan_ids),
                    StudyPlanWeek.phase_id.in_(phase_ids),
                )
                .order_by(StudyPlanWeek.plan_id, StudyPlanWeek.index, StudyPlanWeek.id)
            )
        ).all()
        if phase_ids
        else []
    )
    week_ids = {row.id for row in weeks}
    items = (
        (
            await db.exec(
                select(StudyPlanItem)
                .where(
                    StudyPlanItem.plan_id.in_(plan_ids),
                    StudyPlanItem.phase_id.in_(phase_ids),
                    StudyPlanItem.week_id.in_(week_ids),
                )
                .order_by(
                    StudyPlanItem.plan_id,
                    StudyPlanItem.week_id,
                    StudyPlanItem.guide_order,
                    StudyPlanItem.id,
                )
            )
        ).all()
        if week_ids
        else []
    )
    item_ids = {row.id for row in items}
    dependencies = (
        (
            await db.exec(
                select(StudyPlanItemDependency)
                .where(
                    StudyPlanItemDependency.plan_id.in_(plan_ids),
                    StudyPlanItemDependency.prerequisite_item_id.in_(item_ids),
                    StudyPlanItemDependency.dependent_item_id.in_(item_ids),
                )
                .order_by(
                    StudyPlanItemDependency.plan_id,
                    StudyPlanItemDependency.created_at,
                    StudyPlanItemDependency.id,
                )
            )
        ).all()
        if item_ids
        else []
    )
    revisions = (
        (
            await db.exec(
                select(StudyPlanRevision)
                .where(StudyPlanRevision.plan_id.in_(plan_ids))
                .order_by(StudyPlanRevision.created_at, StudyPlanRevision.id)
            )
        ).all()
        if plan_ids
        else []
    )
    debriefs = (
        (
            await db.exec(
                select(StudyPlanPracticeDebrief)
                .where(
                    StudyPlanPracticeDebrief.plan_id.in_(plan_ids),
                    StudyPlanPracticeDebrief.plan_item_id.in_(item_ids),
                )
                .order_by(
                    StudyPlanPracticeDebrief.created_at,
                    StudyPlanPracticeDebrief.id,
                )
            )
        ).all()
        if item_ids
        else []
    )
    card_proposals = (
        (
            await db.exec(
                select(StudyPlanCardProposal)
                .where(
                    StudyPlanCardProposal.plan_id.in_(plan_ids),
                    StudyPlanCardProposal.source_plan_item_id.in_(item_ids),
                )
                .order_by(
                    StudyPlanCardProposal.created_at,
                    StudyPlanCardProposal.id,
                )
            )
        ).all()
        if item_ids
        else []
    )
    card_proposal_ids = {row.id for row in card_proposals}
    acceptances = (
        (
            await db.exec(
                select(StudyPlanCardProposalAcceptance)
                .where(
                    StudyPlanCardProposalAcceptance.plan_id.in_(plan_ids),
                    StudyPlanCardProposalAcceptance.proposal_id.in_(
                        card_proposal_ids
                    ),
                )
                .order_by(
                    StudyPlanCardProposalAcceptance.created_at,
                    StudyPlanCardProposalAcceptance.id,
                )
            )
        ).all()
        if card_proposal_ids
        else []
    )
    acceptance_ids = {row.id for row in acceptances}
    links = (
        (
            await db.exec(
                select(StudyPlanCardLink)
                .where(
                    StudyPlanCardLink.plan_id.in_(plan_ids),
                    StudyPlanCardLink.plan_item_id.in_(item_ids),
                    StudyPlanCardLink.card_id.in_(card_ids),
                    StudyPlanCardLink.acceptance_id.in_(acceptance_ids),
                )
                .order_by(StudyPlanCardLink.created_at, StudyPlanCardLink.id)
            )
        ).all()
        if acceptance_ids and card_ids
        else []
    )
    duplications = (
        (
            await db.exec(
                select(StudyPlanDuplication)
                .where(
                    StudyPlanDuplication.source_plan_id.in_(plan_ids),
                    StudyPlanDuplication.duplicated_plan_id.in_(plan_ids),
                )
                .order_by(StudyPlanDuplication.created_at, StudyPlanDuplication.id)
            )
        ).all()
        if plan_ids
        else []
    )

    consent_events = (
        await db.exec(
            select(AIConsentEvent)
            .where(AIConsentEvent.user_id == user_id)
            .order_by(AIConsentEvent.created_at)
        )
    ).all()
    model_usage = (
        await db.exec(
            select(LLMUsage)
            .where(LLMUsage.user_id == user_id)
            .order_by(LLMUsage.created_at, LLMUsage.id)
        )
    ).all()
    lesson_checks = (
        await db.exec(
            select(LessonCheck)
            .where(LessonCheck.user_id == user_id)
            .order_by(LessonCheck.started_at, LessonCheck.id)
        )
    ).all()
    proposal_audits = (
        await db.exec(
            select(LessonProposalAudit)
            .join(MaterialSource, MaterialSource.id == LessonProposalAudit.source_id)
            .where(MaterialSource.user_id == user_id)
            .order_by(LessonProposalAudit.created_at, LessonProposalAudit.id)
        )
    ).all()
    pilot_enrollments = (
        await db.exec(
            select(StudyPilotEnrollment)
            .where(StudyPilotEnrollment.user_id == user_id)
            .order_by(StudyPilotEnrollment.created_at, StudyPilotEnrollment.id)
        )
    ).all()
    enrollment_ids = [row.id for row in pilot_enrollments]
    pilot_assignments = (
        (
            await db.exec(
                select(StudyPilotAssignment)
                .where(col(StudyPilotAssignment.enrollment_id).in_(enrollment_ids))
                .order_by(
                    StudyPilotAssignment.enrollment_id,
                    StudyPilotAssignment.sequence_index,
                )
            )
        ).all()
        if enrollment_ids
        else []
    )
    return AccountExport(
        schema_version=2,
        exported_at=datetime.now(UTC),
        account=user.model_dump(),
        settings=settings.model_dump() if settings else {},
        apple_identity=(
            identity.model_dump(exclude={"apple_refresh_token"})
            if identity is not None
            else None
        ),
        apple_notification_receipts=[row.model_dump() for row in notification_receipts],
        auth_sessions=[
            _export_row(
                row,
                exclude={"access_token_hash", "refresh_token_hash"},
                rotated_from_id=(
                    row.rotated_from_id
                    if row.rotated_from_id in auth_session_ids
                    else None
                ),
            )
            for row in auth_sessions
        ],
        devices=[
            {
                "user_id": row.user_id,
                "kind": row.kind,
                "token_fingerprint": _device_fingerprint(row.token),
                "created_at": row.created_at,
            }
            for row in devices
        ],
        sources=[
            _export_row(
                row,
                previous_version_id=(
                    row.previous_version_id
                    if row.previous_version_id in source_ids
                    else None
                ),
                plan_draft_id=(
                    row.plan_draft_id if row.plan_draft_id in guide_draft_ids else None
                ),
            )
            for row in sources
        ],
        material_topic_proposals=[
            _export_row(
                row,
                merged_into_id=(
                    row.merged_into_id
                    if row.merged_into_id in material_proposal_ids
                    else None
                ),
                card_id=row.card_id if row.card_id in card_ids else None,
            )
            for row in material_proposals
        ],
        cards=[
            _export_row(
                row,
                source_id=row.source_id if row.source_id in source_ids else None,
                replaces_card_id=(
                    row.replaces_card_id if row.replaces_card_id in card_ids else None
                ),
                replaced_by_card_id=(
                    row.replaced_by_card_id
                    if row.replaced_by_card_id in card_ids
                    else None
                ),
            )
            for row in cards
        ],
        pending_captures=[
            _export_row(
                row,
                activated_card_id=(
                    row.activated_card_id if row.activated_card_id in card_ids else None
                ),
            )
            for row in captures
        ],
        sessions=[row.model_dump() for row in sessions],
        session_probes=[row.model_dump() for row in probes],
        study_plans=[row.model_dump() for row in plans],
        study_plan_phases=[row.model_dump() for row in phases],
        study_plan_weeks=[row.model_dump() for row in weeks],
        study_plan_items=[
            _export_row(
                row,
                source_item_id=(
                    row.source_item_id if row.source_item_id in item_ids else None
                ),
            )
            for row in items
        ],
        study_plan_item_dependencies=[row.model_dump() for row in dependencies],
        study_plan_revisions=[row.model_dump() for row in revisions],
        study_plan_guide_drafts=[row.model_dump() for row in guide_drafts],
        study_plan_practice_debriefs=[row.model_dump() for row in debriefs],
        study_plan_card_proposals=[
            _export_row(
                row,
                duplicate_card_id=(
                    row.duplicate_card_id if row.duplicate_card_id in card_ids else None
                ),
            )
            for row in card_proposals
        ],
        study_plan_card_proposal_acceptances=[
            _export_row(
                row,
                created_card_ids=[
                    card_id
                    for card_id in row.created_card_ids
                    if card_id in card_id_strings
                ],
            )
            for row in acceptances
        ],
        study_plan_card_links=[row.model_dump() for row in links],
        study_plan_duplications=[row.model_dump() for row in duplications],
        ai_consent_events=[row.model_dump() for row in consent_events],
        llm_usage=[row.model_dump() for row in model_usage],
        lesson_checks=[
            row.model_dump()
            for row in lesson_checks
            if row.kind != LESSON_CHECK_TRANSFER
            or isinstance(row.provider_route.get(TRANSFER_OPENED_AT_KEY), str)
        ],
        lesson_proposal_audits=[row.model_dump() for row in proposal_audits],
        study_pilot_enrollments=[row.model_dump() for row in pilot_enrollments],
        study_pilot_assignments=[row.model_dump() for row in pilot_assignments],
    )


@router.delete("/account", status_code=204, dependencies=[Depends(require_user)])
async def delete_account(db: AsyncSession = Depends(get_session)) -> Response:
    # Read the Apple credential without locking the User.  The network revocation
    # can be slow, and holding a User row lock across it would block independent
    # provider-audit FK inserts and can deadlock a provider transaction.
    user = await db.get(User, current_user_id())
    if user is None:
        return Response(status_code=204)
    identity = (
        await db.exec(select(AppleIdentity).where(AppleIdentity.user_id == user.id))
    ).first()
    encrypted_apple_token = identity.apple_refresh_token if identity else None
    # End the read transaction before external I/O.  No deletion state has been
    # changed, so an Apple failure still leaves the account wholly intact.
    await db.rollback()
    if encrypted_apple_token:
        try:
            token = authentication.decrypt_apple_token(encrypted_apple_token, get_settings())
            await authentication.revoke_apple_authorization(token, get_settings())
        except authentication.AuthenticationUnavailable as exc:
            raise HTTPException(status_code=503, detail="deletion_unavailable") from exc
    # Provider authorization, consent changes, result finalization, and deletion
    # share this per-user boundary.  Re-read after the external Apple revocation:
    # another deletion may have completed while no database lock was held.
    user = await ai_consent.lock_user_boundary(db, current_user_id())
    if user is None:
        return Response(status_code=204)
    await db.delete(user)
    await db.commit()
    return Response(status_code=204)


@router.post("/logout", status_code=204, dependencies=[Depends(require_user)])
async def logout(request: Request, db: AsyncSession = Depends(get_session)) -> Response:
    token = bearer_token(request)
    if token:
        await authentication.revoke_access_token(db, token)
    return Response(status_code=204)
