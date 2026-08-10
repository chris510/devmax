# Explicit-evidence V4 targeted gate

**Status:** E1 passed; E2 failed the reviewed Depth tolerance. See
`CLAUDE-EXPLICIT-EVIDENCE-V4-RESULTS.md`.

## Objective

Test the smallest prompt correction that directly addresses the two blocking V3
C5b failures without changing production. V4 keeps the complete V3 overlay and
appends two return-time invariants:

1. Choose and freeze Accuracy from mechanism correctness and completeness before
   scoring Depth or Boundaries. Missing secondary evidence cannot reduce it.
2. Before a secondary axis can reach 3-5, both endpoints and the relationship
   between them must appear in learner `ANSWER:` text. Feedback must paraphrase
   those learner-stated endpoints or lower the axis to 0-2.

Production, provider, model, effort, request shape, output schema, parser,
scheduler, and reviewed labels remain unchanged. `explicit-evidence-v4` is an
evaluation-only overlay shared byte-for-byte by the Claude and OpenAI runners.

## Why this delta

V3 stopped after 24 calls for two different contract violations:

- a correct NFR follow-up received Accuracy 3 instead of reviewed 5 because the
  learner omitted trade-off and failure evidence; and
- an identity guardrail with no learner-stated adverse outcome received
  Boundaries 4 instead of reviewed 2, moving composite 3 to 5.

V4 does not add a new scoring theory. It freezes the already independent
Accuracy decision and turns V3's failure relationship into a mechanical
two-endpoint check. It includes one positive and one negative calibration for
each observed risk.

## Deterministic controls

Tests establish that:

- V4 includes all V3 bidirectional-band rules;
- Accuracy is frozen before secondary scoring;
- both secondary endpoints must come from learner text;
- a client-identity guardrail is not itself a stated harm;
- a 3-5 secondary score must be supported by feedback that paraphrases both
  learner endpoints;
- Claude and OpenAI receive a byte-identical V4 rubric; and
- V4 fingerprints cannot resume production, V1, V2, or V3 results.

The prompt change does not touch the Anthropic request, model identifier,
structured-output schema, token settings, or caching behavior.

## Paid gate

The gate is split so the two known failures run before any controls. Both stages
use Claude Sonnet 5, low effort, concurrency 1, approved grounding, the
production score schema, and `--enforce-reviewed-gate`.

### E1 — two blocking V3 cases

| Case | Reviewed axes | Composite | Required behavior |
| --- | --- | ---: | --- |
| NFR follow-up supplies constraints | 5 / 1 / 1 | 3 | Keep correct mechanism Accuracy independent of missing secondary evidence. |
| Identity follow-up supplies authorization | 5 / 1 / 2 | 3 | Keep a guardrail without stated harm below Boundaries 3. |

Fresh free token count:

| Calls | Counted input | Reserved output | Exact bound | Stage cap |
| ---: | ---: | ---: | ---: | ---: |
| 2 | 7,294 | 1,024 | $0.024828 | **$0.0249** |

E1 must pass the automatic reviewed gate and manual feedback/evidence audit
before E2 runs.

### E2 — symmetric controls

| Case | Reviewed axes | Composite | Control protected |
| --- | --- | ---: | --- |
| NFR fluent quality checklist | 1 / 0 / 0 | 1 | Axis independence must not inflate an incorrect mechanism. |
| NFR noisy transcript | 5 / 1 / 1 | 3 | Correct mechanism can remain high without secondary evidence. |
| Identity failure boundary only | 5 / 1 / 5 | 5 | A learner-stated action-to-harm relationship must still activate Boundaries. |
| Decision-driven estimation trade-off only | 5 / 5 / 1 | 4 | A learner-stated choice-to-tension relationship must still activate Depth. |

Fresh free token count:

| Calls | Counted input | Reserved output | Exact bound | Stage cap |
| ---: | ---: | ---: | ---: | ---: |
| 4 | 14,471 | 2,048 | $0.049422 | **$0.0495** |

## Cost and stop policy

| Scope | Calls | Counted input | Reserved output | Conservative bound |
| --- | ---: | ---: | ---: | ---: |
| E1 known failures | 2 | 7,294 | 1,024 | $0.024828 |
| E2 controls, only after E1 passes | 4 | 14,471 | 2,048 | $0.049422 |
| **Maximum targeted gate** | **6** | **21,765** | **3,072** | **$0.074250** |

The requested cumulative authorization is **$0.0743**. The two runner commands
still receive their upward-rounded stage caps. Any E1 failure leaves all E2
authorization unspent. There is no retry, effort escalation, or favorable-sample
search.

The compact V4 correction adds 510 input tokens per case over V3. It costs
$0.006120 more than V3 across this six-case gate at the current promotional
input rate. An earlier draft added 804 tokens per case; tightening it before the
paid run reduced the V4 premium by 36.6% and the total six-call ceiling by 4.5%.

Every response must:

- parse into the production schema;
- keep composite and each axis within one of the reviewed label;
- preserve the reviewed Accuracy pass/fail bucket;
- satisfy the mandatory secondary eligibility bands; and
- keep feedback attribution consistent with learner evidence.

The runner stops after durably recording the first stage that fails any automatic
gate. A provider error, malformed response, manual evidence contradiction, or
budget issue also stops the experiment.

## What comes after a pass

A six-case pass would show only that V4 repairs the observed defects without
breaking their closest controls. It would not authorize production, Terra, or
prompt distillation. The next paid step would resume the remaining four
follow-up-anchored cases and then the unrun V3 families, each behind the same
automatic and manual gates.

## Run outcome

All six calls ran after E1 passed. E2 then stopped the experiment because the
decision-driven-estimation trade-off received Depth 3 against reviewed 5. V4
fixed the two original C5b blockers and preserved all evidence eligibility
bands, but its within-band Depth calibration is not reliable enough to promote.
No production setting changed. The full audit and cost ledger are in
`CLAUDE-EXPLICIT-EVIDENCE-V4-RESULTS.md`.
