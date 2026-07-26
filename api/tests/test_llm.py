"""Tests for the scoring gate in services/llm.py.

`score_answer`'s follow-up decision is the structural half of "maximum one
follow-up per session" — the model always writes a probe and returns a provisional
score, and this code decides whether the probe is used. Every other test in the
suite monkeypatches `score_answer` wholesale, so without this file the invariant
has no coverage at all.
"""

from typing import Any

import pytest

from app.services import llm

SCORE_ARGS = dict(
    topic="Consistent hashing",
    mastery_summary="",
    question_asked="What problem does consistent hashing solve?",
    answer_text="It keeps most keys in place when a node joins.",
    follow_up_question=None,
    follow_up_answer="",
)


def stub_completion(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> list[dict]:
    """Replace the Anthropic round trip; record the kwargs it was called with."""
    calls: list[dict] = []

    async def _fake(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return payload

    monkeypatch.setattr(llm, "_complete", _fake)
    return calls


# The two thresholds are independent (score 2 fails SM-2, score 3 passes; both
# probe), so the gate is checked across the whole 0-5 range rather than at a
# single boundary.
@pytest.mark.parametrize(
    ("score", "expected"),
    [(0, "complete"), (1, "complete"), (2, "follow_up"), (3, "follow_up"), (4, "complete"),
     (5, "complete")],
)
async def test_only_shaky_scores_probe(
    monkeypatch: pytest.MonkeyPatch, score: int, expected: str
) -> None:
    stub_completion(
        monkeypatch,
        {"score": score, "feedback": "f", "mastery_summary": "m", "follow_up_question": "probe?"},
    )

    result = await llm.score_answer(**SCORE_ARGS, follow_up_used=False)

    assert result.status == expected


@pytest.mark.parametrize("score", [0, 1, 2, 3, 4, 5])
async def test_a_used_follow_up_always_completes(
    monkeypatch: pytest.MonkeyPatch, score: int
) -> None:
    """The second answer of a session can never produce a third turn.

    This is enforced here in code rather than by asking the model nicely — the
    model still writes a probe, and it is discarded.
    """
    stub_completion(
        monkeypatch,
        {"score": score, "feedback": "f", "mastery_summary": "m", "follow_up_question": "probe?"},
    )

    result = await llm.score_answer(**SCORE_ARGS, follow_up_used=True)

    assert result.status == "complete"
    assert result.score == score


@pytest.mark.parametrize("probe", ["", "   "])
async def test_a_shaky_score_without_a_probe_completes(
    monkeypatch: pytest.MonkeyPatch, probe: str
) -> None:
    stub_completion(
        monkeypatch,
        {"score": 2, "feedback": "f", "mastery_summary": "m", "follow_up_question": probe},
    )

    result = await llm.score_answer(**SCORE_ARGS, follow_up_used=False)

    assert result.status == "complete"
    assert result.score == 2


async def test_follow_up_result_carries_the_probe_and_no_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_completion(
        monkeypatch,
        {"score": 2, "feedback": "f", "mastery_summary": "m", "follow_up_question": " One more — "},
    )

    result = await llm.score_answer(**SCORE_ARGS, follow_up_used=False)

    assert result.status == "follow_up"
    assert result.follow_up_question == "One more —"
    # A provisional score must not leak out as a real one: the card is not
    # rescheduled until the session completes.
    assert result.score is None


async def test_the_prior_follow_up_turns_are_sent_for_the_second_scoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = stub_completion(
        monkeypatch, {"score": 4, "feedback": "f", "mastery_summary": "m"}
    )

    await llm.score_answer(
        **{
            **SCORE_ARGS,
            "follow_up_question": "What are virtual nodes for?",
            "follow_up_answer": "They spread each node over many ring positions.",
        },
        follow_up_used=True,
    )

    sent = calls[0]["user_content"]
    assert "What are virtual nodes for?" in sent
    assert "They spread each node over many ring positions." in sent


@pytest.mark.parametrize("payload", [{}, {"score": None}, {"score": "not a number"}])
async def test_an_unusable_score_raises_llm_error(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    """503, not 500.

    The JSON schema makes `score` required so this should be unreachable, but the
    client only knows how to retry a 503 — and on a 500 the user's spoken answer is
    gone.
    """
    stub_completion(monkeypatch, payload)

    with pytest.raises(llm.LLMError):
        await llm.score_answer(**SCORE_ARGS, follow_up_used=False)
