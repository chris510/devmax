import argparse
import json
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.llm import ReattemptResult, derive_composite
from scripts import effort_sweep, openai_bakeoff, reattempt_effort_sweep
from scripts import effort_sweep_support as support
from scripts.effort_sweep_support import hydrate_grounding
from scripts.scoring_prompt_variants import (
    EXPLICIT_EVIDENCE_V1,
    EXPLICIT_EVIDENCE_V2,
    EXPLICIT_EVIDENCE_V3,
    EXPLICIT_EVIDENCE_V4,
    EXPLICIT_EVIDENCE_V5,
    EXPLICIT_EVIDENCE_V5_RULES,
    EXPLICIT_EVIDENCE_V6,
    EXPLICIT_EVIDENCE_V6_RULES,
    EXPLICIT_EVIDENCE_V7,
    EXPLICIT_EVIDENCE_V7_RULES,
    EXPLICIT_EVIDENCE_V8,
    EXPLICIT_EVIDENCE_V8_RULES,
    PRODUCTION,
    apply_scoring_prompt_variant,
)

API = Path(__file__).resolve().parent.parent
SCORING_CASES = API / "scripts" / "grounded_effort_cases_week1.json"
RELEASE_CASES = API / "scripts" / "grounded_effort_cases_week1_release.json"
REATTEMPT_CASES = API / "scripts" / "grounded_reattempt_cases_week1.json"


def grounding_entry(**changes) -> dict:
    entry = {
        "topic": "Cursor pagination",
        "grounding_status": "approved",
        "source_url": "https://example.com/cursor",
        "source_section": "Cursor pagination",
        "source_label": "Reviewed source",
        "answer_basis": "Resume after the last stable ordered key.",
        "answer_rubric": {
            "mechanism": "Use the last stable ordered key.",
            "acceptable_alternative": "Keyset pagination.",
            "trade_off": "No arbitrary page jumps.",
            "failure_mode": "A mutable key duplicates or skips rows.",
            "misconception": "A cursor is not a page number.",
        },
        "canonical_question": "How does a cursor preserve stable traversal?",
    }
    entry.update(changes)
    return entry


def write_manifest(tmp_path, entry: dict):
    path = tmp_path / "cards.json"
    path.write_text(json.dumps([entry]))
    return path


def test_evaluation_hydrates_only_from_the_reviewed_manifest(tmp_path) -> None:
    cases = [{"topic": "Cursor pagination", "answer": "Use the last ordered key."}]

    hydrated = hydrate_grounding(
        cases, write_manifest(tmp_path, grounding_entry()), argparse.ArgumentParser()
    )

    assert hydrated[0]["question"] == "How does a cursor preserve stable traversal?"
    assert hydrated[0]["answer_basis"].startswith("Resume after")
    assert hydrated[0]["answer_rubric"]["misconception"].startswith("A cursor")
    assert "question" not in cases[0]


def test_evaluation_refuses_a_machine_draft_before_live_calls(tmp_path) -> None:
    cases = [{"topic": "Cursor pagination", "answer": "Use the last ordered key."}]
    manifest = write_manifest(
        tmp_path, grounding_entry(grounding_status="draft_review")
    )

    with pytest.raises(SystemExit, match="2"):
        hydrate_grounding(cases, manifest, argparse.ArgumentParser())


def test_week_one_scoring_pack_has_three_consistent_labels_per_card() -> None:
    cases = json.loads(SCORING_CASES.read_text())

    assert len(cases) == 18
    assert set(Counter(case["topic"] for case in cases).values()) == {3}
    for case in cases:
        axes = (
            case["expected_accuracy"],
            case["expected_depth"],
            case["expected_boundaries"],
        )
        assert case["expected_score"] == derive_composite(*axes)
        assert not {"question", "answer_basis", "answer_rubric"} & case.keys()


def test_release_pack_has_seven_approved_families_per_card() -> None:
    cases = json.loads(RELEASE_CASES.read_text())
    family_tags = {
        "partial-self-correction",
        "source-compatible-alternative",
        "speech-noise",
        "adjacent-jargon",
        "follow-up-anchored",
        "prior-summary-contradiction",
        "axis-isolation",
    }

    assert len(cases) == 42
    assert set(Counter(case["topic"] for case in cases).values()) == {7}
    assert sum("risk-smoke" in case["tags"] for case in cases) == 12
    risk_cases = [case for case in cases if "risk-smoke" in case["tags"]]
    assert Counter(case["expected_accuracy"] >= 3 for case in risk_cases) == {
        True: 6,
        False: 6,
    }
    assert Counter(
        tag for case in cases for tag in case["tags"] if tag in family_tags
    ) == {tag: 6 for tag in family_tags}
    sol_held_out = [case for case in cases if "sol-secondary-heldout" in case["tags"]]
    sol_canary = [case for case in cases if "sol-secondary-canary" in case["tags"]]
    sol_remainder = [case for case in cases if "sol-secondary-remainder" in case["tags"]]
    assert len(sol_held_out) == 24
    assert len(sol_canary) == 6
    assert len(sol_remainder) == 18
    assert {case["name"] for case in sol_canary}.isdisjoint(
        case["name"] for case in sol_remainder
    )
    assert Counter(case["topic"] for case in sol_canary) == {
        topic: 1 for topic in {case["topic"] for case in cases}
    }
    assert Counter(case["expected_accuracy"] >= 3 for case in sol_canary) == {
        True: 3,
        False: 3,
    }
    assert sum(case["expected_depth"] >= 3 for case in sol_canary) == 2
    assert sum(case["expected_boundaries"] >= 3 for case in sol_canary) == 2
    for case in cases:
        axes = (
            case["expected_accuracy"],
            case["expected_depth"],
            case["expected_boundaries"],
        )
        assert case["expected_score"] == derive_composite(*axes)
        assert case["review_status"] == "approved"
        assert "release-pack" in case["tags"]
        assert case["review_note"]
        assert not {"question", "answer_basis", "answer_rubric"} & case.keys()


def test_release_pack_conversational_families_have_required_evidence() -> None:
    cases = json.loads(RELEASE_CASES.read_text())
    follow_ups = [case for case in cases if "follow-up-anchored" in case["tags"]]
    contradictions = [
        case for case in cases if "prior-summary-contradiction" in case["tags"]
    ]
    depth_only = [case for case in cases if "depth-only" in case["tags"]]
    boundaries_only = [case for case in cases if "boundaries-only" in case["tags"]]

    assert len(follow_ups) == len(contradictions) == 6
    assert all(case["follow_up_question"] and case["follow_up_answer"] for case in follow_ups)
    assert all(case["mastery_summary"] for case in contradictions)
    assert len(depth_only) == len(boundaries_only) == 3
    assert all(
        case["expected_depth"] >= 3 and case["expected_boundaries"] <= 2
        for case in depth_only
    )
    assert all(
        case["expected_depth"] <= 2 and case["expected_boundaries"] >= 3
        for case in boundaries_only
    )


def test_release_pack_hydrates_against_the_six_approved_cards() -> None:
    cases = json.loads(RELEASE_CASES.read_text())

    hydrated = hydrate_grounding(
        cases, API / "cards.json", argparse.ArgumentParser()
    )

    assert len(hydrated) == 42
    assert all(case["question"] and case["answer_basis"] for case in hydrated)


def test_week_one_reattempt_pack_has_two_authority_free_cases_per_card() -> None:
    cases = json.loads(REATTEMPT_CASES.read_text())

    assert len(cases) == 12
    assert set(Counter(case["topic"] for case in cases).values()) == {2}
    for case in cases:
        assert not {"question", "answer_basis", "answer_rubric"} & case.keys()


def test_week_one_smoke_pack_is_six_scoring_and_four_reattempt_cases() -> None:
    scoring = json.loads(SCORING_CASES.read_text())
    reattempt = json.loads(REATTEMPT_CASES.read_text())

    assert sum("smoke" in case.get("tags", []) for case in scoring) == 6
    assert sum("smoke" in case.get("tags", []) for case in reattempt) == 4


def test_shipping_effort_is_the_default_and_comparison_is_explicit() -> None:
    assert support.levels_for(None, "low") == ["low"]
    assert support.levels_for(["low", "medium"], "low") == ["low", "medium"]


def test_case_filters_combine_exact_names_with_any_requested_tag() -> None:
    cases = [
        {"name": "one", "tags": ["smoke", "accuracy"]},
        {"name": "two", "tags": ["depth"]},
        {"name": "three", "tags": ["smoke"]},
    ]

    selected = support.select_cases(
        cases,
        names=["one", "two"],
        tags=["smoke"],
        parser=argparse.ArgumentParser(),
    )

    assert [case["name"] for case in selected] == ["one"]


def test_unknown_case_filter_fails_before_grounding_or_api_calls() -> None:
    with pytest.raises(SystemExit, match="2"):
        support.select_cases(
            [{"name": "known"}],
            names=["missing"],
            tags=None,
            parser=argparse.ArgumentParser(),
        )


def prepared_call(**case_changes):
    case = {
        "name": "case",
        "topic": "Topic",
        "answer": "Answer",
        "expected_score": 3,
        **case_changes,
    }
    completion = {
        "model": "claude-sonnet-5",
        "effort": "low",
        "rubric": "rubric",
        "user_content": "content",
        "schema": {"type": "object"},
        "max_tokens": 128,
    }
    return support.prepare_call(
        index=0,
        case=case,
        kind="scoring",
        effort="low",
        completion=completion,
    )


def test_fingerprint_invalidates_when_a_label_or_request_changes() -> None:
    original = prepared_call()
    changed_label = prepared_call(expected_score=4)
    retagged = prepared_call(tags=["smoke"])
    changed_completion = support.prepare_call(
        index=0,
        case=original.case,
        kind="scoring",
        effort="medium",
        completion={**original.completion, "effort": "medium"},
    )

    assert original.fingerprint != changed_label.fingerprint
    assert original.fingerprint != changed_completion.fingerprint
    assert original.fingerprint == retagged.fingerprint


def test_review_metadata_does_not_change_the_paid_request_fingerprint() -> None:
    candidate = prepared_call(
        review_status="candidate", review_note="Human should inspect this boundary."
    )
    approved = prepared_call(
        review_status="approved", review_note="Boundary was inspected."
    )

    assert candidate.fingerprint == approved.fingerprint


def test_jsonl_results_are_durable_and_resume_by_exact_fingerprint(tmp_path) -> None:
    prepared = prepared_call()
    record = support.make_result_record(
        prepared,
        model="claude-sonnet-5",
        result={"score": 3},
        usage=support.Usage(input_tokens=100, output_tokens=20),
    )
    path = tmp_path / "results.jsonl"

    with support.JsonlRecorder(path) as recorder:
        recorder.append(
            support.make_run_manifest(
                kind="scoring",
                evaluation_run_id="00000000-0000-0000-0000-000000000001",
                provider="anthropic",
                model="claude-sonnet-5",
                stage2_pack_fingerprint="a" * 64,
                calls=[prepared],
                qualification_fingerprints={prepared.fingerprint: "b" * 64},
                approved_max_cost_usd=Decimal("1"),
                rates_per_million_usd={
                    "input": Decimal("2"),
                    "output": Decimal("10"),
                    "cached_input": Decimal("0.2"),
                    "cache_write": Decimal("2.5"),
                },
                input_count_method=support.INPUT_COUNT_ANTHROPIC_EXACT,
                input_counts={prepared.fingerprint: 100},
                estimate=support.CostEstimate(
                    calls=1,
                    input_tokens=100,
                    output_tokens=20,
                    usd=Decimal("0.00045"),
                    output_tokens_per_call=20,
                ),
                qualification_expires_at=(
                    datetime.now(UTC) + timedelta(days=1)
                ),
            )
        )
        recorder.append(record)
        assert path.read_text().endswith("\n")

    assert record["scoring_prompt_variant"] == PRODUCTION
    assert support.load_result_records(path, kind="scoring") == {
        prepared.fingerprint: record
    }


def test_sonnet_five_price_schedule_changes_after_the_promotion() -> None:
    promotional = support.rate_for_model(
        "claude-sonnet-5", on_date=date(2026, 8, 31)
    )
    standard = support.rate_for_model(
        "claude-sonnet-5", on_date=date(2026, 9, 1)
    )

    assert (promotional.input_per_million, promotional.output_per_million) == (
        Decimal("2"),
        Decimal("10"),
    )
    assert (standard.input_per_million, standard.output_per_million) == (
        Decimal("3"),
        Decimal("15"),
    )


def test_cost_estimate_uses_counted_input_and_conservative_output() -> None:
    first = prepared_call()
    second = support.prepare_call(
        index=1,
        case={**first.case, "name": "second"},
        kind="scoring",
        effort="low",
        completion={**first.completion, "user_content": "different"},
    )
    rate = support.rate_for_model(
        "claude-sonnet-5", on_date=date(2026, 8, 31)
    )

    estimate = support.estimate_cost(
        [first, second],
        input_counts={first.fingerprint: 100, second.fingerprint: 200},
        prior_records=[],
        fallback_output_tokens=50,
        rate=rate,
    )

    assert estimate.input_tokens == 300
    assert estimate.output_tokens == 100
    assert estimate.usd == Decimal("0.0016")


def test_preflight_cost_display_rounds_up_to_an_authorizable_budget() -> None:
    assert support.cost_ceiling_for_display(Decimal("0.321850")) == Decimal(
        "0.3219"
    )
    assert support.cost_ceiling_for_display(Decimal("0.321800")) == Decimal(
        "0.3218"
    )


@pytest.mark.anyio
async def test_preflight_uses_free_token_count_shape_without_max_tokens() -> None:
    prepared = prepared_call()

    class Messages:
        def __init__(self):
            self.calls = []

        async def count_tokens(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(input_tokens=321)

    messages = Messages()
    counts = await support.count_prepared_calls(
        [prepared], concurrency=1, client=SimpleNamespace(messages=messages)
    )

    assert counts == {prepared.fingerprint: 321}
    assert "max_tokens" not in messages.calls[0]
    assert messages.calls[0]["output_config"]["effort"] == "low"


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_count", [True, -1, "7", 1.0, None])
async def test_anthropic_preflight_rejects_coercible_or_negative_counts(
    invalid_count,
) -> None:
    prepared = prepared_call()

    class Messages:
        async def count_tokens(self, **_kwargs):
            return SimpleNamespace(input_tokens=invalid_count)

    with pytest.raises(ValueError, match="non-negative exact integer"):
        await support.count_prepared_calls(
            [prepared],
            concurrency=1,
            client=SimpleNamespace(messages=Messages()),
        )


def test_paid_run_requires_an_explicit_sufficient_budget() -> None:
    estimate = support.CostEstimate(
        calls=1,
        input_tokens=100,
        output_tokens=20,
        usd=Decimal("0.01"),
        output_tokens_per_call=20,
    )
    parser = argparse.ArgumentParser()

    support.enforce_budget(estimate, budget=None, dry_run=True, parser=parser)
    with pytest.raises(SystemExit, match="2"):
        support.enforce_budget(estimate, budget=None, dry_run=False, parser=parser)
    with pytest.raises(SystemExit, match="2"):
        support.enforce_budget(
            estimate, budget=Decimal("0.009"), dry_run=False, parser=parser
        )
    support.enforce_budget(
        estimate, budget=Decimal("0.01"), dry_run=False, parser=parser
    )

    rounded = support.CostEstimate(
        calls=1,
        input_tokens=1,
        output_tokens=1,
        usd=Decimal("0.00001"),
        output_tokens_per_call=1,
    )
    with pytest.raises(SystemExit, match="2"):
        support.enforce_budget(
            rounded,
            budget=Decimal("0.00001"),
            dry_run=False,
            parser=parser,
        )
    support.enforce_budget(
        rounded,
        budget=Decimal("0.0001"),
        dry_run=False,
        parser=parser,
    )


def write_scoring_case(tmp_path) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            [
                {
                    "name": "one scoring case",
                    "topic": "Topic",
                    "question": "Question?",
                    "answer": "Answer.",
                    "expected_score": 5,
                    "expected_accuracy": 5,
                    "expected_depth": 4,
                    "expected_boundaries": 5,
                }
            ]
        )
    )
    return path


@pytest.mark.anyio
async def test_dry_run_counts_shipping_effort_without_paid_messages(
    monkeypatch, tmp_path
) -> None:
    cases = write_scoring_case(tmp_path)
    settings = effort_sweep.get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    counted: list[support.PreparedCall] = []

    async def count_calls(prepared, *, concurrency):
        counted.extend(prepared)
        return {call.fingerprint: 100 for call in prepared}

    async def forbidden_score(**_kwargs):
        raise AssertionError("dry-run reached a paid Message call")

    monkeypatch.setattr(effort_sweep, "count_prepared_calls", count_calls)
    monkeypatch.setattr(effort_sweep.llm, "_complete", forbidden_score)
    monkeypatch.setattr(
        effort_sweep.sys,
        "argv",
        ["effort_sweep.py", str(cases), "--dry-run"],
    )

    assert await effort_sweep.main() == 0
    assert len(counted) == 1
    assert counted[0].effort == settings.scoring_effort == "low"


@pytest.mark.anyio
async def test_missing_budget_refuses_before_paid_messages(monkeypatch, tmp_path) -> None:
    cases = write_scoring_case(tmp_path)
    settings = effort_sweep.get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")

    async def count_calls(prepared, *, concurrency):
        return {call.fingerprint: 100 for call in prepared}

    async def forbidden_score(**_kwargs):
        raise AssertionError("budget refusal reached a paid Message call")

    monkeypatch.setattr(effort_sweep, "count_prepared_calls", count_calls)
    monkeypatch.setattr(effort_sweep.llm, "_complete", forbidden_score)
    monkeypatch.setattr(effort_sweep.sys, "argv", ["effort_sweep.py", str(cases)])

    with pytest.raises(SystemExit, match="2"):
        await effort_sweep.main()


@pytest.mark.anyio
async def test_exact_resume_needs_no_api_key_or_paid_call(monkeypatch, tmp_path) -> None:
    cases_path = write_scoring_case(tmp_path)
    case = json.loads(cases_path.read_text())[0]
    settings = effort_sweep.get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    prepared = effort_sweep.prepare_cases(
        [case], levels=[settings.scoring_effort], model=settings.scoring_model
    )[0]
    prior = support.make_result_record(
        prepared,
        model=settings.scoring_model,
        result={
            "expected_score": 5,
            "score": 5,
            "expected_axes": [5, 4, 5],
            "axes": [5, 4, 5],
            "feedback": "saved",
        },
        usage=support.Usage(input_tokens=100, output_tokens=20),
    )
    resume_path = tmp_path / "prior.jsonl"
    with support.JsonlRecorder(resume_path) as recorder:
        recorder.append(prior)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("exact resume reached an API call")

    monkeypatch.setattr(effort_sweep, "count_prepared_calls", forbidden)
    monkeypatch.setattr(effort_sweep.llm, "_complete", forbidden)
    output_path = tmp_path / "resumed.jsonl"
    monkeypatch.setattr(
        effort_sweep.sys,
        "argv",
        [
            "effort_sweep.py",
            str(cases_path),
            "--resume",
            str(resume_path),
            "--output",
            str(output_path),
        ],
    )

    assert await effort_sweep.main() == 0
    assert json.loads(output_path.read_text())["fingerprint"] == prepared.fingerprint


@pytest.mark.anyio
async def test_scoring_sweep_reads_the_current_axis_contract(monkeypatch, tmp_path) -> None:
    async def complete(**kwargs):
        assert "EXPLICIT EVIDENCE ATTRIBUTION V1" in kwargs["rubric"]
        return {
            "accuracy": 5,
            "depth": 4,
            "boundaries": 5,
            "feedback": "saved",
            "follow_up_question": "One more — why?",
            "mastery_summary": "solid",
        }

    monkeypatch.setattr(effort_sweep.llm, "_complete", complete)
    case = {
        "name": "current scoring contract",
        "topic": "Topic",
        "question": "Question?",
        "answer": "Answer.",
        "expected_score": 5,
        "expected_accuracy": 5,
        "expected_depth": 4,
        "expected_boundaries": 5,
    }
    prepared = effort_sweep.prepare_cases(
        [case],
        levels=["low"],
        model="claude-sonnet-5",
        prompt_variant=EXPLICIT_EVIDENCE_V1,
    )[0]
    with effort_sweep.JsonlRecorder(tmp_path / "scoring.jsonl") as recorder:
        result, _record = await effort_sweep.run_case(
            prepared,
            effort_sweep.UsageTap(),
            recorder,
            model="claude-sonnet-5",
        )

    assert result.axes == (5, 4, 5)
    assert result.expected_axes == (5, 4, 5)


def test_prompt_candidate_is_shared_and_cannot_resume_production_results() -> None:
    case = {
        "name": "shared candidate",
        "topic": "Topic",
        "question": "Question?",
        "answer": "Answer.",
    }
    production = effort_sweep.prepare_cases(
        [case],
        levels=["low"],
        model="claude-sonnet-5",
        prompt_variant=PRODUCTION,
    )[0]
    claude_candidate = effort_sweep.prepare_cases(
        [case],
        levels=["low"],
        model="claude-sonnet-5",
        prompt_variant=EXPLICIT_EVIDENCE_V6,
    )[0]
    openai_candidate = openai_bakeoff.prepare_cases(
        [case],
        kind="scoring",
        levels=["low"],
        model="gpt-5.6-terra",
        max_output_tokens=1024,
        prompt_variant=EXPLICIT_EVIDENCE_V6,
    )[0]
    v1_candidate = effort_sweep.prepare_cases(
        [case],
        levels=["low"],
        model="claude-sonnet-5",
        prompt_variant=EXPLICIT_EVIDENCE_V1,
    )[0]
    v2_candidate = effort_sweep.prepare_cases(
        [case],
        levels=["low"],
        model="claude-sonnet-5",
        prompt_variant=EXPLICIT_EVIDENCE_V2,
    )[0]
    v3_candidate = effort_sweep.prepare_cases(
        [case],
        levels=["low"],
        model="claude-sonnet-5",
        prompt_variant=EXPLICIT_EVIDENCE_V3,
    )[0]
    v4_candidate = effort_sweep.prepare_cases(
        [case],
        levels=["low"],
        model="claude-sonnet-5",
        prompt_variant=EXPLICIT_EVIDENCE_V4,
    )[0]
    v5_candidate = effort_sweep.prepare_cases(
        [case],
        levels=["low"],
        model="claude-sonnet-5",
        prompt_variant=EXPLICIT_EVIDENCE_V5,
    )[0]

    assert production.completion["rubric"] == effort_sweep.llm.SCORING_RUBRIC
    assert claude_candidate.completion["rubric"] == openai_candidate.completion["rubric"]
    assert claude_candidate.fingerprint != production.fingerprint
    assert claude_candidate.fingerprint != v1_candidate.fingerprint
    assert claude_candidate.fingerprint != v2_candidate.fingerprint
    assert claude_candidate.fingerprint != v3_candidate.fingerprint
    assert claude_candidate.fingerprint != v4_candidate.fingerprint
    assert claude_candidate.fingerprint != v5_candidate.fingerprint
    assert claude_candidate.scoring_prompt_variant == EXPLICIT_EVIDENCE_V6
    assert openai_candidate.scoring_prompt_variant == EXPLICIT_EVIDENCE_V6

    record = support.make_result_record(
        claude_candidate,
        model="claude-sonnet-5",
        result={"score": 3},
        usage=support.Usage(),
    )
    assert record["scoring_prompt_variant"] == EXPLICIT_EVIDENCE_V6


def test_explicit_evidence_candidate_maps_axes_and_forbids_context_credit() -> None:
    case = {
        "topic": "Topic",
        "question": "Question?",
        "answer": "Answer.",
    }

    rubric = effort_sweep.build_completion(
        case,
        model="claude-sonnet-5",
        effort="low",
        prompt_variant=EXPLICIT_EVIDENCE_V1,
    )["rubric"]

    assert "depth — trade-off awareness only" in rubric
    assert "boundaries — failure-mode awareness only" in rubric
    assert "The only learner evidence is text after `ANSWER:` labels" in rubric
    assert "not claims the learner made" in rubric
    assert "If feedback supplies a missing trade-off, depth must be 0-2" in rubric
    assert "If feedback supplies a missing failure" in rubric


def test_v2_uses_mechanical_secondary_axis_eligibility() -> None:
    case = {
        "topic": "Topic",
        "question": "Question?",
        "answer": "Only keep the few numbers that drive the design.",
    }

    rubric = effort_sweep.build_completion(
        case,
        model="claude-sonnet-5",
        effort="low",
        prompt_variant=EXPLICIT_EVIDENCE_V2,
    )["rubric"]

    assert "DEPTH — TRADE-OFF AWARENESS ONLY" in rubric
    assert "Depth is ineligible for 3-5 and" in rubric
    assert "BOUNDARIES — FAILURE-MODE AWARENESS ONLY" in rubric
    assert "Boundaries is ineligible for 3-5" in rubric
    assert "Never reverse a prescription into an unstated failure" in rubric
    assert "does not affect or drive the design is not a concrete" in rubric
    assert '"only keep the few numbers that drive the design"' in rubric
    assert "Apply the hard 0-2 ceilings even when" in rubric


def test_unknown_scoring_prompt_variant_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown scoring prompt variant"):
        apply_scoring_prompt_variant({"rubric": "base"}, "unknown")


def test_v3_uses_bidirectional_mandatory_axis_bands() -> None:
    case = {
        "topic": "Topic",
        "question": "Question?",
        "answer": "Skipping unrelated arithmetic saves interview time.",
    }

    rubric = effort_sweep.build_completion(
        case,
        model="claude-sonnet-5",
        effort="low",
        prompt_variant=EXPLICIT_EVIDENCE_V3,
    )["rubric"]

    assert "BIDIRECTIONAL EVIDENCE ELIGIBILITY V3" in rubric
    assert "No qualifying relationship: Depth MUST be 0-2" in rubric
    assert "One or more qualifying relationships: Depth MUST be 3-5" in rubric
    assert "No qualifying relationship: Boundaries MUST be 0-2" in rubric
    assert "One or more qualifying relationships: Boundaries MUST be 3-5" in rubric
    assert "MUST NOT lower it to 0-2" in rubric
    assert "does not erase an independently stated trade-off" in rubric
    assert "even without a numerical estimate" in rubric
    assert "feedback acknowledges or paraphrases" in rubric


def test_v4_freezes_accuracy_and_requires_two_learner_endpoints() -> None:
    case = {
        "topic": "Topic",
        "question": "Question?",
        "answer": "Ignore the body ID as identity evidence.",
    }

    rubric = effort_sweep.build_completion(
        case,
        model="claude-sonnet-5",
        effort="low",
        prompt_variant=EXPLICIT_EVIDENCE_V4,
    )["rubric"]

    assert "BIDIRECTIONAL EVIDENCE ELIGIBILITY V3" in rubric
    assert "AXIS INDEPENDENCE AND TWO-ENDPOINT EVIDENCE V4" in rubric
    assert "choose and freeze Accuracy" in rubric
    assert "MUST NOT lower frozen Accuracy" in rubric
    assert "using only\nlearner words" in rubric
    assert "Both endpoints and their connection" in rubric
    assert '"Ignore/reject/do not trust client-supplied identity"' in rubric
    assert "If it cannot, lower the axis to 0-2" in rubric


def test_v5_calibrates_complete_secondary_relationships_above_three() -> None:
    case = {
        "topic": "Topic",
        "question": "Question?",
        "answer": "Skipping unrelated arithmetic saves interview time.",
    }

    rubric = effort_sweep.build_completion(
        case,
        model="claude-sonnet-5",
        effort="low",
        prompt_variant=EXPLICIT_EVIDENCE_V5,
    )["rubric"]

    assert "AXIS INDEPENDENCE AND TWO-ENDPOINT EVIDENCE V4" in rubric
    assert "WITHIN-BAND SECONDARY CALIBRATION V5" in rubric
    assert "correct but materially vague or incomplete" in rubric
    assert "clear and complete, with a minor omission" in rubric
    assert "One complete relationship is enough" in rubric
    assert "this axis MUST be 4-5" in rubric
    assert "capacity limits is Depth 4-5" in rubric


def test_v6_unifies_the_contract_and_is_shorter_than_v5() -> None:
    case = {
        "topic": "Topic",
        "question": "Question?",
        "answer": "Ignore the body ID as identity evidence.",
    }

    rubric = effort_sweep.build_completion(
        case,
        model="claude-sonnet-5",
        effort="low",
        prompt_variant=EXPLICIT_EVIDENCE_V6,
    )["rubric"]

    assert "UNIFIED AXIS CONTRACT V6" in rubric
    assert "1. ACCURACY — SCORE AND FREEZE" in rubric
    assert "2. DEPTH — TRADE-OFF RELATIONSHIP" in rubric
    assert "3. BOUNDARIES — FAILURE RELATIONSHIP" in rubric
    assert "4. CALIBRATE AN ELIGIBLE SECONDARY AXIS" in rubric
    assert "5. FINAL CONSISTENCY CHECK" in rubric
    assert "Accuracy MUST be 4-5 even without secondary evidence" in rubric
    assert "Boundaries MUST be 0-2" in rubric
    assert "Depth MUST be 4-5 without numbers" in rubric
    assert "BIDIRECTIONAL EVIDENCE ELIGIBILITY V3" not in EXPLICIT_EVIDENCE_V6_RULES
    assert len(EXPLICIT_EVIDENCE_V6_RULES.encode()) < len(
        EXPLICIT_EVIDENCE_V5_RULES.encode()
    )


def test_v7_separates_option_selection_from_failure_evidence() -> None:
    case = {
        "topic": "Topic",
        "question": "Question?",
        "answer": "If one heap can hold the topics, keep it; otherwise shard.",
    }

    rubric = effort_sweep.build_completion(
        case,
        model="claude-sonnet-5",
        effort="low",
        prompt_variant=EXPLICIT_EVIDENCE_V7,
    )["rubric"]

    assert "UNIFIED AXIS CONTRACT V7" in rubric
    assert "Selection logic is not failure evidence" in rubric
    assert 'An option or capacity branch ("if A fits' in rubric
    assert "only selection logic or identifies no failure" in rubric
    assert '"If one heap fits, keep it; otherwise shard"' in rubric
    assert "links a mistake to incorrect" in rubric
    assert '"Save time but watch capacity"' in rubric
    assert "UNIFIED AXIS CONTRACT V6" not in EXPLICIT_EVIDENCE_V7_RULES
    assert len(EXPLICIT_EVIDENCE_V7_RULES.encode()) < (
        len(EXPLICIT_EVIDENCE_V6_RULES.encode()) + 1000
    )


def test_v7_prompt_is_shared_and_fingerprint_isolated() -> None:
    case = {
        "name": "shared V7 candidate",
        "topic": "Topic",
        "question": "Question?",
        "answer": "Answer.",
    }
    production = effort_sweep.prepare_cases(
        [case],
        levels=["low"],
        model="claude-sonnet-5",
        prompt_variant=PRODUCTION,
    )[0]
    v6 = effort_sweep.prepare_cases(
        [case],
        levels=["low"],
        model="claude-sonnet-5",
        prompt_variant=EXPLICIT_EVIDENCE_V6,
    )[0]
    claude_v7 = effort_sweep.prepare_cases(
        [case],
        levels=["low"],
        model="claude-sonnet-5",
        prompt_variant=EXPLICIT_EVIDENCE_V7,
    )[0]
    openai_v7 = openai_bakeoff.prepare_cases(
        [case],
        kind="scoring",
        levels=["low"],
        model="gpt-5.6-terra",
        max_output_tokens=1024,
        prompt_variant=EXPLICIT_EVIDENCE_V7,
    )[0]

    assert claude_v7.completion["rubric"] == openai_v7.completion["rubric"]
    assert claude_v7.fingerprint != production.fingerprint
    assert claude_v7.fingerprint != v6.fingerprint
    assert claude_v7.scoring_prompt_variant == EXPLICIT_EVIDENCE_V7
    assert openai_v7.scoring_prompt_variant == EXPLICIT_EVIDENCE_V7


def test_v8_treats_selection_as_mechanism_only() -> None:
    case = {
        "topic": "Topic",
        "question": "Question?",
        "answer": "If one heap can hold the topics, keep it; otherwise shard.",
    }

    rubric = effort_sweep.build_completion(
        case,
        model="claude-sonnet-5",
        effort="low",
        prompt_variant=EXPLICIT_EVIDENCE_V8,
    )["rubric"]

    assert "UNIFIED AXIS CONTRACT V8" in rubric
    assert "Selection logic is mechanism evidence only" in rubric
    assert "MUST by itself stay Depth 0-2 and Boundaries 0-2" in rubric
    assert "separate learner-stated choice/cost relationship" in rubric
    assert "separate learner-stated wrong-action/harm relationship" in rubric
    assert "supplies a missing cost or harm" in rubric
    assert '"If one heap fits, keep it; otherwise shard"' in rubric
    assert "Boundaries 3-5 but Depth 0-2" in rubric
    assert '"Save time but\n    watch capacity"' in rubric
    assert "UNIFIED AXIS CONTRACT V7" not in EXPLICIT_EVIDENCE_V8_RULES
    assert len(EXPLICIT_EVIDENCE_V8_RULES.encode()) < (
        len(EXPLICIT_EVIDENCE_V7_RULES.encode()) + 500
    )


def test_v8_prompt_is_shared_and_fingerprint_isolated() -> None:
    case = {
        "name": "shared V8 candidate",
        "topic": "Topic",
        "question": "Question?",
        "answer": "Answer.",
    }
    production = effort_sweep.prepare_cases(
        [case],
        levels=["low"],
        model="claude-sonnet-5",
        prompt_variant=PRODUCTION,
    )[0]
    v7 = effort_sweep.prepare_cases(
        [case],
        levels=["low"],
        model="claude-sonnet-5",
        prompt_variant=EXPLICIT_EVIDENCE_V7,
    )[0]
    claude_v8 = effort_sweep.prepare_cases(
        [case],
        levels=["low"],
        model="claude-sonnet-5",
        prompt_variant=EXPLICIT_EVIDENCE_V8,
    )[0]
    openai_v8 = openai_bakeoff.prepare_cases(
        [case],
        kind="scoring",
        levels=["low"],
        model="gpt-5.6-terra",
        max_output_tokens=1024,
        prompt_variant=EXPLICIT_EVIDENCE_V8,
    )[0]

    assert claude_v8.completion["rubric"] == openai_v8.completion["rubric"]
    assert claude_v8.fingerprint != production.fingerprint
    assert claude_v8.fingerprint != v7.fingerprint
    assert claude_v8.scoring_prompt_variant == EXPLICIT_EVIDENCE_V8
    assert openai_v8.scoring_prompt_variant == EXPLICIT_EVIDENCE_V8


def test_reviewed_gate_accepts_results_within_one_on_every_signal() -> None:
    result = effort_sweep.Result(
        index=0,
        case="passing",
        expected_score=4,
        score=3,
        expected_axes=(5, 5, 1),
        axes=(4, 4, 1),
        usage=support.Usage(),
    )

    assert effort_sweep.reviewed_gate_failures({"low": [result]}) == []


def test_reviewed_gate_reports_axis_composite_bucket_and_label_failures() -> None:
    false_failure = effort_sweep.Result(
        index=0,
        case="false failure",
        expected_score=4,
        score=2,
        expected_axes=(5, 5, 1),
        axes=(2, 2, 1),
        usage=support.Usage(),
    )
    unlabeled = effort_sweep.Result(
        index=1,
        case="unlabeled",
        expected_score=None,
        score=3,
        expected_axes=(None, None, None),
        axes=(3, 1, 1),
        usage=support.Usage(),
    )

    failures = effort_sweep.reviewed_gate_failures(
        {"low": [false_failure, unlabeled]}
    )

    assert any("composite 2 is more than one from 4" in failure for failure in failures)
    assert any("accuracy 2 is more than one from 5" in failure for failure in failures)
    assert any("depth 2 is more than one from 5" in failure for failure in failures)
    assert any("false Accuracy failure" in failure for failure in failures)
    assert any("missing reviewed label" in failure for failure in failures)


def test_secondary_bucket_gate_reports_both_crossing_directions() -> None:
    result = effort_sweep.Result(
        index=0,
        case="crossed buckets",
        expected_score=3,
        score=4,
        expected_axes=(5, 2, 3),
        axes=(5, 3, 2),
        usage=support.Usage(),
    )

    failures = effort_sweep.secondary_bucket_gate_failures({"medium": [result]})

    assert failures == [
        "medium/crossed buckets: false Depth pass",
        "medium/crossed buckets: false Boundaries failure",
    ]


def test_openai_sol_price_is_versioned_in_the_shared_guard() -> None:
    rate = support.rate_for_model("gpt-5.6-sol")

    assert rate.input_per_million == Decimal("5")
    assert rate.output_per_million == Decimal("30")
    assert rate.label == "published standard rate"


@pytest.mark.anyio
async def test_reattempt_sweep_reads_the_current_accuracy_contract(
    monkeypatch, tmp_path
) -> None:
    async def score_reattempt(**kwargs) -> ReattemptResult:
        assert kwargs["unaided_accuracy"] == 1
        return ReattemptResult(accuracy=5, mastery_summary="Reconstructed it.")

    monkeypatch.setattr(reattempt_effort_sweep.llm, "score_reattempt", score_reattempt)
    case = {
        "name": "current reattempt contract",
        "topic": "Topic",
        "question": "Question?",
        "feedback": "Feedback.",
        "reattempt_answer": "Answer.",
        "unaided_accuracy": 1,
        "expected_accuracy": 5,
    }
    prepared = reattempt_effort_sweep.prepare_cases(
        [case], levels=["low"], model="claude-sonnet-5"
    )[0]
    with reattempt_effort_sweep.JsonlRecorder(tmp_path / "reattempt.jsonl") as recorder:
        result, _record = await reattempt_effort_sweep.run_case(
            prepared,
            reattempt_effort_sweep.UsageTap(),
            recorder,
            model="claude-sonnet-5",
        )

    assert result.actual == 5
    assert result.expected == 5
