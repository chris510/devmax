# Explicit-evidence V3 continuous workstream

**Status:** Prepared and free-preflighted; no paid V3 calls made

## Objective

Stabilize the shared scoring contract on Claude, validate it across all 60
frozen reviewed scoring cases, then distill the successful diagnostic prompt
and recheck the 12 highest-risk cases. Do this as one continuous workstream
without requiring a user merge between every stage.

Production remains unchanged. `explicit-evidence-v3` is an evaluation-only
overlay shared byte-for-byte by the Claude and OpenAI runners. No provider,
model, effort, schema, parser, scheduler, or approved label changes in this PR.

## Why V3 exists

V2 fixed the original false-positive Boundaries problem but failed the symmetric
Depth requirement. On the decision-driven-estimation case, Claude's feedback
acknowledged that skipping unrelated arithmetic saves interview time, yet the
model assigned Depth 2 against approved 5.

V2 made absent evidence a hard 0–2 ceiling, but treated present evidence as
permission rather than a 3–5 floor. V3 makes the eligibility bands mandatory in
both directions:

| Axis | No correct explicit relationship | At least one correct explicit relationship |
| --- | --- | --- |
| Depth | **Must be 0–2** | **Must be 3–5** |
| Boundaries | **Must be 0–2** | **Must be 3–5** |

Depth requires a learner-stated choice/target/approach connected to a cost,
sacrifice, tension, or opposing benefit. Boundaries requires a learner-stated
trigger/action/exception/limitation/misconception connected to a concrete adverse
outcome or incorrect behavior.

Completeness and specificity choose the value within the eligible band; they
cannot move a correct explicit relationship below 3. Missing mechanism detail
affects Accuracy or completeness within 3–5, not whether an independent
trade-off exists. Conversely, context, grounding, implication, and feedback
cannot fill in a missing learner relationship.

The feedback consistency check is also bidirectional:

- if feedback acknowledges a qualifying learner relationship, its axis must be
  3–5;
- if feedback supplies the relationship as correction, its axis must be 0–2.

## Automatic stop gate

The Claude runner now has opt-in `--enforce-reviewed-gate`. After every response
is durably written, it exits nonzero if any of these occur:

- a composite is more than one point from its frozen reviewed label;
- any axis is more than one point from its frozen reviewed label;
- Accuracy crosses the reviewed pass/fail boundary; or
- a selected case is missing a reviewed composite or axis label.

The continuous workflow uses this flag with shell fail-fast behavior. A failed
stage cannot silently proceed to the next paid command. After each quantitative
pass, the raw feedback is manually checked for learner-evidence attribution
before work continues. The user does not need to approve each successful stage.

The gate was replay-tested against the exact keyless V2 B2 result that motivated
V3. It resumed all three records, scheduled zero paid calls, identified Depth 2
as more than one point from approved 5, and exited with status 1. The stop path is
therefore exercised rather than only unit-tested.

## Staged 60-case Claude regression

All stages use Claude Sonnet 5, low effort, concurrency 1, the production score
schema, approved grounding, and V3. The figures below came from Anthropic's free
token counter. They are conservative authorization ceilings, not forecasts.

| Stage | Reviewed cases | Calls | Counted input | Reserved output | Ceiling |
| --- | --- | ---: | ---: | ---: | ---: |
| C1 | Failed V2 trade-off activation case | 1 | 3,123 | 512 | **$0.0114** |
| C2 | Original false-positive Boundaries case | 1 | 3,094 | 512 | **$0.0114** |
| C3 | Remaining axis-isolation cases | 5 | 15,489 | 2,560 | **$0.0566** |
| C4 | Remaining speech-noise cases | 5 | 15,371 | 2,560 | **$0.0564** |
| C5a | Adjacent jargon | 6 | 18,597 | 3,072 | **$0.0680** |
| C5b | Follow-up anchored | 6 | 18,782 | 3,072 | **$0.0683** |
| C5c | Partial self-correction | 6 | 18,684 | 3,072 | **$0.0681** |
| C5d | Prior-summary contradiction | 6 | 18,650 | 3,072 | **$0.0681** |
| C5e | Source-compatible alternative | 6 | 18,702 | 3,072 | **$0.0682** |
| C6 | Frozen complete/mechanism-only/incorrect baseline | 18 | 55,715 | 9,216 | **$0.2036** |
| **Full V3 regression** | **60 unique cases** | **60** | **186,207** | **30,720** | **$0.6797** |

Stages proceed in this order. C1 proves the new positive floor before C2 checks
that it did not reopen the original false positive. C3 tests both secondary axes,
C4 protects voice-noise handling, the five C5 families isolate transcript/context
edge cases, and C6 finishes with the original reviewed baseline.

Every stage stops on the automatic gate, manual feedback contradiction, malformed
response, provider error, or cumulative budget breach. A failure is recorded and
the remaining ceiling stays unspent; there is no retry or higher-effort search for
a favorable sample.

## Prompt distillation reserve

Only if all 60 V3 cases pass, the diagnostic wording may be shortened into a new
versioned compact candidate. The compact prompt must count fewer input tokens
than V3 on every critical completion and preserve the same mandatory bands.

The compact candidate is then tested on the 12 combined `axis-isolation` and
`speech-noise` cases:

| Stage | Calls | V3 input upper bound | Reserved output | Ceiling |
| --- | ---: | ---: | ---: | ---: |
| D1 compact critical regression | 12 | 37,077 | 6,144 | **$0.1356** |

Using V3 rather than an unknown future compact count makes this a safe upper
bound. If the compact prompt is not actually shorter, D1 does not run.

## One cumulative authorization

| Scope | Calls | Exact conservative bound | Upward-rounded cap |
| --- | ---: | ---: | ---: |
| Full V3 Claude regression | 60 | $0.679614 | $0.6797 |
| Compact critical reserve | 12 | $0.135594 | $0.1356 |
| **Maximum continuous workstream** | **72** | **$0.815208** | **$0.8153** |

The requested single authorization is therefore **$0.8153**. Each runner command
still receives its own printed stage cap, while the workstream tracks cumulative
actual cost against the one total. Unused stage or distillation reserve is never
spent.

Applying V2's observed average output length to these counts gives a rough
working projection near $0.65 for the full regression plus compact reserve. That
is not a billing promise: the $0.8153 bound is the safety control, and provider
billing remains authoritative.

## What a complete pass would mean

A 60-case V3 pass plus a 12-case compact pass would establish that Claude can:

- preserve every scheduler-critical Accuracy pass/fail decision;
- keep each reviewed composite and axis within one;
- withhold secondary credit when the learner supplies no relationship;
- activate secondary credit when the learner supplies a correct relationship;
- separate current answers from questions, grounding, feedback, and stale
  mastery summaries; and
- do so with a shorter prompt on the highest-risk subset.

It would not yet authorize Terra or a production change. The consolidated result
would support the next decision: repeatability on the risk subset, followed by a
same-contract Terra comparison or initial Claude prompt promotion.
