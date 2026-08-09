# Claude explicit-evidence Stage A

**Run date:** 2026-08-08 (America/Los_Angeles)

**Decision:** Failed; Stage B and Terra remain stopped

## What ran

Stage A evaluated the `explicit-evidence-v1` candidate on the six approved
speech-noise cases. It was deliberately the first and smallest paid gate after
the failed Terra release smoke.

| Control | Value |
| --- | --- |
| Model | `claude-sonnet-5` |
| Effort | `low` |
| Cases | Six approved `speech-noise` cases |
| Concurrency | 1 |
| Prompt | Production rubric plus evaluation-only `explicit-evidence-v1` |
| Production change | None |
| Preflight ceiling | **$0.0614** |
| Paid calls | **6** |
| Calculated cost | **$0.050304** |

The free preflight counted 15,327 input tokens and reserved 512 output tokens
per call. The paid run stayed below its authorized ceiling and wrote every
result immediately to an ignored local JSONL file.

## Gate result

| Criterion | Result | Gate |
| --- | ---: | --- |
| Structured responses parsed | 6/6 | Pass |
| False Accuracy passes / failures | 0 / 0 | Pass |
| Accuracy exact / within one | 1/6 · 6/6 | Pass safety boundary |
| Composite exact | 5/6 | Informational |
| Composite within one | **5/6** | **Fail** |
| Mean composite deviation | 0.33 | Informational |
| Depth exact / within one | 2/6 · 6/6 | Pass tolerance |
| Boundaries exact / within one | 3/6 · 5/6 | **Fail signal** |
| Feedback/evidence audit | 5/6 consistent | **Fail** |

The candidate remains scheduler-safe on this small set because all six
mechanism scores stayed in the correct passing bucket. It is not display-safe:
one mechanism-only answer still appeared as composite 5, claiming failure-mode
awareness the learner did not demonstrate.

## Case matrix

| Case | Approved axes | Candidate axes | Composite | Audit |
| --- | --- | --- | --- | --- |
| Delivery sequence | 5 / 1 / 1 | 4 / 0 / 0 | 3 → 3 | Correctly withheld both secondary axes and supplied the missing trade-off and failure in feedback. |
| Non-functional requirements | 5 / 1 / 1 | **4 / 2 / 3** | **3 → 5** | Blocking failure: treated pruning to material numbers as failure-mode evidence even though the learner stated no bad outcome, exception, or misconception. |
| Identity boundary | 5 / 1 / 2 | 5 / 1 / 2 | 3 → 3 | Exact. Feedback correctly identified the missing trade-off and unstated IDOR consequence. |
| Timeout retry | 5 / 2 / 2 | 4 / 1 / 1 | 3 → 3 | Correctly kept both missing secondary axes below 3. |
| Cursor pagination | 5 / 1 / 2 | 4 / 1 / 2 | 3 → 3 | Correctly distinguished the stated cursor boundary from the unstated offset-paging failure. |
| Decision-driven estimation | 5 / 1 / 2 | 4 / 2 / 2 | 3 → 3 | Correctly treated dismissing irrelevant math as only near failure-mode evidence because no consequence was explained. |

## Why the blocking case fails the candidate contract

The learner said to quantify p95 latency, availability, and tolerated staleness,
then “only keep the few numbers that actually drive the design.” The approved
rubric's failure mode is more specific: a generic checklist gives no basis for
architectural choices and can create conflicting goals. The learner did not
state that failure or its consequence.

The candidate explicitly requires a relevant failure, exception, limitation,
or misconception plus the condition or consequence that makes it matter before
Boundaries can reach 3. It also says a secondary score of 3–5 must be supported
by quoted or precisely paraphrased learner evidence. Claude nevertheless awarded
Boundaries 3. Its feedback discussed only the missing cost of tighter targets;
it did not identify valid failure-mode evidence.

This is the same case Terra overgraded to composite 5 in the prior smoke. The
shared candidate therefore reduced the other five speech-noise cases to their
approved composite, but it did not resolve the known cross-provider ambiguity.
Relabeling the approved case would move the goalposts and remains prohibited.

## Cost and resume proof

| Measure | Result |
| --- | ---: |
| Input tokens | 15,327 |
| Output tokens | 1,965 |
| Cache read / write tokens | 0 / 0 |
| Input cost at $2/M | $0.030654 |
| Output cost at $10/M | $0.019650 |
| **Calculated total** | **$0.050304** |
| Average per call | $0.008384 |
| Share of authorized ceiling | 81.9% |

The cost uses the published Claude Sonnet 5 promotional rate through August 31,
2026. Provider billing remains authoritative.

A second run with the Anthropic key explicitly unset resumed all six exact
fingerprints, scheduled zero new calls, and reported a $0.0000 new-call ceiling.

## Decision and next experiment

Do not run Stage B, Terra, a higher-effort variant, or the broader release pack.
The candidate failed the first stop gate, and more calls with the same wording
would measure a prompt already known to violate its contract.

The next useful work is offline: draft an `explicit-evidence-v2` candidate that
makes secondary-axis eligibility mechanical. A promising direction is to require
the grader to first identify the learner's exact trade-off and failure-mode
claims, set an axis ceiling of 2 when either claim is absent, and only then choose
0–5 severity within the eligible range. That proposal needs deterministic prompt
tests and a new free preflight before requesting any paid call. It must remain
evaluation-only until it passes a reviewed Claude regression.

That offline candidate and its one-call-first gate are now prepared in
`EXPLICIT-EVIDENCE-V2-EXPERIMENT.md`. No V2 paid call has been made.
