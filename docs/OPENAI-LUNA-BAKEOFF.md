# OpenAI GPT-5.6 Luna scoring bake-off

Status: **full Week 1 pack completed on 2026-08-08; production switch blocked**

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

## Live smoke results

The funded-key run completed all ten risk-stratified cases against
`gpt-5.6-luna` at low reasoning effort. Every response satisfied the strict
schema and the unchanged production parser.

| Pack | Exact | Within one | False pass / fail | Input | Output | Mean latency | Actual cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| Scoring | 6/6 composites | 6/6 on every axis | 0 / 0 Accuracy | 6,267 | 1,384 | 3.01s | $0.0029 |
| Coached re-attempt | 3/4 | 4/4 | 0 / 0 reconstruction | 3,834 | 456 | 1.78s | $0.0013 |
| **Total** |  |  | **0 / 0** | **10,101** | **1,840** |  | **$0.0042** |

Scoring matched all six expected composites. Accuracy and Depth were exact on
five of six cases and within one on all six; Boundaries was exact on all six.
The only coached-label mismatch was a correct cursor-pagination reconstruction
scored 5 instead of the conservative expected 4. Its mastery summary still
said the learner got there after being told and that unaided recall remained
unproven. All four coached summaries likewise distinguished coached performance
from unaided mastery.

The live result files were then replayed with `OPENAI_API_KEY` unavailable.
All ten fingerprints resumed, zero new paid calls were scheduled, and both
runs reported `$0.0000`. The observed $0.0042 total was 87% below the $0.0314
worst-case authorization ceiling and about 47% below the normalized $0.0080
estimate used before an OpenAI-tokenized run existed.

This passes the smoke acceptance gate and authorizes the next evaluation step:
all 30 reviewed Week 1 cases. It does not authorize a production provider
switch.

## Full Week 1 results

The 30-case first-pass evaluation reused the ten exact smoke fingerprints and
made 20 new paid calls. All 30 responses satisfied the strict schema and the
unchanged production parser.

| Pack | Exact | Within one | False pass / fail | Input | Output | Median latency | Actual cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| Scoring | 12/18 composites | 16/18 composites | 0 / 2 Accuracy | 18,809 | 4,140 | 2.99s | $0.0087 |
| Coached re-attempt | 7/12 | 11/12 | 0 / 0 reconstruction | 11,462 | 1,512 | 1.56s | $0.0041 |
| **Total** |  |  | **0 / 2** | **30,271** | **5,652** |  | **$0.0128** |

Scoring-axis agreement was:

| Axis | Exact | Within one |
|---|---:|---:|
| Accuracy | 12/18 | 15/18 |
| Depth | 9/18 | 15/18 |
| Boundaries | 13/18 | 17/18 |

The scoring mean composite deviation was 0.67. Median latency was 2.99 seconds,
but one response took 19.55 seconds. Coached grading had mean deviation 0.50,
zero false reconstruction passes or failures, and every mastery summary
preserved the coached-versus-unaided distinction.

### Repeatability failure

The primary run scored both correct decision-driven estimation answers as
Accuracy 0 and claimed no answer was provided, even though the recorded request
construction contained each answer in full. This created two false Accuracy
failures: the complete answer moved from expected composite 5 to 0, and the
mechanism-only answer moved from expected 3 to 0.

A controlled concurrency-1 rerun of those exact two fingerprints produced
Accuracy 5 for both. The complete answer then scored composite 5 as expected,
while the mechanism-only answer was over-scored at composite 5 instead of 3.
The rerun cost $0.0011. This rules out a missing-input or result-ordering bug and
demonstrates material output instability on identical reviewed evidence.

### Focused repeatability experiment

The unstable complete answer, its mechanism-only counterpart, and the
confidently-wrong same-topic control were then run five times each at low and
medium effort, sequentially at concurrency 1. The output cap was reduced to
1,024 tokens; no response approached it.

| Configuration | Complete scores (expected 5) | Mechanism-only scores (expected 3) | Wrong-control scores (expected 1) | False Accuracy failures | Calculated cost |
|---|---|---|---|---:|---:|
| Unchanged Luna low, 5 trials | 5 · 0 · 5 · 0 · 5 | 5 · 5 · 5 · 5 · 5 | 1 · 1 · 1 · 0 · 1 | 2 | $0.0073 |
| Unchanged Luna medium, 5 trials | 5 · 5 · 5 · 5 · 5 | 5 · 5 · 5 · 5 · 5 | 1 · 1 · 0 · 1 · 0 | 0 | $0.0086 |
| Calibrated medium, implicit cache, 5 trials | 5 · 0 · 5 · 0 · 0 | 3 · 0 · 0 · 3 · 0 | 0 · 1 · 1 · 1 · 1 | 6 | $0.0098 |
| Calibrated medium, implicit cache disabled, 2 trials | 5 · 0 | 4 · 0 | 1 · 1 | 2 | $0.0041 |

Medium effort removed the complete-answer false failures under the unchanged
prompt, but it over-scored the mechanism-only answer as composite 5 in all five
trials. That preserves the scheduler's Accuracy signal but inflates the score
shown to the learner.

A narrow OpenAI-only calibration explicitly separated learner evidence from
trusted grading authority and added Depth and Boundaries anchors. It scored the
mechanism-only answer correctly when it processed the answer, but valid
structured responses still intermittently claimed that non-empty answers were
missing. Setting [`prompt_cache_options.mode`](https://developers.openai.com/api/docs/guides/prompt-caching)
to `explicit` with no breakpoint, the documented way to disable GPT-5.6's
implicit cache, produced zero cache-read tokens and reproduced both false
failures on the second trial. The experiment stopped after that falsified the
cache hypothesis.

The matrix and prompt diagnostics made 54 calls and cost $0.0322, including one
three-call intermediate calibration trial not shown in the table. Concurrency,
reasoning effort, explicit evidence instructions, axis anchors, and automatic
prompt caching have now been ruled out as sufficient fixes. Retrying is not a
safe production repair: the bad result is valid structured output and cannot be
distinguished from a genuinely wrong learner answer without already knowing
the answer's score.

### Claude comparison and decision

| Provider | Scoring exact | Scoring mean deviation | False Accuracy pass / fail | Coached exact / within one | Full-pack calculated cost |
|---|---:|---:|---:|---:|---:|
| Claude Sonnet 5 low | 13/18 | 0.28 | 0 / 0 | 6/12 · 11/12 | $0.1617 |
| GPT-5.6 Luna low | 12/18 | 0.67 | 0 / 2 | 7/12 · 11/12 | $0.0128 |

Claude's cost is calculated from its recorded 55,795 input and 5,012 output
tokens at the published promotional $2/$10 per-million rate through 2026-08-31.
Luna was about 92% cheaper and slightly better on coached exact agreement, but
Claude was materially more reliable on first-pass scoring and produced no false
Accuracy failures.

The production scorer therefore remains Claude. Luna does not advance to a
production adapter or the 60–100-case release gate. The focused experiment
showed that more Luna prompt tuning, cache changes, retries, or a medium-effort
switch cannot provide an honest single-call scorer. If an OpenAI scorer remains
a product goal, the next candidate should be a stronger model such as GPT-5.6
Terra, starting with a separately cost-capped canary rather than another Luna
configuration.

Both complete result files were replayed with `OPENAI_API_KEY` unavailable. All
30 fingerprints resumed, zero new paid calls were scheduled, and both runs
reported `$0.0000`.

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
- Prompt caching: no explicit breakpoints; the primary pack reported no cache
  reads, while the controlled rerun reported 1,064 automatically cached input
  tokens.
- Optional GPT-5.6 features: no Pro mode, programmatic tool calling, multi-agent,
  or explicit cache writes.

The current OpenAI integration guidance recommends the Responses API and an
explicit reasoning effort for GPT-5.6. The experiment intentionally adopts no
other provider-specific behavior, so a measured score difference belongs to the
model rather than an unrelated feature change.
