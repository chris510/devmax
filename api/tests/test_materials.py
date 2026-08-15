import asyncio
import json
import uuid
from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import (
    FOUNDER_USER_ID,
    SOURCE_CONFIRMED,
    SOURCE_FAILED,
    SOURCE_NEEDS_ATTENTION,
    SOURCE_PENDING,
    SOURCE_PROCESSING,
    SOURCE_READY,
    SOURCE_SUPERSEDED,
    Card,
    MaterialSource,
    MaterialTopicProposal,
    Session,
    StudyPlanGuideDraft,
)
from app.services import llm, materials, usage
from tests.conftest import (
    API_HEADERS,
    GUIDE,
    TEST_DATABASE_URL,
    import_payload,
    make_card,
)

LESSON_RUBRIC = {
    "mechanism": "A request crosses named routing stages before storage answers it.",
    "acceptable_alternative": "Equivalent names for the routing stages are acceptable.",
    "trade_off": "Each extra boundary adds latency in exchange for isolation or scale.",
    "failure_mode": "A failed routing stage can stop the request before storage.",
    "misconception": "The request does not jump directly from DNS to the database.",
}

NETWORKING_101_SOURCE = (
    "Networking 101 focuses on three layers relevant to system design. At the "
    "network layer, IP addresses and routes packets between networks using "
    "best-effort delivery, so packets may be lost, reordered, or duplicated. At "
    "the transport layer, TCP provides a reliable ordered byte stream using "
    "sequence numbers, acknowledgements, retransmission, flow control, and "
    "congestion control. At the application layer, protocols such as DNS and HTTP "
    "define message meaning. To load a webpage, DNS resolves the hostname, the "
    "client establishes a TCP connection over IP, and HTTP sends the request and "
    "response. Creating a new TCP connection for every HTTP request repeats "
    "handshake round trips and adds latency. Reusing persistent connections avoids "
    "repeated setup, but each open connection consumes sockets, memory, buffers, "
    "and server state, so services need timeouts and capacity limits."
)


def lesson_concept(source_text: str, **overrides) -> dict:
    prompts = [
        {
            "level": "definition_recognition",
            "question": "What signals distinguish this request path from a direct database call?",
        },
        {
            "level": "mechanism",
            "question": (
                "How does a request move through the routing stages before storage responds?"
            ),
        },
        {
            "level": "derivation",
            "question": "Why does adding a routing boundary change both isolation and latency?",
        },
        {
            "level": "application",
            "question": "How would you locate the failing routing stage in a stalled request?",
        },
        {
            "level": "failure_tradeoff",
            "question": "Where can this request path fail, and what does each boundary cost?",
        },
    ]
    base = {
        "topic": "Request routing path",
        "section_title": "Week 1: The request path",
        "source_excerpt": source_text[:120],
        "answer_basis": (
            "A request passes through DNS and a load balancer before application "
            "and storage work return a response."
        ),
        "canonical_question": (
            "How would you trace a request from DNS to storage and back?"
        ),
        "answer_rubric": LESSON_RUBRIC,
        "recall_questions": prompts,
    }
    return {**base, **overrides}


def lesson_findings(
    concepts: list[dict],
    *,
    verdicts: dict[tuple[int, str], str] | None = None,
    repairs: dict[tuple[int, str], str] | None = None,
) -> list[dict]:
    verdicts = verdicts or {}
    repairs = repairs or {}
    findings = []
    for concept_index, concept in enumerate(concepts, 1):
        evidence = concept["source_excerpt"][:300]
        for field in llm.LESSON_GROUNDING_FIELDS:
            key = (concept_index, field)
            verdict = verdicts.get(key, "supported")
            findings.append(
                {
                    "concept_index": concept_index,
                    "field": field,
                    "verdict": verdict,
                    "evidence_spans": [evidence],
                    "reason": (
                        "The expected answer is stated in the excerpt."
                        if verdict != "unsupported"
                        else "The field adds a detail the excerpt does not state."
                    ),
                    "repair": repairs.get(key, ""),
                }
            )
    return findings


def stub_lesson_verifier(monkeypatch, responses=None) -> list[dict]:
    calls: list[dict] = []
    queued = list(responses or [])

    async def verify(**kwargs):
        calls.append(kwargs)
        if queued:
            response = queued.pop(0)
            return response(kwargs["concepts"]) if callable(response) else response
        return lesson_findings(kwargs["concepts"])

    monkeypatch.setattr(llm, "verify_lesson_grounding", verify)
    return calls


def grounded_summary() -> dict[str, int]:
    return {
        "grounding_gate_version": materials.LESSON_GROUNDING_GATE_VERSION,
    }


def networking_concept(**overrides) -> dict:
    base = {
        "topic": "TCP reliability and web requests",
        "section_title": "Networking 101",
        "source_excerpt": NETWORKING_101_SOURCE,
        "answer_basis": (
            "TCP provides a reliable ordered byte stream above IP, while DNS and "
            "HTTP define the application-level steps used to load a webpage."
        ),
        "canonical_question": (
            "How does a webpage request move through DNS, TCP, IP, and HTTP?"
        ),
        "answer_rubric": {
            "mechanism": (
                "TCP uses sequence numbers, acknowledgements, retransmission, flow "
                "control, and congestion control for a reliable ordered byte stream."
            ),
            "acceptable_alternative": (
                "The source supports describing DNS and HTTP as application-layer "
                "protocols around the TCP connection."
            ),
            "trade_off": (
                "Persistent connections avoid repeated setup but consume sockets, "
                "memory, buffers, and server state."
            ),
            "failure_mode": "Best-effort IP packets may be lost, reordered, or duplicated.",
            "misconception": (
                "TCP is the transport layer; DNS and HTTP define application message "
                "meaning."
            ),
        },
        "recall_questions": [
            {
                "level": "definition_recognition",
                "question": (
                    "What distinguishes IP, TCP, and DNS or HTTP in the webpage flow?"
                ),
            },
            {
                "level": "mechanism",
                "question": "How does TCP provide a reliable ordered byte stream?",
            },
            {
                "level": "derivation",
                "question": (
                    "Why does creating a new TCP connection for each request add latency?"
                ),
            },
            {
                "level": "application",
                "question": (
                    "How does the stated protocol sequence load a webpage from a hostname?"
                ),
            },
            {
                "level": "failure_tradeoff",
                "question": (
                    "What does a persistent connection save, and what server resources "
                    "does it consume?"
                ),
            },
        ],
    }
    return {**base, **overrides}


def adversarial_networking_concept() -> dict:
    base = networking_concept()
    return networking_concept(
        answer_rubric={
            **base["answer_rubric"],
            "failure_mode": (
                "TCP waits for a retransmission timeout when an acknowledgement is "
                "lost, then resends corrupted data."
            ),
        },
        recall_questions=[
            *base["recall_questions"][:3],
            {
                "level": "application",
                "question": (
                    "How would TCP recover corrupted data during a live video call?"
                ),
            },
            base["recall_questions"][4],
        ],
    )


def lesson_proposal(
    source: MaterialSource, *, position: int, topic: str, status: str = "clean"
) -> MaterialTopicProposal:
    concept = lesson_concept(GUIDE, topic=topic)
    return MaterialTopicProposal(
        source_id=source.id,
        position=position,
        section_title=concept["section_title"],
        topic=concept["topic"],
        answer_anchor=concept["answer_basis"],
        source_excerpt=concept["source_excerpt"],
        canonical_question=concept["canonical_question"],
        answer_rubric=concept["answer_rubric"],
        recall_questions=concept["recall_questions"],
        status=status,
    )


async def _claim(db, source: MaterialSource) -> uuid.UUID:
    run_id = uuid.uuid4()
    source.status = "processing"
    source.processing_run_id = run_id
    source.processing_heartbeat_at = datetime.now(UTC)
    db.add(source)
    await db.commit()
    return run_id


async def test_import_saves_the_complete_source_before_background_work(client, db, monkeypatch):
    calls = []

    async def no_work(source_id):
        calls.append(source_id)

    monkeypatch.setattr(materials, "process_import", no_work)
    response = await client.post(
        "/materials/imports",
        headers=API_HEADERS,
        json={
            "title": "A 16-week law guide",
            "source_text": GUIDE,
            "original_filename": "law.md",
            "mime_type": "text/markdown",
            "import_path": "topics",
            "intent": "already_studied",
            "requested_weeks": 16,
            "weekly_capacity_minutes": 480,
        },
    )
    assert response.status_code == 202
    source = (await db.exec(select(MaterialSource))).one()
    assert source.source_text == GUIDE
    assert source.status == SOURCE_PENDING
    assert source.requested_weeks == 16
    assert calls == [source.id]


async def test_lesson_import_persists_safe_url_provenance_and_source_type(
    client, db, monkeypatch
):
    calls: list[uuid.UUID] = []

    async def no_work(source_id):
        calls.append(source_id)

    monkeypatch.setattr(materials, "process_import", no_work)
    response = await client.post(
        "/materials/imports",
        headers=API_HEADERS,
        json={
            "title": "Request path notes",
            "source_text": GUIDE,
            "source_url": "https://example.com/engineering/request-path?view=full",
            "kind": "article",
            "content_provenance": "learner_notes",
            "import_path": "lesson",
            "intent": "learn",
        },
    )

    assert response.status_code == 202, response.text
    assert response.json()["source_url"] == (
        "https://example.com/engineering/request-path?view=full"
    )
    assert response.json()["kind"] == "article"
    assert response.json()["content_provenance"] == "learner_notes"
    source = (await db.exec(select(MaterialSource))).one()
    assert source.import_path == "lesson"
    assert source.kind == "article"
    assert source.content_provenance == "learner_notes"
    assert source.source_text == GUIDE
    assert calls == [source.id]


@pytest.mark.parametrize(
    "source_url",
    [
        "example.com/no-scheme",
        "ftp://example.com/source",
        "https://reader:secret@example.com/source",
    ],
)
async def test_lesson_import_rejects_unsafe_source_provenance(
    client, source_url
):
    response = await client.post(
        "/materials/imports",
        headers=API_HEADERS,
        json={
            "title": "Request path notes",
            "source_text": GUIDE,
            "source_url": source_url,
            "kind": "article",
            "import_path": "lesson",
        },
    )

    assert response.status_code == 422
    assert "absolute http(s) URL without credentials" in response.text


async def test_lesson_url_is_metadata_and_never_replaces_pasted_text(client):
    response = await client.post(
        "/materials/imports",
        headers=API_HEADERS,
        json={
            "title": "URL only",
            "source_url": "https://example.com/source",
            "kind": "article",
            "import_path": "lesson",
        },
    )

    assert response.status_code == 422
    assert "provenance metadata only" in response.text


async def test_lesson_extraction_stores_one_complete_concept_pack(
    db, monkeypatch
):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Request path notes",
        source_text=GUIDE,
        source_url="https://example.com/request-path",
        content_provenance="exact_source_excerpt",
        kind="article",
        import_path="lesson",
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()

    async def extract(**_kwargs):
        return [lesson_concept(GUIDE)]

    monkeypatch.setattr(llm, "extract_lesson", extract)
    verifier_calls = stub_lesson_verifier(monkeypatch)
    processed = await materials._process_lesson(db, source, await _claim(db, source))
    await db.flush()

    proposal = (await db.exec(select(MaterialTopicProposal))).one()
    assert processed.status == SOURCE_READY
    assert processed.result_summary["concept_count"] == 1
    assert processed.result_summary["grounding_gate_version"] == (
        materials.LESSON_GROUNDING_GATE_VERSION
    )
    assert len(verifier_calls) == 1
    assert proposal.canonical_question.startswith("How would you trace")
    assert proposal.answer_rubric == LESSON_RUBRIC
    assert [prompt["level"] for prompt in proposal.recall_questions] == list(
        llm.LESSON_RECALL_LEVELS
    )
    assert proposal.source_excerpt in source.source_text
    assert not (await db.exec(select(Card))).all()


async def test_invalid_lesson_extraction_writes_no_proposals_or_cards(
    db, monkeypatch
):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Request path notes",
        source_text=GUIDE,
        kind="article",
        import_path="lesson",
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()
    invalid = lesson_concept(GUIDE)
    invalid["recall_questions"][2] = {
        "level": "application",
        "question": "Is this useful?",
    }

    async def extract(**_kwargs):
        return [invalid]

    monkeypatch.setattr(llm, "extract_lesson", extract)
    with pytest.raises(llm.LLMError, match="invalid derivation recall"):
        await materials._process_lesson(db, source, await _claim(db, source))

    assert not (await db.exec(select(MaterialTopicProposal))).all()
    assert not (await db.exec(select(Card))).all()


def test_lesson_post_validation_owns_provider_unsupported_array_bounds():
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Request path notes",
        source_text=GUIDE,
        kind="article",
        import_path="lesson",
        status=SOURCE_PENDING,
    )

    with pytest.raises(llm.LLMError, match="between 1 and 7 concepts"):
        materials._validated_lesson_concepts(source, [])

    too_many = [
        lesson_concept(GUIDE, topic=f"Request routing path {index}")
        for index in range(8)
    ]
    with pytest.raises(llm.LLMError, match="between 1 and 7 concepts"):
        materials._validated_lesson_concepts(source, too_many)

    missing_prompt = lesson_concept(GUIDE)
    missing_prompt["recall_questions"] = missing_prompt["recall_questions"][:-1]
    with pytest.raises(llm.LLMError, match="exactly five recall prompts"):
        materials._validated_lesson_concepts(source, [missing_prompt])

    empty_section = lesson_concept(GUIDE, section_title="")
    with pytest.raises(llm.LLMError, match="empty section title"):
        materials._validated_lesson_concepts(source, [empty_section])


def test_networking_grounding_requires_a_complete_literal_evidence_matrix():
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Networking 101",
        source_text=NETWORKING_101_SOURCE,
        kind="article",
        import_path="lesson",
    )
    concepts = materials._validated_lesson_concepts(
        source, [networking_concept()]
    )
    findings = lesson_findings(concepts)
    review = materials._validated_lesson_grounding(
        source, concepts, findings
    )
    assert len(review) == len(llm.LESSON_GROUNDING_FIELDS)

    with pytest.raises(llm.LLMError, match="missing 1 required field verdict"):
        materials._validated_lesson_grounding(source, concepts, findings[:-1])

    duplicate = [*findings, findings[0]]
    with pytest.raises(llm.LLMError, match="unexpected or duplicated"):
        materials._validated_lesson_grounding(source, concepts, duplicate)

    non_literal = lesson_findings(concepts)
    non_literal[0]["evidence_spans"] = [
        "Each packet can take a different path through the network."
    ]
    with pytest.raises(llm.LLMError, match="non-literal span"):
        materials._validated_lesson_grounding(source, concepts, non_literal)

    passing_repair = lesson_findings(concepts)
    passing_repair[0]["repair"] = "A replacement that must not be trusted."
    with pytest.raises(llm.LLMError, match="repairs a passing field"):
        materials._validated_lesson_grounding(source, concepts, passing_repair)

    allowed_absence = lesson_findings(concepts)
    next(
        finding
        for finding in allowed_absence
        if finding["field"] == "answer_rubric.trade_off"
    )["verdict"] = "bounded_absence"
    materials._validated_lesson_grounding(source, concepts, allowed_absence)

    missing_mechanism = lesson_findings(concepts)
    next(
        finding
        for finding in missing_mechanism
        if finding["field"] == "answer_rubric.mechanism"
    )["verdict"] = "bounded_absence"
    with pytest.raises(llm.LLMError, match="required positive field"):
        materials._validated_lesson_grounding(
            source, concepts, missing_mechanism
        )


def test_networking_grounding_rejects_source_text_outside_the_concept_excerpt():
    excerpt = NETWORKING_101_SOURCE[:400]
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Networking 101",
        source_text=NETWORKING_101_SOURCE,
        kind="article",
        import_path="lesson",
    )
    concepts = materials._validated_lesson_concepts(
        source, [networking_concept(source_excerpt=excerpt)]
    )
    findings = lesson_findings(concepts)
    findings[0]["evidence_spans"] = [
        "Reusing persistent connections avoids repeated setup"
    ]

    with pytest.raises(llm.LLMError, match="non-literal span"):
        materials._validated_lesson_grounding(source, concepts, findings)


async def test_networking_unsupported_additions_are_review_only(
    client, db, monkeypatch
):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Networking 101",
        source_text=NETWORKING_101_SOURCE,
        content_provenance="exact_source_excerpt",
        kind="article",
        import_path="lesson",
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()
    concept = adversarial_networking_concept()

    async def extract(**_kwargs):
        return [concept]

    def unsupported(concepts):
        return lesson_findings(
            concepts,
            verdicts={
                (1, "answer_rubric.failure_mode"): "unsupported",
                (1, "recall_questions.application"): "unsupported",
            },
        )

    monkeypatch.setattr(llm, "extract_lesson", extract)
    verifier_calls = stub_lesson_verifier(monkeypatch, [unsupported])
    processed = await materials._process_lesson(
        db, source, await _claim(db, source)
    )
    db.add(processed)
    await db.commit()

    proposal = (await db.exec(select(MaterialTopicProposal))).one()
    assert len(verifier_calls) == 1
    assert processed.status == SOURCE_NEEDS_ATTENTION
    assert proposal.status == "needs_attention"
    assert "answer_rubric.failure_mode" in proposal.issue
    assert "recall_questions.application" in proposal.issue
    assert processed.result_summary["grounding_gate_version"] == (
        materials.LESSON_GROUNDING_GATE_VERSION
    )
    confirmed = await client.post(
        f"/materials/imports/{source.id}/confirm",
        headers=API_HEADERS,
        json={"selected_topic_ids": [str(proposal.id)]},
    )
    assert confirmed.status_code == 409
    assert not (await db.exec(select(Card))).all()


async def test_malformed_lesson_verification_writes_nothing(db, monkeypatch):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Networking 101",
        source_text=NETWORKING_101_SOURCE,
        kind="article",
        import_path="lesson",
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()

    async def extract(**_kwargs):
        return [networking_concept()]

    def incomplete(concepts):
        return lesson_findings(concepts)[:-1]

    monkeypatch.setattr(llm, "extract_lesson", extract)
    stub_lesson_verifier(monkeypatch, [incomplete])
    with pytest.raises(llm.LLMError, match="missing 1 required field verdict"):
        await materials._process_lesson(db, source, await _claim(db, source))

    assert not (await db.exec(select(MaterialTopicProposal))).all()
    assert not (await db.exec(select(Card))).all()


async def test_networking_repair_gets_one_independent_recheck(db, monkeypatch):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Networking 101",
        source_text=NETWORKING_101_SOURCE,
        kind="article",
        import_path="lesson",
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()

    async def extract(**_kwargs):
        return [adversarial_networking_concept()]

    def repairable(concepts):
        return lesson_findings(
            concepts,
            verdicts={
                (1, "answer_rubric.failure_mode"): "unsupported",
                (1, "recall_questions.application"): "unsupported",
            },
            repairs={
                (1, "answer_rubric.failure_mode"): (
                    "Best-effort IP packets may be lost, reordered, or duplicated."
                ),
                (1, "recall_questions.application"): (
                    "How does the stated protocol sequence load a webpage from a hostname?"
                ),
            },
        )

    authorizer_operations = []

    def authorizer(*_args, operation="guide_import", **_kwargs):
        authorizer_operations.append(operation)

        async def authorize(_attempt):
            return None

        return authorize

    monkeypatch.setattr(llm, "extract_lesson", extract)
    monkeypatch.setattr(materials, "_guide_authorizer", authorizer)
    verifier_calls = stub_lesson_verifier(monkeypatch, [repairable])
    processed = await materials._process_lesson(
        db, source, await _claim(db, source)
    )
    await db.flush()

    proposal = (await db.exec(select(MaterialTopicProposal))).one()
    assert len(verifier_calls) == 2
    assert authorizer_operations == [
        "guide_import",
        "lesson_grounding",
        "lesson_grounding_recheck",
    ]
    assert processed.status == SOURCE_READY
    assert proposal.status == "clean"
    assert proposal.answer_rubric["failure_mode"].startswith("Best-effort IP")
    assert "video call" not in proposal.recall_questions[3]["question"]


async def test_second_grounding_failure_never_gets_a_third_repair(db, monkeypatch):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Networking 101",
        source_text=NETWORKING_101_SOURCE,
        kind="article",
        import_path="lesson",
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()

    async def extract(**_kwargs):
        return [adversarial_networking_concept()]

    def first(concepts):
        return lesson_findings(
            concepts,
            verdicts={(1, "recall_questions.application"): "unsupported"},
            repairs={
                (1, "recall_questions.application"): (
                    "How does the stated protocol sequence load a webpage from a hostname?"
                )
            },
        )

    def second(concepts):
        return lesson_findings(
            concepts,
            verdicts={(1, "recall_questions.application"): "unsupported"},
            repairs={
                (1, "recall_questions.application"): (
                    "How does DNS resolve the hostname before the TCP connection?"
                )
            },
        )

    monkeypatch.setattr(llm, "extract_lesson", extract)
    verifier_calls = stub_lesson_verifier(monkeypatch, [first, second])
    processed = await materials._process_lesson(
        db, source, await _claim(db, source)
    )
    await db.flush()

    proposal = (await db.exec(select(MaterialTopicProposal))).one()
    assert len(verifier_calls) == 2
    assert processed.status == SOURCE_NEEDS_ATTENTION
    assert proposal.status == "needs_attention"
    assert "recall_questions.application" in proposal.issue
    assert "live video call" in proposal.recall_questions[3]["question"]
    assert "stated protocol sequence" not in proposal.recall_questions[3]["question"]


async def test_second_grounding_failure_restores_and_reports_every_original_rejection(
    db, monkeypatch
):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Networking 101",
        source_text=NETWORKING_101_SOURCE,
        kind="article",
        import_path="lesson",
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()

    async def extract(**_kwargs):
        return [adversarial_networking_concept()]

    def first(concepts):
        return lesson_findings(
            concepts,
            verdicts={(1, "answer_rubric.failure_mode"): "unsupported"},
            repairs={
                (1, "answer_rubric.failure_mode"): (
                    "Best-effort IP packets may be lost, reordered, or duplicated."
                )
            },
        )

    def second(concepts):
        return lesson_findings(
            concepts,
            verdicts={(1, "recall_questions.application"): "unsupported"},
        )

    monkeypatch.setattr(llm, "extract_lesson", extract)
    verifier_calls = stub_lesson_verifier(monkeypatch, [first, second])
    processed = await materials._process_lesson(
        db, source, await _claim(db, source)
    )
    await db.flush()

    proposal = (await db.exec(select(MaterialTopicProposal))).one()
    assert len(verifier_calls) == 2
    assert processed.status == SOURCE_NEEDS_ATTENTION
    assert "answer_rubric.failure_mode" in proposal.issue
    assert "recall_questions.application" in proposal.issue
    assert "retransmission timeout" in proposal.answer_rubric["failure_mode"]
    assert "live video call" in proposal.recall_questions[3]["question"]


async def test_incomplete_grounding_repair_stays_attention_without_recheck(
    db, monkeypatch
):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Networking 101",
        source_text=NETWORKING_101_SOURCE,
        kind="article",
        import_path="lesson",
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()

    async def extract(**_kwargs):
        return [adversarial_networking_concept()]

    def incomplete_repair(concepts):
        return lesson_findings(
            concepts,
            verdicts={
                (1, "answer_rubric.failure_mode"): "unsupported",
                (1, "recall_questions.application"): "unsupported",
            },
            repairs={
                (1, "answer_rubric.failure_mode"): (
                    "Best-effort IP packets may be lost, reordered, or duplicated."
                )
            },
        )

    monkeypatch.setattr(llm, "extract_lesson", extract)
    verifier_calls = stub_lesson_verifier(monkeypatch, [incomplete_repair])
    processed = await materials._process_lesson(
        db, source, await _claim(db, source)
    )
    await db.flush()

    proposal = (await db.exec(select(MaterialTopicProposal))).one()
    assert len(verifier_calls) == 1
    assert processed.status == SOURCE_NEEDS_ATTENTION
    assert "retransmission timeout" in proposal.answer_rubric["failure_mode"]


async def test_malformed_grounding_recheck_writes_nothing(db, monkeypatch):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Networking 101",
        source_text=NETWORKING_101_SOURCE,
        kind="article",
        import_path="lesson",
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()

    async def extract(**_kwargs):
        return [adversarial_networking_concept()]

    def repairable(concepts):
        return lesson_findings(
            concepts,
            verdicts={(1, "recall_questions.application"): "unsupported"},
            repairs={
                (1, "recall_questions.application"): (
                    "How does the stated protocol sequence load a webpage from a hostname?"
                )
            },
        )

    def malformed(concepts):
        return lesson_findings(concepts)[:-1]

    monkeypatch.setattr(llm, "extract_lesson", extract)
    verifier_calls = stub_lesson_verifier(
        monkeypatch, [repairable, malformed]
    )
    with pytest.raises(llm.LLMError, match="missing 1 required field verdict"):
        await materials._process_lesson(db, source, await _claim(db, source))

    assert len(verifier_calls) == 2
    assert not (await db.exec(select(MaterialTopicProposal))).all()
    assert not (await db.exec(select(Card))).all()


@pytest.mark.parametrize(
    "status",
    [
        SOURCE_PENDING,
        SOURCE_PROCESSING,
        SOURCE_READY,
        SOURCE_NEEDS_ATTENTION,
        SOURCE_CONFIRMED,
        SOURCE_SUPERSEDED,
    ],
)
async def test_retry_rejects_every_import_that_is_not_failed(
    client, db, monkeypatch, status
):
    calls: list[uuid.UUID] = []

    async def no_work(source_id):
        calls.append(source_id)

    monkeypatch.setattr(materials, "process_import", no_work)
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Guide",
        source_text=GUIDE,
        status=status,
    )
    db.add(source)
    await db.commit()

    before = await client.get(
        f"/materials/imports/{source.id}", headers=API_HEADERS
    )
    response = await client.post(
        f"/materials/imports/{source.id}/retry", headers=API_HEADERS
    )

    assert before.status_code == 200
    assert before.json()["lesson_grounding_required"] is False
    assert response.status_code == 409
    await db.refresh(source)
    assert source.status == status
    assert calls == []


@pytest.mark.parametrize("status", [SOURCE_READY, SOURCE_NEEDS_ATTENTION])
async def test_retry_requeues_a_pre_gate_lesson_without_discarding_its_source_or_preview(
    client, db, monkeypatch, status
):
    calls: list[uuid.UUID] = []

    async def no_work(source_id):
        calls.append(source_id)

    monkeypatch.setattr(materials, "process_import", no_work)
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Pre-gate Networking lesson",
        source_text=NETWORKING_101_SOURCE,
        import_path="lesson",
        status=status,
        result_summary={"workflow": "lesson", "concept_count": 1},
        error="old preview needs a current grounding check",
    )
    proposal = lesson_proposal(
        source, position=1, topic="Legacy unverified Networking concept"
    )
    db.add(source)
    db.add(proposal)
    await db.commit()

    before = await client.get(
        f"/materials/imports/{source.id}", headers=API_HEADERS
    )
    response = await client.post(
        f"/materials/imports/{source.id}/retry", headers=API_HEADERS
    )

    assert before.status_code == 200
    assert before.json()["lesson_grounding_required"] is True
    assert response.status_code == 202, response.text
    assert response.json()["status"] == SOURCE_PENDING
    assert response.json()["lesson_grounding_required"] is False
    await db.refresh(source)
    retained = await db.get(MaterialTopicProposal, proposal.id)
    assert source.status == SOURCE_PENDING
    assert source.source_text == NETWORKING_101_SOURCE
    assert source.result_summary == {
        "workflow": "lesson",
        "concept_count": 1,
        "lesson_grounding_recovery_required": True,
    }
    assert source.error == ""
    assert retained is not None
    assert retained.topic == "Legacy unverified Networking concept"
    assert calls == [source.id]


async def test_pre_gate_grounding_recovery_survives_a_failed_worker_and_retries(
    client, db, monkeypatch
):
    background_calls: list[uuid.UUID] = []
    real_process_import = materials.process_import

    async def no_work(source_id):
        background_calls.append(source_id)

    monkeypatch.setattr(materials, "process_import", no_work)
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Pre-gate recovery survives restart",
        source_text=NETWORKING_101_SOURCE,
        import_path="lesson",
        status=SOURCE_READY,
        result_summary={"workflow": "lesson", "concept_count": 1},
    )
    proposal = lesson_proposal(
        source, position=1, topic="Prior concept preview"
    )
    db.add(source)
    db.add(proposal)
    await db.commit()

    started = await client.post(
        f"/materials/imports/{source.id}/retry", headers=API_HEADERS
    )
    assert started.status_code == 202, started.text
    assert background_calls == [source.id]

    async def fail_extraction(**_kwargs):
        raise llm.LLMError("grounding provider unavailable")

    monkeypatch.setattr(llm, "extract_lesson", fail_extraction)
    factory = async_sessionmaker(
        db.bind, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(materials, "session_factory", factory)
    assert await real_process_import(source.id) is True
    await db.refresh(source)
    assert source.status == SOURCE_FAILED

    failed = await client.get(
        f"/materials/imports/{source.id}", headers=API_HEADERS
    )
    assert failed.status_code == 200
    assert failed.json()["status"] == SOURCE_FAILED
    assert failed.json()["lesson_grounding_required"] is True
    assert failed.json()["error"] == "grounding provider unavailable"
    assert [topic["id"] for topic in failed.json()["topics"]] == [str(proposal.id)]

    retried = await client.post(
        f"/materials/imports/{source.id}/retry", headers=API_HEADERS
    )
    assert retried.status_code == 202, retried.text
    assert retried.json()["status"] == SOURCE_PENDING
    assert retried.json()["lesson_grounding_required"] is False
    await db.refresh(source)
    assert source.result_summary["lesson_grounding_recovery_required"] is True
    assert await db.get(MaterialTopicProposal, proposal.id) is not None
    assert background_calls == [source.id, source.id]


@pytest.mark.parametrize("status", [SOURCE_READY, SOURCE_NEEDS_ATTENTION])
async def test_retry_rejects_a_lesson_that_already_passed_the_current_grounding_gate(
    client, db, monkeypatch, status
):
    calls: list[uuid.UUID] = []

    async def no_work(source_id):
        calls.append(source_id)

    monkeypatch.setattr(materials, "process_import", no_work)
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Current grounded lesson",
        source_text=NETWORKING_101_SOURCE,
        import_path="lesson",
        status=status,
        result_summary=grounded_summary(),
    )
    db.add(source)
    await db.commit()

    before = await client.get(
        f"/materials/imports/{source.id}", headers=API_HEADERS
    )
    response = await client.post(
        f"/materials/imports/{source.id}/retry", headers=API_HEADERS
    )

    assert before.status_code == 200
    assert before.json()["lesson_grounding_required"] is False
    assert response.status_code == 409
    await db.refresh(source)
    assert source.status == status
    assert calls == []


async def test_retry_atomically_requeues_only_a_failed_import(
    client, db, monkeypatch
):
    calls: list[uuid.UUID] = []

    async def no_work(source_id):
        calls.append(source_id)

    monkeypatch.setattr(materials, "process_import", no_work)
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Guide",
        source_text=GUIDE,
        status=SOURCE_FAILED,
        error="provider failed",
        processing_run_id=uuid.uuid4(),
        processing_heartbeat_at=datetime.now(UTC),
    )
    db.add(source)
    await db.commit()

    response = await client.post(
        f"/materials/imports/{source.id}/retry", headers=API_HEADERS
    )

    assert response.status_code == 202
    await db.refresh(source)
    assert source.status == SOURCE_PENDING
    assert source.error == ""
    assert source.processing_run_id is None
    assert source.processing_heartbeat_at is None
    assert calls == [source.id]


async def test_delete_removes_source_owned_plan_draft_and_raw_guide(client, db):
    draft = StudyPlanGuideDraft(
        user_id=FOUNDER_USER_ID,
        guide_text=GUIDE,
        requested_weeks=4,
        weekly_capacity_minutes=720,
        start_date=date(2026, 8, 17),
        raw_response={"verbatim": GUIDE},
    )
    db.add(draft)
    await db.flush()
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Delete every transient copy",
        source_text=GUIDE,
        import_path="plan",
        plan_draft_id=draft.id,
        status=SOURCE_FAILED,
    )
    db.add(source)
    await db.commit()

    response = await client.delete(
        f"/materials/imports/{source.id}", headers=API_HEADERS
    )

    assert response.status_code == 204
    assert await db.get(MaterialSource, source.id) is None
    assert await db.get(StudyPlanGuideDraft, draft.id) is None


async def test_postgres_concurrent_workers_transmit_one_physical_import(
    db, monkeypatch
):
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("atomic worker concurrency requires Postgres")

    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Concurrent guide",
        source_text=GUIDE,
        import_path="topics",
        requested_weeks=4,
        weekly_capacity_minutes=720,
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()

    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(materials, "session_factory", factory)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def blocked_import(**kwargs):
        nonlocal calls
        await kwargs["before_provider_call"](1)
        calls += 1
        entered.set()
        await release.wait()
        return import_payload()

    monkeypatch.setattr(llm, "import_guide", blocked_import)
    first = asyncio.create_task(materials.process_import(source.id))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        # The first worker is still at the provider; a duplicate task cannot
        # steal its fresh claim or make a second transmission.
        assert await asyncio.wait_for(materials.process_import(source.id), timeout=2) is False
        assert calls == 1
        release.set()
        assert await asyncio.wait_for(first, timeout=2) is True

        async with factory() as verify_db:
            current = await verify_db.get(MaterialSource, source.id)
            assert current is not None
            assert current.status in {SOURCE_READY, SOURCE_NEEDS_ATTENTION}
            assert current.processing_run_id is None
            assert current.processing_heartbeat_at is None
    finally:
        release.set()
        if not first.done():
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        await engine.dispose()


async def test_postgres_old_worker_discards_result_after_claim_token_changes(
    db, monkeypatch
):
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("claim takeover concurrency requires Postgres")

    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Claimed guide",
        source_text=GUIDE,
        import_path="topics",
        requested_weeks=4,
        weekly_capacity_minutes=720,
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()

    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(materials, "session_factory", factory)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_import(**kwargs):
        await kwargs["before_provider_call"](1)
        entered.set()
        await release.wait()
        return import_payload()

    monkeypatch.setattr(llm, "import_guide", blocked_import)
    old_worker = asyncio.create_task(materials.process_import(source.id))
    replacement_run_id = uuid.uuid4()
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        async with factory() as control_db:
            await control_db.exec(
                update(MaterialSource)
                .where(MaterialSource.id == source.id)
                .values(
                    processing_run_id=replacement_run_id,
                    processing_heartbeat_at=datetime.now(UTC),
                )
            )
            await control_db.commit()

        release.set()
        assert await asyncio.wait_for(old_worker, timeout=2) is True

        async with factory() as verify_db:
            current = await verify_db.get(MaterialSource, source.id)
            proposals = (await verify_db.exec(select(MaterialTopicProposal))).all()
            assert current is not None
            assert current.status == SOURCE_PROCESSING
            assert current.processing_run_id == replacement_run_id
            assert proposals == []
    finally:
        release.set()
        if not old_worker.done():
            old_worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await old_worker
        await engine.dispose()


async def test_postgres_delete_before_authorization_prevents_material_transmission(
    db, monkeypatch
):
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("provider-boundary deletion ordering requires Postgres")

    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Deleted before transmission",
        source_text=GUIDE,
        import_path="plan",
        requested_weeks=4,
        weekly_capacity_minutes=720,
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()

    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(materials, "session_factory", factory)
    boundary_entered = asyncio.Event()
    release_boundary = asyncio.Event()
    calls = 0
    real_ensure_available = usage.ensure_available

    async def paused_ensure_available(*args, **kwargs):
        boundary_entered.set()
        await release_boundary.wait()
        await real_ensure_available(*args, **kwargs)

    async def fake_import(**kwargs):
        nonlocal calls
        await kwargs["before_provider_call"](1)
        calls += 1
        return import_payload()

    monkeypatch.setattr(usage, "ensure_available", paused_ensure_available)
    monkeypatch.setattr(llm, "import_guide", fake_import)
    worker = asyncio.create_task(materials.process_import(source.id))
    try:
        await asyncio.wait_for(boundary_entered.wait(), timeout=3)
        async with factory() as delete_db:
            assert await materials.delete_source(
                delete_db,
                source_id=source.id,
                user_id=FOUNDER_USER_ID,
            )
        release_boundary.set()

        assert await asyncio.wait_for(worker, timeout=3) is True
        assert calls == 0
        async with factory() as verify_db:
            assert await verify_db.get(MaterialSource, source.id) is None
            assert (await verify_db.exec(select(StudyPlanGuideDraft))).all() == []
            assert (await verify_db.exec(select(MaterialTopicProposal))).all() == []
    finally:
        release_boundary.set()
        if not worker.done():
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker
        await engine.dispose()


async def test_postgres_authorization_before_delete_allows_only_inflight_call(
    db, monkeypatch
):
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("provider-boundary deletion ordering requires Postgres")

    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Deleted after authorization",
        source_text=GUIDE,
        import_path="plan",
        requested_weeks=4,
        weekly_capacity_minutes=720,
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()

    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(materials, "session_factory", factory)
    provider_entered = asyncio.Event()
    release_provider = asyncio.Event()
    calls = 0

    async def blocked_import(**kwargs):
        nonlocal calls
        await kwargs["before_provider_call"](1)
        calls += 1
        provider_entered.set()
        await release_provider.wait()
        return import_payload()

    monkeypatch.setattr(llm, "import_guide", blocked_import)
    worker = asyncio.create_task(materials.process_import(source.id))
    try:
        await asyncio.wait_for(provider_entered.wait(), timeout=3)
        async with factory() as delete_db:
            assert await materials.delete_source(
                delete_db,
                source_id=source.id,
                user_id=FOUNDER_USER_ID,
            )
        release_provider.set()

        assert await asyncio.wait_for(worker, timeout=3) is True
        assert calls == 1
        async with factory() as verify_db:
            assert await verify_db.get(MaterialSource, source.id) is None
            assert (await verify_db.exec(select(StudyPlanGuideDraft))).all() == []
            assert (await verify_db.exec(select(MaterialTopicProposal))).all() == []
    finally:
        release_provider.set()
        if not worker.done():
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker
        await engine.dispose()


async def test_postgres_heartbeat_loss_cancels_work_before_material_retry(
    db, monkeypatch
):
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("lease-loss cancellation requires Postgres")

    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Lease-monitored guide",
        source_text=GUIDE,
        import_path="topics",
        requested_weeks=4,
        weekly_capacity_minutes=720,
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(materials, "session_factory", factory)
    provider_entered = asyncio.Event()
    provider_cancelled = asyncio.Event()
    keep_provider_open = asyncio.Event()
    calls = 0
    active_calls = 0
    max_active_calls = 0

    async def import_with_first_call_blocked(**kwargs):
        nonlocal calls, active_calls, max_active_calls
        await kwargs["before_provider_call"](1)
        calls += 1
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        try:
            if calls == 1:
                provider_entered.set()
                try:
                    await keep_provider_open.wait()
                except asyncio.CancelledError:
                    provider_cancelled.set()
                    raise
            return import_payload()
        finally:
            active_calls -= 1

    async def failed_heartbeat(_source_id, _run_id):
        await provider_entered.wait()
        raise HTTPException(
            status_code=503, detail="material import lease could not be renewed"
        )

    monkeypatch.setattr(llm, "import_guide", import_with_first_call_blocked)
    monkeypatch.setattr(materials, "_heartbeat_import", failed_heartbeat)
    try:
        assert await asyncio.wait_for(materials.process_import(source.id), timeout=3)
        await asyncio.wait_for(provider_cancelled.wait(), timeout=3)
        async with factory() as control_db:
            failed = await control_db.get(MaterialSource, source.id)
            assert failed is not None
            assert failed.status == SOURCE_FAILED
            failed.status = SOURCE_PENDING
            failed.processing_run_id = None
            failed.processing_heartbeat_at = None
            control_db.add(failed)
            await control_db.commit()

        never = asyncio.Event()

        async def stable_heartbeat(_source_id, _run_id):
            await never.wait()

        monkeypatch.setattr(materials, "_heartbeat_import", stable_heartbeat)
        assert await asyncio.wait_for(materials.process_import(source.id), timeout=3)
        assert calls == 2
        assert max_active_calls == 1
    finally:
        keep_provider_open.set()
        await engine.dispose()


async def test_topics_are_proposals_until_the_user_confirms(client, db, stub_import):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Anatomy guide",
        source_text=GUIDE,
        import_path="topics",
        requested_weeks=4,
        weekly_capacity_minutes=720,
    )
    db.add(source)
    await db.commit()

    await materials._process_topics(db, source, await _claim(db, source))
    source.status = SOURCE_NEEDS_ATTENTION
    db.add(source)
    await db.commit()

    listing = (await client.get("/materials/imports", headers=API_HEADERS)).json()[0]
    detail = (await client.get(f"/materials/imports/{source.id}", headers=API_HEADERS)).json()
    assert listing["character_count"] == len(GUIDE)
    assert listing["clean_count"] == 4
    assert listing["attention_count"] == 1
    assert len(listing["topics"]) == 5
    assert len(detail["topics"]) == 5

    proposals = (
        await db.exec(
            select(MaterialTopicProposal)
            .where(MaterialTopicProposal.source_id == source.id)
            .order_by(MaterialTopicProposal.position)
        )
    ).all()
    assert len(proposals) == 5
    assert not (await db.exec(select(Card))).all()
    assert proposals[-1].status == "needs_attention"

    clean = [row.id for row in proposals if row.status == "clean"]
    confirmed = await client.post(
        f"/materials/imports/{source.id}/confirm",
        headers=API_HEADERS,
        json={"selected_topic_ids": [str(value) for value in clean]},
    )
    assert confirmed.status_code == 200, confirmed.text
    cards = (await db.exec(select(Card).order_by(Card.topic))).all()
    assert len(cards) == 4
    assert all(card.answer_anchor and card.source_id == source.id for card in cards)
    assert all(card.last_score is None for card in cards)


async def test_pre_gate_lesson_cannot_confirm_clean_legacy_proposals(client, db):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Pre-gate Networking lesson",
        source_text=NETWORKING_101_SOURCE,
        content_provenance="exact_source_excerpt",
        import_path="lesson",
        status=SOURCE_READY,
    )
    proposal = lesson_proposal(
        source, position=1, topic="Legacy unverified Networking concept"
    )
    db.add(source)
    db.add(proposal)
    await db.commit()

    response = await client.post(
        f"/materials/imports/{source.id}/confirm",
        headers=API_HEADERS,
        json={"selected_topic_ids": [str(proposal.id)]},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "lesson_grounding_required"
    assert not (await db.exec(select(Card))).all()


async def test_legacy_lesson_requires_explicit_content_provenance_before_confirmation(
    client, db, monkeypatch
):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Coached request-path correction",
        source_text=GUIDE,
        source_url="https://example.com/request-path",
        kind="notes",
        import_path="lesson",
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()

    async def extract(**_kwargs):
        return [lesson_concept(GUIDE)]

    monkeypatch.setattr(llm, "extract_lesson", extract)
    stub_lesson_verifier(monkeypatch)
    await materials._process_lesson(db, source, await _claim(db, source))
    proposal = (await db.exec(select(MaterialTopicProposal))).one()

    blocked = await client.post(
        f"/materials/imports/{source.id}/confirm",
        headers=API_HEADERS,
        json={"selected_topic_ids": [str(proposal.id)]},
    )

    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["code"] == "content_provenance_required"
    assert not (await db.exec(select(Card))).all()
    await db.refresh(source)
    assert source.content_provenance == "legacy_unspecified"

    confirmed = await client.post(
        f"/materials/imports/{source.id}/confirm",
        headers=API_HEADERS,
        json={
            "selected_topic_ids": [str(proposal.id)],
            "content_provenance": "coached_correction",
        },
    )

    assert confirmed.status_code == 200, confirmed.text
    await db.refresh(source)
    assert source.content_provenance == "coached_correction"
    assert len((await db.exec(select(Card))).all()) == 1


async def test_lesson_confirmation_progress_and_distillation_reuse_card_mastery(
    client, db, monkeypatch
):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Request path notes",
        source_text=GUIDE,
        source_url="https://example.com/request-path",
        content_provenance="exact_source_excerpt",
        kind="article",
        import_path="lesson",
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()

    async def extract(**_kwargs):
        return [lesson_concept(GUIDE)]

    monkeypatch.setattr(llm, "extract_lesson", extract)
    stub_lesson_verifier(monkeypatch)
    processed = await materials._process_lesson(db, source, await _claim(db, source))
    db.add(processed)
    await db.commit()
    proposal = (await db.exec(select(MaterialTopicProposal))).one()
    preview = await client.get(
        f"/materials/imports/{source.id}", headers=API_HEADERS
    )
    assert preview.status_code == 200
    assert preview.json()["topics"][0]["canonical_question"] == (
        proposal.canonical_question
    )
    assert preview.json()["topics"][0]["answer_rubric"] == LESSON_RUBRIC
    assert len(preview.json()["topics"][0]["recall_questions"]) == 5
    assert preview.json()["topics"][0]["card_id"] is None
    assert preview.json()["content_provenance"] == "exact_source_excerpt"

    confirmed = await client.post(
        f"/materials/imports/{source.id}/confirm",
        headers=API_HEADERS,
        json={"selected_topic_ids": [str(proposal.id)]},
    )
    assert confirmed.status_code == 200, confirmed.text
    card = (await db.exec(select(Card))).one()
    await db.refresh(proposal)
    assert proposal.card_id == card.id
    assert card.canonical_question == proposal.canonical_question
    assert card.answer_basis == proposal.answer_anchor
    assert card.answer_rubric == LESSON_RUBRIC
    assert card.source_url == source.source_url
    assert card.source_id == source.id

    progress = await client.get(
        f"/materials/imports/{source.id}/progress", headers=API_HEADERS
    )
    assert progress.status_code == 200, progress.text
    assert progress.json()["complete"] is False
    assert progress.json()["reviewed_count"] == 0
    assert progress.json()["next_card_id"] == str(card.id)

    incomplete = await client.post(
        f"/materials/imports/{source.id}/distill", headers=API_HEADERS
    )
    assert incomplete.status_code == 409
    assert incomplete.json()["detail"] == {
        "code": "lesson_incomplete",
        "reviewed_count": 0,
        "concept_count": 1,
    }

    reviewed_at = datetime.now(UTC)
    session = Session(
        card_id=card.id,
        question_asked=card.canonical_question or "",
        answer_text="private spoken answer that must never be exported",
        score=4,
        accuracy=4,
        depth=3,
        boundaries=3,
        feedback="Reconstructed the routing stages and their boundary cost.",
        follow_up_used=True,
        scoring_contract_version=2,
        status="complete",
        ended_at=reviewed_at,
    )
    card.last_score = 4
    card.last_accuracy = 4
    card.last_depth = 3
    card.last_boundaries = 3
    card.last_score_contract_version = 1
    card.last_reviewed_at = reviewed_at
    card.mastery_summary = "recalled the request routing mechanism unaided"
    db.add(session)
    db.add(card)
    await db.commit()

    progress = await client.get(
        f"/materials/imports/{source.id}/progress", headers=API_HEADERS
    )
    body = progress.json()
    assert body["complete"] is True
    assert body["reviewed_count"] == 1
    assert body["weak_count"] == 0
    assert body["next_card_id"] is None
    assert body["concepts"][0]["recall_score"] == 4
    assert body["concepts"][0]["mastery_summary"].startswith("recalled")

    distilled = await client.post(
        f"/materials/imports/{source.id}/distill", headers=API_HEADERS
    )
    assert distilled.status_code == 200, distilled.text
    artifact = distilled.json()
    assert artifact["source_url"] == source.source_url
    assert artifact["content_provenance"] == "exact_source_excerpt"
    assert GUIDE not in artifact["canonical_note_markdown"]
    assert "private spoken answer" not in distilled.text
    assert artifact["concepts"][0]["concept"] == proposal.topic
    assert artifact["concepts"][0]["mental_model"] == proposal.answer_anchor
    assert artifact["concepts"][0]["how_it_works"] == LESSON_RUBRIC["mechanism"]
    assert len(artifact["concepts"][0]["gotchas"]) == 4
    assert len(artifact["concepts"][0]["recall_prompts"]) == 5
    assert artifact["concepts"][0]["confidence"] == "established"
    assert artifact["concepts"][0]["quiz_results"][0]["recall_score"] == 4
    assert artifact["concepts"][0]["quiz_results"][0]["question"] == (
        card.canonical_question
    )
    bundle = artifact["writeback_bundle"]
    assert bundle["schema"] == "second-brain.learning-writeback"
    assert bundle["schema_version"] == 1
    assert bundle["producer"] == "devmax"
    assert bundle["source"]["id"] == f"devmax:source:{source.id}"
    assert bundle["source"]["lineage_id"] == (
        f"devmax:source-lineage:{source.lineage_id}"
    )
    assert bundle["source"]["version"] == source.version
    writeback_concept = bundle["concepts"][0]
    assert writeback_concept["id"] == f"devmax:proposal:{proposal.id}"
    assert writeback_concept["card_id"] == f"devmax:card:{card.id}"
    assert len(writeback_concept["answer_rubric"]) == 5
    assert len(writeback_concept["recall_candidates"]) == 5


    assert writeback_concept["quiz_evidence"] == [
        {
            "id": f"devmax:session:{session.id}",
            "reviewed_at": reviewed_at.isoformat().replace("+00:00", "Z"),
            "prompt": card.canonical_question,
            "score": 4,
            "graded_summary": session.feedback,
            "scoring_contract_version": 2,
            "scored_follow_up_used": True,
        }
    ]
    assert writeback_concept["producer_assessment"] == "established"
    serialized_bundle = json.dumps(bundle, sort_keys=True)
    for forbidden in (
        GUIDE,
        "private spoken answer",
        "answer_text",
        "source_text",
        "interval_days",
        "next_review_at",
        "mastery_summary",
        "canonical_question",
    ):
        assert forbidden not in serialized_bundle

    replayed = await client.post(
        f"/materials/imports/{source.id}/distill", headers=API_HEADERS
    )
    assert replayed.status_code == 200
    assert replayed.json()["distilled_at"] == artifact["distilled_at"]
    fetched = await client.get(
        f"/materials/imports/{source.id}/artifacts", headers=API_HEADERS
    )
    assert fetched.status_code == 200
    assert fetched.json() == replayed.json()

    await db.refresh(source)
    assert source.status == SOURCE_CONFIRMED
    assert source.canonical_note_markdown == artifact["canonical_note_markdown"]
    assert source.recall_export_markdown == artifact["recall_export_markdown"]


async def test_lesson_confirmation_requires_an_explicit_decision_for_every_concept(
    client, db
):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Two request-path concepts",
        source_text=GUIDE,
        content_provenance="learner_notes",
        import_path="lesson",
        status=SOURCE_READY,
        result_summary=grounded_summary(),
    )
    first = lesson_proposal(
        source, position=1, topic="Request routing boundaries"
    )
    second = lesson_proposal(
        source, position=2, topic="Request routing failure points"
    )
    db.add(source)
    db.add(first)
    db.add(second)
    await db.commit()

    partial = await client.post(
        f"/materials/imports/{source.id}/confirm",
        headers=API_HEADERS,
        json={"selected_topic_ids": [str(first.id)]},
    )

    assert partial.status_code == 409, partial.text
    assert partial.json()["detail"] == {
        "code": "lesson_decisions_incomplete",
        "unselected_clean_topic_ids": [str(second.id)],
        "needs_attention_topic_ids": [],
    }
    assert not (await db.exec(select(Card))).all()
    await db.refresh(first)
    await db.refresh(second)
    assert first.status == "clean"
    assert second.status == "clean"

    second.status = "excluded"
    db.add(second)
    await db.commit()
    confirmed = await client.post(
        f"/materials/imports/{source.id}/confirm",
        headers=API_HEADERS,
        json={"selected_topic_ids": [str(first.id)]},
    )

    assert confirmed.status_code == 200, confirmed.text
    assert len((await db.exec(select(Card))).all()) == 1


async def test_lesson_confirmation_rejects_any_concept_that_still_needs_attention(
    client, db
):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Request-path concepts needing review",
        source_text=GUIDE,
        content_provenance="learner_notes",
        import_path="lesson",
        status=SOURCE_NEEDS_ATTENTION,
        result_summary=grounded_summary(),
    )
    clean = lesson_proposal(source, position=1, topic="Request routing boundaries")
    attention = lesson_proposal(
        source,
        position=2,
        topic="Request routing failure points",
        status="needs_attention",
    )
    db.add(source)
    db.add(clean)
    db.add(attention)
    await db.commit()

    response = await client.post(
        f"/materials/imports/{source.id}/confirm",
        headers=API_HEADERS,
        json={"selected_topic_ids": [str(clean.id)]},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {
        "code": "lesson_decisions_incomplete",
        "unselected_clean_topic_ids": [],
        "needs_attention_topic_ids": [str(attention.id)],
    }
    assert not (await db.exec(select(Card))).all()


async def test_lesson_partial_edit_requires_new_grounding_before_confirmation(
    client, db
):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Editable request-path concept",
        source_text=GUIDE,
        content_provenance="learner_notes",
        import_path="lesson",
        status=SOURCE_READY,
        result_summary=grounded_summary(),
    )
    proposal = lesson_proposal(
        source, position=1, topic="Request routing boundaries"
    )
    original_question = proposal.canonical_question
    original_rubric = proposal.answer_rubric
    db.add(source)
    db.add(proposal)
    await db.commit()

    edited = await client.patch(
        f"/materials/topics/{proposal.id}",
        headers=API_HEADERS,
        json={
            "topic": proposal.topic,
            "answer_anchor": "A changed answer basis that has not been re-grounded.",
            "action": "keep",
        },
    )

    assert edited.status_code == 200, edited.text
    assert edited.json()["status"] == "needs_attention"
    assert "source-grounding check" in edited.json()["issue"]
    assert edited.json()["canonical_question"] == original_question
    assert edited.json()["answer_rubric"] == original_rubric

    confirmed = await client.post(
        f"/materials/imports/{source.id}/confirm",
        headers=API_HEADERS,
        json={"selected_topic_ids": [str(proposal.id)]},
    )

    assert confirmed.status_code == 409, confirmed.text
    assert not (await db.exec(select(Card))).all()


async def test_confirmed_lesson_proposal_cannot_be_edited_or_removed(client, db):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Confirmed request-path concept",
        source_text=GUIDE,
        content_provenance="learner_notes",
        import_path="lesson",
        status=SOURCE_READY,
        result_summary=grounded_summary(),
    )
    proposal = lesson_proposal(
        source, position=1, topic="Request routing boundaries"
    )
    db.add(source)
    db.add(proposal)
    await db.commit()
    confirmed = await client.post(
        f"/materials/imports/{source.id}/confirm",
        headers=API_HEADERS,
        json={"selected_topic_ids": [str(proposal.id)]},
    )
    assert confirmed.status_code == 200, confirmed.text

    edited = await client.patch(
        f"/materials/topics/{proposal.id}",
        headers=API_HEADERS,
        json={"action": "exclude"},
    )

    assert edited.status_code == 409, edited.text
    assert edited.json()["detail"] == {"code": "material_not_editable"}
    await db.refresh(proposal)
    assert proposal.status == "confirmed"
    assert proposal.card_id is not None
    assert len((await db.exec(select(Card))).all()) == 1


async def test_processing_source_cannot_confirm_stale_proposals(client, db):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Reprocessing request-path concept",
        source_text=GUIDE,
        content_provenance="learner_notes",
        import_path="lesson",
        status=SOURCE_PROCESSING,
    )
    proposal = lesson_proposal(
        source, position=1, topic="Request routing boundaries"
    )
    db.add(source)
    db.add(proposal)
    await db.commit()

    response = await client.post(
        f"/materials/imports/{source.id}/confirm",
        headers=API_HEADERS,
        json={"selected_topic_ids": [str(proposal.id)]},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {"code": "material_not_confirmable"}
    assert not (await db.exec(select(Card))).all()


async def test_plan_import_reuses_its_preview_for_review_topics(db, stub_import):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Anatomy study plan",
        source_text=GUIDE,
        import_path="plan",
        requested_weeks=4,
        weekly_capacity_minutes=720,
    )
    db.add(source)
    await db.commit()

    await materials._process_plan(db, source, await _claim(db, source))
    await db.commit()

    proposals = (
        await db.exec(
            select(MaterialTopicProposal)
            .where(MaterialTopicProposal.source_id == source.id)
            .order_by(MaterialTopicProposal.position)
        )
    ).all()
    assert stub_import.calls == 1
    assert len(proposals) == 5
    assert source.result_summary["clean_count"] == 4
    assert source.result_summary["attention_count"] == 1
    assert not (await db.exec(select(Card))).all()


async def test_attention_proposal_cannot_be_confirmed(client, db, stub_import):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Study guide",
        source_text=GUIDE,
        import_path="topics",
        requested_weeks=4,
        weekly_capacity_minutes=720,
    )
    db.add(source)
    await db.commit()
    await materials._process_topics(db, source, await _claim(db, source))
    await db.commit()
    attention = (
        await db.exec(
            select(MaterialTopicProposal).where(
                MaterialTopicProposal.source_id == source.id,
                MaterialTopicProposal.status == "needs_attention",
            )
        )
    ).one()

    response = await client.post(
        f"/materials/imports/{source.id}/confirm",
        headers=API_HEADERS,
        json={"selected_topic_ids": [str(attention.id)]},
    )
    assert response.status_code == 409
    assert not (await db.exec(select(Card))).all()


async def test_manual_topic_requires_and_stores_a_trusted_answer_anchor(client, db):
    missing = await client.post(
        "/materials/manual",
        headers=API_HEADERS,
        json={"title": "Law", "topics": [{"topic": "Consideration", "answer_anchor": ""}]},
    )
    assert missing.status_code == 422

    created = await client.post(
        "/materials/manual",
        headers=API_HEADERS,
        json={
            "title": "Law",
            "topics": [
                {
                    "topic": "Consideration",
                    "answer_anchor": "A bargained-for exchange of legal value is required.",
                }
            ],
        },
    )
    assert created.status_code == 201
    card = (await db.exec(select(Card))).one()
    assert card.answer_anchor == "A bargained-for exchange of legal value is required."
    assert card.canonical_question is None


async def test_reviewed_collection_is_versioned_and_grounded(client, db):
    listing = await client.get("/materials/collections", headers=API_HEADERS)
    assert listing.status_code == 200
    assert listing.json()[0]["version"] == "1.0"

    added = await client.post(
        "/materials/collections/system-design-foundations", headers=API_HEADERS
    )
    assert added.status_code == 201
    cards = (await db.exec(select(Card))).all()
    assert len(cards) == 6
    assert all(card.answer_anchor for card in cards)


async def test_failed_extraction_does_not_mutate_the_saved_source_or_create_cards(db, stub_import):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Medicine guide",
        source_text=GUIDE,
        import_path="topics",
        requested_weeks=4,
        weekly_capacity_minutes=720,
    )
    db.add(source)
    await db.commit()
    stub_import.error = llm.LLMError("provider unavailable")

    try:
        await materials._process_topics(db, source, await _claim(db, source))
    except llm.LLMError:
        pass
    else:
        raise AssertionError("the provider failure should reach the durable job wrapper")

    assert source.source_text == GUIDE
    assert not (await db.exec(select(Card))).all()
    assert not (await db.exec(select(MaterialTopicProposal))).all()


async def test_material_lookup_is_scoped_to_its_owner(client, db):
    from app.config import get_settings
    from app.models import Settings, User
    from app.services import authentication

    other = User()
    db.add(other)
    await db.flush()
    db.add(Settings(user_id=other.id))
    pair = await authentication.issue_session(db, other.id, get_settings())
    source = MaterialSource(
        user_id=other.id,
        title="Private anatomy source",
        source_text=GUIDE,
        import_path="topics",
        updated_at=datetime.now(UTC),
    )
    db.add(source)
    await db.commit()

    assert (
        await client.get(f"/materials/imports/{source.id}", headers=API_HEADERS)
    ).status_code == 404
    own = await client.get(
        f"/materials/imports/{source.id}",
        headers={"Authorization": f"Bearer {pair.access_token}"},
    )
    assert own.status_code == 200


async def test_a_new_source_version_reports_impact_without_changing_existing_cards(db, stub_import):
    previous = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Guide",
        source_text=GUIDE,
        import_path="topics",
        status="confirmed",
    )
    db.add(previous)
    await db.flush()
    db.add(
        MaterialTopicProposal(
            source_id=previous.id,
            position=1,
            topic="Item L1",
            answer_anchor="The old anchor.",
            status="confirmed",
        )
    )
    db.add(
        MaterialTopicProposal(
            source_id=previous.id,
            position=2,
            topic="Removed topic",
            answer_anchor="Only in version one.",
            status="confirmed",
        )
    )
    existing = make_card(
        topic="Existing review",
        category="Imported guide",
        source_id=previous.id,
        answer_anchor="Keep this history intact.",
        last_score=4,
        repetitions=3,
        interval_days=12,
    )
    db.add(existing)
    current = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Guide",
        source_text=GUIDE,
        import_path="topics",
        requested_weeks=4,
        weekly_capacity_minutes=720,
        previous_version_id=previous.id,
        lineage_id=previous.lineage_id,
        version=2,
    )
    db.add(current)
    await db.commit()

    before = (existing.last_score, existing.repetitions, existing.interval_days)
    await materials._process_topics(db, current, await _claim(db, current))
    await db.commit()
    await db.refresh(existing)

    assert current.result_summary["comparison"] == {
        "added": 4,
        "changed": 1,
        "removed": 1,
        "unchanged": 0,
    }
    assert (existing.last_score, existing.repetitions, existing.interval_days) == before
    assert previous.status == "confirmed"


async def test_merge_target_must_belong_to_the_same_source(client, db):
    first = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="First",
        source_text=GUIDE,
        import_path="topics",
        status=SOURCE_READY,
    )
    second = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Second",
        source_text=GUIDE,
        import_path="topics",
        status=SOURCE_READY,
    )
    db.add(first)
    db.add(second)
    await db.flush()
    source_topic = MaterialTopicProposal(
        source_id=first.id, position=1, topic="One", answer_anchor="One anchor."
    )
    foreign_target = MaterialTopicProposal(
        source_id=second.id, position=1, topic="Two", answer_anchor="Two anchor."
    )
    db.add(source_topic)
    db.add(foreign_target)
    await db.commit()

    response = await client.patch(
        f"/materials/topics/{source_topic.id}",
        headers=API_HEADERS,
        json={"action": "merge", "merge_into_id": str(foreign_target.id)},
    )
    assert response.status_code == 404
    await db.refresh(source_topic)
    assert source_topic.status == "clean"
    assert source_topic.merged_into_id is None
