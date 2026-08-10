# Explicit-evidence V8 follow-up and repeatability gate

**Status:** Stopped at M1 after one automatic and manual gate failure; see
`CLAUDE-EXPLICIT-EVIDENCE-V8-FOLLOWUP-RESULTS.md`

## Objective

Complete the remaining follow-up-anchored cases under V8, then measure fresh
repeatability on the three historical blockers. This is an evaluation-only
experiment. Production, provider, model, effort, request shape, schema, parser,
scheduler, reviewed labels, and the V8 prompt remain unchanged.

The experiment answers two separate questions:

1. Does V8 correctly score the decisive mechanism when it arrives in the
   single allowed follow-up across every topic?
2. Do the three historical blocker cases remain stable across three total V8
   observations rather than passing once by chance?

The merged L1 run already supplies the first decision trade-off observation.
M1 supplies the first NFR and identity observations while completing the other
three unseen follow-up cases. M2 and M3 make fresh calls for all three blockers,
bringing each to three V8 observations.

## Scope and external-data boundary

The proposed paid run makes 11 synthetic evaluation payload transmissions,
covering six unique cases plus their repository grounding context: question,
answer basis, and reviewed rubric. Three blocker cases are intentionally called
again for repeatability. The payloads contain no learner data, production
records, credentials, or private user content.

The prior authorization covered only the three merged L1 cases. It does not
cover these 11 calls. This preparation performs no Anthropic request or token
count. After this PR merges, paid execution requires explicit destination-
specific authorization to send the M1-M3 synthetic payloads to Anthropic,
capped at **$0.1292**.

## Deterministic controls

Every stage uses Claude Sonnet 5, low effort, concurrency 1, approved grounding,
the production score schema, `explicit-evidence-v8`, and
`--enforce-reviewed-gate`. The runner provides:

- exact case-name selection with approved labels;
- prompt fingerprints that include the V8 overlay;
- free Anthropic input-token counting before any Message call;
- a required per-stage cost acknowledgement;
- durable JSONL recording before aggregate gate evaluation; and
- automatic composite, axis, and Accuracy pass/fail checks.

M2 and M3 use `--fresh` and separate durable files. There is no hidden retry or
repeat loop.

## Exact prepared counts

No case was newly transmitted for this preparation. The input counts below are
exact derivations from already measured per-case counts and prompt deltas. V7
added a measured constant 214 tokens per call over V6. V8 is a shared constant
prompt change that Anthropic counted at exactly 20 fewer input tokens per call
than V7 in the merged L1 preflight. The NFR, identity, and decision values begin
with their measured V7 counts; the other three begin with measured V6 counts,
add 214, then subtract 20. The case payloads are otherwise unchanged.

| Case | Established V7 input | V8 delta | Prepared V8 input |
| --- | ---: | ---: | ---: |
| NFR follow-up supplies constraints | 3,343 | -20 | **3,323** |
| Identity follow-up supplies authorization | 3,331 | -20 | **3,311** |
| Delivery follow-up supplies ordering | 3,350 | -20 | **3,330** |
| Timeout retry follow-up supplies idempotency | 3,316 | -20 | **3,296** |
| Cursor pagination follow-up supplies boundary | 3,303 | -20 | **3,283** |
| Decision-driven estimation — trade-off only | 3,323 | -20 | **3,303** |

Before each paid stage, the runner must perform a fresh free count on the exact
selection and refuse to proceed if it exceeds the prepared stage cap. A lower
fresh count does not enlarge the authorized scope or permit extra calls.

## M1 — complete follow-up-anchored coverage

| Case | Reviewed axes | Composite | Behavior protected |
| --- | --- | ---: | --- |
| NFR follow-up supplies constraints | 5 / 1 / 1 | 3 | The follow-up's concrete constraints repair Accuracy without inventing secondary evidence. |
| Identity follow-up supplies authorization | 5 / 1 / 2 | 3 | Resource authorization counts as mechanism evidence while an unstated exploit remains low Boundaries. |
| Delivery sequence — follow-up supplies ordering | 5 / 1 / 1 | 3 | The complete delivery order counts without unsupported Depth or Boundaries. |
| Timeout retry — follow-up supplies idempotency | 5 / 2 / 2 | 3 | Unknown outcome and same-key handling count while brief mentions stay in low secondary bands. |
| Cursor pagination — follow-up supplies boundary | 5 / 1 / 2 | 3 | The ordered-key boundary repairs Accuracy without promoting an implied trade-off or harm. |

L1 already passed the decision follow-up. A passing M1 therefore completes all
six follow-up-anchored topics under V8.

| Calls | Prepared input | Reserved output | Exact bound | Stage cap |
| ---: | ---: | ---: | ---: | ---: |
| 5 | 16,543 | 2,560 | $0.058686 | **$0.0587** |

M1 must pass its automatic gate and manual feedback/evidence audit before M2
runs.

## M2 and M3 — fresh blocker repeatability

Each stage independently calls the same three approved cases:

| Case | Reviewed axes | Composite | V8 observation 1 |
| --- | --- | ---: | --- |
| NFR follow-up supplies constraints | 5 / 1 / 1 | 3 | Supplied by M1 |
| Identity follow-up supplies authorization | 5 / 1 / 2 | 3 | Supplied by M1 |
| Decision-driven estimation — trade-off only | 5 / 5 / 1 | 4 | 5 / 4 / 2 · composite 4 from L1 |

| Stage | Calls | Prepared input | Reserved output | Exact bound | Stage cap |
| --- | ---: | ---: | ---: | ---: | ---: |
| M2 — observation 2 | 3 | 9,937 | 1,536 | $0.035234 | **$0.0353** |
| M3 — observation 3, conditional | 3 | 9,937 | 1,536 | $0.035234 | **$0.0353** |

M2 must pass its automatic and manual gates before M3 runs. Both stages use
fresh calls and distinct result files; an earlier result cannot be resumed as a
new repeatability observation.

## Cost ledger and authorization

Pricing uses the published promotional schedule through 2026-08-31: $2 per
million input tokens and $10 per million output tokens.

| Stage | Calls | Input | Reserved output | Exact bound | Stage cap |
| --- | ---: | ---: | ---: | ---: | ---: |
| M1 — remaining follow-ups | 5 | 16,543 | 2,560 | $0.058686 | **$0.0587** |
| M2 — blocker observation 2 | 3 | 9,937 | 1,536 | $0.035234 | **$0.0353** |
| M3 — blocker observation 3, conditional | 3 | 9,937 | 1,536 | $0.035234 | **$0.0353** |
| **Maximum experiment** | **11** | **36,417** | **5,632** | **$0.129154** | **$0.1292** |

Comparable prior outputs suggest approximately $0.108164 for all 11 calls.
That is a forecast, not a guarantee. The reserved-output bounds and upward-
rounded stage caps are enforceable. A failed stage leaves every later stage
uncalled and unspent.

## Pass, variance, and stop policy

Every response must:

- parse into the production schema;
- keep composite and every axis within one of its reviewed label;
- preserve the reviewed Accuracy pass/fail bucket;
- obey V8's symmetric selection and evidence rules; and
- keep feedback consistent with the learner's actual evidence and returned
  axes.

A provider error, malformed response, automatic gate failure, manual evidence
contradiction, or budget issue stops the experiment after durable recording.
There is no retry, effort escalation, prompt adjustment, or favorable-sample
replacement. Later stages remain uncalled.

The nine blocker observations across M1/L1, M2, and M3 receive a separate
repeatability classification:

- **Repeatability pass:** every observation passes the gates and each case's
  composite and per-axis range across three samples is at most one point.
- **Safety-stable but calibration-variable:** every observation passes the
  gates and Accuracy buckets remain stable, but any three-sample range is two
  points. V8 remains evaluation-only.
- **Fail:** any automatic or manual gate fails, including an Accuracy bucket
  change. Stop immediately; do not sample around it.

Report exact-match and within-one counts, false Accuracy passes/failures,
per-case axis/composite ranges, manual feedback findings, token usage, actual
cost, and unspent authorization.

## Audited run sequence

The paid runner commands use the exact `--case` selections above, plus:

```text
--grounding-manifest cards.json
--levels low
--concurrency 1
--scoring-prompt-variant explicit-evidence-v8
--enforce-reviewed-gate
--fresh
```

M1, M2, and M3 write respectively to:

```text
.eval-results/claude-explicit-evidence-v8-m1-followups.jsonl
.eval-results/claude-explicit-evidence-v8-m2-blockers.jsonl
.eval-results/claude-explicit-evidence-v8-m3-blockers.jsonl
```

Before each paid stage, rerun the exact selection with `--dry-run`, verify the
fresh token count against the stage cap, and acknowledge only that stage's
cost. After each stage, inspect every feedback statement against the answer and
grounding before continuing. Finally, unset the Anthropic key and replay each
durable file by exact fingerprint to prove that the recorded results reproduce
the gates without new calls.

## What a full pass would mean

A full pass would complete all six follow-up-anchored topics and give the three
historical blockers three V8 observations with no greater than one-point sample
spread. It would justify moving V8 to the broader release evidence, not to
production.

The remaining sequence would be:

1. the 12 balanced speech-noise and adjacent-jargon risk-smoke cases;
2. the other 22 still-unseen release-pack cases, including alternatives,
   partial self-correction, stale-summary isolation, and remaining axis
   controls;
3. the frozen 18-case baseline; and
4. a matched Claude/OpenAI decision under the same approved contract and case
   subset, including quality, repeatability, latency, and actual cost.

Only after those gates pass should the winning prompt/provider be proposed for
production. Each future phase retains its own preparation PR, free count,
destination-specific paid authorization, manual audit, durable result PR, and
stop policy.

## Run outcome

M1 spent $0.050366 across its five authorized calls. Four cases passed, but the
NFR follow-up returned Boundaries 4 and composite 5 against reviewed 1 and 3.
Its feedback identified correct mechanism evidence and a missing trade-off but
no learner-stated failure relationship, so the manual audit failed as well.
The experiment stopped before M2 and M3, leaving $0.078834 of the cumulative
authorization unspent. A keyless replay reproduced the failure with zero new
calls. Production remained unchanged.
