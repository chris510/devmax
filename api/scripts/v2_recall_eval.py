"""Pure evaluation helpers for the dark Recall-only scoring contract.

The provider runners own transport, persistence, and spend controls. This module
owns the shared semantic request, human-label validation, result parsing, and
qualification gates so a provider comparison cannot quietly fork V2 behavior.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.services import llm, scheduler
from app.services.card_lifecycle import Grounding, GroundingError
from app.services.scoring_provider import (
    product_decisions as production_product_decisions,
)
from app.services.scoring_provider import (
    qualification_fingerprint,
)
from scripts.effort_sweep_support import (
    PreparedCall,
    Usage,
    case_name,
    usage_from_record,
)

KIND = "v2-recall"
FLOW_FOLLOW_UP = "follow_up"
FLOW_COMPLETE = "complete"
VALID_FLOWS = frozenset((FLOW_FOLLOW_UP, FLOW_COMPLETE))
SEMANTIC_FINGERPRINT_VERSION = 1
STAGE2_PACK_FINGERPRINT_VERSION = 1

STAGE2_MIN_CARDS = 18
STAGE2_MIN_WEEKS = 3
STAGE2_INITIAL_SCORES = frozenset((0, 1, 3, 4))
STAGE2_TERMINAL_SCORES = frozenset((1, 2, 3, 4))
STAGE2_RISK_TAGS = frozenset(
    (
        "speech-noise",
        "partial-self-correction",
        "adjacent-jargon",
        "source-compatible-alternative",
        "prior-summary-contradiction",
        "follow-up-anchored",
    )
)

_CASE_METADATA_KEYS = {"name", "tags", "review_status", "review_note"}
_TRANSPORT_COMPLETION_KEYS = {
    "model",
    "provider",
    "retry",
    "purpose",
    "cache_rubric",
    "stream",
}


@dataclass
class Result:
    index: int
    case: str
    expected_recall: int
    recall: int
    expected_flow: str
    flow: str
    semantic_fingerprint: str
    usage: Usage
    feedback: str = ""
    follow_up_question: str = ""
    needs_more_evidence: bool = False
    mastery_summary: str = ""
    resumed: bool = False

    @property
    def expected_decision(self) -> str:
        return product_decision(self.expected_flow, self.expected_recall)

    @property
    def decision(self) -> str:
        return product_decision(self.flow, self.recall)


def _text(case: dict[str, Any], key: str) -> str:
    value = case.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def case_probes(case: dict[str, Any]) -> list[tuple[str, str]]:
    """Return the exact answered probes represented by one frozen case.

    New V2 packs can represent both scored probes as an ordered list. The flat
    V1 pair remains accepted so reviewed answer shapes can be relabeled without
    a mechanical data migration.
    """
    raw = case.get("probes")
    flat_question = case.get("follow_up_question")
    flat_answer = case.get("follow_up_answer")
    if raw is not None and (flat_question is not None or flat_answer is not None):
        raise ValueError("use either probes or the flat follow-up pair, not both")

    if raw is None:
        if flat_question is None and flat_answer is None:
            return []
        if not isinstance(flat_question, str) or not flat_question.strip():
            raise ValueError("follow_up_question must be a non-empty string")
        if not isinstance(flat_answer, str) or not flat_answer.strip():
            raise ValueError("follow_up_answer must be a non-empty string")
        return [(flat_question, flat_answer)]

    if not isinstance(raw, list):
        raise ValueError("probes must be a list")
    if len(raw) > llm.MAX_SCORED_FOLLOW_UPS:
        raise ValueError(
            f"probes must contain at most {llm.MAX_SCORED_FOLLOW_UPS} pairs"
        )

    probes: list[tuple[str, str]] = []
    for index, probe in enumerate(raw, 1):
        if not isinstance(probe, dict):
            raise ValueError(f"probe {index} must be an object")
        question = probe.get("question")
        answer = probe.get("answer")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"probe {index} question must be a non-empty string")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError(f"probe {index} answer must be a non-empty string")
        probes.append((question, answer))
    return probes


def build_completion(
    case: dict[str, Any], *, model: str, effort: str | None
) -> dict[str, Any]:
    """Build the byte-for-byte production V2 scoring request."""
    return llm.build_score_v2_completion(
        model=model,
        effort=effort,
        topic=_text(case, "topic"),
        mastery_summary=str(case.get("mastery_summary", "")),
        question_asked=_text(case, "question"),
        answer_text=_text(case, "answer"),
        probes=case_probes(case),
        answer_anchor=str(case.get("answer_anchor", "")),
        source_excerpt=str(case.get("source_excerpt", "")),
        answer_basis=str(case.get("answer_basis", "")),
        answer_rubric=case.get("answer_rubric"),
    )


def deployment_fingerprint(completion: dict[str, Any]) -> str:
    """Return the exact digest the dark production route requires."""
    return qualification_fingerprint(completion)


def human_label_failures(cases: Sequence[dict[str, Any]]) -> list[str]:
    """Reject every V2 label that was not explicitly and completely reviewed."""
    failures: list[str] = []
    for index, case in enumerate(cases):
        name = case_name(case, index)
        if case.get("review_status") != "approved":
            failures.append(f"{name}: review_status must be explicitly 'approved'")
        note = case.get("review_note")
        if not isinstance(note, str) or not note.strip():
            failures.append(f"{name}: review_note must be a non-empty string")

        expected_recall = case.get("expected_recall")
        recall_valid = (
            type(expected_recall) is int and expected_recall in range(6)
        )
        if not recall_valid:
            failures.append(f"{name}: expected_recall must be an integer from 0 to 5")

        expected_flow = case.get("expected_flow")
        if expected_flow not in VALID_FLOWS:
            failures.append(
                f"{name}: expected_flow must be 'follow_up' or 'complete'"
            )

        try:
            probes_used = len(case_probes(case))
        except ValueError as exc:
            failures.append(f"{name}: {exc}")
            continue

        if recall_valid and expected_flow in VALID_FLOWS:
            if probes_used == 0:
                policy_flow = (
                    FLOW_FOLLOW_UP
                    if llm.FOLLOW_UP_LOW <= expected_recall <= llm.FOLLOW_UP_HIGH
                    else FLOW_COMPLETE
                )
                if expected_flow != policy_flow:
                    failures.append(
                        f"{name}: expected_flow {expected_flow!r} conflicts with the "
                        f"initial-turn Recall policy for score {expected_recall}"
                    )
            elif (
                probes_used == llm.MAX_SCORED_FOLLOW_UPS
                and expected_flow != FLOW_COMPLETE
            ):
                failures.append(
                    f"{name}: expected_flow must be 'complete' at the scored-probe cap"
                )
    return failures


def stage2_pack_failures(cases: Sequence[dict[str, Any]]) -> list[str]:
    """Validate the full human/provenance/coverage gate before any paid trial."""
    failures = human_label_failures(cases)
    topics: set[str] = set()
    case_names: set[str] = set()
    topic_weeks: dict[str, int] = {}
    weeks: set[int] = set()
    represented_recalls: set[int] = set()
    risk_tags: set[str] = set()
    initial_by_week: dict[int, set[int]] = {}
    terminal_by_week: dict[int, set[int]] = {}
    one_probe_insufficiency = False
    two_probe_cap = False

    for index, case in enumerate(cases):
        name = case_name(case, index)
        if name in case_names:
            failures.append(f"duplicate case name: {name}")
        case_names.add(name)
        topic = case.get("topic")
        if isinstance(topic, str) and topic.strip():
            normalized_topic = topic.strip()
            topics.add(normalized_topic)
        else:
            normalized_topic = ""

        week = case.get("target_week")
        if type(week) is not int or week < 1:
            failures.append(f"{name}: target_week must be a positive integer")
            continue
        weeks.add(week)
        if normalized_topic:
            prior_week = topic_weeks.setdefault(normalized_topic, week)
            if prior_week != week:
                failures.append(
                    f"{name}: topic appears in both week {prior_week} and week {week}"
                )

        for field in (
            "question",
            "answer_basis",
            "source_url",
            "source_section",
            "source_label",
            "evidence",
        ):
            value = case.get(field)
            if not isinstance(value, str) or not value.strip():
                failures.append(f"{name}: approved grounding is missing {field}")
        if case.get("grounding_status") != "approved":
            failures.append(f"{name}: grounding_status must be approved")
        try:
            Grounding(
                source_url=str(case.get("source_url", "")),
                source_section=str(case.get("source_section", "")),
                source_label=str(case.get("source_label", "")),
                answer_basis=str(case.get("answer_basis", "")),
                answer_rubric=case.get("answer_rubric"),
                canonical_question=str(case.get("question", "")),
            ).require_complete()
        except (AttributeError, GroundingError, TypeError):
            failures.append(
                f"{name}: approved grounding must include all five rubric fields"
            )

        recall = case.get("expected_recall")
        if type(recall) is int and recall in range(6):
            represented_recalls.add(recall)
        else:
            continue
        try:
            probes_used = len(case_probes(case))
        except ValueError:
            continue
        flow = case.get("expected_flow")
        if probes_used == 0:
            initial_by_week.setdefault(week, set()).add(recall)
        if flow == FLOW_COMPLETE:
            terminal_by_week.setdefault(week, set()).add(recall)
        if probes_used == 1 and flow == FLOW_FOLLOW_UP:
            one_probe_insufficiency = True
        if probes_used == llm.MAX_SCORED_FOLLOW_UPS and flow == FLOW_COMPLETE:
            two_probe_cap = True

        tags = case.get("tags", [])
        if isinstance(tags, list):
            risk_tags.update(tag for tag in tags if isinstance(tag, str))

    if len(topics) < STAGE2_MIN_CARDS:
        failures.append(
            f"pack has {len(topics)} distinct cards; at least {STAGE2_MIN_CARDS} required"
        )
    if len(weeks) < STAGE2_MIN_WEEKS:
        failures.append(
            f"pack has {len(weeks)} curriculum weeks; at least {STAGE2_MIN_WEEKS} required"
        )
    if represented_recalls != set(range(6)):
        failures.append(
            "pack must represent every expected Recall 0-5; "
            f"observed={sorted(represented_recalls)}"
        )
    for week in sorted(weeks):
        missing_initial = STAGE2_INITIAL_SCORES - initial_by_week.get(week, set())
        if missing_initial:
            failures.append(
                f"week {week}: initial-turn boundary coverage missing Recall "
                f"{sorted(missing_initial)}"
            )
        missing_terminal = STAGE2_TERMINAL_SCORES - terminal_by_week.get(week, set())
        if missing_terminal:
            failures.append(
                f"week {week}: terminal boundary coverage missing Recall "
                f"{sorted(missing_terminal)}"
            )
    if not one_probe_insufficiency:
        failures.append("pack needs a one-probe insufficiency case that requests probe two")
    if not two_probe_cap:
        failures.append("pack needs a completed case at the two-probe cap")
    missing_risks = STAGE2_RISK_TAGS - risk_tags
    if missing_risks:
        failures.append(f"pack is missing risk tags: {sorted(missing_risks)}")
    return failures


def stage2_pack_fingerprint(cases: Sequence[dict[str, Any]]) -> str:
    """Fingerprint the exact reviewed, hydrated pack used by every trial."""
    payload = {
        "format_version": STAGE2_PACK_FINGERPRINT_VERSION,
        "cases": sorted(cases, key=lambda case: str(case.get("name", ""))),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def semantic_fingerprint(
    case: dict[str, Any], completion: dict[str, Any]
) -> str:
    """Hash the reviewed judgement and request semantics, not its transport.

    This hash can join results from different providers. The normal paid-run
    fingerprint remains provider-specific and continues to own safe resume.
    """
    judged_case = {
        key: value for key, value in case.items() if key not in _CASE_METADATA_KEYS
    }
    semantic_completion = {
        key: value
        for key, value in completion.items()
        if key not in _TRANSPORT_COMPLETION_KEYS
    }
    payload = {
        "format_version": SEMANTIC_FINGERPRINT_VERSION,
        "kind": KIND,
        "case": judged_case,
        "completion": semantic_completion,
    }
    serialized = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(serialized).hexdigest()


def product_decision(flow: str, recall: int) -> str:
    """Return the user-visible next step or terminal scheduler bucket."""
    if flow == FLOW_FOLLOW_UP:
        return FLOW_FOLLOW_UP
    if flow == FLOW_COMPLETE:
        return scheduler.rating_for(recall)
    raise ValueError(f"unsupported V2 flow: {flow!r}")


def behavioral_decisions(flow: str, recall: int) -> dict[str, str]:
    """Use the production branches for follow-up, scheduling, and mastery bands."""
    return production_product_decisions(status=flow, recall=recall)


def parse_result(
    prepared: PreparedCall, data: dict[str, Any], usage: Usage
) -> Result:
    """Preserve provisional Recall while validating the real turn-sensitive flow."""
    probes_used = len(case_probes(prepared.case))
    parsed = llm.parse_score_v2_result(data, probes_used=probes_used)
    recall = int(data["recall_score"])
    return Result(
        index=prepared.index,
        case=prepared.case_name,
        expected_recall=int(prepared.case["expected_recall"]),
        recall=recall,
        expected_flow=str(prepared.case["expected_flow"]),
        flow=parsed.status,
        semantic_fingerprint=semantic_fingerprint(
            prepared.case, prepared.completion
        ),
        usage=usage,
        feedback=str(data.get("feedback", "")).strip(),
        follow_up_question=parsed.follow_up_question or "",
        needs_more_evidence=data["needs_more_evidence"],
        mastery_summary=llm.clean_summary(str(data.get("mastery_summary", ""))),
    )


def result_payload(result: Result) -> dict[str, Any]:
    return {
        "expected_recall": result.expected_recall,
        "recall": result.recall,
        "expected_flow": result.expected_flow,
        "flow": result.flow,
        "expected_decision": result.expected_decision,
        "decision": result.decision,
        "semantic_fingerprint": result.semantic_fingerprint,
        "feedback": result.feedback,
        "follow_up_question": result.follow_up_question,
        "needs_more_evidence": result.needs_more_evidence,
        "mastery_summary": result.mastery_summary,
    }


def failure_payload(
    prepared: PreparedCall,
    error: BaseException,
    *,
    failure_type: str | None = None,
) -> dict[str, Any]:
    """Encode a paid-call failure without losing its reviewed comparison key."""
    return {
        "type": failure_type
        or str(getattr(error, "failure_type", ""))
        or type(error).__name__,
        "message": str(error),
        "semantic_fingerprint": semantic_fingerprint(
            prepared.case, prepared.completion
        ),
        "expected_recall": prepared.case.get("expected_recall"),
        "expected_flow": prepared.case.get("expected_flow"),
    }


def result_from_record(prepared: PreparedCall, record: dict[str, Any]) -> Result:
    try:
        payload = record["result"]
        expected_recall = int(payload["expected_recall"])
        expected_flow = str(payload["expected_flow"])
        stored_semantic_fingerprint = str(payload["semantic_fingerprint"])
        current_semantic_fingerprint = semantic_fingerprint(
            prepared.case, prepared.completion
        )
        if expected_recall != prepared.case["expected_recall"]:
            raise ValueError("saved expected Recall does not match the case")
        if expected_flow != prepared.case["expected_flow"]:
            raise ValueError("saved expected flow does not match the case")
        if stored_semantic_fingerprint != current_semantic_fingerprint:
            raise ValueError("saved semantic fingerprint does not match the request")
        result = Result(
            index=prepared.index,
            case=prepared.case_name,
            expected_recall=expected_recall,
            recall=int(payload["recall"]),
            expected_flow=expected_flow,
            flow=str(payload["flow"]),
            semantic_fingerprint=stored_semantic_fingerprint,
            usage=usage_from_record(record),
            feedback=str(payload.get("feedback", "")),
            follow_up_question=str(payload.get("follow_up_question", "")),
            needs_more_evidence=payload["needs_more_evidence"],
            mastery_summary=str(payload.get("mastery_summary", "")),
            resumed=True,
        )
        if result.flow not in VALID_FLOWS:
            raise ValueError(f"unsupported saved flow {result.flow!r}")
        if type(result.needs_more_evidence) is not bool:
            raise ValueError("saved needs_more_evidence is not boolean")
        if result.recall not in range(6):
            raise ValueError("saved Recall is outside 0-5")
        return result
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"invalid saved V2 Recall result for {prepared.case_name}"
        ) from exc


def qualification_gate_failures(
    by_level: dict[str, list[Result]],
) -> list[str]:
    """Return every human-label or product-decision qualification failure."""
    failures: list[str] = []
    for level, results in by_level.items():
        for result in results:
            prefix = f"{level}/{result.case}"
            if abs(result.recall - result.expected_recall) > 1:
                failures.append(
                    f"{prefix}: Recall {result.recall} is more than one from "
                    f"{result.expected_recall}"
                )
            expected_decisions = behavioral_decisions(
                result.expected_flow, result.expected_recall
            )
            actual_decisions = behavioral_decisions(result.flow, result.recall)
            if actual_decisions != expected_decisions:
                failures.append(
                    f"{prefix}: product decisions {actual_decisions!r} differ from "
                    f"{expected_decisions!r}"
                )
    return failures


def three_run_stability_failures(runs: Sequence[Sequence[Result]]) -> list[str]:
    """Require all three fresh runs; never select a favorable replica."""
    if len(runs) != 3:
        return [f"expected exactly three fresh runs, received {len(runs)}"]

    failures: list[str] = []
    indexed_runs: list[dict[str, Result]] = []
    for run_index, results in enumerate(runs, 1):
        indexed: dict[str, Result] = {}
        for result in results:
            if result.semantic_fingerprint in indexed:
                failures.append(
                    f"run {run_index}: duplicate semantic fingerprint for {result.case}"
                )
            else:
                indexed[result.semantic_fingerprint] = result
        indexed_runs.append(indexed)

    fingerprints = set().union(*(set(run) for run in indexed_runs))
    for fingerprint in sorted(fingerprints):
        replicas = [run.get(fingerprint) for run in indexed_runs]
        if any(result is None for result in replicas):
            present = [
                str(index)
                for index, result in enumerate(replicas, 1)
                if result is not None
            ]
            failures.append(
                f"{fingerprint[:12]}: case present only in run(s) {', '.join(present)}"
            )
            continue
        complete_replicas = [result for result in replicas if result is not None]
        case = complete_replicas[0].case
        flows = {result.flow for result in complete_replicas}
        decisions = {
            json.dumps(
                behavioral_decisions(result.flow, result.recall),
                separators=(",", ":"),
                sort_keys=True,
            )
            for result in complete_replicas
        }
        recalls = [result.recall for result in complete_replicas]
        if len(flows) != 1:
            failures.append(f"{case}: flow changed across three runs: {sorted(flows)}")
        if len(decisions) != 1:
            failures.append(
                f"{case}: product decision changed across three runs: "
                f"{sorted(decisions)}"
            )
        if max(recalls) - min(recalls) > 1:
            failures.append(
                f"{case}: Recall range exceeds one across three runs: {recalls}"
            )
    return failures
