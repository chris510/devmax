# Claude explicit-evidence V2 B1

**Run date:** 2026-08-09 (America/Los_Angeles)

**Decision:** Passed; B2 remains separately gated

## What ran

B1 evaluated `explicit-evidence-v2` on the three approved `boundaries-only`
cases. A1 and A2 showed that V2 can withhold secondary credit when learner
evidence is absent. B1 tested the opposite failure risk: whether the hard
eligibility check suppresses genuine trigger-to-harm recall.

| Control | Value |
| --- | --- |
| Model | `claude-sonnet-5` |
| Effort | `low` |
| Cases | Three approved `boundaries-only` axis-isolation cases |
| Concurrency | 1 |
| Prompt | Production rubric plus evaluation-only `explicit-evidence-v2` |
| Production change | None |
| Preflight ceiling | **$0.0346** |
| Paid calls | **3** |
| Calculated cost | **$0.027502** |

The fresh free preflight counted 9,586 input tokens and reserved 1,536 output
tokens. The paid stage made exactly three Message calls.

## Gate result

| Criterion | Result | Gate |
| --- | ---: | --- |
| Structured responses parsed | 3/3 | Pass |
| False Accuracy passes / failures | 0 / 0 | Pass |
| Accuracy exact / within one | 2/3 · 3/3 | Pass safety boundary |
| Composite exact / within one | **3/3 · 3/3** | **Pass** |
| Mean composite deviation | 0.00 | Pass |
| Depth exact / within one | **3/3 · 3/3** | Pass withholding |
| Boundaries exact / within one | 1/3 · **3/3** | Pass activation |
| Feedback/evidence audit | 3/3 consistent | Pass |

All three answers earned Boundaries 4, kept Depth at 1, and produced the exact
approved composite 5. V2 therefore activated failure-mode credit when the
learner explicitly stated a trigger and adverse outcome, while continuing to
withhold unstated trade-off credit.

## Case evidence

| Case | Approved axes | V2 axes | Learner boundary relationship | Audit |
| --- | --- | --- | --- | --- |
| Non-functional requirements | 5 / 1 / 4 | 4 / 1 / 4 | Long generic checklist → no basis for storage/caching/replication choices and possibly conflicting goals | Exact secondary axes and composite 5. |
| Identity boundary | 5 / 1 / 5 | 5 / 1 / 4 | Trust body/path user ID → another authenticated caller can cancel the booking through IDOR | Boundary within one; feedback recognized the stated IDOR and supplied only the missing trade-off. |
| Cursor pagination | 5 / 1 / 5 | 5 / 1 / 4 | Hide offset in cursor, then insert rows → duplicate or missing results | Boundary within one; feedback recognized the failure and supplied only the missing arbitrary-page trade-off. |

Each Boundaries 4 is grounded in learner words rather than the question,
approved rubric, or feedback. Every corrective trade-off appears only in
feedback while Depth remains at 1. There is no score/feedback contradiction.

## Cost and resume proof

| Measure | Result |
| --- | ---: |
| Input tokens | 9,586 |
| Output tokens | 833 |
| Cache read / write tokens | 0 / 0 |
| Input cost at $2/M | $0.019172 |
| Output cost at $10/M | $0.008330 |
| **Calculated total** | **$0.027502** |
| Average per call | $0.009167 |
| Share of authorized ceiling | 79.5% |

A keyless exact resume reused all three fingerprints, scheduled zero new calls,
and reported a $0.0000 new-call ceiling.

## Cumulative V2 evidence

Across A1, A2, and B1:

| Measure | Nine-call result |
| --- | ---: |
| Composite exact / within one | **9/9 · 9/9** |
| Accuracy exact / within one | 4/9 · 9/9 |
| Depth exact / within one | 7/9 · 9/9 |
| Boundaries exact / within one | 4/9 · 9/9 |
| Accuracy false pass / failure | 0 / 0 |
| Feedback/evidence audit | 9/9 consistent |
| Input / output tokens | 28,645 / 2,516 |
| **Calculated cost** | **$0.082450** |

The first six cases tested withholding secondary eligibility; B1 supplies the
first positive activation evidence. These remain single samples from nine
reviewed cases, not repeatability or production evidence.

## Next gate

A fresh free preflight for the three approved `depth-only` cases reports:

| Control | B2 value |
| --- | ---: |
| Calls | 3 |
| Counted input | 9,620 |
| Reserved output | 1,536 |
| Authorization ceiling | **$0.0346** |

B2 tests the other half of the eligibility rule. Its answers explicitly state
choice-to-cost relationships, have approved Depth 4–5, approved Boundaries 1,
and approved composite 4. A valid B2 must keep Accuracy passing, award Depth at
least 3, keep Boundaries at most 2, return composite 4, and ground each high
Depth score in learner words.

B2 remains unspent and requires a separate authorization after this result
merges. Follow-up evidence, broader families, repeatability, Terra, prompt
distillation, and production promotion remain blocked.
