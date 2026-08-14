#!/usr/bin/env python3
"""Preview or locally write a distilled Devmax learning-note export."""

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
    VaultWriteError,  # noqa: E402
    render_learning_notes,  # noqa: E402
    slugify_concept,  # noqa: E402
    write_learning_notes,  # noqa: E402
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
            "Preview a distilled second-brain learning note. Writing is local-only, "
            "requires --write and an explicit --vault, and never commits or pushes."
        )
    )
    parser.add_argument("artifact", help="export JSON file, URL, or - for stdin")
    parser.add_argument(
        "--concept",
        help=(
            "concept title or kebab-case slug when the API JSON contains multiple concepts"
        ),
    )
    parser.add_argument("--write", action="store_true", help="write note, index, and log")
    parser.add_argument("--vault", type=Path, help="explicit second-brain vault path")
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


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.write and args.vault is None:
        parser.error("--write requires an explicit --vault")

    try:
        payloads = _select_concepts(_read_payload(args.artifact), args.concept)
        rendered = render_learning_notes(payloads)
        if not args.write:
            sys.stdout.write(_preview(rendered))
            return 0

        result = write_learning_notes(rendered, args.vault)
    except (OSError, ValueError, VaultWriteError) as exc:
        print(f"second-brain export: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote {len(result.note_paths)} learning note(s):")
    for path in result.note_paths:
        print(f"- {path}")
    print("Review the vault changes before publishing; no commit or push was performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
