# Structured evidence extraction experiment

**Status:** Executed and stopped after the first frozen run failed. See
[`OPENAI-STRUCTURED-EVIDENCE-V1-RESULTS.md`](OPENAI-STRUCTURED-EVIDENCE-V1-RESULTS.md).

## Decision under test

Test whether GPT-5.6 Terra can extract learner-stated Depth and Boundaries
relationships before asking any model to calibrate 0-5 axis scores.

This is an evaluation-only contract. Production remains unchanged: Claude,
the production scoring schema and prompt, composite derivation, scheduler
inputs, API behavior, and iOS behavior are untouched.

## Why extraction comes next

Claude and Terra both preserved useful parts of the scoring contract but
failed evidence attribution in different directions. More prompt patches would
continue combining two questions in one model decision:

1. did the learner actually state a qualifying relationship; and
2. if so, how complete is it on a 0-5 scale?

This experiment isolates the first question. It produces no scores, feedback,
follow-up, mastery summary, composite, or scheduling input.

OpenAI's Structured Outputs guide says strict output can enforce a supplied
JSON Schema. That guarantees the response shape, not whether the selected text
semantically proves the relationship. The harness therefore adds deterministic
exact-span validation and still requires a manual evidence audit:
<https://developers.openai.com/api/docs/guides/structured-outputs>.

## Extraction contract

The model receives only:

- the topic as non-evidence context;
- the initial learner answer;
- the learner's follow-up answer, when present;
- extraction instructions; and
- the strict evidence schema.

The approved question, answer basis, rubric, review note, and source authority
are used locally to freeze and approve the case, but are deliberately excluded
from the provider payload. This prevents authority text from being copied as if
the learner said it.

The strict schema requests three strings for each secondary axis:

| Axis | Endpoint 1 | Endpoint 2 | Connection |
| --- | --- | --- | --- |
| Depth | choice, target, or approach | cost, sacrifice, tension, or opposing benefit | exact learner span containing both endpoints |
| Boundaries | trigger, action, exception, limitation, or mistake | concrete harm or incorrect behavior | exact learner span containing both endpoints |

All three strings must be empty when no explicit qualifying relationship
exists. The model does not return `eligible`.

## Deterministic eligibility

Code derives eligibility only when:

1. all three fields are non-empty;
2. the connection is an exact contiguous substring of a learner answer; and
3. both endpoint strings occur inside that exact connection span.

Partial fields, paraphrases, invented text, or disconnected endpoints are
ineligible and produce validation errors. Any validation error fails the
experiment, even on a reviewed-negative case. This keeps malformed extraction
from accidentally looking like a correct low-axis decision.

Exact spans do not prove semantic correctness by themselves. A model could
copy a real but irrelevant sentence, so the final gate includes a manual check
that the two endpoints and connection mean what the axis claims.

## Frozen six-case matrix

Reviewed 0-2 axes map to `eligible=false`; reviewed 3-5 axes map to
`eligible=true`.

| Case | Depth | Boundaries | Purpose |
| --- | --- | --- | --- |
| NFR follow-up supplies constraints | false | false | Constraint context must not become a cost or harm. |
| NFR failure boundary only | false | true | Explicit wrong-checklist consequences must be found. |
| Identity follow-up supplies authorization | false | false | A guardrail without an exploit stays ineligible. |
| Decision follow-up supplies decision | false | false | Selection logic alone stays ineligible. |
| Decision estimation self-corrected | false | true | The positive control Terra missed under V8 must be found. |
| Decision trade-off only | true | false | A trade-off must not leak into Boundaries. |

The matrix contains nine negative and three positive axis decisions. It cannot
pass by always returning empty spans or by making both axes eligible together.

## Frozen request

| Control | Value |
| --- | --- |
| Provider | OpenAI Responses API |
| Model | `gpt-5.6-terra` |
| Effort | `medium` |
| Contract | Evaluation-only structured evidence V1 |
| Cases | Six approved symmetric controls above |
| Concurrency | 1 |
| Output cap | 512 tokens per call, including reasoning |
| State | `store: false`; fresh requests |
| Retries | None |

Medium effort keeps the provider and effort fixed relative to the failed
Terra/V8 discriminator. The lower output cap is appropriate because this
contract returns six short strings and no scoring feedback. An incomplete
response stops the experiment without retry.

## Cost guard

The credential-free UTF-8 bound is:

| Calls | Bounded input | Reserved output | Conservative ceiling |
| ---: | ---: | ---: | ---: |
| 6 | 24,221 | 3,072 | **$0.0854** |

This is not authorization. Exact input counting transmits the six topic and
learner-answer payloads plus instructions and schema to OpenAI, so it requires
separate explicit authorization. Generated Responses require a second paid
authorization at or below the resulting exact ceiling.

## Pass and stop policy

The automatic gate requires:

- all 12 eligibility decisions match the frozen matrix;
- every returned non-empty value passes exact-span validation;
- no reviewed-negative relationship contains partial or invented evidence; and
- the keyless replay resumes six records and schedules zero calls.

The manual audit additionally requires every positive extraction to identify
the correct endpoints and relationship, with no irrelevant full-answer copy
used to satisfy the exact-span guard.

A provider error, incomplete response, malformed result, automatic failure,
manual contradiction, or budget problem stops the experiment after durable
recording. There is no retry, effort escalation, prompt edit, fallback,
relabeling, or favorable-sample replacement.

## Audited sequence

After this preparation merges:

1. obtain explicit authorization for six OpenAI input-token count requests;
2. run the exact-count-only dry run and record its lower ceiling;
3. obtain separate authorization for up to six generated Responses at that
   ceiling;
4. run the frozen requests sequentially;
5. run the automatic and manual evidence audits;
6. run a keyless exact-fingerprint replay; and
7. publish the stopped or passing result in a documentation PR.

The credential-free command is:

```text
uv run python scripts/openai_bakeoff.py evidence \
  scripts/grounded_effort_cases_week1_release.json \
  --grounding-manifest cards.json \
  --model gpt-5.6-terra --levels medium --concurrency 1 \
  --max-output-tokens 512 --enforce-evidence-gate --fresh --dry-run \
  --case 'non-functional requirements — follow-up supplies constraints' \
  --case 'non-functional requirements — failure boundary only' \
  --case 'identity boundary — follow-up supplies authorization' \
  --case 'decision-driven estimation — follow-up supplies decision' \
  --case 'decision-driven estimation — self-corrected' \
  --case 'decision-driven estimation — trade-off only'
```

Exact counting adds `--exact-input-counts`. The paid command then removes
`--dry-run`, writes a new ignored JSONL file, and acknowledges only the freshly
printed exact ceiling. When the count authorization does not permit sending the
same payloads to the count endpoint again, `--reuse-exact-input-total TOKENS`
reuses that immediately preceding exact total without making another count
request. It is valid only for an unchanged, fresh selection with no resumed
calls.

## Decisions after the result

- **Any failure:** stop V1 and inspect whether the error is extraction,
  exact-span packaging, or reviewed-label ambiguity. Do not modify production.
- **One complete pass:** run two separately authorized fresh replicas of the
  same matrix before designing score calibration.
- **Three complete passes:** prepare a separate calibration experiment that
  maps validated eligible evidence to the existing 0-5 Depth and Boundaries
  axes. Production still remains unchanged until the broader release evidence
  and provider-routing design pass.
