# OpenAI hybrid scoring release pack

**Status:** The 42 new labels were accepted when the owner merged PR #41. No
OpenAI request, file upload, or paid Batch has been made for this release pack.

## What the release pack adds

The existing 18 reviewed Week 1 cases remain frozen in
`api/scripts/grounded_effort_cases_week1.json`. A second file adds 42 approved
cases, giving the planned 60 unique scheduler-grade cases when the two files
are loaded together.

| New family | Cases | What the label review must protect |
| --- | ---: | --- |
| Partial self-correction | 6 | Grade the learner's final explicit correction, not the retracted clause |
| Source-compatible alternative | 6 | Accept an approved equivalent without requiring rubric wording |
| Speech-to-text noise | 6 | Ignore recoverable fillers and transcription artifacts |
| Fluent adjacent jargon | 6 | Do not award Accuracy for vocabulary without the required mechanism |
| Follow-up anchored | 6 | Credit only evidence actually supplied across the two learner turns |
| Prior-summary contradiction | 6 | Current recall outranks a stale mastery summary |
| Depth/Boundaries isolation | 6 | Keep the two secondary axes independent |
| **New approved cases** | **42** | |

Every approved Week 1 card contributes one case to every family. Twelve cases
carry `risk-smoke`: one noisy-but-correct and one fluent-but-wrong answer per
card. That is the repeatability subset from the evaluation plan.

The release file deliberately contains no question, answer basis, or rubric.
The runners hydrate those fields from the six `grounding_status: approved`
entries in `api/cards.json`. Offline tests verify all 42 labels derive the
declared composite through `llm.derive_composite`, all names are unique, every
family has six cases, and all cases hydrate against approved authority.

## Human label approval

Each new case has `review_status: "approved"` and retains the `review_note` that
explains its axis boundary. PR #41 requested review of every expected Accuracy,
Depth, Boundaries, derived composite, and note against approved grounding; the
owner's merge records acceptance of that review pack. The runner still refuses
any future case carrying an explicit status other than `approved`.

The approval follows these rules:

- Accuracy is the mechanism/retention signal and the only axis that may reach
  SM-2. Correct is 3–5; incorrect or materially partial is 0–2.
- Depth covers trade-offs. Merely naming backoff, cost, or complexity is not
  automatically independent trade-off evidence.
- Boundaries covers failure modes and misconceptions. It should not inherit
  credit from fluent adjacent terminology.
- The composite must stay derived in code. A failing Accuracy caps it; a
  correct mechanism alone is 3; demonstrated trade-offs without a failure mode
  is 4; demonstrated failure-mode awareness makes it 5.
- For a follow-up case, judge the initial and follow-up learner answers
  together, but do not import facts from the model's probe.
- For a stale-summary case, judge the current transcript. The prior summary is
  context, not answer evidence.

## Batch implementation and safety boundary

`api/scripts/openai_batch_bakeoff.py` implements the official asynchronous
workflow without touching production routing:

1. build the exact `/v1/responses` request body used by the standard runner;
2. write one official JSONL line per fingerprinted request;
3. upload it with file purpose `batch`;
4. create a `/v1/responses` Batch with the required `24h` completion window;
5. persist an ignored local state file containing the Batch ID and request map;
6. explicitly collect the completed output file; and
7. write the same resumable result JSONL schema used by the standard runner.

The implementation follows the [OpenAI Batch create
contract](https://developers.openai.com/api/reference/resources/batches/methods/create)
and [file upload contract](https://developers.openai.com/api/reference/resources/files/methods/create).
The service accepts JSONL Batch inputs up to 200 MB; this pack is under 0.4 MB.

Safety properties are structural:

- `submit --dry-run` needs no API key and performs no network request;
- a non-dry submission requires an explicit `--max-cost-usd` acknowledgement;
- an estimate over that acknowledgement is refused before upload;
- any explicit review status other than `approved` is refused before upload;
- exact result fingerprints supplied through `--resume` are not submitted;
- Batch output must contain exactly one known `custom_id` per pending request;
- each successful result is flushed immediately, so a later malformed result
  cannot erase already incurred evidence; and
- Batch state and result files live under the ignored `.eval-results/` path and
  never contain an API key.

Batch does not provide meaningful per-request interactive latency. The
12-request standard smoke measures latency; Batch stages measure grading
quality, tokens, parsing, repeatability, and cost.

## Free preflight results

All figures below were computed locally with Terra medium, the unchanged strict
scoring schema, and the same 1,024-token output cap as the reviewed 18-case
Terra run.

| Stage | Calls | Rate | Authorization ceiling | Expected from observed Terra run |
| --- | ---: | ---: | ---: | ---: |
| Standard risk smoke | 12 | $2/M input · $12/M output | $0.3218 | about $0.0541 |
| Batch remaining unique cases | 48 | $1/M input · $6/M output | $0.6469 | about $0.1081 |
| Batch risk repeat, each run | 12 | $1/M input · $6/M output | $0.1609 | about $0.0270 |
| Two Batch repeats | 24 | Batch | $0.3218 | about $0.0541 |
| **Complete 84-call gate** | **84** | Mixed | **$1.2905** | **about $0.2163** |

The authorization ceiling is intentionally much higher than the expected
charge. It treats every visible UTF-8 byte as a possible input token, adds a
2,048-token provider-framing allowance to every request, and assumes every
response consumes the entire 1,024-token cap. This remains safe without a key
or tokenizer, but it is not a forecast.

The observed 18-case Terra run used 18,809 input and 3,624 output tokens: about
1,045 input and 201 output tokens per call, with a maximum of 394 output
tokens. Applying that observed per-call cost and the published 50% Batch
discount produces the approximately $0.216 projection. Provider billing is
authoritative; the ceiling is what must be separately acknowledged at each
stage.

## Reproducing the free preflights

Run from `api/`. These commands make no API requests:

```bash
# 12 standard-latency risk cases
uv run python scripts/openai_bakeoff.py scoring \
  scripts/grounded_effort_cases_week1_release.json \
  --grounding-manifest cards.json --tag risk-smoke \
  --model gpt-5.6-terra --levels medium \
  --max-output-tokens 1024 --dry-run

# 48 remaining unique cases at Batch rates
uv run python scripts/openai_batch_bakeoff.py submit \
  scripts/grounded_effort_cases_week1.json \
  scripts/grounded_effort_cases_week1_release.json \
  --grounding-manifest cards.json --exclude-tag risk-smoke \
  --model gpt-5.6-terra --levels medium \
  --max-output-tokens 1024 --dry-run

# One 12-case Batch repeat; run twice only after the first 60 calls pass audit
uv run python scripts/openai_batch_bakeoff.py submit \
  scripts/grounded_effort_cases_week1_release.json \
  --grounding-manifest cards.json --tag risk-smoke \
  --model gpt-5.6-terra --levels medium \
  --max-output-tokens 1024 --dry-run
```

Removing `--dry-run` is not sufficient to spend. An API key must be present and
`--max-cost-usd` must acknowledge that stage's freshly printed ceiling.
Submission prints the local state path. After the Batch reports completion,
collect it explicitly:

```bash
uv run python scripts/openai_batch_bakeoff.py collect \
  .eval-results/openai-batch-state-<timestamp>.json
```

## What remains blocked

This approval does not authorize a paid call, change any production provider,
or implement fallback routing. The next separately authorized action is only
the 12-case standard smoke. Terra may move to production adapter design only
after the full 84-call gate in `OPENAI-HYBRID-SCORING-PLAN.md` passes.
