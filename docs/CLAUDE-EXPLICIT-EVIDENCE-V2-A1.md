# Claude explicit-evidence V2 A1

**Run date:** 2026-08-08 (America/Los_Angeles)

**Decision:** Passed; A2 remains separately gated

## What ran

A1 evaluated `explicit-evidence-v2` against only the approved
non-functional-requirements speech-noise case. This case was chosen because
Terra and Claude V1 had both raised its approved composite from 3 to 5 by
inferring failure-mode awareness the learner did not state.

| Control | Value |
| --- | --- |
| Model | `claude-sonnet-5` |
| Effort | `low` |
| Cases | One approved blocking case |
| Concurrency | 1 |
| Prompt | Production rubric plus evaluation-only `explicit-evidence-v2` |
| Production change | None |
| Preflight ceiling | **$0.0116** |
| Paid calls | **1** |
| Calculated cost | **$0.009096** |

The fresh free preflight counted 3,193 input tokens and reserved 512 output
tokens. The paid run made exactly one Message call.

## Result

| Measure | Approved | V2 A1 | Gate |
| --- | ---: | ---: | --- |
| Accuracy | 5 | 4 | Pass bucket; within one |
| Depth | 1 | 1 | Pass; hard ceiling respected |
| Boundaries | 1 | 1 | Pass; hard ceiling respected |
| Composite | 3 | **3** | **Exact pass** |

The strict response parsed. There was no Accuracy false pass or false failure.
All three axes were within one of the frozen approved labels, both secondary
axes were exact, and the derived composite was exact.

## Feedback audit

Claude recognized the quantified p95 latency, read-QPS, availability, and
staleness constraints as mechanism evidence. It then explicitly said the learner
did not state either the cost of tighter targets or the consequence of skipping
the selection discipline. Most importantly, it characterized “only keep the few
numbers” as a prescription without a connected consequence.

That matches V2's eligibility contract:

- no learner-stated target-to-cost relationship, so Depth stays at 0–2;
- no learner-stated trigger-to-adverse-outcome relationship, so Boundaries stays
  at 0–2; and
- trusted rubric details appear only as corrective feedback, not credited recall.

V2 therefore corrected the exact cross-provider ambiguity it was written to
target on this one fresh sample. This is evidence for proceeding to the guarded
regression remainder, not evidence for production promotion or repeatability.

## Cost and resume proof

| Measure | Result |
| --- | ---: |
| Input tokens | 3,193 |
| Output tokens | 271 |
| Cache read / write tokens | 0 / 0 |
| Input cost at $2/M | $0.006386 |
| Output cost at $10/M | $0.002710 |
| **Calculated total** | **$0.009096** |
| Share of authorized ceiling | 78.4% |

The cost uses the published Claude Sonnet 5 promotional rate through August 31,
2026. Provider billing remains authoritative.

A second run with the Anthropic key explicitly unset resumed the exact A1
fingerprint, scheduled zero new calls, and reported a $0.0000 new-call ceiling.

## Next gate

A fresh free preflight for the five remaining speech-noise cases reports:

| Control | A2 value |
| --- | ---: |
| Calls | 5 |
| Counted input | 15,866 |
| Reserved output | 2,560 |
| Authorization ceiling | **$0.0574** |

A2 remains unspent and requires a separate user authorization after this result
merges. It must run at Claude low effort and concurrency 1 with the same V2
fingerprinted prompt. Any composite beyond one point, Accuracy bucket error,
unsupported 3–5 secondary axis, or feedback/evidence contradiction stops the
experiment. Terra and broader release evaluation remain blocked.

Even if A2 passes, V2's extra 1,235 input tokens per call are not automatically
acceptable for production. The next decision would be a broader Claude
regression and later prompt distillation, not an immediate production change.
