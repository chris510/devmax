# OpenAI Terra release smoke

**Status:** Failed release gate; Batch stages stopped

## Decision

GPT-5.6 Terra medium remains scheduler-safe on this 12-case risk subset, but it
does not meet Devmax's display-signal standard. Three noisy-but-correct answers
were overgraded by two composite points. The gate requires every composite to
be within one point of its approved label, so the remaining 48 unique cases and
two repeatability Batches must not run.

Production remains on Claude. This result does not authorize an OpenAI adapter,
fallback routing, or any provider change.

## Authorized run

| Control | Value |
| --- | --- |
| Cases | 12 approved `risk-smoke` cases: one noisy-correct and one fluent-wrong answer per card |
| Model | `gpt-5.6-terra` |
| Effort | `medium` |
| Transport | Standard `/v1/responses` |
| Concurrency | 1 |
| Output cap | 1,024 tokens |
| Strict schema | Unchanged production scoring contract |
| Grounding | Hydrated from six approved `api/cards.json` entries |
| Conservative estimate | $0.321850 before the upward-display fix |
| Actual calculated cost | **$0.055324** |

The run made exactly 12 paid calls. A subsequent keyless resume restored all 12
fingerprints, scheduled zero new calls, and calculated $0.0000 new cost.

## Gate results

| Criterion | Result | Gate |
| --- | ---: | --- |
| Structured responses parsed | 12/12 | Pass |
| False Accuracy passes | 0 | Pass |
| False Accuracy failures | 0 | Pass |
| Accuracy exact / within one | 9/12 · 12/12 | Pass |
| Composite exact | 5/12 | Informational |
| Composite within one | **9/12** | **Fail** |
| Composite mean deviation | 0.83 | Informational |
| Depth exact / within one | 2/12 · 6/12 | Fail signal |
| Boundaries exact / within one | 7/12 · 10/12 | Fail signal |

All six fluent-but-wrong answers stayed in the failing Accuracy bucket. All six
noisy-but-correct answers stayed in the passing bucket. The failure is not
mechanism classification; it is secondary-axis inflation that raises the
user-visible composite.

## Three blocking deviations

| Case | Approved axes | Terra axes | Composite | Audit |
| --- | --- | --- | --- | --- |
| Non-functional requirements — noisy transcript | 5 / 1 / 1 | 4 / 3 / 3 | 3 → 5 | The learner quantified and prioritized constraints but did not explain a trade-off or failure mode. Terra's feedback asks for missing workload and staleness detail while still awarding both secondary axes. |
| Identity boundary — noisy transcript | 5 / 1 / 2 | 5 / 4 / 4 | 3 → 5 | The learner supplied authentication and authorization plus a brief warning about client identity, but no implementation trade-off. Terra's feedback introduces signed gateway context that the learner never said while awarding Depth 4. |
| Timeout retry — noisy transcript | 5 / 2 / 2 | 5 / 4 / 3 | 3 → 5 | The learner named backoff and the replay-safe mechanism but did not explain the delay/state trade-offs or duplicate-charge boundary. Terra's own feedback calls the boundary missing while awarding Boundaries 3. |

The approved labels remain unchanged. Relabeling these cases after seeing the
model output would move the goalposts and erase the exact evidence this release
gate was designed to surface.

## Cost, tokens, and latency

| Measure | Result |
| --- | ---: |
| Input tokens | 12,458 |
| Cached input tokens | 0 |
| Output tokens | 2,534 |
| Calculated cost | **$0.055324** |
| Median latency | 3.077 s |
| Mean latency | 3.485 s |
| Fastest / slowest | 2.105 s / 6.433 s |

The actual cost was close to the approximately $0.054 projection and far below
the deliberately extreme byte-and-full-output-cap estimate.

## Preflight precision correction

The run exposed a presentation defect in the guard: the exact estimate was
$0.321850, while four-decimal nearest rounding printed `$0.3218`. A user who
copied that display value could not authorize the exact estimate. Preflight
ceilings now round upward to four decimals, so this stage displays `$0.3219`
and the printed amount is always sufficient for `--max-cost-usd`.

The corrected remaining stage ceilings are $0.6470 for 48 unique Batch calls
and $0.1610 for each 12-call repeat. They are recorded for reproducibility, not
authorization; those stages are stopped by the failed gate.

## Recommended next experiment

Do not spend on higher effort or the 48-case Batch yet. First decide whether a
shared scoring-prompt revision can require explicit learner evidence for every
Depth and Boundaries point without regressing Claude's reviewed baseline. That
would be a new cross-provider experiment with its own offline prompt review,
Claude regression gate, Terra smoke authorization, and cost cap. A
provider-specific rubric would no longer test the unchanged production
contract and should not be treated as evidence for the current adapter plan.

That experiment is now prepared, with no paid calls made, in
`EXPLICIT-EVIDENCE-SCORING-EXPERIMENT.md`. It starts with a six-call Claude
speech-noise gate and stops for audit before any additional spend.
