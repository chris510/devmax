# Claude explicit-evidence V6 follow-up and repeatability results

**Run date:** 2026-08-09 (America/Los_Angeles)

**Decision:** Stopped at H1; repeatability replicas not run

## Outcome

The four-case follow-up stage made its approved paid calls and then failed the
automatic reviewed gate. Delivery sequence, timeout retry, and cursor
pagination passed. Decision-driven estimation returned Boundaries 4 against
reviewed 2 and composite 5 against reviewed 3.

The response's own feedback praised the mechanism, said the trade-off was
missing, and identified no learner-stated failure mode or harm. Boundaries 4 is
therefore unsupported by the learner evidence and inconsistent with the
feedback. The manual audit independently fails the same response.

The approved stop policy was followed. R1 and R2 made no calls, repeatability
was not measured, and production remained unchanged.

| Control | Value |
| --- | --- |
| Prompt | Production rubric plus evaluation-only `explicit-evidence-v6` |
| Cumulative authorized cap | **$0.113600** |
| Paid calls | **4 of 10 maximum** |
| Calculated spend | **$0.040534** |
| Unspent authorization | **$0.073066** |
| Production change | None |

## Stage ledger

| Stage | Calls | Input | Output | Actual cost | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| H1 — remaining follow-ups | 4 | 12,452 | 1,563 | $0.040534 | **Fail; stopped** |
| R1 — blocker replica 1 | 0 | 0 | 0 | $0.000000 | Not run |
| R2 — blocker replica 2 | 0 | 0 | 0 | $0.000000 | Not run |
| **Total** | **4** | **12,452** | **1,563** | **$0.040534** | **Stopped** |

Cost uses the published promotional schedule through 2026-08-31: $2 per
million input tokens and $10 per million output tokens. No cache tokens were
read or written.

## Automatic gate result

| Criterion | H1 result | Gate |
| --- | ---: | --- |
| Structured responses parsed | 4/4 | Pass |
| False Accuracy passes / failures | 0 / 0 | Pass safety boundary |
| Composite exact / within one | 3/4 · **3/4** | **Fail** |
| Mean composite deviation | 0.50 | Fail from one two-point miss |
| Accuracy exact / within one | 0/4 · **4/4** | Pass |
| Depth exact / within one | 2/4 · **4/4** | Pass |
| Boundaries exact / within one | 2/4 · **3/4** | **Fail** |
| Feedback/evidence audit | **3/4 consistent** | **Fail** |

## Case matrix

| Case | Reviewed axes | V6 axes | Composite | Audit |
| --- | --- | --- | --- | --- |
| Delivery sequence — follow-up ordering | 5 / 1 / 1 | 4 / 2 / 1 | 3 → 3 | Pass. Mechanism counted; unsupported secondary evidence stayed low. |
| Timeout retry — follow-up idempotency | 5 / 2 / 2 | 4 / 2 / 2 | 3 → 3 | Pass. Unknown outcome and same-key handling counted without promoting brief secondary mentions. |
| Cursor pagination — follow-up boundary | 5 / 1 / 2 | 4 / 1 / 1 | 3 → 3 | Pass. Ordered-key mechanism counted; absent trade-off and harm stayed low. |
| Decision-driven estimation — follow-up decision | 5 / 1 / 2 | 4 / 2 / 4 | 3 → 5 | **Fail.** The answer states a capacity threshold and design branch but no harmful outcome; feedback likewise identifies no failure mode. |

## Failure analysis

The decision-driven follow-up says to keep one heap if it can hold the topic
cardinality and shard if it cannot. That is the mechanism's option-selection
threshold. It does not state what harmful behavior results from choosing or
operating the wrong design.

V6 requires both a learner-stated action or condition and its learner-stated
harm before Boundaries may enter the 3-5 band. The returned Boundaries 4 implies
that the model treated the capacity comparison or conditional branch itself as
a failure-mode relationship. This is an inference from the score because the
feedback never explains the high Boundary rating. The score and feedback are
therefore contradictory even under the model's own narrative.

This is not evidence to relabel the approved case. It is evidence that V6's
general two-endpoint rule still needs a decision-threshold calibration. A
decision branch answers “which design should I choose?”; a failure mode answers
“what goes wrong, and how?” Those concepts may use the same conditional syntax
but measure different axes.

## Durable failure proof

A separate replay loaded the H1 JSONL with the Anthropic key unset. It resumed
4/4 exact fingerprints, scheduled zero paid calls, printed a $0.0000 new-call
ceiling, and reproduced both reviewed-gate failures:

- decision-driven composite 5 is more than one from reviewed 3; and
- decision-driven Boundaries 4 is more than one from reviewed 2.

The failure is therefore durably attributable to the recorded response rather
than a second sample.

## Decision and next experiment

Keep V6 evaluation-only. Do not run R1, R2, the remaining release families, or
the frozen baseline, and do not rerun H1 unchanged.

The next cost-efficient candidate should be a small V7 calibration that:

1. states that an option-selection threshold, capacity comparison, or
   conditional design branch is mechanism evidence, not failure-mode evidence,
   unless the learner also states a resulting harmful behavior;
2. makes the final consistency check force Boundaries into 0-2 when feedback
   identifies no learner-stated failure mode; and
3. protects both the decision-driven trade-off-only low-Boundaries control and
   a reviewed positive failure-boundary control.

Preparation should begin with shared Claude/OpenAI prompt tests and free token
counts. Its first paid stage should be the smallest paired negative/positive
Boundary gate. Only a pass should reopen the four-case follow-up stage and the
deferred repeatability replicas.

That preparation is now specified in
`EXPLICIT-EVIDENCE-V7-DECISION-BOUNDARY-GATE.md`. It combines the focused
negative/positive Boundary controls with a first V7 observation of every
historical blocker, then conditionally completes follow-up coverage and two fresh
repeatability observations. No paid calls were made during preparation.
