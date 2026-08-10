"""Evaluation-only extraction contract for learner-stated axis evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scripts.effort_sweep_support import PreparedCall, Usage, usage_from_record

EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "depth": {
            "type": "object",
            "properties": {
                "choice_or_target_span": {"type": "string"},
                "cost_or_tension_span": {"type": "string"},
                "connection_span": {"type": "string"},
            },
            "required": [
                "choice_or_target_span",
                "cost_or_tension_span",
                "connection_span",
            ],
            "additionalProperties": False,
        },
        "boundaries": {
            "type": "object",
            "properties": {
                "trigger_or_mistake_span": {"type": "string"},
                "harm_or_incorrect_behavior_span": {"type": "string"},
                "connection_span": {"type": "string"},
            },
            "required": [
                "trigger_or_mistake_span",
                "harm_or_incorrect_behavior_span",
                "connection_span",
            ],
            "additionalProperties": False,
        },
    },
    "required": ["depth", "boundaries"],
    "additionalProperties": False,
}

EVIDENCE_RUBRIC = """\
Extract learner-stated evidence without grading it.

Use only text inside LEARNER ANSWER blocks. TOPIC is context, not learner evidence.
For each axis, copy the smallest exact contiguous learner-text spans that identify
both endpoints and a span that explicitly connects them.

Depth needs a choice, target, or approach connected to a cost, sacrifice, tension,
or opposing benefit. Boundaries needs a trigger, action, exception, limitation, or
mistake connected to concrete harm or incorrect behavior.

The connection span must itself contain both endpoint spans. Do not paraphrase,
infer an unstated endpoint, reverse a prescription into a failure, or treat option
selection as a cost or harm. Use empty strings for all three fields when no explicit
qualifying relationship exists. Return only the required schema.\
"""


@dataclass(frozen=True)
class RelationshipEvidence:
    first_span: str
    second_span: str
    connection_span: str
    eligible: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class Result:
    index: int
    case: str
    expected_depth_eligible: bool | None
    expected_boundaries_eligible: bool | None
    depth: RelationshipEvidence
    boundaries: RelationshipEvidence
    raw: dict[str, Any]
    usage: Usage
    resumed: bool = False


def learner_answers(case: dict[str, Any]) -> tuple[str, ...]:
    answers = [str(case.get("answer", ""))]
    follow_up = str(case.get("follow_up_answer", ""))
    if follow_up:
        answers.append(follow_up)
    return tuple(answer for answer in answers if answer)


def build_completion(
    case: dict[str, Any], *, model: str, effort: str | None
) -> dict[str, Any]:
    transcript = [f"TOPIC: {case['topic']}"]
    transcript.extend(
        f"LEARNER ANSWER {index}: {answer}"
        for index, answer in enumerate(learner_answers(case), 1)
    )
    return {
        "model": model,
        "effort": effort,
        "rubric": EVIDENCE_RUBRIC,
        "user_content": "\n".join(transcript),
        "schema": EVIDENCE_SCHEMA,
        "max_tokens": 512,
    }


def expected_eligibility(case: dict[str, Any]) -> tuple[bool | None, bool | None]:
    depth = case.get("expected_depth")
    boundaries = case.get("expected_boundaries")
    return (
        None if depth is None else int(depth) >= 3,
        None if boundaries is None else int(boundaries) >= 3,
    )


def _relationship(
    raw: Any,
    *,
    answers: tuple[str, ...],
    first_key: str,
    second_key: str,
) -> RelationshipEvidence:
    if not isinstance(raw, dict):
        return RelationshipEvidence("", "", "", False, ("relationship is not an object",))

    values: list[str] = []
    errors: list[str] = []
    for key in (first_key, second_key, "connection_span"):
        value = raw.get(key, "")
        if not isinstance(value, str):
            errors.append(f"{key} is not a string")
            value = ""
        values.append(value.strip())
    first, second, connection = values

    if not any(values):
        return RelationshipEvidence(first, second, connection, False, tuple(errors))
    if not all(values):
        errors.append("relationship is incomplete")

    if connection and not any(connection in answer for answer in answers):
        errors.append("connection_span is not exact learner text")
    if first and first not in connection:
        errors.append(f"{first_key} is not contained in connection_span")
    if second and second not in connection:
        errors.append(f"{second_key} is not contained in connection_span")

    return RelationshipEvidence(
        first,
        second,
        connection,
        eligible=bool(all(values) and not errors),
        errors=tuple(errors),
    )


def parse_result(
    case: dict[str, Any],
    data: dict[str, Any],
    *,
    usage: Usage,
    index: int = 0,
    case_name: str | None = None,
    resumed: bool = False,
) -> Result:
    answers = learner_answers(case)
    expected_depth, expected_boundaries = expected_eligibility(case)
    return Result(
        index=index,
        case=case_name or str(case.get("name") or case.get("topic") or "case"),
        expected_depth_eligible=expected_depth,
        expected_boundaries_eligible=expected_boundaries,
        depth=_relationship(
            data.get("depth"),
            answers=answers,
            first_key="choice_or_target_span",
            second_key="cost_or_tension_span",
        ),
        boundaries=_relationship(
            data.get("boundaries"),
            answers=answers,
            first_key="trigger_or_mistake_span",
            second_key="harm_or_incorrect_behavior_span",
        ),
        raw=data,
        usage=usage,
        resumed=resumed,
    )


def result_payload(result: Result) -> dict[str, Any]:
    return {
        "expected_depth_eligible": result.expected_depth_eligible,
        "expected_boundaries_eligible": result.expected_boundaries_eligible,
        "raw": result.raw,
        "depth": {
            "first_span": result.depth.first_span,
            "second_span": result.depth.second_span,
            "connection_span": result.depth.connection_span,
            "eligible": result.depth.eligible,
            "errors": list(result.depth.errors),
        },
        "boundaries": {
            "first_span": result.boundaries.first_span,
            "second_span": result.boundaries.second_span,
            "connection_span": result.boundaries.connection_span,
            "eligible": result.boundaries.eligible,
            "errors": list(result.boundaries.errors),
        },
    }


def result_from_record(prepared: PreparedCall, record: dict[str, Any]) -> Result:
    try:
        return parse_result(
            prepared.case,
            record["result"]["raw"],
            usage=usage_from_record(record),
            index=prepared.index,
            case_name=prepared.case_name,
            resumed=True,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"invalid saved structured-evidence result for {prepared.case_name}"
        ) from exc


def gate_failures(by_level: dict[str, list[Result]]) -> list[str]:
    failures: list[str] = []
    for level, results in by_level.items():
        for result in results:
            expected = (
                result.expected_depth_eligible,
                result.expected_boundaries_eligible,
            )
            if any(value is None for value in expected):
                failures.append(f"{level}/{result.case}: missing reviewed eligibility label")
                continue
            for axis, evidence, wanted in (
                ("depth", result.depth, expected[0]),
                ("boundaries", result.boundaries, expected[1]),
            ):
                if evidence.errors:
                    failures.append(
                        f"{level}/{result.case}: {axis} invalid exact-span evidence "
                        f"({'; '.join(evidence.errors)})"
                    )
                if evidence.eligible != wanted:
                    failures.append(
                        f"{level}/{result.case}: {axis} eligible={evidence.eligible} "
                        f"expected={wanted}"
                    )
    return failures


def print_summary(by_level: dict[str, list[Result]]) -> None:
    print("\n=== structured-evidence summary ===")
    for level, results in by_level.items():
        depth_exact = sum(
            result.depth.eligible == result.expected_depth_eligible for result in results
        )
        boundaries_exact = sum(
            result.boundaries.eligible == result.expected_boundaries_eligible
            for result in results
        )
        invalid = sum(
            bool(result.depth.errors or result.boundaries.errors) for result in results
        )
        print(
            f"  {level:<10} depth={depth_exact}/{len(results)} "
            f"boundaries={boundaries_exact}/{len(results)} invalid={invalid}"
        )
