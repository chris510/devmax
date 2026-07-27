# Curriculum — what goes in the deck and why

`cards.json` is not an arbitrary list. This document records how the deck is shaped,
what gets added, what gets retired, and what deliberately lives outside this app.

Companion to `spec.md` (backend behavior) and `docs/DEVIATIONS.md` (spec drift).
This file governs **content**, not architecture. It is not a feature backlog.

---

## Scope of this app

One modality: spaced recall of anything reconstructable out loud in under two
minutes. `QUESTION_RUBRIC` in `app/services/llm.py` enforces it — mechanism
reconstruction over definition recall, concrete scenario, one question, no
multi-part sub-questions.

Three subjects, in this app: system design, coding patterns, behavioral.

### Explicitly out of scope — do not build

* Full timed system design practice. Happens in a browser on an external platform.
  Every card narrows to a single probe by design; the app will never walk a whole
  design and should not try.
* Timed coding rounds. Self-run, CoderPad-style, two problems in 45 minutes.
  Coding cards drill pattern recognition, not speed under a clock.
* Behavioral story *authoring*. Stories are written in a doc first, then become
  cards. The app drills stories that already exist.

Reading this document is not a mandate to add interview-simulation features.
`Review Sprint` is the final form of in-app practice.

---

## Deck shape

Current 111 cards:

| Subject | Cards | Share |
|---|---|---|
| System design (concepts, technologies, patterns, problems) | 66 | 59% |
| Coding patterns and warmups | 22 | 20% |
| Behavioral | 10 | 9% |
| Practical builds + company-specific | 13 | 12% |

**Target direction:** coding and behavioral grow; system design holds roughly flat
after the additions below. Rationale: coding rounds appear at every level and
dominate mid-level loops, and behavioral is decisive for level determination at
both target companies. System design only fully pays off at senior loops.

**Do not add coding or behavioral cards speculatively.** That expansion is gated on
a timed-coding diagnostic that has not been run yet. Adding them before it means
committing review budget to an unvalidated ranking.

---

## Core additions (19 cards, add now)

Append to `cards.json`. `seed.py` dedupes on `Card.topic`, so re-running is safe.

```json
[
  { "topic": "The delivery framework: requirements, entities, API, high-level, deep dives", "category": "Delivery", "target_week": 1 },
  { "topic": "Quantifying non-functional requirements and which ones change the design", "category": "Delivery", "target_week": 1 },

  { "topic": "Geospatial indexing: geohash vs quadtree vs S2 cells", "category": "Core Concept", "target_week": 1 },
  { "topic": "Real-time delivery: polling, long-poll, SSE, and WebSockets, and what each costs the server", "category": "Core Concept", "target_week": 1 },
  { "topic": "API design: cursor vs offset pagination, versioning, and error semantics", "category": "Core Concept", "target_week": 1 },
  { "topic": "Bloom filters and when a false positive is acceptable", "category": "Core Concept", "target_week": 2 },
  { "topic": "Count-Min Sketch and HyperLogLog for heavy hitters and cardinality", "category": "Core Concept", "target_week": 2 },
  { "topic": "Cache stampede, hot keys, and negative caching", "category": "Core Concept", "target_week": 2 },
  { "topic": "Data modeling from access patterns: single-table design vs normalized schemas", "category": "Core Concept", "target_week": 2 },

  { "topic": "DynamoDB: partition and sort keys, GSI vs LSI, and hot partitions", "category": "Key Technology", "target_week": 2 },
  { "topic": "Redis as a data-structure store: sorted sets, streams, HyperLogLog, GEO", "category": "Key Technology", "target_week": 2 },
  { "topic": "Flink: event time vs processing time, watermarks, and checkpointing", "category": "Key Technology", "target_week": 3 },
  { "topic": "Vector indexes: HNSW vs IVF-PQ and the recall-latency-memory triangle", "category": "Key Technology", "target_week": 3 },

  { "topic": "Distributed locks: TTLs, fencing tokens, and why Redlock is contested", "category": "System Design Pattern", "target_week": 2 },
  { "topic": "Large blob upload: presigned URLs, chunked resumable uploads, CDN origin", "category": "System Design Pattern", "target_week": 2 },
  { "topic": "Async job submission with status polling and webhook callback", "category": "System Design Pattern", "target_week": 3 },

  { "topic": "Design a proximity search service for nearby places", "category": "System Design Problem", "target_week": 2 },
  { "topic": "Design an ad click aggregator with exactly-once counting", "category": "System Design Problem", "target_week": 3 },
  { "topic": "Design ticket reservation under scarcity", "category": "System Design Problem", "target_week": 3 }
]
```

`Delivery` is a new category. `category` is an unconstrained `str` in
`app/models.py`, so no migration is needed. It is not in `DESK_CATEGORIES`, so these
cards are conversational and push-eligible — intended.

### Card phrasing rule

Topics must name a **mechanism with a reconstructable shape**, not a decision or a
category comparison. "MVCC: how readers avoid blocking writers" works. "SQL vs
NoSQL trade-offs" does not — there is nothing to rebuild, so the model can only
generate recitation prompts, and category comparisons are a known interview
anti-pattern besides.

---

## Retire (4 cards)

Remove from `cards.json`. Existing DB rows are left alone; this only affects
future seeding.

| Card | Reason |
|---|---|
| Big-O in practice: when constant factors dominate | Not probed in senior design rounds |
| Hash functions: uniformity, collisions, and load factor | Subsumed by consistent hashing |
| Processes, threads, and what a context switch actually costs | Rarely load-bearing above mid-level |
| Tokenization and why token counts drive cost | Thin; covered by metering and prompt-caching cards |

Net daily rotation: **111 → 126.**

---

## Modules — seeded on demand, not at startup

Company-shaped depth. Seed only when a loop is scheduled; one to two weeks is
enough for SM-2 to move a card from untested to solid. Live in `modules/`.

```
uv run python -m app.seed --file modules/logistics.json --start-date <today>
```

### `modules/ai-application.json`
```json
[
  { "topic": "Permission-filtered retrieval: ACLs in a vector or search index", "category": "AI Application", "target_week": 1 },
  { "topic": "Document ingestion: chunking strategy, OCR failures, and re-index on update", "category": "AI Application", "target_week": 1 },
  { "topic": "Hybrid search: combining BM25 and dense retrieval with reranking", "category": "AI Application", "target_week": 2 },
  { "topic": "Elasticsearch write path: segments, refresh interval, and near-real-time search", "category": "AI Application", "target_week": 2 }
]
```

### `modules/logistics.json`
```json
[
  { "topic": "Matching and dispatch under constraints: greedy vs batched assignment", "category": "Logistics", "target_week": 1 },
  { "topic": "ETA estimation and the read path for a live map", "category": "Logistics", "target_week": 1 },
  { "topic": "Hot partition mitigation: key salting and two-phase aggregation", "category": "Logistics", "target_week": 2 }
]
```

### `modules/realtime-social.json`
```json
[
  { "topic": "Message storage at scale: partitioning by channel and time bucketing", "category": "Realtime Social", "target_week": 1 },
  { "topic": "Presence at scale: heartbeats, gossip, and eventual accuracy", "category": "Realtime Social", "target_week": 1 },
  { "topic": "Connection state: sticky routing, reconnect, and missed-message backfill", "category": "Realtime Social", "target_week": 2 }
]
```

### `modules/bigtech-classics.json`
```json
[
  { "topic": "Design a web crawler with politeness and dedup", "category": "Big Tech Classic", "target_week": 1 },
  { "topic": "Design a file sync service with chunking and conflict resolution", "category": "Big Tech Classic", "target_week": 1 },
  { "topic": "Composite index column order and covering indexes", "category": "Big Tech Classic", "target_week": 2 }
]
```

### `modules/infra-observability.json`
```json
[
  { "topic": "Time-series databases: rollups, retention, and label cardinality blowup", "category": "Infra", "target_week": 1 },
  { "topic": "API gateway vs load balancer vs service mesh: what each layer owns", "category": "Infra", "target_week": 1 },
  { "topic": "Durable workflow orchestration vs choreographed sagas", "category": "Infra", "target_week": 2 }
]
```

### `modules/model-lab.json` — candidate, not yet split out

Six existing core cards are calibrated to model-lab infrastructure: KV cache,
attention scaling, GPU memory hierarchy, PagedAttention, continuous batching,
speculative decoding. The first three generalize to anyone reasoning about latency
and cost as an API consumer. The last three are lab-internal and serve a small
slice of the target list. If daily volume becomes the binding constraint, move
those three here first.

---

## Required code change

`QUESTION_RUBRIC` in `app/services/llm.py` hardcodes the target companies:

```
preparing for interviews at Anthropic, OpenAI, and Google
```

This skews generated questions toward lab-infrastructure depth. Replace with a
list-agnostic phrasing:

```
preparing for system design interviews across product and infrastructure companies
```

Nothing else in the prompt changes. The mechanism-reconstruction instruction and
the two-minute constraint are correct as written.

---

## Operational notes

`_schedule` staggers due dates from `--start-date` by `target_week`. Reusing an
original start date pushes new cards into the past or far future. Always pass
today's date for additions:

```
uv run python -m app.seed --file cards.json --weeks-through 3 --start-date <today>
```

Cards are deduped on `Card.topic` (`app/seed.py`), so appends never disturb
existing SM-2 state.

**Keeping the deck honest:** cards are added after a real gap shows up, not from a
catalog diff. The intended loop is that an external timed session surfaces
something you could not reconstruct, and it becomes 1-3 cards via QuickAdd in the
same sitting. Without that, the deck drifts toward whatever a prep site sells.
