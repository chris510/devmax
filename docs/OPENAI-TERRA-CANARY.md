# OpenAI Terra scoring canary

**Run date:** 2026-08-08

**Decision:** The focused canary passes. Terra may advance to the reviewed
30-case pack, but this result does not authorize a production provider switch.

## Why this canary exists

GPT-5.6 Luna was inexpensive, but it was not repeatable enough for the app's
scheduler-critical scoring path. Valid learner answers sometimes received
`mechanism_accuracy: 0`, and the failures remained under concurrency 1, medium
effort, prompt calibration, and disabled implicit caching.

Terra is the next stronger OpenAI model to test. This canary deliberately uses
the three decision-driven estimation cases that exposed Luna's problem:

1. a complete answer, expected composite 5;
2. a mechanism-only answer, expected composite 3;
3. irrelevant ritual math, expected composite 1.

The test repeats all three cases three times. Repetition matters because a
single successful rerun previously hid Luna's instability.

## Frozen configuration

- Model: `gpt-5.6-terra`
- API: Responses API with strict structured output
- Reasoning effort: `medium`
- Concurrency: 1
- Output cap: 1,024 tokens per call
- Prompt and parser: unchanged production-compatible evaluation path
- Trials: three independent fresh runs, nine paid calls total
- [Standard short-context pricing](https://developers.openai.com/api/docs/pricing)
  used for the guard: $2/M input and $12/M output
- Raw JSONL records: retained locally under ignored `api/.eval-results/`; no
  response IDs, API keys, or raw results are committed

The free preflight estimated a deliberately conservative ceiling of $0.080216
per trial, or **$0.240648 total**. The ceiling uses a UTF-8 byte bound plus a
provider-framing allowance and assumes every response consumes the entire
1,024-token output cap. It is a refusal threshold, not a prepaid amount.

## Acceptance gate

The canary could pass only with:

- zero false `mechanism_accuracy` passes;
- zero false `mechanism_accuracy` failures;
- no catastrophic score changes;
- every composite within one point of the reviewed expectation; and
- stable results across all three trials.

`mechanism_accuracy` is the critical axis because it is the only model output
that reaches SM-2. Composite movement is still reviewed, but it is display-only.

## Results

| Case | Expected | Trial 1 | Trial 2 | Trial 3 | Accuracy axes |
| --- | ---: | ---: | ---: | ---: | --- |
| Complete | 5 | 5 | 5 | 5 | 5, 5, 5 |
| Mechanism only | 3 | 4 | 4 | 4 | 5, 5, 5 |
| Ritual math | 1 | 0 | 1 | 1 | 0, 1, 1 |

The canary passes all five gates:

- **0 false Accuracy passes and 0 false Accuracy failures across 9 calls.**
- Every composite was exact or one point from the reviewed score.
- The complete answer was exactly 5 in all three trials.
- The mechanism-only answer was stably one point high at composite 4. Its
  Accuracy was correctly 5 every time; the excess came from Depth and
  Boundaries, so it cannot alter scheduling.
- The negative answer stayed safely failing at composite 0 or 1 and Accuracy 0
  or 1.
- All responses parsed through the unchanged strict schema. No missing-answer
  hallucination or other catastrophic output appeared.

### Token, latency, and cost audit

| Measure | Result |
| --- | ---: |
| Paid calls | 9 |
| Input tokens | 9,333 |
| Cached input tokens | 2,128 |
| Output tokens | 1,886 |
| Median provider latency | 3.12 s |
| Mean provider latency | 3.48 s |
| Slowest call | 5.74 s |
| Conservative runner cost | **$0.041298** |
| Cost using the published $0.20/M cached-input rate | **$0.037468** |

The conservative measured result is about 17% of the approved $0.240648
ceiling and about $0.00459 per scoring call. OpenAI bills consumed tokens, not
the ceiling. The lower cache-adjusted figure is a calculation from the returned
cached-token counts; the platform billing record remains authoritative.

A keyless replay restored all three records from one trial, scheduled zero new
calls, and reported $0.0000 new paid-call cost.

## Interpretation

This is materially better evidence than the Luna reruns. Terra preserved the
scheduler-critical Accuracy distinction across the exact cases where Luna had
returned valid but catastrophically wrong zeros. It also remained stable across
independent calls without prompt changes, retries, voting, or hidden knowledge
of the expected score.

The canary is intentionally too small to justify production use. It does not
establish performance across the other Week 1 rubrics, coached re-attempts, or
the broader 60–100-case provider-switch gate. Production therefore remains on
Claude.

## Recommended next experiment

Run the existing reviewed Week 1 pack on Terra at medium effort and concurrency
1:

- 18 scoring cases;
- 12 coached re-attempt cases;
- the unchanged schemas, parser, and grounding manifest; and
- a fresh output file followed by a keyless resume proof.

Free preflights put the current hard ceilings at $0.4840 for scoring and $0.2379
for coached re-attempts, or **$0.7219 total**. These are intentionally loose
bounds, not expected spend. Applying Terra's 10x token rates to the prior Luna
full-pack usage suggests roughly **$0.13 actual**, before any cache discount;
medium effort may move that figure, so the full pack needs its own explicit
budget approval.

The full pack should stop and reject Terra on any false Accuracy pass, false
Accuracy failure, coached/unaided attribution error, schema failure, or
catastrophic score. Passing it would advance Terra to the larger 60–100-case
evaluation and adapter design review—not directly to production.
