# Week 2 content review — September 9, 2026

Six complete drafts are ready for owner review. Their status remains `draft_review`; no card has been activated. This review is about source correctness and the question/answer boundary, not V2 activation or interview readiness.

The current production account has not been marked as having completed these lessons by this task. Before release, confirm the three prerequisites: `V4-W2-L1` (data modeling), `V4-W2-L2` (indexes), and `V4-W2-L3` (migration and snapshots).

## Corrections made during the audit

- Denormalization now asks about an atomic price update. Independent endpoint reads can straddle a commit even when both stored copies update atomically.
- Composite indexes accept reversed equality keys, a qualifying partial index, backward scans, and optional covering payload.
- Migration rollback explicitly requires the old representation to keep receiving current writes.
- MVCC identifies the first non-transaction-control statement as the snapshot boundary.

For each card below, read its source and the linked complete authority in `api/cards.json`, then check whether the proposed pass/fail examples draw the right boundary. These examples carry no approved numeric labels and have not been sent to a provider.

## Six decisions

### 1. Data modeling from access patterns

**Question:** A product page must return product details and the five newest reviews in one hot read, while another endpoint paginates every review. How would you shape the product and review data from those access patterns?

**Essential account:** Derives the Product–Review relationship from the two endpoints, keeps the bounded hot subset with the product, and retains the complete review collection for pagination.

**Accept:** Keeping every review separate and joining on read is valid if measured workload and latency make the extra lookup acceptable; the choice must still be justified from the access patterns.

**Proposed retention pass:** Keep all reviews by product ID and a bounded recent-five subset for the hot response; refresh that subset when reviews change.

**Proposed retention failure:** Put every review into an ever-growing product document; the pagination endpoint can download it all.

**Source:** [MongoDB Database Manual · Handle Duplicate Data](https://www.mongodb.com/docs/manual/data-modeling/handle-duplicate-data/) — Example: Duplicate Data for Product Reviews → Steps; Benefits of Duplicating Data.

### 2. Denormalization

**Question:** A seller endpoint embeds product prices for one-read responses, while product pages read the product collection. A price update must commit both stored copies together. How would you preserve the read optimization without a partial update?

**Essential account:** Names the duplicated seller read model and updates both the product and seller copies atomically in one transaction.

**Accept:** Embedding the value in one atomic document or replacing the duplicate with a reference is valid when it still satisfies the required reads.

**Proposed retention pass:** Update both stored prices in one transaction, or remove the duplicate. Independent endpoint reads may still occur on opposite sides of a commit.

**Proposed retention failure:** Issue two writes from the same process. That makes them atomic, so a crash between writes is harmless.

**Source:** [MongoDB Database Manual · Enforce Data Consistency with Transactions](https://www.mongodb.com/docs/manual/data-modeling/enforce-consistency/transactions/) — Enforce Data Consistency with Transactions → Steps → Configure a transaction to handle updates.

### 3. B-tree lookup and range scans

**Question:** An events endpoint reads event_id and created_at for a one-hour created_at range in timestamp order. It already has a B-tree on created_at, but the index scan is still slow. Where are the page reads coming from, and what index change could remove many of them?

**Essential account:** Connects the ordered B-tree range scan to separate, potentially scattered heap fetches and explains how a covering index can avoid many of them.

**Accept:** A bitmap heap scan is acceptable when it is explicitly chosen to batch or reorder heap access rather than pretending the heap is already index-ordered.

**Proposed retention pass:** Ordered index entries still point to scattered heap pages. Include event_id to permit index-only access when visibility allows it.

**Proposed retention failure:** The B-tree already sorts the heap, so increasing tree fan-out guarantees there are no random row fetches.

**Source:** [PostgreSQL 18 · Indexes](https://www.postgresql.org/docs/18/indexes.html) — §11.2.1 B-Tree; §11.9 Index-Only Scans and Covering Indexes.

### 4. Composite indexes

**Question:** For SELECT created_at, total_cents FROM orders WHERE tenant_id = ? AND status = 'open' ORDER BY created_at DESC LIMIT 50, what composite B-tree would you create, and why is that column order useful?

**Essential account:** Uses the equality filters to bound the relevant rows and created_at to provide ordered early LIMIT. Explains INCLUDE as optional payload for an index-only scan, rather than a requirement for correct filtering or ordering.

**Accept:** An ascending created_at key scanned backward, either order of the two equality keys, or a partial (tenant_id, created_at DESC) index WHERE status = 'open' is valid for this exact query. INCLUDE is optional if heap fetches are acceptable; explain its separate covering benefit.

**Proposed retention pass:** Use equality keys before created_at; either equality order works here. An open-orders partial index also works if its predicate is provable. INCLUDE is optional coverage.

**Proposed retention failure:** Put created_at first because it is most selective; every column permutation bounds the same tenant/status region.

**Source:** [PostgreSQL 18 · Indexes](https://www.postgresql.org/docs/18/indexes.html) — §11.3 Multicolumn Indexes; §11.4 Indexes and ORDER BY; §11.9 Index-Only Scans and Covering Indexes; §11.8 Partial Indexes.

### 5. Online data migration

**Question:** You must move a live subscriptions representation into a new table without downtime. What sequence keeps old and new data consistent, proves the new reads before cutover, and preserves rollback until the migration is complete?

**Essential account:** Orders live synchronization, backfill, parity comparison, read cutover, new-first writes with the old store still current, and final retirement.

**Accept:** CDC, an outbox, checksums, or shadow reads are valid alternatives when they preserve incremental synchronization, observable parity, and a reversible cutover.

**Proposed retention pass:** Synchronize live changes, backfill, compare/repair, switch reads gradually, and keep the old store current through the rollback window before retirement.

**Proposed retention failure:** Keep the old table but stop updating it as soon as reads switch. It will always remain a safe rollback target.

**Source:** [Stripe Engineering · Online migrations at scale](https://stripe.com/blog/online-migrations) — A pattern for online migrations; Part 1: Dual writing; Part 2: Changing all read paths; Part 3: Changing all write paths; Part 4: Removing old data.

### 6. MVCC snapshots

**Question:** A Repeatable Read transaction reads stock = 1. Another transaction changes that row to 0 and commits. What does the first transaction see on a second SELECT, and what happens if it then tries to update that row?

**Essential account:** Explains stable row-version visibility, the concurrent writer's commit, the stale transaction's update conflict, and full-transaction retry.

**Accept:** The answer may describe old and new row versions without saying MVCC, provided it predicts the same second-read and update outcome.

**Proposed retention pass:** The second SELECT sees 1. Updating the changed row fails; abort and retry the whole transaction using a new snapshot.

**Proposed retention failure:** The older snapshot authorizes overwriting stock 0 with no conflict, or retrying only the final statement.

**Source:** [PostgreSQL 18 · Concurrency Control](https://www.postgresql.org/docs/18/mvcc.html) — §13.1 Introduction; §13.2.2 Repeatable Read Isolation Level.

## Approval and release

Full basis and all five rubric fields: [cards.json](../api/cards.json). The existing [38-case V2 draft pack](../api/scripts/grounded_recall_v2_cases_stage2_draft.json) remains pending and is a separate numeric calibration gate. Approval of these six card drafts does not approve those V2 labels.

Owner decision to record: which questions/authority are approved, any corrections, and which prerequisite lessons are actually complete. Release only the corresponding approved cohort after those prerequisites; use Learn before the first delayed review. Completion of a plan item is never simulated or changed by this audit.

## Week 3 preparation

The next six decisions are assembled in the [Week 3 review packet](WEEK-3-CONTENT-REVIEW-2026-09-09.md).

The six Week 3 sources were also checked. Cache-aside now explicitly includes the in-flight stale-refill race; the Raft-based replication answer limits direct majority counting to current-term entries. The ordered-tablet, consistent-hashing, expiration-herd, and per-operation CAP accounts remain drafts.

The CAP DOI page did not expose its text; the [authorized Eric Brewer republication](https://www.infoq.com/articles/cap-twelve-years-later-how-the-rules-have-changed/) supplies an accessible reading path. The article says it originally appeared in Computer and is republished with IEEE Computer Society.

Validation: 43 seed/grounding and draft-pack tests pass. No approval flag, production card, score, study-plan progress, or scheduling field changed.
