"""Trusted operator mutations for the bounded adaptive-study pilot.

These functions are deliberately not HTTP endpoints. Enrollment, six-source
counterbalancing, blinded proposal review, and target binding are concierge
operations performed from an access-controlled shell. The participant client
cannot choose a condition or rewrite the frozen assignment.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.models import (
    CONTENT_PROVENANCE_LEGACY_UNSPECIFIED,
    LESSON_CHECK_FORMATION,
    LESSON_CHECK_OPEN,
    LESSON_CHECK_TRANSFER,
    LESSON_CONDITION_ATTEMPT_FIRST,
    LESSON_CONDITION_RESTUDY,
    PROPOSAL_AUDIT_APPROVED,
    PROPOSAL_AUDIT_BLOCKED,
    PROPOSAL_AUDIT_CORRECTED,
    PROPOSAL_AUDIT_PENDING,
    PROPOSAL_CLEAN,
    PROPOSAL_EXCLUDED,
    PROPOSAL_NEEDS_ATTENTION,
    SOURCE_DRAFT,
    SOURCE_NEEDS_ATTENTION,
    SOURCE_PENDING,
    SOURCE_READY,
    STATUS_COMPLETE,
    LessonCheck,
    LessonProposalAudit,
    MaterialSource,
    MaterialTopicProposal,
    Session,
    StudyPilotAssignment,
    StudyPilotEnrollment,
    User,
)
from app.pilot_contract import (
    PILOT_ASSIGNMENT_ALGORITHM_VERSION,
    PILOT_CONSENT_FUTURE_SKEW,
    PILOT_MINIMUM_CLIENT_BUILD,
    PILOT_RESEARCH_CONSENT_CATALOG,
    RESTUDY_PROMPT_VERSION,
    TRANSFER_PROMPT_RUBRIC_VERSION,
    TRANSFER_PROMPT_VERSION,
)
from app.schemas import MaterialImportIn
from app.services import llm, materials

ASSIGNMENT_COUNT = 6
CONDITIONS = frozenset(
    {LESSON_CONDITION_ATTEMPT_FIRST, LESSON_CONDITION_RESTUDY}
)
REVIEW_DECISIONS = frozenset(
    {PROPOSAL_AUDIT_APPROVED, PROPOSAL_AUDIT_CORRECTED, PROPOSAL_AUDIT_BLOCKED}
)
ASSIGNABLE_SOURCE_STATUSES = frozenset({SOURCE_DRAFT, SOURCE_PENDING})
REVIEWABLE_SOURCE_STATUSES = frozenset({SOURCE_READY, SOURCE_NEEDS_ATTENTION})
FROZEN_CONTRACT_KEYS = (
    "assignment_algorithm_version",
    "extraction_provider_route",
    "extraction_prompt_version",
    "grounding_gate_version",
    "formation_prompt_version",
    "restudy_prompt_version",
    "transfer_prompt_version",
    "transfer_prompt_rubric_version",
    "minimum_client_build",
    "pilot_consent_version",
    "formation_provider_route",
)
FROZEN_VERSION_KEYS = (
    "assignment_algorithm_version",
    "extraction_prompt_version",
    "grounding_gate_version",
    "formation_prompt_version",
    "restudy_prompt_version",
    "transfer_prompt_version",
    "transfer_prompt_rubric_version",
)


class PilotOperatorError(ValueError):
    """The requested operator mutation would violate the frozen protocol."""


@dataclass(frozen=True)
class AssignmentInput:
    source_id: uuid.UUID
    pair_index: int
    sequence_index: int
    condition: str
    intended_target: str
    version_snapshot: dict[str, Any]


@dataclass(frozen=True)
class ProvisionedAssignmentInput:
    source_id: uuid.UUID
    source_lineage_id: uuid.UUID
    title: str
    source_text: str
    source_url: str
    content_provenance: str
    kind: str
    original_filename: str
    mime_type: str
    intent: str
    pair_index: int
    intended_target: str
    version_snapshot: dict[str, Any]


def _now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PilotOperatorError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive round trip as the UTC value we wrote."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _assignment_digest(randomization_seed: str, label: str) -> bytes:
    return hmac.new(
        randomization_seed.encode("utf-8"),
        f"{PILOT_ASSIGNMENT_ALGORITHM_VERSION}:{label}".encode(),
        hashlib.sha256,
    ).digest()


def derive_assignment_plan(
    randomization_seed: str,
    sources: list[tuple[uuid.UUID, uuid.UUID, int]],
) -> dict[uuid.UUID, tuple[int, str]]:
    """Derive sequence and paired condition without operator-chosen orientation."""

    if len(sources) != ASSIGNMENT_COUNT:
        raise PilotOperatorError("randomization requires exactly six sources")
    if len({source_id for source_id, _, _ in sources}) != ASSIGNMENT_COUNT:
        raise PilotOperatorError("randomization source ids must be unique")
    if len({lineage_id for _, lineage_id, _ in sources}) != ASSIGNMENT_COUNT:
        raise PilotOperatorError("randomization source lineages must be unique")
    by_pair: dict[int, list[tuple[uuid.UUID, uuid.UUID]]] = {}
    for source_id, lineage_id, pair_index in sources:
        if pair_index not in {1, 2, 3}:
            raise PilotOperatorError("pair indexes must be exactly 1 through 3")
        by_pair.setdefault(pair_index, []).append((source_id, lineage_id))
    if any(len(by_pair.get(pair_index, [])) != 2 for pair_index in range(1, 4)):
        raise PilotOperatorError("each of the three pairs must contain exactly two sources")

    ordered = sorted(
        sources,
        key=lambda item: (
            _assignment_digest(
                randomization_seed,
                f"sequence:{item[1].hex}",
            ),
            item[1].bytes,
        ),
    )
    sequence_by_source = {
        source_id: sequence_index
        for sequence_index, (source_id, _, _) in enumerate(ordered, 1)
    }
    condition_by_source: dict[uuid.UUID, str] = {}
    for pair_index in range(1, 4):
        pair = sorted(by_pair[pair_index], key=lambda item: item[1].bytes)
        orientation = _assignment_digest(
            randomization_seed,
            f"pair:{pair_index}:{pair[0][1].hex}:{pair[1][1].hex}",
        )[0] & 1
        attempt_source_id = pair[orientation][0]
        for source_id, _ in pair:
            condition_by_source[source_id] = (
                LESSON_CONDITION_ATTEMPT_FIRST
                if source_id == attempt_source_id
                else LESSON_CONDITION_RESTUDY
            )
    return {
        source_id: (sequence_by_source[source_id], condition_by_source[source_id])
        for source_id, _, _ in sources
    }


def _validate_randomized_assignments(
    enrollment: StudyPilotEnrollment,
    entries: list[AssignmentInput],
    lineage_by_source: dict[uuid.UUID, uuid.UUID],
) -> None:
    derived = derive_assignment_plan(
        enrollment.randomization_seed,
        [
            (entry.source_id, lineage_by_source[entry.source_id], entry.pair_index)
            for entry in entries
        ],
    )
    if any(
        (entry.sequence_index, entry.condition) != derived[entry.source_id]
        for entry in entries
    ):
        raise PilotOperatorError(
            "assignment condition and sequence must match the enrolled randomization seed"
        )


async def enroll_participant(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    cohort: str,
    consent_version: str,
    consented_at: datetime,
    randomization_seed: str,
) -> StudyPilotEnrollment:
    cohort = cohort.strip()
    consent_version = consent_version.strip()
    randomization_seed = randomization_seed.strip()
    consented_at = _aware_utc(consented_at, field="consented_at")
    if not cohort or not consent_version or not randomization_seed:
        raise PilotOperatorError(
            "cohort, consent_version, and randomization_seed must be non-empty"
        )
    if consent_version not in PILOT_RESEARCH_CONSENT_CATALOG:
        raise PilotOperatorError("pilot research consent version is not supported")
    if consented_at > _now() + PILOT_CONSENT_FUTURE_SKEW:
        raise PilotOperatorError("consented_at cannot be meaningfully in the future")
    user = (
        await db.exec(select(User).where(User.id == user_id).with_for_update())
    ).first()
    if user is None:
        raise PilotOperatorError("user not found")
    existing = (
        await db.exec(
            select(StudyPilotEnrollment)
            .where(
                StudyPilotEnrollment.user_id == user_id,
                StudyPilotEnrollment.withdrawn_at.is_(None),
            )
            .with_for_update()
        )
    ).first()
    if existing is not None:
        expected = (
            existing.cohort,
            existing.consent_version,
            _stored_utc(existing.consented_at),
            existing.randomization_seed,
        )
        requested = (cohort, consent_version, consented_at, randomization_seed)
        if expected != requested:
            raise PilotOperatorError("user already has a different active enrollment")
        return existing
    enrollment = StudyPilotEnrollment(
        user_id=user_id,
        cohort=cohort,
        consent_version=consent_version,
        consented_at=consented_at,
        randomization_seed=randomization_seed,
    )
    db.add(enrollment)
    await db.commit()
    await db.refresh(enrollment)
    return enrollment


def _validate_manifest_shape(entries: list[AssignmentInput]) -> None:
    if len(entries) != ASSIGNMENT_COUNT:
        raise PilotOperatorError("assignment manifest must contain exactly six sources")
    if {entry.sequence_index for entry in entries} != set(range(1, 7)):
        raise PilotOperatorError("assignment sequence indexes must be exactly 1 through 6")
    if len({entry.source_id for entry in entries}) != ASSIGNMENT_COUNT:
        raise PilotOperatorError("assignment sources must be unique")
    for entry in entries:
        target = entry.intended_target.strip()
        if target not in {"position:1", "position:2", "position:3"}:
            raise PilotOperatorError(
                "intended_target must be position:1, position:2, or position:3"
            )
        missing = [key for key in FROZEN_CONTRACT_KEYS if key not in entry.version_snapshot]
        if missing:
            raise PilotOperatorError(
                f"version snapshot is missing frozen keys: {', '.join(missing)}"
            )
        for key in FROZEN_VERSION_KEYS:
            value = entry.version_snapshot[key]
            if not isinstance(value, str) or not value.strip():
                raise PilotOperatorError(f"version snapshot {key} must be non-empty")
        minimum_build = entry.version_snapshot["minimum_client_build"]
        if not isinstance(minimum_build, int) or isinstance(minimum_build, bool):
            raise PilotOperatorError("version snapshot minimum_client_build must be an integer")
        if minimum_build <= 0:
            raise PilotOperatorError("version snapshot minimum_client_build must be positive")
        consent_version = entry.version_snapshot["pilot_consent_version"]
        if not isinstance(consent_version, str) or not consent_version.strip():
            raise PilotOperatorError("version snapshot pilot_consent_version must be non-empty")
        for route_key in ("extraction_provider_route", "formation_provider_route"):
            route = entry.version_snapshot[route_key]
            if not isinstance(route, dict):
                raise PilotOperatorError(f"{route_key} must be an object")
            if route.get("provider") != "anthropic":
                raise PilotOperatorError(f"{route_key} must freeze the Anthropic provider")
            for key in ("model", "effort"):
                if not isinstance(route.get(key), str) or not str(route[key]).strip():
                    raise PilotOperatorError(f"{route_key} {key} must be non-empty")
    first_contract = {
        key: entries[0].version_snapshot[key] for key in FROZEN_CONTRACT_KEYS
    }
    if any(
        {key: entry.version_snapshot[key] for key in FROZEN_CONTRACT_KEYS}
        != first_contract
        for entry in entries[1:]
    ):
        raise PilotOperatorError(
            "all six assignments must share one frozen contract snapshot"
        )
    for pair_index in range(1, 4):
        pair = [entry for entry in entries if entry.pair_index == pair_index]
        if len(pair) != 2 or {entry.condition for entry in pair} != CONDITIONS:
            raise PilotOperatorError(
                f"pair {pair_index} must contain one attempt_first and one restudy source"
            )
    if any(entry.pair_index not in {1, 2, 3} for entry in entries):
        raise PilotOperatorError("pair indexes must be exactly 1 through 3")


def _validate_snapshot_for_enrollment(
    enrollment: StudyPilotEnrollment, entries: list[AssignmentInput]
) -> None:
    if any(
        entry.version_snapshot["pilot_consent_version"] != enrollment.consent_version
        for entry in entries
    ):
        raise PilotOperatorError(
            "frozen pilot consent version must match the enrollment consent"
        )
    expected = runtime_contract_snapshot(enrollment.consent_version)
    for key, expected_value in expected.items():
        if any(entry.version_snapshot[key] != expected_value for entry in entries):
            raise PilotOperatorError(
                f"frozen {key} does not match the active pilot runtime contract"
            )


def runtime_contract_snapshot(consent_version: str) -> dict[str, Any]:
    """Return the safe contract metadata operators freeze into all six rows."""

    if consent_version not in PILOT_RESEARCH_CONSENT_CATALOG:
        raise PilotOperatorError("pilot research consent version is not supported")
    settings = get_settings()
    return {
        "assignment_algorithm_version": PILOT_ASSIGNMENT_ALGORITHM_VERSION,
        "extraction_provider_route": {
            "provider": "anthropic",
            "model": settings.card_proposal_model,
            "effort": settings.card_proposal_effort,
        },
        "extraction_prompt_version": llm.LESSON_EXTRACTION_PROMPT_VERSION,
        "grounding_gate_version": str(materials.LESSON_GROUNDING_GATE_VERSION),
        "formation_prompt_version": llm.LESSON_CHECK_PROMPT_VERSION,
        "restudy_prompt_version": RESTUDY_PROMPT_VERSION,
        "transfer_prompt_version": TRANSFER_PROMPT_VERSION,
        "transfer_prompt_rubric_version": TRANSFER_PROMPT_RUBRIC_VERSION,
        "minimum_client_build": PILOT_MINIMUM_CLIENT_BUILD,
        "pilot_consent_version": consent_version,
        "formation_provider_route": {
            "provider": "anthropic",
            "model": settings.scoring_model,
            "effort": settings.scoring_effort,
        },
    }


def _assignment_matches(row: StudyPilotAssignment, entry: AssignmentInput) -> bool:
    return (
        row.source_id == entry.source_id
        and row.pair_index == entry.pair_index
        and row.sequence_index == entry.sequence_index
        and row.condition == entry.condition
        and row.intended_target == entry.intended_target.strip()
        and row.version_snapshot == entry.version_snapshot
    )


async def assign_manifest(
    db: AsyncSession,
    *,
    enrollment_id: uuid.UUID,
    entries: list[AssignmentInput],
) -> list[StudyPilotAssignment]:
    """Atomically freeze the six source-level assignments before processing."""

    _validate_manifest_shape(entries)
    enrollment = (
        await db.exec(
            select(StudyPilotEnrollment)
            .where(StudyPilotEnrollment.id == enrollment_id)
            .with_for_update()
        )
    ).first()
    if enrollment is None or enrollment.withdrawn_at is not None:
        raise PilotOperatorError("active enrollment not found")
    _validate_snapshot_for_enrollment(enrollment, entries)
    existing = list(
        (
            await db.exec(
                select(StudyPilotAssignment)
                .where(StudyPilotAssignment.enrollment_id == enrollment_id)
                .order_by(StudyPilotAssignment.sequence_index)
                .with_for_update()
            )
        ).all()
    )
    ordered_entries = sorted(entries, key=lambda entry: entry.sequence_index)
    if existing:
        existing_lineages = {
            row.source_id: row.source_lineage_id
            for row in existing
            if row.source_id is not None
        }
        if set(existing_lineages) != {entry.source_id for entry in entries}:
            raise PilotOperatorError("frozen assignment sources no longer match")
        _validate_randomized_assignments(enrollment, entries, existing_lineages)
        if len(existing) != ASSIGNMENT_COUNT or any(
            not _assignment_matches(row, entry)
            for row, entry in zip(existing, ordered_entries, strict=True)
        ):
            raise PilotOperatorError("enrollment already has a different frozen manifest")
        return existing

    sources = list(
        (
            await db.exec(
                select(MaterialSource)
                .where(col(MaterialSource.id).in_([entry.source_id for entry in entries]))
                .with_for_update()
            )
        ).all()
    )
    by_id = {source.id: source for source in sources}
    if len(by_id) != ASSIGNMENT_COUNT:
        raise PilotOperatorError("one or more assignment sources were not found")
    if len({source.lineage_id for source in sources}) != ASSIGNMENT_COUNT:
        raise PilotOperatorError("the six assignments must use distinct source lineages")
    _validate_randomized_assignments(
        enrollment,
        entries,
        {source.id: source.lineage_id for source in sources},
    )
    for entry in entries:
        source = by_id[entry.source_id]
        if source.user_id != enrollment.user_id:
            raise PilotOperatorError("assignment source belongs to another account")
        if source.import_path != "lesson":
            raise PilotOperatorError("pilot assignments require focused lessons")
        if source.status not in ASSIGNABLE_SOURCE_STATUSES:
            raise PilotOperatorError(
                "assignments must be frozen before source processing begins"
            )
        if source.proposals_ready_at is not None:
            raise PilotOperatorError("assignments must precede proposal readiness")

    now = _now()
    rows = [
        StudyPilotAssignment(
            enrollment_id=enrollment.id,
            source_lineage_id=by_id[entry.source_id].lineage_id,
            source_id=entry.source_id,
            pair_index=entry.pair_index,
            sequence_index=entry.sequence_index,
            condition=entry.condition,
            intended_target=entry.intended_target.strip(),
            version_snapshot=dict(entry.version_snapshot),
            assigned_at=now,
            updated_at=now,
        )
        for entry in ordered_entries
    ]
    for row in rows:
        db.add(row)
    await db.commit()
    return rows


def _derived_provision_assignments(
    enrollment: StudyPilotEnrollment,
    entries: list[ProvisionedAssignmentInput],
) -> list[AssignmentInput]:
    derived = derive_assignment_plan(
        enrollment.randomization_seed,
        [
            (entry.source_id, entry.source_lineage_id, entry.pair_index)
            for entry in entries
        ],
    )
    return [
        AssignmentInput(
            source_id=entry.source_id,
            pair_index=entry.pair_index,
            sequence_index=derived[entry.source_id][0],
            condition=derived[entry.source_id][1],
            intended_target=entry.intended_target,
            version_snapshot=entry.version_snapshot,
        )
        for entry in entries
    ]


def _source_matches_manifest(
    source: MaterialSource, entry: ProvisionedAssignmentInput
) -> bool:
    return (
        source.id == entry.source_id
        and source.lineage_id == entry.source_lineage_id
        and source.version == 1
        and source.previous_version_id is None
        and source.title == entry.title.strip()
        and source.source_text == entry.source_text
        and source.source_url == entry.source_url.strip()
        and source.content_provenance == entry.content_provenance
        and source.kind == entry.kind
        and source.original_filename == entry.original_filename
        and source.mime_type == entry.mime_type
        and source.import_path == "lesson"
        and source.intent == entry.intent
    )


async def provision_manifest(
    db: AsyncSession,
    *,
    enrollment_id: uuid.UUID,
    entries: list[ProvisionedAssignmentInput],
) -> tuple[list[MaterialSource], list[StudyPilotAssignment]]:
    """Create six draft sources and freeze their assignments in one transaction."""

    if len(entries) != ASSIGNMENT_COUNT:
        raise PilotOperatorError("provision manifest must contain exactly six sources")
    if len({entry.source_id for entry in entries}) != ASSIGNMENT_COUNT:
        raise PilotOperatorError("provisioned source ids must be unique")
    if len({entry.source_lineage_id for entry in entries}) != ASSIGNMENT_COUNT:
        raise PilotOperatorError("provisioned sources must use six distinct lineages")
    validated_sources: dict[uuid.UUID, MaterialImportIn] = {}
    for manifest_index, entry in enumerate(entries, 1):
        try:
            validated = MaterialImportIn(
                title=entry.title,
                source_text=entry.source_text,
                source_url=entry.source_url,
                content_provenance=entry.content_provenance,
                kind=entry.kind,
                original_filename=entry.original_filename,
                mime_type=entry.mime_type,
                import_path="lesson",
                intent=entry.intent,
            )
        except Exception as exc:
            raise PilotOperatorError(
                f"source at manifest index {manifest_index} is invalid: {exc}"
            ) from exc
        if validated.content_provenance == CONTENT_PROVENANCE_LEGACY_UNSPECIFIED:
            raise PilotOperatorError("pilot sources require explicit content provenance")
        validated_sources[entry.source_id] = validated

    enrollment = (
        await db.exec(
            select(StudyPilotEnrollment)
            .where(StudyPilotEnrollment.id == enrollment_id)
            .with_for_update()
        )
    ).first()
    if enrollment is None or enrollment.withdrawn_at is not None:
        raise PilotOperatorError("active enrollment not found")
    assignment_entries = _derived_provision_assignments(enrollment, entries)
    _validate_manifest_shape(assignment_entries)
    _validate_snapshot_for_enrollment(enrollment, assignment_entries)
    existing_assignments = list(
        (
            await db.exec(
                select(StudyPilotAssignment)
                .where(StudyPilotAssignment.enrollment_id == enrollment_id)
                .order_by(StudyPilotAssignment.sequence_index)
                .with_for_update()
            )
        ).all()
    )
    existing_sources = list(
        (
            await db.exec(
                select(MaterialSource)
                .where(col(MaterialSource.id).in_([entry.source_id for entry in entries]))
                .with_for_update()
            )
        ).all()
    )
    assignment_by_source = {entry.source_id: entry for entry in assignment_entries}
    ordered_entries = sorted(
        entries,
        key=lambda entry: assignment_by_source[entry.source_id].sequence_index,
    )
    if existing_assignments or existing_sources:
        sources_by_id = {source.id: source for source in existing_sources}
        if (
            len(existing_assignments) != ASSIGNMENT_COUNT
            or len(sources_by_id) != ASSIGNMENT_COUNT
            or any(
                not _assignment_matches(row, assignment_entry)
                for row, assignment_entry in zip(
                    existing_assignments,
                    sorted(assignment_entries, key=lambda entry: entry.sequence_index),
                    strict=True,
                )
            )
            or any(
                not _source_matches_manifest(sources_by_id[entry.source_id], entry)
                for entry in ordered_entries
            )
        ):
            raise PilotOperatorError("provisioned manifest conflicts with existing rows")
        return (
            [sources_by_id[entry.source_id] for entry in ordered_entries],
            existing_assignments,
        )

    now = _now()
    sources = [
        MaterialSource(
            id=entry.source_id,
            user_id=enrollment.user_id,
            lineage_id=entry.source_lineage_id,
            version=1,
            kind=validated_sources[entry.source_id].kind,
            title=validated_sources[entry.source_id].title.strip(),
            source_text=validated_sources[entry.source_id].source_text,
            source_url=validated_sources[entry.source_id].source_url,
            content_provenance=validated_sources[entry.source_id].content_provenance,
            original_filename=validated_sources[entry.source_id].original_filename,
            mime_type=validated_sources[entry.source_id].mime_type,
            import_path="lesson",
            intent=validated_sources[entry.source_id].intent,
            status=SOURCE_DRAFT,
            created_at=now,
            updated_at=now,
        )
        for entry in ordered_entries
    ]
    for source in sources:
        db.add(source)
    await db.flush()
    assignments = [
        StudyPilotAssignment(
            enrollment_id=enrollment.id,
            source_lineage_id=entry.source_lineage_id,
            source_id=entry.source_id,
            pair_index=entry.pair_index,
            sequence_index=assignment_by_source[entry.source_id].sequence_index,
            condition=assignment_by_source[entry.source_id].condition,
            intended_target=entry.intended_target.strip(),
            version_snapshot=dict(entry.version_snapshot),
            assigned_at=now,
            updated_at=now,
        )
        for entry in ordered_entries
    ]
    for assignment in assignments:
        db.add(assignment)
    await db.commit()
    return sources, assignments


async def start_manifest_processing(
    db: AsyncSession,
    *,
    enrollment_id: uuid.UUID,
) -> list[uuid.UUID]:
    """Atomically release all six frozen drafts to the existing import worker."""

    enrollment = (
        await db.exec(
            select(StudyPilotEnrollment)
            .where(StudyPilotEnrollment.id == enrollment_id)
            .with_for_update()
        )
    ).first()
    if enrollment is None or enrollment.withdrawn_at is not None:
        raise PilotOperatorError("active enrollment not found")
    assignments = list(
        (
            await db.exec(
                select(StudyPilotAssignment)
                .where(StudyPilotAssignment.enrollment_id == enrollment_id)
                .order_by(StudyPilotAssignment.sequence_index)
                .with_for_update()
            )
        ).all()
    )
    if len(assignments) != ASSIGNMENT_COUNT or any(
        assignment.source_id is None for assignment in assignments
    ):
        raise PilotOperatorError("the six-source assignment manifest is incomplete")
    assignment_entries = [
        AssignmentInput(
            source_id=assignment.source_id,
            pair_index=assignment.pair_index,
            sequence_index=assignment.sequence_index,
            condition=assignment.condition,
            intended_target=assignment.intended_target,
            version_snapshot=assignment.version_snapshot,
        )
        for assignment in assignments
        if assignment.source_id is not None
    ]
    _validate_manifest_shape(assignment_entries)
    _validate_snapshot_for_enrollment(enrollment, assignment_entries)
    _validate_randomized_assignments(
        enrollment,
        assignment_entries,
        {
            assignment.source_id: assignment.source_lineage_id
            for assignment in assignments
            if assignment.source_id is not None
        },
    )
    source_ids = [assignment.source_id for assignment in assignments]
    sources = list(
        (
            await db.exec(
                select(MaterialSource)
                .where(col(MaterialSource.id).in_(source_ids))
                .with_for_update()
            )
        ).all()
    )
    if len(sources) != ASSIGNMENT_COUNT:
        raise PilotOperatorError("one or more assigned sources no longer exist")
    by_id = {source.id: source for source in sources}
    statuses = {source.status for source in sources}
    if SOURCE_DRAFT not in statuses:
        return [assignment.source_id for assignment in assignments if assignment.source_id]
    if statuses != {SOURCE_DRAFT}:
        raise PilotOperatorError(
            "processing release is all-or-nothing; sources are in mixed or final states"
        )
    now = _now()
    for assignment in assignments:
        assert assignment.source_id is not None
        source = by_id[assignment.source_id]
        if source.user_id != enrollment.user_id or source.lineage_id != (
            assignment.source_lineage_id
        ):
            raise PilotOperatorError("assigned source ownership or lineage changed")
        source.status = SOURCE_PENDING
        source.updated_at = now
        db.add(source)
    await db.commit()
    return [assignment.source_id for assignment in assignments if assignment.source_id]


async def review_proposal(
    db: AsyncSession,
    *,
    proposal_id: uuid.UUID,
    reviewer_id: str,
    decision: str,
    correction: dict[str, Any] | None = None,
) -> LessonProposalAudit:
    """Record one immutable concierge decision without touching original evidence."""

    reviewer_id = reviewer_id.strip()
    correction = dict(correction or {})
    if not reviewer_id:
        raise PilotOperatorError("reviewer_id must be non-empty")
    if decision not in REVIEW_DECISIONS:
        raise PilotOperatorError("review decision is not supported")
    if decision == PROPOSAL_AUDIT_CORRECTED and not correction:
        raise PilotOperatorError("a corrected decision requires a complete correction pack")
    if decision != PROPOSAL_AUDIT_CORRECTED and correction:
        raise PilotOperatorError("only a corrected decision may include correction content")

    audit = (
        await db.exec(
            select(LessonProposalAudit)
            .where(LessonProposalAudit.proposal_id == proposal_id)
            .with_for_update()
        )
    ).first()
    if audit is None:
        raise PilotOperatorError("immutable extraction audit not found")
    proposal = await db.get(
        MaterialTopicProposal,
        proposal_id,
        with_for_update=True,
        populate_existing=True,
    )
    if proposal is None:
        raise PilotOperatorError("proposal not found")
    source = await db.get(MaterialSource, proposal.source_id, with_for_update=True)
    if source is None or source.id != audit.source_id:
        raise PilotOperatorError("audit does not match the proposal source")

    if audit.reviewer_decision != PROPOSAL_AUDIT_PENDING:
        if (
            audit.reviewer_id == reviewer_id
            and audit.reviewer_decision == decision
            and audit.reviewer_correction == correction
        ):
            return audit
        raise PilotOperatorError("proposal review is immutable once recorded")
    if source.status not in REVIEWABLE_SOURCE_STATUSES:
        raise PilotOperatorError("proposal source is not ready for concierge review")
    if audit.grounding_gate_version != str(materials.LESSON_GROUNDING_GATE_VERSION):
        raise PilotOperatorError("proposal audit uses a stale grounding gate")

    stored_correction: dict[str, Any] = {}
    if decision == PROPOSAL_AUDIT_APPROVED:
        if proposal.status != PROPOSAL_CLEAN:
            raise PilotOperatorError("a proposal with grounding issues cannot be approved")
    elif decision == PROPOSAL_AUDIT_BLOCKED:
        proposal.status = PROPOSAL_NEEDS_ATTENTION
        proposal.issue = "pilot_audit_blocked"
        proposal.updated_at = _now()
        db.add(proposal)
    else:
        try:
            validated = materials._validated_lesson_concepts(source, [correction])[0]
        except Exception as exc:
            raise PilotOperatorError(f"invalid correction pack: {exc}") from exc
        proposal.section_title = validated["section_title"]
        proposal.topic = validated["topic"]
        proposal.answer_anchor = validated["answer_basis"]
        proposal.source_excerpt = validated["source_excerpt"]
        proposal.canonical_question = validated["canonical_question"]
        proposal.answer_rubric = validated["answer_rubric"]
        proposal.recall_questions = validated["recall_questions"]
        proposal.status = PROPOSAL_CLEAN
        proposal.issue = ""
        proposal.updated_at = _now()
        db.add(proposal)
        stored_correction = validated

    reviewed_at = _now()
    audit.reviewer_id = reviewer_id
    audit.reviewer_decision = decision
    audit.reviewer_correction = stored_correction
    audit.reviewed_at = reviewed_at
    db.add(audit)
    await db.commit()
    return audit


async def bind_assignment(
    db: AsyncSession,
    *,
    assignment_id: uuid.UUID,
    proposal_id: uuid.UUID,
) -> StudyPilotAssignment:
    """Bind the predeclared target and exclude every non-target proposal atomically."""

    assignment = (
        await db.exec(
            select(StudyPilotAssignment)
            .where(StudyPilotAssignment.id == assignment_id)
            .with_for_update()
        )
    ).first()
    if assignment is None:
        raise PilotOperatorError("assignment not found")
    enrollment = await db.get(
        StudyPilotEnrollment,
        assignment.enrollment_id,
        with_for_update=True,
        populate_existing=True,
    )
    if enrollment is None or enrollment.withdrawn_at is not None:
        raise PilotOperatorError("active enrollment not found")
    proposal = await db.get(
        MaterialTopicProposal,
        proposal_id,
        with_for_update=True,
        populate_existing=True,
    )
    if proposal is None:
        raise PilotOperatorError("proposal not found")
    source = await db.get(MaterialSource, proposal.source_id, with_for_update=True)
    if source is None:
        raise PilotOperatorError("source not found")
    if source.user_id != enrollment.user_id:
        raise PilotOperatorError("proposal belongs to another account")
    if source.id != assignment.source_id or source.lineage_id != assignment.source_lineage_id:
        raise PilotOperatorError("proposal does not match the frozen source lineage")
    if assignment.bound_at is not None:
        if assignment.target_proposal_id == proposal_id:
            return assignment
        raise PilotOperatorError("assignment is already bound to another proposal")
    intended_target = assignment.intended_target.strip()
    try:
        intended_position = int(intended_target.removeprefix("position:"))
    except ValueError as exc:
        raise PilotOperatorError("frozen target is not a position key") from exc
    if intended_target != f"position:{intended_position}" or intended_position not in {
        1,
        2,
        3,
    }:
        raise PilotOperatorError("frozen target is not a valid position key")
    target_matches = proposal.position == intended_position
    if not target_matches:
        raise PilotOperatorError(
            "proposal does not match the predeclared deterministic target"
        )
    if source.status not in REVIEWABLE_SOURCE_STATUSES:
        raise PilotOperatorError("source is not ready for target binding")
    if source.result_summary.get("grounding_gate_version") != (
        materials.LESSON_GROUNDING_GATE_VERSION
    ):
        raise PilotOperatorError("source has not passed the current grounding gate")

    proposals = list(
        (
            await db.exec(
                select(MaterialTopicProposal)
                .where(MaterialTopicProposal.source_id == source.id)
                .order_by(MaterialTopicProposal.position)
                .with_for_update()
            )
        ).all()
    )
    audits = list(
        (
            await db.exec(
                select(LessonProposalAudit)
                .where(LessonProposalAudit.source_id == source.id)
                .with_for_update()
            )
        ).all()
    )
    audits_by_proposal = {audit.proposal_id: audit for audit in audits}
    if set(audits_by_proposal) != {row.id for row in proposals}:
        raise PilotOperatorError("every extracted proposal must have immutable audit evidence")
    if any(
        audit.reviewer_decision == PROPOSAL_AUDIT_PENDING or audit.reviewed_at is None
        for audit in audits
    ):
        raise PilotOperatorError("every proposal must complete blinded review before binding")
    if any(
        not audit.original_proposal_pack or not audit.original_grounding_findings
        for audit in audits
    ):
        raise PilotOperatorError(
            "every proposal must retain untouched output and grounding findings"
        )
    frozen_extraction_route = assignment.version_snapshot["extraction_provider_route"]
    frozen_extraction_prompt = assignment.version_snapshot[
        "extraction_prompt_version"
    ]
    frozen_grounding_gate = assignment.version_snapshot["grounding_gate_version"]
    if any(
        audit.extraction_route != frozen_extraction_route
        or audit.extraction_prompt_version != frozen_extraction_prompt
        or audit.grounding_gate_version != frozen_grounding_gate
        for audit in audits
    ):
        raise PilotOperatorError(
            "proposal audit evidence does not match the frozen extraction contract"
        )
    target_audit = audits_by_proposal.get(proposal_id)
    if (
        target_audit is None
        or target_audit.reviewer_decision
        not in {PROPOSAL_AUDIT_APPROVED, PROPOSAL_AUDIT_CORRECTED}
        or proposal.status != PROPOSAL_CLEAN
    ):
        raise PilotOperatorError("target proposal did not pass concierge review")

    now = _now()
    for row in proposals:
        if row.id == proposal_id:
            continue
        row.status = PROPOSAL_EXCLUDED
        row.issue = "pilot_non_target"
        row.updated_at = now
        db.add(row)
    assignment.target_proposal_id = proposal_id
    assignment.bound_at = now
    assignment.updated_at = now
    db.add(assignment)
    await db.commit()
    return assignment


async def approve_transfer_prompt(
    db: AsyncSession,
    *,
    assignment_id: uuid.UUID,
    candidate_index: int,
    reviewer_id: str,
    approved_at: datetime,
) -> LessonCheck:
    """Freeze one audited varied-cue candidate before the first delayed Recall."""

    reviewer_id = reviewer_id.strip()
    approved_at = _aware_utc(approved_at, field="approved_at")
    if not reviewer_id:
        raise PilotOperatorError("reviewer_id must be non-empty")
    assignment = (
        await db.exec(
            select(StudyPilotAssignment)
            .where(StudyPilotAssignment.id == assignment_id)
            .with_for_update()
        )
    ).first()
    if (
        assignment is None
        or assignment.bound_at is None
        or assignment.target_proposal_id is None
    ):
        raise PilotOperatorError("assignment target is not bound")
    enrollment = await db.get(
        StudyPilotEnrollment,
        assignment.enrollment_id,
        with_for_update=True,
        populate_existing=True,
    )
    if enrollment is None or enrollment.withdrawn_at is not None:
        raise PilotOperatorError("active enrollment not found")
    if approved_at < _stored_utc(assignment.bound_at):
        raise PilotOperatorError("transfer approval cannot predate target binding")
    proposal = await db.get(
        MaterialTopicProposal,
        assignment.target_proposal_id,
        with_for_update=True,
        populate_existing=True,
    )
    if proposal is None or proposal.source_id != assignment.source_id:
        raise PilotOperatorError("bound proposal no longer matches the assignment")
    audit = (
        await db.exec(
            select(LessonProposalAudit)
            .where(LessonProposalAudit.proposal_id == proposal.id)
            .with_for_update()
        )
    ).first()
    if (
        audit is None
        or audit.reviewer_decision
        not in {PROPOSAL_AUDIT_APPROVED, PROPOSAL_AUDIT_CORRECTED}
        or audit.reviewed_at is None
    ):
        raise PilotOperatorError("proposal has not passed concierge review")
    if candidate_index < 1 or candidate_index > len(proposal.recall_questions):
        raise PilotOperatorError("transfer candidate index is out of range")
    candidate = proposal.recall_questions[candidate_index - 1]
    prompt_level = str(candidate.get("level", "")).strip()
    prompt_text = str(candidate.get("question", "")).strip()
    if prompt_level not in {"application", "failure_tradeoff"} or not prompt_text:
        raise PilotOperatorError(
            "transfer candidate must be a grounded application or failure_tradeoff prompt"
        )
    prompt_version = str(
        assignment.version_snapshot.get("transfer_prompt_version", "")
    ).strip()
    rubric_version = str(
        assignment.version_snapshot.get("transfer_prompt_rubric_version", "")
    ).strip()
    if not prompt_version or not rubric_version:
        raise PilotOperatorError("transfer prompt contract is not frozen")
    if proposal.card_id is not None:
        completed_recall = (
            await db.exec(
                select(Session.id).where(
                    Session.card_id == proposal.card_id,
                    Session.status == STATUS_COMPLETE,
                    Session.practice == False,  # noqa: E712 - SQL expression
                )
            )
        ).first()
        if completed_recall is not None:
            raise PilotOperatorError("transfer prompt must be frozen before first Recall")
    existing = (
        await db.exec(
            select(LessonCheck)
            .where(
                LessonCheck.proposal_id == proposal.id,
                LessonCheck.kind == LESSON_CHECK_TRANSFER,
            )
            .with_for_update()
        )
    ).first()
    candidate_id = f"recall_questions:{candidate_index}"
    if existing is not None:
        if (
            existing.user_id == enrollment.user_id
            and existing.condition == assignment.condition
            and existing.prompt_level == prompt_level
            and existing.prompt_version == prompt_version
            and existing.source_candidate_id == candidate_id
            and existing.prompt_text_snapshot == prompt_text
            and existing.prompt_rubric_version == rubric_version
            and existing.prompt_reviewer_id == reviewer_id
            and existing.prompt_approved_at is not None
            and _stored_utc(existing.prompt_approved_at) == approved_at
        ):
            return existing
        raise PilotOperatorError("transfer prompt is immutable once approved")
    formation = (
        await db.exec(
            select(LessonCheck).where(
                LessonCheck.proposal_id == proposal.id,
                LessonCheck.kind == LESSON_CHECK_FORMATION,
            )
        )
    ).first()
    available_at = (
        _stored_utc(formation.exposed_at) + timedelta(days=7)
        if formation is not None and formation.exposed_at is not None
        else None
    )
    transfer = LessonCheck(
        user_id=enrollment.user_id,
        proposal_id=proposal.id,
        card_id=proposal.card_id,
        kind=LESSON_CHECK_TRANSFER,
        condition=assignment.condition,
        prompt_level=prompt_level,
        prompt_version=prompt_version,
        provider_route={},
        source_candidate_id=candidate_id,
        prompt_text_snapshot=prompt_text,
        prompt_rubric_version=rubric_version,
        prompt_reviewer_id=reviewer_id,
        prompt_approved_at=approved_at,
        status=LESSON_CHECK_OPEN,
        available_at=available_at,
        started_at=approved_at,
        updated_at=approved_at,
    )
    db.add(transfer)
    await db.commit()
    return transfer


async def withdraw_participant(
    db: AsyncSession,
    *,
    enrollment_id: uuid.UUID,
    withdrawn_at: datetime,
) -> StudyPilotEnrollment:
    withdrawn_at = _aware_utc(withdrawn_at, field="withdrawn_at")
    enrollment = (
        await db.exec(
            select(StudyPilotEnrollment)
            .where(StudyPilotEnrollment.id == enrollment_id)
            .with_for_update()
        )
    ).first()
    if enrollment is None:
        raise PilotOperatorError("enrollment not found")
    if withdrawn_at < _stored_utc(enrollment.consented_at):
        raise PilotOperatorError("withdrawal cannot predate consent")
    if enrollment.withdrawn_at is not None:
        if _stored_utc(enrollment.withdrawn_at) == withdrawn_at:
            return enrollment
        raise PilotOperatorError("withdrawal timestamp is immutable once recorded")
    enrollment.withdrawn_at = withdrawn_at
    enrollment.updated_at = _now()
    db.add(enrollment)
    await db.commit()
    return enrollment
