# Curriculum — 12-week senior-backend and AI-systems interview plan

`api/cards.json` is a recall spine, not a complete interview-preparation
program. The Study Plan is the complete program: Hello Interview teaches the
material, coding is implemented in Python, and full designs, coding mocks, and
behavioral practice produce the external evidence. Unprompted retains focused
mechanisms after learning; it does not replace the work.

This document governs curriculum content and activation. `spec.md` governs
backend behavior. The retired 126-card deck is preserved at
`api/archive/cards-legacy-126.json`.

## Target

- Senior backend or backend-leaning platform/product roles, including at AI
  companies.
- Mid-level is an acceptable fallback; preparation is calibrated to the senior
  bar.
- Initial target companies: Anthropic, OpenAI, and Google.
- Twelve weeks at 20 dependable hours per week, with an advisory stretch menu
  of up to 20 more. Stretch work never blocks advancement or changes the
  forecast. If an interview is scheduled sooner, compress the same dependency
  order rather than activating everything.
- Python is the implementation language for every coding exercise and mock.
- Resume work, job applications, and professional networking are intentionally
  deferred from this version of the plan.

Distributed product/backend system design remains the foundation because it
transfers across the widest set of roles. Version 5 adds application-side AI
systems—hosted inference, state and authority, evals, recoverable workflows,
model routing, and RAG—inside the same 240 hours. Model research, distributed
training, GPU kernels, and inference-runtime implementation remain on-demand
specializations rather than core work. The exact boundary and resource map live
in [AI systems foundations](AI-SYSTEMS-FOUNDATIONS.md).

The immediately runnable first week is in
[Week 1 — start here](WEEK-1-START-HERE.md).

The current card-by-card readiness, prerequisite gaps, source-access risks, and
migration order are recorded in the
[card actionability audit](CARD-ACTIONABILITY-AUDIT-2026-08-11.md). Topic
coverage alone is not activation approval.

## Evidence hierarchy

Cards are justified by one of these sources, in descending order:

1. A current Hello Interview lesson in the system-design, coding, or behavioral
   course.
2. A current level guide for the target role, especially Google L5 and OpenAI
   L5.
3. A repeated theme in recent target-company interview reports. Reports are
   self-selected and sorted by recency/popularity, so they identify themes—not
   exact question probabilities.
4. A concrete gap observed in a timed problem, full design, mock, or real
   interview.

The fourth source eventually outranks every generic catalog for personal
prioritization.

Every base card in `api/cards.json` records:

- `source_url`
- `source_section`
- `activation_prerequisite`
- `evidence`

These fields make the repository auditable. A grounded activation persists the
trusted source and answer authority on the card. The prerequisite and evidence
remain curriculum provenance until a versioned Study Plan explicitly maps the
lesson to that card.

## The modality boundary

### Devmax does

- Mechanism reconstruction.
- Coding-pattern recognition, invariants, state transitions, complexity, and
  failure-mode recall.
- Trade-off and failure-mode recall.
- Spaced repetition after a lesson has been learned.
- Focused prompts created from an observed practice gap.

### Devmax does not do

- Scored review as first exposure to a new topic.
- Timed coding or implementation.
- A complete 45-minute system-design interview.
- Behavioral story authoring.
- Readiness certification.

Readiness comes from external performance. A library full of 4s and 5s is not
evidence that the user can code under time pressure or navigate a whole design.

### Learn mode and exposure delay

First exposure belongs to an unscored Learn path backed by approved answer
authority. It must provide an accessible source or an internally authored,
human-approved summary with an observable done-when. It must not scrape or
reproduce paid course material.

Viewing the explanation, worked example, answer basis, rubric, or correction is
exposure, not recall. Learn mode writes no score, session history, mastery
signal, or SM-2 state. After that authority is shown, no attempt in the same
session may be treated as unaided retention; the first scored review belongs to
a later review window: the later of eight hours after exposure and the start of
the next local day. This delay protects the scheduler from measuring short-term
imitation while still giving every new card an actionable way to be learned.

## Deck shape

### Base recall spine — `api/cards.json`

Fifty-four conversational cards:

| Category | Cards |
|---|---:|
| Delivery | 2 |
| Core Concept | 19 |
| Key Technology | 12 |
| System Design Pattern | 21 |

There are six new cards in each of weeks 1–9. Weeks 10–12 contain no generic
additions; they are reserved for mocks, target-company overlays, and cards
earned from observed failures.

The base deck intentionally contains no:

- `System Design Problem`
- `Company-Specific Problem`
- `Coding Pattern`
- `Coding Warmup`
- `Tier 2 Practical Build`
- `Behavioral`

Broad prompts such as “Design a news feed” do not fit a two-minute canonical
question. Run the full problem externally and add a focused mechanism afterward,
such as “fan-out recovery when a publish partially succeeds.”

### Coding reference library — `api/library/coding-patterns.json`

Seventeen mechanism prompts covering Hello Interview's sixteen coding families
live in a desk-only reference file (graphs contributes both dependency ordering
and shortest paths). They are not part of the production seed. Mechanism
understanding and Python implementation are core Study Plan work. A pattern card
is seeded only when practice shows that explaining its recognition cue,
invariant, state transition, complexity, or failure mode would help.

### Company overlays — `api/modules/company-*.json`

Anthropic, OpenAI, and Google each have a six-card mechanism module derived from
current level guides and reported-question themes. Activate a module only when a
matching role or interview loop becomes concrete.

Company modules are not predictions that an exact question will repeat. They
capture transferable mechanisms emphasized by the current evidence.

### AI foundations overlay — `api/modules/ai-foundations.json`

Four vendor-neutral cards cover training versus hosted inference, the inference
request lifecycle, context windows versus durable conversation state, and evaluation-driven
reliability. They are separate from the 54-card base spine and from the older
ungrounded `ai-application.json` topic stubs.

The foundation entries ship as `draft_review`, not active cards. Their complete
proposed authority and twelve-case qualitative eval pack are committed for
operator review, but only a human review may change them to `approved`. Seed one
target-week cohort only after its mapped Learn item is complete. Later AI cards
must come from observed gaps rather than a second generic deck.

### Behavioral cards

Do not seed generic behavioral prompts at startup. First write a story catalog
covering:

1. scope
2. ownership
3. ambiguity
4. perseverance
5. conflict resolution
6. growth
7. communication
8. leadership

Then create one story-specific card per story. Keep only the Big Three as
generic transfer prompts:

- tell me about yourself
- describe a project you are proud of
- describe a conflict

Story-specific cards establish the evidence. Generic prompts later test whether
the right story can be selected under ambiguity.

## Learning-to-review handoff

**A card enters production only after its `activation_prerequisite` is complete
and its answer authority has been reviewed.**

The learner flow is:

1. Open the item's direct source and satisfy its concrete `done_when`.
2. Complete the lesson item in Study Plan.
3. The app resolves that item's reviewed `mapped_recall_topics` from the base
   deck or the AI-foundations overlay against owned, active, fully grounded
   cards. This lookup is read-only.
4. Open each resolved card and use its card-owned Learn action before the first
   scored recall. The first
   review waits until the later of eight hours and the next local day.

Completion remains a plan-only write. The versioned first-party mapping makes no
model call and never copies Premium lesson text. A missing or incompletely
grounded future card remains `Not ready`; completing the item does not create or
activate it. A reviewed cohort must still be released deliberately through the
curated seed, while a personal sourced gap may use the ordinary proposal and
acceptance path. Neither path is triggered by elapsed calendar time.

The curated `cards.json` cohort command is retained for first-deploy bootstrap,
recovery, and clean-room verification:

```sh
cd api
uv run python -m app.seed \
  --file cards.json \
  --activate-week 1 \
  --start-date <today>
```

`--activate-week N` selects exactly that curriculum week and schedules its cards
as a fresh cohort beginning on `--start-date`. The original `target_week`
remains stored on the card for curriculum-order metadata.

Re-running the same command is safe because seeding deduplicates on `Card.topic`.
Do not run it each week during normal study. Study Plan completion and the
proposal gate are the ongoing workflow.

`--weeks-through N` remains available for local verification and clean-room bulk
imports, but it is not the recommended production learning workflow.

### First-party Study Plan bootstrap

The review-card seed above does not create Study Plan rows. Bootstrap the curated
phase → week → item timeline once:

```sh
cd api
uv run python -m app.seed_study_plan \
  --activate \
  --start-date <monday-of-this-week>
```

`api/plans/senior-backend-12-week.json` is the deterministic content-version-5
manifest. It retains the version-4 canonical lineage key and all `V4-*` stable
item identities; `version` is the content revision. That lets an older binary
still discover a newer plan and refuse a downgrade rather than creating a
duplicate. The reviewed version-2 and version-3 keys remain legacy aliases.
The bundle is validated through the same gate as a pasted guide, makes no model
call, and never touches cards or sessions. After bootstrap, progress happens in
the app: card reviews continue in Today, item completion remains an explicit
plan-only signal, and a completed mapped lesson opens each owned, active,
grounded card through Card History.

Eight version-5 Learn items are marked `requires_fresh_completion`. A content
upgrade rewrites them only while they are unfinished. If one is already
complete, its historical row—including resources and recall mappings—is
preserved rather than pretending the learner completed newly introduced AI
work. Curriculum revisions carry that skipped-work debt forward even after a
later manifest drops the marker; it resolves only if the new content is applied
while the row is unfinished. Completion and curriculum updates use an item
snapshot guard so simultaneous database writers cannot merge old completion
with newly swapped content. Item detail also returns `plan_revision`, which the
current client echoes on completion. A stale revision returns 409, reloads the
item, and requires a fresh explicit tap. Clients that omit the token are blocked
from these eight protected items while conventional and generic items remain
compatible. If the upgrade reports a skipped completed key, assign a fresh
later activity before activating the corresponding foundation card.

## Retirement rule

**Deduplication makes activation safe; it does not make replacement safe.** Seeding
only ever adds, so retiring a deck is a separate, explicit act:

```sh
uv run python -m app.seed --retire-file archive/cards-legacy-126.json --dry-run
uv run python -m app.seed --retire-file archive/cards-legacy-126.json --confirm
```

The manifest passed to `--retire-file` *is* the delete list. Retirement never
computes a difference against the current deck, so reference material in
`api/library/`, company overlays in `api/modules/`, and gap-driven cards created
through Capture activation cannot be caught by it.

This is a hard delete and the cards' session history cascades with them. A card
retired by mistake cannot be restored with its scores; only re-seeded blank.

**A retired topic must never reappear in a live manifest.** Retirement matches on
topic, so reintroducing one would aim the prune at a live card.
`test_the_retire_manifest_shares_no_topic_with_any_live_deck` is the guard, and it
covers `cards.json`, `library/`, and `modules/` together.

## Twelve-week program

Every scheduled week is exactly 1,200 minutes on the 30-minute planning grid.
The manifest owns the individual source links, exercises, estimates, completion
conditions, and hard dependencies; this table is the journey-level map.

| Week | System design and practice | Python coding | Behavioral and retrieval |
|---|---|---|---|
| 1 | Delivery, API design, hosted inference lifecycle, retry semantics, then a blind-first Bitly design | Two pointers, sliding window, intervals | Evidence inventory, sourced repair, closed-book reconstruction |
| 2 | AI application state and authority, data modeling, indexing, PostgreSQL internals, then Design LeetCode | Stacks, linked lists, binary search | Scope and ownership stories |
| 3 | Caching, sharding, consistent hashing, CAP, then Distributed Cache | Heaps, DFS, BFS | Ambiguity and perseverance stories |
| 4 | Scaling reads/writes, bounded inference workers, a measured eval, Twitter-style timeline, ad-click aggregation | Topological ordering and shortest paths | Conflict and growth stories |
| 5 | Contention, recoverable AI workflows, bounded tool authority, Ticketmaster, payments | Backtracking, dynamic programming, greedy | Communication and leadership stories |
| 6 | Realtime recovery, blobs, outbox, durable AI jobs, WhatsApp, Dropbox | Tries, prefix sums, matrices | Finish story catalog and Big Three |
| 7 | PostgreSQL, Redis, DynamoDB, Cassandra, trusted inference gateways, online auction | Mixed timed data-structure set | Big Three rehearsal |
| 8 | Kafka, permission-filtered hybrid retrieval, CDN, rate limiting, time series, post search | Mixed timed priority-queue set | Senior behavioral follow-ups |
| 9 | Flink, ZooKeeper, approximate structures, proximity, vector retrieval, end-to-end RAG | Mixed timed graph set | Senior-scope follow-ups |
| 10 | Notification System and Job Scheduler baseline mocks | Two named Python mocks | Behavioral mocks and evidence baseline |
| 11 | OpenAI-shaped ChatGPT and Google-shaped collaborative-docs mocks | Two named Python mocks | Target-company calibration and mocks |
| 12 | Two unseen guided design mocks | Two final named Python mocks | Final behavioral mock and evidence review |

Weeks 1–9 map all 54 base recall topics exactly once to source Learn items and
map the four draft AI-foundation topics exactly once to their explicit Learn
prerequisites.
Coding activities and full designs never create broad cards automatically.
Every external session ends with a gap harvest; add zero to three focused cards
only when a mechanism could not be reconstructed and trusted answer authority
exists. Weeks 10–12 contain no generic mappings.

## Weekly time budget

The dependable budget is 20 hours. Its mix changes with the phase:

| Phase | Source learning | Python coding | Designs or mocks | Behavioral, repair, retrieval | Total |
|---|---:|---:|---:|---:|---:|
| Weeks 1–3 | 5–6h | 6h | 4h | 4–5h | 20h |
| Weeks 4–6 | 4h | 4.5–5.5h | 7h | 3.5–4.5h | 20h |
| Weeks 7–9 | 6–7.5h | 4h | 6–6.5h | 2.5–4h | 20h |
| Weeks 10–12 | 2h calibration | 6h | 6h | 6h | 20h |

Each week also exposes an advisory 20-hour stretch menu: eight hours of unseen
Python work, six hours of extra designs or mocks, three hours of a current-week
deep dive, two hours of behavioral rehearsal, and one hour of gap harvest. It is
not scheduled capacity, does not block advancement, and must not pre-teach the
next week or increase generic card volume. If only 20 hours are available, do
the scheduled plan and ignore Stretch without replanning.

The AI-systems allocation is not extra capacity: eight existing Learn rows total
660 minutes (11 hours), and the existing Week 9 and Week 11 ChatGPT work totals
300 minutes (5 hours). That is 16 explicit AI hours inside the fixed 240-hour
plan. No coding, behavioral, conventional design, or readiness-mock row was
removed.

## Readiness gates

Devmax scores are diagnostic, not readiness gates.

### Coding mechanism fluency

- Identify the likely family from an unfamiliar prompt.
- State the invariant and how each state transition preserves it.
- Compare the approach with the naive alternative.
- Derive time and space complexity.
- Name the edge case most likely to break the approach.

### Coding execution readiness

This can be claimed only from external Python implementation:

- Two unseen problems in 45 minutes.
- Correct or nearly correct implementation.
- Clear narration, complexity analysis, and manual edge-case testing.
- At least five to seven sessions in a plain editor without execution.

### System design

- A complete 45-minute design that follows the delivery framework.
- Constraints that materially affect architecture quantified before choosing
  components.
- At least two defended deep dives.
- Failure modes and trade-offs handled without technology name-dropping.
- At least three mocks at the target level.

### AI application systems

- Trace a hosted inference request and diagnose first-token versus completion
  latency without claiming that ordinary prompting trains the model.
- Separate model weights, request context, durable application state, and
  code-owned policy or side-effect authority.
- Produce a measured eval report with realistic cases, repeated trials,
  calibrated graders, failure classes, and explicit regression gates.
- Defend workflow-versus-agent, provider fallback, retrieval authorization, and
  end-to-end RAG failure boundaries in an external design review and unseen
  failure drill.

### Behavioral

- Eight written stories with individual actions, measurable results, and
  learnings.
- Big Three answers practiced.
- Stories demonstrate senior scope, judgment, and influence.
- At least two mocks with follow-up questions.

## Keeping the curriculum honest

A new card must answer all of these:

1. What source lesson or observed failure justifies it?
2. Can its mechanism be reconstructed in under two minutes?
3. Does it test a scenario rather than a definition?
4. Would a different answer change an interview decision or reveal a real gap?
5. Is it more useful than spending the same review budget on an existing weak
   card?

Do not add a card because a prep catalog happens to contain a page. Do not add a
broad problem because it has appeared at a target company. The external session
is where whole-problem performance is measured; Devmax retains the focused
lesson afterward.
