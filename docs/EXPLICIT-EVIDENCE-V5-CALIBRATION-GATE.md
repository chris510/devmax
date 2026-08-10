# Explicit-evidence V5 within-band calibration gate

**Status:** F1 and F2 passed; F3 failed the reviewed Accuracy tolerance. See
`CLAUDE-EXPLICIT-EVIDENCE-V5-RESULTS.md`.

## Objective

Calibrate 3, 4, and 5 after V4 has already decided that learner evidence is
eligible for a high secondary-axis score. V5 preserves the full V4 contract and
adds only this scale:

- **3:** correct but materially vague or incomplete in an endpoint or connection;
- **4:** clear and complete, with a minor omission; and
- **5:** fully states the approved named relationship.

One complete relationship is sufficient. V5 cannot demand extra examples,
numbers, or multiple relationships, and missing mechanism or other-secondary
evidence cannot lower the eligible axis.

Production, provider, model, effort, request shape, schema, parser, scheduler,
and reviewed labels remain unchanged. `explicit-evidence-v5` is an
evaluation-only overlay shared byte-for-byte by the Claude and OpenAI runners.

## Why V5 exists

V4 fixed both blocking V3 defects and passed every eligibility/attribution
check in its six-call gate. It still failed one reviewed tolerance: Claude
recognized the complete decision-driven-estimation trade-off but assigned the
minimum eligible Depth 3 against reviewed 5.

The problem is now within-band calibration, not evidence eligibility. V5 makes
the scale explicit and requires an axis to reach 4-5 when feedback says the
learner stated the full named relationship.

## Deterministic controls

Tests establish that:

- V5 contains the full V4 axis-independence and two-endpoint contract;
- 3 is reserved for materially vague or incomplete relationships;
- 4 and 5 describe clear, complete relationships;
- one complete relationship is enough;
- feedback that calls the relationship explicit or complete requires 4-5;
- the failed time-versus-capacity relationship is explicitly calibrated at 4-5;
- Claude and OpenAI receive a byte-identical V5 rubric; and
- V5 fingerprints cannot resume production or V1-V4 results.

No Anthropic request, model identifier, structured-output field, token setting,
or caching behavior changed.

## Staged paid gate

Every stage uses Claude Sonnet 5, low effort, concurrency 1, approved grounding,
the production score schema, and `--enforce-reviewed-gate`. Each response also
receives a manual feedback/evidence audit before the next stage.

### F1 — exact V4 blocker

| Case | Reviewed axes | Composite | Required behavior |
| --- | --- | ---: | --- |
| Decision-driven estimation — trade-off only | 5 / 5 / 1 | 4 | The complete explicit time-versus-capacity relationship must receive Depth 4-5. |

| Calls | Counted input | Reserved output | Exact bound | Stage cap |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 3,906 | 512 | $0.012932 | **$0.0130** |

F1 must pass before any additional call runs.

### F2 — remaining axis-isolation controls

| Case | Reviewed axes | Composite | Scale protected |
| --- | --- | ---: | --- |
| Delivery sequence — trade-off only | 5 / 4 / 1 | 4 | One clear time/detail relationship stays within one of reviewed 4. |
| Timeout retry — trade-off only | 5 / 5 / 1 | 4 | Two complete state/delay relationships reach Depth 4-5. |
| NFR — failure boundary only | 5 / 1 / 4 | 5 | A clear checklist failure stays within one of reviewed 4. |
| Identity — failure boundary only | 5 / 1 / 5 | 5 | Explicit IDOR action and harm reach Boundaries 4-5. |
| Cursor pagination — failure boundary only | 5 / 1 / 5 | 5 | Explicit offset drift and duplicate/skip harm reach Boundaries 4-5. |

| Calls | Counted input | Reserved output | Exact bound | Stage cap |
| ---: | ---: | ---: | ---: | ---: |
| 5 | 19,404 | 2,560 | $0.064408 | **$0.0645** |

F2 runs only after F1 passes and must pass before F3.

### F3 — V4 low-band repair regressions

| Case | Reviewed axes | Composite | Repair protected |
| --- | --- | ---: | --- |
| NFR follow-up supplies constraints | 5 / 1 / 1 | 3 | Missing secondary evidence cannot leak into Accuracy. |
| Identity follow-up supplies authorization | 5 / 1 / 2 | 3 | A guardrail without stated harm cannot activate Boundaries. |

| Calls | Counted input | Reserved output | Exact bound | Stage cap |
| ---: | ---: | ---: | ---: | ---: |
| 2 | 7,840 | 1,024 | $0.025920 | **$0.0260** |

## Cost and stop policy

| Scope | Calls | Counted input | Reserved output | Conservative bound |
| --- | ---: | ---: | ---: | ---: |
| F1 blocker | 1 | 3,906 | 512 | $0.012932 |
| F2 axis controls, conditional | 5 | 19,404 | 2,560 | $0.064408 |
| F3 repair regressions, conditional | 2 | 7,840 | 1,024 | $0.025920 |
| **Maximum V5 gate** | **8** | **31,150** | **4,096** | **$0.103260** |

The requested cumulative authorization is **$0.1033**. Runner commands receive
their upward-rounded stage caps. A failed stage leaves every later stage unspent;
there is no retry, effort escalation, or favorable-sample search.

The compact V5 correction adds 273 input tokens per case over V4, a maximum
$0.004368 input premium across this gate. An earlier draft added 358 tokens per
case; tightening it before the paid run reduced that delta by 23.7% and the total
eight-call ceiling by 1.3%.

Every response must:

- parse into the production schema;
- keep composite and every axis within one of its reviewed label;
- preserve the reviewed Accuracy pass/fail bucket;
- preserve V4's mandatory eligibility bands and two-endpoint attribution; and
- keep feedback consistent with the score and learner evidence.

A provider error, malformed response, automatic gate failure, manual evidence
contradiction, or budget issue stops the experiment after durable recording.

## What a pass would mean

An eight-case pass would show that V5 fixes the observed within-band Depth miss,
distinguishes reviewed 4/5 controls within tolerance, and preserves both V4
repairs. It would still not authorize production, Terra, or prompt distillation.
The next step would be the four remaining follow-up-anchored cases, then the
unrun release families behind the same stop gates.

## Run outcome

F1 fixed the V4 Depth blocker and F2 passed all five remaining axis-isolation
controls. F3 then stopped the experiment because V5 reopened the NFR Accuracy
leak: feedback called the mechanism correct and criticized only missing secondary
evidence, but Accuracy was 3 against reviewed 5. Production remained unchanged.
The full audit is in `CLAUDE-EXPLICIT-EVIDENCE-V5-RESULTS.md`.
