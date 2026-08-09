import argparse
import json
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.llm import ReattemptResult, ScoreResult, derive_composite
from scripts import effort_sweep, reattempt_effort_sweep
from scripts import effort_sweep_support as support
from scripts.effort_sweep_support import hydrate_grounding

API = Path(__file__).resolve().parent.parent
SCORING_CASES = API / "scripts" / "grounded_effort_cases_week1.json"
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
        recorder.append(record)
        assert path.read_text().endswith("\n")

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
    monkeypatch.setattr(effort_sweep.llm, "score_answer", forbidden_score)
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
    monkeypatch.setattr(effort_sweep.llm, "score_answer", forbidden_score)
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
    monkeypatch.setattr(effort_sweep.llm, "score_answer", forbidden)
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
    async def score_answer(**_kwargs) -> ScoreResult:
        return ScoreResult(
            status="complete", score=5, accuracy=5, depth=4, boundaries=5
        )

    monkeypatch.setattr(effort_sweep.llm, "score_answer", score_answer)
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
        [case], levels=["low"], model="claude-sonnet-5"
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
