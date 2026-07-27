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

Score the answer 0-5 on three axes only: mechanism accuracy, trade-off awareness, \
and failure-mode awareness. Do not score fluency, length, confidence, or enthusiasm.

  0 - no recall, or fundamentally wrong mechanism
  1 - names the topic but the mechanism described is incorrect
  2 - partial mechanism, major gaps or a confidently wrong detail
  3 - correct core mechanism, missing trade-offs or failure modes
  4 - correct mechanism plus trade-offs, minor gaps
  5 - complete: mechanism, trade-offs, and failure modes, unprompted

Answers arrive as voice transcripts. They will be conversational and disfluent and \
may contain speech-to-text errors. Score the substance. Never penalize verbal filler \
("um", "like", "so yeah"), false starts, or obvious transcription artifacts — if a \
word is clearly a mis-transcription of the right technical term, treat it as correct.

`feedback` is one or two sentences, specific to what was actually said and what was \
missed. Not generic encouragement. Never congratulatory.

`follow_up_question` is a probe at the single most important gap in this answer, \
phrased as one short question and prefaced with "One more — ". Always write one, \
even when the answer was strong; the caller decides whether to use it.

`mastery_summary` replaces the card's previous rolling summary. One or two sentences \
in lowercase fragment style, e.g. "solid on ring mechanics, shaky on virtual nodes".

Return only the structured fields. No preamble, no code fences, no commentary.\
"""

QUESTION_RUBRIC = """\
You are running a spaced-repetition recall session for a senior backend engineer \
preparing for interviews at Anthropic, OpenAI, and Google.

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

SCORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5]},
        "feedback": {"type": "string"},
        "follow_up_question": {"type": "string"},
        "mastery_summary": {"type": "string"},
    },
    "required": ["score", "feedback", "follow_up_question", "mastery_summary"],
    "additionalProperties": False,
}


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
    feedback: str = ""
    follow_up_question: str | None = None
    mastery_summary: str = ""


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

    # The JSON schema makes `score` required, so this should be unreachable — but an
    # unguarded KeyError/ValueError here is a 500, and the client only knows how to
    # retry a 503. Losing a spoken answer is the worst failure mode in the product.
    try:
        score = int(data["score"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LLMError(f"scoring response had no usable score: {data!r}") from exc

    probe = str(data.get("follow_up_question", "")).strip()

    if not follow_up_used and FOLLOW_UP_LOW <= score <= FOLLOW_UP_HIGH and probe:
        return ScoreResult(status="follow_up", follow_up_question=probe)

    return ScoreResult(
        status="complete",
        score=score,
        feedback=str(data.get("feedback", "")).strip(),
        mastery_summary=str(data.get("mastery_summary", "")).strip(),
    )
