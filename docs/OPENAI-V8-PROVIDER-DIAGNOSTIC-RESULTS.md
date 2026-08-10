# OpenAI V8 provider diagnostic results

**Decision:** Stop Terra/V8. Do not implement it in production and do not run
the two repeatability replicas.

## Outcome

The six-case GPT-5.6 Terra diagnostic preserved every reviewed Accuracy
pass/fail bucket, but failed the frozen reviewed gate on Boundaries. The
failure is substantive: the positive self-correction control received
Boundaries 2 even though V8 explicitly calibrates the same learner pattern as
eligible for Boundaries 3-5.

Production remains unchanged. This run does not authorize an OpenAI adapter,
provider fallback, prompt promotion, scheduler change, V9 patch, or relabeling.

## Frozen execution

| Control | Value |
| --- | --- |
| Provider | OpenAI Responses API |
| Model | `gpt-5.6-terra` |
| Effort | `medium` |
| Prompt | Evaluation-only `explicit-evidence-v8` |
| Cases | Six approved symmetric controls |
| Concurrency | 1 |
| Output cap | 1,024 tokens per call, including reasoning |
| State | `store: false`; fresh requests |
| Retries | None |

The exact-count preflight reported 11,015 input tokens and reduced the hard
ceiling from $0.2027 to **$0.0958**. The generated calls used 11,015 input
tokens and 1,703 output tokens. The runner-computed standard-rate cost was
**$0.0425**.

## Results

Axes are Accuracy / Depth / Boundaries.

| Case | Reviewed | Terra | Composite | Gate |
| --- | --- | --- | ---: | --- |
| NFR follow-up supplies constraints | 5 / 1 / 1 | 4 / 1 / 1 | 3 -> 3 | Pass |
| NFR failure boundary only | 5 / 1 / 4 | 5 / 0 / 4 | 5 -> 5 | Pass |
| Identity follow-up supplies authorization | 5 / 1 / 2 | 5 / 0 / 2 | 3 -> 3 | Pass |
| Decision estimation self-corrected | 5 / 1 / 4 | 5 / 2 / 2 | 5 -> 3 | **Fail** |
| Decision follow-up supplies decision | 5 / 1 / 2 | 4 / 1 / 0 | 3 -> 3 | **Fail** |
| Decision trade-off only | 5 / 5 / 1 | 5 / 5 / 0 | 4 -> 4 | Pass |

Aggregate results:

- composite exact: 5/6; mean absolute deviation 0.33;
- Accuracy: exact 4/6, within one 6/6, zero false passes and zero false
  failures;
- Depth: exact 3/6, within one 6/6; and
- Boundaries: exact 3/6, within one 4/6.

The automatic gate reported three deviations:

1. self-corrected composite 3 was more than one point below reviewed 5;
2. self-corrected Boundaries 2 was more than one point below reviewed 4; and
3. follow-up decision Boundaries 0 was more than one point below reviewed 2.

## Manual evidence audit

### Substantive positive-control failure

The self-corrected answer says it would start with DAU, QPS, bandwidth, and
storage, then explicitly rejects those estimates because “none of those
decides this branch.” V8's calibration deliberately says that “Start with
DAU—no, it cannot decide this branch” connects a mistake to incorrect behavior
and must receive Boundaries 3-5.

Terra returned Boundaries 2 and feedback asking the learner to make that
failure relationship explicit. That contradicts the frozen calibration rather
than exposing an ambiguous reviewed label. This is the experiment's decisive
failure.

### Low-bucket label deviation

The follow-up-decision answer contains only heap-cardinality selection logic.
Terra's Boundaries 0 remains in V8's required 0-2 low bucket, but it is two
points from the reviewed calibration value of 2, so the unchanged reviewed
gate correctly fails it. This narrower disagreement would not alone justify a
provider rejection, but it cannot rescue the failed positive control.

### Controls that held

Terra kept NFR constraints low/low, recognized the explicit NFR failure
relationship, kept authorization-without-exploit in the low Boundaries bucket,
and kept the trade-off-only control high Depth and low Boundaries. Accuracy was
stable across all six cases.

## Replay audit

A credential-free exact-fingerprint replay resumed all six records, scheduled
zero calls, reproduced the same gate failure, and cost $0.0000. The original
and replay JSONL files were byte-identical:

```text
b878a29d254b9cb4d2344f489637c94360017e3fccde9ff756443b8152632975
```

Raw provider records remain in the ignored `api/.eval-results/` directory and
are not committed because they contain provider identifiers and full request
grounding.

## Production decision

Do not replace Claude with Terra on the current V8 scoring contract. Terra's
stable Accuracy is promising, but the product exposes the composite and
Coverage consumes the secondary axes, so a provider that misses a frozen
positive Boundary example is not production-safe.

Do not add a topic-specific V9 sentence. Claude and Terra have now both failed
the general evidence-attribution contract in different ways. Another prompt
patch would tune against observed samples without resolving whether the model
actually grounded each secondary axis in learner text.

## Recommended next experiment

Test structured evidence extraction before scoring calibration. For each
secondary axis, require the model to return learner-text spans and both
relationship endpoints before it can assign a high score:

- Depth: learner-stated choice/target and learner-stated cost/tension;
- Boundaries: learner-stated trigger/mistake and learner-stated harm/incorrect
  behavior; and
- an explicit `eligible` decision derived from whether both grounded endpoints
  and their connection are present.

Run that extraction contract first on the same six symmetric cases without
changing production. A useful candidate must identify the self-correction
positive control while continuing to reject selection-only, guardrail-only,
and trade-off-only text as Boundary evidence. Only after the evidence layer is
stable should a separate calibration step map eligible evidence to 0-5 axes.

The credential-free preparation is documented in
[`STRUCTURED-EVIDENCE-EXTRACTION-EXPERIMENT.md`](STRUCTURED-EVIDENCE-EXTRACTION-EXPERIMENT.md).
