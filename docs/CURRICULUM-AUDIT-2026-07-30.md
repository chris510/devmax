# Curriculum audit — 2026-07-30

## Verdict

The current 54-card base deck is a credible senior-backend recall spine derived
from the current Hello Interview curriculum. It passes the manifest-level audit
after four corrections: decision-driven estimation replaced rote capacity
arithmetic, API identity replaced the lower-leverage request-path card,
contention now starts from the invariant rather than assuming OCC, and
time-series storage replaced a source-inaccurate observability card. Six broad
topics still need special attention when their canonical questions are
generated.

This is not an audit of the retired 126-card deck. The untracked
`docs/CURRICULUM-COVERAGE.md` describes that retired curriculum and must not be
used to make decisions about the current deck.

## Scope and method

The audit compared:

- all 54 entries in `api/cards.json`;
- all 31 unique Hello Interview URLs referenced by those entries;
- the current Hello Interview system-design taxonomy;
- the desk-only coding-pattern library;
- the behavioral signal list and Big Three;
- the OpenAI L5 and Google L5 role guides;
- the question-generation rubric and production card state.

Every base-card source URL resolved to the expected current lesson on
2026-07-30. Every entry has `source_url`, `source_section`,
`activation_prerequisite`, and `evidence`.

## Structural coverage

| Area | Result |
|---|---:|
| Core-concept families | 9 / 9 represented |
| Key technologies | 9 / 9 represented |
| Common patterns | 7 / 7 represented |
| Advanced topics | 4 / 4 in the base deck |
| Coding families | 16 / 16 in the desk-only library |
| Behavioral signals | 8 / 8 |
| Behavioral Big Three | 3 / 3 |

The deliberate absence of broad problem-breakdown cards is correct. Devmax
cannot honestly measure a 45-minute design in a two-minute voice answer; full
designs remain external practice and produce focused gap cards afterward.

## Card-level result

| Week | Source-aligned | Sharpen at question audit | Revise topic |
|---|---:|---:|---:|
| 1 | 6 | 0 | 0 |
| 2 | 6 | 0 | 0 |
| 3 | 6 | 0 | 0 |
| 4 | 4 | 2 | 0 |
| 5 | 6 | 0 | 0 |
| 6 | 5 | 1 | 0 |
| 7 | 6 | 0 | 0 |
| 8 | 5 | 1 | 0 |
| 9 | 4 | 2 | 0 |
| **Total** | **48** | **6** | **0** |

### Corrections applied

#### 1. Decision-driven estimation

Replaced:

> Capacity estimation: deriving QPS, storage, bandwidth, and a latency budget

The current Delivery Framework says upfront back-of-the-envelope calculations
are often unnecessary. An estimate should be performed when its result can
change a design decision.

With:

> Decision-driven estimation: calculating only the bound that can change an
> architectural choice

The system-design readiness gate now similarly requires quantifying the
constraints that materially affect architecture.

#### 2. API identity boundary

Replaced the lower-leverage request-path card with:

> API identity boundary: deriving the principal from trusted authentication
> context before authorizing a resource or action

The API Design lesson explicitly distinguishes authentication from
authorization and warns against accepting identity from untrusted request-body
or path fields. This gives that senior-backend boundary a first-class retrieval
without increasing the 54-card budget.

#### 3. Contention

Replaced:

> Optimistic concurrency control: compare-and-swap, version checks, and retry
> under contention

With:

> Contention control: choosing a conditional write, pessimistic lock, OCC, or
> serializable isolation for one invariant

The current contention lesson begins with conditional writes, then compares
pessimistic locking, optimistic concurrency, isolation levels, and distributed
locks. The existing distributed-lock card remains valuable and correctly
includes leases, TTL expiry, fencing, and stale owners.

#### 4. Time-series storage

Replaced:

> Observability path: deriving SLIs and joining metrics, logs, and traces during
> an incident

The cited Metrics Monitoring breakdown explicitly excludes log aggregation and
distributed tracing. The replacement is:

> Time-series storage: append-only writes, time partitioning, compression,
> rollups, and label-cardinality limits

It is sourced from the Time Series Databases lesson and completes the fourth
advanced-topic family.

## Portfolio balance

The deck still contains three PostgreSQL cards. That depth is defensible for a
backend interview spine, but the PostgreSQL lesson itself warns candidates not
to lead with WAL or MVCC when the interviewer is testing architectural
judgment. If later practice evidence demands another slot, WAL internals are
the first generic topic to reconsider.

## Topics requiring canonical-question scrutiny

These topics are source-supported but bundle enough subtopics that a generated
question may become multi-part or test only an arbitrary fragment:

1. PostgreSQL query path: indexes, transactions, replicas, and write bottleneck.
2. Redis data structures: strings, hashes, sorted sets, streams, and HyperLogLog.
3. Count-Min Sketch and HyperLogLog.
4. Circuit breakers, bulkheads, and jittered retries.
5. Real-time delivery across polling, long-polling, SSE, and WebSockets.
6. Large-blob delivery, which overlaps the week-five large-object upload card.

They do not need to be split automatically. First inspect the persisted
canonical question. Approve a question only if it creates one scenario with one
central decision or mechanism.

The large-blob pair deserves an additional duplication check:

- week five should own client-to-object-store upload correctness;
- week nine should own asynchronous processing and delivery from object storage.

If their canonical questions test the same upload path, narrow the week-nine
topic rather than spending two review slots on the same retrieval.

## Production question audit

A read-only production query before the fresh-start operation on 2026-07-30
found exactly the six previously activated week-one cards. All six had
`canonical_question = null` and there were no sessions. No question was
generated or changed during the audit.

The remaining 48 cards are not yet present in production. There are therefore
no persisted canonical questions to approve today.

The guarded fresh-start operation then atomically replaced those six cards with
the corrected week-one cohort. It preserved the one device token and settings
row. Afterward, production had six cards, zero sessions, zero Study Plans, zero
generated questions, and untouched SM-2 defaults. Two cards are due on
2026-07-31, two on 2026-08-01, and two on 2026-08-02.

The current generator strongly instructs the model to produce one concrete,
non-multipart question answerable in under two minutes. However, the seed loader
does not persist curriculum provenance, and the generator receives no source
URL, source section, lesson text, evidence, or authored answer anchor. A
question can satisfy the shape rubric while drifting from its source.

### First-question gate

After a card is opened for the first time, export its persisted
`canonical_question` read-only and record:

| Check | Pass condition |
|---|---|
| Atomicity | One scenario and one central mechanism or decision |
| Voice budget | A strong answer fits comfortably under two minutes |
| Source fidelity | The expected mechanism is supported by the cited lesson |
| Senior signal | Requires reasoning about mechanism, trade-off, or failure |
| Prompt neutrality | Does not reveal the answer or force one named technology |
| Stability | Has an unambiguous answer frame suitable for repeated retrieval |

Any failure should be corrected before the card accumulates review history.
Clearing a bad canonical question merely rerolls the same unconstrained
generator; an approved replacement should be written deliberately and recorded
in this audit.

## Activation decision

- Week 1: content-approved after the estimation and API identity corrections;
  subject to first-question review.
- Weeks 2–3: content-approved, subject to first-question review.
- Week 4: content-approved; scrutinize the PostgreSQL and Redis questions.
- Week 5: content-approved.
- Week 6: content-approved; scrutinize the Count-Min Sketch / HyperLogLog
  question.
- Week 7: content-approved.
- Week 8: content-approved after the contention correction; scrutinize the
  circuit-breaker / bulkhead / retry question.
- Week 9: content-approved after the time-series correction; scrutinize the
  real-time protocol question and check large-blob duplication.

## Authoritative sources checked

- <https://www.hellointerview.com/learn/system-design/in-a-hurry/delivery>
- <https://www.hellointerview.com/learn/system-design/core-concepts/api-design>
- <https://www.hellointerview.com/learn/system-design/patterns/dealing-with-contention>
- <https://www.hellointerview.com/learn/system-design/problem-breakdowns/metrics-monitoring>
- <https://www.hellointerview.com/learn/system-design/deep-dives/postgres>
- <https://www.hellointerview.com/learn/system-design/deep-dives/time-series-databases>
- <https://www.hellointerview.com/learn/code>
- <https://www.hellointerview.com/learn/behavioral>
- <https://www.hellointerview.com/learn/behavioral/course/preparing-for-the-big-three-questions>
- <https://www.hellointerview.com/guides/openai/l5>
- <https://www.hellointerview.com/guides/google/l5>
