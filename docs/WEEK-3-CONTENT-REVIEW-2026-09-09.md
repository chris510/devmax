# Week 3 content review — September 9, 2026

The six Week 3 cards remain `draft_review`. This packet makes their questions,
authority, and proposed retention boundaries reviewable after Week 2. It grants
no content approval, numeric calibration label, or permission to skip learning.

Complete the source lessons `V4-W3-L1` (cache paths and expiration herds) and
`V4-W3-L2` (partitioning, replication, and partition behavior) before activating
their approved cards. Both items were pending in the restored production snapshot.

## 1. Cache-aside updates

**Question:** A profile uses cache-aside reads. On an update, should the database
commit or cache invalidation happen first? Explain the stale-value race that
ordering avoids and whether stale refills remain possible.

**Essential account:** A miss reads the store and populates the cache. Commit
before invalidation avoids a new miss loading the pre-commit value between those
two operations. A reader already in flight can still populate an old value after
invalidation, so the order is not a freshness guarantee.

**Proposed retention pass:** Commit then invalidate; an old in-flight read may
still refill afterward. TTL bounds that entry from insertion.

**Proposed retention failure:** Commit then delete makes all future cache reads
fresh, even if a previous loader is paused with the old value.

**Source:** [Azure Cache-Aside pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/cache-aside).
The audit corrected the draft to include the remaining refill race explicitly.

## 2. Expiration herds

**Question:** A deployment warms thousands of popular cache keys at once; an hour
later they expire together and each key receives many concurrent requests. How
should expiration and refill behavior change to keep the database from seeing
the herd?

**Essential account:** Jitter spreads expiration across keys; request coalescing
shares one refill among concurrent callers for a key. State the coordination
scope: a process-local single-flight still permits one loader per process, which
may or may not be sufficient for the origin's capacity.

**Proposed retention pass:** Randomize TTLs and coalesce each key's concurrent
misses; bound loader time and waiter behavior if the shared refill fails.

**Proposed retention failure:** Random TTLs alone ensure one database call when
the same hot key expires.

**Source:** [Redis cache-layer architecture](https://redis.io/blog/cache-layer-architecture-guide/),
cache stampede discussion. Loader failure and coordination scope are useful
follow-up coaching; they must not erase an otherwise correct essential account.

## 3. Safe leader failover

**Question:** In a three-node leader/follower log, the leader acknowledges an order
and then becomes isolated before one follower receives it. What commit and
election rules allow failover without losing that acknowledged order or letting
two leaders commit?

**Essential account:** Acknowledge a current-term Raft entry after durable
majority replication. Require a majority election in a newer term with an
up-to-date-log restriction. An isolated former leader cannot commit alone.
Older-term entries require the current-term commitment rule, not replica counting
alone. Equivalent epoch/quorum protocols are acceptable when their safety rules
are explicit.

**Proposed retention pass:** The leader and one follower persisted the entry;
majority election plus the log-freshness rule prevents a replacement lacking the
committed order, and the isolated minority cannot commit new orders.

**Proposed retention failure:** Acknowledge after a local append, then promote
whichever follower answers a health check first.

**Source:** [Raft paper](https://raft.github.io/raft.pdf), §§5.2–5.4. The audit
clarified the current-term restriction in the answer basis.

## 4. Ordered range partitions

**Question:** A lexicographically keyed event table has one growing tablet but
must preserve range scans. How should that tablet be split and placed, and how
does a client route a scan that crosses the new boundary?

**Essential account:** Split at a row-key boundary into contiguous ordered ranges,
assign the children, and route each scan segment using tablet-location metadata.
Splitting preserves order; it does not repair a persistently hot single key or a
poor key design.

**Proposed retention pass:** Split the interval at a key, update placement
metadata, and follow every child interval intersecting the requested scan.

**Proposed retention failure:** Hash each row into either child; the original
ordered range scan remains local without fan-out or merging.

**Source:** [Bigtable paper](https://research.google.com/archive/bigtable-osdi06.pdf),
§§2, 5.1, and 5.2.

## 5. Consistent hashing during growth

**Question:** A five-node key-value cluster adds a sixth node during live traffic.
How should virtual-node ownership determine exactly which keys move and when
clients may route them to the new node?

**Essential account:** Keys route clockwise to ownership tokens. New tokens take
their preceding intervals, leaving other intervals unchanged. Coordinate data
handoff and routing so publishing ownership does not make data unavailable or
split live writes. Virtual nodes distribute ownership; they are not replicas.

**Proposed retention pass:** Transfer each interval captured by the new node's
tokens, coordinate concurrent updates and cutover, then publish usable ownership.

**Proposed retention failure:** Change `hash(key) % 5` to `hash(key) % 6`; only
one-sixth of the keys change destination and routing needs no migration protocol.

**Source:** [Dynamo paper](https://www.amazon.science/publications/dynamo-amazons-highly-available-key-value-store),
§§4.2 and 4.9.

## 6. Partition decisions by operation

**Question:** Two regions lose contact while one seat remains. Which reservation
and catalog-read operations should continue or reject, and what consistency
consequence does each choice accept?

**Essential account:** Preserve the last-seat invariant with designated/quorum
authority and reject or wait where that authority is unavailable. Harmless
catalog reads may continue with declared staleness. Allowing both sides to sell
the same last seat requires an explicit business decision to accept conflicts and
compensate. CAP does not force every operation into one permanent database label.

**Proposed retention pass:** Only the authorized side confirms the final seat;
both sides can serve cached catalog descriptions while admitting staleness.

**Proposed retention failure:** Both regions can confirm the last seat and remain
single-copy consistent because reconciliation will happen later.

**Source:** [Eric Brewer's authorized CAP retrospective republication](https://www.infoq.com/articles/cap-twelve-years-later-how-the-rules-have-changed/).
The DOI page did not expose readable text during this audit.

## Owner review

Check the complete basis and five-field rubrics in [cards.json](../api/cards.json).
Record corrections or approval for each card and actual source-lesson completion.
These proposed pass/fail examples are unscored and do not approve the separate V2
calibration pack. No production content or study progress changed.
