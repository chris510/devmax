import json
from pathlib import Path

from scripts import structured_evidence_eval
from scripts.effort_sweep_support import Usage, prepare_call


def case(**overrides):
    value = {
        "name": "self-corrected",
        "topic": "Decision-driven estimation",
        "answer": (
            "I would start with DAU, QPS, bandwidth, and ten-year storage—no, "
            "none of those decides this branch. Use topic cardinality instead."
        ),
        "expected_depth": 1,
        "expected_boundaries": 4,
    }
    value.update(overrides)
    return value


def empty_evidence():
    return {
        "depth": {
            "choice_or_target_span": "",
            "cost_or_tension_span": "",
            "connection_span": "",
        },
        "boundaries": {
            "trigger_or_mistake_span": "",
            "harm_or_incorrect_behavior_span": "",
            "connection_span": "",
        },
    }


def self_correction_evidence():
    data = empty_evidence()
    data["boundaries"] = {
        "trigger_or_mistake_span": (
            "DAU, QPS, bandwidth, and ten-year storage"
        ),
        "harm_or_incorrect_behavior_span": "none of those decides this branch",
        "connection_span": (
            "DAU, QPS, bandwidth, and ten-year storage—no, none of those "
            "decides this branch"
        ),
    }
    return data


def test_frozen_six_case_matrix_has_three_positive_axis_decisions() -> None:
    cases = json.loads(
        (Path(__file__).parents[1] / "scripts/grounded_effort_cases_week1_release.json")
        .read_text()
    )
    selected = {
        case["name"]: structured_evidence_eval.expected_eligibility(case)
        for case in cases
        if case["name"]
        in {
            "non-functional requirements — follow-up supplies constraints",
            "non-functional requirements — failure boundary only",
            "identity boundary — follow-up supplies authorization",
            "decision-driven estimation — follow-up supplies decision",
            "decision-driven estimation — self-corrected",
            "decision-driven estimation — trade-off only",
        }
    }

    assert selected == {
        "non-functional requirements — follow-up supplies constraints": (False, False),
        "non-functional requirements — failure boundary only": (False, True),
        "identity boundary — follow-up supplies authorization": (False, False),
        "decision-driven estimation — follow-up supplies decision": (False, False),
        "decision-driven estimation — self-corrected": (False, True),
        "decision-driven estimation — trade-off only": (True, False),
    }


def test_completion_transmits_only_topic_and_learner_answers() -> None:
    source = case(
        question="Authority question?",
        answer_basis="Secret answer basis",
        answer_rubric={"mechanism": "Secret rubric"},
        follow_up_answer="A second learner answer.",
    )

    completion = structured_evidence_eval.build_completion(
        source, model="gpt-5.6-terra", effort="medium"
    )

    assert completion["schema"] == structured_evidence_eval.EVIDENCE_SCHEMA
    assert completion["max_tokens"] == 512
    assert "Decision-driven estimation" in completion["user_content"]
    assert source["answer"] in completion["user_content"]
    assert source["follow_up_answer"] in completion["user_content"]
    assert "Authority question" not in completion["user_content"]
    assert "Secret answer basis" not in completion["user_content"]
    assert "Secret rubric" not in completion["user_content"]


def test_exact_self_correction_span_is_boundary_eligible() -> None:
    result = structured_evidence_eval.parse_result(
        case(), self_correction_evidence(), usage=Usage()
    )

    assert result.depth.eligible is False
    assert result.boundaries.eligible is True
    assert result.boundaries.errors == ()


def test_empty_relationship_is_ineligible_without_validation_error() -> None:
    result = structured_evidence_eval.parse_result(
        case(expected_boundaries=2), empty_evidence(), usage=Usage()
    )

    assert result.depth.eligible is False
    assert result.boundaries.eligible is False
    assert result.depth.errors == result.boundaries.errors == ()


def test_paraphrase_cannot_become_eligible() -> None:
    data = self_correction_evidence()
    data["boundaries"]["connection_span"] = (
        "Estimating unrelated quantities wastes interview time."
    )

    result = structured_evidence_eval.parse_result(case(), data, usage=Usage())

    assert result.boundaries.eligible is False
    assert "connection_span is not exact learner text" in result.boundaries.errors


def test_endpoint_must_be_inside_the_exact_connection() -> None:
    data = self_correction_evidence()
    data["boundaries"]["connection_span"] = "none of those decides this branch"

    result = structured_evidence_eval.parse_result(case(), data, usage=Usage())

    assert result.boundaries.eligible is False
    assert (
        "trigger_or_mistake_span is not contained in connection_span"
        in result.boundaries.errors
    )


def test_gate_requires_reviewed_eligibility_and_valid_exact_spans() -> None:
    passing = structured_evidence_eval.parse_result(
        case(name="passing"), self_correction_evidence(), usage=Usage()
    )
    invalid = structured_evidence_eval.parse_result(
        case(name="invalid"),
        {
            **empty_evidence(),
            "boundaries": {
                "trigger_or_mistake_span": "ritual arithmetic",
                "harm_or_incorrect_behavior_span": "wastes time",
                "connection_span": "ritual arithmetic wastes time",
            },
        },
        usage=Usage(),
    )
    assert structured_evidence_eval.gate_failures({"medium": [passing]}) == []
    failures = structured_evidence_eval.gate_failures({"medium": [invalid]})
    assert any("invalid exact-span evidence" in failure for failure in failures)
    assert any("eligible=False expected=True" in failure for failure in failures)


def test_saved_result_revalidates_spans_from_the_frozen_case() -> None:
    source = case()
    completion = structured_evidence_eval.build_completion(
        source, model="gpt-5.6-terra", effort="medium"
    )
    prepared = prepare_call(
        index=4,
        case=source,
        kind="evidence",
        effort="medium",
        completion=completion,
    )
    result = structured_evidence_eval.parse_result(
        source, self_correction_evidence(), usage=Usage(input_tokens=10)
    )
    record = {
        "result": structured_evidence_eval.result_payload(result),
        "usage": {
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        },
    }

    replayed = structured_evidence_eval.result_from_record(prepared, record)

    assert replayed.index == 4
    assert replayed.case == "self-corrected"
    assert replayed.boundaries.eligible is True
    assert replayed.resumed is True
