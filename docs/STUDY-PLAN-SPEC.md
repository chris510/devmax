# Study Plan — authoritative specification

Extends `spec.md`. Where this file and `spec.md` disagree about Study Plan, this
file wins; where either disagrees with `AGENTS.md`'s load-bearing invariants,
`AGENTS.md` wins. The design handoff is `design_handoff_study_plan/` — V3.4 owns
behavior, V3.5 owns presentation. Intentional divergences from both are recorded
in `docs/DEVIATIONS.md` §18–§26.

`docs/SCORING-CONTRACT-V2-SPEC.md` is the approved scoring amendment. After its
activation gate, it supersedes only Study Plan's weak-card score read and
three-axis terminology: weak-card proposals read Recall, while the plan safety
boundary below remains unchanged. Until activation, the current V1 reads remain
the runtime behavior.

## What it is

A user pastes a subject-agnostic study guide, picks a duration and a weekly
capacity, and gets a calm 8–12 week plan: phases → weeks → items. It shows the
whole journey at a glance and gets more specific as you drill in. It adapts when
capacity or progress changes — but only through a proposal the user confirms.

Sessions are still 1–3 minutes. Study Plan is the layer that says what those
sessions are *for*; it does not change how any of them work.

## The safety boundary

**No Study Plan operation modifies an existing card, score, session, mastery
summary, ease factor, interval, repetition count, next review date, or any other
SM-2 state.** That covers completion, reopening, replanning, pausing, resuming,
duplication, activation, and archiving.

It is structural, not a discipline:

- No column is added to `cards`. Plan→card linkage lives in
  `study_plan_card_links`, so a plan can be deleted outright and the review
  schedule survives.
- The only path that creates cards is a committed card-proposal acceptance.
- `tests/test_study_plan_invariants.py` snapshots every column of `cards` and
  `sessions` before and after each operation and asserts byte-equality. A
  `test_the_column_lists_above_are_complete` guard fails if a column is added
  and the snapshot stops covering it.

Study Plan also never reschedules an existing card, and never reads SM-2 state as
plan progress: a card's review completing is not a plan item completing.

## Data model

Twelve tables, all prefixed `study_plan`. Migrations `0005` (core), `0006`
(card proposals), and `0007` (Practice Debrief). Handwritten;
`alembic revision --autogenerate` stays disabled.

| Table | Holds |
|---|---|
| `study_plans` | title, subject, `subject_slug`, verbatim `guide_text`, status, mode, deadline, `start_date`, default weekly capacity, `current_week_index`, `forecast_end_plan_week`, `revision`, lifecycle timestamps |
| `study_plan_phases` | index, `full_title`, `overview_title`, description |
| `study_plan_weeks` | index, phase, `full_title`, `overview_title`, `override_capacity_minutes`, `advanced_at` |
| `study_plan_items` | week, phase, `guide_order`, type, priority, titles, `why_it_matters`, `done_when`, estimate + source + confidence, status, origin, `source_item_id`, source span and excerpt, `parser_interpretation`, `approved_at`, completion timestamps, notes, study-block metadata |
| `study_plan_item_dependencies` | prerequisite, dependent, kind, source, confidence, rationale, excerpt, `confirmed_at` |
| `study_plan_revisions` | kind, `base_plan_revision`, before/after JSON, reversible, summary |
| `study_plan_guide_drafts` | the pasted guide and its import attempt, before any plan exists |
| `study_plan_practice_debriefs` | one disk-backed, unscored, immutable-on-submit reflection per Practice item |
| `study_plan_card_proposals` | topic, canonical question, five gate results, duplicate result, disposition, `normalized_topic` |
| `study_plan_card_proposal_acceptances` | idempotency key, request hash, proposal revision, status, created card ids |
| `study_plan_card_links` | the only edge between a plan and `cards` |
| `study_plan_duplications` | source → copy provenance |

### Constraints that carry meaning

- **At most one active plan, enforced by the database.**
  `uq_study_plans_one_active` is a partial unique index on `status` where
  `status = 'active'`. Zero active plans is valid. Both Postgres and SQLite honour
  partial unique indexes, so the SQLite suite exercises the production rule.
  Because the index is checked per statement, any code that swaps the active plan
  must `flush()` the pause before the activation.
- `(mode = 'fixed') = (deadline IS NOT NULL)` — a fixed plan without a deadline
  has nothing to validate against, and a flexible plan with one would ignore it.
- `estimate_minutes > 0 AND estimate_minutes % 30 = 0` — the 30-minute grid, so
  no path can make the displayed hour totals unreconcilable.
- `(status = 'committed') = (committed_at IS NOT NULL)` on acceptances, so the
  idempotent-replay lookup is never ambiguous.
- `uq_study_plan_acceptance_key` — unique idempotency key.

### Ordering caveat

These models declare foreign keys but no SQLAlchemy `relationship()`, and the
unit of work orders inserts across mappers from relationships rather than from
table constraints. Insert a plan graph through `_insert_plan_rows`, which flushes
between levels. Without it, `study_plan_items` is emitted before
`study_plan_phases` (they sort alphabetically) and Postgres rejects the batch;
SQLite accepts it, because it does not enforce foreign keys by default.

## The weekly scheduler

`app/services/study_plan_scheduler.py`. Pure functions, frozen dataclasses in and
out, **no writes**. `build_proposal` returns what a confirmed Apply *would* do;
persisting is `study_plan.apply_proposal`, which is what makes "never mutate the
saved schedule until the user confirms" structural.

All arithmetic is in **integer minutes**. Hours exist only in `format_hours`.

```
effective_capacity_minutes(week) = week.override_capacity_minutes
                                ?? plan.default_weekly_capacity_minutes

scheduled_plan_minutes(week) = learn + practice + plan_local_retrieval
```

**Global SM-2 review time is never part of plan capacity.** The review queue is
not owned by the active plan, so an anatomy plan cannot be made to reflow by how
many cards happen to be due. Plan-local retrieval *does* consume capacity —
it came from the guide — while not blocking week advancement.

### Allocation

Ten rules, applied in order: preserve completed work in place; preserve hard
dependency order; preserve phase order; preserve guide order within a phase; Core
before Optional; retrieval after its source; carry forward when a week is full;
never move backward across an unmet hard dependency; never remove Core
automatically; never mutate until confirmed.

Implemented as two passes, because the schedule is **stable by default**:

- **Pass A — stabilise.** Walk weeks ascending. Completed work is pinned. Open
  residents are kept in Core-then-guide-order while they fit; the rest are
  evicted into a carry pool.
- **Pass B — place.** Each carried item goes to the earliest week at or after its
  floor with room. The floor is the latest of the current plan week, its phase's
  first week, its hard prerequisites' weeks, its source item's week, and its own
  original week — so carry is always forward.

The difference from a global re-pack matters: **carried work fills slack, it never
evicts a resident.** A re-pack would sort week 4's overflow ahead of week 5's own
Core items on guide order and displace them.

**Rule 3 is a ceiling as well as a floor.** Work never drifts past the last week
of its own phase. Without that, phase-2 overflow would colonise phase 4's weeks
and the plan could never report that it had run out of room.

Rule 5 is exactly "Core before Optional" and says nothing about Recurring, so
Core sorts first and everything else ties, letting guide order break it.

Items are **atomic**. An item larger than any eligible week returns
`does_not_fit`, and the app offers: raise that week's capacity, edit the
estimate, or move it outside the plan. Nothing is ever split.

### Validation

A proposal is valid only when every affected week fits, every hard dependency
stays ordered, a fixed plan lands on or before its deadline, no Core work
vanished without explicit confirmation, and nothing is left unplaced. Failures
come back as stable codes (`week_over_capacity`, `dependency_out_of_order`,
`deadline_missed`, `core_removed_without_confirmation`, `work_unplaced`) so the
client renders its own copy. Apply is a native `disabled` button; finishing early
is valid.

Applying a stale proposal is a **409**. Every proposal carries the
`base_plan_revision` it was computed against; `plan.revision` is bumped by every
material schedule write.

### Worked example (V3.4 §3, and what the tests assert)

Week 4 baseline **660 / 720**. The user overrides Week 4 to **420**.

Completed work is preserved: 90 + 90 + 120 + 30 + 30 = **360**. Room for open
work: 60. `L4-03` is Core, first in guide order, and 60 minutes — it fits.
Overflow **240**.

| Item | Est. | Lands |
|---|---|---|
| Behavioral catalog (Core) | 90 | Week 6 |
| Consistent-hashing (Optional) | 30 | Week 5 |
| Architecture write-up (Optional) | 60 | Week 6 |
| Gateway redraw (Recurring) | 30 | Week 5 |
| Weekly pass (Recurring) | 30 | Week 6 |

60 → Week 5, 180 → Week 6. Totals: Week 4 **420/420**, Week 5 **720/720**, Week 6
**660/720**. Forecast Week 12, unchanged.

### Forecasts

Plan-week precision only. `week_start_date(plan, n) = start_date + 7·(n−1)`, and
the label is `week of <date>`. **No completion day is ever derived from weekly
capacity** — there is no field in any response from which one could be.

## Falling behind

A week ending with incomplete Core work changes nothing by itself. It produces a
proposed carry-forward the user confirms. A flexible plan may move later weeks
and update the week-level forecast; a fixed plan preserves the deadline and
offers scope or capacity resolutions. A missed reminder is never evidence of
anything.

## Lifecycle

`active | paused | completed | archived`. Completed is not archived — both are
listed separately in the Plans sheet, and completed plans keep their recap.

- Pausing freezes progression and stops that plan's local reminders. It moves no
  dates.
- Resuming may require a confirmed replan. A short pause usually needs none.
- Making another plan active pauses the current one, after confirmation.
- **Duplicate** copies guide provenance, phases, weeks, items, dependencies,
  estimates, and user edits. It resets item completion, retrieval completion,
  dates, and reminder settings. It copies **no** cards, scores, sessions, SM-2
  state, mastery, or plan history — the duplicate starts with an empty revision
  log and no card links. It is created paused; Preview runs again before it can
  be activated.

## Reopening

Changes Study Plan progress only. Never deletes or modifies a card, never changes
a score, never rewinds SM-2. Writes a plan revision. Produces a replan proposal
when capacity or dependency ordering would be affected, and moves no dates until
that proposal is confirmed.

## AI-assisted guide import

One Claude call, on `POST /study-plans/preview`. Model `claude-opus-5` at effort
`high` (`settings.studyplan_model` / `studyplan_effort`), structured output via
`output_config.format`, streaming, `max_tokens=96000`, and a prompt-cache
breakpoint on the rubric.

Three things about that configuration are load-bearing:

- **Streaming is required.** The SDK refuses a non-streaming request this large.
- **`max_tokens` bounds thinking *plus* output on Opus 5.** At 32000 the model
  spent the entire budget thinking and returned one empty thinking block with
  `stop_reason: max_tokens`. Measured against `docs/CURRICULUM.md`, a successful
  import spends roughly 20–30k thinking and 20–25k on the structure.
- **The rubric is cached.** It clears 1024 tokens and Opus 5's minimum is 512, so
  unlike the three session rubrics this one is a real breakpoint. A measured run
  reported `cache_read_input_tokens: 3417`.

The guide goes in the *user* turn, after the cached rubric.

### Validation is the gate

`app/services/study_plan_import.py` sits between the model and the database.
**Model output never creates an active plan** — it creates a preview, and
creation is a separate confirmed request that reads the preview.

Two rules:

1. **Never trust model arithmetic.** Every total, every weekly load, every
   capacity comparison is recomputed. The rubric explicitly tells the model *not*
   to make the numbers fit, so a plan that does not fit surfaces as a finding
   rather than as quietly shrunken estimates.
2. **Never trust model offsets.** Source spans are recomputed by locating the
   excerpt in the verbatim guide. In the live run 31 of 72 items came back with
   offsets pointing at the wrong span while the excerpts themselves were
   verbatim; treating that as a provenance failure would have blocked a good
   import. Once located, the excerpt is re-read from the guide so the stored
   quote and the stored span are the same text. An excerpt that appears *nowhere*
   is a real failure — it means the item was invented.

Structural problems that cannot be reconciled raise `ImportError_` and the only
move is a retry. Everything else becomes a **check**.

### Checks

`duration`, `capacity`, `deadline`, `estimates`, `coverage`, `retrieval`,
`dependencies`, `titles`, `core`. Each is `ok`, `needs_review` (with a
destination), or `blocked`. **Create Plan unlocks only when every check is `ok`.**
Resolved checks render as one status line with nowhere to go; only exceptions
carry a disclosure arrow.

### Concise titles

`overview_title` on phases and weeks, generated during Preview, user-editable,
**display only** — never used in scheduling, dependency, duplicate, card, or
scoring logic, and never in an accessible name.

Rules: name the subject not the activity list; at most five words and 28
characters; never a truncation or ellipsis; keep the guide's terminology; stay
subject-agnostic; unique within the phase; no vague fragments. V3.5 §4 says
"2–5 words", but its own worked examples are single words ("Databases",
"Coordination", "Filtration"), so the enforced floor is "not empty" and the real
constraint is the vagueness rule — `Databases` passes and `Systems` does not.

### Retrieval activities

Plan-local retrieval is distinct from Devmax recall cards. It may be imported,
generated, or manual; defaults to Recurring; consumes plan capacity; does not
block week advancement unless promoted to Core; tracks its own completion; and
never uses SM-2 completion as plan-item completion. **Generated retrieval must be
approved during Preview** — an unapproved activity has no row built for it in
`build_plan_rows`, which is the enforcement point.

## Practice Debrief

Practice completion diverges deliberately from Learn completion. Completing an
eligible Practice item commits plan progress first, then offers one optional,
unscored debrief: what went wrong, felt shaky, or surprised the user. `Not now`
returns directly to the completed item. Learn items retain the existing automatic
source-based proposal check.

A debrief is available only when all four conditions hold:

1. the item is Practice;
2. the item is complete;
3. the plan subject passes card eligibility; and
4. the item has a non-empty trusted source excerpt.

The source excerpt is mandatory because the user's own account can identify a gap
but can never be the answer authority. Practice proposals receive the debrief as
an observed gap and the source excerpt as the answer basis. The same five-question
gate and explicit acceptance then apply.

The client saves every edit to disk first and sends a cheap, idempotent draft
backup after a debounce. Submission creates one immutable debrief per Practice
item. An exact submission replay is idempotent; different text after submission is
a 409. Reopening and re-completing the item preserves the debrief and any accepted
cards, and does not offer a second debrief. Saving or submitting a debrief touches
no card, session, score, mastery, SM-2 field, plan status, or plan revision.

## Card proposals

Generated only after the source item is **completed**, and only for subjects the
technical rubric can grade. Learn items use the completed source item directly.
Practice items additionally require a submitted Practice Debrief and a trusted
source excerpt.

### Subject eligibility — two keys and a veto

The importer must report the subject as supported, the slug must contain a
technical token, and it must contain no non-technical token. The deny list wins:
`anatomy-of-distributed-systems` is refused. Matching is by token rather than by
exact slug because the live import returned `senior-backend-interview-prep`,
which an exact allowlist rejected — the flagship use case, refused on a variant.

Law, anatomy, and language plans use plan-local retrieval and create no cards.
The model is never even called for them.

### The five-question gate

1. What source lesson or observed failure justifies the card?
2. Can its mechanism be reconstructed in under two minutes?
3. Does it test a scenario rather than a definition?
4. Would a different answer change an interview decision or reveal a real gap?
5. Is it more useful than spending the same review budget on an existing weak card?

All five must pass. A missing or malformed answer counts as a **failure** — the
gate exists to stop weak cards, so an unparseable answer must not open the door.
Failures appear under `NOT SUGGESTED` with the failed question and a reason, are
**not selectable and not counted**, and carry no action. There is no "add back".
Capturing and grounding a gap by hand remains available and separate.

At most three suggestions; low confidence produces none.

### Acceptance — atomic and idempotent

`POST /study-plans/{id}/card-proposals/accept` with selected ids, edits, an
idempotency key, and the proposal revision.

One transaction:

1. Look up the key. A `committed` row with a matching `request_hash` returns its
   original `created_card_ids` and creates nothing. A matching key with a
   *different* hash is a 409 — that is a client bug, not a replay. A
   `processing` row means a previous attempt rolled back and it is safe to run
   again.
2. Re-run the exact normalized-topic check against `cards` for the whole batch
   **before anything is staged**. If a duplicate appeared since Preview, abort,
   refresh that candidate as `EXISTING`, and create nothing.
3. Insert every card, flush, insert the links, flip the acceptance to
   `committed`, commit.
4. Return card ids only after commit. The UI shows `ADDED` only then.

An approved proposal's question is persisted as `cards.canonical_question`, so
the first review reuses it rather than regenerating — the "generated once, then
reused" invariant applies from birth.

An item is proposal-eligible only when `recall_supported = true` and its trusted
source excerpt is non-empty. The proposal model must return the five-field answer
rubric. Acceptance revalidates every proposal through the same grounding gate as
Capture before staging the acceptance row, cards, or links. Missing authority
aborts the entire batch. The approved source excerpt becomes the card's
`answer_basis`; the debrief remains gap-selection context and never answer
authority.

### Duplicate checking

`normalize_topic`: NFKC, trim, casefold, fold `-–—_/\|` to spaces, drop
`"'`.,;:!?()[]{}«»''""`, collapse whitespace. Separators become spaces rather
than being deleted because they carry meaning (`read-your-writes`, `CI/CD`).

An exact normalized match is authoritative, deterministic, and blocking, and it
is re-checked inside the acceptance transaction. Possible semantic overlap is
surfaced with the existing card's context and four actions — keep the new one,
use existing, edit, skip — and is **never decided silently**.

## API

All routes require `X-API-Key`. Response schemas are screen-shaped and carry no
internal item ids in user-facing strings.

| Method | Path |
|---|---|
| POST | `/study-plans/preview` |
| POST | `/study-plans/preview/{draft_id}/retry` |
| PATCH | `/study-plans/preview/{draft_id}` |
| POST | `/study-plans` |
| GET | `/study-plans` |
| GET | `/study-plans/active/summary` |
| GET | `/study-plans/{id}` |
| GET | `/study-plans/{id}/weeks/{index}` |
| GET | `/study-plans/{id}/items/{item_id}` |
| PATCH | `/study-plans/{id}/items/{item_id}` |
| POST | `/study-plans/{id}/items/{item_id}/complete` |
| GET/POST | `/study-plans/{id}/items/{item_id}/practice-debrief` |
| PATCH | `/study-plans/{id}/items/{item_id}/practice-debrief/draft` |
| POST | `/study-plans/{id}/items/{item_id}/reopen/preview` |
| POST | `/study-plans/{id}/items/{item_id}/reopen` |
| POST | `/study-plans/{id}/replans/preview` |
| POST | `/study-plans/{id}/replans/apply` |
| PATCH | `/study-plans/{id}/weeks/{index}/capacity` |
| POST | `/study-plans/{id}/pause` · `/resume/preview` · `/resume/apply` · `/activate` · `/complete` · `/archive` · `/duplicate` |
| GET | `/study-plans/{id}/revisions` · `/recap` |
| POST/GET | `/study-plans/{id}/items/{item_id}/card-proposals` |
| POST | `/study-plans/{id}/card-proposals/accept` |
| POST | `/study-plans/{id}/card-proposals/resolve-duplicate` |

**No Claude call on a read.** Plan reads, week reads, item reads, schedule
application, Today's summary, and reminder handling all resolve from the
database. `/study-plans/active/summary` is declared before `/{plan_id}` so
`active` is never parsed as an id, and it returns `{"active": false}` rather than
404 when there is no active plan.

## iOS

Navigation is Today → Plan overview → Week detail → Item detail, through the
existing `NavigationStack` and `AppState.path`. No new tab.

`StudyPlanState` is a second `@MainActor ObservableObject` alongside `AppState`.
It holds domain state only and never pushes a screen.

### Today

`AppState.loadToday` fetches the plan summary with `async let`, **concurrently**
with `due()`, and swallows the failure into an optional. A Study Plan outage
degrades one line to `PLAN · UNAVAILABLE →`; the due cards are untouched. One
compact line, no capacity, no progress paragraph, accent on the caret only.

### Density budget

At default type on 390×844: every phase header visible without scrolling, at most
two lines per collapsed phase, one forecast, one phase expanded at a time, no NOW
card, no descriptions, no dependency prose, no internal ids, status in text.

### Local study reminders

`StudyReminderService`. `UNCalendarNotificationTrigger` in `Calendar.current`, so
a weekly block stays at the same wall-clock time through a timezone change and
across DST. Identifiers are `wc.plan.<planID>.<itemID>`.

- Authorization is requested **only** when the user first enables a reminder.
  `AppDelegate` skips authorization under `WC_MOCK` because a fixtures build has
  no server to register a push token with — right for APNs, wrong for a local
  reminder, so this path requests its own and is testable in the simulator.
- Not server pushes. Never touches APNs or the device token.
- Missing one changes nothing; changing one changes no capacity or forecast.
- Pausing a plan cancels its requests. Resuming restores only those already on.

### Guide persistence

`GuideDraftStore` mirrors `DraftStore`: one JSON file in Application Support,
atomic write, errors swallowed, written on every edit. A guide that has not been
previewed has never been sent anywhere, so losing it is unrecoverable.

The import-failure state preserves the guide text, duration, capacity, mode,
deadline, and every edit and review choice — server-side on the draft row, and
client-side on disk.

## Debug routes

`WC_ROUTE` values, all prefixed `study-plan` and dispatched before the
Conversation fall-through:

`study-plan-overview` · `-overview-expanded` · `-overview-future` · `-week` ·
`-item` · `-capacity` · `-build` · `-preview` · `-import-failure` · `-replan` ·
`-replan-invalid` · `-fixed-recovery` · `-reopen` · `-reopen-invalid` · `-plans` ·
`-no-active` · `-complete` · `-updates` · `-retrieval-audit` · `-estimate-audit` ·
`-dependency-audit` · `-card-proposal` · `-card-failure` · `-card-existing` ·
`-debrief-offer` · `-debrief-idle` · `-debrief-mic-unavailable` ·
`-debrief-recording` · `-debrief-text` · `-debrief-resume` ·
`-debrief-save-failure` · `-debrief-checking` · `-debrief-check-failure` ·
`-debrief-completed`

Flags: `WC_PLAN_NO_ACTIVE`, `WC_PLAN_SUMMARY_FAIL`, `WC_PLAN_FAIL_IMPORT`,
`WC_PLAN_FAIL_ADD_CARD`, `WC_PLAN_REPLAN_INVALID`, `WC_PLAN_REOPEN_INVALID`,
`WC_PLAN_FIXED_RECOVERY`, `WC_PLAN_FAIL_DEBRIEF_SAVE`,
`WC_PLAN_FAIL_DEBRIEF_CHECK`, `WC_PLAN_VARIANT` (`anatomy` | `five-phase`),
`WC_PLAN_CARD_VARIANT` (`none` | `existing` | `unsupported`).

## Failure and retry

| Failure | Behaviour |
|---|---|
| Guide import fails | Draft keeps the guide and every setting; Retry replays it server-side |
| Invalid structured output | `ImportError_`; the draft is `failed` with the reason |
| Source offset mismatch | Recomputed from the excerpt; only an unfindable excerpt is reported |
| Estimate missing / off-grid | Rounded up to the 30-minute grid and flagged for review |
| Capacity overload | A `needs_review` check with the first and busiest week named |
| Unresolved dependency | Dropped if it names a missing item; otherwise the audit |
| Fixed deadline impossible | `needs_review` with the shortfall in days; recovery options |
| Stale replan revision | 409, and the client regenerates the proposal |
| Plan activation conflict | 409 from the partial unique index |
| Reopen needing a replan | 409 telling the client to preview and apply first |
| Duplicate after Preview | Whole batch aborts; candidate refreshes as `EXISTING` |
| Card-accept rollback | Nothing committed; the retry may safely run again |
| Lost response after commit | Same key + hash returns the original ids, `replayed: true` |
| Network loss editing a guide | Disk copy survives; the draft row survives |
| Network loss completing an item | Inline notice; nothing changed |
| Today summary failure | `PLAN · UNAVAILABLE`; due cards load normally |

## Out of scope — do not build

Everything in `spec.md` §"Out of scope", plus: readiness scores or percentages;
exact-day completion forecasts derived from weekly capacity; calendar
integrations; server-side study-block reminders; actual study-time tracking;
automatic creation of generic cards; automatic schedule mutations; a second
review scheduler; any change to SM-2 or any scoring change other than the
separately approved, versioned V2 migration; light
mode; and any motion beyond the four approved animations.
