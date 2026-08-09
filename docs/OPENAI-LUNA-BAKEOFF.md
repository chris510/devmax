# OpenAI GPT-5.6 Luna scoring bake-off

Status: **harness complete; live run blocked on `OPENAI_API_KEY`**

This experiment compares OpenAI GPT-5.6 Luna with the shipping Claude Sonnet 5
grader. It does not change the production provider, prompts, model defaults,
scheduler, or API endpoints. The candidate receives the same reviewed prompt,
user transcript, JSON schema, low reasoning effort, and production result parser
as Claude.

ChatGPT subscription usage cannot authorize this test. OpenAI
[bills API usage separately](https://help.openai.com/en/articles/8156019), so the
runner reads `OPENAI_API_KEY` from the process environment or the existing local
`api/.env` and refuses a paid run when it is absent.

## Why Luna

OpenAI's current [model guidance](https://developers.openai.com/api/docs/guides/latest-model)
positions GPT-5.6 Luna for efficient high-volume workloads. Luna supports the
Responses API, low reasoning effort, and strict structured output, which are the
capabilities this one-shot grader needs. The published short-context standard
[price](https://developers.openai.com/api/docs/pricing) is $0.20 per million
input tokens and $1.20 per million output tokens.

This is a quality experiment, not a migration. Production remains on Claude
unless Luna passes the same reviewed evidence gate.

## Cost controls

`api/scripts/openai_bakeoff.py` has four controls before it can spend:

1. It selects named or tagged reviewed cases before constructing requests.
2. It computes a deliberately high local input bound from every UTF-8 byte in
   the instructions, transcript, and JSON schema, plus 2,048 tokens of provider
   framing allowance per call.
3. It hard-caps output at 2,048 tokens for scoring and 512 for coached grading.
   OpenAI reasoning tokens are included in reported output usage.
4. It makes no automatic retry and refuses a paid run without an explicit
   `--max-cost-usd` at least as large as the printed ceiling.

Each successful response is immediately flushed to an ignored, fingerprinted
JSONL result file. A changed prompt, schema, expected label, model, effort, or
output cap changes the fingerprint. `--resume` reuses only exact matches and can
complete with no API key when every selected call is already present.

The 2026-08-08 credential-free preflight produced these worst-case bounds:

| Pack | Calls | Bounded input | Hard output ceiling | Cost ceiling |
|---|---:|---:|---:|---:|
| Scoring smoke | 6 | 43,834 | 12,288 | $0.0235 |
| Coached re-attempt smoke | 4 | 27,291 | 2,048 | $0.0079 |
| **Total** | **10** | **71,125** | **14,336** | **$0.0314** |

The input bound intentionally exceeds likely tokenizer usage. Actual output is
also expected to be below the hard ceiling, but the paid acknowledgement uses
the ceiling rather than that expectation.

## Live procedure

Add an API key funded on the OpenAI API platform to the uncommitted `api/.env`,
then run the free local preflights again:

```sh
cd api
# Add this to the existing uncommitted .env; never commit the value:
# OPENAI_API_KEY=your-platform-api-key

uv run python scripts/openai_bakeoff.py scoring \
  scripts/grounded_effort_cases_week1.json \
  --grounding-manifest cards.json --tag smoke --dry-run

uv run python scripts/openai_bakeoff.py reattempt \
  scripts/grounded_reattempt_cases_week1.json \
  --grounding-manifest cards.json --tag smoke --dry-run
```

After reviewing the displayed ceilings, run the paid smoke packs. The budgets
below acknowledge the current bounds but do not weaken them:

```sh
uv run python scripts/openai_bakeoff.py scoring \
  scripts/grounded_effort_cases_week1.json \
  --grounding-manifest cards.json --tag smoke --max-cost-usd 0.024

uv run python scripts/openai_bakeoff.py reattempt \
  scripts/grounded_reattempt_cases_week1.json \
  --grounding-manifest cards.json --tag smoke --max-cost-usd 0.008
```

Preserve the two printed result paths. Rerun each command with its corresponding
`--resume <path>` and no API key to prove it schedules zero new calls.

## Acceptance gate

The ten-case smoke run may advance to all 30 reviewed Week 1 cases only if:

- every response satisfies the unchanged production schema and parser;
- there are zero false Accuracy passes;
- there are zero false reconstruction passes in coached grading;
- every coached mastery summary identifies the result as coached rather than
  unaided mastery;
- per-axis deviations are reviewed rather than hidden by the composite score;
- actual token usage, latency, and cost are recorded beside the Claude baseline.

Passing 10 or 30 cases still does not authorize a provider switch. That decision
remains blocked until the planned 60–100-case pack passes and a production
adapter receives its own design and reliability review.

## Compatibility record

- Endpoint: Responses API, one independent request per case.
- Reasoning: explicit `low`; no inherited `medium` default.
- Output: strict `text.format` JSON schema with the same required fields and
  enums as production.
- Parser: the same composite derivation, follow-up decision, summary cleanup,
  and coached-result parsing used by the Anthropic path.
- State: `store: false`; no persisted reasoning or previous response.
- Tools and multimodal input: none.
- Prompt caching: not enabled for this small rubric experiment.
- Optional GPT-5.6 features: no Pro mode, programmatic tool calling, multi-agent,
  or explicit cache writes.

The current OpenAI integration guidance recommends the Responses API and an
explicit reasoning effort for GPT-5.6. The experiment intentionally adopts no
other provider-specific behavior, so a measured score difference belongs to the
model rather than an unrelated feature change.
