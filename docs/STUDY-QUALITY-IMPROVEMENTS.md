# Devmax study-quality improvements

Status: **audited product direction and design proposal; not approved for implementation**

Claude Design artifact: [Study Quality Improvements v1](https://claude.ai/design/p/25258e0c-5891-401c-9690-88790421a788?file=Study+Quality+Improvements+v1.dc.html)

The artifact lives in the existing private `devmax` Claude Design project beside
Study Plan v3.5 and Practice Debrief v3. Opening it requires access to that
project. No existing design file was modified.

## Decision summary

Devmax already has the right learning loop: short closed-book retrieval,
corrective feedback, one scored follow-up, a bounded coached re-attempt, and
spaced scheduling based only on unaided mechanism recall. The next release
should make the inputs to that loop trustworthy before adding more review
volume.

The proposed work has three product moves:

1. **Capture fast, ground before it counts.** Quick Add becomes a pending inbox.
   A captured gap cannot enter Today, pushes, scoring, Sprint, or Coverage until
   it has a trusted source, a concise answer rubric, and an approved canonical
   question.
2. **Turn visible depth gaps into safe practice.** Coverage may start a Depth
   repair sprint using cards with weak trade-off or failure-mode signal. It is
   Practice mode and never changes SM-2.
3. **Make card upkeep recoverable.** Archive keeps history. Replacing a question
   creates a new blank-history card and archives the old one instead of
   rewriting the retrieval that earlier scores measured.

This proposal does not alter SM-2, the three-axis scoring model, Study Plan
capacity, or the definition of readiness.

## Why this work is first

### The first-party Study Plan has no trusted excerpts

The committed 12-week plan contains 84 items, but
`api/app/seed_study_plan.py` deliberately seeds every item with an empty
`source_excerpt`.

That currently has two different consequences:

- Practice Debrief is unavailable for every first-party Practice item because
  the server correctly requires a non-empty trusted source excerpt.
- A completed Learn item can still call the proposal model using its title,
  generic rationale, and generic completion condition, without an authoritative
  answer basis.

The intended external-practice loop therefore does not work for the built-in
plan: practice cannot produce a debrief, while Learn proposals can be
insufficiently grounded.

### Cards have questions but no answer authority

`cards` stores a topic, optional pattern/company metadata, and one canonical
question. It does not store the source or the expected mechanism. The scorer
receives the question, transcript, and rolling mastery summary, but no trusted
rubric.

That leaves three avoidable risks:

- a generated question can drift from its source;
- a correction can be plausible but wrong;
- a fluent answer can be scored against the model's improvised interpretation
  rather than an approved answer frame.

The existing curriculum audit already identifies six broad topics that need
special canonical-question scrutiny. A larger deck should not be activated
until the same authority exists for scoring and feedback.

### The current model evaluation is too small

`api/scripts/effort_cases.json` contains eight cases. It was useful for choosing
the current scoring effort, but it primarily checks the final composite. It does
not establish per-axis agreement, false mechanism-pass rates, correction
factuality, follow-up quality, or reconstruction-versus-parroting behavior on
the coached re-attempt.

## Authoritative behavior

These rules apply to the design and any later implementation.

1. A quick capture is a pending inbox item, not a card.
2. Pending captures are never due, pushed, scored, shown in Sprint, or counted
   in Coverage.
3. Activation requires a trusted answer basis, an answer rubric, and an approved
   canonical question.
4. The answer rubric records the required mechanism, acceptable alternatives,
   one key trade-off, one key failure mode, and a common misconception.
5. User-written debrief text can select a gap but can never be answer authority.
6. A canonical question is approved once and reused.
7. Changing a question after history exists creates a new card and archives the
   old card; it does not rewrite old history.
8. Archive is recoverable and removes a card from due, push, Sprint, and active
   Coverage without deleting sessions.
9. Depth repair runs in Practice mode and leaves `ease_factor`,
   `interval_days`, `repetitions`, and `next_review_at` unchanged.
10. Daily scheduling continues to depend only on mechanism accuracy.
11. Nothing is activated, replaced, archived, or scheduled automatically.
12. Devmax scores remain diagnostic. External coding, system-design, and
    behavioral performance determine readiness.

## Design storyboard

Claude Design created twelve independently readable 390×844 screens in three
groups.

### Capture → ground → act

#### 1. Quick Capture

- Evolves Quick Add into **Capture a gap**.
- One topic field and one optional context line.
- Removes the scheduling choice.
- Primary action: **Save for review**.
- Trust line: `Not added to your review queue yet.`

#### 2. Captured

- Quiet confirmation: **Saved for review.**
- Primary action: **Review now**.
- Secondary action: **Done**.
- States plainly that a source is still required.

#### 3. Card Inbox

- Reached from a compact Today line: `2 captured gaps →`.
- Rows show topic, context, and a text status:
  `Needs source` or `Ready to review`.
- No score, mastery tier, schedule, or false progress treatment.

#### 4. Review Gap · Source

- Shows the captured topic and context.
- Collects a source URL or label and a concise trusted answer basis.
- Keeps the screen short; it is not a large card-authoring form.
- Primary action: **Continue**.
- Quiet destructive exit: **Discard capture**.

#### 5. Review Gap · Rubric

- Five compact editable fields:
  mechanism, acceptable alternative, trade-off, failure mode, misconception.
- Source label remains visible as context.
- Primary action: **Review question**.

#### 6. Review Gap · Question

- Displays one canonical question in Newsreader.
- Offers Edit and a collapsed **Answer rubric** disclosure.
- `Now / Next review` appears only here, after grounding is complete.
- Primary action: **Add to reviews**.

#### 7. Missing Basis

- The flow stops if no trusted basis exists.
- Copy: `This needs a source before it can become a review card.`
- Primary action: **Add source**.
- Secondary action: **Keep in inbox**.
- Activation is not rendered.

### Depth repair

#### 8. Coverage · Actionable Axis

- Preserves the existing Coverage hierarchy and its single axis rollup.
- Only the weakest global axis carries a subtle caret, for example
  `FAILURE MODES 2.3 →`.
- No chart, readiness summary, or new metric.

#### 9. Depth Repair Setup

- Review Sprint variant titled **Depth repair**.
- Selects cards whose depth signal was thin without telling the user which
  exact dimension each card lacked before they answer.
- Categories remain optional.
- Trust line:
  `PRACTICE MODE · WON'T CHANGE YOUR REVIEW SCHEDULE`.
- Primary action: **Start — 6 cards**.

#### 10. Sprint Correction

- Keeps weakest-first order.
- Exposes one row-level action: **Remove from this run**.
- Removal changes only the current run and never modifies the card.
- The remaining count updates immediately.

### Maintenance

#### 11. Card History · Maintenance

- Preserves Card History and opens a quiet maintenance sheet.
- **Archive card** explains that the action is recoverable and keeps sessions.
- **Replace question** explains that it creates a new blank-history card and
  archives the current card.
- No alarming red styling; this is routine upkeep rather than irreversible
  deletion.

#### 12. Study Plan Item · Grounded

- Completed Learn item with a real tappable Source row.
- Concise, observable Done When text.
- `Review 2 source-based suggestions →` appears only because the item has a
  confirmed answer basis.
- The off-phone contrast note specifies that an item without a basis shows no
  proposal action.

## Proposed data model

Names are illustrative; the invariants matter more than the exact schema.

### Pending capture

| Field | Purpose |
|---|---|
| `id` | Stable identity for disk/server retries |
| `topic` | Fast-captured label |
| `context` | Optional one-line observed gap |
| `status` | `pending_source` or `ready_to_review` |
| `source_url` / `source_label` | Provenance supplied during grounding |
| `answer_basis` | Trusted concise authority, never the user's debrief |
| timestamps | Inbox ordering and recovery |

A pending capture is not a row in `cards`. That structural separation prevents
accidental due/push/scheduling participation.

### Active card additions

| Field | Purpose |
|---|---|
| `source_url` / `source_label` | Auditable provenance |
| `answer_basis` | Concise trusted source material |
| `answer_rubric` | Mechanism, alternatives, trade-off, failure mode, misconception |
| `canonical_question` | One approved retrieval, reused across reviews |
| `lifecycle_status` | `active` or `archived` |
| `replaces_card_id` | New card's link to the archived predecessor |
| `replaced_by_card_id` | Archived card's link to the replacement |

The scoring prompt should receive the answer rubric. The user should not see it
before answering in a normal session.

### Study Plan source eligibility

Add an explicit per-item eligibility decision rather than inferring that every
technical item can produce a card:

- `recall_supported = true` requires a trusted answer basis;
- `recall_supported = false` renders no proposal action;
- Practice additionally requires a submitted debrief;
- Learn proposals must also fail closed when authority is absent.

Do not invent a source excerpt for broad mocks. A broad Practice item may remain
valuable plan work without being a valid card source.

## Backend work, in order

### Phase 1 — grounding boundary

1. Add a pending-capture table and capture/inbox endpoints.
2. Add source and answer-rubric fields to cards.
3. Add lifecycle and replacement linkage.
4. Make due, push, Sprint, and Coverage query active cards only.
5. Require an answer basis before converting a capture or proposal into a card.
6. Pass the rubric to question generation, scoring, and re-attempt grading.
7. Preserve the complete-answer transaction and every existing SM-2 invariant.

### Phase 2 — first-party content

1. Add real source links and explicit recall eligibility to the 84-item manifest.
2. Add reviewed answer bases only to eligible items.
3. Author and audit canonical questions and answer rubrics for the 54-card spine.
4. Populate the curated coding-mechanism library after its corresponding lesson,
   not by calendar week.
5. Update only production cards without history; replacements preserve any
   existing history.

### Phase 3 — safe practice and maintenance

1. Add axis-aware practice-set selection without changing scoring or SM-2.
2. Add run-local Sprint removal.
3. Add archive, restore, and replace-question operations.
4. Add iOS states from the storyboard and screenshot-check them at 390×844.

## Evaluation work

Before a model, prompt, or effort change ships, run a source-grounded evaluation
set of roughly 60–100 cases covering:

- all mechanism bands;
- correct mechanism with missing trade-offs or failure modes;
- fluent but confidently wrong answers;
- partial self-correction;
- plausible alternative explanations;
- speech-to-text errors;
- adjacent jargon without a mechanism;
- follow-up anchoring to what the user actually said;
- factual correctness of corrective feedback;
- coached reconstruction versus parroting.

Report at least:

- false mechanism passes;
- false mechanism failures;
- per-axis exact and within-one agreement;
- follow-up-band agreement;
- correction factuality;
- coached-summary integrity.

Complete the currently open `reattempt_effort` sweep as part of this work.

## Acceptance criteria

### Product behavior

- Capturing a gap never creates an active card.
- A pending capture cannot appear in due, push, Sprint, Coverage, or history.
- No activation endpoint can commit without a source, answer basis, rubric, and
  canonical question.
- Learn and Practice proposal paths both fail closed without authority.
- Practice Debrief text never becomes answer authority.
- Archived cards keep every session and disappear from all active selection.
- Replacing a question preserves the old card and starts the new card with blank
  history and default SM-2 state.
- Depth repair sessions write history/mastery as Practice currently does but
  leave all four SM-2 fields unchanged.
- Existing complete-answer and stale-load protections remain intact.

### Design fidelity

- Every state is rendered at 390×844.
- Dynamic Type is addressed before implementation is called accessible.
- Accent remains restricted to its approved uses.
- Score is never communicated by colour alone.
- No new animation is introduced.
- No screen shows readiness percentages, streaks, XP, badges, or celebration.
- Implementation rules stay in the handoff, not repeated as UI prose.

### Content quality

- Every curated canonical question has one scenario and one central mechanism.
- A strong answer fits comfortably under two minutes.
- The answer rubric is source-supported and admits valid alternative framing.
- The correction for every failing evaluation case is factually correct.
- The six broad topics named by the curriculum audit receive explicit review.

## Claude Design audit record

Claude Design created only `Study Quality Improvements v1.dc.html` and preserved
all existing project files.

The design's automated pass found and corrected two issues:

1. Screens without category chips emitted a stray `CATEGORIES` row.
2. The Required Mechanism field clipped its full value and needed three rows.

The final rendered artifact was checked for:

- all twelve named screens present;
- twelve phone frames computed at exactly 390×844;
- no unresolved template placeholders;
- the Practice-mode schedule warning present;
- the missing-source activation guard present;
- recoverable-archive copy present;
- no readiness-score or gamification copy in the design canvas.

Implementation still requires the repository's normal SwiftUI screenshot pass;
this audit validates the proposal, not a built client.

## Explicitly out of scope

- Any SM-2 change or second scheduler.
- Readiness scores, percentages, or exact-day forecasts.
- Automatic card creation, activation, replacement, or retirement.
- Letting a debrief or user-authored answer become its own authority.
- Rewriting a canonical question underneath existing history.
- Hard deletion as the normal card-maintenance flow.
- Full coding execution, full system-design interviews, or behavioral story
  authoring inside the conversational review screen.
- Analytics, third-party telemetry, gamification, or additional motion.
- Transfer-check and contrast/interleaving experiments; revisit these only after
  the grounding and evaluation work proves trustworthy.

## Review questions

The PR is intentionally a decision artifact. Before implementation, resolve:

1. Should answer bases be concise authored text, licensed source excerpts, or
   both?
2. Which first-party Study Plan items are truly eligible for recall proposals?
3. Should pending captures sync to the server immediately, or use the same
   disk-first then cheap-backup pattern as spoken drafts?
4. Should archive/restore live only in Card History, or also be reachable from
   Coverage after the first version?
5. Is `Now / Next review` still the right activation choice, or should every
   newly grounded card enter the next-review path by default?
6. What false mechanism-pass threshold is acceptable before a scoring-model
   change is blocked?
