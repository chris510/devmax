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
EXPLICIT_EVIDENCE_V3 = "explicit-evidence-v3"
EXPLICIT_EVIDENCE_V4 = "explicit-evidence-v4"
EXPLICIT_EVIDENCE_V5 = "explicit-evidence-v5"
EXPLICIT_EVIDENCE_V6 = "explicit-evidence-v6"
EXPLICIT_EVIDENCE_V7 = "explicit-evidence-v7"

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

EXPLICIT_EVIDENCE_V3_RULES = """\

EVALUATION CANDIDATE — BIDIRECTIONAL EVIDENCE ELIGIBILITY V3

Keep the production Accuracy rules. For Depth and Boundaries, silently assign an
eligibility band before choosing the score. Return only the production schema.

EVIDENCE SOURCE
Only text after `ANSWER:` labels is learner evidence, including an answer after a
`FOLLOW-UP:`. Topic, questions, mastery summary, trusted answer basis, approved
rubric, and feedback are authority or context, not learner claims. A relationship
qualifies only when it is relevant and correct under the trusted material.

DEPTH — TRADE-OFF AWARENESS ONLY
A qualifying Depth relationship explicitly connects a learner-stated choice,
target, or approach to its cost, sacrificed property, tension, or opposing benefit.

  - No qualifying relationship: Depth MUST be 0-2.
  - One or more qualifying relationships: Depth MUST be 3-5.

BOUNDARIES — FAILURE-MODE AWARENESS ONLY
A qualifying Boundaries relationship explicitly connects a learner-stated trigger,
action, exception, limitation, or mistaken belief to a concrete adverse outcome or
incorrect behavior.

  - No qualifying relationship: Boundaries MUST be 0-2.
  - One or more qualifying relationships: Boundaries MUST be 3-5.

The two bands are mandatory and bidirectional. Once a correct explicit relationship
exists, missing specificity, examples, numerical estimates, or additional
relationships may lower the score within 3-5 but MUST NOT lower it to 0-2. Missing
mechanism detail affects Accuracy or completeness within an eligible 3-5 band; it
does not erase an independently stated trade-off or failure relationship.

Do not infer the missing side of a relationship. A target, priority, recommendation,
selection rule, goal, technique, check, guardrail, bare negation, or statement that
information is irrelevant does not qualify by itself. Never reverse "do X" into
"not doing X causes Y" unless the learner states Y. Merely saying a detail does not
drive the design is not a concrete adverse outcome.

CALIBRATION
  - "only keep the few numbers that drive the design" has no adverse outcome:
    Boundaries MUST be 0-2.
  - "using a fresh retry key can duplicate the charge" states action and harm:
    Boundaries MUST be 3-5.
  - "target p95 under 300 ms" has no cost or tension: Depth MUST be 0-2.
  - "skipping unrelated arithmetic saves interview time, but real capacity limits
    still need attention" states a choice, benefit, and tension: Depth MUST be 3-5,
    even without a numerical estimate.

FINAL CONSISTENCY CHECK
Score the axes before writing feedback. If feedback acknowledges or paraphrases a
qualifying learner relationship, that axis MUST be 3-5. If feedback supplies the
missing relationship as correction, that axis MUST be 0-2. Feedback, context, or
the approved rubric can never retroactively change the learner's eligibility band.

Keep all other production rules, schema fields, transcript handling, and feedback
behavior unchanged.\
"""

EXPLICIT_EVIDENCE_V4_RULES = EXPLICIT_EVIDENCE_V3_RULES + """\

EVALUATION CANDIDATE — AXIS INDEPENDENCE AND TWO-ENDPOINT EVIDENCE V4

Apply after V3. First choose and freeze Accuracy from only the correctness and
completeness of the essential mechanism. Then score Depth and Boundaries. Missing
cost, tension, trigger, or harm MUST NOT lower frozen Accuracy. Operation-specific
latency, load, availability, and staleness constraints can fully answer how vague
qualities become architectural constraints without secondary evidence. A generic
maximize-every-quality list remains inaccurate.

Before returning a secondary axis at 3-5, silently fill both brackets using only
learner words:

  - Depth: [choice/target/approach] trades against [cost/sacrifice/tension/benefit].
  - Boundaries: [trigger/action/exception/limitation/mistake] causes [concrete harm
    or incorrect behavior].

Both endpoints and their connection MUST appear in learner `ANSWER:` text; otherwise
the axis MUST be 0-2. "Ignore/reject/do not trust client-supplied identity" is only a
guardrail, not a stated harm. "Trusting the body ID lets one caller act as another"
states both endpoints.

For every secondary axis at 3-5, feedback must paraphrase both learner-stated
endpoints. If it cannot, lower the axis to 0-2. Feedback is never evidence.

Keep every other V3 and production rule unchanged.\
"""

EXPLICIT_EVIDENCE_V5_RULES = EXPLICIT_EVIDENCE_V4_RULES + """\

EVALUATION CANDIDATE — WITHIN-BAND SECONDARY CALIBRATION V5

After V4 establishes 3-5 eligibility:

  - 3 = correct but materially vague or incomplete in an endpoint or connection.
  - 4 = clear and complete, with a minor omission.
  - 5 = fully states the approved named relationship.

One complete relationship is enough; never require extra examples, numbers, or
multiple relationships. Missing mechanism or other-secondary evidence cannot lower
this axis. If feedback calls the named relationship explicit or complete and
criticizes only another axis, this axis MUST be 4-5. The choice between saving
interview time and still noticing real capacity limits is Depth 4-5 without numbers.

Keep every V4 and production rule unchanged.\
"""

EXPLICIT_EVIDENCE_V6_RULES = """\

EVALUATION CANDIDATE — UNIFIED AXIS CONTRACT V6

Use this single ordered procedure. Return only the production schema.

EVIDENCE SOURCE
Only learner text after `ANSWER:` labels is score evidence, including a follow-up
answer. Questions, topic, mastery summary, trusted basis, approved rubric, and
feedback are authority or context, never learner claims.

1. ACCURACY — SCORE AND FREEZE
Grade only correctness and completeness of the essential mechanism, then freeze
Accuracy. Missing trade-off or failure evidence MUST NOT lower it. If feedback says
the mechanism is correct and criticizes only a missing cost, trigger, or harm, that
criticism belongs only to Depth or Boundaries.

2. DEPTH — TRADE-OFF RELATIONSHIP
Using learner words, identify [choice/target/approach] connected to
[cost/sacrifice/tension/opposing benefit]. If either endpoint or their connection is
absent or incorrect, Depth MUST be 0-2. If both are correct and explicit, Depth MUST
be 3-5.

3. BOUNDARIES — FAILURE RELATIONSHIP
Using learner words, identify [trigger/action/exception/limitation/mistake] connected
to [concrete harm or incorrect behavior]. If either endpoint or their connection is
absent or incorrect, Boundaries MUST be 0-2. If both are correct and explicit,
Boundaries MUST be 3-5. A guardrail, prescription, or bare negation states no harm by
itself; never reverse "do X" into an unstated failure.

4. CALIBRATE AN ELIGIBLE SECONDARY AXIS
  - 3 = correct but materially vague or incomplete in an endpoint or connection.
  - 4 = clear and complete, with a minor omission.
  - 5 = fully states the approved named relationship.
One complete relationship is enough; never require extra examples, numbers, or
multiple relationships. Missing mechanism or other-axis evidence cannot lower it.

5. FINAL CONSISTENCY CHECK
Feedback for a 3-5 secondary axis must paraphrase both learner-stated endpoints. If
feedback supplies a missing endpoint, that axis MUST be 0-2. If feedback calls the
full named relationship explicit or complete, that axis MUST be 4-5. Feedback cannot
change frozen Accuracy.

CALIBRATION
  - Operation-specific latency at expected load, availability, and staleness
    constraints fully answer how vague qualities become architectural constraints:
    Accuracy MUST be 4-5 even without secondary evidence. A generic maximize-every-
    quality checklist remains inaccurate.
  - "Ignore the body ID as identity evidence" is a guardrail without a stated harm:
    Boundaries MUST be 0-2. "Trusting the body ID lets one caller act as another"
    states action and harm: Boundaries MUST be 4-5.
  - Skipping unrelated arithmetic to save interview time while still watching real
    capacity limits states the full trade-off: Depth MUST be 4-5 without numbers.

Keep all other production schema, transcript, and feedback rules unchanged.\
"""

EXPLICIT_EVIDENCE_V7_RULES = """\

EVALUATION CANDIDATE — UNIFIED AXIS CONTRACT V7

Use this single ordered procedure. Return only the production schema.

EVIDENCE SOURCE
Only learner text after `ANSWER:` labels is score evidence, including a follow-up
answer. Questions, topic, mastery summary, trusted basis, approved rubric, and
feedback are authority or context, never learner claims.

1. ACCURACY — SCORE AND FREEZE
Grade only correctness and completeness of the essential mechanism, then freeze
Accuracy. Missing trade-off or failure evidence MUST NOT lower it. If feedback says
the mechanism is correct and criticizes only a missing cost, trigger, or harm, that
criticism belongs only to Depth or Boundaries.

2. DEPTH — TRADE-OFF RELATIONSHIP
Using learner words, identify [choice/target/approach] connected to
[cost/sacrifice/tension/opposing benefit]. If either endpoint or their connection is
absent or incorrect, Depth MUST be 0-2. If both are correct and explicit, Depth MUST
be 3-5.

3. BOUNDARIES — FAILURE RELATIONSHIP
Using learner words, identify [trigger/action/exception/limitation/mistake] connected
to [concrete harm or incorrect behavior]. If either endpoint or their connection is
absent or incorrect, Boundaries MUST be 0-2. If both are correct and explicit,
Boundaries MUST be 3-5. A guardrail, prescription, or bare negation states no harm by
itself; never reverse "do X" into an unstated failure.

Selection logic is not failure evidence. An option or capacity branch ("if A fits,
use A; otherwise B") MUST stay Boundaries 0-2 unless the learner separately connects
a wrong action, condition, or belief to concrete harm or incorrect behavior.

4. CALIBRATE AN ELIGIBLE SECONDARY AXIS
  - 3 = correct but materially vague or incomplete in an endpoint or connection.
  - 4 = clear and complete, with a minor omission.
  - 5 = fully states the approved named relationship.
One complete relationship is enough; never require extra examples, numbers, or
multiple relationships. Missing mechanism or other-axis evidence cannot lower it.

5. FINAL CONSISTENCY CHECK
Feedback for a 3-5 secondary axis must paraphrase both learner-stated endpoints. If
feedback supplies a missing endpoint, that axis MUST be 0-2. If feedback calls the
full named relationship explicit or complete, that axis MUST be 4-5. Feedback cannot
change frozen Accuracy.

For Boundaries 3-5, feedback MUST paraphrase both trigger and harm. If it describes
only selection logic or identifies no failure, lower Boundaries to 0-2.

CALIBRATION
  - Operation-specific latency at expected load, availability, and staleness
    constraints fully answer how vague qualities become architectural constraints:
    Accuracy MUST be 4-5 even without secondary evidence. A generic maximize-every-
    quality checklist remains inaccurate.
  - "Ignore the body ID as identity evidence" is a guardrail without a stated harm:
    Boundaries MUST be 0-2. "Trusting the body ID lets one caller act as another"
    states action and harm: Boundaries MUST be 4-5.
  - "If one heap fits, keep it; otherwise shard" is selection logic: Boundaries 0-2.
    "Start with DAU—no, it cannot decide this branch" links a mistake to incorrect
    behavior: Boundaries 3-5. "Save time but watch capacity" is Depth 4-5 and
    Boundaries 0-2.

Keep all other production schema, transcript, and feedback rules unchanged.\
"""

PROMPT_OVERLAYS = {
    PRODUCTION: "",
    EXPLICIT_EVIDENCE_V1: EXPLICIT_EVIDENCE_RULES,
    EXPLICIT_EVIDENCE_V2: EXPLICIT_EVIDENCE_V2_RULES,
    EXPLICIT_EVIDENCE_V3: EXPLICIT_EVIDENCE_V3_RULES,
    EXPLICIT_EVIDENCE_V4: EXPLICIT_EVIDENCE_V4_RULES,
    EXPLICIT_EVIDENCE_V5: EXPLICIT_EVIDENCE_V5_RULES,
    EXPLICIT_EVIDENCE_V6: EXPLICIT_EVIDENCE_V6_RULES,
    EXPLICIT_EVIDENCE_V7: EXPLICIT_EVIDENCE_V7_RULES,
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
