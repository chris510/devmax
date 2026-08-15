#!/usr/bin/env python3
"""Preview distilled notes or save their provider-neutral writeback bundle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.second_brain import (
    LearningNoteError,  # noqa: E402
    RenderedLearningNote,  # noqa: E402
    canonical_json_bytes,  # noqa: E402
    reject_raw_export_fields,  # noqa: E402
    render_learning_notes,  # noqa: E402
    slugify_concept,  # noqa: E402
    validate_learning_writeback_bundle,  # noqa: E402
)


def _read_payload(location: str) -> object:
    if location == "-":
        return json.load(sys.stdin)
    if location.startswith(("http://", "https://")):
        request = Request(location, headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310 - explicit user URL
                return json.load(response)
        except URLError as exc:
            raise ValueError(f"could not read export URL: {exc}") from exc
    with Path(location).open(encoding="utf-8") as handle:
        return json.load(handle)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preview distilled second-brain learning notes or save the validated "
            "provider-neutral JSON writeback bundle. This command never writes to a vault."
        )
    )
    parser.add_argument("artifact", help="export JSON file, URL, or - for stdin")
    parser.add_argument(
        "--concept",
        help=(
            "concept title or kebab-case slug when the API JSON contains multiple concepts"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="save the complete validated writeback bundle to a new JSON file",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--vault", type=Path, help=argparse.SUPPRESS)
    return parser


def _select_concepts(payload: object, selector: str | None) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        raise LearningNoteError("artifact must be a JSON object")
    if "concepts" not in payload:
        if selector is not None:
            concept = payload.get("concept")
            if not isinstance(concept, str) or (
                concept.casefold() != selector.casefold()
                and slugify_concept(concept) != selector
            ):
                raise LearningNoteError("--concept did not match the artifact concept")
        return [payload]

    concepts = payload["concepts"]
    if not isinstance(concepts, list) or not concepts:
        raise LearningNoteError("artifact concepts must be a non-empty list")
    if any(not isinstance(concept, dict) for concept in concepts):
        raise LearningNoteError("every artifact concept must be a JSON object")
    if selector is None:
        return concepts

    wanted = selector.casefold()
    matches = [
        concept
        for concept in concepts
        if isinstance(concept.get("concept"), str)
        and (
            concept["concept"].casefold() == wanted
            or slugify_concept(concept["concept"]) == selector
        )
    ]
    if len(matches) != 1:
        raise LearningNoteError(f"--concept matched {len(matches)} concepts")
    return matches


def _preview(notes: tuple[RenderedLearningNote, ...]) -> str:
    if len(notes) == 1:
        return notes[0].markdown
    return "\n".join(
        f"<!-- {note.filename} -->\n{note.markdown}" for note in notes
    )


def _bundle(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise LearningNoteError("artifact must be a JSON object")
    if "writeback_bundle" not in payload:
        raise LearningNoteError(
            "artifact does not contain writeback_bundle; fetch lesson artifacts again"
        )
    return validate_learning_writeback_bundle(payload["writeback_bundle"])


def _save_bundle(bundle: dict[str, object], destination: Path) -> None:
    path = destination.expanduser().resolve()
    if path.exists():
        raise LearningNoteError(f"output already exists: {path}")
    if not path.parent.is_dir():
        raise LearningNoteError(f"output directory does not exist: {path.parent}")
    path.write_bytes(canonical_json_bytes(bundle) + b"\n")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.write or args.vault is not None:
        parser.error(
            "direct vault writes are deprecated; save the bundle with --output, "
            "then let the vault importer apply it"
        )
    if args.output is not None and args.concept is not None:
        parser.error("--concept is preview-only; --output saves the complete bundle")

    try:
        payload = _read_payload(args.artifact)
        reject_raw_export_fields(payload)
        payloads = _select_concepts(payload, args.concept)
        rendered = render_learning_notes(payloads)
        if args.output is None:
            sys.stdout.write(_preview(rendered))
            return 0
        bundle = _bundle(payload)
        _save_bundle(bundle, args.output)
    except (OSError, ValueError) as exc:
        print(f"second-brain export: {exc}", file=sys.stderr)
        return 2

    print(f"Saved validated writeback bundle: {args.output.expanduser().resolve()}")
    print("No vault files, commits, or pushes were performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
