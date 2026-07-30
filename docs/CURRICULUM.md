# Curriculum — 12-week senior-backend interview plan

`api/cards.json` is a recall spine, not a complete interview-preparation
program. Devmax tests whether a mechanism can be reconstructed out loud in
under two minutes. Coding execution, full system designs, and behavioral story
authoring happen outside the app.

This document governs curriculum content and activation. `spec.md` governs
backend behavior. The retired 126-card deck is preserved at
`api/archive/cards-legacy-126.json`.

## Target

- Senior backend or backend-leaning platform/product roles.
- Mid-level is an acceptable fallback; preparation is calibrated to the senior
  bar.
- Initial target companies: Anthropic, OpenAI, and Google.
- Twelve weeks at 12–15 focused hours per week. If an interview is scheduled
  sooner, compress the same dependency order rather than activating everything.

The foundation is product/backend system design because it transfers across the
widest set of roles. Platform, AI-infrastructure, and company-shaped depth are
on-demand overlays.

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

These fields make the repository auditable. The seed loader deliberately stores
only the app's existing card fields; source metadata is curriculum provenance,
not runtime state.

## The modality boundary

### Devmax does

- Mechanism reconstruction.
- Trade-off and failure-mode recall.
- Spaced repetition after a lesson has been learned.
- Focused prompts created from an observed practice gap.

### Devmax does not do

- First exposure to a new topic.
- Timed coding or implementation.
- A complete 45-minute system-design interview.
- Behavioral story authoring.
- Readiness certification.

Readiness comes from external performance. A library full of 4s and 5s is not
evidence that the user can code under time pressure or navigate a whole design.

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
and shortest paths). They are not part of the production seed. Coding happens in
an editor; a pattern card is seeded only after external practice shows that
recalling its invariant would help.

### Company overlays — `api/modules/company-*.json`

Anthropic, OpenAI, and Google each have a six-card mechanism module derived from
current level guides and reported-question themes. Activate a module only when a
matching role or interview loop becomes concrete.

Company modules are not predictions that an exact question will repeat. They
capture transferable mechanisms emphasized by the current evidence.

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

## Activation rule

**A card enters production only after its `activation_prerequisite` is complete.**

Activate one week from the date learning finishes:

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
Do not activate the next week merely because seven calendar days passed.

`--weeks-through N` remains available for local verification and clean-room bulk
imports, but it is not the recommended production learning workflow.

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
through `POST /cards` cannot be caught by it.

This is a hard delete and the cards' session history cascades with them. A card
retired by mistake cannot be restored with its scores; only re-seeded blank.

**A retired topic must never reappear in a live manifest.** Retirement matches on
topic, so reintroducing one would aim the prune at a live card.
`test_the_retire_manifest_shares_no_topic_with_any_live_deck` is the guard, and it
covers `cards.json`, `library/`, and `modules/` together.

## Twelve-week program

### Weeks 1–3 — delivery and core concepts

System design:

- delivery framework
- non-functional requirements and decision-driven estimation
- network failure handling, API mechanics, and identity boundaries
- data modeling and indexing
- caching, sharding, consistent hashing, and CAP

External coding:

- two pointers
- sliding window
- intervals
- stacks
- linked lists
- binary search
- heaps
- DFS and BFS

Use the editor for implementation. Add a Devmax coding card only when a failed
problem exposes a reusable recognition or invariant gap.

### Weeks 4–6 — technologies

System design:

- PostgreSQL
- Redis
- DynamoDB
- Cassandra
- API gateways
- Kafka
- Elasticsearch
- blob storage and CDNs
- Flink, ZooKeeper, approximate structures, proximity search, and vector indexes

External coding:

- graphs and shortest paths
- backtracking
- dynamic programming
- greedy algorithms
- tries
- prefix sums
- matrices

Begin the behavioral story catalog by the end of week 6.

### Weeks 7–9 — reusable patterns and application

System design:

- scaling reads and writes
- replica lag and fan-out
- backpressure and queues
- contention and distributed locks
- sagas and durable workflows
- circuit breakers, retries, and exactly-once effects
- real-time updates
- large blobs and long-running jobs
- rate limiting and time-series storage

External practice:

- two complete system designs per week
- three timed coding sessions per week
- story catalog completed and Big Three drafted

Every external session ends with a gap harvest. Add at most one to three focused
cards for mechanisms that could not be reconstructed.

### Weeks 10–12 — simulation and target overlays

No automatic generic cards.

Each week:

- two full system-design mocks
- three coding mocks
- two behavioral practices
- due Devmax reviews
- company modules selected for actual roles
- gap cards only

Google-style coding practice must include a plain editor with no execution.
OpenAI-style practice must include practical, production-shaped implementation
and testing. Company-specific full designs remain external even when their
focused mechanisms become cards.

## Weekly time budget

| Phase | Coding | System design | Behavioral | Devmax |
|---|---:|---:|---:|---:|
| Weeks 1–3 | 6–7h | 4–5h | 0–1h | 1h |
| Weeks 4–6 | 6h | 4–5h | 1–2h | 1h |
| Weeks 7–9 | 5–6h | 4–5h | 1–2h | 1h |
| Weeks 10–12 | 5h | 5h | 2h | 1h |

If fewer hours are available, reduce breadth. Do not increase passive card
volume to compensate for missing external practice.

## Readiness gates

Devmax scores are diagnostic, not readiness gates.

### Coding

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
