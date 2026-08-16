"""Serialized account storage guardrails for user-supplied retained content."""

import uuid

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import MaterialSource, StudyPlan, StudyPlanGuideDraft
from app.services import ai_consent

MAX_MATERIAL_SOURCES_PER_USER = 100
MAX_MATERIAL_SOURCE_CHARACTERS_PER_USER = 20_000_000
MAX_STUDY_PLAN_DRAFTS_PER_USER = 50
MAX_STUDY_PLANS_PER_USER = 50
MAX_STUDY_GUIDE_CHARACTERS_PER_USER = 20_000_000


async def reserve_material_source(
    db: AsyncSession, *, user_id: uuid.UUID, characters: int
) -> None:
    """Hold the account boundary through the caller's insert/commit."""
    if await ai_consent.lock_user_boundary(db, user_id) is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    count, stored_characters = (
        await db.exec(
            select(
                func.count(MaterialSource.id),
                func.coalesce(func.sum(func.length(MaterialSource.source_text)), 0),
            ).where(MaterialSource.user_id == user_id)
        )
    ).one()
    if (
        count >= MAX_MATERIAL_SOURCES_PER_USER
        or stored_characters + characters > MAX_MATERIAL_SOURCE_CHARACTERS_PER_USER
    ):
        raise HTTPException(
            status_code=429,
            detail={
                "code": "storage_quota_exceeded",
                "resource": "material_sources",
                "max_sources": MAX_MATERIAL_SOURCES_PER_USER,
                "max_characters": MAX_MATERIAL_SOURCE_CHARACTERS_PER_USER,
            },
        )


async def _study_guide_usage(
    db: AsyncSession, user_id: uuid.UUID
) -> tuple[int, int, int]:
    draft_count, draft_characters = (
        await db.exec(
            select(
                func.count(StudyPlanGuideDraft.id),
                func.coalesce(func.sum(func.length(StudyPlanGuideDraft.guide_text)), 0),
            ).where(StudyPlanGuideDraft.user_id == user_id)
        )
    ).one()
    plan_count, plan_characters = (
        await db.exec(
            select(
                func.count(StudyPlan.id),
                func.coalesce(func.sum(func.length(StudyPlan.guide_text)), 0),
            ).where(StudyPlan.user_id == user_id)
        )
    ).one()
    return draft_count, plan_count, draft_characters + plan_characters


def _study_quota_error(resource: str) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={
            "code": "storage_quota_exceeded",
            "resource": resource,
            "max_drafts": MAX_STUDY_PLAN_DRAFTS_PER_USER,
            "max_plans": MAX_STUDY_PLANS_PER_USER,
            "max_characters": MAX_STUDY_GUIDE_CHARACTERS_PER_USER,
        },
    )


async def reserve_study_guide_draft(
    db: AsyncSession, *, user_id: uuid.UUID, characters: int
) -> None:
    if await ai_consent.lock_user_boundary(db, user_id) is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    drafts, _plans, stored_characters = await _study_guide_usage(db, user_id)
    if (
        drafts >= MAX_STUDY_PLAN_DRAFTS_PER_USER
        or stored_characters + characters > MAX_STUDY_GUIDE_CHARACTERS_PER_USER
    ):
        raise _study_quota_error("study_plan_drafts")


async def reserve_study_plan(
    db: AsyncSession, *, user_id: uuid.UUID, characters: int
) -> None:
    if await ai_consent.lock_user_boundary(db, user_id) is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    _drafts, plans, stored_characters = await _study_guide_usage(db, user_id)
    if (
        plans >= MAX_STUDY_PLANS_PER_USER
        or stored_characters + characters > MAX_STUDY_GUIDE_CHARACTERS_PER_USER
    ):
        raise _study_quota_error("study_plans")
