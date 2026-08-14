from dataclasses import replace

from app.services import llm
from app.services.scoring_provider import qualification_fingerprint
from scripts import v2_recall_eval
from scripts.effort_sweep_support import Usage, prepare_call


def approved_case(
    *,
    expected_recall: int = 4,
    expected_flow: str = "complete",
    probes: list[dict[str, str]] | None = None,
) -> dict:
    case = {
        "name": "synthetic V2 boundary",
        "topic": "Consistent hashing",
        "question": "Why does consistent hashing reduce remapping?",
        "answer": "Only keys in the moved token range change owners.",
        "answer_basis": "A node change moves only adjacent token ranges.",
        "answer_rubric": {"required_mechanism": "Only adjacent ranges move."},
        "expected_recall": expected_recall,
        "expected_flow": expected_flow,
        "review_status": "approved",
        "review_note": "Synthetic unit-test judgement; not a human case-pack label.",
    }
    if probes is not None:
        case["probes"] = probes
    return case


def probe_pairs(count: int) -> list[dict[str, str]]:
    return [
        {"question": f"Probe {index}?", "answer": f"Answer {index}."}
        for index in range(1, count + 1)
    ]


def prepared(case: dict, *, model: str = "gpt-5.6-luna"):
    completion = v2_recall_eval.build_completion(case, model=model, effort="low")
    return prepare_call(
        index=0,
        case=case,
        kind=v2_recall_eval.KIND,
        effort="low",
        completion=completion,
    )


def response(
    recall: int,
    *,
    probe: str = "",
    needs_more_evidence: bool = False,
) -> dict:
    return {
        "recall_score": recall,
        "feedback": "The essential account is grounded.",
        "follow_up_question": probe,
        "needs_more_evidence": needs_more_evidence,
        "mastery_summary": "recalled the essential account",
    }


def synthetic_result(
    *,
    recall: int = 4,
    flow: str = "complete",
    expected_recall: int = 4,
    expected_flow: str = "complete",
    fingerprint: str = "same-request",
) -> v2_recall_eval.Result:
    return v2_recall_eval.Result(
        index=0,
        case="synthetic V2 boundary",
        expected_recall=expected_recall,
        recall=recall,
        expected_flow=expected_flow,
        flow=flow,
        semantic_fingerprint=fingerprint,
        usage=Usage(),
    )


def full_stage2_pack() -> list[dict]:
    cases: list[dict] = []
    risk_tags = sorted(v2_recall_eval.STAGE2_RISK_TAGS)
    for week in (1, 2, 3):
        topics = [f"Week {week} topic {index}" for index in range(1, 7)]
        for position, recall in enumerate((0, 1, 3, 4)):
            cases.append(
                stage2_case(
                    name=f"week {week} initial {recall}",
                    topic=topics[position],
                    week=week,
                    recall=recall,
                    flow="follow_up" if recall in (1, 3) else "complete",
                    tags=[risk_tags[(week * 2 + position) % len(risk_tags)]],
                )
            )
        for position, recall in enumerate((1, 2, 3, 4)):
            cases.append(
                stage2_case(
                    name=f"week {week} terminal {recall}",
                    topic=topics[(position + 2) % len(topics)],
                    week=week,
                    recall=recall,
                    flow="complete",
                    probes=probe_pairs(2),
                )
            )
    cases.append(
        stage2_case(
            name="week 1 one-probe insufficiency",
            topic="Week 1 topic 1",
            week=1,
            recall=3,
            flow="follow_up",
            probes=probe_pairs(1),
        )
    )
    cases.append(
        stage2_case(
            name="week 1 terminal 5",
            topic="Week 1 topic 2",
            week=1,
            recall=5,
            flow="complete",
            probes=probe_pairs(1),
        )
    )
    return cases


def stage2_case(
    *,
    name: str,
    topic: str,
    week: int,
    recall: int,
    flow: str,
    probes: list[dict[str, str]] | None = None,
    tags: list[str] | None = None,
) -> dict:
    case = {
        "name": name,
        "topic": topic,
        "target_week": week,
        "question": f"Question for {topic}?",
        "answer": "Reviewed synthetic learner answer.",
        "answer_basis": "Reviewed synthetic authority.",
        "answer_rubric": {
            "mechanism": "Required mechanism.",
            "acceptable_alternative": "Accepted equivalent.",
            "trade_off": "Relevant extension.",
            "failure_mode": "Relevant boundary.",
            "misconception": "Known confusion.",
        },
        "grounding_status": "approved",
        "source_url": "https://example.com/reviewed",
        "source_section": f"Week {week}",
        "source_label": "Reviewed unit-test source",
        "evidence": "reviewed_test_fixture",
        "expected_recall": recall,
        "expected_flow": flow,
        "review_status": "approved",
        "review_note": "Explicit synthetic human-review fixture for the gate test.",
        "tags": tags or [],
    }
    if probes is not None:
        case["probes"] = probes
    return case


def test_builder_is_the_production_v2_builder_with_exact_probe_state() -> None:
    case = approved_case(
        expected_recall=3,
        expected_flow="complete",
        probes=probe_pairs(2),
    )

    completion = v2_recall_eval.build_completion(
        case, model="gpt-5.6-luna", effort="low"
    )
    production = llm.build_score_v2_completion(
        model="gpt-5.6-luna",
        effort="low",
        topic=case["topic"],
        mastery_summary="",
        question_asked=case["question"],
        answer_text=case["answer"],
        probes=[("Probe 1?", "Answer 1."), ("Probe 2?", "Answer 2.")],
        answer_anchor="",
        source_excerpt="",
        answer_basis=case["answer_basis"],
        answer_rubric=case["answer_rubric"],
    )

    assert completion == production
    assert completion["rubric"] == llm.SCORING_V2_RUBRIC
    assert completion["schema"] == llm.SCORE_V2_SCHEMA
    assert v2_recall_eval.deployment_fingerprint(
        completion
    ) == qualification_fingerprint(completion)
    assert "SCORED FOLLOW-UPS USED: 2 of 2" in completion["user_content"]
    assert completion["retry"] is False


def test_human_labels_must_be_explicit_complete_and_policy_consistent() -> None:
    case = approved_case(expected_recall=2, expected_flow="complete")
    case["review_status"] = "candidate"
    case["review_note"] = " "
    case["expected_recall"] = True
    case["expected_flow"] = "score"

    failures = v2_recall_eval.human_label_failures([case])

    assert any("review_status" in failure for failure in failures)
    assert any("review_note" in failure for failure in failures)
    assert any("expected_recall" in failure for failure in failures)
    assert any("expected_flow" in failure for failure in failures)

    initial_conflict = approved_case(expected_recall=2, expected_flow="complete")
    capped_conflict = approved_case(
        expected_recall=2,
        expected_flow="follow_up",
        probes=probe_pairs(2),
    )
    assert any(
        "initial-turn Recall policy" in failure
        for failure in v2_recall_eval.human_label_failures([initial_conflict])
    )
    assert any(
        "scored-probe cap" in failure
        for failure in v2_recall_eval.human_label_failures([capped_conflict])
    )


def test_stage2_pack_gate_requires_full_grounded_multweek_coverage() -> None:
    cases = full_stage2_pack()

    assert v2_recall_eval.stage2_pack_failures(cases) == []
    assert len(v2_recall_eval.stage2_pack_fingerprint(cases)) == 64

    cases[1]["name"] = cases[0]["name"]
    cases[2]["answer_rubric"].pop("misconception")
    failures = v2_recall_eval.stage2_pack_failures(cases)

    assert any("duplicate case name" in failure for failure in failures)
    assert any("all five rubric fields" in failure for failure in failures)


def test_parser_preserves_initial_provisional_recall_and_validates_follow_up() -> None:
    case = approved_case(expected_recall=2, expected_flow="follow_up")

    result = v2_recall_eval.parse_result(
        prepared(case),
        response(2, probe="One more — name the missing link?"),
        Usage(input_tokens=100, output_tokens=20),
    )

    assert result.recall == 2
    assert result.flow == "follow_up"
    assert result.follow_up_question.startswith("One more —")
    assert result.decision == "follow_up"
    assert result.usage.input_tokens == 100


def test_parser_uses_one_probe_insufficiency_and_two_probe_cap() -> None:
    one_probe = approved_case(
        expected_recall=3,
        expected_flow="follow_up",
        probes=probe_pairs(1),
    )
    one_result = v2_recall_eval.parse_result(
        prepared(one_probe),
        response(
            3,
            probe="Last one — what does that imply?",
            needs_more_evidence=True,
        ),
        Usage(),
    )

    capped = approved_case(
        expected_recall=3,
        expected_flow="complete",
        probes=probe_pairs(2),
    )
    capped_result = v2_recall_eval.parse_result(
        prepared(capped), response(3), Usage()
    )

    assert one_result.flow == "follow_up"
    assert one_result.needs_more_evidence is True
    assert capped_result.flow == "complete"
    assert capped_result.decision == "good"


def test_qualification_gate_blocks_flow_and_terminal_bucket_changes() -> None:
    flow_change = synthetic_result(
        recall=4,
        flow="complete",
        expected_recall=3,
        expected_flow="follow_up",
    )
    bucket_change = synthetic_result(recall=3, expected_recall=2)
    today_band_change = synthetic_result(recall=2, expected_recall=1)
    coverage_band_change = synthetic_result(recall=4, expected_recall=3)
    far_score = synthetic_result(recall=1, expected_recall=4)

    failures = v2_recall_eval.qualification_gate_failures(
        {
            "low": [
                flow_change,
                bucket_change,
                today_band_change,
                coverage_band_change,
                far_score,
            ]
        }
    )

    assert sum("product decisions" in failure for failure in failures) == 5
    assert any("more than one" in failure for failure in failures)


def test_semantic_fingerprint_ignores_transport_and_review_metadata() -> None:
    case = approved_case()
    completion = v2_recall_eval.build_completion(
        case, model="gpt-5.6-luna", effort="low"
    )
    first = v2_recall_eval.semantic_fingerprint(
        case, {**completion, "provider": "openai-responses"}
    )

    relabeled_metadata = {
        **case,
        "name": "renamed",
        "review_status": "candidate",
        "review_note": "Pending a second reader.",
        "tags": ["canary"],
    }
    other_transport = {
        **completion,
        "model": "claude-sonnet-5",
        "provider": "anthropic",
        "retry": True,
    }

    assert v2_recall_eval.semantic_fingerprint(relabeled_metadata, other_transport) == first
    assert (
        v2_recall_eval.semantic_fingerprint(
            {**case, "expected_recall": 5}, completion
        )
        != first
    )
    assert (
        v2_recall_eval.semantic_fingerprint(
            case, {**completion, "max_tokens": 1024}
        )
        != first
    )


def test_three_run_stability_requires_every_replica_without_best_of_three() -> None:
    base = synthetic_result(recall=4)
    stable = [
        [base],
        [replace(base, recall=5)],
        [replace(base, recall=4)],
    ]

    assert v2_recall_eval.three_run_stability_failures(stable) == []

    decision_flip = [
        [replace(base, recall=2)],
        [replace(base, recall=3)],
        [replace(base, recall=2)],
    ]
    failures = v2_recall_eval.three_run_stability_failures(decision_flip)
    assert any("product decision changed" in failure for failure in failures)

    mastery_flip = [
        [replace(base, recall=3)],
        [replace(base, recall=4)],
        [replace(base, recall=3)],
    ]
    assert any(
        "product decision changed" in failure
        for failure in v2_recall_eval.three_run_stability_failures(mastery_flip)
    )

    wide_range = [
        [replace(base, recall=2)],
        [replace(base, recall=4)],
        [replace(base, recall=3)],
    ]
    assert any(
        "Recall range exceeds one" in failure
        for failure in v2_recall_eval.three_run_stability_failures(wide_range)
    )
    assert v2_recall_eval.three_run_stability_failures(stable[:2]) == [
        "expected exactly three fresh runs, received 2"
    ]
