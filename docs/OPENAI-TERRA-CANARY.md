# OpenAI Terra scoring canary

**Run date:** 2026-08-08

**Decision:** The focused canary and reviewed 18-case scoring stage pass, but
the 12-case coached re-attempt stage fails with three false reconstruction
failures. Terra does not advance to the larger release evaluation or a
production provider adapter. Production remains on Claude.

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

## Reviewed Week 1 scoring stage

After the canary merged, Terra ran the complete 18-case scoring pack at medium
effort and concurrency 1. The prompt, schema, parser, 1,024-token output cap,
and grounding manifest were unchanged. The free preflight's hard ceiling was
$0.483986; the user authorized $0.484.

### Aggregate result

| Measure | Terra medium | Luna low | Claude Sonnet 5 low |
| --- | ---: | ---: | ---: |
| Composite exact | 12/18 | 12/18 | 13/18 |
| Composite within one | 18/18 | 16/18 | — |
| Mean composite deviation | 0.33 | 0.67 | 0.28 |
| False Accuracy pass / fail | **0 / 0** | 0 / 2 | **0 / 0** |
| Accuracy exact / within one | 11/18 · 18/18 | 12/18 · 15/18 | 9/18 · 17/18 |
| Depth exact / within one | 7/18 · 16/18 | 9/18 · 15/18 | 10/18 · 17/18 |
| Boundaries exact / within one | 12/18 · 18/18 | 13/18 · 17/18 | 16/18 · 18/18 |
| Calculated scoring cost | **$0.081106** | $0.008730 | $0.111214 |

The Claude cost uses its recorded 35,267 input and 4,068 output tokens at the
promotional $2/$10 per-million rate through 2026-08-31. Terra's conservative
runner cost treats all input at $2/M. Applying the published $0.20/M rate to
the 1,064 returned cached-input tokens produces $0.079191 instead. Provider
billing remains authoritative.

Terra is not as cheap as Luna, but it removed Luna's scheduler-critical failure
and cost about 27% less than the current promotional Claude scoring run. Once
Claude's published promotion ends, the same recorded Claude usage would cost
$0.166821 at $3/$15, making this Terra run about 51% less expensive. Tokenizers
and generated output differ, so these are observed evaluation costs rather than
a guaranteed production ratio.

### Case matrix

| Case family | Complete | Mechanism only | Incorrect |
| --- | ---: | ---: | ---: |
| Delivery sequence | 5 / 5 | 3 / 3 | 0 / 1 |
| Non-functional requirements | 5 / 5 | 3 / 3 | 1 / 1 |
| Identity boundary | 5 / 5 | 4 / 3 | 1 / 1 |
| Timeout retry | 5 / 5 | 4 / 3 | 0 / 1 |
| Cursor pagination | 5 / 5 | 3 / 3 | 1 / 1 |
| Decision-driven estimation | 5 / 5 | 4 / 3 | 0 / 1 |

Each cell is `actual / expected`. All six complete answers scored 5. Every
incorrect answer remained in the failing Accuracy bucket. The three
mechanism-only composite 4s came from extra Depth or Boundaries credit; their
Accuracy remained correctly passing, so none can move SM-2 incorrectly.

### Usage, latency, and resume

| Measure | Result |
| --- | ---: |
| Paid calls | 18 |
| Input tokens | 18,809 |
| Cached input tokens | 1,064 |
| Output tokens | 3,624 |
| Median provider latency | 3.26 s |
| Mean provider latency | 3.43 s |
| Slowest call | 5.97 s |
| Conservative cost | **$0.081106** |
| Cache-adjusted calculation | **$0.079191** |

All 18 responses parsed through the strict schema. No missing-answer
hallucination or catastrophic score appeared. A keyless replay restored all 18
fingerprints, scheduled zero new calls, and reported $0.0000 new paid-call cost.
Raw JSONL records remain local and ignored.

The scoring stage therefore passes and Terra may advance to coached grading.
Production remains on Claude: first-pass scoring alone does not establish safe
coached attribution or satisfy the broader provider-switch release gate.

## Reviewed Week 1 coached stage

After the scoring stage merged, Terra ran the remaining 12 coached re-attempt
cases at medium effort and concurrency 1. The schema, parser, 512-token output
cap, and grounding manifest were unchanged. The free preflight's hard ceiling
was $0.237896; the user authorized $0.238.

### Result

| Measure | Terra medium | Luna low | Claude Sonnet 5 low |
| --- | ---: | ---: | ---: |
| Exact | 6/12 | 7/12 | 6/12 |
| Within one | 9/12 | 11/12 | 11/12 |
| Mean deviation | 0.92 | 0.50 | 0.58 |
| False parrot pass | **0** | **0** | 1 |
| False reconstruction fail | **3** | **0** | **0** |
| Calculated coached cost | **$0.035236** | $0.004107 | $0.050496 |

The three false failures were:

| Case | Expected | Actual | Model characterization |
| --- | ---: | ---: | --- |
| Identity boundary — reconstructed | 5 | 2 | Called the correct applied booking-owner answer parroting |
| Cursor pagination — reconstructed | 4 | 2 | Called the correct ordered-boundary reconstruction parroting |
| Decision-driven estimation — reconstructed | 5 | 2 | Called the correct heap-versus-sharding reconstruction parroting |

Those deviations are not borderline calibration differences. They turn
reviewed correct reconstructions into failing coached results and repeat the
same evidence-separation weakness that made Luna unsafe on first-pass scoring.
The outputs were valid structured responses, so production cannot distinguish
them from genuinely failed reconstructions without already knowing the grade.

All 12 mastery summaries did preserve coached attribution with language such as
"after being told" or "could only parrot." That gate passed. All responses also
parsed through the strict schema, and there were no false reconstruction passes.
The pack nevertheless fails because its acceptance gate required zero false
failures and no deviation greater than one.

### Usage, latency, and resume

| Measure | Result |
| --- | ---: |
| Paid calls | 12 |
| Input tokens | 11,462 |
| Cached input tokens | 0 |
| Output tokens | 1,026 |
| Median provider latency | 1.42 s |
| Mean provider latency | 1.73 s |
| Slowest call | 3.47 s |
| Conservative cost | **$0.035236** |

A keyless replay restored all 12 fingerprints, scheduled zero new calls, and
reported $0.0000 new paid-call cost. Raw JSONL records remain local and ignored.

## Single-provider decision

| Provider | First-pass false Accuracy pass / fail | Coached false pass / fail | Reviewed 30-case cost | Decision |
| --- | ---: | ---: | ---: | --- |
| Claude Sonnet 5 low | 0 / 0 | 1 / 0 | $0.161710 | Production baseline |
| GPT-5.6 Luna low | 0 / 2 | 0 / 0 | $0.012837 | Reject: unsafe first-pass scoring |
| GPT-5.6 Terra medium | 0 / 0 | 0 / 3 | $0.116342 | Reject as a single provider; retain as a first-pass candidate |

Terra's reviewed pack cost about 28% less than Claude at the promotional
$2/$10 rate and would be about 52% less than the same recorded Claude usage at
$3/$15. Cost does not overcome the coached failure: Terra was exact no more
often than Claude, was within one on fewer cases, and introduced three false
failures. Terra therefore fails as a drop-in replacement for every Claude path.

That does not erase the first-pass result. The scheduler-grade path and coached
path already have separate model configuration, coached grading never reaches
SM-2 or the displayed score, and Terra passed the scheduler-critical scoring
pack with zero false Accuracy decisions. A narrower hybrid remains worth
evaluating: Terra for `score_answer`, Claude for `score_reattempt` and every
other production model path. Production remains entirely on Claude until that
larger first-pass gate passes and the provider routing receives its own review.
See [OpenAI hybrid scoring evaluation plan](OPENAI-HYBRID-SCORING-PLAN.md).
