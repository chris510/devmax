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

FOLLOW_UP_LOW = 2
FOLLOW_UP_HIGH = 3

# Retries match the SDK's own default, pinned so a future SDK change can't
# quietly alter how long a session can stall. The timeout is the real
# departure: 600s is the default and is absurd for a session the user is
# sitting through — a hung scoring call has to fail while they still have the
# phone in their hand.
SDK_MAX_RETRIES = 2
SDK_TIMEOUT_SECONDS = 45.0

# Byte-identical across every call — but *not* a prompt-cache breakpoint. The
# minimum cacheable prefix is 1024 tokens on Sonnet 5 and 4096 on Haiku 4.5;
# this rubric is ~450 and QUESTION_RUBRIC ~180, so a `cache_control` marker here
# would silently no-op (no error, just cache_read=0 forever). Padding to reach
# the floor would cost more input tokens per call than caching could return at
# this volume. The cache_* fields in the log line below prove it stays at zero.
SCORING_RUBRIC = """\
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

Answers arrive as voice transcripts. They will be conversational and disfluent and \
may contain speech-to-text errors. Score the substance. Never penalize verbal filler \
("um", "like", "so yeah"), false starts, or obvious transcription artifacts — if a \
word is clearly a mis-transcription of the right technical term, treat it as correct.

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

`mastery_summary` replaces the card's previous rolling summary. One or two sentences \
in lowercase fragment style, e.g. "solid on ring mechanics, shaky on virtual nodes".

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
        mastery_summary=str(data.get("mastery_summary", "")).strip(),
    )
