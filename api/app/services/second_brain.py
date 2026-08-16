"""Render and exchange distilled learning notes with the second-brain vault.

The hosted API must never know where a user's local Obsidian vault lives.  This
module therefore has two deliberately separate halves:

* ``render_learning_note`` is pure and is safe for routers or completion services.
* ``build_learning_writeback_bundle`` produces the provider-neutral handoff.
* ``write_learning_note`` remains as a legacy local adapter for library callers;
  the companion CLI no longer writes directly into a vault.

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
import uuid
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

LEARNING_WRITEBACK_SCHEMA = "second-brain.learning-writeback"
LEARNING_WRITEBACK_SCHEMA_VERSION = 1

ANSWER_RUBRIC_FIELDS = (
    "mechanism",
    "acceptable_alternative",
    "trade_off",
    "failure_mode",
    "misconception",
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
    "answer_rubric",
    "card_id",
    "canonical_question",
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
    "scored_follow_up_used",
    "scoring_contract_version",
    "session_id",
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


def canonical_json_bytes(value: object) -> bytes:
    """Encode one JSON value with the exact byte contract used for stable IDs."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LearningNoteError(f"writeback bundle is not canonical JSON: {exc}") from exc


def _content_id(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def reject_raw_export_fields(value: object, *, path: str = "artifact") -> None:
    """Reject raw source and learner-answer fields at every untrusted boundary."""

    if isinstance(value, Mapping):
        forbidden = sorted(
            str(key) for key in value if isinstance(key, str) and key in _RAW_SOURCE_FIELDS
        )
        if forbidden:
            raise LearningNoteError(
                f"raw source fields are not exportable at {path}: " + ", ".join(forbidden)
            )
        for key, child in value.items():
            reject_raw_export_fields(child, path=f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            reject_raw_export_fields(child, path=f"{path}[{index}]")


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


def _uuid_text(value: object, *, field: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise LearningNoteError(f"{field} must be a UUID") from exc


def _positive_version(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LearningNoteError(f"{field} must be a positive integer")
    return value


def _reviewed_at(value: object, *, field: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise LearningNoteError(f"{field} must be an ISO datetime") from exc
    else:
        raise LearningNoteError(f"{field} must be an ISO datetime")
    if parsed.tzinfo is None:
        raise LearningNoteError(f"{field} must include a timezone")
    normalized = parsed.isoformat()
    return normalized[:-6] + "Z" if normalized.endswith("+00:00") else normalized


def _answer_rubric(value: object, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise LearningNoteError(f"{field} must be an object")
    unknown = sorted(set(value) - set(ANSWER_RUBRIC_FIELDS))
    missing = [name for name in ANSWER_RUBRIC_FIELDS if name not in value]
    if unknown or missing:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise LearningNoteError(
            f"{field} must contain the five rubric fields: " + "; ".join(details)
        )
    return {
        name: _nonempty_text(value[name], field=f"{field}.{name}", max_length=4000)
        for name in ANSWER_RUBRIC_FIELDS
    }


def _bundle_candidate(
    *,
    proposal_id: str,
    level: str,
    question: str,
    answer_rubric: str,
) -> dict[str, Any]:
    return {
        "id": f"devmax:probe:{proposal_id}:{level}",
        "type": level,
        "prompt": question,
        "answer_rubric": answer_rubric,
    }


def _candidate_rubrics(rubric: Mapping[str, str]) -> dict[str, str]:
    """Project the five-field authority into one concise criterion per probe."""

    return {
        "definition_recognition": (
            f"Recognize the concept without this misconception: {rubric['misconception']}"
        ),
        "mechanism": rubric["mechanism"],
        "derivation": (
            f"Derive the mechanism and its cost: {rubric['mechanism']} "
            f"Trade-off: {rubric['trade_off']}"
        ),
        "application": (
            f"Apply an accurate account; this alternative is acceptable: "
            f"{rubric['acceptable_alternative']}"
        ),
        "failure_tradeoff": (
            f"Failure mode: {rubric['failure_mode']} Trade-off: {rubric['trade_off']}"
        ),
    }


def build_learning_writeback_bundle(
    *,
    source_id: object,
    source_lineage_id: object,
    source_version: object,
    source_title: object,
    source_url: object,
    source_distilled_at: object,
    concepts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the privacy-bounded, content-addressed lesson handoff.

    This is an event and knowledge bundle, not a synchronization of the live
    scheduler. Raw source text, learner answers, intervals, due dates, and live
    mastery summaries are deliberately absent.
    """

    if isinstance(concepts, (str, bytes)) or not concepts:
        raise LearningNoteError("writeback concepts must contain at least one concept")
    source = {
        "id": f"devmax:source:{_uuid_text(source_id, field='source.id')}",
        "lineage_id": (
            "devmax:source-lineage:"
            + _uuid_text(source_lineage_id, field="source.lineage_id")
        ),
        "version": _positive_version(source_version, field="source.version"),
        "title": _nonempty_text(source_title, field="source.title", max_length=500),
        "url": _validate_url(
            _optional_text(source_url, field="source.url", max_length=4000)
        ),
        "distilled_at": _reviewed_at(
            source_distilled_at, field="source.distilled_at"
        ),
    }

    rows: list[dict[str, Any]] = []
    for concept_index, concept in enumerate(concepts):
        if not isinstance(concept, Mapping):
            raise LearningNoteError(f"concepts[{concept_index}] must be an object")
        reject_raw_export_fields(concept, path=f"concepts[{concept_index}]")
        proposal_id = _uuid_text(
            concept.get("proposal_id"), field=f"concepts[{concept_index}].proposal_id"
        )
        card_id = _uuid_text(
            concept.get("card_id"), field=f"concepts[{concept_index}].card_id"
        )
        prompts = _parse_recall_prompts(concept.get("recall_prompts"))
        rubric = _answer_rubric(
            concept.get("answer_rubric"),
            field=f"concepts[{concept_index}].answer_rubric",
        )
        candidate_rubrics = _candidate_rubrics(rubric)
        candidates = [
            _bundle_candidate(
                proposal_id=proposal_id,
                level=level,
                question=prompts[level],
                answer_rubric=candidate_rubrics[level],
            )
            for level in RECALL_LEVELS
        ]

        quiz_value = concept.get("quiz_results")
        if (
            not isinstance(quiz_value, Sequence)
            or isinstance(quiz_value, (str, bytes))
            or not 1 <= len(quiz_value) <= 20
        ):
            raise LearningNoteError(
                f"concepts[{concept_index}].quiz_results must contain 1 to 20 rows"
            )
        evidence: list[dict[str, Any]] = []
        for quiz_index, quiz in enumerate(quiz_value):
            if not isinstance(quiz, Mapping):
                raise LearningNoteError(
                    f"concepts[{concept_index}].quiz_results[{quiz_index}] must be an object"
                )
            prefix = f"concepts[{concept_index}].quiz_results[{quiz_index}]"
            contract = quiz.get("scoring_contract_version")
            if isinstance(contract, bool) or contract not in {1, 2}:
                raise LearningNoteError(f"{prefix}.scoring_contract_version must be 1 or 2")
            follow_up = quiz.get("scored_follow_up_used")
            if not isinstance(follow_up, bool):
                raise LearningNoteError(f"{prefix}.scored_follow_up_used must be boolean")
            recall_score = _score(
                quiz.get("recall_score"), field=f"{prefix}.recall_score"
            )
            evidence.append(
                {
                    "id": "devmax:session:"
                    + _uuid_text(quiz.get("session_id"), field=f"{prefix}.session_id"),
                    "scoring_contract_version": contract,
                    "score": recall_score,
                    "scored_follow_up_used": follow_up,
                    "reviewed_at": _reviewed_at(
                        quiz.get("reviewed_at"), field=f"{prefix}.reviewed_at"
                    ),
                    "prompt": _nonempty_text(
                        quiz.get("question"), field=f"{prefix}.question", max_length=2000
                    ),
                    "graded_summary": _nonempty_text(
                        quiz.get("graded_summary"),
                        field=f"{prefix}.graded_summary",
                        max_length=4000,
                    ),
                }
            )

        assessment = _nonempty_text(
            concept.get("confidence"),
            field=f"concepts[{concept_index}].producer_assessment",
            max_length=32,
        )
        if assessment not in {"unrated", "needs_review", "developing", "established"}:
            raise LearningNoteError(
                f"concepts[{concept_index}].producer_assessment is invalid"
            )
        gotchas_value = concept.get("gotchas")
        if (
            not isinstance(gotchas_value, Sequence)
            or isinstance(gotchas_value, (str, bytes))
            or not 1 <= len(gotchas_value) <= 8
        ):
            raise LearningNoteError(
                f"concepts[{concept_index}].gotchas must contain 1 to 8 strings"
            )
        rows.append(
            {
                "id": f"devmax:proposal:{proposal_id}",
                "card_id": f"devmax:card:{card_id}",
                "title": _nonempty_text(
                    concept.get("concept"),
                    field=f"concepts[{concept_index}].concept",
                    max_length=200,
                ),
                "answer_rubric": rubric,
                "mental_model": _nonempty_text(
                    concept.get("mental_model"),
                    field=f"concepts[{concept_index}].mental_model",
                    max_length=2000,
                ),
                "how_it_works": _nonempty_text(
                    concept.get("how_it_works"),
                    field=f"concepts[{concept_index}].how_it_works",
                    max_length=4000,
                ),
                "gotchas": [
                    _nonempty_text(
                        value,
                        field=f"concepts[{concept_index}].gotchas[{gotcha_index}]",
                        max_length=1000,
                    )
                    for gotcha_index, value in enumerate(gotchas_value)
                ],
                "recall_candidates": candidates,
                "quiz_evidence": evidence,
                "producer_assessment": assessment,
            }
        )

    core: dict[str, Any] = {
        "schema": LEARNING_WRITEBACK_SCHEMA,
        "schema_version": LEARNING_WRITEBACK_SCHEMA_VERSION,
        "producer": "devmax",
        "source": source,
        "concepts": rows,
    }
    return {**core, "export_id": _content_id(core)}


def validate_learning_writeback_bundle(bundle: object) -> dict[str, Any]:
    """Strictly validate and normalize a bundle received by the local CLI."""

    if not isinstance(bundle, Mapping):
        raise LearningNoteError("writeback_bundle must be an object")
    reject_raw_export_fields(bundle, path="writeback_bundle")
    expected_top = {
        "schema",
        "schema_version",
        "producer",
        "export_id",
        "source",
        "concepts",
    }
    if set(bundle) != expected_top:
        raise LearningNoteError("writeback_bundle has missing or unknown fields")
    if bundle.get("schema") != LEARNING_WRITEBACK_SCHEMA:
        raise LearningNoteError(
            f"writeback_bundle.schema must be {LEARNING_WRITEBACK_SCHEMA}"
        )
    if bundle.get("schema_version") != LEARNING_WRITEBACK_SCHEMA_VERSION:
        raise LearningNoteError(
            f"writeback_bundle.schema_version must be {LEARNING_WRITEBACK_SCHEMA_VERSION}"
        )
    if bundle.get("producer") != "devmax":
        raise LearningNoteError("writeback_bundle.producer must be devmax")
    source = bundle.get("source")
    if not isinstance(source, Mapping) or set(source) != {
        "id",
        "lineage_id",
        "version",
        "title",
        "url",
        "distilled_at",
    }:
        raise LearningNoteError("writeback_bundle.source has missing or unknown fields")
    concepts = bundle.get("concepts")
    if not isinstance(concepts, Sequence) or isinstance(concepts, (str, bytes)):
        raise LearningNoteError("writeback_bundle.concepts must be a list")

    builder_concepts: list[dict[str, Any]] = []
    expected_concept = {
        "id",
        "card_id",
        "title",
        "answer_rubric",
        "mental_model",
        "how_it_works",
        "gotchas",
        "recall_candidates",
        "quiz_evidence",
        "producer_assessment",
    }
    for index, concept in enumerate(concepts):
        if not isinstance(concept, Mapping) or set(concept) != expected_concept:
            raise LearningNoteError(
                f"writeback_bundle.concepts[{index}] has missing or unknown fields"
            )
        candidates = concept.get("recall_candidates")
        evidence = concept.get("quiz_evidence")
        if not isinstance(candidates, list) or any(
            not isinstance(row, Mapping)
            or set(row) != {"id", "type", "prompt", "answer_rubric"}
            for row in candidates
        ):
            raise LearningNoteError(
                f"writeback_bundle.concepts[{index}].recall_candidates is invalid"
            )
        if not isinstance(evidence, list) or any(
            not isinstance(row, Mapping)
            or set(row)
            != {
                "id",
                "scoring_contract_version",
                "score",
                "scored_follow_up_used",
                "reviewed_at",
                "prompt",
                "graded_summary",
            }
            for row in evidence
        ):
            raise LearningNoteError(
                f"writeback_bundle.concepts[{index}].quiz_evidence is invalid"
            )
        builder_concepts.append(
            {
                "proposal_id": str(concept["id"]).removeprefix("devmax:proposal:"),
                "card_id": str(concept["card_id"]).removeprefix("devmax:card:"),
                "concept": concept["title"],
                "answer_rubric": concept["answer_rubric"],
                "mental_model": concept["mental_model"],
                "how_it_works": concept["how_it_works"],
                "gotchas": concept["gotchas"],
                "confidence": concept["producer_assessment"],
                "recall_prompts": [
                    {"level": row["type"], "question": row["prompt"]}
                    for row in candidates
                ],
                "quiz_results": [
                    {
                        "session_id": str(row["id"]).removeprefix("devmax:session:"),
                        "scoring_contract_version": row["scoring_contract_version"],
                        "recall_score": row["score"],
                        "scored_follow_up_used": row["scored_follow_up_used"],
                        "reviewed_at": row["reviewed_at"],
                        "question": row["prompt"],
                        "graded_summary": row["graded_summary"],
                    }
                    for row in evidence
                ],
            }
        )

    rebuilt = build_learning_writeback_bundle(
        source_id=str(source.get("id")).removeprefix("devmax:source:"),
        source_lineage_id=str(source.get("lineage_id")).removeprefix(
            "devmax:source-lineage:"
        ),
        source_version=source.get("version"),
        source_title=source.get("title"),
        source_url=source.get("url"),
        source_distilled_at=source.get("distilled_at"),
        concepts=builder_concepts,
    )
    if dict(bundle) != rebuilt:
        raise LearningNoteError(
            "writeback_bundle identifiers or export_id do not match canonical JSON"
        )
    return rebuilt


def _first_present(row: Mapping[str, Any], *names: str, default: object = None) -> object:
    for name in names:
        if name in row:
            return row[name]
    return default


def artifact_from_dict(payload: Mapping[str, Any]) -> LearningNoteArtifact:
    """Strictly parse the JSON-friendly artifact shape used by the API and CLI."""

    if not isinstance(payload, Mapping):
        raise LearningNoteError("artifact must be a JSON object")

    reject_raw_export_fields(payload)

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
        label += f" · {row.feedback}"
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
        source_line += f": <{value.source_url}>"

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
    index_entry = f"- [[{slug}]]: graded learning note on {_inline(value.concept)}"
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
    expected_prefix = f"- [[{Path(note.filename).stem}]]: "
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
