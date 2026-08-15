"""Tests for the scoring gate in services/llm.py.

`score_answer`'s follow-up decision is the structural half of "at most
`MAX_SCORED_FOLLOW_UPS` scored follow-ups per session" — the model always writes a
probe and returns a provisional score, and this code decides whether the probe is
used. Every other test in the suite monkeypatches `score_answer` wholesale, so
without this file the invariant has no coverage at all.
"""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.schemas import AnswerRubric
from app.services import llm
from app.services.card_lifecycle import RUBRIC_FIELDS

SCORE_ARGS = dict(
    topic="Consistent hashing",
    mastery_summary="",
    question_asked="What problem does consistent hashing solve?",
    answer_text="It keeps most keys in place when a node joins.",
)

# The parsers gate on how many scored follow-ups a session has already taken, so
# these name the turn rather than the content. `AT_CAP` is what a test uses when it
# wants a completed result regardless of score.
NO_PROBES: list[tuple[str, str]] = []
ONE_PROBE = [("One more — what moves?", "Only the keys on one arc.")]
AT_CAP = ONE_PROBE + [
    ("Last one — what are virtual nodes for?", "They spread each node over the ring.")
]


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


def test_proposal_schema_and_activation_share_the_rubric_fields():
    assert tuple(AnswerRubric.model_fields) == RUBRIC_FIELDS
    schema = llm.CARD_PROPOSAL_SCHEMA["properties"]["candidates"]["items"]
    rubric = schema["properties"]["answer_rubric"]
    assert tuple(rubric["properties"]) == RUBRIC_FIELDS
    assert tuple(rubric["required"]) == RUBRIC_FIELDS


def test_lesson_schema_has_one_open_prompt_slot_for_each_depth():
    concepts = llm.LESSON_EXTRACTION_SCHEMA["properties"]["concepts"]
    assert concepts["minItems"] == 1
    assert concepts["maxItems"] == 7
    prompt_list = concepts["items"]["properties"]["recall_questions"]
    assert prompt_list["minItems"] == prompt_list["maxItems"] == 5
    assert tuple(prompt_list["items"]["properties"]["level"]["enum"]) == (
        llm.LESSON_RECALL_LEVELS
    )
    rubric = concepts["items"]["properties"]["answer_rubric"]
    assert tuple(rubric["properties"]) == RUBRIC_FIELDS
    assert tuple(rubric["required"]) == RUBRIC_FIELDS


async def test_lesson_extraction_uses_the_bounded_card_proposal_route(monkeypatch):
    payload = {"concepts": [{"topic": "Request routing"}]}
    calls = stub_completion(monkeypatch, payload)
    authorizations: list[int] = []

    async def authorize(attempt: int) -> None:
        authorizations.append(attempt)

    result = await llm.extract_lesson(
        title="Request path",
        source_text="DNS to load balancer to application to storage.",
        source_url="https://example.com/request-path",
        source_type="article",
        before_provider_call=authorize,
    )

    assert result == payload["concepts"]
    assert calls[0]["model"] == llm.get_settings().card_proposal_model
    assert calls[0]["effort"] == llm.get_settings().card_proposal_effort
    assert calls[0]["max_tokens"] == 8000
    assert calls[0]["purpose"] == "lesson_extract"
    assert calls[0]["before_provider_call"] is authorize
    assert "Source URL (provenance only)" in calls[0]["user_content"]
    # `_complete` owns invocation at the physical transmission boundary; this
    # stub only verifies the exact callback is forwarded, so it is not called here.
    assert authorizations == []


def scored(
    score: Any, probe: str | None = "probe?", *, needs: bool = False
) -> dict[str, Any]:
    """A well-formed scoring response deriving to `score`.

    `probe=None` omits the follow-up entirely.
    """
    mechanism, trade_offs, failure_modes = AXES_FOR_COMPOSITE[score]
    payload: dict[str, Any] = {
        "accuracy": mechanism,
        "depth": trade_offs,
        "boundaries": failure_modes,
        "feedback": "f",
        "needs_more_evidence": needs,
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
    [
        (0, "complete"),
        (1, "follow_up"),
        (2, "follow_up"),
        (3, "follow_up"),
        (4, "complete"),
        (5, "complete"),
    ],
)
async def test_only_shaky_scores_probe(
    monkeypatch: pytest.MonkeyPatch, score: int, expected: str
) -> None:
    stub_completion(monkeypatch, scored(score))

    result = await llm.score_answer(**SCORE_ARGS, probes=NO_PROBES)

    assert result.status == expected


@pytest.mark.parametrize("needs", [True, False])
async def test_the_band_alone_decides_the_first_probe(
    monkeypatch: pytest.MonkeyPatch, needs: bool
) -> None:
    """`needs_more_evidence` is ignored on turn 1, in both directions.

    A stray `true` cannot widen the band, and a `false` cannot narrow it — the
    first probe is exactly the rule the app already ships.
    """
    stub_completion(monkeypatch, scored(2, needs=needs))
    assert (await llm.score_answer(**SCORE_ARGS, probes=NO_PROBES)).status == "follow_up"

    stub_completion(monkeypatch, scored(5, needs=needs))
    assert (await llm.score_answer(**SCORE_ARGS, probes=NO_PROBES)).status == "complete"


@pytest.mark.parametrize("score", [0, 1, 2, 3, 4, 5])
async def test_at_the_cap_every_score_completes(
    monkeypatch: pytest.MonkeyPatch, score: int
) -> None:
    """The answer to the last permitted probe can never produce another turn.

    This is enforced here in code rather than by asking the model nicely — the
    model still writes a probe and may still claim it needs more evidence, and
    both are discarded.
    """
    stub_completion(monkeypatch, scored(score, needs=True))

    result = await llm.score_answer(**SCORE_ARGS, probes=AT_CAP)

    assert result.status == "complete"
    assert result.score == score


# Mid-session — one probe taken, one still available — the band no longer decides;
# only the model saying it still cannot tell adjacent scores apart earns a turn.
@pytest.mark.parametrize(
    ("score", "needs", "probe", "expected"),
    [
        (2, True, "Last one — why?", "follow_up"),
        (3, True, "Last one — why?", "follow_up"),
        # A score inside the band is not enough on its own past the first probe.
        (2, False, "Last one — why?", "complete"),
        (0, True, "Last one — why?", "follow_up"),
        (5, True, "Last one — why?", "follow_up"),
        # Tolerant, as on turn 1: a claim with no probe to act on completes.
        (2, True, "", "complete"),
    ],
)
async def test_after_the_first_probe_only_insufficiency_probes_again(
    monkeypatch: pytest.MonkeyPatch, score: int, needs: bool, probe: str, expected: str
) -> None:
    stub_completion(monkeypatch, scored(score, probe, needs=needs))

    result = await llm.score_answer(**SCORE_ARGS, probes=ONE_PROBE)

    assert result.status == expected


@pytest.mark.parametrize("probe", ["", "   "])
async def test_a_shaky_score_without_a_probe_completes(
    monkeypatch: pytest.MonkeyPatch, probe: str
) -> None:
    stub_completion(monkeypatch, scored(2, probe))

    result = await llm.score_answer(**SCORE_ARGS, probes=NO_PROBES)

    assert result.status == "complete"
    assert result.score == 2


async def test_follow_up_result_carries_the_probe_and_no_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_completion(monkeypatch, scored(2, " One more — "))

    result = await llm.score_answer(**SCORE_ARGS, probes=NO_PROBES)

    assert result.status == "follow_up"
    assert result.follow_up_question == "One more —"
    # A provisional score must not leak out as a real one: the card is not
    # rescheduled until the session completes.
    assert result.score is None


async def test_a_second_probe_result_also_carries_no_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_completion(monkeypatch, scored(2, " Last one — ", needs=True))

    result = await llm.score_answer(**SCORE_ARGS, probes=ONE_PROBE)

    assert result.status == "follow_up"
    assert result.follow_up_question == "Last one —"
    assert result.score is None


async def test_the_prior_follow_up_turns_are_sent_for_the_second_scoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every probe pair reaches the transcript, in order."""
    calls = stub_completion(monkeypatch, scored(4, probe=None))

    await llm.score_answer(**SCORE_ARGS, probes=AT_CAP)

    sent = calls[0]["user_content"]
    for question, answer in AT_CAP:
        assert question in sent
        assert answer in sent
    assert sent.index(AT_CAP[0][0]) < sent.index(AT_CAP[1][0])
    assert sent.index(SCORE_ARGS["answer_text"]) < sent.index(AT_CAP[0][0])


@pytest.mark.parametrize(
    ("probes", "used", "final"),
    [(NO_PROBES, 0, "no"), (ONE_PROBE, 1, "no"), (AT_CAP, 2, "yes")],
)
@pytest.mark.parametrize(
    "builder", [llm.build_score_answer_completion, llm.build_score_v2_completion]
)
def test_both_builders_state_the_turn_and_the_cap(builder, probes, used, final) -> None:
    """The model is told where it is and that the turn may be its last.

    Both contracts, one wording — the preface rule keys off the count, so a V1/V2
    divergence here would show up as the wrong copy on the second probe.
    """
    completion = builder(
        model="test", effort=None, **SCORE_ARGS, probes=probes
    )

    sent = completion["user_content"]
    assert f"SCORED FOLLOW-UPS USED: {used} of {llm.MAX_SCORED_FOLLOW_UPS}" in sent
    assert f"FINAL SCORED TURN: {final}" in sent


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {**scored(4), "accuracy": None},
        {**scored(4), "depth": "not a number"},
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
        await llm.score_answer(**SCORE_ARGS, probes=NO_PROBES)


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

    result = await llm.score_answer(**SCORE_ARGS, probes=AT_CAP)

    assert (result.accuracy, result.depth) == (4, 3)
    assert result.boundaries == 0
    assert result.score == 4


# --- V2 Recall-only contract -------------------------------------------------


def recalled(score: int, probe: str = "", *, needs: bool = False) -> dict[str, Any]:
    return {
        "recall_score": score,
        "feedback": "recall feedback",
        "follow_up_question": probe,
        "needs_more_evidence": needs,
        "mastery_summary": "recalled the essential account",
    }


PROBE = "One more — missing link?"


# The V2 contract in full. Turn 1 is decided by the band and `needs_more_evidence`
# may say either thing; past it the flag decides alone; at the cap nothing probes.
# Surplus candidate text is ignored wherever the server decides to complete.
@pytest.mark.parametrize(
    ("probes", "score", "needs", "probe", "expected"),
    [
        (NO_PROBES, 0, False, "", "complete"),
        (NO_PROBES, 1, False, PROBE, "follow_up"),
        (NO_PROBES, 2, False, PROBE, "follow_up"),
        (NO_PROBES, 3, False, PROBE, "follow_up"),
        # In band, the model additionally says it lacks signal: still one probe.
        (NO_PROBES, 2, True, PROBE, "follow_up"),
        (NO_PROBES, 4, False, "", "complete"),
        (NO_PROBES, 5, False, "", "complete"),
        (NO_PROBES, 0, False, PROBE, "complete"),
        (NO_PROBES, 0, True, "", "complete"),
        (NO_PROBES, 4, True, PROBE, "complete"),
        (ONE_PROBE, 2, True, PROBE, "follow_up"),
        (ONE_PROBE, 5, True, PROBE, "follow_up"),
        (ONE_PROBE, 2, False, "", "complete"),
        (ONE_PROBE, 2, False, PROBE, "complete"),
        (AT_CAP, 2, False, "", "complete"),
        (AT_CAP, 2, True, "", "complete"),
        (AT_CAP, 2, True, PROBE, "complete"),
    ],
)
async def test_v2_follow_up_truth_table(
    monkeypatch: pytest.MonkeyPatch,
    probes: list[tuple[str, str]],
    score: int,
    needs: bool,
    probe: str,
    expected: str,
) -> None:
    calls = stub_completion(monkeypatch, recalled(score, probe, needs=needs))

    result = await llm.score_answer(
        **SCORE_ARGS, probes=probes, scoring_contract_version=2
    )

    assert result.status == expected
    assert result.scoring_contract_version == 2
    assert len(calls) == 1
    assert calls[0]["schema"] == llm.SCORE_V2_SCHEMA
    assert calls[0]["retry"] is False


async def test_v2_complete_result_is_recall_and_has_no_secondary_axes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_completion(monkeypatch, recalled(4))

    result = await llm.score_answer(
        **SCORE_ARGS, probes=AT_CAP, scoring_contract_version=2
    )

    assert result.score == result.accuracy == 4
    assert result.depth is None
    assert result.boundaries is None
    assert set(llm.SCORE_V2_SCHEMA["properties"]) == {
        "recall_score",
        "feedback",
        "follow_up_question",
        "needs_more_evidence",
        "mastery_summary",
    }
    assert llm.SCORE_V2_SCHEMA["additionalProperties"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**recalled(3), "recall_score": None},
        {**recalled(3), "recall_score": True},
        {**recalled(3), "recall_score": 3.0},
        {**recalled(3), "recall_score": "3"},
        {**recalled(3), "recall_score": "not a number"},
        {**recalled(3), "recall_score": 6},
    ],
)
async def test_v2_unusable_recall_raises_llm_error(
    monkeypatch: pytest.MonkeyPatch, payload: Any
) -> None:
    stub_completion(monkeypatch, payload)
    with pytest.raises(llm.LLMError):
        await llm.score_answer(
            **SCORE_ARGS, probes=NO_PROBES, scoring_contract_version=2
        )


@pytest.mark.parametrize(
    "payload",
    [
        {key: value for key, value in recalled(4).items() if key != "needs_more_evidence"},
        {**recalled(4), "needs_more_evidence": None},
        {**recalled(4), "needs_more_evidence": "yes"},
        {**recalled(4), "needs_more_evidence": 1},
    ],
)
async def test_v2_missing_or_non_boolean_evidence_flag_raises(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    """The flag is a required schema field; V2 never guesses what was meant."""
    stub_completion(monkeypatch, payload)
    with pytest.raises(llm.LLMError):
        await llm.score_answer(
            **SCORE_ARGS, probes=AT_CAP, scoring_contract_version=2
        )


async def test_v2_at_the_cap_cannot_create_another_scored_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_completion(monkeypatch, recalled(2))

    result = await llm.score_answer(
        **SCORE_ARGS, probes=AT_CAP, scoring_contract_version=2
    )

    assert result.status == "complete"
    assert result.score == 2


# Missing evidence for a server-required turn is still a hard contract failure.
# Surplus candidates are covered by the truth table above and cannot create a turn.
@pytest.mark.parametrize(
    ("probes", "score", "needs", "probe"),
    [
        # Turn 1: the band demanded a probe and none came.
        (NO_PROBES, 2, False, ""),
        (NO_PROBES, 2, True, ""),
        # Mid-session: an insufficiency claim must carry the candidate that
        # would settle it.
        (ONE_PROBE, 2, True, ""),
    ],
)
async def test_v2_invalid_follow_up_policy_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    probes: list[tuple[str, str]],
    score: int,
    needs: bool,
    probe: str,
) -> None:
    stub_completion(monkeypatch, recalled(score, probe, needs=needs))
    with pytest.raises(llm.LLMError):
        await llm.score_answer(
            **SCORE_ARGS, probes=probes, scoring_contract_version=2
        )


async def test_v2_surplus_candidate_is_ignored_with_content_free_telemetry(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    candidate = "One more — learner-specific text must not enter logs"
    stub_completion(monkeypatch, recalled(4, candidate, needs=True))

    with caplog.at_level("WARNING", logger="app.services.llm"):
        result = await llm.score_answer(
            **SCORE_ARGS, probes=NO_PROBES, scoring_contract_version=2
        )

    assert result.status == "complete"
    message = next(
        record.getMessage()
        for record in caplog.records
        if "event=surplus_probe_candidate_ignored" in record.getMessage()
    )
    assert "reason=outside_initial_band" in message
    assert "recall=4" in message
    assert "probes_used=0" in message
    assert "candidate_present=True" in message
    assert candidate not in message


async def test_qualitative_coaching_is_unscored_and_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = stub_completion(monkeypatch, {"coaching_feedback": "One grounded note."})

    result = await llm.coach_answer(
        topic="Consistent hashing",
        focus="depth",
        question="Why does it matter?",
        answer="It limits movement.",
        answer_basis="Only one arc moves.",
        answer_rubric={"mechanism": "Only one arc moves."},
    )

    assert result.feedback == "One grounded note."
    assert set(calls[0]["schema"]["properties"]) == {"coaching_feedback"}
    assert calls[0]["retry"] is False
    assert "score" not in json.dumps(calls[0]["schema"])


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
        stop_reason="end_turn",
        _request_id="msg_test",
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
    await llm.score_answer(**SCORE_ARGS, probes=AT_CAP)

    assert client.calls[0]["system"] == [{"type": "text", "text": llm.SCORING_RUBRIC}]
    assert "cache_control" not in json.dumps(client.calls[0])


async def test_v2_parse_failure_makes_exactly_one_transmission(monkeypatch):
    client = FakeClient(
        [
            make_response(text="not json"),
            make_response(recalled(4)),
        ]
    )
    monkeypatch.setattr(llm, "_no_retry_client", lambda: client)

    with pytest.raises(llm.LLMError):
        await llm._complete(
            model="test",
            effort=None,
            rubric=llm.SCORING_V2_RUBRIC,
            user_content="test",
            schema=llm.SCORE_V2_SCHEMA,
            max_tokens=256,
            retry=False,
        )

    assert len(client.calls) == 1


async def test_guarded_long_call_reauthorizes_each_explicit_parse_retry(monkeypatch):
    client = FakeClient(
        [
            make_response(text="not json"),
            make_response({"result": "usable"}),
        ]
    )
    monkeypatch.setattr(llm, "_no_retry_client", lambda: client)
    monkeypatch.setattr(
        llm,
        "_client",
        lambda: pytest.fail("a guarded call must disable hidden SDK retries"),
    )
    authorized: list[int] = []

    async def authorize(attempt: int) -> None:
        authorized.append(attempt)

    result = await llm._complete(
        model="test",
        effort=None,
        rubric="test",
        user_content="test",
        schema={"type": "object"},
        max_tokens=256,
        before_provider_call=authorize,
    )

    assert result == {"result": "usable"}
    assert authorized == [1, 2]
    assert len(client.calls) == 2


async def test_guard_failure_before_parse_retry_prevents_a_second_transmission(
    monkeypatch,
):
    client = FakeClient(
        [
            make_response(text="not json"),
            make_response({"result": "must not be sent"}),
        ]
    )
    monkeypatch.setattr(llm, "_no_retry_client", lambda: client)

    async def authorize(attempt: int) -> None:
        if attempt == 2:
            raise RuntimeError("permission withdrawn")

    with pytest.raises(RuntimeError, match="permission withdrawn"):
        await llm._complete(
            model="test",
            effort=None,
            rubric="test",
            user_content="test",
            schema={"type": "object"},
            max_tokens=256,
            before_provider_call=authorize,
        )

    assert len(client.calls) == 1


async def test_v2_usage_and_outcome_logs_are_attributed_to_the_contract(
    monkeypatch, caplog
):
    client = FakeClient([make_response(recalled(4))])
    monkeypatch.setattr(llm, "_no_retry_client", lambda: client)

    with caplog.at_level("INFO", logger="app.services.llm"):
        await llm.score_answer(
            **SCORE_ARGS, probes=AT_CAP, scoring_contract_version=2
        )

    messages = [record.getMessage() for record in caplog.records]
    usage = next(message for message in messages if message.startswith("llm model="))
    assert "purpose=score_v2" in usage
    assert "stop=end_turn" in usage
    assert "request_id=msg_test" in usage
    assert "llm purpose=score_v2 event=scoring_outcome status=complete" in messages


async def test_v2_contract_failure_is_attributed_for_invalid_rate(monkeypatch, caplog):
    client = FakeClient([make_response(recalled(2, probe=""))])
    monkeypatch.setattr(llm, "_no_retry_client", lambda: client)

    with caplog.at_level("WARNING", logger="app.services.llm"):
        with pytest.raises(llm.LLMError):
            await llm.score_answer(
                **SCORE_ARGS, probes=NO_PROBES, scoring_contract_version=2
            )

    assert "llm purpose=score_v2 event=invalid_contract" in [
        record.getMessage() for record in caplog.records
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {**recalled(4), "unexpected": "field"},
        {key: value for key, value in recalled(4).items() if key != "feedback"},
        {**recalled(4), "feedback": {"text": "not a string"}},
        {**recalled(2, PROBE), "follow_up_question": [PROBE]},
        {**recalled(4), "mastery_summary": 123},
    ],
)
async def test_v2_strict_schema_keys_and_text_types_are_enforced(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    stub_completion(monkeypatch, payload)
    with pytest.raises(llm.LLMError):
        await llm.score_answer(
            **SCORE_ARGS, probes=NO_PROBES, scoring_contract_version=2
        )

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

    await llm.score_answer(**SCORE_ARGS, probes=AT_CAP)

    assert client.calls[0]["output_config"]["effort"] == "low"


async def test_paid_preflight_builder_is_the_exact_scoring_request(
    fake_client, monkeypatch
):
    client = fake_client(make_response(scored(4)))
    monkeypatch.setattr(llm.get_settings(), "scoring_effort", "low")

    await llm.score_answer(**SCORE_ARGS, probes=AT_CAP)
    completion = llm.build_score_answer_completion(
        model=llm.get_settings().scoring_model,
        effort="low",
        **SCORE_ARGS,
        probes=AT_CAP,
    )
    counted = llm.count_params_for_completion(completion)

    assert counted == {
        key: value for key, value in client.calls[0].items() if key != "max_tokens"
    }
    assert "max_tokens" not in counted


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

    result = await llm.score_answer(**SCORE_ARGS, probes=AT_CAP)

    assert len(client.calls) == 2
    assert result.score == 4


async def test_unparseable_twice_raises(fake_client):
    client = fake_client(make_response(text="nope"), make_response(text="still nope"))

    with pytest.raises(llm.LLMError):
        await llm.score_answer(**SCORE_ARGS, probes=AT_CAP)

    assert len(client.calls) == 2


async def test_transport_failure_is_not_retried_here(fake_client):
    """The SDK already retried (SDK_MAX_RETRIES); a second layer would stack."""
    client = fake_client(RuntimeError("overloaded"))

    with pytest.raises(llm.LLMError):
        await llm.score_answer(**SCORE_ARGS, probes=AT_CAP)

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
