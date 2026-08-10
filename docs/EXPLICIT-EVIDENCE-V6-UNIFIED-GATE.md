# Explicit-evidence V6 unified-contract gate

**Status:** Prepared and free-preflighted; no paid V6 calls made

## Objective

Replace the accumulated V3+V4+V5 evaluation overlay with one compact ordered
decision procedure. V6 expresses the same intended contract without historical
patch layers:

1. score and freeze Accuracy from mechanism correctness/completeness only;
2. identify both learner-stated endpoints for Depth and Boundaries;
3. assign the mandatory low/high eligibility band;
4. calibrate eligible secondary evidence across 3/4/5; and
5. verify feedback against the frozen axes before return.

Production, provider, model, effort, request shape, schema, parser, scheduler,
and reviewed labels remain unchanged. `explicit-evidence-v6` is an
evaluation-only overlay shared byte-for-byte by the Claude and OpenAI runners.

## Why V6 replaces rather than extends V5

V5 fixed all six axis-isolation calibrations but reopened the NFR Accuracy leak.
The model called the mechanism correct, criticized only missing secondary
evidence, then returned Accuracy 3 against reviewed 5. V3 had produced the same
3, V4 improved it to 4, and the appended V5 paragraph regressed it to 3.

That pattern points to instruction competition in the accumulated overlay. V6
therefore contains none of the V3, V4, or V5 candidate text. It keeps the learned
rules once, in evaluation order, with three critical calibrations.

## Deterministic controls

Tests establish that:

- V6 contains the five ordered scoring steps;
- Accuracy is frozen before secondary scoring;
- both relationship endpoints must come from learner text;
- absent and present secondary relationships enter mandatory 0-2 and 3-5 bands;
- eligible 3/4/5 calibration is explicit;
- feedback cannot overwrite frozen axes;
- the NFR, identity, and decision-driven historical blockers are calibrated;
- V6 does not contain the V3 candidate heading and is byte-shorter than V5;
- Claude and OpenAI receive a byte-identical V6 rubric; and
- V6 fingerprints cannot resume production or V1-V5 results.

No Anthropic request, model identifier, structured-output field, token setting,
or caching behavior changed.

## Verified prompt reduction

Anthropic's free counter measured V5 and V6 separately for every paid-gate case:

| Case | V5 input | V6 input | Saved |
| --- | ---: | ---: | ---: |
| NFR follow-up constraints | 3,926 | 3,129 | 797 |
| Identity follow-up authorization | 3,914 | 3,117 | 797 |
| Decision-driven trade-off | 3,906 | 3,109 | 797 |
| NFR fluent checklist | 3,891 | 3,094 | 797 |
| Identity failure boundary | 3,889 | 3,092 | 797 |
| Delivery-sequence trade-off | 3,894 | 3,097 | 797 |
| Timeout-retry trade-off | 3,872 | 3,075 | 797 |
| NFR failure boundary | 3,896 | 3,099 | 797 |
| Cursor failure boundary | 3,853 | 3,056 | 797 |
| **Total** | **35,041** | **27,868** | **7,173** |

V6 reduces counted input by 20.5%. With the same conservative output reserve,
the nine-call bound falls from $0.116162 to $0.101816, a 12.3% reduction.

## Staged paid gate

Every stage uses Claude Sonnet 5, low effort, concurrency 1, approved grounding,
the production score schema, and `--enforce-reviewed-gate`. Manual
feedback/evidence audit is required before the next stage.

### G1 — historical blockers and closest controls

| Case | Reviewed axes | Composite | Behavior protected |
| --- | --- | ---: | --- |
| NFR follow-up constraints | 5 / 1 / 1 | 3 | Correct mechanism stays Accuracy 4-5 without secondary evidence. |
| Identity follow-up authorization | 5 / 1 / 2 | 3 | A guardrail without stated harm stays below Boundaries 3. |
| Decision-driven trade-off | 5 / 5 / 1 | 4 | Complete explicit trade-off receives Depth 4-5. |
| NFR fluent checklist | 1 / 0 / 0 | 1 | Incorrect mechanism remains failing despite fluent vocabulary. |
| Identity failure boundary | 5 / 1 / 5 | 5 | Explicit action and IDOR harm receive Boundaries 4-5. |

| Calls | Counted input | Reserved output | Exact bound | Stage cap |
| ---: | ---: | ---: | ---: | ---: |
| 5 | 15,541 | 2,560 | $0.056682 | **$0.0567** |

G1 must pass every automatic and manual gate before G2 runs.

### G2 — uncovered axis-isolation controls

| Case | Reviewed axes | Composite | Behavior protected |
| --- | --- | ---: | --- |
| Delivery-sequence trade-off | 5 / 4 / 1 | 4 | Clear single trade-off remains within one of reviewed 4. |
| Timeout-retry trade-off | 5 / 5 / 1 | 4 | Complete state/delay trade-offs receive Depth 4-5. |
| NFR failure boundary | 5 / 1 / 4 | 5 | Checklist mistake and harm remain within one of reviewed 4. |
| Cursor failure boundary | 5 / 1 / 5 | 5 | Offset drift and duplicate/skip harm receive Boundaries 4-5. |

| Calls | Counted input | Reserved output | Exact bound | Stage cap |
| ---: | ---: | ---: | ---: | ---: |
| 4 | 12,327 | 2,048 | $0.045134 | **$0.0452** |

The nine unique cases cover the complete eight-case V5 gate plus the incorrect
NFR mechanism control. Four repeated calls were deliberately removed; a later
repeatability experiment must be explicit rather than hidden inside regression.

## Cost and stop policy

| Scope | Calls | Counted input | Reserved output | Conservative bound |
| --- | ---: | ---: | ---: | ---: |
| G1 blockers and closest controls | 5 | 15,541 | 2,560 | $0.056682 |
| G2 remaining axis controls, conditional | 4 | 12,327 | 2,048 | $0.045134 |
| **Maximum V6 gate** | **9** | **27,868** | **4,608** | **$0.101816** |

The requested cumulative authorization is **$0.1019**. Runner commands receive
their upward-rounded stage caps. A G1 failure leaves G2 unspent; there is no
retry, effort escalation, or favorable-sample search.

Every response must:

- parse into the production schema;
- keep composite and every axis within one of its reviewed label;
- preserve the reviewed Accuracy pass/fail bucket;
- obey the mandatory relationship bands and within-band calibration; and
- keep feedback consistent with frozen axes and learner evidence.

A provider error, malformed response, automatic gate failure, manual evidence
contradiction, or budget issue stops the experiment after durable recording.

## What a pass would mean

A nine-case pass would show that the unified contract handles every historical
blocker and all axis-isolation controls while materially reducing prompt cost. It
would still not authorize production, Terra, or prompt distillation. The next
steps would be the four remaining follow-up-anchored cases, then repeatability on
the critical subset before broader release families or a provider decision.
