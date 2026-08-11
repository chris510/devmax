"""Deterministic first-party Study Plan bootstrap.

The seed is an operational convenience, not a bridge between the two
schedulers. These tests therefore assert both sides: the timeline graph appears
and every existing card/session field remains byte-for-byte unchanged.
"""

import hashlib
import json
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlmodel import select

from app.models import (
    CONFIDENCE_HIGH,
    DEP_IMPORTED,
    DEP_INFERRED,
    DEP_USER_ADDED,
    FOUNDER_USER_ID,
    ITEM_COMPLETE,
    ITEM_PENDING,
    ITEM_REMOVED,
    PLAN_ACTIVE,
    PLAN_PAUSED,
    REVISION_CREATED,
    Card,
    Session,
    StudyPlan,
    StudyPlanItem,
    StudyPlanItemDependency,
    StudyPlanPhase,
    StudyPlanRevision,
    StudyPlanWeek,
    User,
)
from app.seed_study_plan import (
    DEFAULT_MANIFEST,
    V2_ITEM_KEYS,
    V2_ITEM_TITLES,
    V3_ITEM_KEYS,
    PlanSeedError,
    load_first_party_plan,
    validate_bundle,
)
from app.services import study_plan as sp
from tests.conftest import make_card

START = date(2026, 7, 27)
UPGRADE_START = date(2026, 8, 10)


def test_the_committed_bundle_is_a_complete_twelve_week_timeline() -> None:
    manifest, result = validate_bundle(DEFAULT_MANIFEST, start_date=START)
    loads = {week: 0 for week in range(1, 13)}
    for item in result.preview["items"]:
        loads[item["week_index"]] += item["estimate_minutes"]

    assert result.can_create
    assert manifest["seed_key"] == "devmax.senior-backend-12-week.v4"
    assert manifest["legacy_seed_keys"] == [
        "devmax.senior-backend-12-week.v2",
        "devmax.senior-backend-12-week.v3",
    ]
    assert manifest["version"] == 4
    assert manifest["coding_language"] == "Python"
    assert [phase["overview_title"] for phase in result.preview["phases"]] == [
        "Foundations",
        "Patterns and application",
        "Technologies",
        "Simulation",
    ]
    assert len(result.preview["phases"]) == 4
    assert len(result.preview["weeks"]) == 12
    assert len(result.preview["items"]) == 116
    assert loads == {week: 1200 for week in range(1, 13)}
    assert {check.status for check in result.checks} == {"ok"}


def test_python_practice_is_core_and_extra_volume_is_advisory_stretch() -> None:
    manifest, result = validate_bundle(DEFAULT_MANIFEST, start_date=START)
    items = result.preview["items"]

    raw_items = [item for week in manifest["weeks"] for item in week["items"]]
    coding_resources = [
        resource
        for item in raw_items
        for resource in item["resources"]
        if resource["provider"] == "LeetCode"
    ]
    stretch_owners = [item for item in raw_items if item.get("stretch_actions")]

    assert {item["priority"] for item in items} == {"core", "recurring"}
    assert all(item["priority"] == "recurring" for item in items if item["type"] == "retrieve")
    assert coding_resources
    assert {resource["language"] for resource in coding_resources} == {"Python"}
    assert {resource["action_label"] for resource in coding_resources} == {
        "Solve in Python",
        "Start Python mock",
    }
    assert len(stretch_owners) == 12
    assert {item["key"].split("-")[1] for item in stretch_owners} == {
        f"W{week}" for week in range(1, 13)
    }
    assert all(len(item["stretch_actions"]) == 5 for item in stretch_owners)
    assert all(
        sum(action["minutes"] for action in item["stretch_actions"]) == 1200
        for item in stretch_owners
    )


def test_week_four_names_the_historical_twitter_high_fanout_practice() -> None:
    _manifest, result = validate_bundle(DEFAULT_MANIFEST, start_date=START)
    item = next(
        item
        for item in result.preview["items"]
        if item["key"] == "V4-W4-P1"
    )

    assert item["week_index"] == 4
    assert "Twitter-style home timeline" in item["full_title"]
    assert "ordinary-account materialization" in item["done_when"]
    assert "high-follower merging" in item["done_when"]
    assert "Power-user skew" in item["why_it_matters"]


async def test_the_seed_creates_an_active_week_one_plan_graph(db) -> None:
    seeded = await load_first_party_plan(start_date=START, activate=True, db=db)

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
    assert len(items) == 116
    assert revision.after["seed_key"] == "devmax.senior-backend-12-week.v4"
    assert revision.after["seed_version"] == 4


async def test_rerunning_the_same_seed_is_idempotent(db) -> None:
    first = await load_first_party_plan(start_date=START, activate=True, db=db)
    second = await load_first_party_plan(start_date=START, activate=True, db=db)

    assert first.plan_id == second.plan_id
    assert first.created
    assert not second.created
    assert len((await db.exec(select(StudyPlan))).all()) == 1
    assert len((await db.exec(select(StudyPlanRevision))).all()) == 1


@pytest.mark.parametrize(
    ("legacy_keys", "message"),
    [
        ([], "nonempty list"),
        ([""], "nonempty strings"),
        (
            ["devmax.senior-backend-12-week.v4"],
            "exclude the canonical seed key",
        ),
        (
            [
                "devmax.senior-backend-12-week.v2",
                "devmax.senior-backend-12-week.v2",
            ],
            "must be unique",
        ),
    ],
)
async def test_legacy_seed_aliases_fail_closed_when_malformed(
    db, tmp_path, legacy_keys, message
) -> None:
    manifest = json.loads(DEFAULT_MANIFEST.read_text())
    manifest.pop("source_guide_path", None)
    manifest.pop("source_guide_sha256", None)
    manifest["legacy_seed_keys"] = legacy_keys
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))

    with pytest.raises(PlanSeedError, match=message):
        await load_first_party_plan(path, start_date=START, db=db)

    assert (await db.exec(select(StudyPlan))).all() == []


async def test_two_legacy_alias_claimants_are_rejected_as_ambiguous(db) -> None:
    for suffix in ("v2", "v3"):
        plan = StudyPlan(
            title=f"Legacy {suffix}",
            subject="Backend",
            subject_slug="backend",
            guide_text="legacy",
            status=PLAN_PAUSED,
            mode="flexible",
            start_date=START,
            default_weekly_capacity_minutes=720,
            forecast_end_plan_week=12,
        )
        db.add(plan)
        await db.flush()
        db.add(
            StudyPlanRevision(
                plan_id=plan.id,
                kind=REVISION_CREATED,
                base_plan_revision=1,
                before={},
                after={
                    "seed_key": f"devmax.senior-backend-12-week.{suffix}",
                    "seed_version": int(suffix[-1]),
                    "weeks": 12,
                    "items": 72 if suffix == "v2" else 84,
                },
            )
        )
    await db.commit()

    with pytest.raises(PlanSeedError, match="more than one founder plan"):
        await load_first_party_plan(start_date=START, db=db)

    assert len((await db.exec(select(StudyPlan))).all()) == 2


@pytest.mark.parametrize("valid_legacy_shape", [True, False])
async def test_v4_replaces_only_the_reviewed_pristine_production_v2_shape(
    db, tmp_path, valid_legacy_shape
) -> None:
    manifest = json.loads(DEFAULT_MANIFEST.read_text())
    manifest.pop("source_guide_path", None)
    manifest.pop("source_guide_sha256", None)
    manifest["guide_sha256"] = hashlib.sha256(manifest["guide_text"].encode()).hexdigest()
    target_items = [item for week in manifest["weeks"] for item in week["items"]]
    assert manifest["version"] == 4
    assert all(item["key"].startswith("V4-") for item in target_items)
    assert not ({item["key"] for item in target_items} & set(V3_ITEM_KEYS))
    path = tmp_path / "v4.json"
    path.write_text(json.dumps(manifest))

    # Build the exact deployed v2 shape: 72 reviewed titles in global guide
    # order, no persisted curriculum keys, and the same 12-week shell.
    _target, validated = validate_bundle(path, start_date=START)
    preview = validated.preview
    plan = StudyPlan(
        title="Old first-party plan",
        subject=preview["subject"],
        subject_slug=preview["subject_slug"],
        guide_text="old guide",
        status=PLAN_ACTIVE,
        mode="flexible",
        start_date=START,
        default_weekly_capacity_minutes=720,
        forecast_end_plan_week=12,
    )
    phases = {
        entry["index"]: StudyPlanPhase(
            plan_id=plan.id,
            index=entry["index"],
            full_title=f"Old {entry['index']}",
        )
        for entry in preview["phases"]
    }
    weeks = {
        entry["index"]: StudyPlanWeek(
            plan_id=plan.id,
            phase_id=phases[entry["phase_index"]].id,
            index=entry["index"],
            full_title=f"Old week {entry['index']}",
        )
        for entry in preview["weeks"]
    }

    def week_for(key: str) -> int:
        return int(key.split("-")[0][1:])

    phase_for_week = {entry["index"]: entry["phase_index"] for entry in preview["weeks"]}
    old_items = []
    for order, (key, title) in enumerate(
        zip(V2_ITEM_KEYS, V2_ITEM_TITLES, strict=True), start=1
    ):
        week_index = week_for(key)
        old_items.append(
            StudyPlanItem(
                plan_id=plan.id,
                phase_id=phases[phase_for_week[week_index]].id,
                week_id=weeks[week_index].id,
                guide_order=order,
                type="learn",
                priority="core",
                full_title=(
                    title
                    if valid_legacy_shape or order != 1
                    else f"{title} (unexpected edit)"
                ),
                estimate_minutes=30,
            )
        )

    await sp.insert_plan_rows(
        db,
        plan,
        {
            "phases": list(phases.values()),
            "weeks": list(weeks.values()),
            "items": old_items,
            "dependencies": [],
        },
    )
    creation = StudyPlanRevision(
        plan_id=plan.id,
        kind=REVISION_CREATED,
        base_plan_revision=1,
        before={},
        after={
            "seed_key": "devmax.senior-backend-12-week.v2",
            "seed_version": 2,
            "weeks": 12,
            "items": 72,
        },
    )
    db.add(creation)
    await db.flush()

    imported = StudyPlanItemDependency(
        plan_id=plan.id,
        prerequisite_item_id=old_items[0].id,
        dependent_item_id=old_items[1].id,
        source=DEP_IMPORTED,
        confidence=CONFIDENCE_HIGH,
    )
    inferred = StudyPlanItemDependency(
        plan_id=plan.id,
        prerequisite_item_id=old_items[1].id,
        dependent_item_id=old_items[2].id,
        source=DEP_INFERRED,
    )
    user_added = StudyPlanItemDependency(
        plan_id=plan.id,
        prerequisite_item_id=old_items[2].id,
        dependent_item_id=old_items[3].id,
        source=DEP_USER_ADDED,
    )
    removed_user_added = StudyPlanItemDependency(
        plan_id=plan.id,
        prerequisite_item_id=old_items[4].id,
        dependent_item_id=old_items[0].id,
        source=DEP_USER_ADDED,
    )
    db.add_all([imported, inferred, user_added, removed_user_added])
    card = make_card(topic="Existing recall signal")
    db.add(card)
    await db.flush()
    session = Session(
        card_id=card.id,
        question_asked="Existing question?",
        answer_text="Existing answer",
        score=2,
        feedback="Existing feedback",
        status="complete",
        ended_at=datetime(2026, 8, 1, 13, tzinfo=UTC),
    )
    db.add(session)
    plan_id = plan.id
    card_id = card.id
    session_id = session.id
    old_ids = {key: item.id for key, item in zip(V2_ITEM_KEYS, old_items, strict=True)}
    await db.commit()
    db.expire_all()
    legacy_rows = (
        await db.exec(
            select(StudyPlanItem)
            .where(StudyPlanItem.plan_id == plan_id)
            .order_by(StudyPlanItem.guide_order)
        )
    ).all()
    card_before = (
        await db.exec(select(Card).where(Card.id == card_id))
    ).one().model_dump()
    session_before = (
        await db.exec(select(Session).where(Session.id == session_id))
    ).one().model_dump()
    legacy_before = [item.model_dump() for item in legacy_rows]

    if not valid_legacy_shape:
        with pytest.raises(PlanSeedError, match="reviewed 72-item v2 or 84-item v3"):
            await load_first_party_plan(
                path, start_date=UPGRADE_START, activate=True, db=db
            )
        unchanged = (
            await db.exec(
                select(StudyPlanItem)
                .where(StudyPlanItem.plan_id == plan_id)
                .order_by(StudyPlanItem.guide_order)
            )
        ).all()
        assert [item.model_dump() for item in unchanged] == legacy_before
        assert len((await db.exec(select(StudyPlanRevision))).all()) == 1
        assert (
            await db.exec(select(Card).where(Card.id == card_id))
        ).one().model_dump() == card_before
        assert (
            await db.exec(select(Session).where(Session.id == session_id))
        ).one().model_dump() == session_before
        return

    result = await load_first_party_plan(
        path, start_date=UPGRADE_START, activate=True, db=db
    )

    assert result.updated and not result.created
    assert result.plan_id == str(plan.id)
    assert result.version == manifest["version"]
    assert plan.start_date == UPGRADE_START
    stored = (await db.exec(select(StudyPlanItem).where(StudyPlanItem.plan_id == plan.id))).all()
    by_key = {item.curriculum_key: item for item in stored}
    assert len(stored) == len(V2_ITEM_KEYS) + len(target_items)
    assert all(by_key[key].status == ITEM_REMOVED for key in V2_ITEM_KEYS)
    assert {by_key[key].id for key in V2_ITEM_KEYS} == set(old_ids.values())
    assert by_key[V2_ITEM_KEYS[0]].full_title == V2_ITEM_TITLES[0]
    assert all(by_key[item["key"]].status == ITEM_PENDING for item in target_items)
    assert not ({by_key[item["key"]].id for item in target_items} & set(old_ids.values()))
    assert by_key["V4-W1-L1"].resources == target_items[0]["resources"]

    dependencies = (
        await db.exec(
            select(StudyPlanItemDependency).where(StudyPlanItemDependency.plan_id == plan.id)
        )
    ).all()
    assert inferred.id in {dependency.id for dependency in dependencies}
    assert user_added.id in {dependency.id for dependency in dependencies}
    assert imported.id not in {dependency.id for dependency in dependencies}
    assert removed_user_added.id in {dependency.id for dependency in dependencies}
    assert (await db.exec(select(Card).where(Card.id == card.id))).one().model_dump() == card_before
    assert (
        await db.exec(select(Session).where(Session.id == session.id))
    ).one().model_dump() == session_before

    again = await load_first_party_plan(
        path, start_date=UPGRADE_START, activate=True, db=db
    )
    assert not again.created and not again.updated
    assert again.version == manifest["version"]
    assert again.items == len(preview["items"])

    manifest["version"] = 3
    path.write_text(json.dumps(manifest))
    with pytest.raises(PlanSeedError, match="refusing to downgrade"):
        await load_first_party_plan(path, start_date=START, activate=True, db=db)


@pytest.mark.parametrize(
    "dirty_state",
    [
        "complete",
        "deferred",
        "removed",
        "notes",
        "study_block_label",
        "study_block_weekday",
        "study_block_time",
        "reminder",
        "week_override",
        "week_advanced",
        "current_week",
        "plan_revision",
        "plan_completed",
        "plan_archived",
    ],
)
async def test_v3_to_v4_fails_closed_when_the_old_plan_is_not_pristine(
    db, tmp_path, dirty_state
) -> None:
    manifest = json.loads(DEFAULT_MANIFEST.read_text())
    manifest.pop("source_guide_path", None)
    manifest.pop("source_guide_sha256", None)
    manifest["guide_sha256"] = hashlib.sha256(manifest["guide_text"].encode()).hexdigest()
    manifest["version"] = 3
    path = tmp_path / "curriculum.json"
    path.write_text(json.dumps(manifest))

    seeded = await load_first_party_plan(path, start_date=START, db=db)
    plan_id = uuid.UUID(seeded.plan_id)
    plan = (await db.exec(select(StudyPlan).where(StudyPlan.id == plan_id))).one()
    week = (
        await db.exec(
            select(StudyPlanWeek)
            .where(StudyPlanWeek.plan_id == plan_id)
            .order_by(StudyPlanWeek.index)
        )
    ).first()
    item = (
        await db.exec(
            select(StudyPlanItem)
            .where(StudyPlanItem.plan_id == plan_id)
            .order_by(StudyPlanItem.guide_order)
        )
    ).first()
    if dirty_state == "complete":
        item.status = ITEM_COMPLETE
        item.completed_at = datetime(2026, 8, 10, 12, tzinfo=UTC)
    elif dirty_state == "deferred":
        item.status = "deferred"
    elif dirty_state == "removed":
        item.status = ITEM_REMOVED
    elif dirty_state == "notes":
        item.notes = "This note belongs to the old schedule."
    elif dirty_state == "study_block_label":
        item.study_block_label = "Saturday deep work"
    elif dirty_state == "study_block_weekday":
        item.study_block_weekday = 6
    elif dirty_state == "study_block_time":
        item.study_block_minute_of_day = 600
    elif dirty_state == "reminder":
        item.study_block_reminder_on = True
    elif dirty_state == "week_override":
        week.override_capacity_minutes = 900
    elif dirty_state == "week_advanced":
        week.advanced_at = datetime(2026, 8, 10, 12, tzinfo=UTC)
    elif dirty_state == "current_week":
        plan.current_week_index = 2
    elif dirty_state == "plan_revision":
        plan.revision = 2
    elif dirty_state == "plan_completed":
        plan.status = "completed"
        plan.completed_at = datetime(2026, 8, 10, 12, tzinfo=UTC)
    elif dirty_state == "plan_archived":
        plan.status = "archived"
        plan.archived_at = datetime(2026, 8, 10, 12, tzinfo=UTC)
    db.add_all([plan, week, item])
    await db.commit()
    db.expire_all()

    plan_before = (await db.exec(select(StudyPlan).where(StudyPlan.id == plan_id))).one()
    weeks_before = (
        await db.exec(
            select(StudyPlanWeek)
            .where(StudyPlanWeek.plan_id == plan_id)
            .order_by(StudyPlanWeek.index)
        )
    ).all()
    items_before = (
        await db.exec(
            select(StudyPlanItem)
            .where(StudyPlanItem.plan_id == plan_id)
            .order_by(StudyPlanItem.guide_order)
        )
    ).all()
    revisions_before = (
        await db.exec(
            select(StudyPlanRevision).where(StudyPlanRevision.plan_id == plan_id)
        )
    ).all()
    snapshot = (
        plan_before.model_dump(),
        [row.model_dump() for row in weeks_before],
        [row.model_dump() for row in items_before],
        [row.model_dump() for row in revisions_before],
    )

    manifest["version"] = 4
    path.write_text(json.dumps(manifest))
    with pytest.raises(PlanSeedError, match="not pristine"):
        await load_first_party_plan(path, start_date=START, db=db)

    plan_after = (await db.exec(select(StudyPlan).where(StudyPlan.id == plan_id))).one()
    weeks_after = (
        await db.exec(
            select(StudyPlanWeek)
            .where(StudyPlanWeek.plan_id == plan_id)
            .order_by(StudyPlanWeek.index)
        )
    ).all()
    items_after = (
        await db.exec(
            select(StudyPlanItem)
            .where(StudyPlanItem.plan_id == plan_id)
            .order_by(StudyPlanItem.guide_order)
        )
    ).all()
    revisions_after = (
        await db.exec(
            select(StudyPlanRevision).where(StudyPlanRevision.plan_id == plan_id)
        )
    ).all()
    assert (
        plan_after.model_dump(),
        [row.model_dump() for row in weeks_after],
        [row.model_dump() for row in items_after],
        [row.model_dump() for row in revisions_after],
    ) == snapshot


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

    assert [plan.title for plan in (await db.exec(select(StudyPlan))).all()] == ["Existing"]


async def test_an_explicit_start_date_must_be_a_monday(db) -> None:
    with pytest.raises(PlanSeedError, match="must be Mondays"):
        await load_first_party_plan(start_date=date(2026, 7, 31), activate=True, db=db)

    assert (await db.exec(select(StudyPlan))).all() == []


async def test_default_start_date_uses_founder_settings_without_request_context(db) -> None:
    seeded = await load_first_party_plan(db=db)

    plan = (
        await db.exec(select(StudyPlan).where(StudyPlan.user_id == FOUNDER_USER_ID))
    ).one()
    assert seeded.created
    assert plan.start_date.weekday() == 0


async def test_a_public_users_matching_seed_does_not_satisfy_the_founder_seed(db) -> None:
    other = User()
    db.add(other)
    await db.flush()
    other_plan = StudyPlan(
        user_id=other.id,
        title="Other user's curriculum",
        subject="Backend",
        subject_slug="backend",
        guide_text="other",
        status=PLAN_PAUSED,
        mode="flexible",
        start_date=START,
        default_weekly_capacity_minutes=720,
        forecast_end_plan_week=12,
    )
    db.add(other_plan)
    await db.flush()
    db.add(
        StudyPlanRevision(
            plan_id=other_plan.id,
            kind=REVISION_CREATED,
            base_plan_revision=1,
            before={},
            after={"seed_key": "devmax.senior-backend-12-week.v3"},
            summary="Other user's seed",
        )
    )
    await db.commit()

    seeded = await load_first_party_plan(start_date=START, db=db)

    assert seeded.created
    assert seeded.plan_id != str(other_plan.id)
    assert len((await db.exec(select(StudyPlan))).all()) == 2


async def test_a_public_users_active_plan_does_not_block_founder_activation(db) -> None:
    other = User()
    db.add(other)
    await db.flush()
    db.add(
        StudyPlan(
            user_id=other.id,
            title="Other active plan",
            subject="Law",
            subject_slug="law",
            guide_text="other",
            status=PLAN_ACTIVE,
            mode="flexible",
            start_date=START,
            default_weekly_capacity_minutes=720,
            forecast_end_plan_week=4,
        )
    )
    await db.commit()

    seeded = await load_first_party_plan(start_date=START, activate=True, db=db)

    assert seeded.active
    active = (await db.exec(select(StudyPlan).where(StudyPlan.status == PLAN_ACTIVE))).all()
    assert {plan.user_id for plan in active} == {FOUNDER_USER_ID, other.id}


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
