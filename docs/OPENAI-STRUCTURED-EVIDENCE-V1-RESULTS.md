# OpenAI structured evidence V1 results

**Run date:** 2026-08-10 (America/Los_Angeles)

**Decision:** Stop structured evidence V1. Do not implement it in production,
run replicas, or begin score calibration.

## Outcome

GPT-5.6 Terra returned schema-valid exact learner spans for all six frozen
cases, but only **8 of 12** Depth and Boundaries eligibility decisions matched
the reviewed matrix. The automatic gate failed on four decisions. The manual
audit confirmed that all four are substantive semantic extraction errors, not
exact-span packaging errors or ambiguous reviewed labels.

Production remains unchanged. This result does not authorize an OpenAI
production adapter, a two-stage scorer, a provider fallback, a prompt edit, a
label change, or a scheduling change.

## Frozen execution

| Control | Value |
| --- | --- |
| Provider | OpenAI Responses API |
| Model | `gpt-5.6-terra` |
| Effort | `medium` |
| Contract | Evaluation-only structured evidence V1 |
| Cases | Six approved symmetric controls |
| Concurrency | 1 |
| Output cap | 512 tokens per call, including reasoning |
| State | `store: false`; fresh requests |
| Retries | None |

The separately authorized input-token dry-run counted **2,077 input tokens**.
The paid runner reused that exact total locally instead of retransmitting the
six payloads to the count endpoint. Its hard ceiling was **$0.0411** at the
published standard rate of $2 per million input tokens and $12 per million
output tokens. The six generated calls used 2,077 input tokens and 1,335 output
tokens, for a calculated cost of **$0.020174** (displayed by the runner as
**$0.0202**). The unspent authorization was **$0.020926**.

Pricing source: <https://developers.openai.com/api/docs/models/gpt-5.6-terra>.

## Automatic gate

| Metric | Result | Gate |
| --- | ---: | --- |
| Structured responses parsed | 6/6 | Pass |
| Exact-span validation errors | 0 | Pass |
| Depth decisions | 5/6 | **Fail** |
| Boundaries decisions | 3/6 | **Fail** |
| All axis decisions | 8/12 | **Fail** |
| Positive-control recall | 1/3 | **Fail** |
| Negative-control specificity | 7/9 | **Fail** |

The model emitted three eligible relationships. Only one was a reviewed
positive, so eligible-extraction precision was also 1/3.

## Case matrix

| Case | Reviewed Depth / Boundaries | Terra Depth / Boundaries | Audit |
| --- | --- | --- | --- |
| NFR follow-up supplies constraints | false / false | false / **true** | **Fail:** constraint context was mislabeled as harm evidence. |
| NFR failure boundary only | false / true | **true** / **false** | **Fail:** the wrong-checklist consequence was mislabeled as a trade-off and then missed as a failure boundary. |
| Identity follow-up supplies authorization | false / false | false / false | Pass. Authorization logic was not promoted into an unstated exploit. |
| Decision estimation self-corrected | false / true | false / **false** | **Fail:** the explicit rejected calculation path was missed. |
| Decision follow-up supplies decision | false / false | false / false | Pass. Option selection alone stayed ineligible. |
| Decision trade-off only | true / false | true / false | Pass. The time-saving trade-off did not leak into Boundaries. |

## Manual evidence audit

### Constraint context became a false Boundary

For the NFR follow-up, Terra selected `during a failure` as the trigger and
`stale feed data` as the harm inside the phrase `stale feed data acceptable
during a failure`. The learner is specifying the operating condition and an
acceptable-staleness target. They do not state a mistaken action connected to
harm or incorrect behavior. V1 explicitly forbids reversing a prescription
into a failure, so the reviewed `false` label stands.

### Failure evidence became a false Depth

For the NFR failure-boundary control, Terra copied essentially the full answer
as a Depth connection. It treated the prescribed approach—choose a few
operation-specific constraints—and the consequences of a long generic
checklist as the two sides of a trade-off. The answer states why the generic
checklist is wrong; it does not state a cost, sacrifice, tension, or opposing
benefit of the chosen approach. This is ineligible for Depth.

The same answer explicitly connects the mistake `A long generic checklist` to
the incorrect behaviors `gives no basis for storage, caching, or replication
choices` and `may contain conflicting goals`. Terra returned empty Boundary
spans, so it simultaneously missed the relationship the text actually states.

### The historical self-correction blocker remained missed

The decision-estimation control says the learner would start with DAU, QPS,
bandwidth, and storage, then rejects that path because `none of those decides
this branch`. That is the frozen positive Boundary relationship: a mistaken
calculation path connected to its inability to select the architecture. Terra
returned empty Boundary spans. This reproduces the substantive miss that
motivated the extraction experiment rather than resolving it.

### Controls that held

Terra correctly kept authorization-without-exploit and option-selection-only
answers ineligible on both secondary axes. It also found the trade-off between
estimating only a decision-changing quantity and saving interview time while
keeping that text out of Boundaries.

## Replay audit

A credential-free exact-fingerprint replay resumed all six records, scheduled
zero calls, reproduced the four gate failures, and cost $0.0000. The paid-run
and replay JSONL files were byte-identical:

```text
a78dd2b7310054ed531bfb46c61daf70f260d0cf897163544b6cb14d1ac44445
```

Raw provider records remain in the ignored `api/.eval-results/` directory and
are not committed because they contain provider identifiers and full request
inputs.

## Production decision

Do not replace the current production scorer with Terra and do not add V1 as a
pre-scoring evidence stage. Exact-substring validation prevented invented
quotes, but it could not determine whether real text semantically proved the
claimed axis. The proposed architecture therefore moved the same attribution
error into an earlier stage without making it reliable.

This does not erase Terra's stronger result on the scheduler-critical Accuracy
axis in the earlier V8 diagnostic. It does show that Terra is not currently a
safe whole-scorer replacement: the displayed composite and Coverage still
depend on Depth and Boundaries.

## Recommended next decision

Stop prompt-level iteration on this six-case set. Claude and Terra have now
failed the same general secondary-axis attribution contract in different ways,
and V1 shows that requiring quotes alone does not fix it.

Before another paid experiment, make a no-spend product and architecture
decision about the secondary axes:

1. keep all three axes and benchmark a stronger model on a larger held-out
   matrix with the unchanged extraction contract; or
2. redesign secondary-axis reporting so uncertain Depth and Boundaries do not
   affect the displayed composite, while preserving mechanism accuracy as the
   only scheduler input.

The second option is the lower-complexity direction, but it changes the
product's scoring claim and therefore requires an explicit spec/design decision
before implementation. Neither option should be started from this result PR.
