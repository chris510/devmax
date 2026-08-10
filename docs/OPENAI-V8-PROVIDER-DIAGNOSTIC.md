# OpenAI V8 provider diagnostic

**Status:** Prepared; no evaluation payload transmitted and no paid call made

## Decision

Run one six-case GPT-5.6 Terra diagnostic under the unchanged
`explicit-evidence-v8` contract before writing V9. This is the smallest test
that distinguishes a Claude adherence problem from a prompt-contract problem.

Production remains unchanged. The diagnostic does not authorize an OpenAI
adapter, provider fallback, prompt promotion, or any scheduler change.

## Why OpenAI before V9

Claude's NFR follow-up has not failed in one stable direction. V4 scored it
4 / 1 / 1, V5 leaked missing secondary evidence into Accuracy at 3 / 0 / 0,
V6 and V7 kept every axis within tolerance, and V8 then inflated Boundaries at
5 / 2 / 4. The reviewed answer did not change. That history points to unstable
contract adherence, not one missing NFR label.

A V9 sentence saying that “during a failure” is constraint context rather than
failure-mode evidence could repair the latest sample. It would also be the
ninth prompt candidate and another topic-shaped patch. It would not tell us
whether V8 is already adequate on a different provider.

Terra is the useful discriminator because its prior production-prompt release
smoke had zero false Accuracy passes or failures but inflated secondary axes on
three noisy correct answers. Evidence attribution—not mechanism grading—was
its blocker. V8 is the provider-neutral contract built specifically to require
learner-stated cost and harm relationships. Testing that unchanged contract on
Terra is more informative than immediately changing both prompt and sample.

## Six-case symmetric matrix

All cases are synthetic, source-grounded, and already marked
`review_status: approved`.

| Case | Reviewed axes | Composite | Discriminator |
| --- | --- | ---: | --- |
| NFR follow-up supplies constraints | 5 / 1 / 1 | 3 | The Claude V8 failure: operating during failure is constraint context, not a wrong-action/harm relationship. |
| NFR failure boundary only | 5 / 1 / 4 | 5 | Positive same-topic control: a generic checklist explicitly produces no design basis and conflicting goals. |
| Identity follow-up supplies authorization | 5 / 1 / 2 | 3 | Historical guardrail control: correct authorization without a learner-stated exploit stays low Boundaries. |
| Decision follow-up supplies decision | 5 / 1 / 2 | 3 | Selection logic stays low on both secondary axes. |
| Decision self-corrected | 5 / 1 / 4 | 5 | Explicitly rejected ritual arithmetic remains eligible Boundary evidence without becoming Depth. |
| Decision trade-off only | 5 / 5 / 1 | 4 | Interview-time tension remains high Depth without becoming Boundaries. |

The matrix includes low/low, low/high, and high/low secondary-axis controls. A
provider cannot pass by suppressing every secondary axis.

## Frozen request

| Control | Value |
| --- | --- |
| Provider | OpenAI Responses API |
| Model | `gpt-5.6-terra` |
| Effort | `medium` |
| Prompt | Shared evaluation-only `explicit-evidence-v8` bytes |
| Schema and parser | Unchanged production scoring contract |
| Grounding | Six approved `cards.json` questions, answer bases, and rubrics |
| Concurrency | 1 |
| Output cap | 1,024 tokens, including reasoning |
| State | `store: false`; no prior response |
| Retries | None |

Official OpenAI documentation currently lists Terra at $2 per million input
tokens and $12 per million output tokens and shows support for the Responses API
and structured outputs:
<https://developers.openai.com/api/docs/models/compare>.

## Cost controls

The existing credential-free preflight remains available and makes no OpenAI
request. For this matrix it computed:

| Calls | Local bounded input | Reserved output | Conservative bound |
| ---: | ---: | ---: | ---: |
| 6 | 64,455 | 6,144 | **$0.202638** |

The displayed ceiling is **$0.2027**. It is intentionally extreme: every
visible UTF-8 byte is treated as a token, 2,048 framing tokens are added per
call, and every response is assumed to consume its full 1,024-token cap.

The runner now supports OpenAI's exact Responses input-token endpoint. The
official guide says it accepts the same request input and includes provider
formatting, roles, schema, and other request-structure tokens:
<https://developers.openai.com/api/docs/guides/token-counting>. The runner strips
generation-only `max_output_tokens` and `store` fields, sends the otherwise
matching prompt/input/reasoning/schema payload, and uses the returned count in
the same hard budget guard.

Exact counting is an external transmission even though it generates no model
response. It therefore runs only after explicit authorization to send these six
synthetic cases and their grounding to OpenAI. No exact count was requested in
this preparation PR.

Prior Terra production-prompt smoke usage was 12,458 input and 2,534 output
tokens across 12 calls, costing $0.055324. V8 adds prompt input but does not
require longer output. That evidence suggests approximately **$0.04–$0.05** for
this six-call diagnostic. This is a planning forecast, not an authorization or
guarantee; the fresh exact-count ceiling remains enforceable.

## Runner safety added by this preparation

`openai_bakeoff.py` now has two opt-in controls:

- `--exact-input-counts` uses the authenticated, non-generating OpenAI token
  endpoint rather than the deliberately loose local byte ceiling; and
- `--enforce-reviewed-gate` applies the same composite, axis, and Accuracy
  bucket gate used by the Claude runner after every response is durably
  recorded.

The local no-key/no-network dry run remains the default. Exact counting refuses
to run without an OpenAI API key, and a paid run still separately requires an
explicit sufficient `--max-cost-usd`. ChatGPT subscription credits do not
authorize API usage.

## Pass and stop policy

Every response must:

- parse through the unchanged production schema;
- preserve the reviewed Accuracy pass/fail bucket;
- keep the composite and every axis within one point of its reviewed label;
- preserve the low/low, low/high, and high/low evidence distinctions; and
- keep feedback consistent with learner-stated evidence and returned axes.

A provider error, incomplete response, malformed result, automatic gate
failure, manual evidence contradiction, or budget problem stops the experiment
after durable recording. There is no retry, effort escalation, prompt edit,
fallback, relabeling, or favorable-sample replacement.

After the audit, a keyless exact-fingerprint replay must schedule zero calls and
reproduce the result.

## Audited sequence

After this PR merges:

1. obtain explicit authorization to transmit the six synthetic payloads and
   grounding to OpenAI's input-token endpoint;
2. run the exact-count dry run and record its lower ceiling;
3. obtain a separate paid authorization at or below that exact ceiling;
4. make the six standard Responses calls at concurrency 1;
5. run the automatic and manual evidence audits; and
6. run the keyless replay and publish a result PR.

The exact-count command is:

```text
uv run python scripts/openai_bakeoff.py scoring \
  scripts/grounded_effort_cases_week1_release.json \
  --grounding-manifest cards.json \
  --model gpt-5.6-terra --levels medium --concurrency 1 \
  --max-output-tokens 1024 \
  --scoring-prompt-variant explicit-evidence-v8 \
  --enforce-reviewed-gate --fresh --exact-input-counts --dry-run \
  --case <each of the six exact names above>
```

The paid command removes `--dry-run`, supplies a new ignored JSONL output path,
and acknowledges only the freshly printed exact ceiling.

## Decisions after the result

- **Pass once:** do not promote. Run two separately authorized fresh replicas of
  the same six cases so every discriminator has three Terra/V8 observations.
- **Any automatic or manual failure:** stop Terra/V8. Do not add a topic-specific
  V9 patch immediately. Both providers would then have failed the general
  evidence contract, making structured evidence extraction or a revised score
  schema the more valuable next design question.
- **Three stable passes:** prepare the remaining V8 risk-smoke and release-pack
  evidence. Production still remains unchanged until the broader pack, frozen
  baseline, and provider-routing design all pass.
