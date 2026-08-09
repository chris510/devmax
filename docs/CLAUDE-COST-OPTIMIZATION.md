# Claude evaluation cost optimization

**Status:** investigation and implementation plan
**Measured:** August 7, 2026
**Scope:** paid scoring and coached re-attempt evaluation scripts; no production
prompt, model, scheduler, or scoring changes

## Decision

The approximately **$0.40** evaluation was expensive for routine iteration, but
it was not a billing anomaly. The retained run made 60 Claude Sonnet 5 requests:
18 scoring cases and 12 coached re-attempt cases at both `low` and `medium`
effort. Its recorded 111,590 input tokens and 11,453 output tokens cost an
estimated **$0.338** at the current introductory rate. Exploratory reruns,
including corrected or focused fixtures, explain why the console total can be
closer to the observed $0.40.

The production setting should remain Sonnet 5 at `low` effort. The evaluation
process, not the product's grading contract, is the first place to optimize.
Three changes have the highest value:

1. Make the normal evaluation run only the shipping `low` effort.
2. Send full, non-interactive evaluation suites through the Message Batches API.
3. Add a cheap risk-stratified smoke suite, case filtering, saved JSONL results,
   and a hard preflight budget before any paid requests are submitted.

Together, the first two changes reduce a comparable full run from approximately
**$0.338 to $0.081 today**, a 76% reduction. A 10-case batched smoke run should
cost approximately **$0.027**. The estimates rise after Sonnet 5's introductory
pricing ends on September 1, 2026, so the harness should calculate prices from an
explicit versioned rate table rather than embedding the current promotion as a
permanent assumption.

## What the run spent

The source measurements are the final cost-complete Week 1 live evaluation
recorded in [PR #31](https://github.com/chris510/devmax/pull/31). Cache read and
write counts were zero in every row.

| Suite | Effort | Calls | Input tokens | Output tokens | Estimated cost now | Estimated cost from Sep. 1 |
|---|---:|---:|---:|---:|---:|---:|
| Scoring | low | 18 | 35,267 | 4,068 | $0.111 | $0.167 |
| Scoring | medium | 18 | 35,267 | 5,029 | $0.121 | $0.181 |
| Re-attempt | low | 12 | 20,528 | 944 | $0.050 | $0.076 |
| Re-attempt | medium | 12 | 20,528 | 1,412 | $0.055 | $0.083 |
| **Total** | | **60** | **111,590** | **11,453** | **$0.338** | **$0.507** |

The estimates use Anthropic's published Sonnet 5 rates:

- through August 31, 2026: $2 per million input tokens and $10 per million
  output tokens;
- beginning September 1, 2026: $3 per million input tokens and $15 per million
  output tokens.

The calculation is:

```text
(input_tokens × input_price / 1,000,000)
+ (output_tokens × output_price / 1,000,000)
```

These are estimates rather than invoice reconciliation. They exclude failed or
retried requests that are not represented in the saved measurements, taxes,
credits, negotiated pricing, regional multipliers, and any calls made while
repairing fixtures.

## Production usage is already mostly cost-conscious

The product has five Claude workloads, and their economics are different from an
offline test:

| Workload | Current model/effort | Timing | Cost assessment |
|---|---|---|---|
| Question generation | Haiku 4.5 / no effort control | Once, only when a card is opened | Already uses the lower-cost model and never spends on an ignored push |
| Session scoring | Sonnet 5 / low | User is waiting | Quality-critical; keep synchronous and keep the measured winning effort |
| Coached re-attempt | Sonnet 5 / low | Optional after a failed mechanism | Narrow schema and 512-token ceiling already constrain output |
| Study Plan import | Opus 5 / high | Rare, roughly quarterly | Highest one-call exposure, but the long static rubric is already cached and quality has multi-week consequences |
| Card proposal | Sonnet 5 / low | On demand after eligible plan work | Bounded to at most three candidates; not an automatic background call |

The repository also has account-level daily call limits. Those prevent runaway
volume but do not forecast dollars. A later production-observability change could
record cost estimates beside the existing token usage logs and alert on daily or
per-operation budgets. It should not store prompts or learner answers in cost
telemetry.

The Study Plan importer deserves its own live cost/quality study after this
evaluation harness is cheaper. `medium` effort may reduce a single import's
thinking output, but it is currently untested and must not replace `high` solely
on price: a bad import affects an entire plan. Free input-token preflight and an
explicit estimated-cost confirmation are safe to add before that experiment.

## Why it cost this much

### 1. The default sweep compares two effort levels

Both live runners default to `low medium`. That was appropriate while selecting
the production setting, but it doubles the call count during ordinary fixture or
prompt iteration. The current measurements do not justify continuing to pay for
`medium` by default:

- scoring `low` had 13/18 exact composites versus 12/18 for `medium`;
- both had zero false accuracy passes and zero false accuracy failures;
- `medium` used 24% more scoring output tokens;
- re-attempt `medium` used 50% more output tokens and introduced one false
  reconstruction failure that `low` did not.

Effort comparison remains valuable only when a model, rubric, schema, or effort
setting is genuinely being reconsidered.

### 2. Every fixture edit currently encourages a full rerun

The runners accept a case file but have no built-in `--case`, `--tag`,
`--changed-from`, or `--resume` selection. They also print results without saving
a durable machine-readable artifact. A one-case correction can therefore lead to
another 30- or 60-call run simply to recover comparable evidence.

### 3. The calls use the synchronous Messages API

Immediate responses are useful for production sessions, but unnecessary for an
offline evaluation. Anthropic documents a 50% discount on both input and output
tokens for the Message Batches API, and specifically lists large-scale
evaluations as a good fit. Most batches finish within one hour, which is acceptable
for this workflow.

### 4. Prompt caching correctly stayed at zero

The zero cache totals are not an instrumentation defect. Sonnet 5 requires a
minimum 1,024-token cacheable prefix. The code's measured static prefixes are
approximately 770 tokens for scoring and 810 for re-attempt grading. A cache
marker below the minimum silently does nothing, exactly matching the observed
usage fields.

The changing answer basis, approved rubric, question, and learner answer live in
one user-content block. The system rubric alone therefore cannot be cached. A
future experiment could split the user content into a fixed per-card grounding
block followed by the changing learner transcript. Rubric plus grounding may
clear 1,024 tokens, and the Week 1 fixtures reuse each card for two or three
cases. That is a possible evaluation-only cache prefix, not a current saving:

- the request builder and prompt order would have to remain identical between
  synchronous and batch transports;
- only the cache marker should be evaluation-specific;
- the first response must begin before concurrent requests can hit its cache;
- batch cache hits are best-effort rather than guaranteed;
- production reviews occur days apart, beyond the five-minute or one-hour TTL,
  so enabling the marker in live sessions would usually pay a more expensive
  write without receiving a read.

Measure this only after batch support exists. Padding either system rubric merely
to pass 1,024 tokens would add input to every request and is not justified.

There is one separate, valid cache use in the product: the Study Plan import
rubric clears its model's minimum and is already cached. It should not be changed
as part of this work.

### 5. `max_tokens` is a ceiling, not the observed bill

The 8,000-token scoring limit is not charged in full; actual output is billed.
Lowering it would reduce worst-case exposure but could also truncate adaptive
thinking or structured output. It is therefore a secondary guardrail to test,
not the first cost lever.

## Recommended evaluation ladder

Paid evaluation should be a sequence of increasingly expensive gates. Passing a
cheap gate gives permission to run the next one; it should not happen
automatically on every edit.

| Gate | Purpose | Calls | Mode | Expected current cost |
|---|---|---:|---|---:|
| 0. Offline validation | JSON shape, grounding approval, labels, duplicates, invariants | 0 | local | $0 |
| 1. Smoke | Highest-risk boundaries and coached/parrot distinctions | 10 | batch preferred | ~$0.027 |
| 2. Changed cases | Verify only cases whose request fingerprint changed | variable | batch preferred | ~$0.002–$0.006 per case |
| 3. Shipping baseline | Full scoring + re-attempt suites at `low` | 30 | batch | ~$0.081 |
| 4. Configuration comparison | Full `low` versus candidate effort/model | 60 | batch | ~$0.169 |

The table uses current introductory prices and the observed average token sizes.
At standard September pricing, multiply these estimates by 1.5.

The 10-case smoke suite should be deliberately selected, not the first ten cases:

- two accuracy pass/fail boundary cases;
- one confidently wrong technical detail;
- one voice-transcription artifact;
- one shallow but accurate answer;
- one strong answer with boundaries;
- two coached reconstructions;
- one coached parrot;
- one coached answer that remains wrong.

If the change touches only scoring, run the six scoring sentinels. If it touches
only re-attempt grading, run the four re-attempt sentinels.

## Ranked improvements

### P0 — change the default from comparison to validation

Make each runner default to the configured shipping effort (`low`) instead of
`low medium`. Keep `--levels low medium` as an explicit calibration command.

**Expected saving:** approximately 52% for the measured full run.
**Quality risk:** none; this stops measuring a non-shipping configuration by
default.
**Acceptance:** an explicit two-level invocation produces the existing comparison,
while the default makes exactly one call per case.

### P0 — add a Message Batches execution mode

Build the same standard Messages request parameters used by production, including
the same system rubric, user content, schema, model, effort, and token ceiling.
Submit each case with a stable `custom_id`, poll for completion, and restore source
order by ID when reading the JSONL results.

Use the synchronous path for one request-shape smoke test and interactive
debugging. Use batch for full suites.

**Expected saving:** 50% on batched input and output, stacking with the low-only
change.
**Quality risk:** low, provided request parity is tested. Batch requests accept
standard Messages API parameters, but asynchronous validation means the harness
must surface per-case errors clearly.
**Acceptance:** both transports use the same request builder, parsed schema, and
metric calculation; errored, canceled, and expired cases fail the evaluation
rather than disappearing. Exact model answers need not match across independent
calls.

### P0 — show and enforce a preflight budget

Before submitting a paid run:

1. Call Anthropic's free token-counting endpoint with the exact model and request
   shape for every selected case.
2. Estimate input cost exactly from those counts.
3. Estimate output cost from the most recent matching run, with a conservative
   percentile or explicit fallback.
4. Print calls, model, efforts, transport, estimated cost, and the active price
   schedule.
5. Refuse to submit above `--max-cost-usd` unless the caller supplies an explicit
   override.

Recommended defaults:

- smoke or changed-case run: `$0.05` current / `$0.08` standard pricing;
- full low-only batch: `$0.10` current / `$0.15` standard pricing;
- comparison sweep: no default approval; require an explicit budget.

**Expected saving:** prevents accidental broad runs rather than reducing a valid
run's unit price.
**Quality risk:** none.
**Acceptance:** dry-run mode makes zero Message calls, and the budget uses the
selected model's token count rather than a stale tokenizer estimate.

### P1 — save results and support targeted reruns

Add:

- `--case NAME` repeatable filtering;
- `--tag TAG` for risk-stratified groups;
- `--output PATH` for a JSONL artifact;
- `--resume PATH` to reuse completed results;
- a request fingerprint over model, effort, rubric, schema, normalized hydrated
  case payload, and relevant code version.

Reuse a saved result only when the fingerprint matches exactly. Always offer
`--fresh` for intentional repeatability or nondeterminism studies. Never treat a
cached historical result as a new live sample.

**Expected saving:** 80–97% during small fixture edits when only one to six cases
changed.
**Quality risk:** stale evidence if the fingerprint omits an input.
**Acceptance:** changing any prompt, schema, grounding field, answer, model, or
effort invalidates the affected result.

### P1 — make the smoke suite a first-class manifest

Tag sentinel cases in the evaluation data rather than copying them to temporary
files. The tag selection should remain stable enough to compare runs, while the
full suite remains the release gate.

**Expected saving:** roughly 67% versus a 30-call low-only diagnostic run.
**Quality risk:** smoke can miss a regression outside its strata.
**Acceptance:** smoke is never reported as a full-suite pass.

### P2 — investigate a lower scoring output ceiling separately

Collect the maximum and high-percentile output usage from several clean `low`
runs. Trial a smaller `max_tokens` value with headroom, and reject any candidate
that increases truncation, parsing failures, false accuracy passes, or latency.

**Expected saving:** little in normal runs; meaningful only as runaway protection.
**Quality risk:** medium because thinking and the JSON response share the limit.
**Acceptance:** no `max_tokens` stop reasons or parse retries across the release
evaluation set.

### P2 — measure a per-card cache prefix

After batch mode is working, split the existing prompt into semantically identical
content blocks so rubric plus stable per-card grounding can be counted as a
candidate prefix. Use the free token-counting endpoint and proceed only for cards
whose prefix clears 1,024 tokens. Run one small experiment with and without the
cache marker and compare billed tokens, hit rate, results, and latency.

**Expected saving:** unknown and secondary to the guaranteed batch discount.
**Quality risk:** low if text and ordering are byte-identical, but parity must be
proved.
**Acceptance:** positive measured dollar saving after cache-write cost, no prompt
text change, and no cache marker on ordinary production review calls.

## Options not recommended

### Do not switch the release evaluation to Haiku

A cheaper model can lint fixture clarity or generate hypotheses, but it cannot
prove that the shipping Sonnet grader behaves correctly. The paid release gate
must exercise the production model and prompt.

### Do not shorten the production prompt only for the main evaluation

A compact evaluation-only rubric or numeric-only schema would measure a different
system. Such a mode could be useful as an explicitly non-authoritative diagnostic,
but it cannot replace parity evaluation.

### Do not pad the rubrics or enable rubric-only caching

The scoring prefix is roughly 254 tokens short of Sonnet 5's caching minimum, and
the re-attempt prefix is roughly 214 tokens short. Adding irrelevant text to every
request to buy a cache entry makes the prompt worse and the economics dependent
on hit timing. A measured rubric-plus-grounding experiment is different because
it reuses real existing context. Batch processing should still come first because
it provides a certain 50% discount without prompt distortion.

### Do not run live paid evaluation in ordinary CI

Model variability, secrets, latency, and spend make this a manual calibration or
release workflow. CI should run the deterministic offline gates and validate the
runner itself with fakes.

## Implementation sequence

### PR A — safer synchronous runner

- default to the configured shipping effort;
- add `--case`, `--tag`, `--output`, `--resume`, and `--fresh`;
- emit a common JSONL result schema for both scoring and re-attempt suites;
- add free token-count preflight, `--dry-run`, and `--max-cost-usd`;
- unit-test selection, fingerprints, resumption, and budget refusal without live
  API calls.

This provides immediate protection even before batch support exists.

### PR B — batch transport

- extract one request builder so synchronous and batch paths cannot drift;
- submit stable IDs and save the batch ID immediately for recovery;
- poll with bounded frequency;
- persist raw results before metric calculation;
- classify succeeded, errored, canceled, and expired requests;
- verify one tiny paid batch against the synchronous request shape, then use batch
  for full suites.

### PR C — evidence policy and telemetry

- commit the stable smoke tags and their coverage rationale;
- record estimated and actual cost in every result artifact;
- warn when actual cost exceeds the estimate materially;
- document when a full comparison is required: model change, effort change,
  rubric/schema change that affects both paths, or a scheduled calibration gate.

## Success criteria

The optimization is complete when:

- a routine run evaluates only `low` unless comparison is explicitly requested;
- no paid run starts without showing its selected cases and cost ceiling;
- a full low-only evaluation uses batch and is expected to stay below $0.10 at
  current pricing or $0.15 at standard pricing;
- a normal smoke evaluation stays below $0.05 current or $0.08 standard;
- changing one case can rerun one case without rebuilding a temporary fixture;
- every paid response, usage record, error, and request fingerprint is saved;
- prompt/schema/model parity between synchronous production evaluation and batch
  evaluation is covered by tests;
- quality gates remain unchanged: especially zero false accuracy passes and no
  false coached-reconstruction claims.

## Immediate operating policy

Until the harness improvements are implemented:

1. Run offline validation first.
2. Pass `--levels low` explicitly.
3. Create a small, reviewed case file for the changed risk before running the full
   suite.
4. Save terminal output immediately with the commit and case-file hashes.
5. Run `low medium` only when making an actual configuration decision.
6. Do not enable rubric-only prompt caching or production session caching; defer
   the per-card evaluation experiment until batch support exists.

This policy needs no production change and would have reduced the retained run
from about $0.338 to about $0.162 even before batch support.

## Official references

- [Anthropic model and feature pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Message Batches API](https://platform.claude.com/docs/en/build-with-claude/batch-processing)
- [Prompt caching thresholds and pricing](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Token counting API](https://platform.claude.com/docs/en/build-with-claude/token-counting)
