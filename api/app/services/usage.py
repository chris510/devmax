"""Small account-level budgets around paid model calls."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import Settings
from app.models import LLMUsage
from app.services import ai_consent

SCORING_INTENT_OPERATION = "score_v2_intent"
ProviderBoundaryCheck = Callable[[AsyncSession], Awaitable[None]]


class ScoringAuditIncomplete(RuntimeError):
    """Terminal evidence did not satisfy the pre-call provider manifest."""


async def ensure_available(
    db: AsyncSession,
    user_id: uuid.UUID,
    operation: str,
    config: Settings,
    *,
    requested_calls: int = 1,
    enforce_operation_limit: bool = True,
    consent_boundary_locked: bool = False,
) -> None:
    """Apply the existing best-effort daily guard before provider transmission.

    This count check is not an atomic reservation: concurrent requests can
    observe the same prior count. Deployment-level provider spend caps own the
    hard billing ceiling. ``consent_boundary_locked`` is valid only when the
    caller already ran ``require_ai_processing`` in this same transaction.
    """
    if not consent_boundary_locked:
        await ai_consent.require_ai_processing(db, user_id, config)
    since = datetime.now(UTC) - timedelta(days=1)
    terminal_total = (
        await db.exec(
            select(func.count())
            .select_from(LLMUsage)
            .where(
                LLMUsage.user_id == user_id,
                LLMUsage.created_at >= since,
                # A scoring intent is durable crash-gap evidence, not another
                # provider call. Terminal rows below remain the physical-call
                # units counted against the daily safeguard.
                LLMUsage.operation != SCORING_INTENT_OPERATION,
            )
        )
    ).one()
    pending_intents = (
        await db.exec(
            select(LLMUsage.details).where(
                LLMUsage.user_id == user_id,
                LLMUsage.operation == SCORING_INTENT_OPERATION,
                LLMUsage.created_at >= since,
            )
        )
    ).all()
    pending_reserved_calls = sum(
        max(
            0,
            int(details.get("reserved_calls", 0))
            - int(details.get("terminal_call_count", 0)),
        )
        for details in pending_intents
        if details.get("status") in {"pending", "incomplete"}
    )
    total = terminal_total + pending_reserved_calls
    if requested_calls < 1:
        raise ValueError("requested_calls must be positive")
    if total + requested_calls > config.llm_calls_per_day:
        raise HTTPException(status_code=429, detail="daily_model_limit")
    if operation == "guide_import" and enforce_operation_limit:
        imports = (
            await db.exec(
                select(func.count())
                .select_from(LLMUsage)
                .where(
                    LLMUsage.user_id == user_id,
                    LLMUsage.operation == operation,
                    LLMUsage.created_at >= since,
                )
            )
        ).one()
        if imports >= config.guide_imports_per_day:
            raise HTTPException(status_code=429, detail="daily_import_limit")


def record(
    db: AsyncSession,
    user_id: uuid.UUID,
    operation: str,
    *,
    call_details: list[dict[str, object]] | None = None,
) -> None:
    """Stage one row per physical paid call, or one legacy logical row.

    Shadow and fallback intentionally count twice against the daily safeguard.
    Details contain operational metadata only; learner content stays in the
    existing session tables and never enters this cost/audit surface.
    """
    details = call_details or [{}]
    for item in details:
        db.add(LLMUsage(user_id=user_id, operation=operation, details=item))


async def authorize_provider_call(
    db: AsyncSession,
    user_id: uuid.UUID,
    operation: str,
    *,
    config: Settings,
    provider: str,
    model: str,
    operation_id: uuid.UUID,
    attempt: int,
    boundary_check: ProviderBoundaryCheck | None = None,
) -> None:
    """Authorize one long-running physical call at its transmission boundary.

    The current grant and both daily guards are checked while holding the
    account's transaction advisory boundary.  A caller-supplied resource check
    then proves that the exact import lease is still live.  The authorization
    record is committed immediately before the SDK receives the request, which
    gives withdrawal, resource deletion/lease takeover, and transmission a
    deterministic order without pinning a pooled connection for an 11-minute
    guide import.  Each explicit parse retry crosses this boundary again.
    """
    if attempt < 1:
        raise ValueError("attempt must be positive")
    await ensure_available(
        db,
        user_id,
        operation,
        config,
        # A parse retry is another paid call, but not another user-requested
        # guide import.  It counts against the account call cap below without
        # consuming a second logical import slot.
        enforce_operation_limit=attempt == 1,
    )
    if boundary_check is not None:
        # Lock order is global: account advisory boundary first, child import
        # row second.  If deletion/takeover committed first this fails closed;
        # if this lock won, the authorization commit below releases it before
        # the long provider await.
        await boundary_check(db)
    recorded_operation = operation if attempt == 1 else f"{operation}_retry"
    record(
        db,
        user_id,
        recorded_operation,
        call_details=[
            {
                "audit_type": "provider_call_authorization",
                "outcome": "authorized",
                "authorized_at": datetime.now(UTC).isoformat(),
                "logical_operation": operation,
                "operation_id": str(operation_id),
                "provider_attempt": attempt,
                "provider": provider,
                "model": model,
                "ai_consent_policy_version": ai_consent.POLICY_VERSION,
                "ai_consent_verified": config.ai_consent_enforcement_enabled,
            }
        ],
    )
    await db.commit()


def provider_call_authorizer(
    db: AsyncSession,
    user_id: uuid.UUID,
    operation: str,
    *,
    config: Settings,
    provider: str,
    model: str,
    boundary_check: ProviderBoundaryCheck | None = None,
) -> Callable[[int], Awaitable[None]]:
    """Build the one-operation callback consumed by the provider call loop."""
    operation_id = uuid.uuid4()

    async def authorize(attempt: int) -> None:
        await authorize_provider_call(
            db,
            user_id,
            operation,
            config=config,
            provider=provider,
            model=model,
            operation_id=operation_id,
            attempt=attempt,
            boundary_check=boundary_check,
        )

    return authorize


async def lock_account_for_provider_result(
    db: AsyncSession, user_id: uuid.UUID
) -> bool:
    """Order a short result write against concurrent account deletion.

    Provider authorization releases its user lock before the long await.  On
    return, callers acquire the user first and only then write draft/source
    children.  If deletion won the race, no result is persisted; if this lock
    wins, deletion waits for the short result transaction and then cascades it.
    """
    user = await ai_consent.lock_user_boundary(db, user_id)
    return user is not None


async def record_physical_calls(
    db: AsyncSession,
    user_id: uuid.UUID,
    operation: str,
    *,
    call_details: list[dict[str, object]] | None,
    intent_id: uuid.UUID | None = None,
) -> bool:
    """Durably record provider attempts without releasing the answer lock.

    Production Postgres uses an independent transaction immediately after the
    provider result, before any answer/card mutation. A later scheduler or
    product-transaction failure therefore cannot erase billable-call evidence.
    SQLite's single-connection test setup stages the rows on the caller instead;
    callers commit that session on a failure path and otherwise commit it with
    the normal answer transaction.

    Returns true when the evidence was independently committed.
    """
    if not call_details:
        return False
    bind = db.bind
    if bind is None:
        raise RuntimeError("cannot persist model-call evidence without a database bind")
    if bind.dialect.name == "sqlite":
        complete = await _stage_physical_calls(
            db,
            user_id=user_id,
            operation=operation,
            call_details=call_details,
            intent_id=intent_id,
        )
        if not complete:
            # No product mutation has happened at this point. Preserve the
            # crash-gap evidence before forcing the route to return 503.
            await db.commit()
            raise ScoringAuditIncomplete(
                "terminal calls do not satisfy the scoring intent"
            )
        return False

    async with AsyncSession(bind=bind, expire_on_commit=False) as audit_db:
        complete = await _stage_physical_calls(
            audit_db,
            user_id=user_id,
            operation=operation,
            call_details=call_details,
            intent_id=intent_id,
        )
        await audit_db.commit()
    if not complete:
        raise ScoringAuditIncomplete(
            "terminal calls do not satisfy the scoring intent"
        )
    return True


async def _stage_physical_calls(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    operation: str,
    call_details: list[dict[str, object]],
    intent_id: uuid.UUID | None,
) -> bool:
    """Stage terminal rows and reconcile their optional intent in one session."""
    record(db, user_id, operation, call_details=call_details)
    if intent_id is None:
        return True
    return await _finalize_scoring_intent(
        db,
        intent_id=intent_id,
        user_id=user_id,
        call_details=call_details,
    )


async def _finalize_scoring_intent(
    db: AsyncSession,
    *,
    intent_id: uuid.UUID,
    user_id: uuid.UUID,
    call_details: list[dict[str, object]],
) -> bool:
    """Reconcile the manifest in the terminal-row transaction.

    A complete provider set finalizes and releases the reservation. Partial
    traces remain an explicit audit gap, reserving only the still-unaccounted
    calls. The caller then fails the product request after this transaction is
    durable, so incomplete evidence can neither qualify nor mutate learning
    state.
    """
    intent = await db.get(LLMUsage, intent_id, with_for_update=True)
    if (
        intent is None
        or intent.user_id != user_id
        or intent.operation != SCORING_INTENT_OPERATION
    ):
        raise RuntimeError("scoring intent is missing or belongs to another event")
    details = dict(intent.details)
    if details.get("status") != "pending":
        raise RuntimeError("scoring intent is already reconciled")
    event_id = details.get("scoring_event_id")
    if not isinstance(event_id, str) or not event_id:
        raise RuntimeError("scoring intent has no event identifier")
    expected_calls = details.get("expected_calls")
    if not isinstance(expected_calls, list):
        raise RuntimeError("scoring intent has no expected-call manifest")
    expected_providers = {
        item.get("provider")
        for item in expected_calls
        if isinstance(item, dict) and isinstance(item.get("provider"), str)
    }
    required_providers = {
        item.get("provider")
        for item in expected_calls
        if isinstance(item, dict) and item.get("requirement") == "required"
    }
    if not required_providers or not required_providers <= expected_providers:
        raise RuntimeError("scoring intent has an invalid required-call manifest")
    terminal_providers: list[str] = []
    terminal_by_provider: dict[str, dict[str, object]] = {}
    for terminal in call_details:
        if terminal.get("scoring_event_id") != event_id:
            raise RuntimeError("terminal scoring row belongs to another event")
        call = terminal.get("call")
        provider = call.get("provider") if isinstance(call, dict) else None
        if not isinstance(provider, str) or provider not in expected_providers:
            raise RuntimeError("terminal scoring row has an unexpected provider")
        terminal_providers.append(provider)
        terminal_by_provider[provider] = terminal
    if len(set(terminal_providers)) != len(terminal_providers):
        raise RuntimeError("terminal scoring rows repeat a provider")
    terminal_provider_set = set(terminal_providers)
    conditional_providers = {
        item.get("provider")
        for item in expected_calls
        if isinstance(item, dict)
        and item.get("requirement") == "conditional_fallback"
    }
    complete = False
    if expected_providers == {"anthropic"} and not conditional_providers:
        # Claude-only route, including a kill-switch/qualification reversion.
        complete = terminal_provider_set == {"anthropic"}
    elif required_providers == {"anthropic", "openai"}:
        # Shadow always starts both providers concurrently and therefore owes
        # exactly one terminal row from each, success or failure.
        complete = terminal_provider_set == {"anthropic", "openai"}
    elif required_providers == {"openai"} and conditional_providers == {
        "anthropic"
    }:
        # Primary calls Claude only after a typed OpenAI failure. A successful
        # OpenAI response must not be accompanied by an unnecessary fallback;
        # a failed response must never release the reservation without one.
        openai_terminal = terminal_by_provider.get("openai")
        openai_call = (
            openai_terminal.get("call")
            if isinstance(openai_terminal, dict)
            else None
        )
        openai_outcome = (
            openai_call.get("outcome") if isinstance(openai_call, dict) else None
        )
        authoritative = {
            item.get("authoritative_provider") for item in call_details
        }
        fallback_reasons = {item.get("fallback_reason") for item in call_details}
        if openai_outcome == "success":
            complete = (
                terminal_provider_set == {"openai"}
                and authoritative == {"openai"}
                and fallback_reasons == {""}
            )
        elif isinstance(openai_outcome, str) and openai_outcome:
            error_type = (
                openai_call.get("error_type") if isinstance(openai_call, dict) else None
            )
            complete = (
                terminal_provider_set == {"openai", "anthropic"}
                and authoritative == {"anthropic"}
                and len(fallback_reasons) == 1
                and isinstance(next(iter(fallback_reasons)), str)
                and bool(next(iter(fallback_reasons)))
                and isinstance(error_type, str)
                and bool(error_type)
            )
    details["status"] = "finalized" if complete else "incomplete"
    details["finalized_at"] = datetime.now(UTC).isoformat() if complete else None
    details["terminal_call_count"] = len(call_details)
    intent.details = details
    db.add(intent)
    return complete


async def record_scoring_intent(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    details: dict[str, object],
) -> uuid.UUID:
    """Commit a content-free expected-call manifest before orchestration.

    On Postgres this deliberately uses an independent transaction. If the app
    process disappears after a provider receives the request, the event still
    occupies its chronological place and the missing terminal call row becomes
    a rollout-blocking crash gap instead of silently disappearing from first-N.
    The caller's Card/session transaction stays untouched and can still roll
    back atomically. SQLite retains the suite's single-connection fallback.
    """
    bind = db.bind
    if bind is None:
        raise RuntimeError("cannot persist scoring intent without a database bind")
    payload = dict(details)
    stage_id = payload.get("shadow_stage_id")
    if isinstance(stage_id, str) and stage_id:
        prior_ordinals = (
            await db.exec(
                select(LLMUsage.details["shadow_stage_ordinal"]).where(
                    LLMUsage.user_id == user_id,
                    LLMUsage.operation == SCORING_INTENT_OPERATION,
                    LLMUsage.details["shadow_stage_id"].as_string() == stage_id,
                )
            )
        ).all()
        valid_ordinals = [
            ordinal
            for ordinal in prior_ordinals
            if isinstance(ordinal, int) and not isinstance(ordinal, bool)
        ]
        payload["shadow_stage_ordinal"] = max(valid_ordinals, default=0) + 1

    intent_id = uuid.uuid4()
    intent = LLMUsage(
        id=intent_id,
        user_id=user_id,
        operation=SCORING_INTENT_OPERATION,
        details=payload,
    )
    if bind.dialect.name == "sqlite":
        db.add(intent)
        return intent_id

    async with AsyncSession(bind=bind, expire_on_commit=False) as intent_db:
        intent_db.add(intent)
        await intent_db.commit()
    return intent_id
