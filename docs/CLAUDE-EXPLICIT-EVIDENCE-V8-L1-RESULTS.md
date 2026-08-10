# Claude explicit-evidence V8 symmetric-selection results

**Run date:** 2026-08-10 (America/Los_Angeles)

**Decision:** Passed L1; broader gates remain before production

## Outcome

V8 passed the three-case negative/positive matrix that V7 failed. Selection
logic stayed in the mechanism axis, explicit misconception evidence remained
high Boundaries, and an explicit interview-time tension remained high Depth.

Every composite and axis was within one point of its reviewed label, all
Accuracy buckets were correct, and every feedback statement was consistent
with the learner's evidence and returned axes. Production remained unchanged.

| Control | Value |
| --- | --- |
| Prompt | Production rubric plus evaluation-only `explicit-evidence-v8` |
| Authorized cap | **$0.035200** |
| Paid calls | **3** |
| Calculated spend | **$0.031638** |
| Unspent authorization | **$0.003562** |
| Production change | None |

## Cost ledger

| Calls | Input | Output | Cache read/write | Actual cost | Result |
| ---: | ---: | ---: | ---: | ---: | --- |
| 3 | 9,909 | 1,182 | 0 / 0 | $0.031638 | **Pass** |

Cost uses the published promotional schedule through 2026-08-31: $2 per
million input tokens and $10 per million output tokens.

## Automatic and manual gate

| Criterion | L1 result | Gate |
| --- | ---: | --- |
| Structured responses parsed | 3/3 | Pass |
| False Accuracy passes / failures | 0 / 0 | Pass safety boundary |
| Composite exact / within one | 3/3 · **3/3** | Pass |
| Mean composite deviation | 0.00 | Pass |
| Accuracy exact / within one | 2/3 · **3/3** | Pass |
| Depth exact / within one | 2/3 · **3/3** | Pass |
| Boundaries exact / within one | 1/3 · **3/3** | Pass |
| Feedback/evidence audit | **3/3 consistent** | Pass |

## Case matrix

| Case | Reviewed axes | V8 axes | Composite | Audit |
| --- | --- | --- | --- | --- |
| Decision self-corrected | 5 / 1 / 4 | 5 / 1 / 4 | 5 → 5 | Exact axes. Explicit rejection of irrelevant arithmetic counted as misconception evidence; the option branch stayed low Depth. |
| Decision follow-up supplies decision | 5 / 1 / 2 | 4 / 1 / 1 | 3 → 3 | Correct mechanism counted; pure selection logic stayed low on both secondary axes. |
| Decision trade-off only | 5 / 5 / 1 | 5 / 4 / 2 | 4 → 4 | Explicit interview-time tension stayed high Depth; absent failure evidence stayed low Boundaries. |

## What V8 establishes

V8 is the first candidate to hold all three decision-driven distinctions in one
sample:

- choosing an option from a capacity threshold is mechanism evidence only;
- explicitly rejecting a mistaken estimate because it cannot decide the branch
  is Boundary evidence without becoming Depth; and
- connecting skipped arithmetic to saved interview time while retaining
  capacity awareness is Depth evidence without becoming Boundaries.

The result also confirms that V8 fixed the V7 Depth regression without
reopening the V6 Boundary regression.

## Durable pass proof

A separate keyless replay loaded the L1 JSONL with the Anthropic key unset. It
resumed 3/3 exact fingerprints, scheduled zero paid calls, printed a $0.0000
new-call ceiling, and passed the reviewed gate again. The pass is therefore
durably attributable to the recorded responses rather than a second sample.

## Production implication

Do not implement V8 in production yet. L1 proves only the local correction and
its closest controls. It does not establish the other follow-up topics,
repeatability, speech noise, adjacent jargon, alternatives, stale-summary
isolation, partial self-correction across topics, the frozen baseline, or the
best provider.

The remaining evidence sequence is:

1. **M1 — five remaining follow-up cases:** NFR, identity, delivery sequence,
   timeout retry, and cursor pagination. Together with L1's decision follow-up,
   this completes all six follow-up-anchored topics under V8.
2. **M2 and M3 — blocker repeatability:** fresh NFR follow-up, identity
   follow-up, and decision trade-off calls in each stage. M1/L1 provide the
   first V8 observation; M2 and M3 bring every blocker to three.
3. **Risk-smoke:** 12 balanced speech-noise and adjacent-jargon cases.
4. **Remaining release pack and frozen baseline:** all still-unseen approved
   families, then the frozen 18-case baseline.
5. **Matched provider decision:** compare Claude and OpenAI under the same
   approved contract and case subset, including quality, repeatability, latency,
   and actual cost.

Only after all five evidence groups pass should V8 or its matched winner be
proposed for production. Every paid group retains free counting, a hard cap,
manual feedback audit, durable results, and stop-on-failure behavior.
