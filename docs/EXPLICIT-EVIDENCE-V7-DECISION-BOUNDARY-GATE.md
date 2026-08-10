# Explicit-evidence V7 decision-boundary gate

**Status:** Stopped at K1 after one automatic and manual gate failure; see
`CLAUDE-EXPLICIT-EVIDENCE-V7-RESULTS.md`

## Objective

Correct the V6 decision-threshold failure without disturbing its Accuracy,
Depth, or established Boundary behavior. V7 remains an evaluation-only overlay
shared byte-for-byte by the Claude and OpenAI runners. Production, provider,
model, effort, request shape, schema, parser, scheduler, and reviewed labels are
unchanged.

The failed V6 response treated this mechanism branch as high Boundary evidence:

> If one heap can hold the topic cardinality, keep one instance; if it cannot,
> shard the aggregation across workers.

That statement selects an architecture from a capacity threshold. It does not
say what harmful or incorrect behavior results from choosing or operating the
wrong design. V7 therefore distinguishes two questions:

- **Mechanism:** which option does the threshold select?
- **Boundaries:** what wrong action, condition, or belief causes which concrete
  harm or incorrect behavior?

Selection logic alone must remain Boundaries 0-2. A learner-explicit mistake and
its incorrect behavior still qualifies for 3-5, so the fix does not erase real
failure or misconception evidence.

## Candidate change

V7 is one independent unified contract rather than V6 plus an appended patch.
It preserves V6's five ordered steps and adds only:

1. a rule that an option or capacity branch is not failure evidence by itself;
2. a final check requiring high-Boundary feedback to paraphrase both trigger and
   harm; and
3. compact calibration for the decision branch, explicit self-correction, and
   trade-off-only capacity language.

No Anthropic request, model identifier, structured-output field, token setting,
or cache behavior changed. The required `claude-api` skill is unavailable in
this session; the safe fallback was to leave Anthropic integration code untouched
and constrain the change to evaluation-only rubric text.

## Deterministic controls

Tests establish that:

- V7 is a selectable prompt variant;
- V7 contains the selection-logic and final-consistency rules;
- the heap branch stays in the low Boundary band;
- explicit rejection of ritual arithmetic remains eligible Boundary evidence;
- the interview-time trade-off remains high Depth but low Boundaries;
- Claude and OpenAI receive byte-identical V7 rubric bytes;
- V7 fingerprints cannot resume production or V6 results; and
- the independent V7 contract is less than 1,000 bytes larger than V6.

## Prompt-cost audit

Anthropic's free counter measured the exact staged cases under V6 and compact
V7. V7 adds a constant 214 counted input tokens per call.

| Scope | Calls | V6 input | V7 input | Added |
| --- | ---: | ---: | ---: | ---: |
| K1 focused fix and first blocker observation | 5 | 15,573 | 16,643 | 1,070 |
| K2 remaining follow-ups | 3 | 9,327 | 9,969 | 642 |
| K3 blocker observation 2 | 3 | 9,355 | 9,997 | 642 |
| K4 blocker observation 3 | 3 | 9,355 | 9,997 | 642 |
| **Total** | **14** | **43,610** | **46,606** | **2,996** |

The first correct V7 draft added 343 tokens per call. Tightening the same rule to
three compact clauses reduced new calibration overhead by 37.6%, to 214 tokens
per call. Compared with V6, the final 14-call conservative bound rises by
$0.005992, or 3.8%. No paid call was used for this optimization.

## K1 — focused correction and first blocker observation

| Case | Reviewed axes | Composite | Behavior protected |
| --- | --- | ---: | --- |
| Decision follow-up supplies decision | 5 / 1 / 2 | 3 | A capacity-based option branch stays low Boundaries. |
| Decision self-corrected | 5 / 1 / 4 | 5 | An explicitly rejected mistaken method remains eligible Boundary evidence. |
| Decision trade-off only | 5 / 5 / 1 | 4 | Capacity language does not leak into Boundaries; the complete trade-off stays high Depth. |
| NFR follow-up supplies constraints | 5 / 1 / 1 | 3 | Correct mechanism remains independent of absent secondary evidence. |
| Identity follow-up supplies authorization | 5 / 1 / 2 | 3 | A guardrail without learner-stated harm stays low Boundaries. |

| Calls | Counted input | Reserved output | Exact bound | Stage cap |
| ---: | ---: | ---: | ---: | ---: |
| 5 | 16,643 | 2,560 | $0.058886 | **$0.0589** |

K1 must pass every automatic gate and the manual feedback/evidence audit before
K2 runs.

## K2 — complete follow-up-anchored coverage

| Case | Reviewed axes | Composite |
| --- | --- | ---: |
| Delivery follow-up supplies ordering | 5 / 1 / 1 | 3 |
| Timeout retry follow-up supplies idempotency | 5 / 2 / 2 | 3 |
| Cursor pagination follow-up supplies boundary | 5 / 1 / 2 | 3 |

| Calls | Counted input | Reserved output | Exact bound | Stage cap |
| ---: | ---: | ---: | ---: | ---: |
| 3 | 9,969 | 1,536 | $0.035298 | **$0.0353** |

Together, K1 and K2 cover all six follow-up-anchored topics under V7. K2 runs
only after K1 passes both audits.

## K3 and K4 — fresh blocker repeatability

Each stage independently calls the same three historical blockers:

| Case | Reviewed axes | Composite |
| --- | --- | ---: |
| NFR follow-up supplies constraints | 5 / 1 / 1 | 3 |
| Identity follow-up supplies authorization | 5 / 1 / 2 | 3 |
| Decision trade-off only | 5 / 5 / 1 | 4 |

| Stage | Calls | Counted input | Reserved output | Exact bound | Stage cap |
| --- | ---: | ---: | ---: | ---: | ---: |
| K3 — observation 2 | 3 | 9,997 | 1,536 | $0.035354 | **$0.0354** |
| K4 — observation 3 | 3 | 9,997 | 1,536 | $0.035354 | **$0.0354** |

K1 is observation 1 for all three blockers. K3 and K4 create observations 2 and
3 with `--fresh` and distinct durable files. K3 must pass before K4 runs.

## Cost ledger and authorization

Pricing uses the published promotional schedule through 2026-08-31: $2 per
million input tokens and $10 per million output tokens.

| Scope | Calls | Input | Reserved output | Exact bound | Stage cap |
| --- | ---: | ---: | ---: | ---: | ---: |
| K1 focused correction | 5 | 16,643 | 2,560 | $0.058886 | $0.0589 |
| K2 remaining follow-ups | 3 | 9,969 | 1,536 | $0.035298 | $0.0353 |
| K3 blocker observation 2 | 3 | 9,997 | 1,536 | $0.035354 | $0.0354 |
| K4 blocker observation 3 | 3 | 9,997 | 1,536 | $0.035354 | $0.0354 |
| **Maximum V7 gate** | **14** | **46,606** | **7,168** | **$0.164892** | **$0.1649** |

The requested cumulative authorization after this preparation PR merges is
**$0.1649**. Applying V6's observed output-cost average plus V7's measured input
delta suggests approximately $0.1300 for 14 calls, but that is a planning
forecast rather than a guarantee. The stage caps are the enforceable limits.

## Pass, repeatability, and stop policy

Every response must parse, keep composite and every axis within one of its
reviewed label, preserve the Accuracy pass/fail bucket, obey the mandatory
relationship bands, and keep feedback consistent with learner evidence.

A provider error, malformed response, automatic gate failure, manual evidence
contradiction, or budget issue stops the experiment after durable recording.
There is no retry, effort escalation, prompt adjustment, or favorable-sample
replacement. Later stages remain unspent.

Across K1, K3, and K4, the three blocker observations receive the same explicit
classification as the prior repeatability plan:

- **Repeatability pass:** all gates pass and every case's composite and per-axis
  range across three samples is at most one point.
- **Safety-stable but calibration-variable:** all gates and Accuracy buckets pass,
  but any three-sample range is two points. V7 remains evaluation-only.
- **Fail:** any automatic or manual gate fails. Stop immediately.

After execution, keyless exact-fingerprint replays must prove every durable file
without scheduling a new call.

## What remains after this gate

A complete V7 pass would finish follow-up coverage and blocker repeatability, but
would not authorize production. The remaining sequence is:

1. the 12 balanced speech-noise and adjacent-jargon risk-smoke cases;
2. the other 22 still-unseen V7 release-pack cases, including alternatives,
   partial self-correction, stale-summary isolation, and the remaining axis
   controls;
3. the frozen 18-case baseline; and
4. a matched provider decision using the same approved contract and case subset.

Only after those gates pass should the winning prompt/provider be proposed for
production. Each future phase needs its own free count, paid cap, durable result
PR, and stop policy.

## Run outcome

K1 spent $0.050376 across five approved calls. V7 fixed the original
decision-threshold Boundary leak, but decision-driven self-correction returned
Depth 3 against reviewed 1 while its feedback supplied the missing trade-off.
The experiment stopped before K2-K4, leaving $0.114524 unspent. Production
remained unchanged; the durable audit is in
`CLAUDE-EXPLICIT-EVIDENCE-V7-RESULTS.md`.
