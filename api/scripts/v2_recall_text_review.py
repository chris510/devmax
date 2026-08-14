#!/usr/bin/env python
"""Draft or validate a human text-quality review for one Luna V2 artifact.

This tool is offline. It never opens a provider client and never marks a review
approved. The reviewer must inspect the exact evidence text, fill every note,
set every applicable check, set both approval statuses, and add a timezone-aware
review timestamp.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import (
    v2_recall_compare,  # noqa: E402
    v2_recall_eval,  # noqa: E402
)
from scripts.effort_sweep_support import hydrate_grounding, load_cases  # noqa: E402


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    commands = argument_parser.add_subparsers(dest="command", required=True)

    draft = commands.add_parser("draft", help="write an unapproved all-case template")
    draft.add_argument("--luna", required=True, type=Path)
    draft.add_argument("--output", required=True, type=Path)
    draft.add_argument("--reviewer", required=True)
    draft.add_argument("--cases", required=True, type=Path)
    draft.add_argument("--grounding-manifest", required=True, type=Path)

    check = commands.add_parser("check", help="validate one completed review")
    check.add_argument("--luna", required=True, type=Path)
    check.add_argument("--review", required=True, type=Path)
    check.add_argument("--cases", required=True, type=Path)
    check.add_argument("--grounding-manifest", required=True, type=Path)
    return argument_parser


def _write_new(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _reviewed_cases(args, argument_parser: argparse.ArgumentParser) -> list[dict]:
    cases = hydrate_grounding(
        load_cases(args.cases, argument_parser),
        args.grounding_manifest,
        argument_parser,
    )
    failures = v2_recall_eval.stage2_pack_failures(cases)
    if failures:
        argument_parser.error(
            "trusted Stage 2 pack failed validation: " + "; ".join(failures)
        )
    return cases


def main(argv: Sequence[str] | None = None) -> int:
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    try:
        cases = _reviewed_cases(args, argument_parser)
        run = v2_recall_compare.load_evidence(
            args.luna, label="luna", provider="luna"
        )
        if args.command == "draft":
            payload = v2_recall_compare.text_quality_review_template(
                run, cases, reviewer=args.reviewer
            )
            _write_new(args.output, payload)
            print(
                f"Wrote pending review for {len(payload['case_reviews'])} cases "
                f"to {args.output}. No case is approved yet."
            )
            return 0

        review = v2_recall_compare.load_text_quality_attestation(args.review)
        v2_recall_compare.validate_text_quality_attestations(
            [run], [review], cases
        )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        argument_parser.error(str(exc))
    print(
        "PASS: one human text-quality attestation covers every successful case "
        "and exactly matches the immutable Luna artifact."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
