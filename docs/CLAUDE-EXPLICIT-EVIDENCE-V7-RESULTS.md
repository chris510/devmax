# Claude explicit-evidence V7 decision-boundary results

**Run date:** 2026-08-10 (America/Los_Angeles)

**Decision:** Stopped at K1; later stages not run

## Outcome

V7 fixed the V6 decision-threshold Boundary failure. The decision follow-up
returned Boundaries 2 against reviewed 2, the explicit self-correction retained
Boundaries 4, and the trade-off-only control stayed at Boundaries 1.

The same focused stage exposed a symmetric Depth leak. Decision-driven
self-correction returned Depth 3 against reviewed 1 even though the learner
stated no cost, sacrifice, tension, or opposing benefit. The response's feedback
said the actual trade-off was not quantified and supplied missing examples such
as cross-node merge overhead. Under V7's evidence and final-consistency rules,
that feedback requires Depth 0-2 rather than 3.

K1 therefore failed both the automatic reviewed gate and manual evidence audit.
The approved stop policy was followed: K2, K3, and K4 made no calls,
repeatability was not measured, and production remained unchanged.

| Control | Value |
| --- | --- |
| Prompt | Production rubric plus evaluation-only `explicit-evidence-v7` |
| Cumulative authorized cap | **$0.164900** |
| Paid calls | **5 of 14 maximum** |
| Calculated spend | **$0.050376** |
| Unspent authorization | **$0.114524** |
| Production change | None |

## Stage ledger

| Stage | Calls | Input | Output | Actual cost | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| K1 — focused correction | 5 | 16,643 | 1,709 | $0.050376 | **Fail; stopped** |
| K2 — remaining follow-ups | 0 | 0 | 0 | $0.000000 | Not run |
| K3 — blocker observation 2 | 0 | 0 | 0 | $0.000000 | Not run |
| K4 — blocker observation 3 | 0 | 0 | 0 | $0.000000 | Not run |
| **Total** | **5** | **16,643** | **1,709** | **$0.050376** | **Stopped** |

Cost uses the published promotional schedule through 2026-08-31: $2 per
million input tokens and $10 per million output tokens. No cache tokens were
read or written.

## Automatic gate result

| Criterion | K1 result | Gate |
| --- | ---: | --- |
| Structured responses parsed | 5/5 | Pass |
| False Accuracy passes / failures | 0 / 0 | Pass safety boundary |
| Composite exact / within one | 5/5 · **5/5** | Pass |
| Mean composite deviation | 0.00 | Pass |
| Accuracy exact / within one | 3/5 · **5/5** | Pass |
| Depth exact / within one | 1/5 · **4/5** | **Fail** |
| Boundaries exact / within one | 4/5 · **5/5** | Pass |
| Feedback/evidence audit | **4/5 consistent** | **Fail** |

## Case matrix

| Case | Reviewed axes | V7 axes | Composite | Audit |
| --- | --- | --- | --- | --- |
| NFR follow-up constraints | 5 / 1 / 1 | 4 / 2 / 2 | 3 → 3 | Pass. Correct mechanism stayed independent; missing relationships stayed low. |
| Identity follow-up authorization | 5 / 1 / 2 | 5 / 1 / 2 | 3 → 3 | Pass. Exact axes; the guardrail did not become a failure mode. |
| Decision self-corrected | 5 / 1 / 4 | 5 / 3 / 4 | 5 → 5 | **Fail.** The option branch was promoted to Depth without a learner-stated cost or tension. |
| Decision follow-up decision | 5 / 1 / 2 | 4 / 2 / 2 | 3 → 3 | Pass. The original V6 Boundary leak was fixed. |
| Decision trade-off only | 5 / 5 / 1 | 5 / 4 / 1 | 4 → 4 | Pass. Explicit interview-time trade-off stayed high Depth and low Boundaries. |

## Failure analysis

The self-corrected answer explicitly rejects ritual DAU/QPS arithmetic because
it cannot decide the heap-versus-shard branch. That is valid misconception or
Boundary evidence. It then states the correct topic-cardinality mechanism and
the two design options. It never connects either option to a cost, sacrifice,
tension, or opposing benefit.

V7 explicitly said that selection logic is not failure evidence, which fixed
Boundaries. It did not state the symmetric calibration just as directly for
Depth. The returned Depth 3 implies that the model treated the presence of two
options as a trade-off relationship. This is an inference from the score, but
the feedback supports it: the model then supplied the missing cost of sharding
as a correction while leaving Depth in the 3-5 band.

This is not evidence to relabel the approved case. A choice between A and B is
mechanism selection. It becomes Depth only when the learner states what one
choice gains, costs, sacrifices, or trades against.

## Durable failure proof

A separate replay loaded the K1 JSONL with the Anthropic key unset. It resumed
5/5 exact fingerprints, scheduled zero paid calls, printed a $0.0000 new-call
ceiling, and reproduced the same reviewed-gate failure:

- decision-driven self-correction Depth 3 is more than one from reviewed 1.

The failure is therefore durably attributable to the recorded response rather
than a second sample.

## Decision and next experiment

Keep V7 evaluation-only. Do not run K2-K4, the release families, frozen
baseline, or provider comparison, and do not rerun K1 unchanged.

The next cost-efficient V8 candidate should make one symmetric rule explicit:
an option-selection or capacity branch is mechanism evidence by itself and is
neither Depth nor Boundaries evidence without the corresponding learner-stated
relationship. Its final check should force Depth into 0-2 whenever feedback
supplies the missing cost or says the actual trade-off was absent.

The first paid V8 stage should contain only the three decision-driven controls:

1. self-corrected — Depth 1, Boundaries 4;
2. follow-up supplies decision — Depth 1, Boundaries 2; and
3. trade-off only — Depth 5, Boundaries 1.

That smallest negative/positive matrix must pass before historical blockers or
remaining follow-up cases are reopened. Preparation should again begin with
shared Claude/OpenAI prompt tests, prompt-size audit, and free token counts.

That preparation is now specified in
`EXPLICIT-EVIDENCE-V8-SYMMETRIC-SELECTION-GATE.md`. V8 applies one symmetric
selection rule to both secondary axes, is 64 bytes shorter than V7, and has a
free-counted three-call maximum of $0.0352. No paid calls were made during
preparation.
