"""Destructive migration checks isolated to a disposable local Postgres database.

The normal Postgres test suite expects an already-migrated database and truncates
it. Migration downgrade tests cannot safely use that database, so this module
creates and force-drops a uniquely named sibling database instead. It never
connects when ``TEST_DATABASE_URL`` is not an explicit loopback asyncpg URL.
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import TEST_DATABASE_URL

API_ROOT = Path(__file__).resolve().parents[1]
FOUNDER_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
DATABASE_NAME_PATTERN = re.compile(r"devmax_migration_[0-9a-f]{32}\Z")


def _local_postgres_url() -> URL:
    try:
        url = make_url(TEST_DATABASE_URL)
    except Exception:
        pytest.skip("migration round trips require a valid TEST_DATABASE_URL")

    if url.drivername != "postgresql+asyncpg":
        pytest.skip(
            "migration round trips require an explicit postgresql+asyncpg TEST_DATABASE_URL"
        )
    if url.host not in LOOPBACK_HOSTS:
        pytest.skip(
            "migration round trips refuse non-loopback TEST_DATABASE_URL values before connecting"
        )
    if not url.database:
        pytest.skip("migration round trips require an explicit database name")
    return url


def _render_url(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _engine(url: URL, *, autocommit: bool = False) -> AsyncEngine:
    kwargs: dict[str, object] = {
        "poolclass": NullPool,
        "connect_args": {"statement_cache_size": 0},
    }
    if autocommit:
        kwargs["isolation_level"] = "AUTOCOMMIT"
    return create_async_engine(_render_url(url), **kwargs)


async def _execute(url: URL, statement: str, parameters: dict | None = None) -> None:
    engine = _engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(statement), parameters or {})
    finally:
        await engine.dispose()


async def _fetch_one(url: URL, statement: str, parameters: dict | None = None) -> dict:
    engine = _engine(url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text(statement), parameters or {})
            row = result.mappings().one()
            return dict(row)
    finally:
        await engine.dispose()


async def _column_exists(url: URL, table: str, column: str) -> bool:
    row = await _fetch_one(
        url,
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = :table
              AND column_name = :column
        ) AS present
        """,
        {"table": table, "column": column},
    )
    return bool(row["present"])


async def _table_exists(url: URL, table: str) -> bool:
    row = await _fetch_one(
        url,
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = :table
        ) AS present
        """,
        {"table": table},
    )
    return bool(row["present"])


async def _index_exists(url: URL, index: str) -> bool:
    row = await _fetch_one(
        url,
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE schemaname = 'public' AND indexname = :index
        ) AS present
        """,
        {"index": index},
    )
    return bool(row["present"])


@asynccontextmanager
async def _temporary_database(server_url: URL) -> AsyncIterator[URL]:
    database_name = f"devmax_migration_{uuid.uuid4().hex}"
    assert DATABASE_NAME_PATTERN.fullmatch(database_name)

    admin_url = server_url.set(database="postgres")
    admin_engine = _engine(admin_url, autocommit=True)
    created = False
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        yield server_url.set(database=database_name)
    finally:
        if created:
            async with admin_engine.connect() as connection:
                await connection.execute(
                    text(f'DROP DATABASE "{database_name}" WITH (FORCE)')
                )
        await admin_engine.dispose()


def _run_alembic(database_url: URL, command: str, revision: str) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": _render_url(database_url),
            "API_KEY": "migration-round-trip-api-key-000000000000",
            "CRON_SECRET": "migration-round-trip-cron-secret-00000000",
            "FOUNDER_CLAIM_TOKEN": "",
            "OPENAI_V2_SCORING_MODE": "off",
            "SCORING_CONTRACT_VERSION": "1",
        }
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", command, revision],
        cwd=API_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


async def _seed_scored_history(
    database_url: URL, card_id: uuid.UUID, session_id: uuid.UUID, usage_id: uuid.UUID
) -> None:
    engine = _engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO cards (
                        id, user_id, topic, category, delivery_mode,
                        ease_factor, interval_days, repetitions, next_review_at,
                        last_score, last_accuracy, last_depth, last_boundaries,
                        mastery_summary, created_at, updated_at
                    ) VALUES (
                        :card_id, :user_id, 'Migration preservation', 'Systems',
                        'conversational', 2.37, 17, 4, DATE '2026-09-03',
                        4, 4, 3, 2, 'Preserve this state', now(), now()
                    )
                    """
                ),
                {"card_id": card_id, "user_id": FOUNDER_USER_ID},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO sessions (
                        id, card_id, question_asked, answer_text,
                        score, accuracy, depth, boundaries,
                        feedback, status, started_at, ended_at
                    ) VALUES (
                        :session_id, :card_id, 'What must survive?', 'All numeric state.',
                        4, 4, 3, 2, 'Preserved', 'complete', now(), now()
                    )
                    """
                ),
                {"session_id": session_id, "card_id": card_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO llm_usage (id, user_id, operation, created_at)
                    VALUES (:usage_id, :user_id, 'score_v1', now())
                    """
                ),
                {"usage_id": usage_id, "user_id": FOUNDER_USER_ID},
            )
    finally:
        await engine.dispose()


async def _numeric_snapshot(
    database_url: URL, card_id: uuid.UUID, session_id: uuid.UUID
) -> dict:
    return await _fetch_one(
        database_url,
        """
        SELECT
            s.score,
            s.accuracy,
            s.depth,
            s.boundaries,
            c.last_score,
            c.last_accuracy,
            c.last_depth,
            c.last_boundaries,
            c.ease_factor,
            c.interval_days,
            c.repetitions,
            c.next_review_at
        FROM sessions AS s
        JOIN cards AS c ON c.id = s.card_id
        WHERE s.id = :session_id AND c.id = :card_id
        """,
        {"session_id": session_id, "card_id": card_id},
    )


async def _seed_material_source(database_url: URL, source_id: uuid.UUID) -> None:
    await _execute(
        database_url,
        """
        INSERT INTO material_sources (
            id, user_id, lineage_id, title, source_text, status,
            created_at, updated_at
        ) VALUES (
            :source_id, :user_id, :source_id, 'Migration guide',
            'Keep this verbatim guide.', 'processing', now(), now()
        )
        """,
        {"source_id": source_id, "user_id": FOUNDER_USER_ID},
    )


async def _material_snapshot(database_url: URL, source_id: uuid.UUID) -> dict:
    return await _fetch_one(
        database_url,
        """
        SELECT title, source_text, status, result_summary, error
        FROM material_sources
        WHERE id = :source_id
        """,
        {"source_id": source_id},
    )


async def _seed_material_proposal(
    database_url: URL, proposal_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    await _execute(
        database_url,
        """
        INSERT INTO material_topic_proposals (
            id, source_id, position, section_title, topic, answer_anchor,
            source_excerpt, status, issue, created_at, updated_at
        ) VALUES (
            :proposal_id, :source_id, 1, 'Request path', 'Request routing',
            'DNS routes before storage.', 'Keep this verbatim guide.',
            'confirmed', '', now(), now()
        )
        """,
        {"proposal_id": proposal_id, "source_id": source_id},
    )


async def _seed_guide_draft(database_url: URL, draft_id: uuid.UUID) -> None:
    await _execute(
        database_url,
        """
        INSERT INTO study_plan_guide_drafts (
            id, user_id, guide_text, requested_weeks,
            weekly_capacity_minutes, mode, start_date, status,
            preview, raw_response, checks, error, created_at, updated_at
        ) VALUES (
            :draft_id, :user_id, 'Keep this preview guide.', 6,
            480, 'flexible', DATE '2026-08-17', 'failed',
            '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, 'retryable', now(), now()
        )
        """,
        {"draft_id": draft_id, "user_id": FOUNDER_USER_ID},
    )


async def _guide_draft_snapshot(database_url: URL, draft_id: uuid.UUID) -> dict:
    return await _fetch_one(
        database_url,
        """
        SELECT guide_text, requested_weeks, weekly_capacity_minutes,
               mode, start_date, status, preview, raw_response, checks, error
        FROM study_plan_guide_drafts
        WHERE id = :draft_id
        """,
        {"draft_id": draft_id},
    )


async def _assert_revision(database_url: URL, expected: str) -> None:
    row = await _fetch_one(database_url, "SELECT version_num FROM alembic_version")
    assert row["version_num"] == expected


async def _exercise_round_trips() -> None:
    server_url = _local_postgres_url()
    async with _temporary_database(server_url) as database_url:
        card_id = uuid.uuid4()
        session_id = uuid.uuid4()
        usage_id = uuid.uuid4()

        _run_alembic(database_url, "upgrade", "0010")
        await _seed_scored_history(database_url, card_id, session_id, usage_id)
        original = await _numeric_snapshot(database_url, card_id, session_id)

        # 0011 promises compatibility-only metadata. Exercise both directions so
        # a future downgrade cannot silently rewrite V1 scores or the schedule.
        _run_alembic(database_url, "upgrade", "0011")
        await _assert_revision(database_url, "0011")
        assert await _numeric_snapshot(database_url, card_id, session_id) == original
        compatibility = await _fetch_one(
            database_url,
            """
            SELECT s.scoring_contract_version, c.last_score_contract_version
            FROM sessions AS s
            JOIN cards AS c ON c.id = s.card_id
            WHERE s.id = :session_id AND c.id = :card_id
            """,
            {"session_id": session_id, "card_id": card_id},
        )
        assert compatibility == {
            "scoring_contract_version": 1,
            "last_score_contract_version": 1,
        }

        _run_alembic(database_url, "downgrade", "0010")
        await _assert_revision(database_url, "0010")
        assert await _numeric_snapshot(database_url, card_id, session_id) == original
        assert not await _column_exists(
            database_url, "sessions", "scoring_contract_version"
        )
        assert not await _column_exists(
            database_url, "cards", "last_score_contract_version"
        )

        _run_alembic(database_url, "upgrade", "0011")
        await _assert_revision(database_url, "0011")
        assert await _numeric_snapshot(database_url, card_id, session_id) == original

        _run_alembic(database_url, "upgrade", "0015")
        await _assert_revision(database_url, "0015")
        assert await _numeric_snapshot(database_url, card_id, session_id) == original
        assert not await _column_exists(database_url, "sessions", "scoring_route")
        assert not await _column_exists(database_url, "llm_usage", "details")

        # Existing rows receive empty objects on the first 0016 upgrade.
        _run_alembic(database_url, "upgrade", "0016")
        await _assert_revision(database_url, "0016")
        assert await _numeric_snapshot(database_url, card_id, session_id) == original
        metadata = await _fetch_one(
            database_url,
            """
            SELECT s.scoring_route, u.details
            FROM sessions AS s
            JOIN llm_usage AS u ON u.id = :usage_id
            WHERE s.id = :session_id
            """,
            {"session_id": session_id, "usage_id": usage_id},
        )
        assert metadata == {"scoring_route": {}, "details": {}}

        route = {
            "contract_version": 2,
            "mode": "primary",
            "model": "gpt-qualified",
            "provider": "openai",
        }
        audit = {
            "outcome": "success",
            "provider": "openai",
            "route": "primary",
            "shadow": False,
        }
        await _execute(
            database_url,
            """
            UPDATE sessions
            SET scoring_route = CAST(:route AS jsonb)
            WHERE id = :session_id
            """,
            {"route": json.dumps(route), "session_id": session_id},
        )
        await _execute(
            database_url,
            """
            UPDATE llm_usage
            SET details = CAST(:audit AS jsonb)
            WHERE id = :usage_id
            """,
            {"audit": json.dumps(audit), "usage_id": usage_id},
        )
        written = await _fetch_one(
            database_url,
            """
            SELECT s.scoring_route, u.details
            FROM sessions AS s
            JOIN llm_usage AS u ON u.id = :usage_id
            WHERE s.id = :session_id
            """,
            {"session_id": session_id, "usage_id": usage_id},
        )
        assert written == {"scoring_route": route, "details": audit}

        # 0015 cannot retain 0016-only metadata. The downgrade must preserve all
        # numeric/scheduler state; re-upgrade must safely restore empty defaults.
        _run_alembic(database_url, "downgrade", "0015")
        await _assert_revision(database_url, "0015")
        assert await _numeric_snapshot(database_url, card_id, session_id) == original
        assert not await _column_exists(database_url, "sessions", "scoring_route")
        assert not await _column_exists(database_url, "llm_usage", "details")

        _run_alembic(database_url, "upgrade", "0016")
        await _assert_revision(database_url, "0016")
        assert await _numeric_snapshot(database_url, card_id, session_id) == original
        reset = await _fetch_one(
            database_url,
            """
            SELECT s.scoring_route, u.details, u.operation
            FROM sessions AS s
            JOIN llm_usage AS u ON u.id = :usage_id
            WHERE s.id = :session_id
            """,
            {"session_id": session_id, "usage_id": usage_id},
        )
        assert reset == {
            "scoring_route": {},
            "details": {},
            "operation": "score_v1",
        }

        # 0017 adds only nullable worker-claim metadata. Existing material work
        # and failed Study Plan previews must survive both directions, with a
        # null claim on each first upgrade.
        source_id = uuid.uuid4()
        draft_id = uuid.uuid4()
        await _seed_material_source(database_url, source_id)
        await _seed_guide_draft(database_url, draft_id)
        material = await _material_snapshot(database_url, source_id)
        draft = await _guide_draft_snapshot(database_url, draft_id)
        assert not await _column_exists(
            database_url, "material_sources", "processing_run_id"
        )
        assert not await _column_exists(
            database_url, "material_sources", "processing_heartbeat_at"
        )
        assert not await _column_exists(
            database_url, "study_plan_guide_drafts", "processing_run_id"
        )
        assert not await _column_exists(
            database_url,
            "study_plan_guide_drafts",
            "processing_heartbeat_at",
        )

        _run_alembic(database_url, "upgrade", "0017")
        await _assert_revision(database_url, "0017")
        assert await _material_snapshot(database_url, source_id) == material
        claim = await _fetch_one(
            database_url,
            """
            SELECT processing_run_id, processing_heartbeat_at
            FROM material_sources
            WHERE id = :source_id
            """,
            {"source_id": source_id},
        )
        assert claim == {
            "processing_run_id": None,
            "processing_heartbeat_at": None,
        }
        assert await _guide_draft_snapshot(database_url, draft_id) == draft
        draft_claim = await _fetch_one(
            database_url,
            """
            SELECT processing_run_id, processing_heartbeat_at
            FROM study_plan_guide_drafts
            WHERE id = :draft_id
            """,
            {"draft_id": draft_id},
        )
        assert draft_claim == {
            "processing_run_id": None,
            "processing_heartbeat_at": None,
        }

        _run_alembic(database_url, "downgrade", "0016")
        await _assert_revision(database_url, "0016")
        assert await _material_snapshot(database_url, source_id) == material
        assert not await _column_exists(
            database_url, "material_sources", "processing_run_id"
        )
        assert await _guide_draft_snapshot(database_url, draft_id) == draft
        assert not await _column_exists(
            database_url, "study_plan_guide_drafts", "processing_run_id"
        )

        _run_alembic(database_url, "upgrade", "0017")
        await _assert_revision(database_url, "0017")
        assert await _material_snapshot(database_url, source_id) == material
        assert await _guide_draft_snapshot(database_url, draft_id) == draft

        # 0018 adds lesson provenance, distilled artifacts, complete proposal
        # grounding and one stable proposal -> Card edge. Existing material rows
        # receive empty/null defaults and keep every pre-existing value.
        proposal_id = uuid.uuid4()
        await _seed_material_proposal(database_url, proposal_id, source_id)
        _run_alembic(database_url, "upgrade", "0018")
        await _assert_revision(database_url, "0018")
        assert await _material_snapshot(database_url, source_id) == material
        lesson_defaults = await _fetch_one(
            database_url,
            """
            SELECT s.source_url, s.canonical_note_markdown,
                   s.recall_export_markdown, s.distilled_at,
                   p.canonical_question, p.answer_rubric,
                   p.recall_questions, p.card_id
            FROM material_sources AS s
            JOIN material_topic_proposals AS p ON p.source_id = s.id
            WHERE s.id = :source_id AND p.id = :proposal_id
            """,
            {"source_id": source_id, "proposal_id": proposal_id},
        )
        assert lesson_defaults == {
            "source_url": "",
            "canonical_note_markdown": "",
            "recall_export_markdown": "",
            "distilled_at": None,
            "canonical_question": "",
            "answer_rubric": {},
            "recall_questions": [],
            "card_id": None,
        }

        rubric = {
            "mechanism": "Routes through named stages.",
            "acceptable_alternative": "Equivalent stage names.",
            "trade_off": "Boundaries add latency.",
            "failure_mode": "A stage can fail.",
            "misconception": "Storage is not contacted directly.",
        }
        prompts = [
            {"level": level, "question": f"How does {level} work here?"}
            for level in (
                "definition_recognition",
                "mechanism",
                "derivation",
                "application",
                "failure_tradeoff",
            )
        ]
        await _execute(
            database_url,
            """
            UPDATE material_sources
            SET import_path = 'lesson',
                source_url = 'https://example.com/request-path',
                canonical_note_markdown = '# Request routing',
                recall_export_markdown = '# Recall',
                distilled_at = now()
            WHERE id = :source_id
            """,
            {"source_id": source_id},
        )
        await _execute(
            database_url,
            """
            UPDATE material_topic_proposals
            SET canonical_question = 'How does routing work?',
                answer_rubric = CAST(:rubric AS jsonb),
                recall_questions = CAST(:prompts AS jsonb),
                card_id = :card_id
            WHERE id = :proposal_id
            """,
            {
                "proposal_id": proposal_id,
                "card_id": card_id,
                "rubric": json.dumps(rubric),
                "prompts": json.dumps(prompts),
            },
        )
        lesson_written = await _fetch_one(
            database_url,
            """
            SELECT s.import_path, s.source_url, s.canonical_note_markdown,
                   s.recall_export_markdown, s.distilled_at IS NOT NULL AS distilled,
                   p.canonical_question, p.answer_rubric,
                   p.recall_questions, p.card_id
            FROM material_sources AS s
            JOIN material_topic_proposals AS p ON p.source_id = s.id
            WHERE s.id = :source_id AND p.id = :proposal_id
            """,
            {"source_id": source_id, "proposal_id": proposal_id},
        )
        assert lesson_written == {
            "import_path": "lesson",
            "source_url": "https://example.com/request-path",
            "canonical_note_markdown": "# Request routing",
            "recall_export_markdown": "# Recall",
            "distilled": True,
            "canonical_question": "How does routing work?",
            "answer_rubric": rubric,
            "recall_questions": prompts,
            "card_id": card_id,
        }
        assert await _numeric_snapshot(database_url, card_id, session_id) == original

        # Downgrade retains the raw source/proposal and maps the unsupported lesson
        # path back to topics. Re-upgrade restores additive defaults without
        # inventing a stale card link or artifact.
        _run_alembic(database_url, "downgrade", "0017")
        await _assert_revision(database_url, "0017")
        assert await _material_snapshot(database_url, source_id) == material
        path = await _fetch_one(
            database_url,
            "SELECT import_path FROM material_sources WHERE id = :source_id",
            {"source_id": source_id},
        )
        assert path == {"import_path": "topics"}
        assert not await _column_exists(database_url, "material_sources", "source_url")
        assert not await _column_exists(
            database_url, "material_topic_proposals", "card_id"
        )
        assert await _numeric_snapshot(database_url, card_id, session_id) == original

        _run_alembic(database_url, "upgrade", "0018")
        await _assert_revision(database_url, "0018")
        reset_lesson = await _fetch_one(
            database_url,
            """
            SELECT s.import_path, s.source_url, s.canonical_note_markdown,
                   s.recall_export_markdown, s.distilled_at,
                   p.canonical_question, p.answer_rubric,
                   p.recall_questions, p.card_id
            FROM material_sources AS s
            JOIN material_topic_proposals AS p ON p.source_id = s.id
            WHERE s.id = :source_id AND p.id = :proposal_id
            """,
            {"source_id": source_id, "proposal_id": proposal_id},
        )
        assert reset_lesson == {
            "import_path": "topics",
            "source_url": "",
            "canonical_note_markdown": "",
            "recall_export_markdown": "",
            "distilled_at": None,
            "canonical_question": "",
            "answer_rubric": {},
            "recall_questions": [],
            "card_id": None,
        }
        assert await _numeric_snapshot(database_url, card_id, session_id) == original

        # 0019 never guesses the origin of existing content. The learner can
        # classify it after upgrade, while downgrade removes only that metadata.
        _run_alembic(database_url, "upgrade", "0019")
        await _assert_revision(database_url, "0019")
        provenance = await _fetch_one(
            database_url,
            "SELECT content_provenance FROM material_sources WHERE id = :source_id",
            {"source_id": source_id},
        )
        assert provenance == {"content_provenance": "legacy_unspecified"}

        # 0020 adds only proposal-owned, unscored pilot state. Existing source,
        # proposal, numeric history, and scheduler fields survive both directions.
        _run_alembic(database_url, "upgrade", "0020")
        await _assert_revision(database_url, "0020")
        assert await _numeric_snapshot(database_url, card_id, session_id) == original
        pilot_defaults = await _fetch_one(
            database_url,
            """
            SELECT s.proposals_ready_at, s.review_opened_at, s.confirmed_at,
                   p.last_learning_exposure_at, p.recall_not_before_at
            FROM material_sources AS s
            JOIN material_topic_proposals AS p ON p.source_id = s.id
            WHERE s.id = :source_id AND p.id = :proposal_id
            """,
            {"source_id": source_id, "proposal_id": proposal_id},
        )
        assert pilot_defaults == {
            "proposals_ready_at": None,
            "review_opened_at": None,
            "confirmed_at": None,
            "last_learning_exposure_at": None,
            "recall_not_before_at": None,
        }
        for table in (
            "lesson_checks",
            "lesson_proposal_audits",
            "study_pilot_enrollments",
            "study_pilot_assignments",
        ):
            assert await _table_exists(database_url, table)

        enrollment_id = uuid.uuid4()
        assignment_id = uuid.uuid4()
        check_id = uuid.uuid4()
        audit_id = uuid.uuid4()
        await _execute(
            database_url,
            """
            UPDATE material_sources
            SET proposals_ready_at = now(), review_opened_at = now(),
                confirmed_at = now()
            WHERE id = :source_id
            """,
            {"source_id": source_id},
        )
        await _execute(
            database_url,
            """
            UPDATE material_topic_proposals
            SET last_learning_exposure_at = now(),
                recall_not_before_at = now() + INTERVAL '8 hours'
            WHERE id = :proposal_id
            """,
            {"proposal_id": proposal_id},
        )
        await _execute(
            database_url,
            """
            INSERT INTO study_pilot_enrollments (
                id, user_id, cohort, consent_version, consented_at,
                randomization_seed, created_at, updated_at
            ) VALUES (
                :enrollment_id, :user_id, 'pilot-2026-08', 'pilot-consent-v1',
                now(), 'migration-seed', now(), now()
            )
            """,
            {"enrollment_id": enrollment_id, "user_id": FOUNDER_USER_ID},
        )
        await _execute(
            database_url,
            """
            INSERT INTO study_pilot_assignments (
                id, enrollment_id, source_lineage_id, source_id,
                pair_index, sequence_index, condition, intended_target,
                target_proposal_id, version_snapshot, assigned_at, bound_at,
                updated_at
            ) VALUES (
                :assignment_id, :enrollment_id, :source_id, :source_id,
                1, 1, 'attempt_first', 'position:1', :proposal_id,
                '{"formation_prompt":"v1","model":"frozen"}'::jsonb,
                now(), now(), now()
            )
            """,
            {
                "assignment_id": assignment_id,
                "enrollment_id": enrollment_id,
                "source_id": source_id,
                "proposal_id": proposal_id,
            },
        )
        await _execute(
            database_url,
            """
            INSERT INTO lesson_proposal_audits (
                id, source_id, proposal_id, extraction_route,
                extraction_prompt_version, grounding_gate_version,
                original_proposal_pack, original_grounding_findings,
                reviewer_id, reviewer_decision, reviewer_correction,
                reviewed_at, created_at
            ) VALUES (
                :audit_id, :source_id, :proposal_id,
                '{"provider":"anthropic","model":"frozen"}'::jsonb,
                'extract-v1', 'grounding-v1', '{"topic":"Request routing"}'::jsonb,
                '[]'::jsonb, 'reviewer-1', 'approved', '{}'::jsonb, now(), now()
            )
            """,
            {
                "audit_id": audit_id,
                "source_id": source_id,
                "proposal_id": proposal_id,
            },
        )
        with pytest.raises(IntegrityError):
            await _execute(
                database_url,
                """
                UPDATE study_pilot_assignments
                SET intended_target = 'Private topic must not persist'
                WHERE id = :assignment_id
                """,
                {"assignment_id": assignment_id},
            )
        await _execute(
            database_url,
            """
            INSERT INTO lesson_checks (
                id, user_id, proposal_id, kind, condition, prompt_level,
                prompt_version, provider_route, prompt_text_snapshot,
                prompt_rubric_version, status, answer_text,
                qualitative_outcome, feedback, exposed_at,
                recall_not_before_at, started_at, submitted_at, updated_at
            ) VALUES (
                :check_id, :user_id, :proposal_id, 'formation', 'attempt_first',
                'canonical', 'formation-v1',
                '{"provider":"anthropic","model":"frozen"}'::jsonb,
                'How does routing work?', 'formation-rubric-v1', 'exposed',
                'It passes through named stages.', 'accurate_account',
                'Accurate source-backed account.', now(),
                now() + INTERVAL '8 hours', now() - INTERVAL '1 minute',
                now(), now()
            )
            """,
            {
                "check_id": check_id,
                "user_id": FOUNDER_USER_ID,
                "proposal_id": proposal_id,
            },
        )
        stored_pilot = await _fetch_one(
            database_url,
            """
            SELECT
              (SELECT count(*) FROM lesson_checks) AS checks,
              (SELECT count(*) FROM lesson_proposal_audits) AS audits,
              (SELECT count(*) FROM study_pilot_enrollments) AS enrollments,
              (SELECT count(*) FROM study_pilot_assignments) AS assignments
            """,
        )
        assert stored_pilot == {
            "checks": 1,
            "audits": 1,
            "enrollments": 1,
            "assignments": 1,
        }
        # 0021 separates successful authorization from notification ordering.
        # The overloaded historical event timestamp is deliberately not trusted
        # as an authorization boundary during the upgrade.
        identity_id = uuid.uuid4()
        await _execute(
            database_url,
            """
            INSERT INTO apple_identities (
                id, user_id, subject, last_apple_event_at, created_at, updated_at
            ) VALUES (
                :identity_id, :user_id, 'migration-apple-subject',
                TIMESTAMPTZ '2026-08-15 12:00:00+00', now(), now()
            )
            """,
            {"identity_id": identity_id, "user_id": FOUNDER_USER_ID},
        )
        _run_alembic(database_url, "upgrade", "0021")
        await _assert_revision(database_url, "0021")
        for index_name in (
            "uq_apple_notification_receipts_jti",
            "ix_apple_notification_receipts_identity_created",
            "ix_apple_notification_receipts_created",
            "ix_auth_nonces_expires",
            "ix_auth_nonces_used",
            "ix_auth_sessions_refresh_expires",
        ):
            assert await _index_exists(database_url, index_name)
        boundary = await _fetch_one(
            database_url,
            """
            SELECT last_apple_event_at, last_apple_authorized_at
            FROM apple_identities WHERE id = :identity_id
            """,
            {"identity_id": identity_id},
        )
        assert boundary["last_apple_event_at"] is not None
        assert boundary["last_apple_authorized_at"] is None
        receipt_id = uuid.uuid4()
        await _execute(
            database_url,
            """
            INSERT INTO apple_notification_receipts (
                id, identity_id, jti, event_type, occurred_at, applied, created_at
            ) VALUES (
                :receipt_id, :identity_id, 'migration-notification-jti',
                'consent-revoked', now(), true, now()
            )
            """,
            {"receipt_id": receipt_id, "identity_id": identity_id},
        )
        receipt = await _fetch_one(
            database_url,
            """
            SELECT jti, event_type, applied
            FROM apple_notification_receipts WHERE id = :receipt_id
            """,
            {"receipt_id": receipt_id},
        )
        assert receipt == {
            "jti": "migration-notification-jti",
            "event_type": "consent-revoked",
            "applied": True,
        }

        # 0022 repairs any historical duplicate-live race before installing the
        # portable live-session and one-to-one lineage backstops.
        live_session_ids = (uuid.uuid4(), uuid.uuid4())
        await _execute(
            database_url,
            """
            INSERT INTO sessions (id, card_id, question_asked, status, started_at)
            VALUES
                (:first_id, :card_id, 'Older inaccessible live row', 'open', now()),
                (:second_id, :card_id, 'Newest resumable live row', 'open', now())
            """,
            {
                "first_id": live_session_ids[0],
                "second_id": live_session_ids[1],
                "card_id": card_id,
            },
        )
        _run_alembic(database_url, "upgrade", "0022")
        await _assert_revision(database_url, "0022")
        for index_name in (
            "uq_sessions_live_card",
            "uq_cards_replaces_card",
            "uq_cards_replaced_by_card",
        ):
            assert await _index_exists(database_url, index_name)
        repaired = await _fetch_one(
            database_url,
            """
            SELECT count(*) FILTER (WHERE status = 'open') AS live_count,
                   count(*) FILTER (WHERE status = 'abandoned') AS abandoned_count
            FROM sessions WHERE id IN (:first_id, :second_id)
            """,
            {
                "first_id": live_session_ids[0],
                "second_id": live_session_ids[1],
            },
        )
        assert repaired == {"live_count": 1, "abandoned_count": 1}
        _run_alembic(database_url, "downgrade", "0021")
        await _assert_revision(database_url, "0021")
        assert not await _index_exists(database_url, "uq_sessions_live_card")
        _run_alembic(database_url, "upgrade", "0022")
        await _assert_revision(database_url, "0022")
        assert await _numeric_snapshot(database_url, card_id, session_id) == original

        _run_alembic(database_url, "downgrade", "0019")
        await _assert_revision(database_url, "0019")
        for table in (
            "lesson_checks",
            "lesson_proposal_audits",
            "study_pilot_enrollments",
            "study_pilot_assignments",
        ):
            assert not await _table_exists(database_url, table)
        assert not await _column_exists(
            database_url, "material_sources", "proposals_ready_at"
        )
        assert not await _column_exists(
            database_url,
            "material_topic_proposals",
            "last_learning_exposure_at",
        )
        assert await _material_snapshot(database_url, source_id) == material
        assert not await _table_exists(database_url, "apple_notification_receipts")
        assert not await _column_exists(
            database_url, "apple_identities", "last_apple_authorized_at"
        )
        for index_name in (
            "ix_auth_nonces_expires",
            "ix_auth_nonces_used",
            "ix_auth_sessions_refresh_expires",
        ):
            assert not await _index_exists(database_url, index_name)
        assert await _numeric_snapshot(database_url, card_id, session_id) == original

        _run_alembic(database_url, "upgrade", "0022")
        await _assert_revision(database_url, "0022")
        reset_pilot = await _fetch_one(
            database_url,
            """
            SELECT s.proposals_ready_at, s.review_opened_at, s.confirmed_at,
                   p.last_learning_exposure_at, p.recall_not_before_at
            FROM material_sources AS s
            JOIN material_topic_proposals AS p ON p.source_id = s.id
            WHERE s.id = :source_id AND p.id = :proposal_id
            """,
            {"source_id": source_id, "proposal_id": proposal_id},
        )
        assert reset_pilot == pilot_defaults
        assert await _table_exists(database_url, "apple_notification_receipts")
        assert await _column_exists(
            database_url, "apple_identities", "last_apple_authorized_at"
        )
        assert await _numeric_snapshot(database_url, card_id, session_id) == original
        await _execute(
            database_url,
            """
            UPDATE material_sources
            SET content_provenance = 'coached_correction'
            WHERE id = :source_id
            """,
            {"source_id": source_id},
        )
        _run_alembic(database_url, "downgrade", "0018")
        await _assert_revision(database_url, "0018")
        assert not await _column_exists(
            database_url, "material_sources", "content_provenance"
        )
        assert await _numeric_snapshot(database_url, card_id, session_id) == original

        _run_alembic(database_url, "upgrade", "0019")
        await _assert_revision(database_url, "0019")
        provenance = await _fetch_one(
            database_url,
            "SELECT content_provenance FROM material_sources WHERE id = :source_id",
            {"source_id": source_id},
        )
        assert provenance == {"content_provenance": "legacy_unspecified"}


def test_scoring_migrations_preserve_numeric_and_scheduler_state() -> None:
    asyncio.run(_exercise_round_trips())
