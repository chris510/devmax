import argparse
import json
from collections import Counter
from pathlib import Path

import pytest

from app.services.llm import ReattemptResult, ScoreResult, derive_composite
from scripts import effort_sweep, reattempt_effort_sweep
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


@pytest.mark.anyio
async def test_scoring_sweep_reads_the_current_axis_contract(monkeypatch) -> None:
    async def score_answer(**_kwargs) -> ScoreResult:
        return ScoreResult(
            status="complete", score=5, accuracy=5, depth=4, boundaries=5
        )

    monkeypatch.setattr(effort_sweep.llm, "score_answer", score_answer)
    result = await effort_sweep.run_case(
        0,
        {
            "name": "current scoring contract",
            "topic": "Topic",
            "question": "Question?",
            "answer": "Answer.",
            "expected_score": 5,
            "expected_accuracy": 5,
            "expected_depth": 4,
            "expected_boundaries": 5,
        },
        "low",
        effort_sweep.UsageTap(),
    )

    assert result.axes == (5, 4, 5)
    assert result.expected_axes == (5, 4, 5)


@pytest.mark.anyio
async def test_reattempt_sweep_reads_the_current_accuracy_contract(monkeypatch) -> None:
    async def score_reattempt(**kwargs) -> ReattemptResult:
        assert kwargs["unaided_accuracy"] == 1
        return ReattemptResult(accuracy=5, mastery_summary="Reconstructed it.")

    monkeypatch.setattr(reattempt_effort_sweep.llm, "score_reattempt", score_reattempt)
    result = await reattempt_effort_sweep.run_case(
        0,
        {
            "name": "current reattempt contract",
            "topic": "Topic",
            "question": "Question?",
            "feedback": "Feedback.",
            "reattempt_answer": "Answer.",
            "unaided_accuracy": 1,
            "expected_accuracy": 5,
        },
        "low",
        reattempt_effort_sweep.UsageTap(),
    )

    assert result.actual == 5
    assert result.expected == 5
