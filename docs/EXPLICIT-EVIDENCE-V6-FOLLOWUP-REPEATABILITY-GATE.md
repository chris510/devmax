# Explicit-evidence V6 follow-up and repeatability gate

**Status:** Prepared and free-counted; no paid Message calls made

## Objective

Complete the follow-up-anchored family that V6 has not yet seen, then measure
intentional repeatability on the three historical blockers. This is an
evaluation-only experiment. Production, provider, model, effort, request shape,
schema, parser, scheduler, reviewed labels, and the V6 prompt remain unchanged.

The experiment has two distinct questions:

1. Does V6 correctly score current answer evidence when the decisive mechanism
   arrives in the single allowed follow-up across the four remaining topics?
2. Do the three historical blocker results remain stable across three total V6
   observations rather than passing once by chance?

The original V6 observation for each blocker comes from
`CLAUDE-EXPLICIT-EVIDENCE-V6-RESULTS.md`. Two new fresh replicas bring each
blocker to three observations. Resume is deliberately disabled for both
replicas, and each writes a distinct durable result file.

## Deterministic controls

Every stage uses Claude Sonnet 5, low effort, concurrency 1, approved grounding,
the production score schema, `explicit-evidence-v6`, and
`--enforce-reviewed-gate`. The runner already provides:

- exact case-name selection with approved labels;
- prompt fingerprints that include the V6 overlay;
- free Anthropic input-token counting before any Message call;
- a required per-stage cost acknowledgement;
- durable JSONL recording before aggregate gate evaluation; and
- automatic composite, axis, and Accuracy pass/fail checks.

No runner or prompt change is needed. Separate commands are preferable to a new
repeat flag because they make every paid sample, cap, stop point, and result
file explicit.

## H1 — remaining follow-up-anchored coverage

| Case | Reviewed axes | Composite | Behavior protected |
| --- | --- | ---: | --- |
| Delivery sequence — follow-up supplies ordering | 5 / 1 / 1 | 3 | The follow-up's complete delivery order counts as mechanism evidence without inventing secondary evidence. |
| Timeout retry — follow-up supplies idempotency | 5 / 2 / 2 | 3 | Unknown outcome and same-key handling count, while brief mentions stay in the low secondary bands. |
| Cursor pagination — follow-up supplies boundary | 5 / 1 / 2 | 3 | The follow-up's ordered-key boundary repairs Accuracy without promoting an implied trade-off or harm. |
| Decision-driven estimation — follow-up supplies decision | 5 / 1 / 2 | 3 | The follow-up's quantity and decision branch count as mechanism evidence, not Depth by mere option naming. |

Free preflight selected four new calls and counted 12,452 input tokens. With a
512-token output reserve per call, the exact conservative bound is $0.045384;
the stage cap is **$0.0454**.

H1 must pass its automatic gate and manual feedback/evidence audit before the
first repeatability replica runs.

## R1 and R2 — fresh historical-blocker replicas

Each replica contains the same three reviewed cases but makes independent paid
calls and records a separate result file:

| Case | Reviewed axes | Composite | V6 observation 1 |
| --- | --- | ---: | --- |
| NFR follow-up supplies constraints | 5 / 1 / 1 | 3 | 4 / 2 / 2 · composite 3 |
| Identity follow-up supplies authorization | 5 / 1 / 2 | 3 | 4 / 1 / 2 · composite 3 |
| Decision-driven estimation — trade-off only | 5 / 5 / 1 | 4 | 5 / 4 / 1 · composite 4 |

Free preflight selected three new calls and counted 9,355 input tokens per
replica. With the same output reserve, each replica's exact conservative bound
is $0.034070; each stage cap is **$0.0341**.

R1 must pass its automatic and manual gates before R2 runs. There is no retry,
effort escalation, prompt adjustment, or favorable-sample replacement after a
failure.

## Cost ledger and authorization

Published promotional pricing through 2026-08-31 is $2 per million input tokens
and $10 per million output tokens.

| Stage | Calls | Counted input | Reserved output | Exact bound | Stage cap |
| --- | ---: | ---: | ---: | ---: | ---: |
| H1 — remaining follow-ups | 4 | 12,452 | 2,048 | $0.045384 | **$0.0454** |
| R1 — blocker replica 1 | 3 | 9,355 | 1,536 | $0.034070 | **$0.0341** |
| R2 — blocker replica 2, conditional | 3 | 9,355 | 1,536 | $0.034070 | **$0.0341** |
| **Maximum experiment** | **10** | **31,162** | **5,120** | **$0.113524** | **$0.1136** |

The requested cumulative paid authorization after this preparation PR merges is
**$0.1136**. V6's prior observed average of $0.008856 per call suggests roughly
$0.0886 for ten calls, but that is a forecast, not a spending guarantee. The
stage caps retain the conservative output reserve and are the enforceable
limits.

## Pass, variance, and stop policy

Every response must:

- parse into the production schema;
- keep composite and every axis within one of its reviewed label;
- preserve the reviewed Accuracy pass/fail bucket;
- obey V6's mandatory low/high relationship bands; and
- keep feedback consistent with the learner's actual evidence and frozen axes.

A provider error, malformed response, automatic gate failure, manual evidence
contradiction, or budget issue stops the experiment after durable recording.
Unspent later stages remain uncalled.

The nine blocker observations across the original run, R1, and R2 receive a
separate repeatability classification:

- **Repeatability pass:** every observation passes the gates and each case's
  composite and per-axis range across its three samples is at most one point.
- **Safety-stable but calibration-variable:** every observation passes the
  gates and Accuracy buckets remain stable, but any three-sample range is two
  points. V6 remains evaluation-only.
- **Fail:** any automatic or manual gate fails, including an Accuracy bucket
  change. Stop immediately; do not sample around it.

Report exact-match counts, within-one counts, false Accuracy passes/failures,
per-case axis/composite ranges, manual feedback findings, token usage, actual
cost, and unspent authorization.

## Audited run sequence

The paid runner commands use the four or three exact `--case` selections listed
above, plus:

```text
--grounding-manifest cards.json
--levels low
--concurrency 1
--scoring-prompt-variant explicit-evidence-v6
--enforce-reviewed-gate
--fresh
```

H1, R1, and R2 write respectively to:

```text
.eval-results/claude-explicit-evidence-v6-h1-followups.jsonl
.eval-results/claude-explicit-evidence-v6-r1-blockers.jsonl
.eval-results/claude-explicit-evidence-v6-r2-blockers.jsonl
```

Before each paid stage, rerun the exact selection with `--dry-run` and confirm
the count does not exceed that stage's prepared cap. After each stage, inspect
all feedback and evidence before continuing. Finally, unset the Anthropic key
and replay each durable file by exact fingerprint to prove that the recorded
results, rather than new calls, reproduce the gates.

## What a pass would mean

A full pass would cover all six follow-up-anchored topics and give the three
historical blockers three V6 observations each with no greater than one-point
sample spread. It would justify moving V6 to the remaining release families and
frozen baseline. It would still not authorize production, a provider change,
or prompt distillation.
