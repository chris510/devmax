# OpenAI Sol secondary-axis canary

**Status:** Prepared and locally tested. No evaluation payload has been
transmitted and no provider call has been made.

## Decision under test

Test whether GPT-5.6 Sol can satisfy Devmax's unchanged whole-scoring contract
on approved cases that were not part of a recorded OpenAI live result stage.

This is not another prompt revision and not the structured-evidence V1
contract. Sol receives the same production scoring rubric and strict schema as
the current direct scorer. Production remains unchanged.

## Why Sol

Terra consistently protected the Accuracy pass/fail boundary but did not grade
Depth and Boundaries reliably enough for the user-visible composite and
Coverage. V1 showed that exact quotes alone did not resolve that attribution
failure. The next useful provider variable is model capability, not another
prompt patch.

OpenAI documents `gpt-5.6-sol` as the GPT-5.6 frontier model for complex
professional work. It supports the Responses API and Structured Outputs. The
published standard price is $5 per million input tokens and $30 per million
output tokens:
<https://developers.openai.com/api/docs/models/gpt-5.6-sol>.

## Held-out set

The approved 42-case release pack was compared with every recorded OpenAI live
stage. Twenty-four cases were not named in those result stages. They are frozen
with `sol-secondary-heldout`; six form the first stop-stage and 18 remain behind
that gate.

The first stage has one case per approved topic, three Accuracy passes and three
Accuracy failures, two Depth positives, and two Boundaries positives.

| Case | Reviewed Accuracy / Depth / Boundaries | Composite | Risk |
| --- | --- | ---: | --- |
| Delivery sequence — stale mastery contradicted | 1 / 0 / 0 | 1 | Current wrong answer must outrank favorable prior context. |
| NFR — SLO alternative | 5 / 4 / 2 | 4 | Valid alternative and Depth must not invent a failure mode. |
| Identity — failure boundary only | 5 / 1 / 5 | 5 | Explicit Boundary must not leak into Depth. |
| Timeout retry — request identifier alternative | 5 / 4 / 4 | 5 | Valid alternative with both secondary relationships. |
| Cursor pagination — stale mastery contradicted | 1 / 0 / 0 | 1 | Fluent prior mastery must not rescue the current answer. |
| Decision estimation — stale mastery contradicted | 1 / 0 / 0 | 1 | Correct prior summary must not rescue ritual arithmetic. |

The expected composite remains derived locally with `llm.derive_composite`.
Tags do not enter request fingerprints, so stage labels can select cases without
changing the judged payload.

## Provider payload

Each call sends the existing scoring inputs required by production:

- topic and approved canonical question;
- learner initial answer and follow-up answer when present;
- current mastery summary when the case defines one;
- approved answer anchor, answer basis, answer rubric, and source excerpt;
- the unchanged production scoring instructions; and
- the strict scoring JSON schema.

These repository-grounding and learner-answer payloads require explicit
authorization before exact counting and again before generated Responses.
Provider responses use `store: false`.

## Frozen execution

| Control | Value |
| --- | --- |
| Provider | OpenAI Responses API |
| Model | `gpt-5.6-sol` |
| Effort | `medium` |
| Prompt | Unchanged production scoring contract |
| Stage | Six `sol-secondary-canary` cases |
| Concurrency | 1 |
| Output cap | 1,024 tokens per call, including reasoning |
| State | `store: false`; fresh requests |
| Retries | None |

Medium matches the prior Terra evaluation baseline and follows OpenAI's current
guidance to use medium as a balanced starting point. No effort comparison is
part of this experiment.

## Automatic gate

All of the following must pass:

- six of six structured responses parse through the unchanged production
  result contract;
- zero false Accuracy passes and zero false Accuracy failures;
- every Accuracy, Depth, Boundaries, and composite value is within one point of
  its approved label;
- no Depth or Boundaries decision crosses the product-significant 0-2 versus
  3-5 boundary; and
- a keyless exact-fingerprint replay resumes all six records and schedules zero
  calls.

The new secondary-bucket gate is stricter than numerical within-one checking.
A reviewed 2 returned as 3 is only one point away, but it can change the
composite, trigger a different follow-up path, and alter Coverage, so it must
stop the experiment.

## Manual gate

The audit must confirm:

- every positive secondary score is supported by what the learner actually
  said;
- low-Accuracy feedback supplies the approved essential account;
- passing-Accuracy feedback supplies the genuinely weaker secondary gap;
- stale mastery is never treated as learner evidence; and
- no answer basis, rubric, or source excerpt is attributed to the learner.

Any automatic or manual failure stops the experiment without retry, prompt
change, effort escalation, relabeling, fallback, or case replacement.

## Cost guard

The credential-free local bound is:

| Calls | Bounded input | Reserved output | Conservative ceiling |
| ---: | ---: | ---: | ---: |
| 6 | 44,056 | 6,144 | **$0.4046** |

This is deliberately a safety ceiling, not a forecast or authorization. It
treats UTF-8 bytes as possible tokens, adds provider framing allowance, and
reserves the full output cap. Exact input counting should lower it materially,
but that count transmits all six provider payloads and needs separate explicit
authorization.

## Audited sequence

After this preparation merges:

1. authorize up to six exact input-token count transmissions for the frozen
   payloads, with no generated Responses;
2. run the exact-count-only dry-run and record its lower ceiling;
3. authorize up to six generated Responses at or below that exact ceiling;
4. run the six requests sequentially with both automatic gates enabled;
5. stop on any provider, schema, reviewed, secondary-bucket, manual, or budget
   failure;
6. run the credential-free replay; and
7. publish the stopped or passing result in a separate PR.

Exact counting:

```text
uv run python scripts/openai_bakeoff.py scoring \
  scripts/grounded_effort_cases_week1_release.json \
  --grounding-manifest cards.json --tag sol-secondary-canary \
  --model gpt-5.6-sol --levels medium --concurrency 1 \
  --max-output-tokens 1024 --enforce-reviewed-gate \
  --enforce-secondary-bucket-gate --fresh --exact-input-counts --dry-run
```

The separately authorized paid command replaces `--exact-input-counts
--dry-run` with `--reuse-exact-input-total TOKENS --max-cost-usd CEILING` and
writes a new ignored JSONL file. Reuse avoids retransmitting the payloads to the
count endpoint beyond the six count requests already authorized.

## Stages after a pass

One pass advances only to a separately counted and authorized 18-case
`sol-secondary-remainder` stage. If all 24 unique cases pass, run two separately
authorized fresh replicas of the six-case canary before any adapter design.

The complete 36-call evidence path is still narrow topical evidence from six
approved cards. A pass can justify provider-adapter design review, not an
automatic production switch.
