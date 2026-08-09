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
EXPLICIT_EVIDENCE_V2 = "explicit-evidence-v2"

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

EXPLICIT_EVIDENCE_V2_RULES = """\

EVALUATION CANDIDATE — EXPLICIT EVIDENCE ELIGIBILITY V2

For this candidate, grade the product's three axes in this order. Perform the
eligibility checks silently; return only the production schema fields.

1. ACCURACY — MECHANISM ACCURACY
Grade whether the learner's essential account is correct. Keep the production
Accuracy rules unchanged.

2. DEPTH — TRADE-OFF AWARENESS ONLY
Before choosing a Depth score, locate words in the learner's `ANSWER:` text that
explicitly connect a choice, target, or approach to its cost, sacrificed property,
tension, or opposing benefit.

  - If no such learner-stated connection exists, Depth is ineligible for 3-5 and
    must be 0-2.
  - A target, priority, quality, technique, implementation step, or statement that
    something matters is not a trade-off by itself.
  - Do not supply the missing cost from the trusted rubric, context, implication,
    or feedback and then credit the learner for it.

3. BOUNDARIES — FAILURE-MODE AWARENESS ONLY
Before choosing a Boundaries score, locate words in the learner's `ANSWER:` text
that explicitly connect a triggering condition, action, exception, limitation, or
mistaken belief to a concrete adverse outcome or incorrect behavior.

  - If no such learner-stated connection exists, Boundaries is ineligible for 3-5
    and must be 0-2.
  - A recommendation, priority, selection rule, goal, constraint, check, guardrail,
    bare negation, or statement that information is irrelevant is not failure-mode
    evidence by itself.
  - Never reverse a prescription into an unstated failure. From "do X," do not infer
    "not doing X causes Y" unless the learner also states the adverse Y.
  - Merely saying a detail does not affect or drive the design is not a concrete
    adverse outcome.
  - Do not supply the missing trigger or harm from the trusted rubric, context,
    implication, or feedback and then credit the learner for it.

CALIBRATION
  - "only keep the few numbers that drive the design" gives no Boundaries eligibility:
    it is a selection rule and states no adverse outcome. Boundaries must be 0-2.
  - "using a fresh retry key can duplicate the charge" is eligible Boundaries
    evidence: it states both the action and adverse outcome.
  - "target p95 under 300 ms" gives no Depth eligibility: it states a target but no
    cost or tension. Depth must be 0-2.
  - "a tighter latency target needs more replication and cost" is eligible Depth
    evidence: it states the target/cost relationship.

EVIDENCE SOURCE AND FINAL CHECK
Only text after `ANSWER:` labels is learner evidence, including the answer after a
`FOLLOW-UP:` when present. Topic, questions, mastery summary, trusted answer basis,
approved rubric, and model-written feedback are authority or context, never learner
claims. Apply the hard 0-2 ceilings even when a missing relationship seems obvious.

Choose 3-5 only after its eligibility check passes, using the production scale to
distinguish completeness. Before returning, verify that feedback never introduces
the evidence used to justify a 3-5 axis. If feedback supplies a missing trade-off,
Depth must be 0-2. If feedback supplies a missing trigger or adverse outcome,
Boundaries must be 0-2.

Keep all other production rules, schema fields, transcript handling, and feedback
behavior unchanged.\
"""

PROMPT_OVERLAYS = {
    PRODUCTION: "",
    EXPLICIT_EVIDENCE_V1: EXPLICIT_EVIDENCE_RULES,
    EXPLICIT_EVIDENCE_V2: EXPLICIT_EVIDENCE_V2_RULES,
}
SCORING_PROMPT_VARIANTS = tuple(PROMPT_OVERLAYS)


def apply_scoring_prompt_variant(
    completion: dict[str, Any], variant: str
) -> dict[str, Any]:
    """Return one prepared completion with an evaluation-only rubric overlay."""
    try:
        overlay = PROMPT_OVERLAYS[variant]
    except KeyError as exc:
        raise ValueError(f"unknown scoring prompt variant: {variant}") from exc
    return {
        **completion,
        "rubric": completion["rubric"] + overlay,
    }
