"""Deterministic first-party Study Plan bootstrap.

The seed is an operational convenience, not a bridge between the two
schedulers. These tests therefore assert both sides: the timeline graph appears
and every existing card/session field remains byte-for-byte unchanged.
"""

import json
from datetime import date, datetime

import pytest
from sqlmodel import select

from app.models import (
    PLAN_ACTIVE,
    Card,
    Session,
    StudyPlan,
    StudyPlanItem,
    StudyPlanPhase,
    StudyPlanRevision,
    StudyPlanWeek,
)
from app.seed_study_plan import (
    DEFAULT_MANIFEST,
    PlanSeedError,
    load_first_party_plan,
    validate_bundle,
)
from tests.conftest import make_card

START = date(2026, 7, 27)


def test_the_committed_bundle_is_a_complete_twelve_week_timeline() -> None:
    manifest, result = validate_bundle(DEFAULT_MANIFEST, start_date=START)
    loads = {week: 0 for week in range(1, 13)}
    for item in result.preview["items"]:
        loads[item["week_index"]] += item["estimate_minutes"]

    assert result.can_create
    assert manifest["seed_key"] == "devmax.senior-backend-12-week.v3"
    assert [
        phase["overview_title"] for phase in result.preview["phases"]
    ] == [
        "Foundations",
        "Patterns and application",
        "Technologies",
        "Simulation",
    ]
    assert len(result.preview["phases"]) == 4
    assert len(result.preview["weeks"]) == 12
    assert len(result.preview["items"]) == 84
    assert loads == {week: 720 for week in range(1, 13)}
    assert {check.status for check in result.checks} == {"ok"}


def test_coding_mechanisms_are_core_and_implementation_is_optional() -> None:
    manifest, result = validate_bundle(DEFAULT_MANIFEST, start_date=START)
    items = result.preview["items"]

    optional_desk = [
        item
        for item in items
        if item["full_title"].startswith("Optional desk implementation:")
    ]
    assert len(optional_desk) == 12
    assert {item["week_index"] for item in optional_desk} == set(range(1, 13))
    assert {item["priority"] for item in optional_desk} == {"optional"}

    core_titles = [
        item["full_title"].lower()
        for item in items
        if item["priority"] == "core"
    ]
    assert not any("timed coding" in title for title in core_titles)
    assert not any("coding mock" in title for title in core_titles)
    assert any("two-pointer" in title and "invariant" in title for title in core_titles)
    assert any("binary-search" in title and "invariant" in title for title in core_titles)

    raw_items = [item for week in manifest["weeks"] for item in week["items"]]
    assert all(
        item["priority"] == "optional"
        for item in raw_items
        if item["title"].startswith("Optional desk implementation:")
    )


async def test_the_seed_creates_an_active_week_one_plan_graph(db) -> None:
    seeded = await load_first_party_plan(
        start_date=START, activate=True, db=db
    )

    plan = (await db.exec(select(StudyPlan))).one()
    phases = (await db.exec(select(StudyPlanPhase))).all()
    weeks = (await db.exec(select(StudyPlanWeek))).all()
    items = (await db.exec(select(StudyPlanItem))).all()
    revision = (await db.exec(select(StudyPlanRevision))).one()

    assert seeded.created
    assert seeded.active
    assert plan.status == PLAN_ACTIVE
    assert plan.current_week_index == 1
    assert plan.start_date == START
    assert plan.forecast_end_plan_week == 12
    assert len(phases) == 4
    assert len(weeks) == 12
    assert len(items) == 84
    assert revision.after["seed_key"] == "devmax.senior-backend-12-week.v3"


async def test_rerunning_the_same_seed_is_idempotent(db) -> None:
    first = await load_first_party_plan(start_date=START, activate=True, db=db)
    second = await load_first_party_plan(start_date=START, activate=True, db=db)

    assert first.plan_id == second.plan_id
    assert first.created
    assert not second.created
    assert len((await db.exec(select(StudyPlan))).all()) == 1
    assert len((await db.exec(select(StudyPlanRevision))).all()) == 1


async def test_the_seed_never_touches_cards_or_sessions(db) -> None:
    card = make_card(
        topic="Existing recall signal",
        ease_factor=1.73,
        interval_days=19,
        repetitions=4,
        next_review_at=date(2026, 9, 1),
        last_score=2,
        mastery_summary="specific existing mastery",
        missed_count=3,
    )
    db.add(card)
    await db.commit()
    session = Session(
        card_id=card.id,
        question_asked="Existing question?",
        answer_text="Existing answer",
        score=2,
        feedback="Existing feedback",
        status="complete",
        ended_at=datetime.fromisoformat("2026-07-20T12:00:00+00:00"),
    )
    db.add(session)
    await db.commit()
    card_before = card.model_dump()
    session_before = session.model_dump()

    await load_first_party_plan(start_date=START, activate=True, db=db)

    card_after = (await db.exec(select(Card))).one()
    session_after = (await db.exec(select(Session))).one()
    assert card_after.model_dump() == card_before
    assert session_after.model_dump() == session_before


async def test_activation_refuses_to_displace_an_existing_active_plan(db) -> None:
    existing = StudyPlan(
        title="Existing",
        subject="Existing",
        subject_slug="existing",
        guide_text="existing",
        status=PLAN_ACTIVE,
        mode="flexible",
        start_date=START,
        default_weekly_capacity_minutes=720,
        forecast_end_plan_week=4,
    )
    db.add(existing)
    await db.commit()

    with pytest.raises(PlanSeedError, match="already active"):
        await load_first_party_plan(start_date=START, activate=True, db=db)

    assert [plan.title for plan in (await db.exec(select(StudyPlan))).all()] == [
        "Existing"
    ]


async def test_an_explicit_start_date_must_be_a_monday(db) -> None:
    with pytest.raises(PlanSeedError, match="must be Mondays"):
        await load_first_party_plan(
            start_date=date(2026, 7, 31), activate=True, db=db
        )

    assert (await db.exec(select(StudyPlan))).all() == []


def test_a_changed_embedded_guide_requires_a_reviewed_manifest_update(tmp_path) -> None:
    manifest = json.loads(DEFAULT_MANIFEST.read_text())
    manifest["guide_text"] = "changed curriculum"
    copied = tmp_path / "plan.json"
    copied.write_text(json.dumps(manifest))

    with pytest.raises(PlanSeedError, match="embedded curriculum guide changed"):
        validate_bundle(copied, start_date=START)


def test_the_bundle_does_not_need_the_repository_docs_at_runtime(tmp_path) -> None:
    manifest = json.loads(DEFAULT_MANIFEST.read_text())
    manifest["source_guide_path"] = "not-in-the-production-image.md"
    copied = tmp_path / "plan.json"
    copied.write_text(json.dumps(manifest))

    _, result = validate_bundle(copied, start_date=START)

    assert result.can_create
