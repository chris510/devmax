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

# The gate is a product rule, not a prompt detail, so it is owned by the domain
# module and interpolated into the rubric below. One source of truth for the five
# questions means the prompt and the validator that enforces them cannot drift.
from app.services.study_plan import GATE_QUESTIONS

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

# The three session rubrics below are byte-identical across calls — but none is a
# prompt-cache breakpoint. The minimum cacheable prefix is 1024 tokens on Sonnet 5
# and 4096 on Haiku 4.5; measured via `count_tokens`, SCORING_RUBRIC is ~770,
# REATTEMPT_RUBRIC ~810 and QUESTION_RUBRIC ~220, so a `cache_control` marker on any
# would silently no-op (no error, just cache_read=0 forever). Padding to reach the
# floor would cost more input tokens per call than caching could return at this
# volume. The cache_* fields in the log line below prove it stays at zero.
#
# IMPORT_RUBRIC is the exception and *is* cached — it clears 1024 tokens on its own
# and Opus 5's floor is 512. See docs/DEVIATIONS.md §3.

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
Anchor it in what was actually said — refer to the specific claim, term, or example \
the answer used, so the probe could not have been written before hearing it. A probe \
that would fit any answer to this question is a failure.

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

Ask about ONE mechanism, and name it. Many topics are written as a heading followed \
by a list of the things it covers — "Hash sharding: routing a key, adding capacity, \
and migrating ownership". That list is the card's scope, not the question: pick the \
single most load-bearing item in it and ask about that one thing. Never ask the \
engineer to walk through the list, survey the area, or "describe your approach to" \
the topic as a whole — a question that could be answered by naming the sub-headings \
is a failure, and so is one that just re-reads the topic back as a prompt.

The engineer answers cold, with nothing on screen but the question and no chance to \
ask what you meant. So the question must stand alone: state the mechanism or scenario \
in plain words rather than pointing at the card, and never depend on a previous \
session, a framework name they may not use, or wording only this card would explain.

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

IMPORT_RUBRIC = """\
You are converting a study guide into a structured study plan. The guide may be \
about anything — distributed systems, anatomy, constitutional law, a certification \
syllabus, a language. Stay subject-agnostic: never introduce interview-prep framing \
into a plan that isn't about interviews, and never introduce clinical or legal \
framing into one that isn't.

Read the guide and produce phases, weeks, and items.

STRUCTURE
Produce 3-5 phases and exactly the number of weeks the user requested. Weeks are \
numbered 1..N across the whole plan, not restarted per phase, and each week belongs \
to exactly one phase. Phases occupy contiguous week ranges in order.

ITEMS
Every item is `learn` (take in new material), `practice` (produce something), or \
`retrieve` (recall closed-book). Priority is `core` (the plan fails without it), \
`optional` (worth doing, safe to defer), or `recurring` (a repeated activity).

Retrieval activities:
  - If the guide already contains a retrieval activity, import it with \
origin `imported`.
  - You may propose additional ones with origin `generated`, each pointing at the \
Learn or Practice item it retrieves via `source_item_key`. Do NOT generate one when \
the guide already has an equivalent activity for that material.
  - Retrieval defaults to `recurring`, not `core`. Only mark it core if the guide \
explicitly makes progression depend on it.

ESTIMATES
`estimate_minutes` must be a positive multiple of 30. Use the guide's own figures \
where it gives them (`estimate_source: imported`); otherwise estimate from the scope \
of the work (`estimate_source: generated`). Set `estimate_confidence` honestly — \
`needs_review` whenever you are guessing at scope rather than reading a figure.

Do NOT attempt to make weekly totals fit the user's capacity. Estimate each item on \
its own merits and let the application do the arithmetic. A plan that does not fit \
is a real finding the user needs to see, not a problem to hide by shrinking numbers.

DEPENDENCIES
Record a dependency only where one topic genuinely cannot be understood or produced \
before another. `kind: hard` blocks ordering; `kind: soft` is a preference. Use \
source `imported` with confidence `high` only when the guide uses explicit \
prerequisite language ("before", "prerequisite", "once you have"). Anything you \
worked out yourself is `inferred`, and its `rationale` must say what in the guide \
suggested it.

PROVENANCE
For every item, `source_start` and `source_end` are character offsets into the guide \
text exactly as given to you, and `source_excerpt` is the substring they delimit. \
Count characters from 0. If a line of the guide is ambiguous, say how you read it in \
`parser_interpretation`. If you could not find a source span, use null offsets and an \
empty excerpt rather than guessing — a wrong offset is worse than a missing one.

OVERVIEW TITLES
Each phase and week needs a concise `overview_title` of 2-5 words alongside its \
`full_title`:
  1. Name the subject of the week, not the activity list.
  2. One to five words and 28 characters or fewer. One word is ideal when a single \
word names the subject ("Databases", "Coordination", "Acid-base").
  3. Never a truncation and never an ellipsis — write a real label instead.
  4. Keep the guide's own terminology; don't introduce vocabulary the user never wrote.
  5. Stay subject-agnostic.
  6. Unique within its phase; qualify the second if two would collide.
  7. No vague fragments: "Systems", "Advanced", "Part 2" are all disallowed.

SUBJECT
`subject_slug` is a lowercase hyphenated key for the subject. Set \
`supports_technical_recall_cards` true only for software and systems engineering \
subjects that can be graded on mechanism accuracy, trade-off awareness, and \
failure-mode awareness. Anatomy, law, language learning, and general certification \
study are false.

FINDINGS
List every item whose estimate you are unsure of in `unresolved_estimates`, and \
anything in the guide you could not place in `possible_omissions`. Under-reporting \
these is worse than over-reporting: the user reviews them before the plan is created.

Return only the structured fields. No preamble, no code fences, no commentary.\
"""

_NULLABLE_INT: dict[str, Any] = {"anyOf": [{"type": "integer"}, {"type": "null"}]}
_NULLABLE_STR: dict[str, Any] = {"anyOf": [{"type": "string"}, {"type": "null"}]}

IMPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "subject_slug": {"type": "string"},
        "supports_technical_recall_cards": {"type": "boolean"},
        "plan_title": {"type": "string"},
        "phases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "full_title": {"type": "string"},
                    "overview_title": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["index", "full_title", "overview_title", "description"],
                "additionalProperties": False,
            },
        },
        "weeks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "phase_index": {"type": "integer"},
                    "full_title": {"type": "string"},
                    "overview_title": {"type": "string"},
                },
                "required": ["index", "phase_index", "full_title", "overview_title"],
                "additionalProperties": False,
            },
        },
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "week_index": {"type": "integer"},
                    "guide_order": {"type": "integer"},
                    "type": {"type": "string", "enum": ["learn", "practice", "retrieve"]},
                    "priority": {
                        "type": "string",
                        "enum": ["core", "optional", "recurring"],
                    },
                    "full_title": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "done_when": {"type": "string"},
                    "estimate_minutes": {"type": "integer"},
                    "estimate_source": {
                        "type": "string",
                        "enum": ["imported", "generated", "user_edited"],
                    },
                    "estimate_confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "needs_review"],
                    },
                    "origin": {
                        "type": "string",
                        "enum": ["imported", "generated", "manual"],
                    },
                    "source_item_key": _NULLABLE_STR,
                    "source_start": _NULLABLE_INT,
                    "source_end": _NULLABLE_INT,
                    "source_excerpt": {"type": "string"},
                    "parser_interpretation": {"type": "string"},
                },
                "required": [
                    "key",
                    "week_index",
                    "guide_order",
                    "type",
                    "priority",
                    "full_title",
                    "why_it_matters",
                    "done_when",
                    "estimate_minutes",
                    "estimate_source",
                    "estimate_confidence",
                    "origin",
                    "source_item_key",
                    "source_start",
                    "source_end",
                    "source_excerpt",
                    "parser_interpretation",
                ],
                "additionalProperties": False,
            },
        },
        "dependencies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "prerequisite_key": {"type": "string"},
                    "dependent_key": {"type": "string"},
                    "kind": {"type": "string", "enum": ["hard", "soft"]},
                    "source": {
                        "type": "string",
                        "enum": ["imported", "inferred", "user_added"],
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "needs_review"],
                    },
                    "rationale": {"type": "string"},
                    "source_excerpt": {"type": "string"},
                },
                "required": [
                    "prerequisite_key",
                    "dependent_key",
                    "kind",
                    "source",
                    "confidence",
                    "rationale",
                    "source_excerpt",
                ],
                "additionalProperties": False,
            },
        },
        "unresolved_estimates": {"type": "array", "items": {"type": "string"}},
        "possible_omissions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "note": {"type": "string"},
                    "source_excerpt": {"type": "string"},
                },
                "required": ["note", "source_excerpt"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "subject",
        "subject_slug",
        "supports_technical_recall_cards",
        "plan_title",
        "phases",
        "weeks",
        "items",
        "dependencies",
        "unresolved_estimates",
        "possible_omissions",
    ],
    "additionalProperties": False,
}

# The five gate questions are product rules, so they live in the domain module and
# are interpolated here. Restating them would let the prompt and the validator that
# enforces them drift apart, and the drift would look like a model failure.
_GATE_LIST = "\n".join(
    f"  {n}. {question}" for n, question in enumerate(GATE_QUESTIONS, start=1)
)

CARD_PROPOSAL_RUBRIC = f"""\
You are proposing spaced-repetition recall cards from a study-plan item the \
engineer has just finished. The review budget is scarce, so most items should \
produce no card at all.

Every candidate must pass all five of these questions. Answer each one \
independently and honestly:

{_GATE_LIST}

Failing any single question means the candidate is not suggested. Do not soften an \
answer to get a card through — a card that fails question 3 will be reviewed dozens \
of times over the next year and will teach a definition each time.

Propose at most 3 candidates, and propose none at all if you are not confident. \
Returning an empty list is a good answer and the common one.

For each candidate write:
  - `topic`: a short noun phrase, the way a card is titled.
  - `canonical_question`: the question this card will ask at every review, forever. \
It must force reconstruction of a mechanism rather than recall of a definition, and \
prefer a concrete scenario. This exact text is stored and reused, so write it as the \
final version.
  - `category`: a short subject grouping.
  - `reason`: one sentence on why this card earns its place.
  - `gate`: five entries, one per question, each with `question_index` (1-5), \
`passed`, and a `reason` specific to this candidate.

Return only the structured fields. No preamble, no code fences, no commentary.\
"""

CARD_PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "category": {"type": "string"},
                    "canonical_question": {"type": "string"},
                    "reason": {"type": "string"},
                    "gate": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question_index": {
                                    "type": "integer",
                                    "enum": [1, 2, 3, 4, 5],
                                },
                                "passed": {"type": "boolean"},
                                "reason": {"type": "string"},
                            },
                            "required": ["question_index", "passed", "reason"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["topic", "category", "canonical_question", "reason", "gate"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}

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
    cache_rubric: bool = False,
    stream: bool = False,
) -> dict[str, Any]:
    """One structured-output call, with a single retry on a parse failure.

    ``output_config.format`` constrains the response to the schema, so the parse
    below should never fail — the retry is a backstop for a model or config that
    doesn't support structured outputs. First call with a new schema pays a
    one-time compilation cost; it's cached for 24h after that.

    Transport failures are *not* retried here; the SDK already did that (see
    SDK_MAX_RETRIES) and anything reaching the ``except`` below has exhausted it.

    ``stream`` is required above roughly 16k ``max_tokens``: a non-streaming
    request that large sits on an idle connection long enough to hit an HTTP
    timeout, and the SDK refuses it outright. Only the guide importer needs it —
    a whole 12-week plan does not fit in a scoring call's budget.

    ``cache_rubric`` puts a cache breakpoint on the system block. Only worth
    setting when the rubric clears the model's minimum cacheable prefix; below it
    the marker silently does nothing.
    """
    system_block: dict[str, Any] = {"type": "text", "text": rubric}
    if cache_rubric:
        system_block["cache_control"] = {"type": "ephemeral"}

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [system_block],
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
            if stream:
                async with client.messages.stream(**kwargs) as streamed:
                    response = await streamed.get_final_message()
            else:
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


async def import_guide(
    *,
    guide_text: str,
    requested_weeks: int,
    weekly_capacity_minutes: int,
    mode: str,
    deadline: str | None,
    subject_hint: str,
    title_hint: str,
) -> dict[str, Any]:
    """Turn a pasted study guide into structured plan data. Returns raw fields.

    Deliberately returns the parsed dict rather than a domain object: everything
    the model says is untrusted until `study_plan_import.validate_import` has
    recomputed the arithmetic and checked the offsets against the guide text.
    Nothing here can create a plan.

    The guide goes in the *user* turn, after the cached rubric — it is the
    volatile part of the request and putting it in the system block would
    invalidate the cache on every call.
    """
    settings = get_settings()
    context = [
        f"Requested duration: {requested_weeks} weeks",
        f"Weekly capacity: {weekly_capacity_minutes} minutes",
        f"Mode: {mode}",
        f"Hard deadline: {deadline}" if deadline else "No hard deadline.",
        f"Subject hint: {subject_hint}" if subject_hint else None,
        f"Title hint: {title_hint}" if title_hint else None,
        "",
        "GUIDE TEXT BEGINS. Character offsets are counted from 0 at the G of the",
        "first line below, over this exact string.",
        "",
        guide_text,
    ]

    return await _complete(
        model=settings.studyplan_model,
        effort=settings.studyplan_effort,
        rubric=IMPORT_RUBRIC,
        user_content="\n".join(c for c in context if c is not None),
        schema=IMPORT_SCHEMA,
        # A 12-week plan runs to ~100 items, each with an excerpt and a rationale.
        # Sized for the whole structure in one response, because a truncated plan
        # is worse than a failed one: it validates as "missing content" and the
        # user cannot tell whether the guide or the importer dropped it.
        #
        # This is deliberately large. On Opus 5 thinking is on by default and
        # `max_tokens` bounds thinking *plus* the response — at effort `high` a
        # 32000 budget was consumed entirely by thinking and the call returned a
        # single empty thinking block with `stop_reason: max_tokens`. Measured
        # against docs/CURRICULUM.md, a successful import spends roughly 20-30k on
        # thinking and 20-25k on the structure, so this leaves real headroom.
        max_tokens=96000,
        # Streaming is not optional at this size — the SDK refuses a non-streaming
        # request it estimates will outlive the HTTP timeout.
        stream=True,
        cache_rubric=True,
    )


async def propose_cards(
    *,
    subject: str,
    item_title: str,
    why_it_matters: str,
    done_when: str,
    source_excerpt: str,
    existing_weak_topics: list[str],
) -> list[dict[str, Any]]:
    """Candidate recall cards for a completed plan item. Proposals only.

    `existing_weak_topics` is what gate question 5 is answered against — without
    the cards already competing for the same review budget, the model cannot
    judge whether a new one is a better use of it than an existing weak one.
    """
    settings = get_settings()
    context = [
        f"Subject: {subject}",
        f"Completed item: {item_title}",
        f"Why it matters: {why_it_matters}" if why_it_matters else None,
        f"Done when: {done_when}" if done_when else None,
        f"From the guide: {source_excerpt}" if source_excerpt else None,
    ]
    if existing_weak_topics:
        context.append("Existing cards already competing for the same review budget:")
        context.extend(f"  - {t}" for t in existing_weak_topics)
    else:
        context.append("There are no existing weak cards competing for review budget.")

    data = await _complete(
        model=settings.card_proposal_model,
        effort=settings.card_proposal_effort,
        rubric=CARD_PROPOSAL_RUBRIC,
        user_content="\n".join(c for c in context if c is not None),
        schema=CARD_PROPOSAL_SCHEMA,
        # At most three candidates, each a question plus five gate answers.
        max_tokens=4000,
    )
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        raise LLMError(f"card proposal response had no candidate list: {data!r}")
    return [c for c in candidates if isinstance(c, dict)]


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
