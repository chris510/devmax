# Claude explicit-evidence V8 follow-up results

**Run date:** 2026-08-10 (America/Los_Angeles)

**Decision:** Stopped at M1; repeatability stages not run

## Outcome

The five-case M1 stage made its authorized paid calls and then failed the
automatic reviewed gate. Delivery sequence, identity authorization, timeout
retry, and cursor pagination passed. Non-functional requirements returned
Boundaries 4 against reviewed 1 and composite 5 against reviewed 3.

The response's own feedback praises the correct mechanism and identifies a
missing trade-off. It cites no learner-stated wrong action, condition, or belief
connected to a concrete harm or incorrect behavior. Boundaries 4 is therefore
unsupported by the learner evidence and inconsistent with the feedback. The
manual audit independently fails the same response.

The approved stop policy was followed. M2 and M3 made no calls, repeatability
was not measured, and production remained unchanged.

| Control | Value |
| --- | --- |
| Prompt | Production rubric plus evaluation-only `explicit-evidence-v8` |
| Cumulative authorized cap | **$0.129200** |
| Paid calls | **5 of 11 maximum** |
| Calculated spend | **$0.050366** |
| Unspent authorization | **$0.078834** |
| Production change | None |

## Stage ledger

| Stage | Calls | Input | Output | Actual cost | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| M1 — remaining follow-ups | 5 | 16,543 | 1,728 | $0.050366 | **Fail; stopped** |
| M2 — blocker observation 2 | 0 | 0 | 0 | $0.000000 | Not run |
| M3 — blocker observation 3 | 0 | 0 | 0 | $0.000000 | Not run |
| **Total** | **5** | **16,543** | **1,728** | **$0.050366** | **Stopped** |

Cost uses the published promotional schedule through 2026-08-31: $2 per
million input tokens and $10 per million output tokens. No cache tokens were
read or written. M1 used $0.008334 less than its $0.058700 stage cap.

## Automatic and manual gate

| Criterion | M1 result | Gate |
| --- | ---: | --- |
| Structured responses parsed | 5/5 | Pass |
| False Accuracy passes / failures | 0 / 0 | Pass safety boundary |
| Composite exact / within one | 4/5 · **4/5** | **Fail** |
| Mean composite deviation | 0.40 | Fail from one two-point miss |
| Accuracy exact / within one | 2/5 · **5/5** | Pass |
| Depth exact / within one | 3/5 · **5/5** | Pass |
| Boundaries exact / within one | 1/5 · **4/5** | **Fail** |
| Feedback/evidence audit | **4/5 consistent** | **Fail** |

## Case matrix

| Case | Reviewed axes | V8 axes | Composite | Audit |
| --- | --- | --- | --- | --- |
| Delivery sequence — follow-up ordering | 5 / 1 / 1 | 4 / 1 / 1 | 3 → 3 | Pass. Mechanism counted; feedback correctly identifies absent cost and failure evidence. |
| NFR — follow-up constraints | 5 / 1 / 1 | 5 / 2 / 4 | 3 → 5 | **Fail.** Correct constraints contain no wrong-action/harm relationship; feedback cites none. |
| Identity — follow-up authorization | 5 / 1 / 2 | 4 / 1 / 1 | 3 → 3 | Pass. Authorization counted; absent exploit and trade-off remain low. |
| Timeout retry — follow-up idempotency | 5 / 2 / 2 | 5 / 1 / 1 | 3 → 3 | Pass. Unknown outcome and same-key handling count without inferred secondary evidence. |
| Cursor pagination — follow-up boundary | 5 / 1 / 2 | 4 / 1 / 1 | 3 → 3 | Pass. Ordered-key mechanism counted; missing trade-off and insertion harm stay low. |

## Failure analysis

The NFR follow-up states operation-specific constraints: p95 feed-read latency
at expected QPS, an availability target, and acceptable staleness during a
failure. That fully answers how vague qualities become architectural
constraints, so Accuracy 5 is appropriate.

It does not state that a wrong target, checklist, or design action causes a
concrete harm. The words “during a failure” describe the operating condition in
which staleness is measured; they do not connect a learner-stated mistake to an
incorrect result. Treating that phrase as Boundary evidence conflates the
subject of a constraint with evidence that the learner understands a failure
mode.

V8 already requires a learner-stated trigger/harm relationship and requires
high-Boundary feedback to paraphrase both endpoints. The returned feedback does
neither. This is therefore not a missing-label problem and not evidence to
promote the reviewed Boundary score. It is evidence that Claude did not apply
the general V8 secondary-axis rule reliably outside the focused decision
calibration.

The four passing cases are useful local evidence, but the stopped stage cannot
be converted into a pass by averaging, relabeling NFR, or replacing the failed
sample. Repeatability remains unknown because M2 and M3 were correctly canceled.

## Durable failure proof

A separate replay loaded the M1 JSONL with the Anthropic key explicitly unset.
It resumed 5/5 exact fingerprints, scheduled zero new calls, printed a $0.0000
new-call ceiling, and reproduced both reviewed-gate failures:

- NFR composite 5 is more than one from reviewed 3; and
- NFR Boundaries 4 is more than one from reviewed 1.

The failure is durably attributable to the recorded response rather than a
second sample.

## Decision and next experiment

Keep V8 evaluation-only. Do not run M2, M3, the risk-smoke, the remaining
release pack, or the frozen baseline, and do not rerun M1 unchanged. Production
remains on its current scoring prompt and provider.

The next preparation should compare two small, no-production-change options
before spending again:

1. a minimal V9 provider-neutral contract that makes constraint context
   explicitly non-Boundary evidence unless the learner also states the wrong
   action and harm; and
2. a matched OpenAI diagnostic on the same historical blocker matrix under the
   unchanged V8 contract, to determine whether the repeated attribution errors
   are prompt-specific or provider-specific.

That preparation should audit shared Claude/OpenAI prompt bytes, use only
approved synthetic cases, calculate both caps before any paid call, and select
one smallest discriminating experiment. It must not reopen broader gates until
the local failure and three-sample blocker repeatability both pass.
