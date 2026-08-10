# Claude explicit-evidence V5 calibration-gate results

**Run date:** 2026-08-09 (America/Los_Angeles)

**Decision:** Rejected; F1 and F2 passed, F3 reopened the Accuracy leak

## What ran

V5 evaluated the exact V4 blocker, all five remaining axis-isolation controls,
then the two V4 low-band repair cases. Every call used Claude Sonnet 5, low
effort, concurrency 1, approved grounding, the production schema, and the
automatic reviewed gate. Production remained unchanged.

| Control | Value |
| --- | --- |
| Prompt | Production rubric plus evaluation-only `explicit-evidence-v5` |
| Cumulative authorized cap | **$0.103300** |
| Paid calls | **8** |
| Calculated spend | **$0.084840** |
| Unspent authorization | **$0.018460** |
| Production change | None |

All three fresh free preflights matched the prepared counts and stayed within
their stage caps.

## Stage ledger

| Stage | Calls | Input | Output | Actual cost | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| F1 — exact V4 blocker | 1 | 3,906 | 271 | $0.010522 | Pass |
| F2 — remaining axis controls | 5 | 19,404 | 1,344 | $0.052248 | Pass |
| F3 — V4 repair regressions | 2 | 7,840 | 639 | $0.022070 | **Fail; stop** |
| **Total** | **8** | **31,150** | **2,254** | **$0.084840** | **Rejected** |

Cost uses the published promotional schedule through 2026-08-31: $2 per
million input tokens and $10 per million output tokens. No cache tokens were
read or written.

## Aggregate gate result

| Criterion | Eight-call result | Gate |
| --- | ---: | --- |
| Structured responses parsed | 8/8 | Pass |
| False Accuracy passes / failures | 0 / 0 | Pass safety boundary |
| Composite exact / within one | **8/8 · 8/8** | Pass |
| Mean composite deviation | 0.00 | Pass |
| Accuracy exact / within one | 5/8 · **7/8** | **Fail tolerance** |
| Depth exact / within one | 4/8 · 8/8 | Pass |
| Boundaries exact / within one | 6/8 · 8/8 | Pass |
| Feedback/evidence audit | **7/8 consistent** | **Fail** |

The exact composite on every call hides the blocking defect. Accuracy is the
scheduler-owned axis; a two-point miss cannot be accepted merely because the
display-only composite happens to match.

## Case matrix

| Case | Reviewed axes | V5 axes | Composite | Audit |
| --- | --- | --- | --- | --- |
| Decision-driven trade-off | 5 / 5 / 1 | 4 / 4 / 1 | 4 → 4 | V4 blocker fixed; complete trade-off moved from Depth 3 to 4. |
| Delivery-sequence trade-off | 5 / 4 / 1 | 5 / 4 / 0 | 4 → 4 | Clear single relationship calibrated at Depth 4. |
| Timeout-retry trade-off | 5 / 5 / 1 | 5 / 4 / 1 | 4 → 4 | Two explicit trade-offs remained within one of reviewed 5. |
| NFR failure boundary | 5 / 1 / 4 | 4 / 1 / 4 | 5 → 5 | Checklist mistake and architectural harm earned Boundaries 4. |
| Identity failure boundary | 5 / 1 / 5 | 5 / 1 / 5 | 5 → 5 | Explicit identity action and IDOR harm earned Boundaries 5. |
| Cursor failure boundary | 5 / 1 / 5 | 5 / 2 / 5 | 5 → 5 | Offset drift and duplicate/skip harm earned Boundaries 5. |
| NFR follow-up constraints | 5 / 1 / 1 | **3 / 0 / 0** | 3 → 3 | **Blocking:** feedback called the mechanism correct, then missing secondary evidence leaked into Accuracy. |
| Identity follow-up authorization | 5 / 1 / 2 | 5 / 1 / 2 | 3 → 3 | Exact axes; guardrail without stated harm stayed below 3. |

## What V5 fixed

V5 solved the defect it targeted:

- the exact V4 blocker moved from Depth 3 to 4;
- all six axis-isolation cases stayed within one on every axis;
- reviewed Depth 4 and 5 controls remained distinguishable within tolerance;
- both positive Boundaries 5 controls stayed at 5; and
- all high secondary scores were grounded in both learner-stated endpoints.

The within-band secondary calibration is therefore promising on this sample.

## Why V5 still fails

The NFR follow-up correctly supplied operation-specific latency at expected QPS,
availability, and staleness constraints. Claude's feedback explicitly said those
constraints were correct, then discussed only the absent trade-off and failure
outcome. V5 nevertheless returned Accuracy 3 rather than reviewed 5.

That directly violates V4's freeze-Accuracy rule. The secondary omissions should
affect only Depth and Boundaries. Composite 3 remained exact because composite
derivation is intentionally tolerant of low secondary axes, which makes the raw
axis audit essential.

V3 produced the same Accuracy 3, V4 improved it to 4, and V5 regressed to 3 after
a calibration paragraph was appended. This sequence suggests instruction
competition in the accumulated V3+V4+V5 overlay, not a missing sixth corrective
paragraph.

## Durable stop proof

The live F3 process durably wrote both responses before returning nonzero. A
keyless replay resumed 2/2 exact fingerprints, scheduled zero new calls, printed
a $0.0000 new-call ceiling, and reproduced `accuracy 3 is more than one from 5`.
No repeat or favorable sample was taken.

## Decision and next experiment

Do not promote V5, append V6 to it, repeat the sample, run broader families,
compare Terra, or change production.

The next useful candidate is a compact, unified V6 evaluation contract that
replaces the accumulated candidate overlay rather than extending it. It should
express one ordered decision procedure:

1. Score and freeze Accuracy from mechanism correctness/completeness only.
2. For each secondary axis, identify both learner-stated relationship endpoints.
3. Assign the mandatory low/high eligibility band.
4. Calibrate eligible relationships across 3/4/5.
5. Verify each feedback statement against the frozen axes before return.

V6 must count fewer input tokens than V5 on every critical case before any paid
call. Its first gate should cover the three historical blockers—NFR Accuracy,
identity guardrail Boundaries, and decision-driven Depth—plus one incorrect
mechanism and one explicit failure-mode control. Only after that five-case gate
passes should the full eight-case V5 set run.

This direction reduces both instruction interference and cost. A complete pass
would still need repeatability before any production or provider decision. V6
requires an offline prompt/test PR, fresh free counts, and new explicit paid
authorization. The prepared unified candidate and nine-case gate are documented
in `EXPLICIT-EVIDENCE-V6-UNIFIED-GATE.md`.
