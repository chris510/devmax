"""Question generation and scoring. See spec.md §LLM integration.

Pure-ish functions independent of FastAPI request context so they're directly
unit-testable — the callers pass plain values, not ORM sessions.
"""

import json
import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from anthropic import AsyncAnthropic

from app.config import get_settings

log = logging.getLogger(__name__)

# The composite band that earns a second attempt before scoring. `spec.md` says
# 2-3; the low end is 1, and 0 is deliberately still excluded — docs/DEVIATIONS.md
# §15 has the reasoning.
FOLLOW_UP_LOW = 1
FOLLOW_UP_HIGH = 3

# Retries match the SDK's own default, pinned so a future SDK change can't
# quietly alter how long a session can stall. The timeout is the real
# departure: 600s is the default and is absurd for a session the user is
# sitting through — a hung scoring call has to fail while they still have the
# phone in their hand.
SDK_MAX_RETRIES = 2
SDK_TIMEOUT_SECONDS = 45.0

# Every rubric below is byte-identical across calls — but none is a prompt-cache
# breakpoint. The minimum cacheable prefix is 1024 tokens on Sonnet 5 and 4096 on
# Haiku 4.5; measured via `count_tokens`, SCORING_RUBRIC is ~770, REATTEMPT_RUBRIC
# ~810 and QUESTION_RUBRIC ~220, so a `cache_control` marker on any would silently
# no-op (no error, just cache_read=0 forever). Padding to reach the floor would cost
# more input tokens per call than caching could return at this volume. The cache_*
# fields in the log line below prove it stays at zero.

# Shared by every rubric that grades a spoken answer. Hoisted rather than restated
# so a change to how transcription artifacts are treated cannot apply to one rubric
# and not the other — they grade the same transcripts and write the same column.
# Interpolation happens once at import, so each rubric string stays byte-identical
# across calls (see the cache note above).
VOICE_TRANSCRIPT_RULE = """\
Answers arrive as voice transcripts. They will be conversational and disfluent and \
may contain speech-to-text errors. Score the substance. Never penalize verbal filler \
("um", "like", "so yeah"), false starts, or obvious transcription artifacts — if a \
word is clearly a mis-transcription of the right technical term, treat it as correct.\
"""

MASTERY_SUMMARY_RULE = """\
`mastery_summary` replaces the card's previous rolling summary. One or two sentences \
in lowercase fragment style, e.g. "solid on ring mechanics, shaky on virtual nodes".\
"""

SCORING_RUBRIC = f"""\
You are grading a spaced-repetition recall session for a senior backend engineer \
preparing for interviews at Anthropic, OpenAI, and Google.

Score the answer on three axes, 0-5 each, independently:

  mechanism_accuracy — is the underlying mechanism correct?
    0 no recall attempted or fundamentally wrong
    1 names the topic but the mechanism described is incorrect
    2 partial mechanism, major gaps or a confidently wrong detail
    3-5 core mechanism correct, distinguish by completeness

  trade_off_awareness — did they name the relevant trade-offs unprompted?
  failure_mode_awareness — did they name how/when this breaks, unprompted?

Do not score fluency, length, confidence, or enthusiasm.

{VOICE_TRANSCRIPT_RULE}

`feedback` is one to three sentences, and its content depends on mechanism_accuracy:
  - If mechanism_accuracy <= 2: state the correct mechanism directly, in plain terms —
    don't just note that it was wrong or incomplete. This is the single most important
    thing feedback does; a low mechanism score with vague feedback is a bug, not a
    valid response.
  - If mechanism_accuracy >= 3: skip re-explaining the mechanism. Instead, supply
    whichever of trade_off_awareness or failure_mode_awareness scored lower — state
    the actual trade-off or failure mode, don't just note it was missing.
  Never generic encouragement. Never congratulatory.

`follow_up_question` is a probe at the single most important gap in this answer, \
phrased as one short question and prefaced with "One more — ". Always write one, \
even when the answer was strong; the caller decides whether to use it.

{MASTERY_SUMMARY_RULE}

Return only the structured fields. No preamble, no code fences, no commentary.\
"""

QUESTION_RUBRIC = """\
You are running a spaced-repetition recall session for a senior backend engineer \
preparing for system design interviews across product and infrastructure companies.

Generate ONE question about the given topic that forces the engineer to reconstruct \
the mechanism from memory rather than recite a definition. Prefer concrete scenarios \
("you add a sixth node to a five-node ring — what moves?") over open prompts \
("explain consistent hashing"). If a mastery summary indicates a specific weak area, \
target that area. Do not repeat any of the recent questions listed.

The question is read aloud and answered by voice in under two minutes. One question, \
no multi-part sub-questions, no preamble.\
"""

REATTEMPT_RUBRIC = f"""\
You are grading a coached re-attempt in a spaced-repetition session for a senior \
backend engineer.

The engineer answered, scored poorly on mechanism accuracy, and was then TOLD the \
correct mechanism. They are now saying it back. You are grading whether they \
reconstructed it, not whether it matches what they were told.

  5 accurate reconstruction in their own framing, plus a correct extension they \
supplied themselves — applied it to the specific scenario, named a consequence, or \
connected it to something the feedback did not mention. The extension must be \
correct and do actual work; naming adjacent jargon is not an extension.
  3-4 accurate reconstruction in their own words, no real extension
  1-2 accurate, but tracks the feedback's phrasing and structure closely enough that \
it reads as echo rather than re-derivation
  0 did not reproduce the mechanism, or reproduced it wrongly, even with the answer \
in front of them

Grade what they generated, not how fluently they said it. This is not a polish \
judgement and it does not contradict the transcript rule below: a halting, disfluent \
answer that genuinely re-derives the mechanism outranks a smooth echo of it. \
Restating the feedback well proves it was read, not that it was encoded.

Length is not evidence. A long answer that is mostly hedging and half-memories is \
not a 5 — score the mechanism they actually produced.

{VOICE_TRANSCRIPT_RULE}

{MASTERY_SUMMARY_RULE}

The summary must record that this was *coached*. The engineer had already failed \
this card unaided — that is the only reason this turn exists — so a reconstruction \
here, however good, is not evidence they can recall it cold. Say so explicitly, \
whatever the score: "got there after being told", "solid once corrected", "could \
only parrot it back". Never write a summary that reads as unaided mastery. This is \
the one failure that makes this whole turn worse than not asking, because the \
summary is what the next session grades against.

Return only the structured fields. No preamble, no code fences, no commentary.\
"""

REATTEMPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mechanism_accuracy": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5]},
        "mastery_summary": {"type": "string"},
    },
    "required": ["mechanism_accuracy", "mastery_summary"],
    "additionalProperties": False,
}

QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"question": {"type": "string"}},
    "required": ["question"],
    "additionalProperties": False,
}

AXES = ("mechanism_accuracy", "trade_off_awareness", "failure_mode_awareness")

SCORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mechanism_accuracy": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5]},
        "trade_off_awareness": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5]},
        "failure_mode_awareness": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5]},
        "feedback": {"type": "string"},
        "follow_up_question": {"type": "string"},
        "mastery_summary": {"type": "string"},
    },
    "required": [
        "mechanism_accuracy",
        "trade_off_awareness",
        "failure_mode_awareness",
        "feedback",
        "follow_up_question",
        "mastery_summary",
    ],
    "additionalProperties": False,
}


# Stray wrappers the model occasionally leaves on `mastery_summary` — matched
# straight quotes, curly quotes, and CJK brackets have all shown up in live output
# (`shaky.'`, `shaky.」`). The string renders verbatim on Today and Card History and
# is fed back as context to the next scoring call, so it is cleaned on ingest rather
# than at each of those three read sites.
_SUMMARY_WRAPPERS = "\"'`«»‘’“”「」『』"


def clean_summary(text: str) -> str:
    """Trim a model-written rolling summary to what should reach the database.

    Deliberately conservative: it strips surrounding whitespace and stray wrapper
    punctuation and nothing else. Sentence-final `.`/`!`/`?` survive, because those
    are the summary's own text rather than packaging around it.
    """
    return text.strip().strip(_SUMMARY_WRAPPERS).strip()


def derive_composite(mechanism: int, trade_offs: int, failure_modes: int) -> int:
    """The 0-5 number the app displays, computed rather than guessed.

    A direct restatement of the bands the old blended rubric asked the model to
    apply in its head (0/1 mechanism wrong, 3 mechanism only, 4 + trade-offs,
    5 complete), so nothing downstream sees a meaning change. Deriving it also
    removes a real source of inconsistency: the model used to return a blended
    score that could disagree with its own stated reasoning.

    Display only. Scheduling gates on ``mechanism_accuracy`` — see
    ``scheduler.rating_for``.
    """
    # A failing mechanism caps the composite at itself: no recall, a wrong
    # mechanism, or a partial one cannot be rescued by depth elsewhere.
    if mechanism <= 2:
        return mechanism
    if trade_offs <= 2 and failure_modes <= 2:
        return 3  # correct mechanism, nothing else
    if failure_modes <= 2:
        return 4  # mechanism + trade-offs, failure modes still thin
    return 5  # all three present


class LLMError(RuntimeError):
    """Raised when the model can't be reached or its output can't be parsed."""


@dataclass(frozen=True)
class ScoreResult:
    """Either a follow-up probe or a final score — never both.

    The follow-up decision is made *here*, in code, from the score the model
    returned. The model always writes a probe; whether it gets used depends on
    ``follow_up_used``, so "maximum one follow-up per session" is structurally
    guaranteed rather than dependent on the model obeying a prompt.
    """

    status: str  # "follow_up" | "complete"
    score: int | None = None
    mechanism_accuracy: int | None = None
    trade_off_awareness: int | None = None
    failure_mode_awareness: int | None = None
    feedback: str = ""
    follow_up_question: str | None = None
    mastery_summary: str = ""

    def __post_init__(self) -> None:
        """A completed result always carries all three axes.

        Enforced here so `submit_answer` can read `mechanism_accuracy` without a
        fallback. The fallback it replaces was a path where the composite reached
        SM-2 — the exact conflation this decomposition exists to remove, and one
        no test could have caught, because a `ScoreResult` built without axes is
        only reachable by constructing one by hand.
        """
        if self.status != "complete":
            return
        missing = [axis for axis in AXES if getattr(self, axis) is None]
        if missing:
            raise ValueError(f"a complete ScoreResult is missing {', '.join(missing)}")


@dataclass(frozen=True)
class ReattemptResult:
    """Turn 3's grade. One axis and a summary — no composite, by design.

    There is deliberately no `score` field. Deriving a composite from a single axis
    would invent the two it doesn't have, and the composite is what the app displays
    and what history records. Turn 3 changes neither.
    """

    mechanism_accuracy: int
    mastery_summary: str


@lru_cache
def _client() -> AsyncAnthropic:
    """One client per process — each construction opens its own connection pool.

    Cached rather than per-call: the previous version leaked an httpx pool on
    every request. Mirrors the ``get_settings`` caching in app.config, and is
    safe for the same reason — the API key is fixed for the process lifetime.
    """
    return AsyncAnthropic(
        api_key=get_settings().anthropic_api_key,
        max_retries=SDK_MAX_RETRIES,
        timeout=SDK_TIMEOUT_SECONDS,
    )


async def _complete(
    *,
    model: str,
    effort: str | None,
    rubric: str,
    user_content: str,
    schema: dict[str, Any],
    max_tokens: int,
) -> dict[str, Any]:
    """One structured-output call, with a single retry on a parse failure.

    ``output_config.format`` constrains the response to the schema, so the parse
    below should never fail — the retry is a backstop for a model or config that
    doesn't support structured outputs. First call with a new schema pays a
    one-time compilation cost; it's cached for 24h after that.

    Transport failures are *not* retried here; the SDK already did that (see
    SDK_MAX_RETRIES) and anything reaching the ``except`` below has exhausted it.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": rubric}],
        "messages": [{"role": "user", "content": user_content}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }
    # Haiku 4.5 rejects `effort` outright, so it's per-model config rather than a
    # constant — see spec.md's "make the model a config value per function".
    if effort is not None:
        kwargs["output_config"]["effort"] = effort

    client = _client()
    last_error: Exception | None = None

    for attempt in (1, 2):
        started = time.monotonic()
        try:
            response = await client.messages.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"{model} call failed: {exc}") from exc

        elapsed_ms = int((time.monotonic() - started) * 1000)
        usage = response.usage
        log.info(
            "llm model=%s attempt=%d ms=%d in=%d out=%d cache_read=%d cache_write=%d",
            model,
            attempt,
            elapsed_ms,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_read_input_tokens or 0,
            usage.cache_creation_input_tokens or 0,
        )

        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            last_error = exc
            log.warning(
                "llm model=%s attempt=%d unparseable output: %r", model, attempt, text[:200]
            )

    raise LLMError(f"{model} returned unparseable output twice") from last_error


async def generate_question(
    *,
    topic: str,
    category: str,
    pattern: str | None,
    source_company: str | None,
    mastery_summary: str,
    last_score: int | None,
    recent_questions: list[str],
) -> str:
    """The opening question for a card. Called on engagement, never on push."""
    settings = get_settings()
    context = [
        f"Topic: {topic}",
        f"Category: {category}",
        f"Pattern: {pattern}" if pattern else None,
        f"Asked at: {source_company}" if source_company else None,
        f"Rolling mastery summary: {mastery_summary}" if mastery_summary else "No prior sessions.",
        f"Last score: {last_score}" if last_score is not None else None,
    ]
    if recent_questions:
        context.append("Recent questions (do not repeat):")
        context.extend(f"  - {q}" for q in recent_questions)

    data = await _complete(
        model=settings.question_model,
        effort=settings.question_effort,
        rubric=QUESTION_RUBRIC,
        user_content="\n".join(c for c in context if c),
        schema=QUESTION_SCHEMA,
        max_tokens=1024,
    )
    question = str(data.get("question", "")).strip()
    if not question:
        raise LLMError("question generation returned an empty question")
    return question


async def score_answer(
    *,
    topic: str,
    mastery_summary: str,
    question_asked: str,
    answer_text: str,
    follow_up_question: str | None,
    follow_up_answer: str,
    follow_up_used: bool,
) -> ScoreResult:
    """Score the session so far, or return a probe if the answer was partial."""
    settings = get_settings()
    transcript = [
        f"Topic: {topic}",
        f"Rolling mastery summary: {mastery_summary}" if mastery_summary else None,
        "",
        f"QUESTION: {question_asked}",
        f"ANSWER: {answer_text}",
    ]
    if follow_up_used and follow_up_question:
        transcript += [f"FOLLOW-UP: {follow_up_question}", f"ANSWER: {follow_up_answer}"]

    data = await _complete(
        model=settings.scoring_model,
        effort=settings.scoring_effort,
        rubric=SCORING_RUBRIC,
        user_content="\n".join(t for t in transcript if t is not None),
        schema=SCORE_SCHEMA,
        max_tokens=8000,
    )

    # The JSON schema makes all three axes required, so this should be unreachable —
    # but an unguarded KeyError/ValueError here is a 500, and the client only knows
    # how to retry a 503. Losing a spoken answer is the worst failure mode in the
    # product.
    try:
        mechanism, trade_offs, failure_modes = (int(data[axis]) for axis in AXES)
    except (KeyError, TypeError, ValueError) as exc:
        raise LLMError(f"scoring response had no usable axis scores: {data!r}") from exc

    score = derive_composite(mechanism, trade_offs, failure_modes)
    probe = str(data.get("follow_up_question", "")).strip()

    if not follow_up_used and FOLLOW_UP_LOW <= score <= FOLLOW_UP_HIGH and probe:
        return ScoreResult(status="follow_up", follow_up_question=probe)

    return ScoreResult(
        status="complete",
        score=score,
        mechanism_accuracy=mechanism,
        trade_off_awareness=trade_offs,
        failure_mode_awareness=failure_modes,
        feedback=str(data.get("feedback", "")).strip(),
        mastery_summary=clean_summary(str(data.get("mastery_summary", ""))),
    )


async def score_reattempt(
    *,
    topic: str,
    question_asked: str,
    feedback_given: str,
    reattempt_answer: str,
    unaided_mechanism: int,
) -> ReattemptResult:
    """Grade turn 3 — the coached re-attempt. Never reaches SM-2.

    `feedback_given` is not optional context: without the text the engineer was
    shown, the model cannot tell reconstruction from recitation, which is the only
    thing this call measures. See docs/multi-turn-coaching-design.md §5.2.

    The turn-1/2 answers are deliberately absent — grading the re-attempt against
    the failed attempt invites scoring the delta rather than the reconstruction. But
    the unaided *score* is passed, because without it the model cannot know this was
    a coached turn at all and writes summaries that read as unaided mastery. That
    text is what the next session grades against, so an over-generous summary here
    is the one way turn 3 reaches a future scheduling decision.
    """
    settings = get_settings()
    transcript = [
        f"Topic: {topic}",
        "",
        f"QUESTION: {question_asked}",
        f"UNAIDED MECHANISM SCORE, BEFORE THEY WERE TOLD: {unaided_mechanism}/5",
        f"CORRECT MECHANISM, AS STATED TO THEM: {feedback_given}",
        f"THEIR RE-ATTEMPT: {reattempt_answer}",
    ]

    data = await _complete(
        model=settings.reattempt_model,
        effort=settings.reattempt_effort,
        rubric=REATTEMPT_RUBRIC,
        user_content="\n".join(transcript),
        schema=REATTEMPT_SCHEMA,
        # One enum integer and two sentences — well under 100 output tokens. Sized to
        # bound the worst case, not the expected one: a degenerate generation on a
        # turn the user is waiting through should fail fast, not run for 4000 tokens.
        max_tokens=512,
    )

    try:
        mechanism = int(data["mechanism_accuracy"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LLMError(f"re-attempt response had no usable score: {data!r}") from exc

    return ReattemptResult(
        mechanism_accuracy=mechanism,
        mastery_summary=clean_summary(str(data.get("mastery_summary", ""))),
    )
