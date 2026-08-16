#!/usr/bin/env python3
"""Produce the privacy-bounded participant-level adaptive-study pilot report.

The report intentionally contains only pseudonymous identifiers, enums, counts,
and timestamps. It excludes source text and attribution, proposal/answer content,
drafts, responses, transcripts, rubrics, feedback, runtime scores, scheduling
state, provider output, and note content. Withdrawn enrollments are always
excluded; account export is the separate user-facing path for private records.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import session_factory  # noqa: E402
from app.models import (  # noqa: E402
    LESSON_CHECK_FORMATION,
    LESSON_CHECK_TRANSFER,
    STATUS_COMPLETE,
    Card,
    LessonCheck,
    LessonProposalAudit,
    MaterialSource,
    MaterialTopicProposal,
    Session,
    StudyPilotAssignment,
    StudyPilotEnrollment,
)

REPORT_SCHEMA_VERSION = "lesson-pilot-participant-v1"


class PilotReportError(ValueError):
    """The requested restricted report cannot be produced safely."""


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _check_summary(check: LessonCheck | None) -> dict[str, object] | None:
    if check is None:
        return None
    return {
        "check_id": str(check.id),
        "status": check.status,
        "condition": check.condition,
        "prompt_level": check.prompt_level,
        "qualitative_outcome": check.qualitative_outcome or None,
        "started_at": _timestamp(check.started_at),
        "submitted_at": _timestamp(check.submitted_at),
        "exposed_at": _timestamp(check.exposed_at),
        "recall_not_before_at": _timestamp(check.recall_not_before_at),
        "available_at": _timestamp(check.available_at),
    }


async def build_report(
    db: AsyncSession,
    *,
    enrollment_id: uuid.UUID | None = None,
    generated_at: datetime | None = None,
) -> dict[str, object]:
    """Aggregate active-enrollment domain rows without private content."""

    statement = select(StudyPilotEnrollment).where(
        StudyPilotEnrollment.withdrawn_at.is_(None)
    )
    if enrollment_id is not None:
        statement = statement.where(StudyPilotEnrollment.id == enrollment_id)
    enrollments = list(
        (
            await db.exec(
                statement.order_by(
                    StudyPilotEnrollment.consented_at,
                    StudyPilotEnrollment.id,
                )
            )
        ).all()
    )
    if enrollment_id is not None and not enrollments:
        raise PilotReportError("active enrollment not found")
    if not enrollments:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "generated_at": _timestamp(generated_at or datetime.now(UTC)),
            "participant_count": 0,
            "participants": [],
        }

    enrollment_ids = [row.id for row in enrollments]
    assignments = list(
        (
            await db.exec(
                select(StudyPilotAssignment)
                .where(col(StudyPilotAssignment.enrollment_id).in_(enrollment_ids))
                .order_by(
                    StudyPilotAssignment.enrollment_id,
                    StudyPilotAssignment.sequence_index,
                )
            )
        ).all()
    )
    source_ids = [row.source_id for row in assignments if row.source_id is not None]
    proposal_ids = [
        row.target_proposal_id
        for row in assignments
        if row.target_proposal_id is not None
    ]
    sources = (
        list(
            (
                await db.exec(
                    select(MaterialSource).where(col(MaterialSource.id).in_(source_ids))
                )
            ).all()
        )
        if source_ids
        else []
    )
    proposals = (
        list(
            (
                await db.exec(
                    select(MaterialTopicProposal).where(
                        col(MaterialTopicProposal.id).in_(proposal_ids)
                    )
                )
            ).all()
        )
        if proposal_ids
        else []
    )
    audits = (
        list(
            (
                await db.exec(
                    select(LessonProposalAudit).where(
                        col(LessonProposalAudit.proposal_id).in_(proposal_ids)
                    )
                )
            ).all()
        )
        if proposal_ids
        else []
    )
    checks = list(
        (
            await db.exec(
                select(LessonCheck).where(
                    col(LessonCheck.user_id).in_([row.user_id for row in enrollments])
                )
            )
        ).all()
    )
    card_ids = [row.card_id for row in proposals if row.card_id is not None]
    recalls = (
        list(
            (
                await db.exec(
                    select(Session)
                    .join(Card, Card.id == Session.card_id)
                    .where(
                        col(Session.card_id).in_(card_ids),
                        Session.status == STATUS_COMPLETE,
                        Session.practice.is_(False),
                    )
                    .order_by(Session.card_id, Session.started_at, Session.id)
                )
            ).all()
        )
        if card_ids
        else []
    )

    sources_by_id = {row.id: row for row in sources}
    proposals_by_id = {row.id: row for row in proposals}
    audits_by_proposal = {row.proposal_id: row for row in audits}
    checks_by_key = {(row.proposal_id, row.kind): row for row in checks}
    recalls_by_card: dict[uuid.UUID, list[Session]] = {}
    for session in recalls:
        recalls_by_card.setdefault(session.card_id, []).append(session)
    assignments_by_enrollment: dict[uuid.UUID, list[StudyPilotAssignment]] = {}
    for assignment in assignments:
        assignments_by_enrollment.setdefault(assignment.enrollment_id, []).append(
            assignment
        )

    participants: list[dict[str, object]] = []
    for enrollment in enrollments:
        assignment_rows: list[dict[str, object]] = []
        formation_completed = 0
        first_recall_completed = 0
        transfer_submitted = 0
        for assignment in assignments_by_enrollment.get(enrollment.id, []):
            source = sources_by_id.get(assignment.source_id)
            proposal = proposals_by_id.get(assignment.target_proposal_id)
            audit = (
                audits_by_proposal.get(assignment.target_proposal_id)
                if assignment.target_proposal_id is not None
                else None
            )
            formation = (
                checks_by_key.get((proposal.id, LESSON_CHECK_FORMATION))
                if proposal is not None
                else None
            )
            transfer = (
                checks_by_key.get((proposal.id, LESSON_CHECK_TRANSFER))
                if proposal is not None
                else None
            )
            first_recall = None
            if proposal is not None and proposal.card_id is not None and formation is not None:
                eligible = formation.recall_not_before_at
                for candidate in recalls_by_card.get(proposal.card_id, []):
                    if eligible is None or candidate.started_at >= eligible:
                        first_recall = candidate
                        break
            if formation is not None and formation.submitted_at is not None:
                formation_completed += 1
            if first_recall is not None:
                first_recall_completed += 1
            if transfer is not None and transfer.submitted_at is not None:
                transfer_submitted += 1
            assignment_rows.append(
                {
                    "assignment_id": str(assignment.id),
                    "source_lineage_id": str(assignment.source_lineage_id),
                    "source_id": str(assignment.source_id) if assignment.source_id else None,
                    "target_proposal_id": (
                        str(assignment.target_proposal_id)
                        if assignment.target_proposal_id
                        else None
                    ),
                    "pair_index": assignment.pair_index,
                    "sequence_index": assignment.sequence_index,
                    "condition": assignment.condition,
                    "assigned_at": _timestamp(assignment.assigned_at),
                    "bound_at": _timestamp(assignment.bound_at),
                    "source_lifecycle": (
                        {
                            "status": source.status,
                            "created_at": _timestamp(source.created_at),
                            "proposals_ready_at": _timestamp(source.proposals_ready_at),
                            "review_opened_at": _timestamp(source.review_opened_at),
                            "confirmed_at": _timestamp(source.confirmed_at),
                            "distilled_at": _timestamp(source.distilled_at),
                        }
                        if source is not None
                        else None
                    ),
                    "proposal_status": proposal.status if proposal is not None else None,
                    "proposal_audit": (
                        {
                            "audit_id": str(audit.id),
                            "decision": audit.reviewer_decision,
                            "reviewed_at": _timestamp(audit.reviewed_at),
                        }
                        if audit is not None
                        else None
                    ),
                    "formation": _check_summary(formation),
                    "first_recall": (
                        {
                            "session_id": str(first_recall.id),
                            "started_at": _timestamp(first_recall.started_at),
                            "ended_at": _timestamp(first_recall.ended_at),
                        }
                        if first_recall is not None
                        else None
                    ),
                    "transfer": _check_summary(transfer),
                }
            )
        participants.append(
            {
                "enrollment_id": str(enrollment.id),
                "cohort": enrollment.cohort,
                "consented_at": _timestamp(enrollment.consented_at),
                "assignment_count": len(assignment_rows),
                "bound_source_count": sum(
                    row["bound_at"] is not None for row in assignment_rows
                ),
                "formation_completed_count": formation_completed,
                "first_recall_completed_count": first_recall_completed,
                "transfer_submitted_count": transfer_submitted,
                "assignments": assignment_rows,
            }
        )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": _timestamp(generated_at or datetime.now(UTC)),
        "participant_count": len(participants),
        "participants": participants,
    }


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a UUID") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--enrollment-id",
        type=_uuid,
        help="restrict the report to one active pseudonymous enrollment",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="write to a new mode-0600 JSON file; existing files are refused",
    )
    return parser


async def _run(enrollment_id: uuid.UUID | None) -> dict[str, object]:
    async with session_factory() as db:
        return await build_report(db, enrollment_id=enrollment_id)


def _write_restricted_report(destination: Path, payload: str) -> None:
    """Create one report exclusively with mode 0600 and never replace a path."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(destination, flags, 0o600)
    except FileExistsError as exc:
        raise PilotReportError(
            f"refusing to overwrite existing output: {destination}"
        ) from exc
    except OSError as exc:
        raise PilotReportError(f"could not create report output: {exc}") from exc
    try:
        os.fchmod(descriptor, 0o600)
        data = payload.encode("utf-8")
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError("report output write made no progress")
            offset += written
        os.fsync(descriptor)
    except OSError as exc:
        os.close(descriptor)
        destination.unlink(missing_ok=True)
        raise PilotReportError(f"could not write report output: {exc}") from exc
    os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(_run(args.enrollment_id))
    except PilotReportError as exc:
        parser.error(str(exc))
    payload = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    destination = args.output.expanduser().resolve()
    if not destination.parent.is_dir():
        parser.error(f"output directory does not exist: {destination.parent}")
    try:
        _write_restricted_report(destination, payload)
    except PilotReportError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
