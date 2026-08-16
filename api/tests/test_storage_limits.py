from sqlmodel import select

from app.models import FOUNDER_USER_ID, Card, MaterialSource
from app.services import materials, storage
from tests.conftest import API_HEADERS, GUIDE


async def test_material_source_count_quota_is_checked_before_background_work(
    client, db, monkeypatch
) -> None:
    monkeypatch.setattr(storage, "MAX_MATERIAL_SOURCES_PER_USER", 2)
    for index in range(2):
        db.add(
            MaterialSource(
                user_id=FOUNDER_USER_ID,
                title=f"Existing {index}",
                source_text=GUIDE,
            )
        )
    await db.commit()
    calls = []

    async def should_not_run(source_id):
        calls.append(source_id)

    monkeypatch.setattr(materials, "process_import", should_not_run)
    response = await client.post(
        "/materials/imports",
        headers=API_HEADERS,
        json={"title": "One too many", "source_text": GUIDE},
    )

    assert response.status_code == 429
    assert response.json()["detail"] == {
        "code": "storage_quota_exceeded",
        "resource": "material_sources",
        "max_sources": 2,
        "max_characters": storage.MAX_MATERIAL_SOURCE_CHARACTERS_PER_USER,
    }
    assert calls == []


async def test_material_character_quota_counts_existing_retained_text(
    client, db, monkeypatch
) -> None:
    db.add(
        MaterialSource(
            user_id=FOUNDER_USER_ID,
            title="Existing",
            source_text=GUIDE,
        )
    )
    await db.commit()
    monkeypatch.setattr(
        storage,
        "MAX_MATERIAL_SOURCE_CHARACTERS_PER_USER",
        len(GUIDE) * 2 - 1,
    )

    response = await client.post(
        "/materials/imports",
        headers=API_HEADERS,
        json={"title": "Too much retained text", "source_text": GUIDE},
    )

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "storage_quota_exceeded"


async def test_manual_material_creation_honors_source_and_character_quotas(
    client, db, monkeypatch
) -> None:
    body = {
        "title": "Manual material",
        "topics": [
            {
                "topic": "Write-ahead logging",
                "answer_anchor": "The log is durable before data pages are flushed.",
            }
        ],
    }
    retained_text = "\n\n".join(
        f"{topic['topic']}\n{topic['answer_anchor']}" for topic in body["topics"]
    )

    monkeypatch.setattr(storage, "MAX_MATERIAL_SOURCES_PER_USER", 0)
    source_limited = await client.post(
        "/materials/manual", headers=API_HEADERS, json=body
    )
    assert source_limited.status_code == 429
    assert source_limited.json()["detail"]["resource"] == "material_sources"

    monkeypatch.setattr(storage, "MAX_MATERIAL_SOURCES_PER_USER", 100)
    monkeypatch.setattr(
        storage, "MAX_MATERIAL_SOURCE_CHARACTERS_PER_USER", len(retained_text) - 1
    )
    character_limited = await client.post(
        "/materials/manual", headers=API_HEADERS, json=body
    )
    assert character_limited.status_code == 429
    assert character_limited.json()["detail"]["resource"] == "material_sources"

    assert not (await db.exec(select(MaterialSource))).all()
    assert not (await db.exec(select(Card))).all()


async def test_direct_study_plan_preview_has_an_account_draft_quota(
    client, stub_import, monkeypatch
) -> None:
    monkeypatch.setattr(storage, "MAX_STUDY_PLAN_DRAFTS_PER_USER", 0)

    response = await client.post(
        "/study-plans/preview",
        headers=API_HEADERS,
        json={
            "guide_text": GUIDE,
            "requested_weeks": 4,
            "weekly_capacity_minutes": 720,
            "mode": "flexible",
        },
    )

    assert response.status_code == 429
    assert response.json()["detail"]["resource"] == "study_plan_drafts"
