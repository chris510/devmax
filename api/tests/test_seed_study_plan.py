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
from sqlalchemy import update
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
    _claim_item_for_upgrade,
    load_first_party_plan,
    validate_bundle,
)
from app.services import study_plan as sp
from tests.conftest import make_card

START = date(2026, 7, 27)
UPGRADE_START = date(2026, 8, 10)
API = DEFAULT_MANIFEST.parent.parent
BASE_CARDS = API / "cards.json"
AI_FOUNDATIONS = API / "modules" / "ai-foundations.json"
AI_FOUNDATION_EVALS = API / "evals" / "ai-foundations-v1.json"
AI_RETRIEVAL_LAB = API / "evals" / "ai-retrieval-lab-v1.json"
V4_PLAN_SHAPE_SHA256 = "58a14697f8d5941eb4c9b2cf54e054c13fdd78726004a7b07ae7287fde4bf7c7"
AI_ITEM_KEYS = {
    "V4-W1-L3",
    "V4-W2-L1",
    "V4-W4-L3",
    "V4-W5-L2",
    "V4-W6-L3",
    "V4-W7-L5",
    "V4-W8-L2",
    "V4-W9-L5",
}


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
    assert manifest["version"] == 5
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


def test_v5_reallocates_exactly_eleven_hours_without_changing_the_plan_shape() -> None:
    manifest, result = validate_bundle(DEFAULT_MANIFEST, start_date=START)
    raw_items = [item for week in manifest["weeks"] for item in week["items"]]
    ai_items = [item for item in raw_items if item.get("requires_fresh_completion")]
    base_topics = {entry["topic"] for entry in json.loads(BASE_CARDS.read_text())}
    stable_items = []
    base_mappings = []
    guide_order = 0
    for week in manifest["weeks"]:
        for item in week["items"]:
            guide_order += 1
            stable_items.append(
                (
                    item["key"],
                    week["index"],
                    guide_order,
                    item["type"],
                    item["priority"],
                    item["minutes"],
                )
            )
            base_mappings.extend(
                (topic, item["key"])
                for topic in item.get("mapped_recall_topics", [])
                if topic in base_topics
            )
    frozen_shape = {
        "items": stable_items,
        "dependencies": manifest["dependencies"],
        "base_mappings": sorted(base_mappings),
        "weeks_10_12": [week for week in manifest["weeks"] if week["index"] >= 10],
    }
    shape_sha256 = hashlib.sha256(
        json.dumps(frozen_shape, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    assert {item["key"] for item in ai_items} == AI_ITEM_KEYS
    assert sum(item["minutes"] for item in ai_items) == 660
    assert len({item["key"] for item in raw_items}) == 116
    assert len(result.preview["dependencies"]) == 31
    assert shape_sha256 == V4_PLAN_SHAPE_SHA256
    assert all(
        not item.get("requires_fresh_completion")
        for week in manifest["weeks"]
        if week["index"] >= 10
        for item in week["items"]
    )


def test_base_and_ai_foundation_topics_each_map_to_exactly_one_learn_item() -> None:
    manifest = json.loads(DEFAULT_MANIFEST.read_text())
    base_topics = {entry["topic"] for entry in json.loads(BASE_CARDS.read_text())}
    foundations = json.loads(AI_FOUNDATIONS.read_text())
    foundation_topics = {entry["topic"] for entry in foundations}
    raw_items = [item for week in manifest["weeks"] for item in week["items"]]
    mappings = [
        (item["key"], topic)
        for item in raw_items
        for topic in item.get("mapped_recall_topics", [])
    ]

    assert len(base_topics) == 54
    assert len(foundation_topics) == 4
    assert base_topics.isdisjoint(foundation_topics)
    assert {topic for _, topic in mappings} == base_topics | foundation_topics
    assert len(mappings) == 58
    assert all(sum(mapped == topic for _, mapped in mappings) == 1 for topic in base_topics)
    assert all(
        sum(mapped == entry["topic"] for _, mapped in mappings) == 1
        and next(key for key, mapped in mappings if mapped == entry["topic"])
        == entry["activation_item_key"]
        for entry in foundations
    )
    week_by_key = {
        item["key"]: week["index"]
        for week in manifest["weeks"]
        for item in week["items"]
    }
    assert all(
        week_by_key[entry["activation_item_key"]] == entry["target_week"]
        for entry in foundations
    )


def test_ai_foundation_eval_pack_is_balanced_and_qualitative() -> None:
    foundations = json.loads(AI_FOUNDATIONS.read_text())
    cases = json.loads(AI_FOUNDATION_EVALS.read_text())
    topics = {entry["topic"] for entry in foundations}

    assert len(cases) == 12
    assert {case["topic"] for case in cases} == topics
    assert all(case["review_status"] == "draft_review" for case in cases)
    assert all("expected_score" not in case for case in cases)
    for topic in topics:
        topic_cases = [case for case in cases if case["topic"] == topic]
        assert {case["expected_judgment"] for case in topic_cases} == {
            "strong",
            "partial",
            "incorrect",
        }


def test_ai_retrieval_lab_has_versioned_qrels_and_access_denials() -> None:
    lab = json.loads(AI_RETRIEVAL_LAB.read_text())
    documents = lab["documents"]
    queries = lab["queries"]
    chunk_ids = {entry["chunk_id"] for entry in documents}
    query_ids = {entry["query_id"] for entry in queries}

    assert lab["schema_version"] == 1
    assert len(documents) == len(chunk_ids) == 10
    assert len({entry["document_id"] for entry in documents}) == 9
    assert len(queries) == len(query_ids) == 10
    assert all(
        all(
            field in entry
            for field in (
                "document_id",
                "version",
                "chunk_id",
                "text",
                "provenance",
                "tenant_id",
                "acl",
                "is_current",
            )
        )
        for entry in documents
    )
    assert {entry["category"] for entry in queries} == {
        "lexical",
        "semantic",
        "hybrid",
        "freshness",
        "version",
        "unauthorized_evidence",
    }
    for query in queries:
        assert query["principal"]["principal_id"]
        assert query["principal"]["tenant_id"]
        assert set(query["qrels"]) <= chunk_ids
        assert set(query["forbidden_chunk_ids"]) <= chunk_ids
        assert all(isinstance(value, int) and value > 0 for value in query["qrels"].values())
        assert query["qrels"] or query["forbidden_chunk_ids"]

    denials = [query for query in queries if query["category"] == "unauthorized_evidence"]
    assert len(denials) == 2
    assert all(not query["qrels"] and query["forbidden_chunk_ids"] for query in denials)
    by_id = {query["query_id"]: query for query in queries}
    assert by_id["q04-freshness-current"]["qrels"] == {
        "atlas-cache-failover:v2:c1": 1
    }
    assert by_id["q04-freshness-current"]["forbidden_chunk_ids"] == [
        "atlas-cache-failover:v1:c1"
    ]
    assert by_id["q05-version-archived"]["version_scope"] == "all"
    assert by_id["q05-version-archived"]["qrels"] == {
        "atlas-cache-failover:v1:c1": 1
    }


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
    assert revision.after["seed_version"] == 5
    assert set(revision.after["fresh_completion_keys"]) == AI_ITEM_KEYS


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


async def test_fresh_completion_marker_must_be_boolean(db, tmp_path) -> None:
    manifest = json.loads(DEFAULT_MANIFEST.read_text())
    manifest.pop("source_guide_path", None)
    manifest.pop("source_guide_sha256", None)
    manifest["weeks"][0]["items"][2]["requires_fresh_completion"] = "yes"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))

    with pytest.raises(PlanSeedError, match="requires_fresh_completion must be a boolean"):
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
    manifest["version"] = 4
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


async def test_v4_to_v6_keeps_skipped_fresh_work_as_durable_debt(
    db, tmp_path
) -> None:
    v5_manifest = json.loads(DEFAULT_MANIFEST.read_text())
    v5_manifest.pop("source_guide_path", None)
    v5_manifest.pop("source_guide_sha256", None)
    v5_path = tmp_path / "v5.json"
    v5_path.write_text(json.dumps(v5_manifest))
    foundation_topics = {
        entry["topic"] for entry in json.loads(AI_FOUNDATIONS.read_text())
    }
    v4_manifest = json.loads(DEFAULT_MANIFEST.read_text())
    v4_manifest["version"] = 4
    v4_manifest["title"] = "Senior backend interview preparation"
    v4_manifest["subject"] = "Senior backend interview preparation"
    v4_manifest["guide_text"] = "reviewed version-4 baseline"
    v4_manifest["guide_sha256"] = hashlib.sha256(
        v4_manifest["guide_text"].encode()
    ).hexdigest()
    v4_manifest.pop("source_guide_path", None)
    v4_manifest.pop("source_guide_sha256", None)
    for week in v4_manifest["weeks"]:
        for item in week["items"]:
            if item["key"] not in AI_ITEM_KEYS:
                continue
            item.pop("requires_fresh_completion", None)
            item["title"] = f"Version 4 baseline · {item['key']}"
            item["why"] = "Historical version-4 rationale."
            item["done_when"] = "Complete the historical version-4 activity."
            item["mapped_recall_topics"] = [
                topic
                for topic in item.get("mapped_recall_topics", [])
                if topic not in foundation_topics
            ]
    v4_path = tmp_path / "v4.json"
    v4_path.write_text(json.dumps(v4_manifest))

    seeded = await load_first_party_plan(
        v4_path, start_date=START, activate=True, db=db
    )
    plan_id = uuid.UUID(seeded.plan_id)
    rows = (
        await db.exec(
            select(StudyPlanItem).where(StudyPlanItem.plan_id == plan_id)
        )
    ).all()
    by_key = {item.curriculum_key: item for item in rows}
    completed = by_key["V4-W1-L3"]
    completed.status = ITEM_COMPLETE
    completed.completed_at = datetime(2026, 8, 12, 20, tzinfo=UTC)
    completed.notes = "Historical completion evidence."
    completed.study_block_label = "Tuesday AI review"
    completed.study_block_weekday = 2
    completed.study_block_minute_of_day = 600
    completed.study_block_reminder_on = True
    pending = by_key["V4-W2-L1"]
    pending.notes = "Keep this note through the content update."
    pending.study_block_label = "Saturday"
    pending.study_block_weekday = 6
    pending.study_block_minute_of_day = 540
    pending.study_block_reminder_on = True
    legacy_removed = StudyPlanItem(
        plan_id=plan_id,
        phase_id=completed.phase_id,
        week_id=completed.week_id,
        guide_order=1001,
        curriculum_key="W1-L1",
        type="learn",
        priority="core",
        full_title="Removed legacy activity",
        estimate_minutes=30,
        status=ITEM_REMOVED,
        notes="Preserve the removed production lineage row.",
    )
    week = (
        await db.exec(
            select(StudyPlanWeek).where(
                StudyPlanWeek.plan_id == plan_id,
                StudyPlanWeek.index == 2,
            )
        )
    ).one()
    week.override_capacity_minutes = 900
    db.add_all([completed, pending, legacy_removed, week])
    card = make_card(
        topic="Existing production recall signal",
        ease_factor=1.91,
        interval_days=13,
        repetitions=4,
        next_review_at=date(2026, 9, 5),
        last_score=2,
        mastery_summary="existing history",
    )
    db.add(card)
    await db.flush()
    session = Session(
        card_id=card.id,
        question_asked="Existing question?",
        answer_text="Existing answer",
        score=2,
        feedback="Existing feedback",
        status="complete",
        ended_at=datetime(2026, 8, 11, 12, tzinfo=UTC),
    )
    db.add(session)
    card_id = card.id
    session_id = session.id
    await db.commit()
    db.expire_all()

    before_rows = (
        await db.exec(
            select(StudyPlanItem).where(StudyPlanItem.plan_id == plan_id)
        )
    ).all()
    before_by_key = {item.curriculum_key: item for item in before_rows}
    ids_before = {key: item.id for key, item in before_by_key.items()}
    completed_before = before_by_key["V4-W1-L3"].model_dump()
    removed_before = before_by_key["W1-L1"].model_dump()
    key_by_id_before = {item.id: key for key, item in before_by_key.items()}
    dependencies_before = (
        await db.exec(
            select(StudyPlanItemDependency).where(
                StudyPlanItemDependency.plan_id == plan_id
            )
        )
    ).all()
    dependency_semantics_before = {
        (
            key_by_id_before[row.prerequisite_item_id],
            key_by_id_before[row.dependent_item_id],
            row.kind,
            row.source,
            row.confidence,
            row.rationale,
            row.source_excerpt,
        )
        for row in dependencies_before
    }
    card_before = (await db.exec(select(Card).where(Card.id == card_id))).one().model_dump()
    session_before = (
        await db.exec(select(Session).where(Session.id == session_id))
    ).one().model_dump()

    upgraded = await load_first_party_plan(
        v5_path, start_date=UPGRADE_START, activate=True, db=db
    )

    assert upgraded.updated and not upgraded.created
    assert upgraded.version == 5
    assert upgraded.skipped_completed_keys == ("V4-W1-L3",)
    stored_plan = (
        await db.exec(select(StudyPlan).where(StudyPlan.id == plan_id))
    ).one()
    assert stored_plan.start_date == START
    after_rows = (
        await db.exec(
            select(StudyPlanItem).where(StudyPlanItem.plan_id == plan_id)
        )
    ).all()
    after_by_key = {item.curriculum_key: item for item in after_rows}
    assert len({key for key in after_by_key if key and key.startswith("V4-")}) == 116
    assert {key: item.id for key, item in after_by_key.items()} == ids_before
    assert after_by_key["V4-W1-L3"].model_dump() == completed_before
    assert after_by_key["W1-L1"].model_dump() == removed_before
    assert after_by_key["V4-W2-L1"].full_title == next(
        item["title"]
        for week_entry in v5_manifest["weeks"]
        for item in week_entry["items"]
        if item["key"] == "V4-W2-L1"
    )
    assert after_by_key["V4-W2-L1"].notes == "Keep this note through the content update."
    assert after_by_key["V4-W2-L1"].study_block_label == "Saturday"
    assert after_by_key["V4-W2-L1"].study_block_reminder_on
    key_by_id_after = {item.id: key for key, item in after_by_key.items()}
    dependencies_after = (
        await db.exec(
            select(StudyPlanItemDependency).where(
                StudyPlanItemDependency.plan_id == plan_id
            )
        )
    ).all()
    dependency_semantics_after = {
        (
            key_by_id_after[row.prerequisite_item_id],
            key_by_id_after[row.dependent_item_id],
            row.kind,
            row.source,
            row.confidence,
            row.rationale,
            row.source_excerpt,
        )
        for row in dependencies_after
    }
    assert len(dependency_semantics_after) == 31
    assert dependency_semantics_after == dependency_semantics_before
    stored_week = (
        await db.exec(
            select(StudyPlanWeek).where(
                StudyPlanWeek.plan_id == plan_id,
                StudyPlanWeek.index == 2,
            )
        )
    ).one()
    assert stored_week.override_capacity_minutes == 900
    assert (await db.exec(select(Card).where(Card.id == card_id))).one().model_dump() == card_before
    assert (
        await db.exec(select(Session).where(Session.id == session_id))
    ).one().model_dump() == session_before
    revision = (
        await db.exec(
            select(StudyPlanRevision)
            .where(StudyPlanRevision.plan_id == plan_id)
            .order_by(StudyPlanRevision.created_at.desc())
        )
    ).first()
    assert revision.after["from_version"] == 4
    assert revision.after["seed_version"] == 5
    assert set(revision.after["fresh_completion_keys"]) == AI_ITEM_KEYS
    assert revision.after["skipped_completed_keys"] == ["V4-W1-L3"]

    again = await load_first_party_plan(
        v5_path, start_date=UPGRADE_START, activate=True, db=db
    )
    assert not again.created and not again.updated
    assert again.version == 5
    assert again.skipped_completed_keys == ("V4-W1-L3",)

    # v6 no longer carries v5's release-local marker. The skipped key remains
    # protected by the revision ledger until its row is reopened and an upgrade
    # actually applies the fresh content.
    v6_manifest = json.loads(DEFAULT_MANIFEST.read_text())
    v6_manifest["version"] = 6
    v6_manifest["guide_text"] = "reviewed version-6 curriculum"
    v6_manifest["guide_sha256"] = hashlib.sha256(
        v6_manifest["guide_text"].encode()
    ).hexdigest()
    v6_manifest.pop("source_guide_path", None)
    v6_manifest.pop("source_guide_sha256", None)
    v6_completed_entry = next(
        item
        for week_entry in v6_manifest["weeks"]
        for item in week_entry["items"]
        if item["key"] == "V4-W1-L3"
    )
    for week_entry in v6_manifest["weeks"]:
        for item in week_entry["items"]:
            item.pop("requires_fresh_completion", None)
    v6_completed_entry["title"] = "Version 6 content must not inherit old credit"
    v6_completed_entry["done_when"] = "Complete the version-6 work."
    v6_completed_entry["mapped_recall_topics"] = ["Version 6 unmapped recall"]
    v6_path = tmp_path / "v6.json"
    v6_path.write_text(json.dumps(v6_manifest))

    upgraded_v6 = await load_first_party_plan(
        v6_path, start_date=UPGRADE_START, activate=True, db=db
    )

    assert upgraded_v6.updated and upgraded_v6.version == 6
    assert upgraded_v6.skipped_completed_keys == ("V4-W1-L3",)
    completed_after_v6 = (
        await db.exec(
            select(StudyPlanItem).where(StudyPlanItem.id == completed_before["id"])
        )
    ).one()
    assert completed_after_v6.model_dump() == completed_before
    v6_revision = (
        await db.exec(
            select(StudyPlanRevision)
            .where(StudyPlanRevision.plan_id == plan_id)
            .order_by(StudyPlanRevision.created_at.desc())
        )
    ).first()
    assert v6_revision.after["from_version"] == 5
    assert v6_revision.after["fresh_completion_keys"] == []
    assert v6_revision.after["skipped_completed_keys"] == ["V4-W1-L3"]
    assert v6_revision.after["resolved_fresh_completion_keys"] == []

    again_v6 = await load_first_party_plan(
        v6_path, start_date=UPGRADE_START, activate=True, db=db
    )
    assert not again_v6.created and not again_v6.updated
    assert again_v6.version == 6
    assert again_v6.skipped_completed_keys == ("V4-W1-L3",)

    # Reopening makes it possible to attach the fresh content honestly. The
    # next upgrade records the debt resolution; after a new completion, a
    # later navigation-only release may update resources and mappings normally.
    reopened_at = datetime(2026, 8, 14, 9, tzinfo=UTC)
    completed_after_v6.status = ITEM_PENDING
    completed_after_v6.completed_at = None
    completed_after_v6.reopened_at = reopened_at
    completed_after_v6.updated_at = reopened_at
    db.add(completed_after_v6)
    await db.commit()

    v7_manifest = json.loads(json.dumps(v6_manifest))
    v7_manifest["version"] = 7
    v7_manifest["guide_text"] = "reviewed version-7 curriculum"
    v7_manifest["guide_sha256"] = hashlib.sha256(
        v7_manifest["guide_text"].encode()
    ).hexdigest()
    v7_entry = next(
        item
        for week_entry in v7_manifest["weeks"]
        for item in week_entry["items"]
        if item["key"] == "V4-W1-L3"
    )
    v7_entry["title"] = "Version 7 fresh work applied while unfinished"
    v7_entry["mapped_recall_topics"] = ["Version 7 applied recall"]
    v7_path = tmp_path / "v7.json"
    v7_path.write_text(json.dumps(v7_manifest))

    upgraded_v7 = await load_first_party_plan(
        v7_path, start_date=UPGRADE_START, activate=True, db=db
    )
    assert upgraded_v7.updated and upgraded_v7.skipped_completed_keys == ()
    resolved_row = (
        await db.exec(select(StudyPlanItem).where(StudyPlanItem.id == completed_before["id"]))
    ).one()
    assert resolved_row.status == ITEM_PENDING
    assert resolved_row.full_title == "Version 7 fresh work applied while unfinished"
    assert resolved_row.mapped_recall_topics == ["Version 7 applied recall"]
    v7_revision = (
        await db.exec(
            select(StudyPlanRevision)
            .where(StudyPlanRevision.plan_id == plan_id)
            .order_by(StudyPlanRevision.created_at.desc())
        )
    ).first()
    assert v7_revision.after["skipped_completed_keys"] == []
    assert v7_revision.after["resolved_fresh_completion_keys"] == ["V4-W1-L3"]

    second_completion = datetime(2026, 8, 14, 11, tzinfo=UTC)
    resolved_row.status = ITEM_COMPLETE
    resolved_row.completed_at = second_completion
    resolved_row.updated_at = second_completion
    db.add(resolved_row)
    await db.commit()

    v8_manifest = json.loads(json.dumps(v7_manifest))
    v8_manifest["version"] = 8
    v8_manifest["guide_text"] = "reviewed version-8 navigation update"
    v8_manifest["guide_sha256"] = hashlib.sha256(
        v8_manifest["guide_text"].encode()
    ).hexdigest()
    v8_entry = next(
        item
        for week_entry in v8_manifest["weeks"]
        for item in week_entry["items"]
        if item["key"] == "V4-W1-L3"
    )
    v8_entry["title"] = "Version 8 title must not rewrite completed history"
    v8_entry["mapped_recall_topics"] = ["Version 8 reviewed navigation"]
    v8_entry["resources"] = [
        {
            "kind": "reference",
            "provider": "Test",
            "label": "Version 8 navigation",
            "action_label": "Open reference",
            "url": "https://example.com/version-8",
            "language": "",
        }
    ]
    v8_path = tmp_path / "v8.json"
    v8_path.write_text(json.dumps(v8_manifest))

    upgraded_v8 = await load_first_party_plan(
        v8_path, start_date=UPGRADE_START, activate=True, db=db
    )
    assert upgraded_v8.updated and upgraded_v8.skipped_completed_keys == ()
    completed_after_resolution = (
        await db.exec(select(StudyPlanItem).where(StudyPlanItem.id == completed_before["id"]))
    ).one()
    assert completed_after_resolution.status == ITEM_COMPLETE
    assert completed_after_resolution.full_title == (
        "Version 7 fresh work applied while unfinished"
    )
    assert completed_after_resolution.mapped_recall_topics == [
        "Version 8 reviewed navigation"
    ]
    assert completed_after_resolution.resources == v8_entry["resources"]
    v8_revision = (
        await db.exec(
            select(StudyPlanRevision)
            .where(StudyPlanRevision.plan_id == plan_id)
            .order_by(StudyPlanRevision.created_at.desc())
        )
    ).first()
    assert v8_revision.after["skipped_completed_keys"] == []
    assert v8_revision.after["resolved_fresh_completion_keys"] == []

    with pytest.raises(PlanSeedError, match="refusing to downgrade"):
        await load_first_party_plan(v4_path, start_date=START, activate=True, db=db)


async def test_upgrade_claim_loses_cleanly_to_a_concurrent_completion(
    db, tmp_path
) -> None:
    manifest = json.loads(DEFAULT_MANIFEST.read_text())
    manifest.pop("source_guide_path", None)
    manifest.pop("source_guide_sha256", None)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    seeded = await load_first_party_plan(
        path, start_date=START, activate=True, db=db
    )
    plan_id = uuid.UUID(seeded.plan_id)
    item = (
        await db.exec(
            select(StudyPlanItem)
            .where(StudyPlanItem.plan_id == plan_id)
            .order_by(StudyPlanItem.guide_order)
        )
    ).first()
    original_title = item.full_title
    completed_at = datetime(2026, 8, 13, 18, tzinfo=UTC)

    # Simulate completion winning after the upgrader read the row but before it
    # claimed that exact status/timestamp snapshot.
    await db.exec(
        update(StudyPlanItem)
        .where(StudyPlanItem.id == item.id)
        .values(
            status=ITEM_COMPLETE,
            completed_at=completed_at,
            updated_at=completed_at,
        )
        .execution_options(synchronize_session=False)
    )

    claimed = await _claim_item_for_upgrade(
        db, item, claimed_at=datetime(2026, 8, 13, 18, 1, tzinfo=UTC)
    )

    assert not claimed
    assert item.status == ITEM_COMPLETE
    assert item.completed_at.replace(tzinfo=UTC) == completed_at
    assert item.full_title == original_title


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
