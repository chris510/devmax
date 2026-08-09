# OpenAI hybrid scoring evaluation plan

**Status:** Release evaluation stopped after the failed standard smoke;
production is unchanged.

**Gate result:** The authorized 12-case standard smoke failed the composite
criterion even though Accuracy had zero false passes or failures. The remaining
Batch stages are stopped and Terra does not advance to adapter design. See
[`OPENAI-TERRA-RELEASE-SMOKE.md`](OPENAI-TERRA-RELEASE-SMOKE.md).

**Preparation update:** The 42 additional labels and cost-guarded Batch tooling
landed in PR #41, and the reviewed labels were approved in PR #42. The approved
pack and execution controls are recorded in
[`OPENAI-HYBRID-RELEASE-PACK.md`](OPENAI-HYBRID-RELEASE-PACK.md). Paid stages
still require separate cost authorization.

## Decision

GPT-5.6 Terra is not a safe single-provider replacement for Claude. It passed
the reviewed scheduler-grade scoring pack but failed coached grading. The next
candidate architecture is deliberately narrower:

| Product path | Candidate provider | Reason |
| --- | --- | --- |
| `score_answer` after the initial answer or one follow-up | GPT-5.6 Terra, medium | Passed 18/18 within one with zero false Accuracy passes or failures |
| `score_reattempt` after the correction | Claude Sonnet 5, low | Terra produced three false reconstruction failures |
| Canonical-question generation | Claude Haiku 4.5 | Not evaluated on OpenAI; changing it would confound retrieval stability |
| Study Plan import | Claude Opus 5 | High-authority quarterly extraction; outside this experiment |
| Card proposals | Claude Sonnet 5 | Must remain aligned with the current authoring and approval path |

This is a candidate for evaluation, not an implementation decision. Production
remains entirely on Claude until the release gate below passes and a separate
adapter design is reviewed.

## Why the narrower conclusion is justified

Only the `score_answer` result can move SM-2. Terra's reviewed 18-case run had:

- zero false Accuracy passes;
- zero false Accuracy failures;
- 18/18 composites within one point;
- mean composite deviation 0.33, compared with Luna's 0.67 and Claude low's
  0.28; and
- $0.081106 calculated cost, compared with $0.111214 for the recorded Claude
  scoring run at its promotional rate.

Terra's coached failure remains real. Three correct reconstructions were called
parroting and scored 2. That path does not reach SM-2 or replace the displayed
session score, but its mastery summary becomes context for the next grading
call. The failure therefore blocks Terra from coached grading without proving
that Terra is unsafe on the separate scheduler-grade path.

## Observed hybrid economics

Using the reviewed packs as a normalization rather than a traffic forecast:

| Configuration | Through 2026-08-31 | From 2026-09-01 |
| --- | ---: | ---: |
| Claude scoring + Claude coached | $0.161710 | $0.242565 |
| Terra scoring + Claude coached | $0.131602 | $0.156850 |
| Hybrid savings | 18.6% | 35.3% |

Real savings depend on the mix of initial answers, follow-ups, and optional
coached turns. Because coached re-attempts occur only after a failed mechanism
and explicit user opt-in, a real workload may weight the Terra scoring path more
heavily than this balanced 18-plus-12 evaluation. Question generation, Study
Plan import, and card proposals remain Claude costs and are intentionally not
hidden inside the comparison.

## Grounding constraint

Only the six Week 1 cards have approved answer bases, rubrics, and canonical
questions. The other 48 curriculum cards still require source-grounded drafts
and human approval. Their topic names and URLs are not authority, so this gate
must not fabricate cases for them.

The release pack will deepen behavioral coverage across the six approved cards.
It can establish robustness to answer shape and conversational noise, but it
cannot claim broad topical coverage. Grounding more curriculum cards remains a
separate content milestone before a final production switch.

## Sixty-case first-pass pack

Keep the existing 18 reviewed cases, then add seven six-case families—one case
per approved card—for 60 total:

| Family | Count | Risk tested |
| --- | ---: | --- |
| Existing complete, mechanism-only, and confidently wrong | 18 | Frozen baseline and direct Claude/Luna/Terra comparison |
| Partial self-correction | 6 | Final mechanism is correct after an initially wrong clause |
| Plausible source-compatible alternative | 6 | Avoid penalizing a valid explanation that does not mirror the rubric wording |
| Speech-to-text noise | 6 | Fillers, false starts, punctuation loss, and recoverable transcription errors |
| Fluent adjacent jargon | 6 | Confident vocabulary without the required mechanism |
| Follow-up anchored answer | 6 | Grade only what the learner actually adds after the probe |
| Prior-summary contradiction | 6 | Current transcript outranks stale or misleading mastery context |
| Depth/Boundaries isolation | 6 | Correct mechanism with exactly one independently demonstrated secondary axis |
| **Total** | **60** | |

Every new case must contain reviewed expected Accuracy, Depth, Boundaries, and
derived composite labels. It must hydrate its question, answer basis, and rubric
from the approved `api/cards.json` entry. A short review note should explain the
axis boundary being tested; labels are human-approved before any live call.

## Repeatability design

Select a 12-case risk subset containing, for each approved card:

- one correct but speech-noisy or self-corrected answer; and
- one fluent adjacent-jargon or plausible-wrong answer.

Run the subset once at standard latency as the smoke test. If it passes, run the
remaining 48 cases once, then run the 12-case subset two more times with fresh
requests. The completed evaluation makes 84 calls: 60 unique cases plus 24
repeatability calls. Concurrency remains 1 for the smoke and is recorded
explicitly for later stages.

## Acceptance gate

Terra advances to adapter design only if all of the following hold:

- zero false Accuracy passes across all 84 calls;
- zero false Accuracy failures across all 84 calls;
- zero missing-answer hallucinations or other catastrophic valid-schema output;
- every composite within one point of its reviewed expectation;
- every structured response parses through the unchanged production contract;
- follow-up cases use only evidence present in the learner's two answers;
- all corrective feedback is manually checked against the approved answer basis;
- the 12-case risk subset keeps the same pass/fail Accuracy bucket in all three
  trials; and
- per-axis exact and within-one agreement, latency, tokens, caching, and cost are
  reported without hiding deviations behind the composite.

The gate evaluates Terra only for `score_answer`. The existing Claude coached
pack remains the baseline for `score_reattempt`; Terra is not rerun there.

## Cost strategy

[OpenAI's current pricing](https://developers.openai.com/api/docs/pricing) lists
Terra standard short-context rates at $2/M input and $12/M output, and Batch at
$1/M input and $6/M output. The evaluation can wait for Batch; production
latency cannot.

At the observed $0.00451 per standard scoring call, 84 standard calls would be
roughly $0.38. A cost-conscious staging plan is approximately $0.22:

1. 12-case smoke at standard rates: about $0.054;
2. remaining 48 unique cases through Batch: about $0.108; and
3. two 12-case repeatability runs through Batch: about $0.054.

These are projections, not authorizations. Before any paid call, the completed
pack must pass offline validation, the runner must print exact standard and
Batch ceilings, and the user must approve each paid stage separately. If Batch
support would alter request semantics or structured-output behavior, run the
release gate at standard rates instead of accepting a cheaper but incomparable
test.

## Production adapter boundary after a pass

A passing evaluation authorizes design review, not deployment. The adapter must:

- route only `score_answer` to OpenAI;
- leave `score_reattempt`, question generation, Study Plan import, and card
  proposals on Claude;
- preserve the existing strict schema and `parse_score_result` contract;
- preserve the rule that only Accuracy reaches SM-2;
- fall back to Claude only on transport, timeout, or schema failure;
- never retry or fall back merely because a valid score looks surprising—the
  expected score is unknown in production;
- record provider, model, latency, token usage, and fallback reason without
  storing secrets or adding engagement analytics; and
- ship behind a reversible configuration flag with Claude as the default.

No production code should be written until the 84-call gate passes.
