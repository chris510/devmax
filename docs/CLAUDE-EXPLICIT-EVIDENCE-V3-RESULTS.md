# Claude explicit-evidence V3 stopped-run results

**Run date:** 2026-08-09 (America/Los_Angeles)

**Decision:** Rejected; the automatic gate stopped the workstream at C5b

## What ran

The continuous workstream evaluated the shared `explicit-evidence-v3` scoring
overlay on Claude Sonnet 5 at low effort. It ran one case at a time against the
frozen reviewed labels and stopped on the first failing stage. Production
scoring, the provider, model, effort, schema, parser, scheduler, and approved
labels were not changed.

| Control | Value |
| --- | --- |
| Model | `claude-sonnet-5` |
| Effort | `low` |
| Concurrency | 1 |
| Prompt | Production rubric plus evaluation-only `explicit-evidence-v3` |
| Automatic gate | `--enforce-reviewed-gate` |
| Cumulative authorized cap | **$0.815300** |
| Paid calls made | **24 of at most 72** |
| Calculated spend | **$0.226772** |
| Unspent authorization | **$0.588528** |
| Production change | None |

Every stage received a fresh Anthropic token-count preflight before its paid
call. Each count matched its documented ceiling. C1 through C5a passed both the
reviewed gate and manual feedback/evidence audit. C5b failed, so C5c, C5d, C5e,
C6, and compact distillation were not run.

## Stage ledger

| Stage | Family | Calls | Input | Output | Actual cost | Result |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| C1 | V2 trade-off false negative | 1 | 3,123 | 355 | $0.009796 | Pass |
| C2 | Original Boundaries false positive | 1 | 3,094 | 399 | $0.010178 | Pass |
| C3 | Remaining axis isolation | 5 | 15,489 | 1,580 | $0.046778 | Pass |
| C4 | Remaining speech noise | 5 | 15,371 | 1,664 | $0.047382 | Pass |
| C5a | Adjacent jargon | 6 | 18,597 | 1,867 | $0.055864 | Pass |
| C5b | Follow-up anchored | 6 | 18,782 | 1,921 | $0.056774 | **Fail; stop** |
| C5c-C6 | Remaining reviewed families | 0 | 0 | 0 | $0.000000 | Not run |
| D1 | Compact critical regression | 0 | 0 | 0 | $0.000000 | Blocked by V3 failure |
| **Total** | **24 reviewed cases** | **24** | **74,456** | **7,786** | **$0.226772** | **Rejected** |

Cost uses the published promotional schedule through 2026-08-31: $2 per
million input tokens and $10 per million output tokens. No cache tokens were
read or written.

## Aggregate gate result

| Criterion | 24-call result | Gate |
| --- | ---: | --- |
| Structured responses parsed | 24/24 | Pass |
| False Accuracy passes / failures | 0 / 0 | Pass safety boundary |
| Composite exact / within one | 20/24 · **23/24** | **Fail tolerance** |
| Mean composite deviation | 0.21 | Informational |
| Accuracy exact / within one | 8/24 · **23/24** | **Fail tolerance** |
| Depth exact / within one | 12/24 · 24/24 | Pass |
| Boundaries exact / within one | 13/24 · **23/24** | **Fail tolerance** |
| Feedback/evidence audit | **23/24 consistent** | **Fail** |

The scheduler pass/fail boundary happened to remain intact in this single
sample, but that is not enough for promotion. One correct mechanism lost two
Accuracy points because secondary evidence was missing, and another answer
received unsupported failure-mode credit that moved its displayed composite by
two points.

## Blocking failures

### 1. Secondary omissions leaked into Accuracy

`non-functional requirements — follow-up supplies constraints` has approved
axes **5 / 1 / 1** and approved composite **3**. The follow-up answer supplies
operation-specific p95 latency, expected QPS, availability, and stale-data
tolerance. It does not supply a trade-off or failure consequence.

V3 returned **3 / 2 / 2**, composite **3**. Its feedback explicitly said the
follow-up correctly reframed vague adjectives into operation-specific SLOs,
then criticized only the absent cost and failure outcome. Those are Depth and
Boundaries omissions; they must not reduce mechanism Accuracy. The display
number happened to remain exact, but the scheduler-owned axis was two points
below its frozen label.

### 2. A warning was promoted into an unstated failure outcome

`identity boundary — follow-up supplies authorization` has approved axes
**5 / 1 / 2** and approved composite **3**. The learner says to ignore the body
ID as identity evidence, load the booking, and authorize the verified principal.
That is the correct mechanism and a guardrail, but it does not state the adverse
outcome, such as impersonation or cancelling another user's booking.

V3 returned **4 / 1 / 4**, composite **5**. Boundaries 4 violates the mandatory
0-2 band because no learner-stated trigger-to-harm relationship exists. The
feedback discussed the mechanism and missing trade-off but did not identify any
learner-stated failure consequence, so the high Boundaries score also
contradicts its own evidence account.

## What V3 did establish

The first 18 calls passed before the blocking family:

- it corrected V2's decision-driven-estimation Depth false negative;
- it preserved the original noisy-NFR Boundaries correction;
- all five remaining axis-isolation cases activated only the supported
  secondary axis;
- all five remaining speech-noise cases withheld unsupported secondary credit;
- all six adjacent-jargon cases stayed in the reviewed Accuracy failure bucket;
  and
- no call crossed the Accuracy pass/fail boundary.

This is useful diagnostic evidence, but it does not make V3 promotable. C5b
shows that the current prose rules are still not reliably axis-independent and
still allow an implied harm to masquerade as explicit learner evidence.

## Durable stop proof

The live C5b process wrote all six responses before returning nonzero. A second
run loaded those exact fingerprints with the Anthropic key unset, resumed 6/6,
scheduled zero paid calls, printed a $0.0000 new-call ceiling, and reproduced
the same three gate messages:

- NFR Accuracy 3 was more than one point from approved 5;
- identity composite 5 was more than one point from approved 3; and
- identity Boundaries 4 was more than one point from approved 2.

The stop is therefore durable and replayable. It was not inferred from console
output or dependent on another paid sample.

## Decision and next experiment

Do not repeat V3, distill it, run the remaining families, compare Terra, or
change production. More samples cannot remove two known contract violations.

The next candidate should be a small evaluation-only V4 delta, not another
broad prompt expansion:

1. State axis independence as a return-time invariant: once the answer basis is
   correctly supplied, missing trade-off or failure evidence can lower only its
   own axis, never Accuracy.
2. Require both endpoints of a secondary relationship to come from the learner.
   A warning such as “ignore client-supplied identity” is not a concrete adverse
   outcome and must keep Boundaries at 0-2.
3. Require feedback to name the learner-stated relationship before returning a
   secondary score of 3-5; if it cannot, the axis must be lowered before return.
4. Begin with the two C5b failures plus symmetric positive and negative controls.
   Only after those pass should the remaining C5 families resume.

That targeted sequence tests the two observed defects directly while minimizing
spend. It still requires deterministic prompt/fingerprint tests, a fresh free
token count, and explicit authorization before any new paid calls.
