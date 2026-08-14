"""Question generation and scoring. See spec.md §LLM integration.

Pure-ish functions independent of FastAPI request context so they're directly
unit-testable — the callers pass plain values, not ORM sessions.
"""

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from anthropic import AsyncAnthropic

from app.config import get_settings
from app.services.card_lifecycle import RUBRIC_FIELDS, scoring_rubric
from app.services.scoring_contract import SCORING_CONTRACT_V1, SCORING_CONTRACT_V2

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

# How many *scored* follow-ups a session may take, and the only place that number
# is decided. The parsers below enforce it and the router re-checks it at the write
# site, so a prompt alone can never extend a session. `session_probes` deliberately
# carries no `idx <= N` CHECK constraint — a second copy of the cap in the schema
# would have to be migrated in lockstep with this line.
MAX_SCORED_FOLLOW_UPS = 2

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
# REATTEMPT_RUBRIC ~810 and QUESTION_RUBRIC ~220 — the last has since grown to
# roughly ~370 with the scope-line rules, estimated rather than re-measured, and is
# still an order of magnitude under Haiku's floor. So a `cache_control` marker on any
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
You are grading a short, source-grounded spaced-repetition recall session. The \
subject may be software engineering, law, medicine, anatomy, or another field the \
learner can explain in words. The supplied answer anchor and excerpt are the \
authority; do not replace them with unsupported outside knowledge.

Score the answer on three axes, 0-5 each, independently:

  accuracy — is the essential concept, rule, process, or relationship correct?
    0 no recall attempted or fundamentally wrong
    1 names the topic but the essential account is incorrect
    2 partial account, major gaps or a confidently wrong detail
    3-5 essential account correct, distinguish by completeness

  depth — did they explain relevant reasoning, structure, causality, or application?
  boundaries — did they recognize relevant conditions, exceptions, limitations, \
trade-offs, or failure cases?

Do not score fluency, length, confidence, or enthusiasm.

When the user context includes a TRUSTED ANSWER BASIS and APPROVED ANSWER RUBRIC,
they are the authority for mechanism, accepted alternatives, trade-offs, failure
modes, and corrections. Do not improvise a conflicting answer frame or penalize an
alternative the approved rubric accepts.

{VOICE_TRANSCRIPT_RULE}

`feedback` is one to three sentences, and its content depends on accuracy:
  - If accuracy <= 2: state the correct essential account directly, in plain terms —
    don't just note that it was wrong or incomplete. This is the single most important
    thing feedback does; low Accuracy with vague feedback is a bug, not a
    valid response.
  - If accuracy >= 3: skip re-explaining the essential account. Instead, supply
    whichever of depth or boundaries scored lower — state
    the actual missing depth or boundary, don't just note it was missing.
  Never generic encouragement. Never congratulatory.

`follow_up_question` is a probe at the single most important gap in this answer, \
phrased as one short question. Preface it "One more — " when no scored follow-up has \
been used and "Last one — " when one has. Always write one, \
even when the answer was strong; the caller decides whether to use it.
Anchor it in what was actually said — refer to the specific claim, term, or example \
the answer used, so the probe could not have been written before hearing it. A probe \
that would fit any answer to this question is a failure.

`needs_more_evidence` is true only when, after this transcript, you cannot honestly \
distinguish between adjacent scores and one further probe would settle it. It reports \
missing signal, not a wrong answer: a wrong essential account is scored, not probed. \
Otherwise it is false.

{MASTERY_SUMMARY_RULE}

Return only the structured fields. No preamble, no code fences, no commentary.\
"""

SCORING_V2_RUBRIC = f"""\
You are grading a short, source-grounded spaced-repetition recall session. The \
supplied answer basis, excerpt, and approved rubric are the authority; do not \
replace them with unsupported outside knowledge.

Return one numeric learning signal: `recall_score`, an integer 0-5 measuring \
whether the learner reconstructed the essential account:
  0 no recall attempted or fundamentally wrong
  1 names the topic but the essential account is incorrect
  2 partial account with major gaps or a confidently wrong detail
  3 essential account correct after allowing one bounded omission
  4 correct and substantially complete essential account
  5 precise, complete essential account in the learner's own framing

Do not grade Depth or Boundaries. Do not derive a composite. Do not score \
fluency, length, confidence, enthusiasm, trade-off detail, exceptions, or failure \
cases unless the canonical question makes one part of the essential account.

{VOICE_TRANSCRIPT_RULE}

`feedback` is one to three direct sentences:
  - recall_score <= 2: state the correct essential account plainly.
  - recall_score >= 3: identify what essential account was successfully recalled \
and, if needed, the single bounded omission that kept it from the next score.
Never congratulate and never imply a numeric Depth or Boundaries grade.

`needs_more_evidence` is true only when, after this transcript, you cannot honestly \
distinguish between adjacent Recall scores and one further probe would settle it. It \
reports missing signal, not a wrong answer: a wrong essential account is scored, not \
probed. Otherwise it is false.

`follow_up_question` is one short probe at the single missing essential link, and is \
non-empty in exactly two cases. When the transcript says no scored follow-up has been \
used, write it prefaced "One more — " whenever recall_score is 1-3; for recall_score \
0 or 4-5 return an empty string and `needs_more_evidence` false. When one scored \
follow-up has been used, write it prefaced "Last one — " if and only if \
`needs_more_evidence` is true. On the final scored turn, return an empty string and \
`needs_more_evidence` false. The server validates this policy and structurally caps \
the session at two scored follow-ups.

`mastery_summary` replaces the prior rolling summary. Write one or two sentences \
in lowercase fragment style describing essential-account recall only: unaided, \
recovered after a probe, or still missing. Never claim strong/weak Depth or \
Boundaries.

Return only the structured fields. No preamble, no code fences, no commentary.\
"""

COACHING_RUBRIC = f"""\
You are responding to one optional, post-result qualitative practice turn. The \
learner's Recall score is already final and the schedule is already written. This \
turn must never produce a score, pass/fail label, mastery claim, or scheduling \
recommendation.

Use only the trusted answer basis and approved rubric. Give one or two concise \
sentences of `coaching_feedback` about what the learner's answer usefully explains \
and the single most important grounded point it still misses. Never congratulate. \
Never call the answer strong/weak mastery.

{VOICE_TRANSCRIPT_RULE}

Return only the structured field. No preamble, no code fences, no commentary.\
"""

QUESTION_RUBRIC = """\
You are running a short, source-grounded spaced-repetition recall session. The \
subject may be software engineering, law, medicine, anatomy, or another field that \
can be explained in words.

Generate ONE question about the topic that forces the learner to reconstruct the \
essential concept, rule, process, or relationship rather than recite a definition. \
Prefer concrete scenarios \
("you add a sixth node to a five-node ring — what moves?") over open prompts \
("explain consistent hashing"). If a mastery summary indicates a specific weak area, \
target that area. Do not repeat any of the recent questions listed.

When the user context includes a TRUSTED ANSWER BASIS and APPROVED ANSWER RUBRIC,
the question must be fully supported by them and test the required mechanism without
revealing it. Do not introduce a mechanism or failure mode that the trusted material
does not support.

A topic written as a heading plus a list — "Hash sharding: routing a key, adding \
capacity, and migrating ownership" — states the card's scope, not the question. Pick \
the single most load-bearing item and ask only about that one thing.

The learner hears the question once, cold, with nothing on screen and no chance to \
ask what you meant, so it must stand alone: state the mechanism or scenario in plain \
words rather than pointing at the card, and never lean on a previous session, a \
framework name they may not use, or wording only this card would explain.

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


def _grounding_context(
    answer_basis: str = "",
    answer_rubric: dict[str, str] | None = None,
    *,
    v2_aliases: bool = False,
) -> list[str]:
    """Stable labels for trusted, card-specific authority in LLM user turns."""
    lines: list[str] = []
    if answer_basis.strip():
        lines.append(f"TRUSTED ANSWER BASIS: {answer_basis.strip()}")
    # Keep the active V1 prompt byte-for-byte compatible during dark launch.
    # V2 scoring and qualitative coaching opt into the subject-agnostic aliases.
    rubric = scoring_rubric(answer_rubric) if v2_aliases else (answer_rubric or {})
    if any(str(value).strip() for value in rubric.values()):
        lines.append("APPROVED ANSWER RUBRIC:")
        lines.extend(
            f"  {name}: {str(value).strip()}"
            for name, value in rubric.items()
            if str(value).strip()
        )
    return lines

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

For every item, set `recall_supported` independently. It is true only when this \
specific item has a trusted source excerpt that supports one bounded technical \
mechanism suitable for a sub-two-minute Unprompted recall card. A broad mock, timed \
build, behavioral exercise, or item with no answer authority is false even when the \
overall subject supports technical recall cards.

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
                    "recall_supported": {"type": "boolean"},
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
                    "recall_supported",
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
_GATE_LIST = "\n".join(f"  {n}. {question}" for n, question in enumerate(GATE_QUESTIONS, start=1))

CARD_PROPOSAL_RUBRIC = f"""\
You are proposing spaced-repetition recall cards from a study-plan item the \
engineer has just finished. The review budget is scarce, so most items should \
produce no card at all.

The trusted source excerpt is the only answer authority. An observed practice gap \
may tell you which mechanism to test, but it may contain a misconception and must \
never supply, correct, or extend the answer. If the source does not contain enough \
information to support the canonical question and its expected mechanism, return no \
candidate for that gap.

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
  - `answer_rubric`: the source-supported expected answer frame, with exactly one \
required mechanism, acceptable alternative framing, key trade-off, key failure \
mode, and common misconception. Every value must be supported by the trusted source \
excerpt; if it is not, return no candidate.
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
                    "answer_rubric": {
                        "type": "object",
                        "properties": {field: {"type": "string"} for field in RUBRIC_FIELDS},
                        "required": list(RUBRIC_FIELDS),
                        "additionalProperties": False,
                    },
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
                "required": [
                    "topic",
                    "category",
                    "canonical_question",
                    "answer_rubric",
                    "reason",
                    "gate",
                ],
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
        "accuracy": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5]},
        "mastery_summary": {"type": "string"},
    },
    "required": ["accuracy", "mastery_summary"],
    "additionalProperties": False,
}

QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"question": {"type": "string"}},
    "required": ["question"],
    "additionalProperties": False,
}

AXES = ("accuracy", "depth", "boundaries")

SCORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "accuracy": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5]},
        "depth": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5]},
        "boundaries": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5]},
        "feedback": {"type": "string"},
        "follow_up_question": {"type": "string"},
        "needs_more_evidence": {"type": "boolean"},
        "mastery_summary": {"type": "string"},
    },
    "required": [
        "accuracy",
        "depth",
        "boundaries",
        "feedback",
        "follow_up_question",
        "needs_more_evidence",
        "mastery_summary",
    ],
    "additionalProperties": False,
}

SCORE_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "recall_score": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5]},
        "feedback": {"type": "string"},
        "follow_up_question": {"type": "string"},
        "needs_more_evidence": {"type": "boolean"},
        "mastery_summary": {"type": "string"},
    },
    "required": [
        "recall_score",
        "feedback",
        "follow_up_question",
        "needs_more_evidence",
        "mastery_summary",
    ],
    "additionalProperties": False,
}

COACHING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"coaching_feedback": {"type": "string"}},
    "required": ["coaching_feedback"],
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

    Display only. Scheduling gates on ``accuracy`` — see
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

    The follow-up decision is made *here*, in code. The model always writes a
    probe and reports whether it still lacks signal; the parsers decide from
    ``probes_used`` whether that probe is used — today's band rule for the first
    one, the model's ``needs_more_evidence`` claim for the second. At
    ``MAX_SCORED_FOLLOW_UPS`` the result is structurally complete whatever the
    model wrote, so the cap holds without depending on the model obeying a prompt.
    """

    status: str  # "follow_up" | "complete"
    score: int | None = None
    accuracy: int | None = None
    depth: int | None = None
    boundaries: int | None = None
    feedback: str = ""
    follow_up_question: str | None = None
    mastery_summary: str = ""
    scoring_contract_version: int = SCORING_CONTRACT_V1

    def __post_init__(self) -> None:
        """A completed result carries exactly the numeric signals its contract owns.

        Enforced here so `submit_answer` can read `accuracy` without a
        fallback. The fallback it replaces was a path where the composite reached
        SM-2 — the exact conflation this decomposition exists to remove, and one
        no test could have caught, because a `ScoreResult` built without axes is
        only reachable by constructing one by hand.
        """
        if self.status != "complete":
            return
        required = AXES if self.scoring_contract_version == SCORING_CONTRACT_V1 else ("accuracy",)
        missing = [axis for axis in required if getattr(self, axis) is None]
        if missing:
            raise ValueError(f"a complete ScoreResult is missing {', '.join(missing)}")
        if self.scoring_contract_version == SCORING_CONTRACT_V2:
            if self.depth is not None or self.boundaries is not None:
                raise ValueError("a V2 ScoreResult cannot carry numeric secondary axes")
            if self.score != self.accuracy:
                raise ValueError("a V2 ScoreResult score must equal Recall/Accuracy")


@dataclass(frozen=True)
class ReattemptResult:
    """Turn 3's grade. One axis and a summary — no composite, by design.

    There is deliberately no `score` field. Deriving a composite from a single axis
    would invent the two it doesn't have, and the composite is what the app displays
    and what history records. Turn 3 changes neither.
    """

    accuracy: int
    mastery_summary: str


@dataclass(frozen=True)
class CoachingResult:
    feedback: str


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


@lru_cache
def _no_retry_client() -> AsyncAnthropic:
    """V2 scoring/coaching client: one transmission, no hidden SDK retry."""
    return AsyncAnthropic(
        api_key=get_settings().anthropic_api_key,
        max_retries=0,
        timeout=SDK_TIMEOUT_SECONDS,
    )


def _message_params(
    *,
    model: str,
    effort: str | None,
    rubric: str,
    user_content: str,
    schema: dict[str, Any],
    max_tokens: int,
    cache_rubric: bool = False,
) -> dict[str, Any]:
    """Build the exact Messages request shared by live calls and paid-eval preflight."""
    system_block: dict[str, Any] = {"type": "text", "text": rubric}
    if cache_rubric:
        system_block["cache_control"] = {"type": "ephemeral"}

    params: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [system_block],
        "messages": [{"role": "user", "content": user_content}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }
    # Haiku 4.5 rejects `effort` outright, so it's per-model config rather than a
    # constant — see spec.md's "make the model a config value per function".
    if effort is not None:
        params["output_config"]["effort"] = effort
    return params


def count_params_for_completion(completion: dict[str, Any]) -> dict[str, Any]:
    """Return the free token-count request for a prepared ``_complete`` call.

    Evaluation tools use this instead of reconstructing prompts. A pricing guard
    that counts a slightly different request is not a guard at all.
    """
    params = _message_params(
        model=completion["model"],
        effort=completion["effort"],
        rubric=completion["rubric"],
        user_content=completion["user_content"],
        schema=completion["schema"],
        max_tokens=completion["max_tokens"],
        cache_rubric=completion.get("cache_rubric", False),
    )
    params.pop("max_tokens")
    return params


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
    retry: bool = True,
    purpose: str = "unknown",
) -> dict[str, Any]:
    """One structured-output call, optionally retrying one parse failure.

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
    kwargs = _message_params(
        model=model,
        effort=effort,
        rubric=rubric,
        user_content=user_content,
        schema=schema,
        max_tokens=max_tokens,
        cache_rubric=cache_rubric,
    )

    client = _client() if retry else _no_retry_client()
    last_error: Exception | None = None

    attempts = (1, 2) if retry else (1,)
    for attempt in attempts:
        started = time.monotonic()
        try:
            if stream:
                async with client.messages.stream(**kwargs) as streamed:
                    response = await streamed.get_final_message()
            else:
                response = await client.messages.create(**kwargs)
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            log.warning(
                "llm model=%s attempt=%d ms=%d purpose=%s "
                "event=transport_error error_type=%s",
                model,
                attempt,
                elapsed_ms,
                purpose,
                type(exc).__name__,
            )
            raise LLMError(f"{model} call failed: {exc}") from exc

        elapsed_ms = int((time.monotonic() - started) * 1000)
        usage = response.usage
        stop_reason = getattr(response, "stop_reason", "") or ""
        request_id = getattr(response, "_request_id", "") or ""
        log.info(
            "llm model=%s attempt=%d ms=%d in=%d out=%d cache_read=%d cache_write=%d "
            "purpose=%s stop=%s request_id=%s",
            model,
            attempt,
            elapsed_ms,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_read_input_tokens or 0,
            usage.cache_creation_input_tokens or 0,
            purpose,
            stop_reason,
            request_id,
        )

        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            last_error = exc
            log.warning(
                "llm model=%s attempt=%d purpose=%s stop=%s request_id=%s "
                "event=invalid_json",
                model,
                attempt,
                purpose,
                stop_reason,
                request_id,
            )

    raise LLMError(f"{model} returned unparseable output") from last_error


async def generate_question(
    *,
    topic: str,
    category: str,
    pattern: str | None,
    source_company: str | None,
    mastery_summary: str,
    last_score: int | None,
    recent_questions: list[str],
    answer_anchor: str = "",
    source_excerpt: str = "",
    answer_basis: str = "",
    answer_rubric: dict[str, str] | None = None,
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
        f"A good answer should include: {answer_anchor}" if answer_anchor else None,
        f"Source excerpt: {source_excerpt}" if source_excerpt else None,
    ]
    context.extend(_grounding_context(answer_basis, answer_rubric))
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
        purpose="question",
    )
    question = str(data.get("question", "")).strip()
    if not question:
        raise LLMError("question generation returned an empty question")
    return question


def _turn_state_lines(probes: Sequence[tuple[str, str]]) -> list[str]:
    """Tell the model where in the session it is, in both contracts.

    Two lines rather than one: the count drives which preface the probe takes, and
    the explicit final-turn flag means the model never has to do the arithmetic. It
    is told the cap, but is not trusted with it — the parsers enforce it regardless
    of what comes back.
    """
    return [
        f"SCORED FOLLOW-UPS USED: {len(probes)} of {MAX_SCORED_FOLLOW_UPS}",
        f"FINAL SCORED TURN: {'yes' if len(probes) == MAX_SCORED_FOLLOW_UPS else 'no'}",
    ]


def _probe_transcript_lines(probes: Sequence[tuple[str, str]]) -> list[str]:
    """Flatten ordered probe pairs into the transcript shared by both contracts."""
    return [
        line
        for question, answer in probes
        for line in (f"FOLLOW-UP: {question}", f"ANSWER: {answer}")
    ]


def build_score_answer_completion(
    *,
    model: str,
    effort: str | None,
    topic: str,
    mastery_summary: str,
    question_asked: str,
    answer_text: str,
    probes: Sequence[tuple[str, str]],
    answer_anchor: str = "",
    source_excerpt: str = "",
    answer_basis: str = "",
    answer_rubric: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Prepare the scoring call without sending it.

    Production scoring and paid-evaluation token preflight deliberately share this
    builder so model, prompt, schema, and effort cannot drift.

    ``probes`` is the ordered (question, answer) pairs of the scored follow-ups
    taken so far — empty on the initial answer, and when the learner is answering
    probe *k* the k-th pair carries the text they just submitted.
    """
    transcript = [
        f"Topic: {topic}",
        f"Rolling mastery summary: {mastery_summary}" if mastery_summary else None,
        f"A good answer should include: {answer_anchor}" if answer_anchor else None,
        f"Source excerpt: {source_excerpt}" if source_excerpt else None,
        *_grounding_context(answer_basis, answer_rubric),
        *_turn_state_lines(probes),
        "",
        f"QUESTION: {question_asked}",
        f"ANSWER: {answer_text}",
        *_probe_transcript_lines(probes),
    ]

    return {
        "model": model,
        "effort": effort,
        "rubric": SCORING_RUBRIC,
        "user_content": "\n".join(t for t in transcript if t is not None),
        "schema": SCORE_SCHEMA,
        "max_tokens": 8000,
    }


def build_score_v2_completion(
    *,
    model: str,
    effort: str | None,
    topic: str,
    mastery_summary: str,
    question_asked: str,
    answer_text: str,
    probes: Sequence[tuple[str, str]],
    answer_anchor: str = "",
    source_excerpt: str = "",
    answer_basis: str = "",
    answer_rubric: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Prepare the single-signal V2 call without sending it.

    ``probes`` carries the same ordered (question, answer) pairs as the V1 builder.
    """
    transcript = [
        f"Topic: {topic}",
        f"Rolling Recall summary: {mastery_summary}" if mastery_summary else None,
        f"A good answer should include: {answer_anchor}" if answer_anchor else None,
        f"Source excerpt: {source_excerpt}" if source_excerpt else None,
        *_grounding_context(answer_basis, answer_rubric, v2_aliases=True),
        *_turn_state_lines(probes),
        "",
        f"QUESTION: {question_asked}",
        f"ANSWER: {answer_text}",
        *_probe_transcript_lines(probes),
    ]

    return {
        "model": model,
        "effort": effort,
        "rubric": SCORING_V2_RUBRIC,
        "user_content": "\n".join(t for t in transcript if t is not None),
        "schema": SCORE_V2_SCHEMA,
        "max_tokens": 2048,
        "retry": False,
    }


async def score_answer(
    *,
    topic: str,
    mastery_summary: str,
    question_asked: str,
    answer_text: str,
    probes: Sequence[tuple[str, str]],
    answer_anchor: str = "",
    source_excerpt: str = "",
    answer_basis: str = "",
    answer_rubric: dict[str, str] | None = None,
    scoring_contract_version: int = SCORING_CONTRACT_V1,
) -> ScoreResult:
    """Score the session so far, or return a probe if the answer was partial."""
    settings = get_settings()
    probes_used = len(probes)
    builder = (
        build_score_v2_completion
        if scoring_contract_version == SCORING_CONTRACT_V2
        else build_score_answer_completion
    )
    completion = builder(
        model=settings.scoring_model,
        effort=settings.scoring_effort,
        topic=topic,
        mastery_summary=mastery_summary,
        question_asked=question_asked,
        answer_text=answer_text,
        probes=probes,
        answer_anchor=answer_anchor,
        source_excerpt=source_excerpt,
        answer_basis=answer_basis,
        answer_rubric=answer_rubric,
    )
    purpose = (
        "score_v2" if scoring_contract_version == SCORING_CONTRACT_V2 else "score_v1"
    )
    data = await _complete(**completion, purpose=purpose)

    try:
        result = (
            parse_score_v2_result(data, probes_used=probes_used)
            if scoring_contract_version == SCORING_CONTRACT_V2
            else parse_score_result(data, probes_used=probes_used)
        )
    except LLMError:
        log.warning("llm purpose=%s event=invalid_contract", purpose)
        raise
    log.info("llm purpose=%s event=scoring_outcome status=%s", purpose, result.status)
    return result


def _should_probe(score: int, probes_used: int, needs_more_evidence: bool) -> bool:
    """The shared band/insufficiency/cap policy for both scoring contracts."""
    if probes_used == 0:
        return FOLLOW_UP_LOW <= score <= FOLLOW_UP_HIGH
    return probes_used < MAX_SCORED_FOLLOW_UPS and needs_more_evidence


def parse_score_result(data: dict[str, Any], *, probes_used: int) -> ScoreResult:
    """Apply the production scoring contract to provider-structured data.

    Tolerant by design, in both directions: a probe the policy did not ask for is
    dropped, and a missing probe completes the session rather than failing it. The
    live contract cannot afford to lose a spoken answer to a model that returned
    the wrong shape.
    """

    # The JSON schema makes all three axes required, so this should be unreachable —
    # but an unguarded KeyError/ValueError here is a 500, and the client only knows
    # how to retry a 503. Losing a spoken answer is the worst failure mode in the
    # product.
    try:
        accuracy, depth, boundaries = (int(data[axis]) for axis in AXES)
    except (KeyError, TypeError, ValueError) as exc:
        raise LLMError(f"scoring response had no usable axis scores: {data!r}") from exc

    score = derive_composite(accuracy, depth, boundaries)
    probe = str(data.get("follow_up_question", "")).strip()

    # On turn 1 the band decides, so a stray insufficiency claim cannot widen it.
    # Past the first, only the model's claim earns another turn, never at the cap.
    should_probe = _should_probe(
        score, probes_used, data.get("needs_more_evidence") is True
    )

    if should_probe and probe:
        return ScoreResult(status="follow_up", follow_up_question=probe)

    return ScoreResult(
        status="complete",
        score=score,
        accuracy=accuracy,
        depth=depth,
        boundaries=boundaries,
        feedback=str(data.get("feedback", "")).strip(),
        mastery_summary=clean_summary(str(data.get("mastery_summary", ""))),
    )


def parse_score_v2_result(data: dict[str, Any], *, probes_used: int) -> ScoreResult:
    """Apply the Recall-only V2 contract to provider-structured data.

    Fail-closed, unlike V1: every departure from the follow-up policy is an
    LLMError rather than a silent coercion, because V2 is still dark and a
    tolerated violation would be invisible until activation.
    """
    try:
        recall = int(data["recall_score"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LLMError(f"V2 scoring response had no usable Recall score: {data!r}") from exc
    if recall not in range(6):
        raise LLMError(f"V2 scoring response had out-of-range Recall: {recall}")

    try:
        needs = data["needs_more_evidence"]
    except KeyError as exc:
        raise LLMError(
            f"V2 scoring response omitted needs_more_evidence: {data!r}"
        ) from exc
    if not isinstance(needs, bool):
        raise LLMError(f"V2 scoring response had non-boolean needs_more_evidence: {needs!r}")

    probe = str(data.get("follow_up_question", "")).strip()
    should_probe = _should_probe(recall, probes_used, needs)
    # An insufficiency claim has to carry the probe that would settle it. This is
    # also what rejects `needs_more_evidence` at the cap, where no probe is legal.
    if needs is True and not probe:
        raise LLMError("V2 scoring response claimed missing evidence without a probe")
    if bool(probe) != should_probe:
        raise LLMError(
            "V2 scoring response violated the Recall follow-up policy: "
            f"probe={'present' if probe else 'absent'} at probes_used={probes_used}"
        )
    if should_probe:
        return ScoreResult(
            status="follow_up",
            follow_up_question=probe,
            scoring_contract_version=SCORING_CONTRACT_V2,
        )

    feedback = str(data.get("feedback", "")).strip()
    mastery_summary = clean_summary(str(data.get("mastery_summary", "")))
    if not feedback or not mastery_summary:
        raise LLMError("V2 scoring response omitted required completion text")
    return ScoreResult(
        status="complete",
        score=recall,
        accuracy=recall,
        feedback=feedback,
        mastery_summary=mastery_summary,
        scoring_contract_version=SCORING_CONTRACT_V2,
    )


def build_coaching_completion(
    *,
    model: str,
    effort: str | None,
    topic: str,
    focus: str,
    question: str,
    answer: str,
    answer_basis: str = "",
    answer_rubric: dict[str, str] | None = None,
) -> dict[str, Any]:
    transcript = [
        f"Topic: {topic}",
        f"Qualitative focus: {focus}",
        *_grounding_context(answer_basis, answer_rubric, v2_aliases=True),
        "",
        f"COACHING QUESTION: {question}",
        f"LEARNER ANSWER: {answer}",
    ]
    return {
        "model": model,
        "effort": effort,
        "rubric": COACHING_RUBRIC,
        "user_content": "\n".join(transcript),
        "schema": COACHING_SCHEMA,
        "max_tokens": 512,
        "retry": False,
    }


async def coach_answer(
    *,
    topic: str,
    focus: str,
    question: str,
    answer: str,
    answer_basis: str = "",
    answer_rubric: dict[str, str] | None = None,
) -> CoachingResult:
    settings = get_settings()
    data = await _complete(
        purpose="coaching_v2",
        **build_coaching_completion(
            model=settings.scoring_model,
            effort=settings.scoring_effort,
            topic=topic,
            focus=focus,
            question=question,
            answer=answer,
            answer_basis=answer_basis,
            answer_rubric=answer_rubric,
        )
    )
    feedback = str(data.get("coaching_feedback", "")).strip()
    if not feedback:
        log.warning("llm purpose=coaching_v2 event=invalid_contract")
        raise LLMError("qualitative coaching returned empty feedback")
    return CoachingResult(feedback=feedback)


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
        purpose="guide_import",
    )


async def propose_cards(
    *,
    subject: str,
    item_title: str,
    why_it_matters: str,
    done_when: str,
    source_excerpt: str,
    existing_weak_topics: list[str],
    observed_gap: str = "",
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
        f"Trusted answer basis from the guide: {source_excerpt}" if source_excerpt else None,
        (
            "Observed practice gap (use only to choose what to test; it is not an "
            f"answer source): {observed_gap}"
            if observed_gap
            else None
        ),
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
        purpose="card_proposal",
    )
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        raise LLMError(f"card proposal response had no candidate list: {data!r}")
    return [c for c in candidates if isinstance(c, dict)]


def build_reattempt_completion(
    *,
    model: str,
    effort: str | None,
    topic: str,
    question_asked: str,
    feedback_given: str,
    reattempt_answer: str,
    unaided_accuracy: int,
    answer_basis: str = "",
    answer_rubric: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Prepare the coached re-attempt call without sending it."""
    transcript = [
        f"Topic: {topic}",
        *_grounding_context(answer_basis, answer_rubric),
        "",
        f"QUESTION: {question_asked}",
        f"UNAIDED ACCURACY SCORE, BEFORE THEY WERE TOLD: {unaided_accuracy}/5",
        f"CORRECT ESSENTIAL ACCOUNT, AS STATED TO THEM: {feedback_given}",
        f"THEIR RE-ATTEMPT: {reattempt_answer}",
    ]
    return {
        "model": model,
        "effort": effort,
        "rubric": REATTEMPT_RUBRIC,
        "user_content": "\n".join(transcript),
        "schema": REATTEMPT_SCHEMA,
        # One enum integer and two sentences — well under 100 output tokens. Sized to
        # bound the worst case, not the expected one: a degenerate generation on a
        # turn the user is waiting through should fail fast, not run for 4000 tokens.
        "max_tokens": 512,
    }


async def score_reattempt(
    *,
    topic: str,
    question_asked: str,
    feedback_given: str,
    reattempt_answer: str,
    unaided_accuracy: int,
    answer_basis: str = "",
    answer_rubric: dict[str, str] | None = None,
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
    completion = build_reattempt_completion(
        model=settings.reattempt_model,
        effort=settings.reattempt_effort,
        topic=topic,
        question_asked=question_asked,
        feedback_given=feedback_given,
        reattempt_answer=reattempt_answer,
        unaided_accuracy=unaided_accuracy,
        answer_basis=answer_basis,
        answer_rubric=answer_rubric,
    )
    data = await _complete(**completion, purpose="reattempt")

    try:
        return parse_reattempt_result(data)
    except LLMError:
        log.warning("llm purpose=reattempt event=invalid_contract")
        raise


def parse_reattempt_result(data: dict[str, Any]) -> ReattemptResult:
    """Apply the production coached-grade contract to provider-structured data."""

    try:
        accuracy = int(data["accuracy"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LLMError(f"re-attempt response had no usable score: {data!r}") from exc

    return ReattemptResult(
        accuracy=accuracy,
        mastery_summary=clean_summary(str(data.get("mastery_summary", ""))),
    )
