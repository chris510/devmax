# Claude explicit-evidence V4 targeted-gate results

**Run date:** 2026-08-09 (America/Los_Angeles)

**Decision:** Rejected; E1 passed and E2 failed Depth tolerance

## What ran

V4 evaluated the two blocking V3 cases first, then four symmetric controls after
the first stage passed. Every call used Claude Sonnet 5, low effort, concurrency
1, approved grounding, the production score schema, and the automatic reviewed
gate. Production remained unchanged.

| Control | Value |
| --- | --- |
| Prompt | Production rubric plus evaluation-only `explicit-evidence-v4` |
| Cumulative authorized cap | **$0.074300** |
| Paid calls | **6** |
| Calculated spend | **$0.063240** |
| Unspent authorization | **$0.011060** |
| Production change | None |

Both fresh free preflights matched the prepared token counts and stayed within
their stage caps.

## Stage ledger

| Stage | Calls | Input | Output | Actual cost | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| E1 — two V3 failures | 2 | 7,294 | 677 | $0.021358 | Pass |
| E2 — symmetric controls | 4 | 14,471 | 1,294 | $0.041882 | **Fail; stop** |
| **Total** | **6** | **21,765** | **1,971** | **$0.063240** | **Rejected** |

Cost uses the published promotional schedule through 2026-08-31: $2 per
million input tokens and $10 per million output tokens. No cache tokens were
read or written.

## Aggregate gate result

| Criterion | Six-call result | Gate |
| --- | ---: | --- |
| Structured responses parsed | 6/6 | Pass |
| False Accuracy passes / failures | 0 / 0 | Pass safety boundary |
| Composite exact / within one | 5/6 · 6/6 | Pass |
| Mean composite deviation | 0.17 | Informational |
| Accuracy exact / within one | 1/6 · 6/6 | Pass tolerance |
| Depth exact / within one | 3/6 · **5/6** | **Fail tolerance** |
| Boundaries exact / within one | 4/6 · 6/6 | Pass |
| Feedback/evidence audit | 6/6 consistent | Pass |

## Case matrix

| Case | Reviewed axes | V4 axes | Composite | Audit |
| --- | --- | --- | --- | --- |
| NFR follow-up constraints | 5 / 1 / 1 | 4 / 1 / 1 | 3 → 3 | Original Accuracy leak fixed within tolerance; secondary omissions stayed isolated. |
| Identity follow-up authorization | 5 / 1 / 2 | 4 / 1 / 1 | 3 → 3 | Original Boundaries overgrade fixed; feedback supplied IDOR only as correction. |
| NFR fluent checklist | 1 / 0 / 0 | 0 / 0 / 0 | 1 → 0 | Incorrect mechanism stayed failing; axis independence did not inflate it. |
| NFR noisy transcript | 5 / 1 / 1 | 4 / 2 / 2 | 3 → 3 | Correct mechanism remained passing; missing secondary relationships stayed below 3. |
| Identity failure boundary | 5 / 1 / 5 | 5 / 2 / 5 | 5 → 5 | Explicit action-to-harm relationship activated Boundaries exactly. |
| Decision-driven trade-off | 5 / 5 / 1 | **4 / 3 / 1** | 4 → 4 | **Blocking:** the correct explicit time-versus-capacity relationship entered the right band but Depth was two points low. |

## What V4 fixed

V4 repaired both defects it was designed to target:

- Missing trade-off and failure evidence no longer dragged correct NFR mechanism
  Accuracy more than one point below its reviewed label.
- An identity guardrail without a learner-stated adverse outcome no longer
  received high Boundaries credit.
- The matching positive control still received Boundaries 5 when the learner
  explicitly connected trusting client identity to IDOR.

The two-endpoint evidence rule therefore worked in both directions on this gate.
All six feedback statements attributed learner evidence consistently.

## Why V4 still fails

The decision-driven-estimation learner explicitly connected skipping irrelevant
arithmetic to saved interview time while retaining attention to real capacity
limits. Claude's feedback precisely acknowledged that complete relationship, so
Depth correctly entered the eligible 3-5 band. It nevertheless selected the
minimum eligible value, 3, against the frozen reviewed value 5.

This is no longer an eligibility or attribution contradiction. It is a
within-band calibration problem: V3/V4 explain when secondary credit activates
but do not distinguish 3, 4, and 5 mechanically enough. The derived composite
happened to remain exact, but the displayed Depth signal missed by two and failed
the predeclared gate.

## Durable stop proof

The live E2 process durably wrote all four responses before returning nonzero. A
keyless replay resumed 4/4 exact fingerprints, scheduled zero new calls, printed
a $0.0000 new-call ceiling, and reproduced the same `depth 3 is more than one
from 5` failure. No repeat or favorable sample was taken.

## Decision and next experiment

Do not promote V4, repeat the sample, run remaining families, compare Terra, or
change production.

The next useful candidate is a small V5 within-band calibration appended to V4:

1. Reserve 3 for a correct but materially incomplete or ambiguous relationship.
2. Use 4 for a clear, correct relationship with a minor omission.
3. Use 5 when the learner completely states the rubric's named relationship;
   one complete relationship is enough and extra examples are not required.
4. Require feedback that calls a relationship complete and explicit to return
   4-5, preserving the existing two-endpoint eligibility checks.

Start with the single failed decision-driven case. Only if it reaches Depth 4-5
should the other five axis-isolation cases run to distinguish genuine 4s from 5s
and protect both secondary axes. The two V4 repair cases should then serve as
low-band regression controls before any broader family resumes. This needs an
offline prompt/test PR, fresh free counts, and a new explicit paid authorization.
The prepared candidate and eight-case gate are documented in
`EXPLICIT-EVIDENCE-V5-CALIBRATION-GATE.md`.
