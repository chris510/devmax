# Explicit-evidence V8 symmetric-selection gate

**Status:** Prepared, tested, and free-counted; no paid Message calls made

## Objective

Correct V7's Depth leak without reopening its fixed Boundary behavior. V8 makes
the decision-threshold distinction symmetric: option-selection logic is
mechanism evidence only and cannot raise either secondary axis by itself.

V8 remains an evaluation-only overlay shared byte-for-byte by the Claude and
OpenAI runners. Production, provider, model, effort, request shape, schema,
parser, scheduler, and reviewed labels remain unchanged.

## Why V8

V7 correctly moved the heap-versus-shard branch from Boundaries 4 to reviewed
2. In the same focused stage, however, the self-corrected case received Depth 3
against reviewed 1. The learner named the two architectural options but stated
no cost, sacrifice, tension, or opposing benefit. The feedback then supplied
missing examples such as cross-node merge overhead while leaving Depth high.

The missing rule is therefore not topic-specific:

- choosing A or B from a threshold explains the mechanism;
- Depth additionally requires the learner to connect a choice to a cost or
  opposing benefit; and
- Boundaries additionally requires the learner to connect a wrong action,
  condition, or belief to harm or incorrect behavior.

## Candidate change

V8 is an independent unified contract, not V7 plus an appended patch. It keeps
the same five ordered steps while replacing V7's Boundary-only selection rule
with one symmetric rule:

> Selection logic is mechanism evidence only. An option or capacity branch must
> by itself stay Depth 0-2 and Boundaries 0-2.

The final check is symmetric too: if feedback supplies a missing cost or harm,
says the actual relationship was absent, or describes only selection logic, the
affected secondary axis must be lowered to 0-2 before return.

No Anthropic request, model identifier, structured-output field, token setting,
or cache behavior changed. The required `claude-api` skill is unavailable in
this session; the safe fallback was to leave Anthropic integration untouched
and constrain the change to evaluation-only rubric text.

## Deterministic controls

Tests establish that:

- V8 is a selectable prompt variant;
- selection logic alone is explicitly low for both Depth and Boundaries;
- high Depth requires a separate learner-stated choice/cost relationship;
- high Boundaries requires a separate learner-stated wrong-action/harm
  relationship;
- the final check lowers either secondary axis when feedback supplies missing
  evidence;
- the three decision calibrations preserve low/low, low/high, and high/low axis
  isolation;
- Claude and OpenAI receive byte-identical V8 rubric bytes; and
- V8 fingerprints cannot resume production or V7 results.

## Prompt-cost audit

V8 is 3,291 bytes, 64 bytes shorter than V7's 3,355-byte contract. Anthropic's
free counter measured the exact three-case gate under both candidates:

| Candidate | Calls | Counted input | Reserved output | Exact bound | Ceiling |
| --- | ---: | ---: | ---: | ---: | ---: |
| V7 | 3 | 9,969 | 1,536 | $0.035298 | $0.0353 |
| V8 | 3 | 9,909 | 1,536 | $0.035178 | **$0.0352** |

V8 saves 20 counted input tokens per call and $0.000120 across this gate. The
prior V7 outputs for these cases imply approximately $0.0300 if V8 response
lengths are similar, but the enforceable cap retains the 512-token reserve.

## L1 — three-case symmetric-selection gate

| Case | Reviewed axes | Composite | Behavior protected |
| --- | --- | ---: | --- |
| Decision self-corrected | 5 / 1 / 4 | 5 | Explicitly rejected ritual arithmetic remains high Boundaries while the option branch stays low Depth. |
| Decision follow-up supplies decision | 5 / 1 / 2 | 3 | Correct threshold selection stays low on both secondary axes. |
| Decision trade-off only | 5 / 5 / 1 | 4 | A learner-stated interview-time tension stays high Depth while absent failure evidence stays low Boundaries. |

| Calls | Counted input | Reserved output | Exact bound | Stage cap |
| ---: | ---: | ---: | ---: | ---: |
| 3 | 9,909 | 1,536 | $0.035178 | **$0.0352** |

The requested paid authorization after this preparation PR merges is
**$0.0352**.

## Pass and stop policy

Every response must:

- parse into the production schema;
- keep composite and every axis within one of its reviewed label;
- preserve the reviewed Accuracy pass/fail bucket;
- keep selection logic out of unsupported secondary bands; and
- keep feedback consistent with learner evidence and returned axes.

A provider error, malformed response, automatic gate failure, manual evidence
contradiction, or budget issue stops after durable recording. There is no retry,
effort escalation, prompt adjustment, or favorable-sample replacement.

After execution, a keyless exact-fingerprint replay must reproduce the result
without scheduling a new call.

## What a pass would unlock

A three-case pass would establish the local V8 correction, not authorize
production. The next sequence would be:

1. reopen the NFR and identity historical blockers under V8;
2. complete all remaining follow-up-anchored cases;
3. obtain three V8 observations of the critical blocker set;
4. run the 12 balanced risk-smoke cases;
5. run the remaining release-pack and frozen-baseline cases; and
6. compare Claude and OpenAI under the same approved contract before proposing
   a production implementation.

Every later phase retains its own free count, paid authorization, manual audit,
durable result PR, and stop policy.
