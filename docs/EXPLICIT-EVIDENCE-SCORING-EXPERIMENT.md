# Explicit-evidence scoring experiment

**Status:** Prepared and free-preflighted; no paid calls made

## Decision this PR enables

Test one shared scoring-prompt candidate before spending any more on a provider
switch. Run Claude first because it is the production baseline. Stop immediately
if the candidate regresses Claude; only then consider a separately authorized
Terra comparison.

This PR does not change `app/services/llm.py`, the production scoring rubric,
the production provider, any model setting, or any request schema. It adds an
evaluation-only prompt variant and the controls needed to audit it.

## Why the previous Terra smoke failed

The 12-case Terra smoke preserved the scheduler-critical Accuracy boundary but
overgraded three noisy, correct answers by two composite points. In each case,
the learner supplied the mechanism but not the trade-off or failure-mode evidence
required by the approved label. The model's own feedback supplied the missing
detail while still awarding it.

The production rubric leaves room for this interpretation:

- `depth` currently includes reasoning, structure, causality, or application;
- `boundaries` currently includes conditions, exceptions, limitations,
  trade-offs, or failure cases.

The product contract is narrower: Depth is trade-off awareness, and Boundaries
is failure-mode awareness. The candidate makes those mappings explicit and
prevents the grader from treating trusted context or its own feedback as learner
evidence.

## Candidate contract

`explicit-evidence-v1` appends these rules to the unchanged production rubric:

1. Accuracy grades the learner's essential mechanism.
2. Depth grades only an explicitly explained cost, benefit, tension, or
   consequence of choosing the approach.
3. Boundaries grades only an explicitly explained failure, exception,
   limitation, or misconception and the condition or consequence that matters.
4. Only text after an `ANSWER:` label is learner evidence. The topic, questions,
   mastery summary, trusted answer basis, and approved rubric are authority or
   context, not claims the learner made.
5. The grader cannot award credit for content it introduces in feedback. If
   feedback supplies the missing trade-off or failure-mode detail, that axis
   must remain at 0–2.

The same candidate rubric is passed byte-for-byte to the Claude and OpenAI
evaluation runners. Its contents are included in each completion fingerprint,
so a candidate run cannot resume an old production-rubric result. The selected
variant is also written into every result record and OpenAI Batch state file.

The Claude runner previously prepared the exact prompt used for token counting
but called the production convenience function during paid execution. It now
executes and parses that prepared completion directly. This closes a test-versus-
execution drift that would otherwise make a candidate preflight misleading.

## Staged test and cost controls

All figures are freshly computed authorization ceilings, not forecasts or
authorizations. They assume the configured Claude Sonnet 5 promotional rate of
$2/M input and $10/M output through August 31, 2026, or Terra's $2/M input and
$12/M output rate. They deliberately reserve the runner's fallback/full output
allowance; provider billing is based on actual use.

| Stage | Purpose | Calls | Free-preflight ceiling | Stop rule |
| --- | --- | ---: | ---: | --- |
| A. Claude speech noise | Directly retest the three prior overgrades plus their peers | 6 | **$0.0614** | Stop on any gate failure |
| B. Claude adjacent jargon | Check that stricter evidence does not rescue fluent wrong answers | 6 | **$0.0617** | Stop on any gate failure |
| Claude subtotal | Complete baseline risk subset | 12 | **$0.1231** | Required before Terra |
| C. Terra risk smoke | Compare the same candidate on all 12 cases | 12 | **$0.3656** | Stop on any gate failure |

Splitting Claude into two six-call stages limits the first authorization to
$0.0614. If the prompt cannot fix its target family without damage, Stage B and
Terra remain unspent. Each paid stage requires a fresh user decision and an
explicit `--max-cost-usd` at least as large as the current printed ceiling.

The likely charge should be materially below these ceilings, as it was in the
previous Terra smoke, but no expected dollar amount is asserted until this
longer prompt has a measured run.

## Acceptance gate

Each stage must satisfy all of the following before proceeding:

- every strict structured response parses;
- zero false Accuracy passes and zero false Accuracy failures;
- every composite is within one point of its approved label;
- no feedback/axis contradiction, especially feedback that supplies a missing
  trade-off while Depth is 3–5 or a missing failure while Boundaries is 3–5;
- the feedback and each 3–5 secondary-axis score are grounded in something the
  learner actually said; and
- approved labels remain frozen.

Exact composite and per-axis agreement remain reported. Manual review of the
six raw feedback records is required after each Claude stage; aggregate metrics
alone cannot prove evidence attribution.

Passing these 12 risk cases would justify the next broader regression proposal,
not a production prompt change. Promotion still requires reviewed evidence that
the narrower mapping behaves across complete, mechanism-only, incorrect,
self-correction, alternative-answer, follow-up, stale-summary, and axis-isolation
families.

## Reproduce the free preflights

Run from `api/`. The Claude commands use Anthropic's token-count endpoint but
make no paid Message calls. The Terra command is local and makes no API request.

```bash
# Stage A — six Claude speech-noise cases
uv run python scripts/effort_sweep.py \
  scripts/grounded_effort_cases_week1_release.json \
  --grounding-manifest cards.json --tag speech-noise \
  --levels low --concurrency 1 \
  --scoring-prompt-variant explicit-evidence-v1 --dry-run

# Stage B — six Claude adjacent-jargon cases
uv run python scripts/effort_sweep.py \
  scripts/grounded_effort_cases_week1_release.json \
  --grounding-manifest cards.json --tag adjacent-jargon \
  --levels low --concurrency 1 \
  --scoring-prompt-variant explicit-evidence-v1 --dry-run

# Stage C — the same 12 cases on Terra; only after both Claude stages pass
uv run python scripts/openai_bakeoff.py scoring \
  scripts/grounded_effort_cases_week1_release.json \
  --grounding-manifest cards.json --tag risk-smoke \
  --model gpt-5.6-terra --levels medium --concurrency 1 \
  --max-output-tokens 1024 \
  --scoring-prompt-variant explicit-evidence-v1 --dry-run
```

To run a paid stage, remove `--dry-run`, add a new ignored output path, and add
the freshly printed ceiling. For example, Stage A should only be run after a
separate authorization:

```bash
uv run python scripts/effort_sweep.py \
  scripts/grounded_effort_cases_week1_release.json \
  --grounding-manifest cards.json --tag speech-noise \
  --levels low --concurrency 1 \
  --scoring-prompt-variant explicit-evidence-v1 \
  --output .eval-results/claude-explicit-evidence-v1-speech-noise.jsonl \
  --max-cost-usd 0.0614 --verbose
```

Removing `--dry-run` alone cannot spend: the runner also requires the provider
key and an explicit budget acknowledgement. Results remain in the ignored
`.eval-results/` directory; API keys and raw responses are not committed.

## Recommended next action

After this preparation PR merges, authorize only Stage A: six Claude Sonnet 5
low-effort speech-noise calls, concurrency 1, with a current ceiling no higher
than the freshly recomputed preflight. Audit all six records before considering
Stage B.
