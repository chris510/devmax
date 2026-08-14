# Card actionability audit — 2026-08-11

> **Resolution update:** Curriculum v4 replaces the 84 generic first-party
> activities with 116 concrete, source-linked items at 20 scheduled hours per
> week, uses Python throughout coding practice, and maps all 54 topics exactly
> once to their teaching lesson. The user confirmed Hello Interview Premium
> access. These changes close the plan-navigation and prerequisite-linkage
> findings below; they do not change the content-approval result: six cards are
> ready, one is a reviewed draft candidate, and 47 remain blocked until grounded.

> **2026-08-14 follow-up:** Curriculum v6 authors complete `draft_review`
> grounding for all twelve Week 2–3 cards from primary or official technical
> sources. WAL is replaced by reversible online migration, basic hash sharding
> by quorum-safe leader/follower replication, and cache stampede is narrowed to
> expiration herds, request coalescing, and TTL jitter. These drafts are not
> approved or activated; their source claims, questions, rubrics, and 38-case V2
> evaluation pack still require explicit owner review.

## Verdict

The 54-card base deck is a credible recall spine, but only Week 1 is ready for
use. A topic and source URL are not yet a lesson, a trusted correction, or an
approved retrieval.

| Activation state | Cards |
|---|---:|
| Ready: approved basis, rubric, question, and source | 6 |
| Draft: fully authored but awaiting human approval | 13 |
| Blocked: topic-level provenance only | 35 |
| **Total** | **54** |

Question shape is a separate judgment from activation:

| Shape | Cards |
|---|---:|
| Atomic: can support one scenario and one central retrieval | 35 |
| Narrow first: currently bundles multiple decisions or mechanisms | 19 |

“Atomic” does not mean approved. The six ready cards are unchanged. The twelve
Week 2–3 drafts and one Twitter draft now have a basis, rubric, and question, but
still need owner source review, spoken-answer review, and evaluation approval.
The other 35 cards still need complete grounding.

## Structural blockers

- All 54 entries have source URL, section, evidence, and a written activation
  prerequisite. After the curriculum v6 draft update there are 36 unique URLs:
  41 cards cite Hello Interview, six cite official documentation, two cite
  official engineering guidance, four cite primary papers, and one cites a
  primary practitioner talk.
- The six Week 1 cards are approved. The twelve Week 2–3 cards and historical
  Twitter fan-out card are complete drafts. The remaining 35 have no approved
  answer authority.
- At audit time, the curated Study Plan had 84 generic items, empty source
  excerpts, no actionable source links, and no explicit card mapping. Curriculum
  v4 resolves those structural findings with direct resources, specific
  completion conditions, and read-only mapped topics. It deliberately does not
  treat Premium URLs as answer authority or activate a card on completion.
- None of the 18 Anthropic, Google, or OpenAI overlay cards is grounded. They
  are not activation-ready.

### Source-access risk

At the original 2026-08-11 snapshot, Hello Interview was the provenance for 53
cards. Thirteen of the 31 distinct source pages displayed an explicit Premium
gate and supported 28 cards. The user subsequently confirmed Premium access, so
those links are usable for this private plan. Curriculum v6 replaces twelve of
those card citations with official or primary technical sources; the historical
Premium figures are retained here only to document the original audit.

Before a card is exposed, the product must either confirm that its complete
source is accessible or provide a concise, human-approved, internally authored
lesson derived from the trusted answer basis. It must not scrape or reproduce
paid course material.

## Card-by-card result

### Week 1 — ready, with prerequisite sequencing repair

| Card | State | Shape | Required action |
|---|---|---|---|
| Delivery: requirements → entities → APIs → HLD → deep dives | Ready | Atomic | Surface the Delivery Framework lesson or approved Learn summary, then delay the scored review. |
| Turn scale, latency, availability, and durability into constraints | Ready | Atomic | Surface the Delivery requirements lesson and its observable done-when. |
| Derive API identity from trusted authentication context | Ready | Atomic | Surface API Design before review; do not treat the request-body user ID as authority. |
| Timeouts, retries, and idempotency after a partial failure | Ready | Atomic | Surface Networking Essentials before review. |
| Cursor pagination under concurrent inserts | Ready | Atomic | Surface API Design before review. |
| Estimate only the bound that changes a decision | Ready | Atomic | Surface the Delivery estimation section before review. |

The active Week 1 cohort requires Delivery Framework, API Design, and Networking
Essentials. The current Week 1 Plan teaches only Delivery and requirements; API
and networking are still placed in Week 2.

### Week 2 — draft review

| Card | State | Shape | Required action |
|---|---|---|---|
| Data modeling from access patterns | Draft | Atomic | Owner-review the endpoint-driven schema scenario, basis, rubric, and question. |
| Denormalization without losing update correctness | Draft | Atomic | Owner-review the read-optimized duplicate and its consistency repair. |
| B-tree lookup and range scans | Draft | Atomic | Owner-review the concrete range query and page-locality decision. |
| Composite indexes and left-prefix matching | Draft | Atomic | Owner-review the concrete query and index-column order. |
| Online data migration | Draft | Atomic | Owner-review the reversible dual-write, backfill, comparison, cutover, and rollback sequence. |
| MVCC snapshots and bounded anomalies | Draft | Atomic | Owner-review the concurrent-update snapshot scenario and retry boundary. |

Curriculum v6 now provides source-bearing Week 2 Learn items and complete card
drafts. None is actionable until the owner verifies the linked source claims,
question, basis, rubric, and pending V2 labels.

### Week 3 — draft review

| Card | State | Shape | Required action |
|---|---|---|---|
| Cache-aside reads and stale-data windows | Draft | Atomic | Owner-review the miss/populate/invalidate race. |
| Cache stampede under synchronized expiry | Draft | Atomic | Owner-review expiration jitter and same-key request coalescing. |
| Leader/follower replication and safe failover | Draft | Atomic | Owner-review majority commit, lagging followers, and the failover safety boundary. |
| Range sharding and scan-preserving routing | Draft | Atomic | Owner-review one ordered-tablet split, placement, and cross-boundary scan. |
| Consistent hashing and virtual nodes | Draft | Atomic | Owner-review the add-capacity/key-movement scenario. |
| CAP behavior during a partition | Draft | Atomic | Owner-review which specific operations remain available or reject. |

Curriculum v6 now maps all six Week 3 drafts to source-bearing Learn items,
including replication and failover safety. Owner approval remains mandatory.

### Week 4 — five blocked, one draft

| Card | State | Shape | Required action |
|---|---|---|---|
| Compose caches, replicas, and denormalized read views | Blocked | Narrow first | Ask for one read bottleneck and the consistency cost of the chosen path. |
| Preserve read-your-writes under replica lag | Blocked | Atomic | Ground one post-write read-routing scenario. |
| Historical Twitter hybrid fan-out | Draft | Atomic | Human-review the primary talk, rubric, and question; keep it historical and threshold-free. |
| Partition, batch, and asynchronously ingest writes under skew | Blocked | Narrow first | Reduce to one hot-partition decision under measured skew. |
| Backpressure and load shedding | Blocked | Atomic | Ground one overloaded dependency and explicit admission policy. |
| Queue-worker concurrency, ordering, retry, and DLQ | Blocked | Narrow first | Focus on crash-after-effect-before-ack, bounded concurrency, and terminal poison handling. |

One 120-minute Learn item currently claims to cover read scaling, write scaling,
replica lag, fan-out, backpressure, and queues. Those sources need separate
checkpoints.

### Week 5 — blocked

| Card | State | Shape | Required action |
|---|---|---|---|
| Select contention control for one invariant | Blocked | Atomic | Use one scarce-reservation scenario and defend one primitive. |
| Distributed locks, leases, TTLs, and fencing | Blocked | Atomic | Ground one paused/stale owner and require a fencing explanation. |
| Saga compensation | Blocked | Atomic | Ground one partially completed workflow and irreversible step. |
| Durable workflow orchestration | Blocked | Atomic | Ground progress persistence across crash, retry, and long wait. |
| Circuit breakers, bulkheads, and jittered retries | Blocked | Narrow first | Focus on dependency isolation and circuit opening; retries are already a Week 1 mechanism. |
| Exactly-once effects over at-least-once delivery | Blocked | Atomic | Ground consumer dedupe committed atomically with the business effect. |

The topic order is reasonable, but 120 total Learn minutes is not a credible
source path for six resilience mechanisms.

### Week 6 — blocked

| Card | State | Shape | Required action |
|---|---|---|---|
| Polling, long-polling, SSE, and WebSocket delivery | Blocked | Narrow first | Ask one SSE-versus-WebSocket decision from directionality and state requirements. |
| Reconnect recovery and missed-event backfill | Blocked | Atomic | Ground one disconnect, cursor, and replay scenario. |
| Multipart upload, checksums, and resumability | Blocked | Atomic | Ground client-to-object-store upload correctness. |
| Large-blob processing, object storage, and CDN delivery | Blocked | Narrow first | Replace with RPO/RTO and tested disaster recovery. |
| Submission, status, cancellation, and completion for long jobs | Blocked | Atomic | Ground one durable job lifecycle. |
| Transactional outbox | Blocked | Atomic | Ground the database commit/publication failure boundary. |

The two blob cards overlap each other and later CDN coverage.

### Week 7 — blocked

| Card | State | Shape | Required action |
|---|---|---|---|
| PostgreSQL indexes, transactions, replicas, and write bottleneck | Blocked | Narrow first | Ask what to do when read replicas cannot fix the measured write bottleneck. |
| Redis strings, hashes, sorted sets, streams, and HLL | Blocked | Narrow first | Choose one access-pattern scenario, such as a leaderboard. |
| Redis durability and eviction | Blocked | Narrow first | Use one restart or max-memory failure; do not quiz two independent surveys. |
| DynamoDB keys and indexes without a hot partition | Blocked | Atomic | Ground one access pattern and partition-key correction. |
| Cassandra write/read paths, compaction, and repair | Blocked | Narrow first | Use the historical Discord hot-partition and read-amplification case. |
| API gateway authentication, quotas, routing, and policy | Blocked | Atomic | Ground one edge request path with a clear trust and quota boundary. |

API Gateway is a Week 7 card but is not taught until the current Week 8 Plan.

### Week 8 — blocked

| Card | State | Shape | Required action |
|---|---|---|---|
| Kafka ordering, offsets, groups, and rebalancing | Blocked | Narrow first | Ground one partition-key and rebalance scenario. |
| At-least-once event processing | Blocked | Atomic | Ground a crash after side effect but before acknowledgement. |
| Elasticsearch inverted indexes, refresh, and merge | Blocked | Narrow first | Focus on document visibility after refresh and segment-merge cost. |
| CDN hierarchy, shielding, invalidation, and signed access | Blocked | Narrow first | Use one stale or private-object invalidation scenario. |
| Distributed rate limiting and regional drift | Blocked | Narrow first | Ask whether accounting is local or global and what overshoot is acceptable. |
| Time-series partitioning, compression, rollups, and cardinality | Blocked | Narrow first | Focus on label-cardinality explosion and one rollup decision. |

The sources align broadly, but six technologies are compressed into 270 minutes
of Learn work.

### Week 9 — blocked

| Card | State | Shape | Required action |
|---|---|---|---|
| Flink watermarks, late data, state, and checkpoints | Blocked | Narrow first | Ground one late event after a watermark and recovery from a checkpoint. |
| ZooKeeper ephemeral nodes, watches, leases, and sessions | Blocked | Narrow first | Reframe as consensus, leader election, and replicated-log safety; keep ZooKeeper only as an example. |
| Bloom filters | Blocked | Atomic | Ground one avoid-an-expensive-read decision with tolerated false positives. |
| Count-Min Sketch and HyperLogLog | Blocked | Narrow first | Replace with a user-visible SLO and error-budget decision. |
| Geospatial cell indexing | Blocked | Atomic | Ground one resolution/candidate-retrieval trade-off using historical Uber H3. |
| HNSW vector search | Blocked | Atomic | Ground one recall, latency, and memory tuning branch. |

## Five one-for-one replacements

These changes preserve the 54-card cap.

| Current slot | Replacement | Why | Primary grounding source |
|---|---|---|---|
| W2 WAL internals | Online data migration: dual write, backfill, compare, cutover, rollback | More transferable senior-backend judgment; PostgreSQL depth remains later. | [Stripe: Online migrations at scale](https://stripe.com/blog/online-migrations) |
| W3 basic hash sharding | Leader/follower replication, failover, lag, and split-brain protection | Range and consistent hashing retain partitioning; replication is promised but absent. | [Raft paper and materials](https://raft.github.io/) plus the chosen database's official replication documentation |
| W6 duplicate large-blob delivery | RPO/RTO, backups, failover, and restore testing | Upload and CDN already cover blobs; recovery objectives are absent. | [Google Cloud disaster-recovery planning guide](https://docs.cloud.google.com/architecture/dr-scenarios-planning-guide) |
| W9 ZooKeeper survey | Consensus, leader election, replicated-log safety, and quorum availability | Underlying mechanism transfers better than a vendor feature inventory. | [USENIX: In Search of an Understandable Consensus Algorithm](https://www.usenix.org/conference/atc14/technical-sessions/presentation/ongaro) |
| W9 Count-Min Sketch + HLL | User-visible SLI/SLO and error-budget rollout decisions | The current card combines unrelated sketches; HLL already appears in Redis. | [Google SRE: Service Level Objectives](https://sre.google/sre-book/service-level-objectives/) |

Candidate canonical scenarios:

- Move a live subscriptions table without downtime while preserving rollback.
- Fail over after an acknowledged write may not have reached every follower.
- Turn a five-minute RPO and one-hour RTO into a recovery architecture and
  restore drill.
- Prevent two leaders from committing conflicting entries during a partition.
- An API averages 40 ms while one percent takes two seconds; define the SLI,
  SLO, and rollout decision.

## Named practice migration

Named company architectures belong in full external practice. They do not add
generic recall cards. Every item must be labeled as a historical case by
publication date; no fixed traffic or follower threshold may be generalized as
a current company rule.

| Week | Replace a generic practice with | Primary source |
|---|---|---|
| 3 | Shopify pod isolation: sharding plus blast-radius containment | [Shopify engineering](https://shopify.engineering/a-pods-architecture-to-allow-shopify-to-scale) |
| 4 | Twitter home timeline: ordinary write-time materialization plus a high-fan-out read-time merge | [InfoQ practitioner talk](https://www.infoq.com/presentations/Twitter-Timeline-Scalability/) |
| 5 | Stripe payment retry, idempotency, and workflow state | [Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests) |
| 6 | Slack real-time delivery, connection state, and regional draining | [Slack engineering](https://slack.engineering/real-time-messaging/) |
| 7 | Discord message storage: hot partitions, request coalescing, and migration | [Discord engineering](https://discord.com/blog/how-discord-stores-trillions-of-messages) |
| 8 | Discord message search: indexing throughput, refresh, and tail latency | [Discord engineering](https://pax.discord.com/blog/how-discord-indexes-trillions-of-messages) |
| 9 | Uber H3: cell resolution and proximity-candidate retrieval | [Uber engineering](https://www.uber.com/us/en/blog/h3/) |

## Dependency order

1. Week 1: Delivery Framework → API Design → Networking Essentials → six
   delayed scored reviews.
2. Week 2: Data Modeling → Database Indexing → MVCC → online migration.
3. Week 3: caching → partitioning → replication/failover → partition behavior.
4. Week 4: read scaling → write scaling/overload → queues → historical Twitter
   practice.
5. Week 5: contention → idempotent effects/outbox → sagas/workflows → failure
   practice.
6. Week 6: realtime/reconnect → uploads/jobs → disaster recovery.
7. Week 7: PostgreSQL/Redis → DynamoDB/Cassandra → API gateway → storage-choice
   practice.
8. Week 8: Kafka/delivery semantics → Elasticsearch/CDN → rate limiting and
   time-series trade-offs.
9. Week 9: Flink → consensus → specialized indexes → SLO judgment.
10. Weeks 10–12: no generic additions; exact mocks, target overlays, and only
    gaps observed in external performance.

Weeks 10–12 must name the prompt or prompt-bank ID, timebox, allowed aids,
required recording/diagram/decision log, evaluator, readiness rubric, and the
single highest-impact repair. “Complete two mocks” is not an actionable item.

## Actionable-card acceptance contract

A card is actionable only when all of the following are true:

1. **Accessible learning authority.** The user can open the complete source or
   an approved, internally authored Learn summary without encountering an
   unhandled paywall.
2. **Observable prerequisite.** The Plan names the exact lesson and a specific
   done-when, not “understand” or “complete the work.”
3. **Trusted grounding.** Source label, answer basis, five rubric fields, and
   canonical question have been human-reviewed.
4. **One retrieval.** The fixed question contains one scenario and one central
   mechanism or decision; a strong spoken answer fits under two minutes.
5. **Unscored Learn mode.** Opening the explanation, worked example, rubric, or
   correction creates no score, session history, mastery signal, or SM-2
   movement.
6. **Exposure delay.** Once answer authority has been shown, no scored attempt
   may claim unaided retention in that session. The first scored review belongs
   to the later of eight hours after exposure and the start of the next local
   day.
7. **Explicit activation.** Lesson completion may open a proposal gate, but the
   card exists only after explicit acceptance. Calendar age never activates it.
8. **History-safe maintenance.** A changed question creates a replacement with
   blank history and archived lineage; it never rewrites an old retrieval.
9. **Evaluation evidence.** Each cohort includes strong, mechanism-only,
   confidently-wrong, reconstruction, and parroting cases before approval.

## Production next actions

1. Keep production at the six approved Week 1 cards. Do not activate another
   weekly cohort or any company module.
2. Make the three Week 1 sources actionable in this order: Delivery Framework,
   API Design, Networking Essentials. Confirm Hello Interview access; otherwise
   use approved authored summaries.
3. Treat any same-session answer after Learn exposure as practice, not a scored
   retention attempt.
4. Publish a versioned Study Plan migration with source-bearing items, explicit
   prerequisite links, and specific done-when conditions. Do not silently
   rewrite the active Plan or touch card history/scheduling through a Plan
   operation.
5. Owner-review the redesigned Week 2 drafts: keep four original topics, narrow
   MVCC, and approve or revise the online-migration replacement. Apply the Week
   1 human/evaluation gate before activation.
6. Owner-review Week 3 after the replication swap and stampede narrowing, then
   finish Week 4. The Twitter
   draft may be reviewed independently but must not appear in the existing
   production Plan without the versioned content migration.
7. Ground a company overlay only after a real role makes it relevant; all 18
   current overlay cards remain blocked.
