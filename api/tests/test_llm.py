"""Tests for the scoring gate in services/llm.py.

`score_answer`'s follow-up decision is the structural half of "maximum one
follow-up per session" — the model always writes a probe and returns a provisional
score, and this code decides whether the probe is used. Every other test in the
suite monkeypatches `score_answer` wholesale, so without this file the invariant
has no coverage at all.
"""

import json
from types import SimpleNamespace
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


# One axis triple per composite band. The model no longer returns a composite, so
# a test that wants "a session that scores 4" has to say it in axis terms.
AXES_FOR_COMPOSITE = {
    0: (0, 0, 0),
    1: (1, 0, 0),
    2: (2, 0, 0),
    3: (3, 0, 0),  # mechanism only
    4: (4, 3, 0),  # + trade-offs, failure modes still thin
    5: (5, 3, 3),  # all three
}


def scored(score: Any, probe: str | None = "probe?") -> dict[str, Any]:
    """A well-formed scoring response deriving to `score`.

    `probe=None` omits the follow-up entirely.
    """
    mechanism, trade_offs, failure_modes = AXES_FOR_COMPOSITE[score]
    payload: dict[str, Any] = {
        "mechanism_accuracy": mechanism,
        "trade_off_awareness": trade_offs,
        "failure_mode_awareness": failure_modes,
        "feedback": "f",
        "mastery_summary": "m",
    }
    if probe is not None:
        payload["follow_up_question"] = probe
    return payload


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
# single boundary. 0 is the other edge that matters: it is the one failing score
# that does not probe — see docs/DEVIATIONS.md §15.
@pytest.mark.parametrize(
    ("score", "expected"),
    [(0, "complete"), (1, "follow_up"), (2, "follow_up"), (3, "follow_up"), (4, "complete"),
     (5, "complete")],
)
async def test_only_shaky_scores_probe(
    monkeypatch: pytest.MonkeyPatch, score: int, expected: str
) -> None:
    stub_completion(monkeypatch, scored(score))

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
    stub_completion(monkeypatch, scored(score))

    result = await llm.score_answer(**SCORE_ARGS, follow_up_used=True)

    assert result.status == "complete"
    assert result.score == score


@pytest.mark.parametrize("probe", ["", "   "])
async def test_a_shaky_score_without_a_probe_completes(
    monkeypatch: pytest.MonkeyPatch, probe: str
) -> None:
    stub_completion(monkeypatch, scored(2, probe))

    result = await llm.score_answer(**SCORE_ARGS, follow_up_used=False)

    assert result.status == "complete"
    assert result.score == 2


async def test_follow_up_result_carries_the_probe_and_no_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_completion(monkeypatch, scored(2, " One more — "))

    result = await llm.score_answer(**SCORE_ARGS, follow_up_used=False)

    assert result.status == "follow_up"
    assert result.follow_up_question == "One more —"
    # A provisional score must not leak out as a real one: the card is not
    # rescheduled until the session completes.
    assert result.score is None


async def test_the_prior_follow_up_turns_are_sent_for_the_second_scoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = stub_completion(monkeypatch, scored(4, probe=None))

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


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**scored(4), "mechanism_accuracy": None},
        {**scored(4), "trade_off_awareness": "not a number"},
        # The old blended shape: a model or config that ignored the new schema.
        {"score": 4, "feedback": "f", "mastery_summary": "m"},
    ],
)
async def test_an_unusable_score_raises_llm_error(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    """503, not 500.

    The JSON schema makes all three axes required so this should be unreachable,
    but the client only knows how to retry a 503 — and on a 500 the user's spoken
    answer is gone.
    """
    stub_completion(monkeypatch, payload)

    with pytest.raises(llm.LLMError):
        await llm.score_answer(**SCORE_ARGS, follow_up_used=False)


# --- the composite ----------------------------------------------------------
# `derive_composite` replaces a number the model used to guess. These cases pin
# the bands to the ones the old blended rubric described, so Card History and
# `last_score` keep meaning what they meant.


@pytest.mark.parametrize(
    ("axes", "expected"),
    [
        ((0, 5, 5), 0),  # no recall — depth elsewhere cannot rescue it
        ((1, 5, 5), 1),  # wrong mechanism, likewise
        ((2, 5, 5), 2),  # partial mechanism, likewise
        ((3, 0, 0), 3),  # mechanism only
        ((5, 2, 2), 3),  # "3-5 correct" still lands at 3 without the other axes
        ((3, 3, 0), 4),  # + trade-offs
        ((3, 5, 2), 4),  # failure modes still thin
        ((3, 0, 3), 5),  # failure modes present without trade-offs
        ((5, 5, 5), 5),  # complete
    ],
)
def test_derive_composite_reproduces_the_old_bands(axes: tuple[int, int, int], expected: int):
    assert llm.derive_composite(*axes) == expected


def test_a_low_mechanism_caps_the_composite_at_the_mechanism_score():
    """The single load-bearing property: depth never masks a broken mechanism."""
    for mechanism in (0, 1, 2):
        composites = {
            llm.derive_composite(mechanism, trade_offs, failure_modes)
            for trade_offs in range(6)
            for failure_modes in range(6)
        }
        assert composites == {mechanism}


async def test_the_axes_are_carried_onto_the_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """They are persisted per session and rolled up on Coverage."""
    stub_completion(monkeypatch, scored(4))

    result = await llm.score_answer(**SCORE_ARGS, follow_up_used=True)

    assert (result.mechanism_accuracy, result.trade_off_awareness) == (4, 3)
    assert result.failure_mode_awareness == 0
    assert result.score == 4


# --- request construction ---------------------------------------------------
# The tests above stub `_complete`, so nothing there covers what `_complete`
# actually sends or how it retries. These stub one layer deeper, at `_client`.


def make_response(payload=None, *, text: str | None = None):
    """A stand-in for anthropic's Message, with just the fields _complete reads."""
    body = text if text is not None else json.dumps(payload)
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=body)],
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=50,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


class FakeClient:
    """Records every request and replays a scripted list of outcomes."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def fake_client(monkeypatch):
    def install(*outcomes) -> FakeClient:
        client = FakeClient(outcomes)
        monkeypatch.setattr(llm, "_client", lambda: client)
        return client

    return install


async def test_no_cache_control_is_sent(fake_client):
    """Regression guard: the rubrics are below every model's cacheable minimum.

    A `cache_control` marker here fails silently — no error, just a cache that
    never fills. Keeping it out is deliberate; see the comment on SCORING_RUBRIC.
    """
    client = fake_client(make_response(scored(4)))
    await llm.score_answer(**SCORE_ARGS, follow_up_used=True)

    assert client.calls[0]["system"] == [{"type": "text", "text": llm.SCORING_RUBRIC}]
    assert "cache_control" not in json.dumps(client.calls[0])


async def test_effort_is_omitted_when_unset(fake_client, monkeypatch):
    """Haiku 4.5 rejects `effort` outright, so it must be absent, not null."""
    client = fake_client(make_response({"question": "What moves?"}))
    monkeypatch.setattr(llm.get_settings(), "question_effort", None)

    await llm.generate_question(
        topic="Consistent hashing",
        category="Systems",
        pattern=None,
        source_company=None,
        mastery_summary="",
        last_score=None,
        recent_questions=[],
    )

    assert "effort" not in client.calls[0]["output_config"]


async def test_effort_is_passed_through_when_set(fake_client, monkeypatch):
    client = fake_client(make_response(scored(4)))
    monkeypatch.setattr(llm.get_settings(), "scoring_effort", "low")

    await llm.score_answer(**SCORE_ARGS, follow_up_used=True)

    assert client.calls[0]["output_config"]["effort"] == "low"


def test_client_is_cached_with_explicit_limits():
    """A per-call client leaked an httpx connection pool per request."""
    llm._client.cache_clear()
    try:
        first = llm._client()
        assert llm._client() is first
        assert first.max_retries == llm.SDK_MAX_RETRIES
        assert first.timeout == llm.SDK_TIMEOUT_SECONDS
    finally:
        llm._client.cache_clear()


async def test_unparseable_output_is_retried_once(fake_client):
    client = fake_client(make_response(text="Here you go: {oops"), make_response(scored(4)))

    result = await llm.score_answer(**SCORE_ARGS, follow_up_used=True)

    assert len(client.calls) == 2
    assert result.score == 4


async def test_unparseable_twice_raises(fake_client):
    client = fake_client(make_response(text="nope"), make_response(text="still nope"))

    with pytest.raises(llm.LLMError):
        await llm.score_answer(**SCORE_ARGS, follow_up_used=True)

    assert len(client.calls) == 2


async def test_transport_failure_is_not_retried_here(fake_client):
    """The SDK already retried (SDK_MAX_RETRIES); a second layer would stack."""
    client = fake_client(RuntimeError("overloaded"))

    with pytest.raises(llm.LLMError):
        await llm.score_answer(**SCORE_ARGS, follow_up_used=True)

    assert len(client.calls) == 1


async def test_empty_question_is_rejected(fake_client):
    fake_client(make_response({"question": "   "}))

    with pytest.raises(llm.LLMError):
        await llm.generate_question(
            topic="Consistent hashing",
            category="Systems",
            pattern=None,
            source_company=None,
            mastery_summary="",
            last_score=None,
            recent_questions=[],
        )


# --- mastery summary hygiene -------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("shaky on virtual nodes", "shaky on virtual nodes"),
        ("  padded  ", "padded"),
        # Straight and curly quotes, and the CJK bracket seen in live output.
        ("'shaky.'", "shaky."),
        ('"solid on rings"', "solid on rings"),
        ("shaky.」", "shaky."),
        ("“solid, shaky on vnodes”", "solid, shaky on vnodes"),
        # A sentence-final stop is the summary's own text, not packaging.
        ("solid on ring mechanics.", "solid on ring mechanics."),
        ("", ""),
    ],
)
def test_clean_summary_strips_wrappers_but_keeps_the_sentence(raw, expected):
    """The summary renders verbatim in the UI and is fed to the next scoring call."""
    assert llm.clean_summary(raw) == expected
