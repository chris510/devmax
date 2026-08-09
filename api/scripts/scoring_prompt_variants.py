"""Versioned scoring-prompt candidates for paid evaluation only.

Production continues to use ``llm.SCORING_RUBRIC`` unchanged. A candidate is
applied to the prepared completion shared by the Claude and OpenAI runners, so
both providers receive byte-identical grading instructions and resume
fingerprints cannot cross prompt variants.
"""

from __future__ import annotations

from typing import Any

PRODUCTION = "production"
EXPLICIT_EVIDENCE_V1 = "explicit-evidence-v1"
SCORING_PROMPT_VARIANTS = (PRODUCTION, EXPLICIT_EVIDENCE_V1)

EXPLICIT_EVIDENCE_RULES = """\

EVALUATION CANDIDATE — EXPLICIT EVIDENCE ATTRIBUTION V1

For this candidate, enforce the product's three-axis meanings exactly:

  accuracy — mechanism accuracy: whether the learner's essential account is correct.
  depth — trade-off awareness only: whether the learner explicitly explains a cost,
    benefit, tension, or consequence of choosing the approach. Restating mechanism
    steps, naming a technique, or merely saying a quality matters is not trade-off
    evidence. Award 3-5 only when that trade-off evidence appears in the learner's
    answer.
  boundaries — failure-mode awareness only: whether the learner explicitly explains
    a relevant failure, exception, limitation, or misconception, including the
    condition or consequence that makes it matter. A bare "not X" or an adjacent term
    without the failure relationship is at most 2.

The only learner evidence is text after `ANSWER:` labels, including the answer after
`FOLLOW-UP:` when present. The topic, question, follow-up question, rolling mastery
summary, trusted answer basis, and approved rubric define context or authority; they
are not claims the learner made. Never infer an axis point from them and never award
credit for content that appears only in feedback you are about to write.

Run an axis/feedback consistency check before returning:
  - If feedback supplies a missing trade-off, depth must be 0-2.
  - If feedback supplies a missing failure, exception, limitation, or misconception,
    boundaries must be 0-2.
  - An axis at 3-5 requires quoting or precisely paraphrasing the learner evidence
    that earned it; do not raise the score based on what a strong answer could imply.

Keep all other production rules, schema fields, transcript handling, and feedback
behavior unchanged.\
"""


def apply_scoring_prompt_variant(
    completion: dict[str, Any], variant: str
) -> dict[str, Any]:
    """Return one prepared completion with an evaluation-only rubric overlay."""
    if variant == PRODUCTION:
        return dict(completion)
    if variant == EXPLICIT_EVIDENCE_V1:
        return {
            **completion,
            "rubric": completion["rubric"] + EXPLICIT_EVIDENCE_RULES,
        }
    raise ValueError(f"unknown scoring prompt variant: {variant}")
