# Week 1 curriculum grounding review

Status: **human-approved and live-evaluated on 2026-08-07**

This tranche adds reviewed answer authority and fixed canonical questions for
the six Week 1 conversational cards. It does not activate them; activation is
still an explicit seed operation. Every Week 1 entry in `api/cards.json` is now
`grounding_status: "approved"` after a human reviewed the source, answer frame,
question, and all five evaluation cases for that card.

The answer bases are concise authored paraphrases, not licensed excerpts. The
source lessons remain the authority:

- [Delivery Framework](https://www.hellointerview.com/learn/system-design/in-a-hurry/delivery)
- [API Design](https://www.hellointerview.com/learn/system-design/core-concepts/api-design)
- [Networking Essentials](https://www.hellointerview.com/learn/system-design/core-concepts/networking-essentials)

## What was approved

Each card now has:

- a source label and existing source URL/section;
- a concise answer basis;
- a five-field rubric covering mechanism, acceptable alternative, trade-off,
  failure mode, and misconception;
- one canonical question that will be reused across reviews;
- an explicit approved state that may cross the activation boundary only when
  the Week 1 cohort is deliberately seeded.

The seed loader validates the entire selected cohort before opening a database
transaction: every conversational entry needs `grounding_status: "approved"`,
a trusted basis, all five rubric fields, and a canonical question. A missing or
draft authority therefore activates nothing. Desk reference material remains
outside scoring and does not require the rubric.

| Card | Retrieval target | Canonical question |
|---|---|---|
| Delivery sequence | Complete the simplest endpoint-driven design before targeted hardening | You're eight minutes into a design interview and the requirements are agreed. What sequence gets you to a working design before the deep dives, and why? |
| Non-functional requirements | Convert vague qualities into a few contextual, quantified constraints | An interviewer asks for a social feed that is “fast and reliable.” How do you turn that into the few non-functional requirements that should actually drive the architecture? |
| API identity boundary | Derive the caller from trusted auth context, then authorize the resource action | A cancellation request includes both a bearer token and a user ID in the body. How should the service decide who is acting and whether that booking may be cancelled? |
| Timeout, retry, idempotency | Treat timeout outcome as unknown and safely replay one logical operation | A payment request times out after the server may already have charged the card. What should the client and server do on retry, and what must they not assume? |
| Cursor pagination | Resume after a stable ordered position rather than a moving offset | A feed inserts new rows between a client's first and second page. How does a cursor keep traversal stable, and what must the cursor represent? |
| Decision-driven estimation | Estimate only a bound that can change a concrete design branch | A candidate says a trending-topics service needs distributed aggregation. What is the smallest estimate that could prove or disprove that choice, and how would you use it? |

## First-question audit

Each question was checked against the six-part
[first-question gate](CURRICULUM-AUDIT-2026-07-30.md#first-question-gate). These
checks were confirmed during human approval.

| Card | Atomicity | Voice budget | Source fidelity | Senior signal | Neutrality | Stability |
|---|---|---|---|---|---|---|
| Delivery sequence | One sequencing decision | Under two minutes | Matches the delivery scaffold | Requires prioritizing completeness vs depth | No technology supplied | Ordered answer frame |
| Non-functional requirements | One requirement-framing task | Under two minutes | Matches contextual, quantified NFR guidance | Connects constraints to architecture | Example adjectives do not reveal targets | Bounded constraint-selection frame |
| API identity boundary | One caller/resource authorization scenario | Under two minutes | Matches authentication-before-authorization guidance | Tests trust boundary and IDOR failure | Bearer token and body ID create the ambiguity | Principal → resource → action frame |
| Timeout, retry, idempotency | One ambiguous payment outcome | Under two minutes | Matches timeout, backoff, jitter, and idempotency guidance | Tests partial failure and duplicate effects | Does not name the recovery mechanism | Unknown outcome → safe replay frame |
| Cursor pagination | One insertion-between-pages scenario | Under two minutes | Matches last-position cursor guidance | Tests ordering stability and key choice | Names cursor because it is the subject, not its implementation | Stable boundary frame |
| Decision-driven estimation | One distribute-or-not branch | Under two minutes | Matches “estimate only when useful” guidance | Requires converting a bound into a decision | Does not prescribe the estimated quantity | Decision → bound → limit frame |

## Evaluation pack

Two offline-reviewed case sets accompany the tranche:

- `api/scripts/grounded_effort_cases_week1.json`: 18 answers, with complete,
  mechanism-only, and confidently wrong cases for every card. Each case labels
  all three scoring axes and the derived composite.
- `api/scripts/grounded_reattempt_cases_week1.json`: 12 coached answers covering
  reconstruction, parroting, adjacent jargon, and persistence of the original
  misconception.

The runners hydrate the canonical question, answer basis, and rubric directly
from `api/cards.json`. They refuse any matching entry that is not `approved`, so
the case files cannot drift from production authority or accidentally spend
Anthropic credits during draft review.

The Week 1 packs are only the first 30 cases toward the planned 60–100-case
release evaluation. They intentionally do not claim enough topical coverage to
approve a scoring-model or effort change.

## Human approval record

All six cards were reviewed individually on 2026-08-07. The review made these
corrections before approval:

| Card | Approval correction |
|---|---|
| Delivery sequence | Raised the mechanism-only Accuracy label from 4 to 5; the mechanism was complete even though depth remained thin. |
| Non-functional requirements | Raised the mechanism-only Accuracy label from 4 to 5 for the same separation of mechanism from depth. |
| API identity boundary | Replaced a loosely sourced gateway trade-off with the source-supported JWT versus database-session trade-off. |
| Timeout, retry, idempotency | Named the exact source section and replaced the unsupported key-lifetime claim with the server-state/backoff trade-off. |
| Cursor pagination | Narrowed the claim to insertions and removed deletion, backward-navigation, and composite-key details not taught by the source. |
| Decision-driven estimation | Removed extra memory-accounting and partial-merge mechanics, retaining the lesson's topic-count decision between one min-heap and sharding. |

## Human approval checklist

For each of the six entries in `api/cards.json`:

- [x] Open the cited lesson and verify every sentence in `answer_basis`.
- [x] Confirm all five rubric fields are source-supported and mutually
  consistent.
- [x] Add any valid alternative framing the current rubric would score too
  harshly.
- [x] Reject any correction that depends on an unstated product assumption.
- [x] Speak a strong answer aloud and confirm it fits comfortably under two
  minutes.
- [x] Confirm the question has one central retrieval target and does not reveal
  the answer.
- [x] Compare the 18 scoring answers and 12 re-attempt answers with their labels.
- [x] Only then change that entry's `grounding_status` from `draft_review` to
  `approved`.

## Evaluation audit

The post-approval offline gate passed on 2026-08-07:

- 434 tests passed on SQLite;
- 434 tests passed against a freshly migrated Postgres 17 database;
- Ruff passed across the full API tree;
- all three grounding JSON files parsed successfully.

The first live scoring attempt exposed that the sweep scripts still referenced
the pre-accounts scoring field names. The production contract now calls the
axes Accuracy, Depth, and Boundaries. Both scoring runners, all evaluation case
labels, and regression tests were updated to that contract before another live
attempt.

Credits were restored later on 2026-08-07 and both final sweeps completed against
`claude-sonnet-5` at low and medium effort with concurrency 4.

### Scoring results

| Effort | Composite exact | Mean deviation | Accuracy exact / within one | Depth exact / within one | Boundaries exact / within one | False Accuracy pass / fail | Input | Cache read / write | Output |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| low | 13/18 | 0.28 | 9/18 · 17/18 | 10/18 · 17/18 | 16/18 · 18/18 | 0 / 0 | 35,267 | 0 / 0 | 4,068 |
| medium | 12/18 | 0.33 | 10/18 · 17/18 | 10/18 · 17/18 | 16/18 · 18/18 | 0 / 0 | 35,267 | 0 / 0 | 5,029 |

Low effort remains the scoring default. It was more exact, had lower mean
deviation, and used 19% fewer output tokens without changing the false-pass or
false-failure result.

### Coached re-attempt results

| Effort | Exact | Within one | Mean deviation | False parrot pass | False reconstruction fail | Input | Cache read / write | Output |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| low | 6/12 | 11/12 | 0.58 | 1 | 0 | 20,528 | 0 / 0 | 944 |
| medium | 8/12 | 11/12 | 0.50 | 1 | 1 | 20,528 | 0 / 0 | 1,412 |

Low effort remains the coached-grading default provisionally. Medium was more
exact, but both levels were within one on 11 cases and medium introduced a false
failure on a correct reconstruction. Low also used 33% fewer output tokens.
Most importantly, all 24 final mastery summaries explicitly described the
performance as coached or not yet demonstrated unaided.

### Findings and corrections

- The initial identity “mechanism only” scoring answer also stated the trust
  boundary, so Claude reasonably treated it as depth. The fixture now contains
  only the core caller/resource authorization flow and is labeled 4/2/2; a
  focused low/medium rerun produced that exact composite and stayed within one
  on every axis.
- Confidently wrong scoring answers were repeatedly graded Accuracy 0 instead of
  the rubric's 1. Both values remain in the scheduler's failing bucket, so this
  did not create a false retention pass, but it is a display-severity mismatch.
- Correct coached extensions were sometimes graded as plain reconstruction,
  apparently because the model treated trusted grading authority as though it
  had been shown to the learner. A verbatim sequence also sometimes scored 3
  despite a mastery summary correctly calling it parroting.
- Three coached answers that remained fully wrong were relabeled from 1 to 0,
  matching the written rubric. The source-aligned cursor reconstruction was
  relabeled from 5 to 4 because it adds no independent extension.
- The re-attempt runner now prints full input, output, cache usage, within-one
  agreement, false-pass/failure counts, and optional mastery summaries.

### Cost-safety canary and provider comparison

A two-call live canary on 2026-08-08 validated the paid-evaluation controls
without rerunning the full pack:

| Path | Case | Result | Input | Output | Conservative ceiling | Actual cost |
|---|---|---:|---:|---:|---:|---:|
| Scoring | Delivery sequence — complete | 5 expected 5; axes 5/4/4 | 2,026 | 235 | $0.0092 | $0.0064 |
| Coached re-attempt | Delivery sequence — reconstructed | 5 expected 5 | 1,766 | 114 | $0.0048 | $0.0047 |
| **Total** |  | **2/2 exact** | **3,792** | **349** | **$0.0140** | **$0.0111** |

Both calls used `claude-sonnet-5` at the shipping low effort. Each preflight
counted the exact request input, assumed the configured maximum output, and
required a ceiling above that estimate before the paid Message call could
start. Replaying both result files with `ANTHROPIC_API_KEY` explicitly unset
reported one resumed call, zero new paid calls, and `$0.0000` for each path.
This verifies the resume contract against the live response fingerprints, not
only against unit fixtures.

ChatGPT subscription usage cannot be substituted for API balance: OpenAI
[bills and manages ChatGPT and API usage separately](https://help.openai.com/en/articles/8156019).
An OpenAI backend would therefore need its own API billing account. The relevant
comparison is provider API price, not the user's remaining ChatGPT credits.

Using the ten-case smoke preflight's 18,600 input and conservative 3,584 output
tokens as a normalized workload gives this price comparison. It is directional:
OpenAI tokenization and reasoning-token use will differ, so only an equivalent
live runner can establish the actual total.

| Candidate | Published standard input / output per MTok | Normalized smoke cost | Versus current Claude |
|---|---:|---:|---:|
| Claude Sonnet 5 through 2026-08-31 | $2 / $10 | $0.0730 | baseline |
| Claude Sonnet 5 from 2026-09-01 | $3 / $15 | $0.1096 | 50% more |
| OpenAI GPT-5.6 Terra | $2 / $12 | $0.0802 | 10% more |
| OpenAI GPT-5.6 Luna | $0.20 / $1.20 | $0.0080 | 89% less |

OpenAI's current [model guidance](https://developers.openai.com/api/docs/guides/latest-model)
positions GPT-5.6 Luna for efficient high-volume work, and the model supports
structured outputs required by this grader. OpenAI's published
[API pricing](https://developers.openai.com/api/docs/pricing) lists Luna at
$0.20 input and $1.20 output per million short-context tokens. Its
[Batch API](https://developers.openai.com/api/docs/guides/batch) is another 50%
lower for evaluations that can wait up to 24 hours. Claude prices use
Anthropic's published [pricing schedule](https://platform.claude.com/docs/en/about-claude/pricing).

Price is not sufficient authority to change the production scorer. GPT-5.6
Luna should first run the ten risk-stratified smoke cases, then all 30 reviewed
Week 1 cases. A provider switch remains blocked until the planned 60–100-case
pack passes with no false Accuracy pass and acceptable per-axis agreement. This
keeps the scheduler's retention signal behind the same quality gate regardless
of provider.

The isolated Luna runner, cost ceilings, commands, and acceptance gate are
recorded in [OpenAI GPT-5.6 Luna scoring bake-off](OPENAI-LUNA-BAKEOFF.md).
Its ten-case live smoke passed on 2026-08-08: scoring composites were exact on
6/6 cases, coached grading was exact on 3/4 and within one on 4/4, there were no
false passes or false failures, and every coached summary preserved the
coached-versus-unaided distinction. The run used 10,101 input and 1,840 output
tokens, cost $0.0042, and replayed from fingerprints with zero new paid calls.
The complete 30-case Week 1 comparison then cost $0.0128: Luna scoring was
exact on 12/18 with mean deviation 0.67 and two false Accuracy failures;
coached grading was exact on 7/12 and within one on 11/12 with no false pass or
failure. Both correct decision-driven estimation answers scored Accuracy 0 on
the first pass, then Accuracy 5 on an identical controlled rerun. That material
repeatability failure blocks a production provider switch despite Luna being
about 92% cheaper than the calculated Claude full-pack cost. Production remains
on Claude. A subsequent five-trial low-versus-medium matrix reproduced two low
false failures; medium avoided those failures but inflated the mechanism-only
answer from composite 3 to 5 in all five trials. A provider-specific evidence
and axis calibration still produced six false failures across five trials, and
disabling the documented implicit prompt cache reproduced both failures with
zero cached tokens. Concurrency, effort, prompt calibration, and cache behavior
are therefore ruled out as sufficient fixes. If OpenAI remains a goal, the next
candidate is a separately cost-capped stronger-model canary, not more Luna
tuning or production retries.

This 30-case tranche is enough to retain the current low-effort settings, but
not enough to approve a production prompt or model change. The trusted-authority
versus learner-visible-feedback ambiguity must be retested in the planned
60–100-case evaluation before editing the prompt.

To reproduce the evaluation:

```sh
cd api
uv run pytest -q
uv run ruff check .
# Free preflight over the ten risk-stratified smoke cases. This makes no paid
# Message calls and prints the exact input count plus a conservative output estimate.
uv run python scripts/effort_sweep.py \
  scripts/grounded_effort_cases_week1.json \
  --grounding-manifest cards.json --tag smoke --dry-run
uv run python scripts/reattempt_effort_sweep.py \
  scripts/grounded_reattempt_cases_week1.json \
  --grounding-manifest cards.json --tag smoke --dry-run

# Full shipping-effort baseline. The explicit ceilings acknowledge the displayed
# estimate; omit --levels to test only the configured production effort.
uv run python scripts/effort_sweep.py \
  scripts/grounded_effort_cases_week1.json \
  --grounding-manifest cards.json --max-cost-usd 0.20
uv run python scripts/reattempt_effort_sweep.py \
  scripts/grounded_reattempt_cases_week1.json \
  --grounding-manifest cards.json --max-cost-usd 0.08
```

Each paid response is flushed to the printed `.eval-results/*.jsonl` path. Use
`--resume <path>` to reuse only exact request-and-label fingerprints, `--case`
for one named fixture, and explicit `--levels low medium` only when making a real
configuration comparison. Record model ID, effort levels, token usage, false
Accuracy passes/failures, per-axis agreement, and every mismatch before changing
a production prompt or model setting. Do not approve the remaining 48 curriculum
cards by analogy; each needs its own source review and first-question audit.
