# OpenAI Sol secondary-axis canary results

**Run date:** 2026-08-10 (America/Los_Angeles)

**Decision:** Stop GPT-5.6 Sol on the unchanged whole-scoring contract. Do not
run the 18-case remainder, the two repeatability replicas, or design a
production OpenAI adapter from this result.

## Outcome

GPT-5.6 Sol returned six schema-valid results and preserved every reviewed
Accuracy value, including all three stale-mastery contradiction controls. It
failed both secondary-axis gates by promoting unsupported learner evidence:

- the NFR answer's operating allowance became a high Boundaries score; and
- the identity answer's failure mechanism became a high Depth score despite
  containing no trade-off.

The reviewed gate and the stricter 0-2 versus 3-5 bucket gate each failed on
those two decisions. The manual audit confirms that both are substantive
attribution errors rather than ambiguous reviewed labels.

Production remains unchanged on Claude. This result does not authorize an
OpenAI production adapter, provider fallback, prompt change, label change,
retry policy, or scheduling change.

## Frozen execution

| Control | Value |
| --- | --- |
| Provider | OpenAI Responses API |
| Model | `gpt-5.6-sol` |
| Effort | `medium` |
| Prompt | Unchanged production scoring contract |
| Cases | Six approved `sol-secondary-canary` cases |
| Concurrency | 1 |
| Output cap | 1,024 tokens per call, including reasoning |
| State | `store: false`; fresh requests |
| Retries | None |

OpenAI documents GPT-5.6 Sol as a reasoning model with Responses and Structured
Outputs support:
<https://developers.openai.com/api/docs/models/gpt-5.6-sol>.

## Cost

The separately authorized input-token run sent the six frozen payloads only to
`POST /v1/responses/input_tokens`. It counted **6,298 input tokens**, lowering
the generated-run ceiling from the credential-free **$0.4046** bound to the
authorized **$0.2159** maximum. The generated runner reused that total locally
and did not repeat the count transmissions.

The six generated calls used 6,298 input tokens and 1,468 output tokens. At the
published standard rate of $5 per million input tokens and $30 per million
output tokens, the calculated cost was **$0.075530** (displayed by the runner
as **$0.0755**). The unspent authorization was **$0.140370**.

## Automatic gates

| Metric | Result | Gate |
| --- | ---: | --- |
| Structured responses parsed | 6/6 | Pass |
| Accuracy exact | 6/6 | Pass |
| Accuracy false passes / false failures | 0 / 0 | Pass |
| Depth exact / within one | 4/6 / 5/6 | **Fail** |
| Boundaries exact / within one | 4/6 / 5/6 | **Fail** |
| Composite exact | 5/6 | **Fail** |
| Secondary 0-2 / 3-5 bucket crossings | 2 | **Fail** |

The reviewed gate reported:

1. NFR Boundaries returned 4 against reviewed 2; and
2. Identity Depth returned 4 against reviewed 1.

The independent bucket gate classified the same deviations as a false
Boundaries pass and a false Depth pass. Both cross product-significant
boundaries even though only the NFR composite changed.

## Case matrix

Axes are Accuracy / Depth / Boundaries.

| Case | Reviewed | Sol | Composite | Audit |
| --- | --- | --- | ---: | --- |
| Delivery sequence — stale mastery contradicted | 1 / 0 / 0 | 1 / 0 / 0 | 1 -> 1 | Pass |
| NFR — SLO alternative | 5 / 4 / 2 | 5 / 4 / **4** | 4 -> **5** | **Fail:** operating context was promoted into a high failure-boundary score. |
| Identity — failure boundary only | 5 / 1 / 5 | 5 / **4** / 4 | 5 -> 5 | **Fail:** failure evidence was also credited as a trade-off. |
| Timeout retry — request identifier alternative | 5 / 4 / 4 | 5 / 5 / 4 | 5 -> 5 | Pass |
| Cursor pagination — stale mastery contradicted | 1 / 0 / 0 | 1 / 0 / 0 | 1 -> 1 | Pass |
| Decision estimation — stale mastery contradicted | 1 / 0 / 0 | 1 / 0 / 0 | 1 -> 1 | Pass |

## Manual evidence audit

### NFR operating allowance became a false high Boundary

The learner specified operation-specific SLOs and included “a freshness or
consistency allowance during failure.” That is relevant boundary context, so
the approved low score is 2 rather than 0, but the answer does not connect a
mistaken action or trigger to a concrete harmful behavior. Sol returned
Boundaries 4 and composite 5. The feedback asks for more concrete targets, but
the score already claims high failure-mode evidence that the learner did not
supply.

### Identity failure evidence became a false Depth pass

The learner correctly derived the principal from trusted authentication,
authorized the booking action, and named the IDOR consequence of trusting a
client-supplied user ID. That supports Accuracy and Boundaries. It states no
cost, sacrifice, tension, or alternative-authentication trade-off, so the
reviewed Depth value is 1. Sol returned Depth 4 and then used its feedback for
another authorization-boundary nuance rather than the genuinely missing
trade-off. This is the same semantic leakage the axis-isolation case was built
to detect.

### Controls that held

Sol correctly let the current learner answer outrank favorable mastery context
in all three contradiction cases. Each low-Accuracy feedback supplied the
approved essential account without treating the prior summary, answer basis,
rubric, or source excerpt as learner evidence.

The timeout-retry alternative also held. The learner explicitly connected the
operation registry to its state-and-coordination cost and connected replay to
duplicate charging, so Sol's high secondary scores were grounded in what the
learner said.

## Replay audit

A credential-free exact-fingerprint replay resumed all six records, scheduled
zero calls, reproduced the reviewed-gate failure, and cost $0.0000. A second
zero-call replay isolated and reproduced the two secondary-bucket failures.
The paid result and both replay JSONL files were byte-identical:

```text
d9ba0944d7e92c55cda959814882953987b59c42ea60c9b4909e65466e8ae5f2
```

Raw provider records remain in the ignored `api/.eval-results/` directory and
are not committed because they contain provider identifiers and full request
grounding.

## Production decision

Do not replace Claude with Sol on the current direct scorer. Sol materially
improved the scheduler-critical Accuracy result, but the product also uses the
derived composite for follow-ups, mastery bands, Coverage, Sprint selection,
recaps, history, and Study Plan retrieval. A false high secondary bucket is
therefore user-visible even when SM-2 remains protected.

Do not continue to the 18 held-out cases or repeatability replicas. The frozen
six-case stage was explicitly a stop gate, and two independent controls failed
for the exact risk under test.

## Recommended next decision

Stop provider and prompt iteration until the product contract is revisited.
Claude, Terra, structured-evidence V1, and now Sol have all shown that a single
model can return schema-valid scores while attributing secondary evidence to
the wrong axis. Another provider sample or wording patch would not resolve
that architectural uncertainty.

The next step should be a no-spend spec and design decision with two explicit
options:

1. keep the three-axis contract and accept Claude as the current imperfect
   scorer while defining the evidence and repeatability bar required of any
   future replacement; or
2. redesign follow-ups, the displayed composite, Coverage, depth repair,
   history, and Study Plan consumers so uncertain Depth and Boundaries cannot
   present false mastery as a reliable score.

The second option may produce the more honest study signal, but it is a product
redesign—not a provider-routing change—and must amend the authoritative spec
and design handoff before implementation.
