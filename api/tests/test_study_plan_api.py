"""Study Plan over HTTP, against the real ASGI app.

The importer is stubbed at `llm.import_guide` and `llm.propose_cards`, so every
validation, transaction, and lifecycle rule below runs for real — only the
network call is fake. No test here reaches Anthropic.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.models import (
    Card,
    StudyPlan,
    StudyPlanCardProposal,
    StudyPlanCardProposalAcceptance,
    StudyPlanItem,
    StudyPlanPracticeDebrief,
    StudyPlanRevision,
    User,
)
from app.seed_study_plan import load_first_party_plan
from app.services import llm
from tests.conftest import API_HEADERS, GUIDE, _item, gate, import_payload, make_card


async def make_plan(client: AsyncClient, *, activate=True, **preview_overrides) -> dict:
    body = {
        "guide_text": GUIDE,
        "requested_weeks": 4,
        "weekly_capacity_minutes": 720,
        "mode": "flexible",
        **preview_overrides,
    }
    preview = await client.post("/study-plans/preview", json=body, headers=API_HEADERS)
    assert preview.status_code == 201, preview.text
    draft = preview.json()
    assert draft["can_create"], draft["checks"]
    created = await client.post(
        "/study-plans",
        json={"draft_id": draft["draft_id"], "activate": activate},
        headers=API_HEADERS,
    )
    assert created.status_code == 201, created.text
    return created.json()


async def test_item_resources_stretch_and_existing_recall_cards_are_actionable_read_only(
    client, stub_import, db
):
    topics = [
        "Learn",
        "Waiting",
        "Ready",
        "Rotation",
        "Ungrounded",
        "Archived",
        "Other user",
        "Missing",
    ]
    payload = import_payload()
    payload["items"][0] = {
        **payload["items"][0],
        "resources": [
            {
                "kind": "lesson",
                "provider": "Hello Interview",
                "label": "Delivery Framework",
                "action_label": "Open lesson",
                "url": "https://www.hellointerview.com/learn/system-design/in-a-hurry/delivery",
                "language": "",
            }
        ],
        "mapped_recall_topics": topics,
        "stretch_actions": [
            {
                "title": "Run one extra design",
                "done_when": "Deliver it in 35 minutes and write the largest gap.",
                "minutes": 60,
                "resource_url": "https://www.hellointerview.com/practice/overview",
                "resource_label": "Hello Interview practice",
            }
        ],
    }
    stub_import.response = payload
    plan = await make_plan(client)
    week = (await client.get(f"/study-plans/{plan['id']}/weeks/1", headers=API_HEADERS)).json()
    item_id = week["sections"][0]["rows"][0]["id"]

    rubric = {
        "mechanism": "State the essential mechanism.",
        "acceptable_alternative": "An equivalent account is valid.",
        "trade_off": "Name the central trade-off.",
        "failure_mode": "Name the central failure.",
        "misconception": "Reject the common misconception.",
    }

    def grounded(topic, **overrides):
        return make_card(
            topic=topic,
            canonical_question=f"Explain {topic}?",
            source_url="https://example.com/source",
            answer_basis="A trusted answer basis.",
            answer_rubric=rubric,
            **overrides,
        )

    other_user = User()
    db.add(other_user)
    await db.flush()
    cards = [
        grounded("Learn"),
        grounded(
            "Waiting",
            last_learning_exposure_at=datetime.now(UTC),
            recall_not_before_at=datetime.now(UTC) + timedelta(hours=8),
        ),
        grounded("Ready", last_learning_exposure_at=datetime.now(UTC)),
        grounded("Rotation", last_score=4, repetitions=1),
        make_card(topic="Ungrounded"),
        grounded(
            "Archived",
            lifecycle_status="archived",
            archived_at=datetime.now(UTC),
        ),
        grounded("Other user", user_id=other_user.id),
    ]
    db.add_all(cards)
    await db.commit()
    before = [card.model_dump() for card in cards]

    response = await client.get(
        f"/study-plans/{plan['id']}/items/{item_id}", headers=API_HEADERS
    )

    assert response.status_code == 200
    item = response.json()
    assert item["resources"][0]["action_label"] == "Open lesson"
    assert item["stretch_actions"][0]["minutes"] == 60
    assert [entry["availability_label"] for entry in item["mapped_recall_cards"]] == [
        "Learn first",
        "Waiting for delayed recall",
        "Ready for recall",
        "In review rotation",
        "Not ready",
        "Not ready",
        "Not ready",
        "Not ready",
    ]
    assert all(entry["card_id"] is None for entry in item["mapped_recall_cards"][-4:])
    assert [card.model_dump() for card in cards] == before


# --- preview ----------------------------------------------------------------


async def test_a_pasted_guide_becomes_a_reviewable_preview(client, stub_import):
    response = await client.post(
        "/study-plans/preview",
        json={
            "guide_text": GUIDE,
            "requested_weeks": 4,
            "weekly_capacity_minutes": 720,
            "mode": "flexible",
        },
        headers=API_HEADERS,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ready"
    assert body["weeks"] == 4
    assert body["phases"] == 2
    assert body["total_minutes"] == 270
    assert body["can_create"]
    assert stub_import.calls == 1


async def test_a_guide_that_is_too_short_never_reaches_the_model(client, stub_import):
    response = await client.post(
        "/study-plans/preview",
        json={
            "guide_text": "hello",
            "requested_weeks": 4,
            "weekly_capacity_minutes": 720,
            "mode": "flexible",
        },
        headers=API_HEADERS,
    )
    assert response.status_code == 422
    assert stub_import.calls == 0


async def test_a_failed_import_keeps_the_guide_and_every_setting(
    client, stub_import, db: AsyncSession
):
    stub_import.error = llm.LLMError("the model is unreachable")
    response = await client.post(
        "/study-plans/preview",
        json={
            "guide_text": GUIDE,
            "requested_weeks": 4,
            "weekly_capacity_minutes": 720,
            "mode": "fixed",
            "deadline": "2026-12-01",
        },
        headers=API_HEADERS,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    assert "unreachable" in body["error"]
    assert not body["can_create"]

    from app.models import StudyPlanGuideDraft

    draft = (await db.exec(select(StudyPlanGuideDraft))).one()
    assert draft.guide_text == GUIDE
    assert draft.requested_weeks == 4
    assert draft.weekly_capacity_minutes == 720
    assert draft.mode == "fixed"
    assert draft.deadline == date(2026, 12, 1)


async def test_retrying_a_failed_import_reuses_the_stored_guide(client, stub_import):
    stub_import.error = llm.LLMError("transient")
    first = await client.post(
        "/study-plans/preview",
        json={
            "guide_text": GUIDE,
            "requested_weeks": 4,
            "weekly_capacity_minutes": 720,
            "mode": "flexible",
        },
        headers=API_HEADERS,
    )
    draft_id = first.json()["draft_id"]

    stub_import.error = None
    retried = await client.post(f"/study-plans/preview/{draft_id}/retry", headers=API_HEADERS)
    assert retried.status_code == 200
    assert retried.json()["status"] == "ready"
    assert retried.json()["can_create"]


async def test_an_import_that_needs_review_blocks_creation_until_resolved(client, stub_import):
    stub_import.response = import_payload(
        possible_omissions=[{"note": "A behavioral section", "source_excerpt": "..."}]
    )
    preview = await client.post(
        "/study-plans/preview",
        json={
            "guide_text": GUIDE,
            "requested_weeks": 4,
            "weekly_capacity_minutes": 720,
            "mode": "flexible",
        },
        headers=API_HEADERS,
    )
    draft_id = preview.json()["draft_id"]
    assert not preview.json()["can_create"]

    blocked = await client.post("/study-plans", json={"draft_id": draft_id}, headers=API_HEADERS)
    assert blocked.status_code == 409

    resolved = await client.patch(
        f"/study-plans/preview/{draft_id}",
        json={"omissions_acknowledged": True},
        headers=API_HEADERS,
    )
    assert resolved.json()["can_create"]

    created = await client.post("/study-plans", json={"draft_id": draft_id}, headers=API_HEADERS)
    assert created.status_code == 201


async def test_editing_an_estimate_in_the_preview_survives_revalidation(client, stub_import):
    stub_import.response = import_payload()
    stub_import.response["items"][0]["estimate_confidence"] = "needs_review"
    preview = await client.post(
        "/study-plans/preview",
        json={
            "guide_text": GUIDE,
            "requested_weeks": 4,
            "weekly_capacity_minutes": 720,
            "mode": "flexible",
        },
        headers=API_HEADERS,
    )
    draft_id = preview.json()["draft_id"]

    edited = await client.patch(
        f"/study-plans/preview/{draft_id}",
        json={"item_estimates": {"L1": 120}},
        headers=API_HEADERS,
    )
    assert edited.json()["total_minutes"] == 330
    assert edited.json()["can_create"]

    # A second, unrelated edit must not revert the first.
    again = await client.patch(
        f"/study-plans/preview/{draft_id}",
        json={"omissions_acknowledged": True},
        headers=API_HEADERS,
    )
    assert again.json()["total_minutes"] == 330


async def test_an_import_failure_leaves_no_partial_plan(client, stub_import, db):
    stub_import.error = llm.LLMError("boom")
    await client.post(
        "/study-plans/preview",
        json={
            "guide_text": GUIDE,
            "requested_weeks": 4,
            "weekly_capacity_minutes": 720,
            "mode": "flexible",
        },
        headers=API_HEADERS,
    )
    assert (await db.exec(select(StudyPlan))).all() == []
    assert (await db.exec(select(StudyPlanItem))).all() == []


# --- creation and lifecycle -------------------------------------------------


async def test_creating_a_plan_builds_the_whole_structure(client, stub_import, db):
    overview = await make_plan(client)
    assert overview["week_total"] == 4
    assert len(overview["phases"]) == 2
    assert [p["display_title"] for p in overview["phases"]] == [
        "Foundations",
        "Technologies",
    ]
    assert overview["phases"][0]["week_range"] == "Weeks 1–2"
    assert overview["status"] == "active"
    assert overview["forecast_label"].startswith("Est. completion · week of ")

    items = (await db.exec(select(StudyPlanItem))).all()
    assert len(items) == 5
    assert all(i.status == "pending" for i in items)


async def test_at_most_one_plan_is_active_and_zero_is_valid(client, stub_import, db):
    first = await make_plan(client)
    second = await make_plan(client)

    plans = (await db.exec(select(StudyPlan))).all()
    assert sum(1 for p in plans if p.status == "active") == 1
    assert next(p.status for p in plans if str(p.id) == first["id"]) == "paused"
    assert next(p.status for p in plans if str(p.id) == second["id"]) == "active"

    paused = await client.post(f"/study-plans/{second['id']}/pause", headers=API_HEADERS)
    assert paused.status_code == 200
    summary = await client.get("/study-plans/active/summary", headers=API_HEADERS)
    assert summary.json() == {
        "active": False,
        "plan_id": None,
        "title": "",
        "subject": "",
        "week_index": None,
        "week_total": None,
        "phase_title": "",
        "next_block_label": None,
    }


async def test_the_database_refuses_a_second_active_plan(client, stub_import, db):
    """Belt and braces: the partial unique index, not just the service layer."""
    from sqlalchemy.exc import IntegrityError

    await make_plan(client)
    rogue = StudyPlan(
        title="Rogue",
        subject="s",
        subject_slug="s",
        guide_text="g",
        status="active",
        mode="flexible",
        start_date=date(2026, 8, 3),
        default_weekly_capacity_minutes=720,
        forecast_end_plan_week=4,
    )
    db.add(rogue)
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_completed_is_not_archived(client, stub_import):
    plan = await make_plan(client)
    completed = await client.post(f"/study-plans/{plan['id']}/complete", headers=API_HEADERS)
    assert completed.json()["status"] == "completed"

    listed = (await client.get("/study-plans", headers=API_HEADERS)).json()
    assert len(listed["completed"]) == 1
    assert listed["archived"] == []

    await client.post(f"/study-plans/{plan['id']}/archive", headers=API_HEADERS)
    listed = (await client.get("/study-plans", headers=API_HEADERS)).json()
    assert listed["completed"] == []
    assert len(listed["archived"]) == 1


async def test_stale_item_completion_loses_to_curriculum_claim(
    client, stub_import, db, monkeypatch
):
    from app.routers import study_plan as router

    plan = await make_plan(client)
    plan_id = uuid.UUID(plan["id"])
    item = (
        await db.exec(
            select(StudyPlanItem)
            .where(StudyPlanItem.plan_id == plan_id)
            .order_by(StudyPlanItem.guide_order)
        )
    ).first()
    original_complete = router._complete_item_cas

    async def curriculum_claim_wins(session, stale_item, *, completed_at):
        await session.exec(
            update(StudyPlanItem)
            .where(StudyPlanItem.id == stale_item.id)
            .values(
                full_title="New curriculum content",
                updated_at=completed_at,
            )
            .execution_options(synchronize_session=False)
        )
        return await original_complete(
            session, stale_item, completed_at=completed_at + timedelta(seconds=1)
        )

    monkeypatch.setattr(router, "_complete_item_cas", curriculum_claim_wins)

    response = await client.post(
        f"/study-plans/{plan_id}/items/{item.id}/complete", headers=API_HEADERS
    )

    assert response.status_code == 409
    assert response.json()["detail"].startswith("item changed")
    await db.refresh(item)
    assert item.status == "pending"
    assert item.completed_at is None


async def test_duplicating_copies_structure_and_resets_progress(client, stub_import, db):
    payload = import_payload()
    payload["items"][0] = {
        **payload["items"][0],
        "resources": [
            {
                "kind": "lesson",
                "provider": "Hello Interview",
                "label": "Request path",
                "action_label": "Open lesson",
                "url": "https://example.com/request-path",
                "language": "",
            }
        ],
        "mapped_recall_topics": ["Request path failures"],
        "stretch_actions": [
            {
                "title": "Trace another request",
                "done_when": "Explain every boundary.",
                "minutes": 30,
                "resource_url": "",
                "resource_label": "",
            }
        ],
    }
    stub_import.response = payload
    plan = await make_plan(client)
    week = (await client.get(f"/study-plans/{plan['id']}/weeks/1", headers=API_HEADERS)).json()
    item_id = week["sections"][0]["rows"][0]["id"]
    await client.post(f"/study-plans/{plan['id']}/items/{item_id}/complete", headers=API_HEADERS)

    copy = await client.post(f"/study-plans/{plan['id']}/duplicate", headers=API_HEADERS)
    assert copy.status_code == 201
    body = copy.json()
    assert body["status"] == "paused"
    assert body["title"].endswith("(copy)")
    assert body["week_total"] == 4

    copied_items = (
        await db.exec(select(StudyPlanItem).where(StudyPlanItem.plan_id == uuid.UUID(body["id"])))
    ).all()
    assert len(copied_items) == 5
    assert all(i.status == "pending" for i in copied_items)
    assert all(i.completed_at is None for i in copied_items)
    assert all(not i.study_block_reminder_on for i in copied_items)
    copied_metadata = next(item for item in copied_items if item.full_title == "Item L1")
    assert copied_metadata.resources[0]["provider"] == "Hello Interview"
    assert copied_metadata.mapped_recall_topics == ["Request path failures"]
    assert copied_metadata.stretch_actions[0]["minutes"] == 30

    # It copies no plan history: the duplicate starts with an empty revision log.
    revisions = (
        await client.get(f"/study-plans/{body['id']}/revisions", headers=API_HEADERS)
    ).json()
    assert revisions == []


async def test_retired_items_stay_out_of_progress_recap_and_duplicates(
    client, stub_import, db
):
    plan = await make_plan(client)
    plan_id = uuid.UUID(plan["id"])
    live = (
        await db.exec(
            select(StudyPlanItem)
            .where(StudyPlanItem.plan_id == plan_id)
            .order_by(StudyPlanItem.guide_order)
        )
    ).first()
    completed = await client.post(
        f"/study-plans/{plan['id']}/items/{live.id}/complete", headers=API_HEADERS
    )
    assert completed.status_code == 200

    retired = StudyPlanItem(
        plan_id=plan_id,
        phase_id=live.phase_id,
        week_id=live.week_id,
        guide_order=0,
        curriculum_key="retired-v3-item",
        type="learn",
        priority="core",
        full_title="Retired historical curriculum item",
        estimate_minutes=720,
        status="removed",
        notes="Historical context stays stored.",
    )
    db.add(retired)
    await db.commit()
    retired_before = (
        await db.exec(select(StudyPlanItem).where(StudyPlanItem.id == retired.id))
    ).one().model_dump()

    stale_paths = [
        ("GET", f"/study-plans/{plan['id']}/items/{retired.id}", None),
        ("POST", f"/study-plans/{plan['id']}/items/{retired.id}/complete", None),
        (
            "PATCH",
            f"/study-plans/{plan['id']}/items/{retired.id}",
            {"notes": "Do not resurrect this row."},
        ),
        (
            "GET",
            f"/study-plans/{plan['id']}/items/{retired.id}/practice-debrief",
            None,
        ),
        (
            "GET",
            f"/study-plans/{plan['id']}/items/{retired.id}/card-proposals",
            None,
        ),
        (
            "POST",
            f"/study-plans/{plan['id']}/items/{retired.id}/card-proposals",
            None,
        ),
    ]
    for method, path, body in stale_paths:
        response = await client.request(method, path, json=body, headers=API_HEADERS)
        assert response.status_code == 404
    assert (
        await db.exec(select(StudyPlanItem).where(StudyPlanItem.id == retired.id))
    ).one().model_dump() == retired_before

    overview = (await client.get(f"/study-plans/{plan['id']}", headers=API_HEADERS)).json()
    assert overview["phases"][0]["weeks"][0]["status"] == "Complete"

    recap = (
        await client.get(f"/study-plans/{plan['id']}/recap", headers=API_HEADERS)
    ).json()
    assert recap["core_complete"] == 1
    assert recap["core_total"] == 4
    assert "Retired historical curriculum item" not in recap["remaining_gaps"]

    await client.post(f"/study-plans/{plan['id']}/complete", headers=API_HEADERS)
    listed = (await client.get("/study-plans", headers=API_HEADERS)).json()
    assert listed["completed"][0]["meta"].endswith("· 1 of 4 Core")

    copied = (
        await client.post(f"/study-plans/{plan['id']}/duplicate", headers=API_HEADERS)
    ).json()
    copied_items = (
        await db.exec(
            select(StudyPlanItem).where(
                StudyPlanItem.plan_id == uuid.UUID(copied["id"])
            )
        )
    ).all()
    assert len(copied_items) == 5
    assert "Retired historical curriculum item" not in {
        item.full_title for item in copied_items
    }


async def test_a_duplicate_is_not_active_until_it_is_made_active(client, stub_import):
    plan = await make_plan(client)
    copy = (await client.post(f"/study-plans/{plan['id']}/duplicate", headers=API_HEADERS)).json()

    activated = await client.post(
        f"/study-plans/{copy['id']}/activate",
        params={"base_plan_revision": copy["revision"]},
        headers=API_HEADERS,
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"

    original = (await client.get(f"/study-plans/{plan['id']}", headers=API_HEADERS)).json()
    assert original["status"] == "paused"


# --- Today ------------------------------------------------------------------


async def test_the_today_summary_is_one_line_of_position(client, stub_import):
    plan = await make_plan(client)
    summary = (await client.get("/study-plans/active/summary", headers=API_HEADERS)).json()
    assert summary["active"] is True
    assert summary["plan_id"] == plan["id"]
    assert summary["week_index"] == 1
    assert summary["week_total"] == 4
    assert summary["phase_title"] == "Foundations"
    # No capacity, no progress, no description — Today asks a different question.
    assert set(summary) == {
        "active",
        "plan_id",
        "title",
        "subject",
        "week_index",
        "week_total",
        "phase_title",
        "next_block_label",
    }


async def test_the_today_summary_names_the_next_study_block(client, stub_import):
    plan = await make_plan(client)
    week = (await client.get(f"/study-plans/{plan['id']}/weeks/1", headers=API_HEADERS)).json()
    item_id = week["sections"][0]["rows"][0]["id"]
    await client.patch(
        f"/study-plans/{plan['id']}/items/{item_id}",
        json={"study_block_weekday": 2, "study_block_minute_of_day": 1140},
        headers=API_HEADERS,
    )
    summary = (await client.get("/study-plans/active/summary", headers=API_HEADERS)).json()
    assert summary["next_block_label"] == "Tue 19:00"


# --- drill-down -------------------------------------------------------------


async def test_week_detail_carries_two_facts_and_sections_by_type(client, stub_import):
    plan = await make_plan(client)
    week = (await client.get(f"/study-plans/{plan['id']}/weeks/4", headers=API_HEADERS)).json()

    assert week["core_complete"] == 0
    assert week["core_total"] == 1
    assert week["planned_minutes"] == 90
    assert week["capacity_minutes"] == 720
    assert [s["type"] for s in week["sections"]] == ["learn", "practice"]
    assert week["sections"][1]["rows"][0]["optional"] is True


async def test_the_reviews_note_lives_under_retrieve(client, stub_import):
    stub_import.response = import_payload()
    stub_import.response["items"].append(
        _item("R1", 1, 9, type="retrieve", priority="recurring", estimate_minutes=30)
    )
    plan = await make_plan(client)
    week = (await client.get(f"/study-plans/{plan['id']}/weeks/1", headers=API_HEADERS)).json()

    retrieve = next(s for s in week["sections"] if s["type"] == "retrieve")
    assert "Doesn't block the week" in retrieve["aside"]
    assert "Reviews continue separately in Today" in retrieve["note"]
    for section in week["sections"]:
        if section["type"] != "retrieve":
            assert section["note"] == ""


async def test_item_detail_keeps_the_full_explanatory_structure(client, stub_import):
    plan = await make_plan(client)
    week = (await client.get(f"/study-plans/{plan['id']}/weeks/1", headers=API_HEADERS)).json()
    item_id = week["sections"][0]["rows"][0]["id"]

    item = (
        await client.get(f"/study-plans/{plan['id']}/items/{item_id}", headers=API_HEADERS)
    ).json()
    assert item["why_it_matters"]
    assert item["done_when"]
    assert item["phase_title"] == "Foundations"
    assert item["week_index"] == 1
    assert item["status"] == "pending"


async def test_a_missing_plan_week_or_item_is_a_404(client, stub_import):
    plan = await make_plan(client)
    ghost = uuid.uuid4()
    assert (await client.get(f"/study-plans/{ghost}", headers=API_HEADERS)).status_code == 404
    assert (
        await client.get(f"/study-plans/{plan['id']}/weeks/99", headers=API_HEADERS)
    ).status_code == 404
    assert (
        await client.get(f"/study-plans/{plan['id']}/items/{ghost}", headers=API_HEADERS)
    ).status_code == 404


# --- replanning -------------------------------------------------------------


async def test_a_capacity_change_that_displaces_work_writes_nothing(client, stub_import, db):
    plan = await make_plan(client)
    before = (await db.exec(select(StudyPlan))).one().revision

    proposal = await client.patch(
        f"/study-plans/{plan['id']}/weeks/1/capacity",
        json={
            "week_index": 1,
            "override_capacity_minutes": 30,
            "base_plan_revision": plan["revision"],
        },
        headers=API_HEADERS,
    )
    assert proposal.status_code == 200
    body = proposal.json()
    assert body["moves"] or body["unresolved"]

    await db.refresh((await db.exec(select(StudyPlan))).one())
    assert (await db.exec(select(StudyPlan))).one().revision == before


async def test_a_capacity_change_that_moves_nothing_applies_immediately(client, stub_import):
    plan = await make_plan(client)
    response = await client.patch(
        f"/study-plans/{plan['id']}/weeks/1/capacity",
        json={
            "week_index": 1,
            "override_capacity_minutes": 600,
            "base_plan_revision": plan["revision"],
        },
        headers=API_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["moves"] == []

    week = (await client.get(f"/study-plans/{plan['id']}/weeks/1", headers=API_HEADERS)).json()
    assert week["capacity_minutes"] == 600


async def test_a_stale_proposal_is_a_conflict_and_must_be_regenerated(client, stub_import):
    plan = await make_plan(client)
    stale = plan["revision"] - 1
    response = await client.post(
        f"/study-plans/{plan['id']}/replans/preview",
        json={"base_plan_revision": stale},
        headers=API_HEADERS,
    )
    assert response.status_code == 409
    assert "changed" in response.json()["detail"]


async def test_applying_an_invalid_proposal_is_refused(client, stub_import):
    plan = await make_plan(client)
    # Both of phase 1's weeks squeezed to 30 minutes, so its 60-minute Core item
    # has nowhere left inside its own phase to go.
    response = await client.post(
        f"/study-plans/{plan['id']}/replans/apply",
        json={
            "base_plan_revision": plan["revision"],
            "capacity_overrides": {"1": 30, "2": 30},
        },
        headers=API_HEADERS,
    )
    assert response.status_code == 409
    assert "validate" in response.json()["detail"]


async def test_a_confirmed_replan_moves_work_and_bumps_the_revision(client, stub_import):
    plan = await make_plan(client)
    applied = await client.post(
        f"/study-plans/{plan['id']}/replans/apply",
        json={
            "base_plan_revision": plan["revision"],
            "capacity_overrides": {"1": 30},
            "default_capacity_minutes": 720,
            "deferred_item_ids": [],
            "extra_weeks": 1,
            "insert_after_phase": 1,
        },
        headers=API_HEADERS,
    )
    assert applied.status_code == 200
    assert applied.json()["can_apply"]

    overview = (await client.get(f"/study-plans/{plan['id']}", headers=API_HEADERS)).json()
    assert overview["revision"] > plan["revision"]

    revisions = (
        await client.get(f"/study-plans/{plan['id']}/revisions", headers=API_HEADERS)
    ).json()
    assert revisions[0]["kind"] == "replan"


async def test_a_proposal_states_what_stays_the_same(client, stub_import):
    plan = await make_plan(client)
    body = (
        await client.post(
            f"/study-plans/{plan['id']}/replans/preview",
            json={"base_plan_revision": plan["revision"]},
            headers=API_HEADERS,
        )
    ).json()
    assert any("cards" in line for line in body["unchanged"])
    assert body["headline"]


# --- item progress ----------------------------------------------------------


async def test_completing_and_reopening_an_item(client, stub_import):
    plan = await make_plan(client)
    week = (await client.get(f"/study-plans/{plan['id']}/weeks/1", headers=API_HEADERS)).json()
    item_id = week["sections"][0]["rows"][0]["id"]

    done = await client.post(
        f"/study-plans/{plan['id']}/items/{item_id}/complete", headers=API_HEADERS
    )
    assert done.json()["status"] == "complete"
    assert done.json()["completed_at"] is not None

    again = await client.post(
        f"/study-plans/{plan['id']}/items/{item_id}/complete", headers=API_HEADERS
    )
    assert again.status_code == 409

    preview = await client.post(
        f"/study-plans/{plan['id']}/items/{item_id}/reopen/preview", headers=API_HEADERS
    )
    assert preview.status_code == 200
    assert any("progress only" in line for line in preview.json()["unchanged"])

    overview = (await client.get(f"/study-plans/{plan['id']}", headers=API_HEADERS)).json()
    reopened = await client.post(
        f"/study-plans/{plan['id']}/items/{item_id}/reopen",
        params={"base_plan_revision": overview["revision"]},
        headers=API_HEADERS,
    )
    assert reopened.json()["status"] == "pending"
    assert reopened.json()["reopened_at"] is not None
    assert reopened.json()["completed_at"] is None


async def test_item_detail_revision_can_bind_a_matching_completion(
    client, stub_import
):
    plan = await make_plan(client)
    week = (
        await client.get(
            f"/study-plans/{plan['id']}/weeks/1", headers=API_HEADERS
        )
    ).json()
    item_id = week["sections"][0]["rows"][0]["id"]

    opened = (
        await client.get(
            f"/study-plans/{plan['id']}/items/{item_id}", headers=API_HEADERS
        )
    ).json()

    assert opened["plan_revision"] == plan["revision"]
    completed = await client.post(
        f"/study-plans/{plan['id']}/items/{item_id}/complete",
        params={"base_plan_revision": opened["plan_revision"]},
        headers=API_HEADERS,
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "complete"
    assert completed.json()["plan_revision"] == opened["plan_revision"]


async def test_stale_item_revision_rejects_completion_without_writes(
    client, stub_import
):
    plan = await make_plan(client)
    week = (
        await client.get(
            f"/study-plans/{plan['id']}/weeks/1", headers=API_HEADERS
        )
    ).json()
    item_id = week["sections"][0]["rows"][0]["id"]
    item_url = f"/study-plans/{plan['id']}/items/{item_id}"
    opened = (await client.get(item_url, headers=API_HEADERS)).json()

    edited = await client.patch(
        item_url,
        json={"estimate_minutes": opened["estimate_minutes"] + 30},
        headers=API_HEADERS,
    )
    assert edited.status_code == 200
    before_attempt = edited.json()
    assert before_attempt["plan_revision"] == opened["plan_revision"] + 1

    stale = await client.post(
        f"{item_url}/complete",
        params={"base_plan_revision": opened["plan_revision"]},
        headers=API_HEADERS,
    )

    assert stale.status_code == 409
    assert "opened" in stale.json()["detail"]
    assert (await client.get(item_url, headers=API_HEADERS)).json() == before_attempt


async def test_first_party_fresh_work_requires_a_revision_after_later_markers_drop(
    client, db
):
    seeded = await load_first_party_plan(
        start_date=date(2026, 7, 27), activate=True, db=db
    )
    plan_id = uuid.UUID(seeded.plan_id)
    item = (
        await db.exec(
            select(StudyPlanItem).where(
                StudyPlanItem.plan_id == plan_id,
                StudyPlanItem.curriculum_key == "V4-W1-L3",
            )
        )
    ).one()
    item_url = f"/study-plans/{plan_id}/items/{item.id}"
    opened = (await client.get(item_url, headers=API_HEADERS)).json()
    before_missing = item.model_dump()

    missing = await client.post(f"{item_url}/complete", headers=API_HEADERS)

    assert missing.status_code == 409
    assert "requires a current plan revision" in missing.json()["detail"]
    await db.refresh(item)
    assert item.model_dump() == before_missing

    # A later release can omit its release-local marker. The earlier revision's
    # durable key remains in the per-plan union, so an old client is still
    # refused while a current client can complete normally.
    plan = (await db.exec(select(StudyPlan).where(StudyPlan.id == plan_id))).one()
    plan.revision += 1
    db.add(plan)
    db.add(
        StudyPlanRevision(
            plan_id=plan_id,
            kind="curriculum_update",
            base_plan_revision=opened["plan_revision"],
            before={"seed_version": 5},
            after={"seed_version": 6},
            summary="Later release without a fresh-work marker",
        )
    )
    await db.commit()
    reopened = (await client.get(item_url, headers=API_HEADERS)).json()
    assert reopened["plan_revision"] == opened["plan_revision"] + 1

    still_missing = await client.post(f"{item_url}/complete", headers=API_HEADERS)
    assert still_missing.status_code == 409

    completed = await client.post(
        f"{item_url}/complete",
        params={"base_plan_revision": reopened["plan_revision"]},
        headers=API_HEADERS,
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "complete"


async def test_generic_plan_keeps_legacy_completion_without_a_revision(
    client, stub_import
):
    plan = await make_plan(client)
    week = (
        await client.get(
            f"/study-plans/{plan['id']}/weeks/1", headers=API_HEADERS
        )
    ).json()
    item_id = week["sections"][0]["rows"][0]["id"]

    completed = await client.post(
        f"/study-plans/{plan['id']}/items/{item_id}/complete",
        headers=API_HEADERS,
    )

    assert completed.status_code == 200
    assert completed.json()["status"] == "complete"


async def test_an_estimate_edit_is_recorded_as_a_material_change(client, stub_import):
    plan = await make_plan(client)
    week = (await client.get(f"/study-plans/{plan['id']}/weeks/1", headers=API_HEADERS)).json()
    item_id = week["sections"][0]["rows"][0]["id"]

    edited = await client.patch(
        f"/study-plans/{plan['id']}/items/{item_id}",
        json={"estimate_minutes": 120, "notes": "Write out the WAL path once."},
        headers=API_HEADERS,
    )
    assert edited.json()["estimate_minutes"] == 120
    assert edited.json()["estimate_source"] == "user_edited"
    assert edited.json()["notes"] == "Write out the WAL path once."

    revisions = (
        await client.get(f"/study-plans/{plan['id']}/revisions", headers=API_HEADERS)
    ).json()
    assert revisions[0]["kind"] == "item_edit"


async def test_an_estimate_off_the_thirty_minute_grid_is_rejected(client, stub_import):
    plan = await make_plan(client)
    week = (await client.get(f"/study-plans/{plan['id']}/weeks/1", headers=API_HEADERS)).json()
    item_id = week["sections"][0]["rows"][0]["id"]
    response = await client.patch(
        f"/study-plans/{plan['id']}/items/{item_id}",
        json={"estimate_minutes": 45},
        headers=API_HEADERS,
    )
    assert response.status_code == 422


# --- card proposals ---------------------------------------------------------


async def _complete_first_item(client, plan) -> str:
    week = (await client.get(f"/study-plans/{plan['id']}/weeks/1", headers=API_HEADERS)).json()
    item_id = week["sections"][0]["rows"][0]["id"]
    await client.post(f"/study-plans/{plan['id']}/items/{item_id}/complete", headers=API_HEADERS)
    return item_id


async def _complete_practice_item(client, plan) -> str:
    week = (await client.get(f"/study-plans/{plan['id']}/weeks/4", headers=API_HEADERS)).json()
    practice = next(section for section in week["sections"] if section["type"] == "practice")
    item_id = practice["rows"][0]["id"]
    response = await client.post(
        f"/study-plans/{plan['id']}/items/{item_id}/complete", headers=API_HEADERS
    )
    assert response.status_code == 200
    return item_id


async def test_practice_completion_offers_debrief_before_card_proposals(
    client, stub_import, stub_cards
):
    plan = await make_plan(client)
    item_id = await _complete_practice_item(client, plan)

    item = (
        await client.get(f"/study-plans/{plan['id']}/items/{item_id}", headers=API_HEADERS)
    ).json()
    assert item["practice_debrief_eligible"] is True
    assert item["practice_debrief"] is None
    assert item["card_proposals_available"] is False

    blocked = await client.post(
        f"/study-plans/{plan['id']}/items/{item_id}/card-proposals", headers=API_HEADERS
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "practice debrief is required"
    assert stub_cards.calls == 0


async def test_practice_debrief_draft_is_idempotent_and_submit_is_immutable(
    client, stub_import, db
):
    plan = await make_plan(client)
    item_id = await _complete_practice_item(client, plan)
    path = f"/study-plans/{plan['id']}/items/{item_id}/practice-debrief"
    text = "I put the retry loop outside the timeout boundary."

    first = await client.patch(f"{path}/draft", json={"text": text}, headers=API_HEADERS)
    second = await client.patch(f"{path}/draft", json={"text": text}, headers=API_HEADERS)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len((await db.exec(select(StudyPlanPracticeDebrief))).all()) == 1

    submitted = await client.post(path, json={"text": text}, headers=API_HEADERS)
    replay = await client.post(path, json={"text": text}, headers=API_HEADERS)
    changed = await client.post(path, json={"text": text + " Changed."}, headers=API_HEADERS)
    assert submitted.status_code == replay.status_code == 200
    assert submitted.json()["text"] == text
    assert changed.status_code == 409

    item = (
        await client.get(f"/study-plans/{plan['id']}/items/{item_id}", headers=API_HEADERS)
    ).json()
    assert item["card_proposals_available"] is True
    assert item["practice_debrief"]["summary"] == text


async def test_practice_proposals_use_the_gap_but_keep_the_source_as_authority(
    client, stub_import, stub_cards
):
    stub_cards.candidates = []
    plan = await make_plan(client)
    item_id = await _complete_practice_item(client, plan)
    text = "I treated retries and timeouts as unrelated settings."
    await client.post(
        f"/study-plans/{plan['id']}/items/{item_id}/practice-debrief",
        json={"text": text},
        headers=API_HEADERS,
    )

    response = await client.post(
        f"/study-plans/{plan['id']}/items/{item_id}/card-proposals", headers=API_HEADERS
    )
    assert response.status_code == 200
    assert stub_cards.calls == 1
    assert stub_cards.last_kwargs["observed_gap"] == text
    assert stub_cards.last_kwargs["source_excerpt"]


async def test_practice_without_a_source_answer_basis_never_offers_debrief(
    client, stub_import, stub_cards
):
    payload = import_payload()
    payload["items"][-1] = {**payload["items"][-1], "source_excerpt": ""}
    stub_import.response = payload
    plan = await make_plan(client)
    item_id = await _complete_practice_item(client, plan)

    item = (
        await client.get(f"/study-plans/{plan['id']}/items/{item_id}", headers=API_HEADERS)
    ).json()
    assert item["practice_debrief_eligible"] is False
    save = await client.post(
        f"/study-plans/{plan['id']}/items/{item_id}/practice-debrief",
        json={"text": "A gap"},
        headers=API_HEADERS,
    )
    assert save.status_code == 409
    assert stub_cards.calls == 0


async def test_cards_are_only_proposed_after_the_item_is_complete(client, stub_import, stub_cards):
    plan = await make_plan(client)
    week = (await client.get(f"/study-plans/{plan['id']}/weeks/1", headers=API_HEADERS)).json()
    item_id = week["sections"][0]["rows"][0]["id"]

    early = await client.post(
        f"/study-plans/{plan['id']}/items/{item_id}/card-proposals", headers=API_HEADERS
    )
    assert early.status_code == 409
    assert stub_cards.calls == 0


async def test_an_unsupported_subject_never_reaches_the_card_model(client, stub_import, stub_cards):
    stub_import.response = import_payload(
        subject="Human anatomy",
        subject_slug="anatomy",
        supports_technical_recall_cards=True,
    )
    plan = await make_plan(client)
    item_id = await _complete_first_item(client, plan)

    response = await client.post(
        f"/study-plans/{plan['id']}/items/{item_id}/card-proposals", headers=API_HEADERS
    )
    assert response.status_code == 200
    body = response.json()
    assert body["supports_recall_cards"] is False
    assert body["proposals"] == []
    assert body["suggested_count"] == 0
    assert stub_cards.calls == 0


async def test_an_item_without_confirmed_recall_support_never_reaches_the_card_model(
    client, stub_import, stub_cards
):
    payload = import_payload()
    payload["items"][0] = {**payload["items"][0], "recall_supported": False}
    stub_import.response = payload
    plan = await make_plan(client)
    item_id = await _complete_first_item(client, plan)

    response = await client.post(
        f"/study-plans/{plan['id']}/items/{item_id}/card-proposals", headers=API_HEADERS
    )

    assert response.status_code == 200
    assert response.json()["proposals"] == []
    assert "no confirmed answer basis" in response.json()["note"]
    assert stub_cards.calls == 0


async def test_a_candidate_that_fails_the_gate_is_not_suggested_and_not_counted(
    client, stub_import, stub_cards
):
    stub_cards.candidates = [
        {
            "topic": "Postgres write path",
            "category": "Databases",
            "canonical_question": "Walk the write path from WAL append to checkpoint.",
            "reason": "The mechanism behind most write-throughput answers.",
            "gate": gate(all_passed=True),
        },
        {
            "topic": "Autovacuum thresholds",
            "category": "Databases",
            "canonical_question": "What triggers autovacuum?",
            "reason": "",
            "gate": gate(all_passed=False, failing=3),
        },
    ]
    plan = await make_plan(client)
    item_id = await _complete_first_item(client, plan)

    body = (
        await client.post(
            f"/study-plans/{plan['id']}/items/{item_id}/card-proposals", headers=API_HEADERS
        )
    ).json()
    assert body["suggested_count"] == 1
    failed = next(p for p in body["proposals"] if p["topic"] == "Autovacuum thresholds")
    assert failed["disposition"] == "not_suggested"
    assert failed["failed_question_index"] == 3
    assert "definition" in failed["failed_reason"]


async def test_a_missing_gate_answer_counts_as_a_failure(client, stub_import, stub_cards):
    stub_cards.candidates = [
        {
            "topic": "Partial gate",
            "category": "Databases",
            "canonical_question": "Why?",
            "reason": "",
            "gate": [{"question_index": 1, "passed": True, "reason": "yes"}],
        }
    ]
    plan = await make_plan(client)
    item_id = await _complete_first_item(client, plan)
    body = (
        await client.post(
            f"/study-plans/{plan['id']}/items/{item_id}/card-proposals", headers=API_HEADERS
        )
    ).json()
    assert body["suggested_count"] == 0
    assert body["proposals"][0]["disposition"] == "not_suggested"


async def test_an_exact_normalized_duplicate_is_never_proposed(client, stub_import, stub_cards, db):
    db.add(make_card(topic="Postgres  Write-Path!"))
    await db.commit()

    stub_cards.candidates = [
        {
            "topic": "postgres write path",
            "category": "Databases",
            "canonical_question": "Walk the write path.",
            "reason": "",
            "gate": gate(),
        }
    ]
    plan = await make_plan(client)
    item_id = await _complete_first_item(client, plan)
    body = (
        await client.post(
            f"/study-plans/{plan['id']}/items/{item_id}/card-proposals", headers=API_HEADERS
        )
    ).json()
    assert body["suggested_count"] == 0
    assert body["proposals"][0]["disposition"] == "existing"


async def test_v2_card_proposals_select_and_describe_weak_recall(
    client, stub_import, stub_cards, db, monkeypatch
):
    monkeypatch.setattr(get_settings(), "scoring_contract_version", 2)
    weak_recall = make_card(topic="Weak Recall", last_score=5, last_accuracy=1)
    weak_recall.last_score_contract_version = 1
    composite_only_weak = make_card(topic="Weak Composite", last_score=1, last_accuracy=None)
    composite_only_weak.last_score_contract_version = 1
    db.add(weak_recall)
    db.add(composite_only_weak)
    await db.commit()
    stub_cards.candidates = []

    plan = await make_plan(client)
    item_id = await _complete_first_item(client, plan)
    response = await client.post(
        f"/study-plans/{plan['id']}/items/{item_id}/card-proposals", headers=API_HEADERS
    )

    assert response.status_code == 200
    weak_context = stub_cards.last_kwargs["existing_weak_topics"]
    assert "Weak Recall" in weak_context
    assert "Weak Composite" not in weak_context


# --- acceptance -------------------------------------------------------------


async def _one_suggested(client, stub_cards, plan) -> dict:
    stub_cards.candidates = [
        {
            "topic": "Postgres write path",
            "category": "Databases",
            "canonical_question": (
                "Walk the write path for a single row update, from WAL append to "
                "checkpoint. Where can it stall under load?"
            ),
            "reason": "The mechanism behind most write-throughput answers.",
            "gate": gate(),
        }
    ]
    item_id = await _complete_first_item(client, plan)
    body = (
        await client.post(
            f"/study-plans/{plan['id']}/items/{item_id}/card-proposals", headers=API_HEADERS
        )
    ).json()
    return next(p for p in body["proposals"] if p["disposition"] == "suggested")


async def test_accepting_a_proposal_creates_the_card_and_keeps_its_question(
    client, stub_import, stub_cards, db
):
    plan = await make_plan(client)
    proposal = await _one_suggested(client, stub_cards, plan)

    response = await client.post(
        f"/study-plans/{plan['id']}/card-proposals/accept",
        json={
            "selected_proposal_ids": [proposal["id"]],
            "idempotency_key": "key-alpha-0001",
            "proposal_revision": proposal["revision"],
        },
        headers=API_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "committed"
    assert len(body["created_card_ids"]) == 1
    assert body["replayed"] is False

    card = (await db.exec(select(Card).where(Card.topic == "Postgres write path"))).one()
    # The approved question is persisted, so the first review does not regenerate.
    assert card.canonical_question == proposal["canonical_question"]
    assert card.answer_basis == GUIDE[0:20].strip()
    assert card.answer_rubric["mechanism"] == "The source-supported mechanism."
    assert card.source_label.endswith(" · Study Plan")
    assert card.delivery_mode == "conversational"
    assert card.ease_factor == 2.5
    assert card.repetitions == 0


async def test_a_retry_after_commit_returns_the_original_response(
    client, stub_import, stub_cards, db
):
    plan = await make_plan(client)
    proposal = await _one_suggested(client, stub_cards, plan)
    payload = {
        "selected_proposal_ids": [proposal["id"]],
        "idempotency_key": "key-alpha-0002",
        "proposal_revision": proposal["revision"],
    }

    first = await client.post(
        f"/study-plans/{plan['id']}/card-proposals/accept", json=payload, headers=API_HEADERS
    )
    second = await client.post(
        f"/study-plans/{plan['id']}/card-proposals/accept", json=payload, headers=API_HEADERS
    )

    assert second.status_code == 200
    assert second.json()["created_card_ids"] == first.json()["created_card_ids"]
    assert second.json()["replayed"] is True
    assert len((await db.exec(select(Card))).all()) == 1


async def test_the_same_key_with_a_different_request_is_a_conflict(client, stub_import, stub_cards):
    plan = await make_plan(client)
    proposal = await _one_suggested(client, stub_cards, plan)
    key = "key-alpha-0003"

    await client.post(
        f"/study-plans/{plan['id']}/card-proposals/accept",
        json={
            "selected_proposal_ids": [proposal["id"]],
            "idempotency_key": key,
            "proposal_revision": proposal["revision"],
        },
        headers=API_HEADERS,
    )
    conflict = await client.post(
        f"/study-plans/{plan['id']}/card-proposals/accept",
        json={
            "selected_proposal_ids": [proposal["id"]],
            "idempotency_key": key,
            "proposal_revision": proposal["revision"],
            "edits": {proposal["id"]: {"topic": "Something else"}},
        },
        headers=API_HEADERS,
    )
    assert conflict.status_code == 409
    assert "different request" in conflict.json()["detail"]


async def test_a_retry_after_a_rollback_may_safely_run_again(client, stub_import, stub_cards, db):
    """A processing record with no cards behind it must not block the retry."""
    plan = await make_plan(client)
    proposal = await _one_suggested(client, stub_cards, plan)
    payload = {
        "selected_proposal_ids": [proposal["id"]],
        "idempotency_key": "key-alpha-0004",
        "proposal_revision": proposal["revision"],
    }
    stranded = StudyPlanCardProposalAcceptance(
        proposal_id=uuid.UUID(proposal["id"]),
        idempotency_key=payload["idempotency_key"],
        request_hash=__import__("app.services.study_plan", fromlist=["x"]).request_hash(
            [uuid.UUID(proposal["id"])], {}
        ),
        proposal_revision=proposal["revision"],
        status="processing",
    )
    db.add(stranded)
    await db.commit()

    response = await client.post(
        f"/study-plans/{plan['id']}/card-proposals/accept", json=payload, headers=API_HEADERS
    )
    assert response.status_code == 200
    assert response.json()["replayed"] is False
    assert len((await db.exec(select(Card))).all()) == 1


async def test_a_duplicate_appearing_after_preview_aborts_the_whole_batch(
    client, stub_import, stub_cards, db
):
    plan = await make_plan(client)
    proposal = await _one_suggested(client, stub_cards, plan)

    # Written by hand between Preview and Accept.
    db.add(make_card(topic="Postgres Write Path"))
    await db.commit()

    response = await client.post(
        f"/study-plans/{plan['id']}/card-proposals/accept",
        json={
            "selected_proposal_ids": [proposal["id"]],
            "idempotency_key": "key-alpha-0005",
            "proposal_revision": proposal["revision"],
        },
        headers=API_HEADERS,
    )
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]

    # Nothing was created, and the candidate refreshed as existing.
    cards = (await db.exec(select(Card))).all()
    assert len(cards) == 1
    refreshed = (await db.exec(select(StudyPlanCardProposal))).one()
    assert refreshed.disposition == "existing"


async def test_an_edited_proposal_invalidates_a_stale_acceptance_intent(
    client, stub_import, stub_cards
):
    plan = await make_plan(client)
    proposal = await _one_suggested(client, stub_cards, plan)
    response = await client.post(
        f"/study-plans/{plan['id']}/card-proposals/accept",
        json={
            "selected_proposal_ids": [proposal["id"]],
            "idempotency_key": "key-alpha-0006",
            "proposal_revision": proposal["revision"] + 1,
        },
        headers=API_HEADERS,
    )
    assert response.status_code == 409
    assert "edited" in response.json()["detail"]


async def test_a_failed_candidate_cannot_be_accepted(client, stub_import, stub_cards):
    stub_cards.candidates = [
        {
            "topic": "Autovacuum thresholds",
            "category": "Databases",
            "canonical_question": "What triggers autovacuum?",
            "reason": "",
            "gate": gate(all_passed=False),
        }
    ]
    plan = await make_plan(client)
    item_id = await _complete_first_item(client, plan)
    body = (
        await client.post(
            f"/study-plans/{plan['id']}/items/{item_id}/card-proposals", headers=API_HEADERS
        )
    ).json()

    response = await client.post(
        f"/study-plans/{plan['id']}/card-proposals/accept",
        json={
            "selected_proposal_ids": [body["proposals"][0]["id"]],
            "idempotency_key": "key-alpha-0007",
            "proposal_revision": 1,
        },
        headers=API_HEADERS,
    )
    assert response.status_code == 409
    assert "not selectable" in response.json()["detail"]


async def test_resolving_a_possible_overlap_is_the_users_call(client, stub_import, stub_cards, db):
    plan = await make_plan(client)
    proposal = await _one_suggested(client, stub_cards, plan)

    row = (await db.exec(select(StudyPlanCardProposal))).one()
    row.duplicate_check_result = "possible"
    row.disposition = "possible_overlap"
    db.add(row)
    await db.commit()

    skipped = await client.post(
        f"/study-plans/{plan['id']}/card-proposals/resolve-duplicate",
        json={"proposal_id": proposal["id"], "action": "skip"},
        headers=API_HEADERS,
    )
    assert skipped.status_code == 204
    await db.refresh(row)
    assert row.disposition == "skipped"


# --- auth -------------------------------------------------------------------


async def test_study_plan_endpoints_require_the_api_key(client):
    for method, path in (
        ("get", "/study-plans"),
        ("get", "/study-plans/active/summary"),
        ("post", "/study-plans/preview"),
    ):
        response = await getattr(client, method)(path)
        assert response.status_code == 401, path
