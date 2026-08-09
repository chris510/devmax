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

This 30-case tranche is enough to retain the current low-effort settings, but
not enough to approve a production prompt or model change. The trusted-authority
versus learner-visible-feedback ambiguity must be retested in the planned
60–100-case evaluation before editing the prompt.

To reproduce the evaluation:

```sh
cd api
uv run pytest -q
uv run ruff check .
uv run python scripts/effort_sweep.py \
  scripts/grounded_effort_cases_week1.json \
  --grounding-manifest cards.json
uv run python scripts/reattempt_effort_sweep.py \
  scripts/grounded_reattempt_cases_week1.json \
  --grounding-manifest cards.json
```

Record model ID, effort levels, token usage, false Accuracy passes/failures,
per-axis agreement, and every mismatch before changing a production prompt or
model setting. Do not approve the remaining 48 curriculum cards by analogy;
each needs its own source review and first-question audit.
