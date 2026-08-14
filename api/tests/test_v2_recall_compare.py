import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from scripts import (
    openai_bakeoff,
    v2_recall_compare,
    v2_recall_eval,
    v2_recall_sweep,
    v2_recall_text_review,
)
from scripts.effort_sweep_support import (
    RESULT_FORMAT_VERSION,
    Usage,
    cost_ceiling_for_display,
)
from scripts.openai_eval_support import (
    V2_EVAL_SAFETY_IDENTIFIER,
    V2_EVAL_SAFETY_IDENTIFIER_FORMAT_VERSION,
)

EXPECTED_CLAUDE_MODEL = "claude-sonnet-5"
EXPECTED_CLAUDE_EFFORT = "low"
QUALIFICATION_EXPIRES_AT = datetime(2026, 9, 12, tzinfo=UTC)
COMPARISON_AS_OF = datetime(2026, 8, 14, tzinfo=UTC)


def semantic(number: int) -> str:
    return f"{number:064x}"


def trusted_case(
    *,
    name: str = "case one",
    expected_recall: int = 4,
    expected_flow: str = "complete",
    probes: list[dict[str, str]] | None = None,
) -> dict:
    return {
        "name": name,
        "topic": f"Trusted topic for {name}",
        "target_week": 1,
        "question": f"What is the essential account for {name}?",
        "answer": f"Initial learner answer for {name}.",
        "probes": probes
        if probes is not None
        else [
            {
                "question": f"First trusted probe for {name}?",
                "answer": f"First trusted probe answer for {name}.",
            },
            {
                "question": f"Second trusted probe for {name}?",
                "answer": f"Second trusted probe answer for {name}.",
            },
        ],
        "expected_recall": expected_recall,
        "expected_flow": expected_flow,
        "review_status": "approved",
        "review_note": "Human-reviewed fixture label.",
        "tags": ["fixture"],
        "source_url": "https://example.test/trusted",
        "source_section": "Trusted section",
        "source_label": "Trusted source",
        "evidence": "fixture",
        "grounding_status": "approved",
        "answer_basis": f"Trusted answer basis for {name}.",
        "answer_rubric": {
            "mechanism": "Trusted mechanism.",
            "acceptable_alternative": "Trusted alternative.",
            "trade_off": "Trusted trade-off.",
            "failure_mode": "Trusted failure mode.",
            "misconception": "Trusted misconception.",
        },
    }


def prepared_call(
    case: dict,
    *,
    model: str,
    effort: str = "low",
    output_cap: int = 2048,
):
    if model.startswith("claude-"):
        return v2_recall_sweep.prepare_cases(
            [case], levels=[effort], model=model
        )[0]
    return openai_bakeoff.prepare_cases(
        [case],
        kind=v2_recall_eval.KIND,
        levels=[effort],
        model=model,
        max_output_tokens=output_cap,
    )[0]


_DEFAULT_TRUSTED_CASE = trusted_case()
QUALIFICATION_FINGERPRINT = v2_recall_eval.deployment_fingerprint(
    prepared_call(_DEFAULT_TRUSTED_CASE, model="gpt-5.6-luna").completion
)
PACK_FINGERPRINT = v2_recall_eval.stage2_pack_fingerprint(
    [_DEFAULT_TRUSTED_CASE]
)


def success_record(
    *,
    case: str = "case one",
    semantic_fingerprint: str = semantic(1),
    model: str,
    replica: str,
    expected_recall: int = 4,
    recall: int = 4,
    expected_flow: str = "complete",
    flow: str = "complete",
    input_tokens: int = 100,
    output_tokens: int = 10,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    response_id: str | None = None,
    evaluation_run_id: str | None = None,
    effort: str = "low",
    needs_more_evidence: bool = False,
    follow_up_question: str = "",
) -> dict:
    result = v2_recall_eval.Result(
        index=0,
        case=case,
        expected_recall=expected_recall,
        recall=recall,
        expected_flow=expected_flow,
        flow=flow,
        semantic_fingerprint=semantic_fingerprint,
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
        feedback="The essential mechanism was recalled accurately.",
        follow_up_question=follow_up_question,
        needs_more_evidence=needs_more_evidence,
        mastery_summary="Recall of the essential mechanism is strong.",
    )
    record = {
        "format_version": RESULT_FORMAT_VERSION,
        "kind": v2_recall_eval.KIND,
        "fingerprint": f"request-{case}",
        "created_at": "2026-08-13T00:00:01Z",
        "case": case,
        "model": model,
        "effort": effort,
        "qualification_fingerprint": QUALIFICATION_FINGERPRINT,
        "stage2_pack_fingerprint": PACK_FINGERPRINT,
        "evaluation_run_id": evaluation_run_id
        or f"00000000-0000-0000-0000-{int(replica) + 1:012d}",
        "fresh": True,
        "evidence_outcome": "success",
        "scoring_prompt_variant": "production",
        "result": v2_recall_eval.result_payload(result),
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
        },
        "provider_elapsed_ms": 100 + int(replica),
        "provider_response_model": model,
    }
    if response_id is not None:
        record["provider_response_id"] = response_id
    return record


def failure_record(
    *,
    model: str,
    replica: str,
    semantic_fingerprint: str = semantic(1),
) -> dict:
    return {
        "format_version": RESULT_FORMAT_VERSION,
        "kind": v2_recall_eval.KIND,
        "fingerprint": "request-case one",
        "created_at": "2026-08-13T00:00:01Z",
        "case": "case one",
        "model": model,
        "effort": "low",
        "qualification_fingerprint": QUALIFICATION_FINGERPRINT,
        "stage2_pack_fingerprint": PACK_FINGERPRINT,
        "evaluation_run_id": f"00000000-0000-0000-0000-{int(replica) + 1:012d}",
        "fresh": True,
        "evidence_outcome": "failure",
        "scoring_prompt_variant": "production",
        "failure": {
            "type": "invalid_schema",
            "message": "missing recall_score",
            "semantic_fingerprint": semantic_fingerprint,
            "expected_recall": 4,
            "expected_flow": "complete",
        },
        "usage": {
            "input_tokens": 100,
            "output_tokens": 3,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        },
        "provider_elapsed_ms": 100 + int(replica),
    }


def write_jsonl(
    path: Path,
    records: list[dict],
    *,
    rates: dict[str, Decimal] | None = None,
    output_allowance: int | None = None,
) -> Path:
    rows = list(records)
    if rows and rows[0].get("record_type") != "run_manifest":
        first = rows[0]
        effective_rates = rates or {
            "input": Decimal("1"),
            "output": Decimal("1"),
            "cached_input": Decimal("1"),
            "cache_write": Decimal("1"),
        }
        input_counts = {
            record["fingerprint"]: record["usage"]["input_tokens"]
            for record in records
        }
        output_per_call = output_allowance or max(
            record["usage"]["output_tokens"] for record in records
        )
        estimated_cost = (
            Decimal(sum(input_counts.values()))
            * max(
                effective_rates["input"],
                effective_rates["cached_input"],
                effective_rates["cache_write"],
            )
            + Decimal(output_per_call * len(records))
            * effective_rates["output"]
        ) / Decimal(1_000_000)
        rows.insert(
            0,
            {
                "format_version": RESULT_FORMAT_VERSION,
                "record_type": "run_manifest",
                "kind": v2_recall_eval.KIND,
                "created_at": "2026-08-13T00:00:00Z",
                "qualification_expires_at": "2026-09-12T00:00:00Z",
                "evaluation_run_id": first["evaluation_run_id"],
                "provider": (
                    "anthropic"
                    if str(first["model"]).startswith("claude-")
                    else "openai"
                ),
                "model": first["model"],
                "stage2_pack_fingerprint": first["stage2_pack_fingerprint"],
                "fresh": True,
                "safety_identifier": (
                    None
                    if str(first["model"]).startswith("claude-")
                    else {
                        "kind": "synthetic_non_user",
                        "format_version": (
                            V2_EVAL_SAFETY_IDENTIFIER_FORMAT_VERSION
                        ),
                        "value": V2_EVAL_SAFETY_IDENTIFIER,
                    }
                ),
                "preflight": {
                    "approved_max_cost_usd": "1",
                    "estimated_ceiling_usd": str(
                        cost_ceiling_for_display(estimated_cost)
                    ),
                    "rates_per_million_usd": {
                        key: str(value) for key, value in effective_rates.items()
                    },
                    "input_count_method": (
                        "anthropic_messages_count_tokens"
                        if str(first["model"]).startswith("claude-")
                        else "local_utf8_byte_upper_bound"
                    ),
                    "input_counts": input_counts,
                    "input_tokens_total": sum(input_counts.values()),
                    "estimated_output_tokens": output_per_call * len(records),
                    "estimated_output_tokens_per_call": output_per_call,
                },
                "invocations": [
                    {
                        "fingerprint": record["fingerprint"],
                        "case": record["case"],
                        "effort": record["effort"],
                        "qualification_fingerprint": record[
                            "qualification_fingerprint"
                        ],
                    }
                    for record in records
                ],
            },
        )
    path.write_text("".join(f"{json.dumps(record)}\n" for record in rows))
    return path


def bound_success_record(
    case: dict,
    *,
    model: str,
    replica: str,
    recall: int | None = None,
    input_tokens: int = 100,
    output_tokens: int = 10,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    response_id: str | None = None,
    evaluation_run_id: str | None = None,
    effort: str = "low",
    needs_more_evidence: bool = False,
    follow_up_question: str = "",
) -> dict:
    call = prepared_call(case, model=model, effort=effort)
    record = success_record(
        case=call.case_name,
        semantic_fingerprint=v2_recall_eval.semantic_fingerprint(
            call.case, call.completion
        ),
        model=model,
        replica=replica,
        expected_recall=case["expected_recall"],
        recall=case["expected_recall"] if recall is None else recall,
        expected_flow=case["expected_flow"],
        flow=case["expected_flow"],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        response_id=response_id,
        evaluation_run_id=evaluation_run_id,
        effort=effort,
        needs_more_evidence=needs_more_evidence,
        follow_up_question=follow_up_question,
    )
    record["fingerprint"] = call.fingerprint
    record["qualification_fingerprint"] = (
        v2_recall_eval.deployment_fingerprint(call.completion)
    )
    record["stage2_pack_fingerprint"] = (
        v2_recall_eval.stage2_pack_fingerprint([case])
    )
    return record


def bound_failure_record(
    case: dict,
    *,
    model: str,
    replica: str,
    response_id: str | None = None,
    evaluation_run_id: str | None = None,
    effort: str = "low",
) -> dict:
    call = prepared_call(case, model=model, effort=effort)
    record = failure_record(
        model=model,
        replica=replica,
        semantic_fingerprint=v2_recall_eval.semantic_fingerprint(
            call.case, call.completion
        ),
    )
    record["case"] = call.case_name
    record["fingerprint"] = call.fingerprint
    record["effort"] = effort
    record["qualification_fingerprint"] = (
        v2_recall_eval.deployment_fingerprint(call.completion)
    )
    record["stage2_pack_fingerprint"] = (
        v2_recall_eval.stage2_pack_fingerprint([case])
    )
    record["evaluation_run_id"] = evaluation_run_id or record["evaluation_run_id"]
    record["failure"].update(
        {
            "expected_recall": case["expected_recall"],
            "expected_flow": case["expected_flow"],
        }
    )
    if response_id is not None:
        record["provider_response_id"] = response_id
    return record


def write_trusted_inputs(
    tmp_path: Path, cases: list[dict]
) -> tuple[Path, Path]:
    hydrated_fields = {
        "question",
        "answer_basis",
        "answer_rubric",
        "grounding_status",
        "source_url",
        "source_section",
        "source_label",
        "target_week",
        "evidence",
    }
    case_rows = [
        {key: value for key, value in case.items() if key not in hydrated_fields}
        for case in cases
    ]
    grounding_rows = [
        {
            "topic": case["topic"],
            "canonical_question": case["question"],
            "answer_basis": case["answer_basis"],
            "answer_rubric": case["answer_rubric"],
            "grounding_status": case["grounding_status"],
            "source_url": case["source_url"],
            "source_section": case["source_section"],
            "source_label": case["source_label"],
            "target_week": case["target_week"],
            "evidence": case["evidence"],
        }
        for case in cases
    ]
    cases_path = tmp_path / "cases.json"
    grounding_path = tmp_path / "grounding.json"
    cases_path.write_text(json.dumps(case_rows) + "\n")
    grounding_path.write_text(json.dumps(grounding_rows) + "\n")
    return cases_path, grounding_path


def four_runs(
    tmp_path: Path,
    *,
    expected_recall: int = 4,
    claude_recall: int = 4,
    luna_recalls: tuple[int, int, int] = (4, 4, 4),
    claude_tokens: tuple[int, int] = (1000, 100),
    luna_tokens: tuple[int, int] = (100, 10),
    claude_cache: tuple[int, int] = (0, 0),
    luna_cached_input: int = 0,
    luna_cache_write: int = 0,
    claude_manifest_rates: dict[str, Decimal] | None = None,
    luna_manifest_rates: dict[str, Decimal] | None = None,
) -> tuple[v2_recall_compare.EvidenceRun, list[v2_recall_compare.EvidenceRun]]:
    case = trusted_case(expected_recall=expected_recall)
    claude_path = write_jsonl(
        tmp_path / "claude.jsonl",
        [
            bound_success_record(
                case,
                model="claude-sonnet-5",
                replica="0",
                recall=claude_recall,
                input_tokens=claude_tokens[0],
                output_tokens=claude_tokens[1],
                cache_read_tokens=claude_cache[0],
                cache_write_tokens=claude_cache[1],
            )
        ],
        rates=claude_manifest_rates,
        output_allowance=2048,
    )
    luna_paths = []
    for index, recall in enumerate(luna_recalls, 1):
        luna_paths.append(
            write_jsonl(
                tmp_path / f"luna-{index}.jsonl",
                [
                    bound_success_record(
                        case,
                        model="gpt-5.6-luna",
                        replica=str(index),
                        recall=recall,
                        input_tokens=luna_tokens[0],
                        output_tokens=luna_tokens[1],
                        cache_read_tokens=luna_cached_input,
                        cache_write_tokens=luna_cache_write,
                        response_id=f"resp-{index}",
                    )
                ],
                rates=luna_manifest_rates,
                output_allowance=2048,
            )
        )
    return (
        v2_recall_compare.load_evidence(
            claude_path, label="claude", provider="claude"
        ),
        [
            v2_recall_compare.load_evidence(
                path, label=f"luna-{index}", provider="luna"
            )
            for index, path in enumerate(luna_paths, 1)
        ],
    )


def reviewed_cases_for_run(
    run: v2_recall_compare.EvidenceRun,
) -> list[dict]:
    return [
        trusted_case(
            name=outcome.case,
            expected_recall=outcome.expected_recall,
            expected_flow=outcome.expected_flow,
        )
        for outcome in run.outcomes.values()
    ]


def approved_review_payload(
    run: v2_recall_compare.EvidenceRun,
    cases: list[dict] | None = None,
) -> dict:
    reviewed_cases = cases or reviewed_cases_for_run(run)
    payload = v2_recall_compare.text_quality_review_template(
        run, reviewed_cases, reviewer="owner@example.test"
    )
    payload.update(
        {
            "status": "approved",
            "reviewed_at": "2026-08-13T12:00:00-07:00",
            "notes": "Reviewed every response against the approved card authority.",
        }
    )
    for review in payload["case_reviews"]:
        outcome = run.outcomes[review["semantic_fingerprint"]]
        assert outcome.result is not None
        recall = outcome.result.recall
        review.update(
            {
                "status": "approved",
                "notes": "Grounded, Recall-only feedback and summary are suitable.",
                "source_grounded": True,
                "no_unsupported_correction": True,
                "no_numeric_secondary_axis_claim": True,
                "mastery_summary_recall_only": True,
                "mastery_summary_distinguishes_unaided_from_probe_assisted_recall": (
                    True
                ),
                "no_broad_or_unmeasured_mastery_claim": True,
                "feedback_and_mastery_are_concise_and_direct": True,
                "low_recall_feedback_states_correct_essential_account": (
                    recall <= 2
                ),
                "passing_feedback_is_appropriately_direct": recall >= 3,
            }
        )
    return payload


def reviews_for(
    lunas: list[v2_recall_compare.EvidenceRun],
    cases: list[dict] | None = None,
) -> list[v2_recall_compare.TextQualityAttestation]:
    reviews: list[v2_recall_compare.TextQualityAttestation] = []
    for index, run in enumerate(lunas, 1):
        path = run.path.with_name(f"luna-{index}-text-review.json")
        path.write_text(json.dumps(approved_review_payload(run, cases)) + "\n")
        reviews.append(v2_recall_compare.load_text_quality_attestation(path))
    return reviews


def report_for(
    claude: v2_recall_compare.EvidenceRun,
    lunas: list[v2_recall_compare.EvidenceRun],
) -> v2_recall_compare.ComparisonReport:
    reviewed_cases = reviewed_cases_for_run(claude)
    return v2_recall_compare.build_report(
        claude,
        lunas,
        reviews_for(lunas, reviewed_cases),
        reviewed_cases,
        claude_input_price=Decimal("1"),
        claude_output_price=Decimal("1"),
        claude_cache_read_price=Decimal("1"),
        claude_cache_write_price=Decimal("1"),
        luna_input_price=Decimal("1"),
        luna_output_price=Decimal("1"),
        luna_cached_input_price=Decimal("1"),
        luna_cache_write_price=Decimal("1"),
        expected_claude_model=EXPECTED_CLAUDE_MODEL,
        expected_claude_effort=EXPECTED_CLAUDE_EFFORT,
        expected_luna_qualification_fingerprint=(
            lunas[0].qualification_fingerprint
        ),
        expected_stage2_pack_fingerprint=claude.stage2_pack_fingerprint,
        qualification_expires_at=QUALIFICATION_EXPIRES_AT,
        as_of=COMPARISON_AS_OF,
    )


def test_four_aligned_files_report_every_replica_without_best_of_three(
    tmp_path: Path,
) -> None:
    claude, lunas = four_runs(tmp_path)

    report = report_for(claude, lunas)

    assert report.passed
    assert [metric.label for metric in report.metrics] == [
        "claude",
        "luna-1",
        "luna-2",
        "luna-3",
    ]
    assert report.luna_aggregate.attempts == 3
    assert report.luna_aggregate.exact == 3
    assert report.cost_reduction == Decimal("0.9")
    assert report.text_quality_review_count == 3
    assert all(sum(matrix.values()) == 1 for matrix in report.decision_confusion.values())


def test_text_review_tool_writes_only_a_pending_hash_bound_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _claude, lunas = four_runs(tmp_path)
    output = tmp_path / "review-draft.json"
    cases_path, grounding_path = write_trusted_inputs(
        tmp_path, [_DEFAULT_TRUSTED_CASE]
    )
    monkeypatch.setattr(v2_recall_eval, "stage2_pack_failures", lambda _cases: [])

    assert (
        v2_recall_text_review.main(
            [
                "draft",
                "--luna",
                str(lunas[0].path),
                "--output",
                str(output),
                "--reviewer",
                "owner@example.test",
                "--cases",
                str(cases_path),
                "--grounding-manifest",
                str(grounding_path),
            ]
        )
        == 0
    )

    payload = json.loads(output.read_text())
    assert payload["format_version"] == 3
    assert payload["status"] == "pending"
    assert payload["evaluation_run_id"] == lunas[0].evaluation_run_id
    assert payload["evidence_sha256"] == lunas[0].file_digest
    assert payload["manifest_sha256"] == lunas[0].manifest_digest
    assert len(payload["case_reviews"]) == len(lunas[0].outcomes)
    assert all(review["status"] == "pending" for review in payload["case_reviews"])
    assert all(
        review[check] is False
        for review in payload["case_reviews"]
        for check in v2_recall_compare.TEXT_QUALITY_REVIEW_CHECKS
    )
    with pytest.raises(SystemExit, match="2"):
        v2_recall_text_review.main(
            [
                "check",
                "--luna",
                str(lunas[0].path),
                "--review",
                str(output),
                "--cases",
                str(cases_path),
                "--grounding-manifest",
                str(grounding_path),
            ]
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("root_status", "not approved"),
        ("root_notes", "notes: must be non-empty"),
        ("case_status", "review is not approved"),
        ("case_notes", "notes: must be non-empty"),
        ("source_grounded", "source_grounded must be explicitly true"),
        (
            "no_unsupported_correction",
            "no_unsupported_correction must be explicitly true",
        ),
        (
            "no_numeric_secondary_axis_claim",
            "no_numeric_secondary_axis_claim must be explicitly true",
        ),
        (
            "mastery_summary_recall_only",
            "mastery_summary_recall_only must be explicitly true",
        ),
        (
            "mastery_summary_distinguishes_unaided_from_probe_assisted_recall",
            "mastery_summary_distinguishes_unaided_from_probe_assisted_recall "
            "must be explicitly true",
        ),
        (
            "no_broad_or_unmeasured_mastery_claim",
            "no_broad_or_unmeasured_mastery_claim must be explicitly true",
        ),
        (
            "feedback_and_mastery_are_concise_and_direct",
            "feedback_and_mastery_are_concise_and_direct must be explicitly true",
        ),
    ],
)
def test_text_review_requires_human_approval_notes_and_every_quality_check(
    tmp_path: Path, mutation: str, message: str
) -> None:
    _claude, lunas = four_runs(tmp_path, expected_recall=2, luna_recalls=(2, 2, 2))
    payload = approved_review_payload(lunas[0])
    if mutation == "root_status":
        payload["status"] = "pending"
    elif mutation == "root_notes":
        payload["notes"] = ""
    elif mutation == "case_status":
        payload["case_reviews"][0]["status"] = "pending"
    elif mutation == "case_notes":
        payload["case_reviews"][0]["notes"] = ""
    else:
        payload["case_reviews"][0][mutation] = False
    path = tmp_path / f"unapproved-{mutation}.json"
    path.write_text(json.dumps(payload) + "\n")

    with pytest.raises(ValueError, match=message):
        v2_recall_compare.load_text_quality_attestation(path)


@pytest.mark.parametrize(
    ("recall", "field", "value", "message"),
    [
        (
            2,
            "low_recall_feedback_states_correct_essential_account",
            False,
            "must be true for this score",
        ),
        (
            2,
            "passing_feedback_is_appropriately_direct",
            True,
            "must be false because it is not applicable",
        ),
        (
            3,
            "passing_feedback_is_appropriately_direct",
            False,
            "must be true for this score",
        ),
        (
            3,
            "low_recall_feedback_states_correct_essential_account",
            True,
            "must be false because it is not applicable",
        ),
    ],
)
def test_text_review_enforces_the_score_dependent_feedback_contract(
    tmp_path: Path, recall: int, field: str, value: bool, message: str
) -> None:
    _claude, lunas = four_runs(
        tmp_path,
        expected_recall=recall,
        claude_recall=recall,
        luna_recalls=(recall, recall, recall),
    )
    payload = approved_review_payload(lunas[0])
    payload["case_reviews"][0][field] = value
    path = tmp_path / f"wrong-score-contract-{recall}-{field}.json"
    path.write_text(json.dumps(payload) + "\n")
    review = v2_recall_compare.load_text_quality_attestation(path)

    with pytest.raises(ValueError, match=message):
        v2_recall_compare.validate_text_quality_attestations(
            [lunas[0]],
            [review],
            reviewed_cases_for_run(lunas[0]),
            as_of=COMPARISON_AS_OF,
        )


def test_text_review_score_contract_fields_are_strict_booleans(
    tmp_path: Path,
) -> None:
    _claude, lunas = four_runs(tmp_path)
    payload = approved_review_payload(lunas[0])
    payload["case_reviews"][0][
        "passing_feedback_is_appropriately_direct"
    ] = 1
    path = tmp_path / "non-boolean-score-contract.json"
    path.write_text(json.dumps(payload) + "\n")

    with pytest.raises(ValueError, match="must be boolean"):
        v2_recall_compare.load_text_quality_attestation(path)


def test_expanded_text_quality_contract_rejects_old_review_format(
    tmp_path: Path,
) -> None:
    _claude, lunas = four_runs(tmp_path)
    payload = approved_review_payload(lunas[0])
    payload["format_version"] = 1
    path = tmp_path / "old-text-review-format.json"
    path.write_text(json.dumps(payload) + "\n")

    with pytest.raises(ValueError, match="unsupported text-quality review format"):
        v2_recall_compare.load_text_quality_attestation(path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("evidence", "stale or tampered evidence"),
        ("manifest", "stale or tampered manifest"),
        ("response", "case/semantic/response identity mismatch"),
        ("feedback", "feedback SHA-256 does not match"),
        ("mastery", "mastery_summary SHA-256 does not match"),
    ],
)
def test_text_review_rejects_stale_artifact_identity_or_text_hashes(
    tmp_path: Path, mutation: str, message: str
) -> None:
    _claude, lunas = four_runs(tmp_path)
    payload = approved_review_payload(lunas[0])
    if mutation == "evidence":
        payload["evidence_sha256"] = "0" * 64
    elif mutation == "manifest":
        payload["manifest_sha256"] = "0" * 64
    elif mutation == "response":
        payload["case_reviews"][0]["provider_response_id"] = "resp-substituted"
    elif mutation == "feedback":
        payload["case_reviews"][0]["feedback_sha256"] = "0" * 64
    else:
        payload["case_reviews"][0]["mastery_summary_sha256"] = "0" * 64
    path = tmp_path / f"stale-{mutation}.json"
    path.write_text(json.dumps(payload) + "\n")
    if mutation in {"feedback", "mastery"}:
        with pytest.raises(ValueError, match=message):
            v2_recall_compare.load_text_quality_attestation(path)
        return
    review = v2_recall_compare.load_text_quality_attestation(path)

    with pytest.raises(ValueError, match=message):
        v2_recall_compare.validate_text_quality_attestations(
            [lunas[0]], [review], reviewed_cases_for_run(lunas[0])
        )


def test_text_review_covers_all_successful_cases_and_exactly_one_per_run(
    tmp_path: Path,
) -> None:
    cases = [trusted_case(), trusted_case(name="case two")]
    first = bound_success_record(
        cases[0],
        model="gpt-5.6-luna",
        replica="1",
        response_id="resp-one",
    )
    second = bound_success_record(
        cases[1],
        model="gpt-5.6-luna",
        replica="2",
        response_id="resp-two",
        evaluation_run_id=first["evaluation_run_id"],
    )
    pack_fingerprint = v2_recall_eval.stage2_pack_fingerprint(cases)
    first["stage2_pack_fingerprint"] = pack_fingerprint
    second["stage2_pack_fingerprint"] = pack_fingerprint
    run = v2_recall_compare.load_evidence(
        write_jsonl(
            tmp_path / "two-cases.jsonl",
            [first, second],
            output_allowance=2048,
        ),
        label="luna",
        provider="luna",
    )
    payload = approved_review_payload(run, cases)
    payload["case_reviews"].pop()
    path = tmp_path / "missing-case-review.json"
    path.write_text(json.dumps(payload) + "\n")
    incomplete = v2_recall_compare.load_text_quality_attestation(path)

    with pytest.raises(ValueError, match="every successful case"):
        v2_recall_compare.validate_text_quality_attestations(
            [run], [incomplete], cases
        )
    with pytest.raises(ValueError, match="exactly one text-quality attestation"):
        v2_recall_compare.validate_text_quality_attestations([run], [], cases)


@pytest.mark.parametrize(
    ("reviewed_at", "message"),
    [
        ("2026-08-12T23:59:59Z", "predates its evidence run"),
        ("2026-08-15T00:00:00Z", "timestamp is in the future"),
        ("2026-09-12T00:00:00Z", "at or after qualification expiry"),
    ],
)
def test_text_review_time_is_bound_to_run_comparison_and_expiry(
    tmp_path: Path, reviewed_at: str, message: str
) -> None:
    _claude, lunas = four_runs(tmp_path)
    payload = approved_review_payload(lunas[0])
    payload["reviewed_at"] = reviewed_at
    path = tmp_path / "review-time.json"
    path.write_text(json.dumps(payload) + "\n")
    review = v2_recall_compare.load_text_quality_attestation(path)

    with pytest.raises(ValueError, match=message):
        v2_recall_compare.validate_text_quality_attestations(
            [lunas[0]],
            [review],
            reviewed_cases_for_run(lunas[0]),
            as_of=COMPARISON_AS_OF,
        )


def test_cli_requires_exactly_three_explicit_luna_files() -> None:
    with pytest.raises(SystemExit, match="2"):
        v2_recall_compare.main(
            [
                "--claude",
                "claude.jsonl",
                "--luna",
                "one.jsonl",
                "--luna",
                "two.jsonl",
                "--claude-input-price-per-million",
                "1",
                "--claude-output-price-per-million",
                "1",
                "--luna-input-price-per-million",
                "1",
                "--luna-output-price-per-million",
                "1",
            ]
        )


def test_duplicate_rows_and_copied_luna_replicas_are_rejected(tmp_path: Path) -> None:
    duplicate = success_record(model="gpt-5.6-luna", replica="1", response_id="r1")
    duplicate_path = write_jsonl(tmp_path / "duplicate.jsonl", [duplicate, duplicate])
    with pytest.raises(ValueError, match="duplicate (invocation|request) fingerprint"):
        v2_recall_compare.load_evidence(
            duplicate_path, label="luna-1", provider="luna"
        )

    claude, lunas = four_runs(tmp_path)
    copied_record = list(lunas[0].outcomes.values())[0].record
    copied_path = write_jsonl(
        tmp_path / "luna-copy.jsonl",
        [copied_record],
    )
    copied = v2_recall_compare.load_evidence(
        copied_path, label="luna-copy", provider="luna"
    )
    with pytest.raises(
        ValueError, match="duplicate Luna replica|copied replica|four fresh runs"
    ):
        v2_recall_compare.validate_alignment(
            claude,
            [lunas[0], copied, lunas[2]],
            expected_claude_model=EXPECTED_CLAUDE_MODEL,
            expected_claude_effort=EXPECTED_CLAUDE_EFFORT,
            expected_luna_qualification_fingerprint=QUALIFICATION_FINGERPRINT,
            expected_stage2_pack_fingerprint=PACK_FINGERPRINT,
            expected_qualification_expires_at=QUALIFICATION_EXPIRES_AT,
            as_of=COMPARISON_AS_OF,
        )


def test_manifest_proves_no_selected_invocation_was_discarded(tmp_path: Path) -> None:
    first = success_record(
        model="gpt-5.6-luna", replica="1", response_id="resp-one"
    )
    second = success_record(
        case="case two",
        semantic_fingerprint=semantic(2),
        model="gpt-5.6-luna",
        replica="2",
        response_id="resp-two",
        evaluation_run_id=first["evaluation_run_id"],
    )
    complete_path = write_jsonl(tmp_path / "complete.jsonl", [first, second])
    lines = complete_path.read_text().splitlines()
    incomplete_path = tmp_path / "discarded.jsonl"
    incomplete_path.write_text("\n".join(lines[:-1]) + "\n")

    with pytest.raises(ValueError, match="missing evidence for 1 manifest invocation"):
        v2_recall_compare.load_evidence(
            incomplete_path, label="luna-1", provider="luna"
        )

    no_manifest = tmp_path / "no-manifest.jsonl"
    no_manifest.write_text(json.dumps(first) + "\n")
    with pytest.raises(ValueError, match="precedes the run manifest"):
        v2_recall_compare.load_evidence(
            no_manifest, label="luna-1", provider="luna"
        )


def test_manifest_and_typed_outcome_must_match_each_evidence_row(
    tmp_path: Path,
) -> None:
    record = success_record(
        model="gpt-5.6-luna", replica="1", response_id="resp-one"
    )
    record["evidence_outcome"] = "failure"
    path = write_jsonl(tmp_path / "mistyped.jsonl", [record])
    with pytest.raises(ValueError, match="must exactly match"):
        v2_recall_compare.load_evidence(path, label="luna-1", provider="luna")


@pytest.mark.parametrize(("input_tokens", "output_tokens"), [(0, 10), (100, 0)])
def test_success_evidence_requires_positive_billable_usage(
    tmp_path: Path, input_tokens: int, output_tokens: int
) -> None:
    record = success_record(
        model="gpt-5.6-luna",
        replica="1",
        response_id="resp-one",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    path = write_jsonl(tmp_path / "zero-success.jsonl", [record])

    with pytest.raises(ValueError, match="requires positive input and output tokens"):
        v2_recall_compare.load_evidence(path, label="luna-1", provider="luna")


@pytest.mark.parametrize("field", ["feedback", "mastery_summary"])
def test_success_evidence_requires_both_adopted_text_fields(
    tmp_path: Path, field: str
) -> None:
    record = success_record(
        model="gpt-5.6-luna", replica="1", response_id="resp-one"
    )
    record["result"][field] = "  "
    path = write_jsonl(tmp_path / f"empty-{field}.jsonl", [record])

    with pytest.raises(ValueError, match=f"{field} must be non-empty text"):
        v2_recall_compare.load_evidence(path, label="luna-1", provider="luna")


def test_manifest_preflight_rejects_tampered_counts_or_ceiling(
    tmp_path: Path,
) -> None:
    record = success_record(
        model="gpt-5.6-luna", replica="1", response_id="resp-one"
    )
    valid_path = write_jsonl(tmp_path / "valid.jsonl", [record])
    rows = [json.loads(line) for line in valid_path.read_text().splitlines()]
    fingerprint = record["fingerprint"]
    rows[0]["preflight"]["input_counts"][fingerprint] = True
    bad_count = tmp_path / "bad-count.jsonl"
    bad_count.write_text("".join(f"{json.dumps(row)}\n" for row in rows))
    with pytest.raises(ValueError, match="non-negative exact integers"):
        v2_recall_compare.load_evidence(
            bad_count, label="luna-1", provider="luna"
        )

    rows = [json.loads(line) for line in valid_path.read_text().splitlines()]
    rows[0]["preflight"]["estimated_ceiling_usd"] = "0.9"
    bad_ceiling = tmp_path / "bad-ceiling.jsonl"
    bad_ceiling.write_text("".join(f"{json.dumps(row)}\n" for row in rows))
    with pytest.raises(ValueError, match="ceiling does not match"):
        v2_recall_compare.load_evidence(
            bad_ceiling, label="luna-1", provider="luna"
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("created_at", "2026-08-13T00:00:00-07:00", "ISO-8601 UTC timestamp"),
        (
            "qualification_expires_at",
            "2026-09-13T00:00:00Z",
            "30-day evidence window",
        ),
    ],
)
def test_manifest_requires_strict_utc_times_inside_the_30_day_window(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    record = success_record(
        model="gpt-5.6-luna", replica="1", response_id="resp-one"
    )
    valid_path = write_jsonl(tmp_path / "valid-time.jsonl", [record])
    rows = [json.loads(line) for line in valid_path.read_text().splitlines()]
    rows[0][field] = value
    tampered = tmp_path / f"bad-time-{field}.jsonl"
    tampered.write_text("".join(f"{json.dumps(row)}\n" for row in rows))

    with pytest.raises(ValueError, match=message):
        v2_recall_compare.load_evidence(
            tampered, label="luna-1", provider="luna"
        )


def test_comparison_rejects_elapsed_qualification_evidence(tmp_path: Path) -> None:
    claude, lunas = four_runs(tmp_path)

    with pytest.raises(ValueError, match="qualification evidence has expired"):
        v2_recall_compare.validate_alignment(
            claude,
            lunas,
            expected_claude_model=EXPECTED_CLAUDE_MODEL,
            expected_claude_effort=EXPECTED_CLAUDE_EFFORT,
            expected_luna_qualification_fingerprint=QUALIFICATION_FINGERPRINT,
            expected_stage2_pack_fingerprint=PACK_FINGERPRINT,
            expected_qualification_expires_at=QUALIFICATION_EXPIRES_AT,
            as_of=QUALIFICATION_EXPIRES_AT,
        )


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("format_version", 2, "unsupported safety-identifier format version"),
        ("value", "0" * 64, "unexpected evaluation safety identifier"),
        ("kind", "account", "marked synthetic non-user"),
    ],
)
def test_luna_manifest_attests_the_synthetic_safety_identifier(
    tmp_path: Path, field: str, replacement: object, message: str
) -> None:
    record = success_record(
        model="gpt-5.6-luna", replica="1", response_id="resp-one"
    )
    valid_path = write_jsonl(tmp_path / "valid.jsonl", [record])
    rows = [json.loads(line) for line in valid_path.read_text().splitlines()]
    rows[0]["safety_identifier"][field] = replacement
    tampered = tmp_path / f"bad-safety-{field}.jsonl"
    tampered.write_text("".join(f"{json.dumps(row)}\n" for row in rows))

    with pytest.raises(ValueError, match=message):
        v2_recall_compare.load_evidence(
            tampered, label="luna-1", provider="luna"
        )


def test_comparison_rates_must_match_each_approved_manifest(tmp_path: Path) -> None:
    claude, lunas = four_runs(tmp_path)

    with pytest.raises(ValueError, match="luna-1: comparison rates differ"):
        v2_recall_compare.build_report(
            claude,
            lunas,
            reviews_for(lunas, reviewed_cases_for_run(claude)),
            reviewed_cases_for_run(claude),
            claude_input_price=Decimal("1"),
            claude_output_price=Decimal("1"),
            claude_cache_read_price=Decimal("1"),
            claude_cache_write_price=Decimal("1"),
            luna_input_price=Decimal("2"),
            luna_output_price=Decimal("1"),
            luna_cached_input_price=Decimal("1"),
            luna_cache_write_price=Decimal("1"),
            expected_claude_model=EXPECTED_CLAUDE_MODEL,
            expected_claude_effort=EXPECTED_CLAUDE_EFFORT,
            expected_luna_qualification_fingerprint=QUALIFICATION_FINGERPRINT,
            expected_stage2_pack_fingerprint=PACK_FINGERPRINT,
            qualification_expires_at=QUALIFICATION_EXPIRES_AT,
            as_of=COMPARISON_AS_OF,
        )

def test_alignment_rejects_mixed_effort_or_qualification_and_reused_rows(
    tmp_path: Path,
) -> None:
    claude, lunas = four_runs(tmp_path)

    high_effort_record = success_record(
        model="gpt-5.6-luna", replica="7", response_id="resp-high"
    )
    high_effort_record["effort"] = "high"
    high_effort = v2_recall_compare.load_evidence(
        write_jsonl(tmp_path / "luna-high.jsonl", [high_effort_record]),
        label="luna-high",
        provider="luna",
    )
    with pytest.raises(ValueError, match="mixed effort"):
        v2_recall_compare.validate_alignment(
            claude,
            [lunas[0], high_effort, lunas[2]],
            expected_claude_model=EXPECTED_CLAUDE_MODEL,
            expected_claude_effort=EXPECTED_CLAUDE_EFFORT,
            expected_luna_qualification_fingerprint=QUALIFICATION_FINGERPRINT,
            expected_stage2_pack_fingerprint=PACK_FINGERPRINT,
        )

    other_qualification_record = success_record(
        model="gpt-5.6-luna", replica="8", response_id="resp-other-qualification"
    )
    other_qualification_record["qualification_fingerprint"] = "c" * 64
    other_qualification = v2_recall_compare.load_evidence(
        write_jsonl(
            tmp_path / "luna-other-qualification.jsonl",
            [other_qualification_record],
        ),
        label="luna-other-qualification",
        provider="luna",
    )
    with pytest.raises(ValueError, match="qualification fingerprint mismatch"):
        v2_recall_compare.validate_alignment(
            claude,
            [lunas[0], other_qualification, lunas[2]],
            expected_claude_model=EXPECTED_CLAUDE_MODEL,
            expected_claude_effort=EXPECTED_CLAUDE_EFFORT,
            expected_luna_qualification_fingerprint=QUALIFICATION_FINGERPRINT,
            expected_stage2_pack_fingerprint=PACK_FINGERPRINT,
        )

    reused_record = success_record(
        model="gpt-5.6-luna", replica="9", response_id="resp-reused"
    )
    reused_record["resumed"] = True
    with pytest.raises(ValueError, match="cannot contain resumed rows"):
        v2_recall_compare.load_evidence(
            write_jsonl(tmp_path / "luna-reused.jsonl", [reused_record]),
            label="luna-reused",
            provider="luna",
        )

    unmarked_record = success_record(
        model="gpt-5.6-luna", replica="10", response_id="resp-unmarked"
    )
    unmarked_record.pop("fresh")
    with pytest.raises(ValueError, match="marked fresh"):
        v2_recall_compare.load_evidence(
            write_jsonl(tmp_path / "luna-unmarked.jsonl", [unmarked_record]),
            label="luna-unmarked",
            provider="luna",
        )


def test_alignment_rejects_expensive_or_unstabilized_claude_baselines(
    tmp_path: Path,
) -> None:
    _claude, lunas = four_runs(tmp_path)
    opus = v2_recall_compare.load_evidence(
        write_jsonl(
            tmp_path / "claude-opus.jsonl",
            [success_record(model="claude-opus-5", replica="7")],
        ),
        label="claude",
        provider="claude",
    )
    with pytest.raises(ValueError, match="Claude baseline model mismatch"):
        v2_recall_compare.validate_alignment(
            opus,
            lunas,
            expected_claude_model=EXPECTED_CLAUDE_MODEL,
            expected_claude_effort=EXPECTED_CLAUDE_EFFORT,
            expected_luna_qualification_fingerprint=QUALIFICATION_FINGERPRINT,
            expected_stage2_pack_fingerprint=PACK_FINGERPRINT,
        )

    high = v2_recall_compare.load_evidence(
        write_jsonl(
            tmp_path / "claude-high.jsonl",
            [
                success_record(
                    model=EXPECTED_CLAUDE_MODEL,
                    effort="high",
                    replica="8",
                )
            ],
        ),
        label="claude",
        provider="claude",
    )
    with pytest.raises(ValueError, match="Claude baseline effort mismatch"):
        v2_recall_compare.validate_alignment(
            high,
            lunas,
            expected_claude_model=EXPECTED_CLAUDE_MODEL,
            expected_claude_effort=EXPECTED_CLAUDE_EFFORT,
            expected_luna_qualification_fingerprint=QUALIFICATION_FINGERPRINT,
            expected_stage2_pack_fingerprint=PACK_FINGERPRINT,
        )


def test_alignment_rejects_missing_semantics_and_payload_mismatches(
    tmp_path: Path,
) -> None:
    claude, lunas = four_runs(tmp_path)
    claude_record = list(claude.outcomes.values())[0].record
    extra_claude_record = success_record(
        case="case two",
        semantic_fingerprint=semantic(2),
        model="claude-sonnet-5",
        replica="6",
        evaluation_run_id=claude_record["evaluation_run_id"],
    )
    extra_claude_record["qualification_fingerprint"] = claude_record[
        "qualification_fingerprint"
    ]
    claude_two_path = write_jsonl(
        tmp_path / "claude-two.jsonl",
        [
            claude_record,
            extra_claude_record,
        ],
    )
    claude_two = v2_recall_compare.load_evidence(
        claude_two_path, label="claude", provider="claude"
    )
    with pytest.raises(ValueError, match="case alignment mismatch"):
        v2_recall_compare.validate_alignment(
            claude_two,
            lunas,
            expected_claude_model=EXPECTED_CLAUDE_MODEL,
            expected_claude_effort=EXPECTED_CLAUDE_EFFORT,
            expected_luna_qualification_fingerprint=QUALIFICATION_FINGERPRINT,
            expected_stage2_pack_fingerprint=PACK_FINGERPRINT,
        )

    changed_semantic_path = write_jsonl(
        tmp_path / "luna-semantic-mismatch.jsonl",
        [
            success_record(
                model="gpt-5.6-luna",
                replica="4",
                semantic_fingerprint=semantic(2),
                response_id="resp-4",
            )
        ],
    )
    changed_semantic = v2_recall_compare.load_evidence(
        changed_semantic_path, label="luna-semantic-mismatch", provider="luna"
    )
    with pytest.raises(ValueError, match="semantic fingerprint mismatch"):
        v2_recall_compare.validate_alignment(
            claude,
            [changed_semantic, lunas[1], lunas[2]],
            expected_claude_model=EXPECTED_CLAUDE_MODEL,
            expected_claude_effort=EXPECTED_CLAUDE_EFFORT,
            expected_luna_qualification_fingerprint=QUALIFICATION_FINGERPRINT,
            expected_stage2_pack_fingerprint=PACK_FINGERPRINT,
        )

    changed_payload_path = write_jsonl(
        tmp_path / "luna-payload-mismatch.jsonl",
        [
                success_record(
                    model="gpt-5.6-luna",
                    replica="5",
                    semantic_fingerprint=next(iter(claude.outcomes)),
                    expected_recall=5,
                recall=5,
                response_id="resp-5",
            )
        ],
    )
    changed_payload = v2_recall_compare.load_evidence(
        changed_payload_path, label="luna-payload-mismatch", provider="luna"
    )
    with pytest.raises(ValueError, match="payload mismatch"):
        v2_recall_compare.validate_alignment(
            claude,
            [changed_payload, lunas[1], lunas[2]],
            expected_claude_model=EXPECTED_CLAUDE_MODEL,
            expected_claude_effort=EXPECTED_CLAUDE_EFFORT,
            expected_luna_qualification_fingerprint=QUALIFICATION_FINGERPRINT,
            expected_stage2_pack_fingerprint=PACK_FINGERPRINT,
        )


def test_payload_rejects_a_tampered_semantic_or_stored_decision(tmp_path: Path) -> None:
    record = success_record(
        model="gpt-5.6-luna", replica="1", response_id="resp-1"
    )
    record["semantic_fingerprint"] = semantic(2)
    path = write_jsonl(tmp_path / "tampered-semantic.jsonl", [record])
    with pytest.raises(ValueError, match="top-level semantic fingerprint"):
        v2_recall_compare.load_evidence(path, label="luna-1", provider="luna")

    record = success_record(
        model="gpt-5.6-luna", replica="2", response_id="resp-2"
    )
    record["result"]["decision"] = "again"
    path = write_jsonl(tmp_path / "tampered-decision.jsonl", [record])
    with pytest.raises(ValueError, match="stored decision"):
        v2_recall_compare.load_evidence(path, label="luna-2", provider="luna")


@pytest.mark.parametrize(
    ("follow_up_question", "needs_more_evidence"),
    [("", True), ("What evidence distinguishes the adjacent scores?", False)],
)
def test_frozen_binding_reparses_hand_edited_follow_up_policy_fields(
    tmp_path: Path,
    follow_up_question: str,
    needs_more_evidence: bool,
) -> None:
    case = trusted_case(
        expected_recall=2,
        expected_flow="follow_up",
        probes=[
            {
                "question": "First trusted probe for case one?",
                "answer": "First trusted probe answer for case one.",
            }
        ],
    )
    record = bound_success_record(
        case,
        model="gpt-5.6-luna",
        replica="1",
        recall=2,
        response_id="resp-strict",
        needs_more_evidence=needs_more_evidence,
        follow_up_question=follow_up_question,
    )
    run = v2_recall_compare.load_evidence(
        write_jsonl(
            tmp_path / "hand-edited-follow-up.jsonl",
            [record],
            output_allowance=2048,
        ),
        label="luna",
        provider="luna",
    )

    with pytest.raises(ValueError, match="strict V2 contract"):
        v2_recall_compare.validate_frozen_case_bindings([run], [case])


def test_artifact_reader_does_not_coerce_non_string_follow_up_text(
    tmp_path: Path,
) -> None:
    record = bound_success_record(
        _DEFAULT_TRUSTED_CASE,
        model="gpt-5.6-luna",
        replica="1",
        response_id="resp-non-string",
    )
    record["result"]["follow_up_question"] = False

    with pytest.raises(ValueError, match="follow_up_question must be text"):
        v2_recall_compare.load_evidence(
            write_jsonl(
                tmp_path / "non-string-follow-up.jsonl",
                [record],
                output_allowance=2048,
            ),
            label="luna",
            provider="luna",
        )


def test_frozen_binding_rejects_a_manifest_and_row_relabel(
    tmp_path: Path,
) -> None:
    record = bound_success_record(
        _DEFAULT_TRUSTED_CASE,
        model="gpt-5.6-luna",
        replica="1",
        response_id="resp-relabel",
    )
    record["case"] = "relabelled case"
    run = v2_recall_compare.load_evidence(
        write_jsonl(
            tmp_path / "relabelled.jsonl", [record], output_allowance=2048
        ),
        label="luna",
        provider="luna",
    )

    with pytest.raises(ValueError, match="manifest invocation identity"):
        v2_recall_compare.validate_frozen_case_bindings(
            [run], [_DEFAULT_TRUSTED_CASE]
        )


def test_frozen_binding_rejects_swapped_case_results(
    tmp_path: Path,
) -> None:
    cases = [trusted_case(), trusted_case(name="case two")]
    first = bound_success_record(
        cases[0],
        model="gpt-5.6-luna",
        replica="1",
        response_id="resp-swap-one",
    )
    second = bound_success_record(
        cases[1],
        model="gpt-5.6-luna",
        replica="1",
        response_id="resp-swap-two",
        evaluation_run_id=first["evaluation_run_id"],
    )
    first["result"], second["result"] = second["result"], first["result"]
    pack_fingerprint = v2_recall_eval.stage2_pack_fingerprint(cases)
    first["stage2_pack_fingerprint"] = pack_fingerprint
    second["stage2_pack_fingerprint"] = pack_fingerprint
    run = v2_recall_compare.load_evidence(
        write_jsonl(
            tmp_path / "swapped.jsonl",
            [first, second],
            output_allowance=2048,
        ),
        label="luna",
        provider="luna",
    )

    with pytest.raises(ValueError, match="evidence identity or expected mapping"):
        v2_recall_compare.validate_frozen_case_bindings([run], cases)


def test_frozen_binding_rejects_a_tampered_expected_label_mapping(
    tmp_path: Path,
) -> None:
    record = bound_success_record(
        _DEFAULT_TRUSTED_CASE,
        model="gpt-5.6-luna",
        replica="1",
        response_id="resp-label-tamper",
    )
    record["result"]["expected_recall"] = 5
    record["result"]["expected_decision"] = v2_recall_eval.product_decision(
        "complete", 5
    )
    run = v2_recall_compare.load_evidence(
        write_jsonl(
            tmp_path / "label-tamper.jsonl", [record], output_allowance=2048
        ),
        label="luna",
        provider="luna",
    )

    with pytest.raises(ValueError, match="evidence identity or expected mapping"):
        v2_recall_compare.validate_frozen_case_bindings(
            [run], [_DEFAULT_TRUSTED_CASE]
        )


def test_mastery_band_flip_fails_even_when_recall_is_within_one(tmp_path: Path) -> None:
    claude, lunas = four_runs(
        tmp_path,
        expected_recall=3,
        claude_recall=3,
        luna_recalls=(4, 3, 3),
    )

    report = report_for(claude, lunas)

    assert not report.passed
    assert report.metrics[1].within_one == 1
    assert any("product decisions" in failure for failure in report.gate_failures)
    assert any(
        disagreement.expected["coverage_tier"] == "developing"
        and disagreement.actual["coverage_tier"] == "solid"
        for disagreement in report.human_disagreements
    )
    assert any(
        "product decision changed" in failure for failure in report.gate_failures
    )


def test_cost_per_success_must_be_reduced_by_at_least_85_percent(
    tmp_path: Path,
) -> None:
    claude, lunas = four_runs(tmp_path, luna_tokens=(200, 20))

    report = report_for(claude, lunas)

    assert report.cost_reduction == Decimal("0.8")
    assert not report.passed
    assert any("below the required 85%" in failure for failure in report.gate_failures)


def test_provider_specific_cached_token_costs_are_invoice_shaped(
    tmp_path: Path,
) -> None:
    claude, lunas = four_runs(
        tmp_path,
        claude_tokens=(100, 10),
        luna_tokens=(100, 10),
        claude_cache=(20, 5),
        luna_cached_input=40,
        luna_cache_write=10,
        claude_manifest_rates={
            "input": Decimal("2"),
            "output": Decimal("3"),
            "cached_input": Decimal("0.2"),
            "cache_write": Decimal("2.5"),
        },
        luna_manifest_rates={
            "input": Decimal("2"),
            "output": Decimal("3"),
            "cached_input": Decimal("0.2"),
            "cache_write": Decimal("1.5"),
        },
    )

    report = v2_recall_compare.build_report(
        claude,
        lunas,
        reviews_for(lunas, reviewed_cases_for_run(claude)),
        reviewed_cases_for_run(claude),
        claude_input_price=Decimal("2"),
        claude_output_price=Decimal("3"),
        claude_cache_read_price=Decimal("0.2"),
        claude_cache_write_price=Decimal("2.5"),
        luna_input_price=Decimal("2"),
        luna_output_price=Decimal("3"),
        luna_cached_input_price=Decimal("0.2"),
        luna_cache_write_price=Decimal("1.5"),
        expected_claude_model=EXPECTED_CLAUDE_MODEL,
        expected_claude_effort=EXPECTED_CLAUDE_EFFORT,
        expected_luna_qualification_fingerprint=QUALIFICATION_FINGERPRINT,
        expected_stage2_pack_fingerprint=PACK_FINGERPRINT,
        qualification_expires_at=QUALIFICATION_EXPIRES_AT,
        as_of=COMPARISON_AS_OF,
    )

    assert report.metrics[0].cost_per_success == Decimal("0.0002465")
    assert report.luna_aggregate.cost_per_success == Decimal("0.000153")


def test_encoded_invalid_responses_are_counted_and_fail_the_gate(tmp_path: Path) -> None:
    claude, lunas = four_runs(tmp_path)
    case = reviewed_cases_for_run(claude)[0]
    invalid_path = write_jsonl(
        tmp_path / "luna-invalid.jsonl",
        [bound_failure_record(case, model="gpt-5.6-luna", replica="4")],
        output_allowance=2048,
    )
    invalid = v2_recall_compare.load_evidence(
        invalid_path, label="luna-invalid", provider="luna"
    )

    report = report_for(claude, [invalid, lunas[1], lunas[2]])

    assert report.metrics[1].invalids == 1
    assert report.metrics[1].failures == 1
    assert any("encoded invalid_schema failure" in failure for failure in report.gate_failures)
