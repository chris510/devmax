#!/usr/bin/env python3
"""Run reviewed, access-controlled adaptive-study pilot operations.

Examples (all writes require the explicit ``--confirm`` flag):

    uv run python scripts/lesson_pilot_operator.py enroll \
      --user-id ... --cohort pilot-2026-08 \
      --consent-version adaptive-study-pilot-research-v1 \
      --consented-at 2026-08-17T16:00:00Z --randomization-seed ... --confirm

    uv run python scripts/lesson_pilot_operator.py contract \
      --consent-version adaptive-study-pilot-research-v1

    uv run python scripts/lesson_pilot_operator.py provision \
      --enrollment-id ... --manifest six-sources.json --confirm

    uv run python scripts/lesson_pilot_operator.py process \
      --enrollment-id ... --confirm

    uv run python scripts/lesson_pilot_operator.py review \
      --proposal-id ... --reviewer-id reviewer-01 --decision approved --confirm

    uv run python scripts/lesson_pilot_operator.py bind \
      --assignment-id ... --proposal-id ... --confirm

This command prints identifiers, enums, and timestamps only. It never prints a
source, proposal pack, response, correction, rubric, or provider transcript.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import session_factory  # noqa: E402
from app.models import MaterialSource  # noqa: E402
from app.pilot_contract import PILOT_RESEARCH_CONSENT_CATALOG  # noqa: E402
from app.services import materials  # noqa: E402
from app.services.lesson_pilot_ops import (  # noqa: E402
    PilotOperatorError,
    ProvisionedAssignmentInput,
    approve_transfer_prompt,
    bind_assignment,
    enroll_participant,
    provision_manifest,
    review_proposal,
    runtime_contract_snapshot,
    start_manifest_processing,
    withdraw_participant,
)


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a UUID") from exc


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a UTC offset")
    return parsed


def _json_file(path: Path) -> object:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotOperatorError(f"could not read JSON file: {exc}") from exc


def _provision_manifest(path: Path) -> list[ProvisionedAssignmentInput]:
    payload = _json_file(path)
    if not isinstance(payload, list):
        raise PilotOperatorError("provision manifest must be a JSON array")
    rows: list[ProvisionedAssignmentInput] = []
    for index, item in enumerate(payload, 1):
        if not isinstance(item, dict):
            raise PilotOperatorError(f"source {index} must be an object")
        forbidden = {"condition", "sequence_index"} & item.keys()
        if forbidden:
            raise PilotOperatorError(
                "condition and sequence_index are derived from the enrollment seed"
            )
        try:
            snapshot = item["version_snapshot"]
            if not isinstance(snapshot, dict):
                raise TypeError("version_snapshot")
            rows.append(
                ProvisionedAssignmentInput(
                    source_id=uuid.UUID(str(item["source_id"])),
                    source_lineage_id=uuid.UUID(str(item["source_lineage_id"])),
                    title=str(item["title"]),
                    source_text=str(item["source_text"]),
                    source_url=str(item.get("source_url", "")),
                    content_provenance=str(item["content_provenance"]),
                    kind=str(item.get("kind", "notes")),
                    original_filename=str(item.get("original_filename", "")),
                    mime_type=str(item.get("mime_type", "text/plain")),
                    intent=str(item.get("intent", "learn")),
                    pair_index=int(item["pair_index"]),
                    intended_target=str(item["intended_target"]),
                    version_snapshot=dict(snapshot),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PilotOperatorError(f"source {index} is invalid") from exc
    return rows


def _correction(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = _json_file(path)
    if not isinstance(payload, dict):
        raise PilotOperatorError("correction pack must be a JSON object")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    enroll = subparsers.add_parser("enroll", help="record explicit pilot consent")
    enroll.add_argument("--user-id", required=True, type=_uuid)
    enroll.add_argument("--cohort", required=True)
    enroll.add_argument(
        "--consent-version",
        required=True,
        choices=tuple(PILOT_RESEARCH_CONSENT_CATALOG),
    )
    enroll.add_argument("--consented-at", required=True, type=_timestamp)
    enroll.add_argument("--randomization-seed", required=True)
    enroll.add_argument("--confirm", action="store_true")

    contract = subparsers.add_parser(
        "contract", help="print the safe runtime snapshot to freeze in a manifest"
    )
    contract.add_argument(
        "--consent-version",
        required=True,
        choices=tuple(PILOT_RESEARCH_CONSENT_CATALOG),
    )

    provision = subparsers.add_parser(
        "provision",
        help="atomically create six draft lessons and freeze their assignments",
    )
    provision.add_argument("--enrollment-id", required=True, type=_uuid)
    provision.add_argument("--manifest", required=True, type=Path)
    provision.add_argument("--confirm", action="store_true")

    process = subparsers.add_parser(
        "process", help="release all six frozen drafts to the existing import worker"
    )
    process.add_argument("--enrollment-id", required=True, type=_uuid)
    process.add_argument("--confirm", action="store_true")

    review = subparsers.add_parser(
        "review", help="record the immutable concierge proposal decision"
    )
    review.add_argument("--proposal-id", required=True, type=_uuid)
    review.add_argument("--reviewer-id", required=True)
    review.add_argument(
        "--decision", required=True, choices=("approved", "corrected", "blocked")
    )
    review.add_argument(
        "--correction",
        type=Path,
        help="complete grounded correction pack; required only for corrected",
    )
    review.add_argument("--confirm", action="store_true")

    bind = subparsers.add_parser(
        "bind", help="bind the predeclared target and exclude every non-target"
    )
    bind.add_argument("--assignment-id", required=True, type=_uuid)
    bind.add_argument("--proposal-id", required=True, type=_uuid)
    bind.add_argument("--confirm", action="store_true")

    transfer = subparsers.add_parser(
        "approve-transfer",
        help="freeze one human-reviewed varied-cue candidate before first Recall",
    )
    transfer.add_argument("--assignment-id", required=True, type=_uuid)
    transfer.add_argument("--candidate-index", required=True, type=int)
    transfer.add_argument("--reviewer-id", required=True)
    transfer.add_argument("--approved-at", required=True, type=_timestamp)
    transfer.add_argument("--confirm", action="store_true")

    withdraw = subparsers.add_parser(
        "withdraw", help="block new pilot checks and research reporting"
    )
    withdraw.add_argument("--enrollment-id", required=True, type=_uuid)
    withdraw.add_argument("--withdrawn-at", required=True, type=_timestamp)
    withdraw.add_argument("--confirm", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "contract":
        return {
            "operation": "contract",
            "version_snapshot": runtime_contract_snapshot(args.consent_version),
        }
    if not args.confirm:
        raise PilotOperatorError("refusing to mutate without --confirm")
    async with session_factory() as db:
        if args.command == "enroll":
            row = await enroll_participant(
                db,
                user_id=args.user_id,
                cohort=args.cohort,
                consent_version=args.consent_version,
                consented_at=args.consented_at,
                randomization_seed=args.randomization_seed,
            )
            return {
                "operation": "enroll",
                "enrollment_id": str(row.id),
                "cohort": row.cohort,
                "consented_at": row.consented_at.isoformat(),
            }
        if args.command == "provision":
            sources, rows = await provision_manifest(
                db,
                enrollment_id=args.enrollment_id,
                entries=_provision_manifest(args.manifest),
            )
            return {
                "operation": "provision",
                "enrollment_id": str(args.enrollment_id),
                "source_ids": [str(row.id) for row in sources],
                "assignment_ids": [str(row.id) for row in rows],
                "source_count": len(sources),
            }
        if args.command == "process":
            source_ids = await start_manifest_processing(
                db, enrollment_id=args.enrollment_id
            )
            claimed = await asyncio.gather(
                *(materials.process_import(source_id) for source_id in source_ids)
            )
            await db.rollback()
            source_rows = [
                await db.get(MaterialSource, source_id, populate_existing=True)
                for source_id in source_ids
            ]
            return {
                "operation": "process",
                "enrollment_id": str(args.enrollment_id),
                "source_ids": [str(source_id) for source_id in source_ids],
                "claimed_count": sum(claimed),
                "statuses": [
                    {
                        "source_id": str(source_id),
                        "status": row.status if row is not None else "deleted",
                    }
                    for source_id, row in zip(source_ids, source_rows, strict=True)
                ],
            }
        if args.command == "review":
            row = await review_proposal(
                db,
                proposal_id=args.proposal_id,
                reviewer_id=args.reviewer_id,
                decision=args.decision,
                correction=_correction(args.correction),
            )
            return {
                "operation": "review",
                "audit_id": str(row.id),
                "proposal_id": str(row.proposal_id),
                "decision": row.reviewer_decision,
                "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
            }
        if args.command == "bind":
            row = await bind_assignment(
                db,
                assignment_id=args.assignment_id,
                proposal_id=args.proposal_id,
            )
            return {
                "operation": "bind",
                "assignment_id": str(row.id),
                "proposal_id": str(row.target_proposal_id),
                "bound_at": row.bound_at.isoformat() if row.bound_at else None,
            }
        if args.command == "approve-transfer":
            row = await approve_transfer_prompt(
                db,
                assignment_id=args.assignment_id,
                candidate_index=args.candidate_index,
                reviewer_id=args.reviewer_id,
                approved_at=args.approved_at,
            )
            return {
                "operation": "approve-transfer",
                "check_id": str(row.id),
                "proposal_id": str(row.proposal_id),
                "prompt_level": row.prompt_level,
                "approved_at": (
                    row.prompt_approved_at.isoformat()
                    if row.prompt_approved_at
                    else None
                ),
            }
        row = await withdraw_participant(
            db,
            enrollment_id=args.enrollment_id,
            withdrawn_at=args.withdrawn_at,
        )
        return {
            "operation": "withdraw",
            "enrollment_id": str(row.id),
            "withdrawn_at": row.withdrawn_at.isoformat() if row.withdrawn_at else None,
        }


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except PilotOperatorError as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
