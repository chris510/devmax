"""Render and locally write distilled learning notes for the second-brain vault.

The hosted API must never know where a user's local Obsidian vault lives.  This
module therefore has two deliberately separate halves:

* ``render_learning_note`` is pure and is safe for routers or completion services.
* ``write_learning_note`` is a local-only adapter used by the companion CLI.

The input schema contains distilled fields only.  Raw page text, HTML, pasted
source content, and answer-basis dumps are rejected at the dictionary boundary.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

RECALL_LEVELS = (
    "definition_recognition",
    "mechanism",
    "derivation",
    "application",
    "failure_tradeoff",
)

RECALL_LABELS = {
    "definition_recognition": "Definition / recognition",
    "mechanism": "Mechanism",
    "derivation": "Derivation",
    "application": "Application",
    "failure_tradeoff": "Failure / trade-off",
}

_RECALL_LEVEL_ALIASES = {
    "definition": "definition_recognition",
    "recognition": "definition_recognition",
    "definition_recognition": "definition_recognition",
    "definition/recognition": "definition_recognition",
    "mechanism": "mechanism",
    "derivation": "derivation",
    "application": "application",
    "failure": "failure_tradeoff",
    "tradeoff": "failure_tradeoff",
    "trade_off": "failure_tradeoff",
    "failure_tradeoff": "failure_tradeoff",
    "failure_trade_off": "failure_tradeoff",
    "failure/tradeoff": "failure_tradeoff",
    "failure/trade-off": "failure_tradeoff",
}

_ARTIFACT_FIELDS = {
    "card_id",
    "concept",
    "confidence",
    "source_url",
    "source_title",
    "mental_model",
    "how_it_works",
    "gotchas",
    "proposal_id",
    "recall_prompts",
    "quiz_results",
    "score",
    "reviewed_on",
}
_QUIZ_FIELDS = {
    "coached",
    "date",
    "feedback",
    "graded_summary",
    "prompt",
    "question",
    "question_text",
    "recall_score",
    "reviewed_at",
    "reviewed_on",
    "score",
}
_RAW_SOURCE_FIELDS = {
    "answer",
    "answer_basis",
    "answer_text",
    "content",
    "html",
    "page_text",
    "pasted_text",
    "raw",
    "raw_source",
    "source_content",
    "source_excerpt",
    "source_text",
    "transcript",
}


class LearningNoteError(ValueError):
    """The distilled export artifact is invalid."""


class VaultWriteError(RuntimeError):
    """The local vault cannot be safely updated."""


class VaultConflictError(VaultWriteError):
    """The vault has state that requires a human merge or cleanup."""


@dataclass(frozen=True)
class QuizResult:
    date: date
    question: str
    evidence: str
    score: int
    feedback: str = ""
    coached: bool = False


@dataclass(frozen=True)
class LearningNoteArtifact:
    """One concept's distilled, graded learning artifact.

    ``score`` is the concept's latest unaided score.  Coached quiz rows may be
    retained as honest history, but they do not determine ``confidence``.
    """

    concept: str
    source_title: str
    mental_model: str
    how_it_works: str
    gotchas: tuple[str, ...]
    recall_prompts: Mapping[str, str]
    quiz_results: tuple[QuizResult, ...]
    score: int
    reviewed_on: date
    source_url: str = ""


@dataclass(frozen=True)
class RenderedLearningNote:
    filename: str
    markdown: str
    confidence: str
    index_entry: str
    export_id: str


@dataclass(frozen=True)
class VaultWriteResult:
    note_path: Path
    index_path: Path
    log_path: Path
    export_id: str


@dataclass(frozen=True)
class VaultBatchWriteResult:
    note_paths: tuple[Path, ...]
    index_path: Path
    log_path: Path
    export_id: str


def _nonempty_text(value: object, *, field: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise LearningNoteError(f"{field} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise LearningNoteError(f"{field} must not be empty")
    if len(cleaned) > max_length:
        raise LearningNoteError(f"{field} exceeds {max_length} characters")
    if "\x00" in cleaned:
        raise LearningNoteError(f"{field} contains a null byte")
    return cleaned


def _optional_text(value: object, *, field: str, max_length: int) -> str:
    if value in (None, ""):
        return ""
    return _nonempty_text(value, field=field, max_length=max_length)


def _score(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 5:
        raise LearningNoteError(f"{field} must be an integer from 0 to 5")
    return value


def _date(value: object, *, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
            except ValueError:
                raise LearningNoteError(f"{field} must be an ISO date") from exc
    raise LearningNoteError(f"{field} must be an ISO date")


def _validate_url(value: str) -> str:
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise LearningNoteError("source_url must be an absolute http(s) URL")
    if parts.username or parts.password:
        raise LearningNoteError("source_url must not contain credentials")
    if any(character.isspace() for character in value) or any(
        character in value for character in "<>"
    ):
        raise LearningNoteError("source_url contains unsafe whitespace or delimiters")
    return value


def _canonical_recall_level(value: object, *, field: str) -> str:
    label = _nonempty_text(value, field=field, max_length=100)
    normalized = re.sub(r"[ -]+", "_", label.casefold().strip())
    canonical = _RECALL_LEVEL_ALIASES.get(normalized)
    if canonical is None:
        canonical = _RECALL_LEVEL_ALIASES.get(label.casefold().strip())
    if canonical is None:
        raise LearningNoteError(f"{field} is not a supported recall level: {label}")
    return canonical


def _parse_recall_prompts(value: object) -> dict[str, str]:
    rows: list[tuple[object, object]]
    if isinstance(value, Mapping):
        rows = list(value.items())
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        rows = []
        for idx, row in enumerate(value):
            if not isinstance(row, Mapping):
                raise LearningNoteError(f"recall_prompts[{idx}] must be an object")
            unknown = sorted(set(row) - {"level", "question"})
            if unknown:
                raise LearningNoteError(
                    f"unknown recall_prompts[{idx}] fields: " + ", ".join(unknown)
                )
            rows.append((row.get("level"), row.get("question")))
    else:
        raise LearningNoteError(
            "recall_prompts must be a level mapping or five {level, question} rows"
        )

    parsed: dict[str, str] = {}
    for idx, (level_value, question_value) in enumerate(rows):
        level = _canonical_recall_level(level_value, field=f"recall_prompts[{idx}].level")
        if level in parsed:
            raise LearningNoteError(f"recall_prompts repeats level {level}")
        parsed[level] = _nonempty_text(
            question_value, field=f"recall_prompts.{level}", max_length=1000
        )

    missing_levels = [level for level in RECALL_LEVELS if level not in parsed]
    if missing_levels or len(parsed) != len(RECALL_LEVELS):
        raise LearningNoteError(
            "recall_prompts must contain exactly five levels; missing "
            + ", ".join(missing_levels)
        )
    return {level: parsed[level] for level in RECALL_LEVELS}


def _first_present(row: Mapping[str, Any], *names: str, default: object = None) -> object:
    for name in names:
        if name in row:
            return row[name]
    return default


def artifact_from_dict(payload: Mapping[str, Any]) -> LearningNoteArtifact:
    """Strictly parse the JSON-friendly artifact shape used by the API and CLI."""

    if not isinstance(payload, Mapping):
        raise LearningNoteError("artifact must be a JSON object")

    raw_fields = sorted(set(payload) & _RAW_SOURCE_FIELDS)
    if raw_fields:
        raise LearningNoteError(
            "raw source fields are not exportable: " + ", ".join(raw_fields)
        )
    unknown = sorted(set(payload) - _ARTIFACT_FIELDS)
    if unknown:
        raise LearningNoteError("unknown artifact fields: " + ", ".join(unknown))

    concept = _nonempty_text(payload.get("concept"), field="concept", max_length=200)
    source_title = _nonempty_text(
        payload.get("source_title"), field="source_title", max_length=500
    )
    source_url = _validate_url(
        _optional_text(payload.get("source_url"), field="source_url", max_length=4000)
    )
    mental_model = _nonempty_text(
        payload.get("mental_model"), field="mental_model", max_length=2000
    )
    how_it_works = _nonempty_text(
        payload.get("how_it_works"), field="how_it_works", max_length=4000
    )

    gotchas_value = payload.get("gotchas")
    if (
        not isinstance(gotchas_value, Sequence)
        or isinstance(gotchas_value, (str, bytes))
        or not 1 <= len(gotchas_value) <= 8
    ):
        raise LearningNoteError("gotchas must contain 1 to 8 concise strings")
    gotchas = tuple(
        _nonempty_text(item, field=f"gotchas[{idx}]", max_length=1000)
        for idx, item in enumerate(gotchas_value)
    )

    recall_prompts = _parse_recall_prompts(payload.get("recall_prompts"))

    quiz_value = payload.get("quiz_results")
    if isinstance(quiz_value, Mapping):
        quiz_value = [quiz_value]
    if (
        not isinstance(quiz_value, Sequence)
        or isinstance(quiz_value, (str, bytes))
        or not 1 <= len(quiz_value) <= 20
    ):
        raise LearningNoteError("quiz_results must contain 1 to 20 graded answers")

    overall_score_value = payload.get("score")
    reviewed_on_value = payload.get("reviewed_on")
    quiz_results: list[QuizResult] = []
    for idx, row in enumerate(quiz_value):
        if not isinstance(row, Mapping):
            raise LearningNoteError(f"quiz_results[{idx}] must be an object")
        unknown_quiz = sorted(set(row) - _QUIZ_FIELDS)
        if unknown_quiz:
            raise LearningNoteError(
                f"unknown quiz_results[{idx}] fields: " + ", ".join(unknown_quiz)
            )
        coached = row.get("coached", False)
        if not isinstance(coached, bool):
            raise LearningNoteError(f"quiz_results[{idx}].coached must be boolean")
        quiz_results.append(
            QuizResult(
                date=_date(
                    _first_present(
                        row,
                        "date",
                        "reviewed_on",
                        "reviewed_at",
                        default=reviewed_on_value,
                    ),
                    field=f"quiz_results[{idx}].date",
                ),
                question=_nonempty_text(
                    _first_present(row, "question", "question_text", "prompt"),
                    field=f"quiz_results[{idx}].question",
                    max_length=2000,
                ),
                evidence=_nonempty_text(
                    _first_present(row, "graded_summary", "feedback"),
                    field=f"quiz_results[{idx}].graded_summary",
                    max_length=4000,
                ),
                score=_score(
                    _first_present(
                        row, "score", "recall_score", default=overall_score_value
                    ),
                    field=f"quiz_results[{idx}].score",
                ),
                feedback=_optional_text(
                    row.get("feedback"),
                    field=f"quiz_results[{idx}].feedback",
                    max_length=2000,
                ),
                coached=coached,
            )
        )

    unaided_results = [row for row in quiz_results if not row.coached]
    if not unaided_results:
        raise LearningNoteError("at least one unaided quiz result is required for export")
    if overall_score_value is None:
        overall_score = unaided_results[-1].score
    else:
        overall_score = _score(overall_score_value, field="score")

    if reviewed_on_value is None:
        reviewed_on = max(row.date for row in quiz_results)
    else:
        reviewed_on = _date(reviewed_on_value, field="reviewed_on")

    return LearningNoteArtifact(
        concept=concept,
        source_title=source_title,
        source_url=source_url,
        mental_model=mental_model,
        how_it_works=how_it_works,
        gotchas=gotchas,
        recall_prompts=recall_prompts,
        quiz_results=tuple(quiz_results),
        score=overall_score,
        reviewed_on=reviewed_on,
    )


def confidence_for_score(score: int) -> str:
    """Map the latest unaided 0-5 result to the vault's confidence vocabulary."""

    value = _score(score, field="score")
    if value <= 2:
        return "shaky"
    if value <= 4:
        return "solid"
    return "teachable"


def slugify_concept(concept: str) -> str:
    normalized = unicodedata.normalize("NFKD", concept)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    slug = slug[:96].rstrip("-")
    if not slug:
        raise LearningNoteError("concept must produce a non-empty kebab-case filename")
    return slug


def _yaml_string(value: str) -> str:
    # JSON double-quoted strings are a strict subset of YAML strings.
    return json.dumps(value, ensure_ascii=False)


def _inline(value: str) -> str:
    return " ".join(value.split())


def _table_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _quiz_grade(row: QuizResult) -> str:
    label = f"Score {row.score}/5"
    if row.coached:
        label += " · coached"
    if row.feedback and row.feedback != row.evidence:
        label += f" — {row.feedback}"
    return f"{row.evidence}<br>_{label}_"


def render_learning_note(
    artifact: LearningNoteArtifact | Mapping[str, Any],
) -> RenderedLearningNote:
    """Render one concept into a deterministic, vault-compatible learning note."""

    value = artifact_from_dict(artifact) if isinstance(artifact, Mapping) else artifact
    if not isinstance(value, LearningNoteArtifact):
        raise LearningNoteError("artifact must be LearningNoteArtifact or a mapping")

    # Re-parse dataclass callers through the same bounds as JSON callers.  This
    # avoids creating a privileged path that can smuggle a page dump into a note.
    payload = {
        "concept": value.concept,
        "source_url": value.source_url,
        "source_title": value.source_title,
        "mental_model": value.mental_model,
        "how_it_works": value.how_it_works,
        "gotchas": list(value.gotchas),
        "recall_prompts": dict(value.recall_prompts),
        "quiz_results": [
            {
                "date": row.date,
                "question": row.question,
                "graded_summary": row.evidence,
                "score": row.score,
                "feedback": row.feedback,
                "coached": row.coached,
            }
            for row in value.quiz_results
        ],
        "score": value.score,
        "reviewed_on": value.reviewed_on,
    }
    value = artifact_from_dict(payload)

    confidence = confidence_for_score(value.score)
    slug = slugify_concept(value.concept)
    source = value.source_url or value.source_title
    source_line = f"> Source: {_inline(value.source_title)}"
    if value.source_url:
        source_line += f" — <{value.source_url}>"

    lines = [
        "---",
        "type: learning",
        f"source: {_yaml_string(source)}",
        f"created: {value.reviewed_on.isoformat()}",
        f"confidence: {confidence}",
        "tags: []",
        "---",
        "",
        f"# Learning: {_inline(value.concept)}",
        "",
        source_line,
        (
            "> Distilled from a graded Devmax session; raw source content is "
            "intentionally excluded."
        ),
        "",
        "## Mental Model",
        "",
        value.mental_model,
        "",
        "## How It Works",
        "",
        value.how_it_works,
        "",
        "## Gotchas",
        "",
    ]
    lines.extend(f"- {gotcha}" for gotcha in value.gotchas)
    lines.extend(["", "## Recall Prompts", ""])
    lines.extend(
        f"- **{RECALL_LABELS[level]}:** {value.recall_prompts[level]}"
        for level in RECALL_LEVELS
    )
    lines.extend(
        [
            "",
            "## Quiz Results",
            "",
            "| Date | Question | Grade / evidence |",
            "|------|----------|--------------------|",
        ]
    )
    lines.extend(
        "| "
        + " | ".join(
            (
                row.date.isoformat(),
                _table_cell(row.question),
                _table_cell(_quiz_grade(row)),
            )
        )
        + " |"
        for row in value.quiz_results
    )
    lines.extend(["", "## Related", "", "- [[interview-prep]]", ""])

    markdown = "\n".join(lines)
    export_id = hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:20]
    index_entry = f"- [[{slug}]] — graded learning note on {_inline(value.concept)}"
    return RenderedLearningNote(
        filename=f"{slug}.md",
        markdown=markdown,
        confidence=confidence,
        index_entry=index_entry,
        export_id=export_id,
    )


def render_learning_notes(
    artifacts: Sequence[LearningNoteArtifact | Mapping[str, Any]],
) -> tuple[RenderedLearningNote, ...]:
    """Render a lesson's concepts and reject filename collisions before writing."""

    if isinstance(artifacts, (str, bytes)) or not artifacts:
        raise LearningNoteError("artifacts must contain at least one concept")
    rendered = tuple(render_learning_note(artifact) for artifact in artifacts)
    _reject_duplicate_filenames(rendered)
    return rendered


def _reject_duplicate_filenames(rendered: Sequence[RenderedLearningNote]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for note in rendered:
        if note.filename in seen:
            duplicates.add(note.filename)
        seen.add(note.filename)
    if duplicates:
        raise LearningNoteError(
            "multiple concepts resolve to the same filename: "
            + ", ".join(sorted(duplicates))
        )


def _validate_rendered_note(note: RenderedLearningNote) -> None:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*\.md", note.filename):
        raise LearningNoteError(
            "rendered filename must be one kebab-case .md basename"
        )
    expected_prefix = f"- [[{Path(note.filename).stem}]] — "
    if "\n" in note.index_entry or not note.index_entry.startswith(expected_prefix):
        raise LearningNoteError("rendered index entry does not match its filename")
    if note.confidence not in {"shaky", "solid", "teachable"}:
        raise LearningNoteError("rendered confidence is invalid")
    expected_id = hashlib.sha256(note.markdown.encode("utf-8")).hexdigest()[:20]
    if note.export_id != expected_id:
        raise LearningNoteError("rendered export_id does not match its markdown")


def _git(vault: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(vault), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise VaultWriteError(f"could not inspect vault git state: {detail.strip()}") from exc
    return result.stdout.strip()


def _validate_vault(vault: Path) -> tuple[Path, Path, Path]:
    if not vault.is_dir():
        raise VaultWriteError(f"vault does not exist: {vault}")
    if not (vault / ".git").is_dir():
        raise VaultWriteError("vault must be a Git working tree")

    wiki = vault / "wiki"
    if wiki.is_symlink() or not wiki.is_dir():
        raise VaultWriteError("vault wiki directory is missing or unsafe")
    try:
        wiki.resolve().relative_to(vault)
    except ValueError as exc:
        raise VaultWriteError("vault wiki directory escapes the vault") from exc

    contract = vault / "CLAUDE.md"
    index = wiki / "_index.md"
    log = vault / "log.md"
    for path in (contract, index, log):
        if path.is_symlink() or not path.is_file():
            raise VaultWriteError(f"required vault file is missing or unsafe: {path}")

    contract_text = contract.read_text(encoding="utf-8")
    if "type: learning" not in contract_text or "wiki/" not in contract_text:
        raise VaultWriteError("CLAUDE.md does not declare the learning-note wiki contract")
    return wiki, index, log


def _require_main_and_clean(vault: Path) -> None:
    branch = _git(vault, "branch", "--show-current")
    if branch != "main":
        raise VaultConflictError(f"vault must be on main, found {branch or 'detached HEAD'}")
    status = _git(vault, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise VaultConflictError("vault has local changes; review or publish them before exporting")


@contextmanager
def _vault_lock(vault: Path):
    lock_path = vault / ".git" / "devmax-second-brain-export.lock"
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _stage_bytes(path: Path, content: bytes, *, mode: int) -> Path:
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def _replace(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _atomic_replace_files(updates: Mapping[Path, bytes]) -> None:
    """Replace several files with rollback if any replacement fails.

    Filesystems do not offer a multi-file rename transaction.  Staging each file
    beside its destination and restoring byte-for-byte originals is the narrowest
    all-or-none behavior available without mutating the vault's Git history.
    """

    originals: dict[Path, tuple[bytes | None, int]] = {}
    staged: dict[Path, Path] = {}
    committed: list[Path] = []

    try:
        for path, content in updates.items():
            if path.exists():
                originals[path] = (path.read_bytes(), path.stat().st_mode & 0o777)
            else:
                originals[path] = (None, 0o644)
            staged[path] = _stage_bytes(path, content, mode=originals[path][1])

        for path, temp_path in staged.items():
            _replace(temp_path, path)
            committed.append(path)
    except Exception as exc:
        rollback_errors: list[str] = []
        for path in reversed(committed):
            original, mode = originals[path]
            try:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    rollback = _stage_bytes(path, original, mode=mode)
                    os.replace(rollback, path)
            except Exception as rollback_exc:  # pragma: no cover - catastrophic filesystem failure
                rollback_errors.append(f"{path}: {rollback_exc}")
        detail = ""
        if rollback_errors:
            detail = "; rollback also failed: " + "; ".join(rollback_errors)
        raise VaultWriteError(f"vault write failed and was rolled back: {exc}{detail}") from exc
    finally:
        for temp_path in staged.values():
            temp_path.unlink(missing_ok=True)


def _append_line(text: str, line: str) -> str:
    return text.rstrip("\n") + "\n" + line + "\n"


def write_learning_note(
    artifact: LearningNoteArtifact | Mapping[str, Any] | RenderedLearningNote,
    vault_path: str | Path,
    *,
    now: datetime | None = None,
) -> VaultWriteResult:
    """Create one note and update the vault maps without committing or pushing.

    The explicit vault path, clean-main requirement, existing-note refusal, and
    absence of Git mutation are intentional safety boundaries.
    """

    batch = write_learning_notes((artifact,), vault_path, now=now)
    return VaultWriteResult(
        note_path=batch.note_paths[0],
        index_path=batch.index_path,
        log_path=batch.log_path,
        export_id=batch.export_id,
    )


def write_learning_notes(
    artifacts: Sequence[
        LearningNoteArtifact | Mapping[str, Any] | RenderedLearningNote
    ],
    vault_path: str | Path,
    *,
    now: datetime | None = None,
) -> VaultBatchWriteResult:
    """Create all concept notes in one checked, rollback-capable vault transaction."""

    if isinstance(artifacts, (str, bytes)) or not artifacts:
        raise LearningNoteError("artifacts must contain at least one concept")
    rendered = tuple(
        artifact
        if isinstance(artifact, RenderedLearningNote)
        else render_learning_note(artifact)
        for artifact in artifacts
    )
    for note in rendered:
        _validate_rendered_note(note)
    _reject_duplicate_filenames(rendered)

    vault = Path(vault_path).expanduser().resolve()
    wiki_path, index_path, log_path = _validate_vault(vault)
    note_paths = tuple(wiki_path / note.filename for note in rendered)
    symlinks = [path for path in note_paths if path.is_symlink()]
    if symlinks:
        raise VaultConflictError(f"refusing symlink note path: {symlinks[0]}")

    with _vault_lock(vault):
        _require_main_and_clean(vault)
        existing = [path for path in note_paths if path.exists()]
        if existing:
            raise VaultConflictError(
                f"learning note already exists; merge it deliberately: {existing[0]}"
            )

        index_text = index_path.read_text(encoding="utf-8")
        indexed = [path.stem for path in note_paths if f"[[{path.stem}]]" in index_text]
        if indexed:
            raise VaultConflictError(
                f"wiki index already contains [[{indexed[0]}]]; repair or merge it deliberately"
            )

        log_text = log_path.read_text(encoding="utf-8")
        event_time = now or datetime.now().astimezone()
        if len(rendered) == 1:
            scope = f"wiki/{rendered[0].filename}"
            summary = "Added graded Devmax learning note."
        else:
            scope = "wiki/"
            summary = f"Added {len(rendered)} graded Devmax learning notes."
        log_line = f"## [{event_time.strftime('%Y-%m-%d %H:%M')}] ingest | {scope} | {summary}"

        new_index = index_text
        for note in rendered:
            new_index = _append_line(new_index, note.index_entry)
        updates = {
            **{
                path: note.markdown.encode("utf-8")
                for path, note in zip(note_paths, rendered, strict=True)
            },
            index_path: new_index.encode("utf-8"),
            log_path: _append_line(log_text, log_line).encode("utf-8"),
        }
        _atomic_replace_files(updates)

    batch_id = rendered[0].export_id
    if len(rendered) > 1:
        batch_id = hashlib.sha256(
            "\n".join(note.export_id for note in rendered).encode("ascii")
        ).hexdigest()[:20]
    return VaultBatchWriteResult(
        note_paths=note_paths,
        index_path=index_path,
        log_path=log_path,
        export_id=batch_id,
    )
