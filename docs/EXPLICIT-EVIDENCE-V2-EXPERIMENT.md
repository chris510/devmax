# Explicit-evidence V2 experiment

**Status:** B2 failed; all broader V2 stages stopped

A1 returned axes 4 / 1 / 1 and the exact approved composite 3 on the single
known blocking case. See `CLAUDE-EXPLICIT-EVIDENCE-V2-A1.md` for the complete
feedback, cost, and resume audit. A2 then returned five exact composites with
all axes within one. See `CLAUDE-EXPLICIT-EVIDENCE-V2-A2.md` for the combined
six-case evidence. B1 then activated Boundaries on all three reviewed positive
cases while keeping Depth at 1. See `CLAUDE-EXPLICIT-EVIDENCE-V2-B1.md` for the
complete audit. B2 then exposed a false-negative Depth eligibility decision: Claude
acknowledged the learner's explicit timing trade-off in feedback but assigned
Depth 2 against approved 5. See `CLAUDE-EXPLICIT-EVIDENCE-V2-B2.md`. V2 must not
advance to broader, provider, or production evaluation.

## Decision this PR enables

Evaluate a stricter, shared scoring candidate against the single case that
failed both Terra and Claude before spending on any broader stage.

`explicit-evidence-v2` remains evaluation-only. This PR does not change the
production rubric, provider, model, effort, output schema, parser, scheduler,
or any request setting. V1 remains available so its recorded evidence stays
reproducible.

## Why V1 was insufficient

V1 said Boundaries required an explicitly explained failure, limitation, or
misconception. Claude still converted this learner prescription—“only keep the
few numbers that actually drive the design”—into failure-mode awareness and
awarded Boundaries 3. That raised the derived composite from approved 3 to 5.

The answer never stated what goes wrong when irrelevant requirements are kept.
The trusted rubric supplied that missing harm: a generic checklist gives no
basis for architectural choices and may create conflicting goals. V1 prohibited
context credit but did not make the evidence eligibility test mechanical enough
to prevent the inference.

## V2 contract

V2 separates eligibility from score severity. The grader must apply these hard
checks before choosing a 0–5 value:

| Axis | Evidence required for 3–5 | Otherwise |
| --- | --- | --- |
| Depth | Learner explicitly connects a choice, target, or approach to a cost, sacrificed property, tension, or opposing benefit | Hard ceiling of 2 |
| Boundaries | Learner explicitly connects a triggering condition, action, exception, limitation, or mistaken belief to a concrete adverse outcome or incorrect behavior | Hard ceiling of 2 |

Recommendations, priorities, selection rules, goals, constraints, checks,
guardrails, bare negations, and statements that information is irrelevant do
not independently qualify as failure-mode evidence. In particular, the grader
may not reverse “do X” into “not doing X causes Y” unless the learner also states
the adverse Y. Saying a detail does not drive the design is not itself a concrete
adverse outcome.

The eligibility checks happen silently because the production score schema must
remain unchanged. Only learner `ANSWER:` text can satisfy them. Trusted grounding
continues to define correctness, but it cannot be credited as learner recall.
Model-written feedback is generated after scoring and cannot retroactively make
an axis eligible.

Four embedded calibrations distinguish the failed phrase from genuine evidence:

- “only keep the few numbers that drive the design” keeps Boundaries at 0–2;
- “using a fresh retry key can duplicate the charge” is eligible Boundaries
  evidence;
- “target p95 under 300 ms” keeps Depth at 0–2; and
- “a tighter latency target needs more replication and cost” is eligible Depth
  evidence.

Claude and OpenAI receive the same byte-identical V2 rubric. The overlay remains
inside the completion fingerprint and result metadata, so V2 cannot resume V1
or production outputs.

## Cost-minimized live sequence

All amounts are freshly computed authorization ceilings, not forecasts or
authorizations. The Claude preflight uses the published Sonnet 5 promotional
rate of $2/M input and $10/M output through August 31, 2026. Token counting uses
Anthropic's free count endpoint; no paid Message call was made.

| Stage | Cases | Calls | Counted input | Reserved output | Ceiling |
| --- | --- | ---: | ---: | ---: | ---: |
| A1. Blocking case | Non-functional requirements only | 1 | 3,193 | 512 | **$0.0116** |
| A2. Regression remainder | Other five speech-noise cases | 5 | 15,866 | 2,560 | **$0.0574** |
| Combined only if both pass | All speech-noise cases | 6 | 19,059 | 3,072 | **$0.0689** |

The one-call A1 gate is intentional. V1 already failed this exact case; paying
for five additional calls before confirming the targeted correction would add
no decision value. A2 requires a separate authorization after A1's raw response
is manually audited.

### Prompt overhead

The free Anthropic counter also measured the same blocking completion under all
three prompt variants:

| Prompt | Counted input | Increase from production | Input-cost increase per call |
| --- | ---: | ---: | ---: |
| Production | 1,958 | — | — |
| V1 | 2,571 | +613 | $0.001226 |
| V2 | 3,193 | +1,235 | $0.002470 |

The cost deltas use the current $2/M promotional input rate and exclude output,
which the comparison holds constant. V2 is deliberately explicit enough to test
the eligibility idea, not presumed production-ready prompt copy. A passing V2
would prove the rule direction; it would still need a shorter candidate and a
fresh regression before production promotion. The one-call A1 gate limits the
cost of learning whether the longer rule works at all.

## A1 acceptance gate

The one blocking case passes only if all of these hold:

- the strict response parses;
- Accuracy remains in the passing bucket;
- Depth is 0–2 because the learner stated no cost or tension;
- Boundaries is 0–2 because the learner stated no concrete adverse outcome;
- the derived composite is exactly 3;
- feedback does not claim the learner supplied a trade-off or failure mode; and
- the approved 5 / 1 / 1 label remains unchanged.

Any failure stops the experiment. Do not retry, raise effort, run A2, or test
Terra to search for a favorable sample.

## A2 acceptance gate

If A1 passes, the remaining five cases must retain the original gate:

- 5/5 strict responses parse;
- zero Accuracy false passes and false failures;
- every composite is within one point of its approved label;
- every Depth or Boundaries score of 3–5 is supported by the required explicit
  learner relationship;
- feedback never introduces evidence used to justify a 3–5 axis; and
- approved labels remain frozen.

Passing A1 and A2 would justify a broader reviewed Claude regression proposal,
not a production prompt change or Terra run.

## Reproduce the free preflights

Run from `api/` with the existing local Anthropic configuration:

```bash
# A1 — one blocking case
uv run python scripts/effort_sweep.py \
  scripts/grounded_effort_cases_week1_release.json \
  --grounding-manifest cards.json \
  --case 'non-functional requirements — noisy transcript' \
  --levels low --concurrency 1 \
  --scoring-prompt-variant explicit-evidence-v2 --dry-run

# A2 — only after A1 passes and is audited
uv run python scripts/effort_sweep.py \
  scripts/grounded_effort_cases_week1_release.json \
  --grounding-manifest cards.json \
  --case 'delivery sequence — noisy transcript' \
  --case 'identity boundary — noisy transcript' \
  --case 'timeout retry — noisy transcript' \
  --case 'cursor pagination — noisy transcript' \
  --case 'decision-driven estimation — noisy transcript' \
  --levels low --concurrency 1 \
  --scoring-prompt-variant explicit-evidence-v2 --dry-run
```

These commands now reproduce historical preflights only. A1 and A2 have already
run, and B2 later stopped the candidate. No additional V2 paid call is
authorized.

## Recommended next action

B2 failed its activation gate. Do not run more V2 calls. Draft and offline-test
a bidirectional V3 eligibility rule before requesting any additional paid call.
