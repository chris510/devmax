# Week 1 curriculum grounding review

Status: **machine-drafted on 2026-08-07; awaiting human approval**

This tranche adds answer authority and fixed canonical questions for the six
Week 1 conversational cards. It does not approve or activate them. Every entry
in `api/cards.json` remains `grounding_status: "draft_review"`, and the seed and
live-evaluation paths fail closed until a reviewer changes that status to
`approved` after checking the source, answer frame, and question.

The answer bases are concise authored paraphrases, not licensed excerpts. The
source lessons remain the authority:

- [Delivery Framework](https://www.hellointerview.com/learn/system-design/in-a-hurry/delivery)
- [API Design](https://www.hellointerview.com/learn/system-design/core-concepts/api-design)
- [Networking Essentials](https://www.hellointerview.com/learn/system-design/core-concepts/networking-essentials)

## What was drafted

Each card now has:

- a source label and existing source URL/section;
- a concise answer basis;
- a five-field rubric covering mechanism, acceptable alternative, trade-off,
  failure mode, and misconception;
- one canonical question that will be reused across reviews;
- an explicit draft-review state that cannot cross the activation boundary.

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

The draft was checked against the six-part
[first-question gate](CURRICULUM-AUDIT-2026-07-30.md#first-question-gate). These
are author checks, not human approval.

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

## Human approval checklist

For each of the six entries in `api/cards.json`:

- [ ] Open the cited lesson and verify every sentence in `answer_basis`.
- [ ] Confirm all five rubric fields are source-supported and mutually
  consistent.
- [ ] Add any valid alternative framing the current rubric would score too
  harshly.
- [ ] Reject any correction that depends on an unstated product assumption.
- [ ] Speak a strong answer aloud and confirm it fits comfortably under two
  minutes.
- [ ] Confirm the question has one central retrieval target and does not reveal
  the answer.
- [ ] Compare the 18 scoring answers and 12 re-attempt answers with their labels.
- [ ] Only then change that entry's `grounding_status` from `draft_review` to
  `approved`.

After all six entries are approved, run the offline suite first, then explicitly
start the paid sweeps:

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

Record model ID, effort levels, token usage, false mechanism passes/failures,
per-axis agreement, and every mismatch before changing a production prompt or
model setting. Do not approve the remaining 48 curriculum cards by analogy;
each needs its own source review and first-question audit.
