# Claude explicit-evidence V6 unified-gate results

**Run date:** 2026-08-09 (America/Los_Angeles)

**Decision:** Passed targeted gate; broader regression and repeatability remain

## What ran

V6 evaluated five historical blockers/closest controls, then four uncovered
axis-isolation controls after the first stage passed. Every call used Claude
Sonnet 5, low effort, concurrency 1, approved grounding, the production schema,
and the automatic reviewed gate. Production remained unchanged.

| Control | Value |
| --- | --- |
| Prompt | Production rubric plus evaluation-only `explicit-evidence-v6` |
| Cumulative authorized cap | **$0.101900** |
| Paid calls | **9** |
| Calculated spend | **$0.079706** |
| Unspent authorization | **$0.022194** |
| Production change | None |

Both fresh free preflights matched the prepared counts and stayed within their
stage caps.

## Stage ledger

| Stage | Calls | Input | Output | Actual cost | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| G1 — blockers and closest controls | 5 | 15,541 | 1,232 | $0.043402 | Pass |
| G2 — uncovered axis controls | 4 | 12,327 | 1,165 | $0.036304 | Pass |
| **Total** | **9** | **27,868** | **2,397** | **$0.079706** | **Pass** |

Cost uses the published promotional schedule through 2026-08-31: $2 per
million input tokens and $10 per million output tokens. No cache tokens were
read or written.

## Aggregate gate result

| Criterion | Nine-call result | Gate |
| --- | ---: | --- |
| Structured responses parsed | 9/9 | Pass |
| False Accuracy passes / failures | 0 / 0 | Pass safety boundary |
| Composite exact / within one | 8/9 · **9/9** | Pass |
| Mean composite deviation | 0.11 | Pass |
| Accuracy exact / within one | 6/9 · **9/9** | Pass |
| Depth exact / within one | 6/9 · **9/9** | Pass |
| Boundaries exact / within one | 8/9 · **9/9** | Pass |
| Feedback/evidence audit | **9/9 consistent** | Pass |

## Case matrix

| Case | Reviewed axes | V6 axes | Composite | Audit |
| --- | --- | --- | --- | --- |
| NFR follow-up constraints | 5 / 1 / 1 | 4 / 2 / 2 | 3 → 3 | Accuracy leak fixed within tolerance; absent secondary evidence stayed below 3. |
| Identity follow-up authorization | 5 / 1 / 2 | 4 / 1 / 2 | 3 → 3 | Guardrail without learner-stated harm stayed below Boundaries 3. |
| Decision-driven trade-off | 5 / 5 / 1 | 5 / 4 / 1 | 4 → 4 | Full trade-off received Depth 4 while missing failure stayed low. |
| NFR fluent checklist | 1 / 0 / 0 | 0 / 0 / 0 | 1 → 0 | Incorrect generic mechanism remained failing. |
| Identity failure boundary | 5 / 1 / 5 | 5 / 1 / 5 | 5 → 5 | Explicit action and IDOR harm received Boundaries 5. |
| Delivery-sequence trade-off | 5 / 4 / 1 | 5 / 4 / 1 | 4 → 4 | Exact axes; clear single trade-off received Depth 4. |
| Timeout-retry trade-off | 5 / 5 / 1 | 5 / 5 / 1 | 4 → 4 | Exact axes; both explicit trade-offs received Depth 5. |
| NFR failure boundary | 5 / 1 / 4 | 5 / 2 / 4 | 5 → 5 | Checklist mistake and architectural harm received Boundaries 4. |
| Cursor failure boundary | 5 / 1 / 5 | 5 / 1 / 5 | 5 → 5 | Exact axes; offset drift and duplicate/skip harm received Boundaries 5. |

## What V6 establishes

V6 is the first candidate to satisfy all of these in one sample:

- keep correct mechanism Accuracy independent of missing secondary evidence;
- keep an identity guardrail without stated harm below high Boundaries;
- award a complete decision-driven trade-off Depth 4-5;
- keep fluent but incorrect mechanism language in the failure bucket;
- activate all three explicit trade-off controls;
- activate all three explicit failure-mode controls;
- keep unsupported opposite axes below 3; and
- align every feedback statement with learner evidence and frozen axes.

It does this with 797 fewer input tokens per case than V5. The nine-case input
count fell 20.5%, and the conservative total bound fell 12.3% before execution.
Actual spend averaged $0.008856 per call and used 78.2% of the authorized cap.

## Durable pass proof

Separate keyless replays loaded the five G1 and four G2 result files with the
Anthropic key unset. They resumed 9/9 exact fingerprints, scheduled zero paid
calls, printed $0.0000 new-call ceilings, and passed the reviewed gate again.
The result is therefore durably reproducible from the recorded responses.

## What this does not prove

Each case was sampled once under V6. The gate does not yet prove repeatability,
the other four follow-up-anchored topics, speech-noise and adjacent-jargon
families across every card, stale-summary isolation, partial self-correction,
source-compatible alternatives, or the frozen 18-case baseline. It also does
not authorize production, Terra, or an OpenAI provider change.

## Decision and next experiment

Keep V6 evaluation-only. Do not promote or compare providers yet.

The next cost-efficient workstream should combine:

1. the four follow-up-anchored cases not represented here, protecting current
   answer evidence from question anchoring across the remaining topics; and
2. an explicit repeatability stage on the three historical blockers, using fresh
   calls rather than resume so variance is measured intentionally.

Those stages need reviewed automatic gates, manual feedback audits, fresh free
counts, and a new paid authorization. Only after they pass should V6 resume the
remaining release families and frozen baseline. Production remains unchanged
until the broader regression and repeatability both pass.

The audited case set, three-stage stop policy, variance classification, and
free-counted $0.1136 maximum are prepared in
`EXPLICIT-EVIDENCE-V6-FOLLOWUP-REPEATABILITY-GATE.md`. No paid calls were made
during preparation.
