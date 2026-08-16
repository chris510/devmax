# Multi-turn coaching — design

**Status:** accepted and implemented. This document records the decision about
whether a Devmax session may contain more than two turns and under what rule;
the opening sections retain the pre-implementation context, while §5.1 records
the later two-probe amendment.

§4 is the accepted design; §3 records the rejected alternative and why. §7 closes
every question the first draft left open, and §8 is the build order.

---

## 1. The problem: where one follow-up is not enough

Today a session is at most two turns. The user answers, and if the composite
lands in 1–3 the model's already-written probe is issued once; the second answer
is scored and the session ends. `follow_up_used` makes the cap structural rather
than prompt-dependent (`llm.ScoreResult`, `sessions.submit_answer`).

Four cases where that shape leaves value on the table. They are not equally
important, and the difference matters for what gets built.

**1.1 The correction is never re-attempted.** This is the real one. When
`mechanism_accuracy <= 2`, `SCORING_RUBRIC` requires feedback to *state the
correct mechanism directly* — and then the session ends. The user reads the
right answer and closes the app.

`docs/DEVIATIONS.md` §15 already made this argument once, to justify widening
the probe band down to 1: *"corrective feedback alone is recognition, not
recall."* The reasoning holds one step further than the change did. After being
told the mechanism, a re-attempt is the single highest-value retrieval the
session can offer — it is the only turn where the user produces the correct
mechanism themselves. It is also the turn most likely to be skipped forever,
because the card resets to a one-day interval and the *next* session asks the
same canonical question cold, with the correction now a day stale.

**1.2 The second gap is invisible for weeks.** The probe targets "the single
most important gap." An answer with two gaps surfaces one; the other lands in
`mastery_summary` and waits for the next review. On a solid card that is 6+ days,
and after a few passes at ease 2.5, several weeks.

This is a real cost but a *smaller* one than 1.1, because it self-corrects:
`generate_question` reads the mastery summary and targets weak areas, so the gap
is not lost, only deferred. 1.1 does not self-correct.

**1.3 The user cannot ask anything.** A session is strictly interrogative — the
model asks, the user answers, the model grades. Curiosity mid-session ("wait,
why does that matter?") has no channel. This is a genuine gap and it is
explicitly **not** addressed by this proposal; see §6.

**1.4 Desk cards have no interaction model at all.** `delivery_mode: 'desk'`
cards never reach `/cards/due` and never push. Coding problems plausibly want a
multi-turn shape, but that is a different product with a different session
length, and folding it in here would smuggle a second surface into a scheduling
change. Out of scope.

## 2. What bounds any answer

Four constraints, in descending order of how hard they are.

**The session budget is 1–3 minutes, half-awake, possibly in line.** This is the
product. Every turn is a full scoring round trip — `scoring_effort` at
`max_tokens=8000`, empirically ~5s — plus however long the user spends speaking.
A third turn is not a 50% increase on a two-turn session; it is roughly a third
of the entire budget.

**The more turns you allow, the less the final score measures unaided recall.**
This is the constraint that actually decides the design, and it is easy to miss.
SM-2 needs to know whether the mechanism was reconstructed *from memory*.
Today's two-turn shape is safe because the probe is narrow and issued *before*
any correction is given — turn 2 is still retrieval. The moment a turn happens
after the model has stated the correct mechanism, that turn measures coached
performance. Feeding it to `quality_for` would tell the scheduler the card is
solid precisely when it is not, and the error compounds: an inflated pass
multiplies the interval by the ease factor.

So: **scheduling signal and conversation length must be decoupled.** Any
multi-turn design that scores "the final turn" is wrong, and today's rule —
`quality_for(result.mechanism_accuracy)` from the last turn — only works because
there is no post-correction turn to reach.

**The complete path is one transaction, scored before anything is written.** A
partial write leaves a card permanently stuck. Anything added must not create a
second way to half-apply SM-2.

**The schema encodes the cap.** `follow_up_question`, `follow_up_answer`,
`follow_up_used` are scalar columns. They do not generalize to N turns without a
migration to a turns table — which is a feature, not an obstacle (§5.1).

## 3. Option A — a separate coaching mode

Add an opt-in mode alongside Review Sprint: pick a card, converse for N turns,
no cap. Extends the existing `practice` semantics — mastery signal written, the
four SM-2 fields untouched — from a whole session to a whole mode.

**What it gets right.** Every invariant survives untouched; the push loop never
changes; blast radius on failure is zero; it is the only option that could
eventually host desk cards (§1.4) and user-initiated questions (§1.3).

**Why it is rejected.** Two reasons, and the first is decisive.

It does not reach the moment that matters. The value in §1.1 is available for
about ten seconds — immediately after the correction, while the mechanism is
loaded and the user is already in the app. A separate mode requires the user to
notice they got one wrong, remember it later, re-enter through a different door,
and choose that card. That is compliance-dependent behavior, and the entire push
loop exists because this product does not trust compliance. The feature would be
used the way a second gym membership is used.

Second, it is a new product surface the design handoff cannot authorize. The
handoff ships 29 states and none of them are coaching; colors, type, and copy are
final, so a new mode means new design work that no source document owns. Review
Sprint got away with this because it was in the handoff. This would not be.

## 4. Option B, bounded — the recommendation

**Add exactly one turn, after the correction, only when the mechanism was wrong,
only if the user asks for it, and never let it touch the scheduler.**

Concretely:

| Turn | What it is | Feeds SM-2 | Feeds display | Feeds mastery |
|---|---|---|---|---|
| 1 | Unaided answer | — | — | — |
| 2 | Probe, band 1–3, pre-correction (today's follow-up) | **yes** | **yes** | yes |
| 3 | Coached re-attempt, post-correction, opt-in | **no** | no | yes |

The rules that make this safe:

- **The scoring signal freezes at turn 2.** All three axes, the composite, the
  SM-2 application, and the score the app displays are computed exactly as they
  are today, from turns 1–2, and written in the same single transaction. Turn 3
  cannot change any of them. `submit_answer` is untouched.
- **Turn 3 is offered only when `mechanism_accuracy <= 2`** — the band where
  `SCORING_RUBRIC` states the correct mechanism outright, and therefore the only
  band where a re-attempt has something to re-attempt.
- **Turn 3 is user-initiated.** A tap under the score block, not an automatic
  next question. The session *completes* where it completes today; turn 3 is
  strictly opt-in overtime. This is what preserves the 1–3 minute floor: a
  half-awake user's session is byte-for-byte the length it is now.
- **Three turns is a hard server-side cap**, enforced the same structural way
  `follow_up_used` is enforced — a flag checked in code, not a prompt
  instruction.

**Why not more turns.** Every turn past the re-attempt is coached performance
scored against a mechanism the model already supplied. It costs a round trip and
tells the scheduler nothing. If the user needs a third and fourth explanation,
the card is not a spaced-repetition problem that session; it resets to a one-day
interval and comes back tomorrow, which is the correct handling.

**Why this beats Option A.** It reaches the ten-second window, reuses the
Conversation screen's existing thread rendering, and requires no new surface.
Its cost is one invariant rewrite and one migration.

**What it gives up.** §1.2 (the second gap) and §1.3 (user questions) are not
addressed. §1.2 self-corrects through the mastery summary; §1.3 is a real gap
this proposal deliberately declines to fill.

## 5. Consequences

### 5.1 Schema

`follow_up_*` does not generalize; add a parallel set rather than a turns table:

```
sessions.reattempt_answer             text NOT NULL DEFAULT ''
sessions.reattempt_mechanism_accuracy smallint NULL   -- CHECK 0..5
sessions.reattempt_used               bool NOT NULL DEFAULT false
```

**The prompt itself is not stored.** An earlier draft of this section added a
`reattempt_question` column; it turned out to be write-only — a fixed preface plus
the card's own `question_asked`, with no reader anywhere (§7.2 having already ruled
out showing turn 3 in history). Persisting it would also put the same string on both
sides of the wire with only a comment holding the two copies equal, and §7.1 notes
that string is the most likely thing to change. The client composes it for display;
nothing needs to agree.

`reattempt_used`, not `reattempt_offered`: it records that the turn was *taken*, and
`CompleteOut.reattempt_offered` on the wire means something different — that the
client should offer it. Same word, opposite predicates, one file apart. The name
mirrors `follow_up_used`, the other structural cap in this table.

This looks like the uglier of the two options and is the right one. A
`session_turns` table would model turn 3 as "another follow-up," which is exactly
the flattening this design rejects — turn 3 is post-correction and unscored for
scheduling, a different kind of thing. Scalar columns keep that distinction in the
schema and make growing the cap require a migration. **The shape is deliberately
hostile to N turns.** If the cap ever legitimately becomes N, the turns table is
the right move and this decision should be reversed on purpose, not drifted past.

**Amended 2026-08-13 — that condition fired.** The scored follow-up cap became N
(`llm.MAX_SCORED_FOLLOW_UPS = 2`, the model requesting probe #2 through
`needs_more_evidence`), so the reversal above is taken, on purpose:

> ~~`follow_up_*` does not generalize; add a parallel set rather than a turns
> table.~~
>
> **Scored follow-ups are rows: `session_probes(session_id, idx, question,
> answer)`, migration 0015, one row per probe written unanswered when its
> question is issued. `sessions.follow_up_question` / `follow_up_answer` are kept
> and frozen; `follow_up_used` is still written and still means "a probe was
> issued". The `reattempt_*` columns stay scalar.**

The flattening objection in this section is not withdrawn — it was never about
the number of turns. It says turn 3 is post-correction and unscored for
scheduling, *a different kind of thing* from a probe, and modelling it as
"another follow-up" would erase that. `session_probes` cannot absorb it for
exactly that reason: every row in that table is by definition scored and
pre-correction, so the same argument that put the re-attempt in scalar columns is
what keeps it there. What changed is only the premise that the scored side had a
fixed cap of one.

Handwritten migration, as always — `alembic revision --autogenerate` is off.

### 5.2 Scoring and prompts

Turn 3 needs its own call, not a branch inside `score_answer`. It returns only
`mechanism_accuracy` and `mastery_summary` — no composite, no feedback in the
grading sense, no `follow_up_question`. Cheaper on every axis than the scoring
call: smaller schema, lower `max_tokens`, and a strong candidate for a lower
effort setting (re-run `scripts/effort_sweep.py` rather than guessing, the way
`scoring_effort` was chosen).

Its rubric needs one instruction the existing rubrics do not: **the model has
already given the answer, so grade whether it was reconstructed, not whether it
matches.** A verbatim parrot of the feedback is a 1, not a 5.

Two things about the grading turned out to matter more than expected, and both were
found by testing the rubric against the live model rather than by reading it:

- **The 5 band must require the extension to be *correct*.** An earlier draft asked
  only that the answer "connected it to something not in the feedback", which
  name-dropping adjacent jargon satisfies — the same answer scored 2, 3, 2 and 5
  across four runs. Requiring the addition to be correct and load-bearing, and
  stating that length is not evidence, collapsed that to 1, 1, 2.
- **The call must be told the unaided score.** Turn 3 deliberately omits the turn-1/2
  *answers* (grading against the failed attempt invites scoring the delta), but
  omitting the *score* too left the model structurally unable to know it was grading
  a coached turn. It wrote summaries like "solid grasp of WAL ordering…" for a card
  the engineer had just failed cold — and that text is what the next session grades
  against (§7.3). Passing `unaided_mechanism` and requiring the summary to say so
  explicitly fixed it: every summary now reads "coached success, not yet unaided
  recall" or similar. Without this the
axis is meaningless — and since it rewrites `mastery_summary`, a meaningless
value pollutes the next session's *scoring* context (see §7.3 for why it does
not reach question generation).

Turn 3's summary **replaces** the one turn 2 just wrote, consistent with
`SCORING_RUBRIC`'s existing "replaces the card's previous rolling summary". It is
the more recent evidence about the same card, and keeping both would need a
second column with no consumer.

### 5.3 Transaction safety

Turn 3 is a **separate endpoint and a separate write, after the session is
already `complete`.** `POST /sessions/{id}/reattempt`. It must not be able to
re-open a completed session or re-run SM-2 — the session status stays `complete`
throughout, and the write touches only the three `reattempt_*` columns and
`card.mastery_summary` / `card.updated_at`.

This is the property that makes the whole feature cheap: a failed or abandoned
turn 3 loses nothing. The score is already banked, the schedule already applied.

Turn 3 does not reopen the completed session and therefore has no server-draft
resume path. Its in-progress text remains in the client's contextual local draft
store; `PATCH /sessions/{id}/draft` is deliberately a 204 no-op after completion
rather than retaining server state no endpoint can safely rehydrate.

Three guards, not one. `reattempt_used` guards replay; empty text is rejected
outright (it would otherwise spend the single re-attempt and rewrite mastery on a 0);
and the offer **expires once the card is reviewed again** — without that, an old
session's coaching could overwrite a newer review's mastery summary, which is the one
indirect route by which turn 3 could reach a future scheduling decision.

The structural cap still permits only one re-attempt. An exact retransmission of
its already-committed answer returns the stored result so a lost HTTP response does
not trap the client; different text is a 409. That replay is allowed only until a
newer review supersedes the card-side mastery summary. Eligibility is a single
shared predicate (`_reattempt_eligible`) rather than a condition written once to
compute the offer and again, inverted, to honour it — an offer the endpoint would
409 has to be unrepresentable, and DEVIATIONS §15 shows this band does move.

### 5.4 iOS

`AppState.thread` already renders a role-tagged list, so N entries render today —
add `.reattemptQuestion` / `.reattemptAnswer` roles alongside `.followUpQuestion`.
The recording and draft machinery is reusable as-is, including `DraftStore`.

**The score block itself does not change at all.** `scoreBlock` — numeral,
`/ 5 RECALL`, feedback, schedule line — renders exactly as designed. The
re-attempt is a *sibling of the existing secondary text link* in `resultActions`,
which already establishes the pattern: `View history for this card`, at
`TypeRole.secondaryAction` / `Theme.meta` / `Metrics.minTapTarget`, matching
prototype line 438 (13px, `#7c848b`, hover to accent). The new action is a second
link in that same stack, shown only when `mechanism_accuracy <= 2`.

So there is no new component, no new type role, no new color, and no layout
change to a designed block — the affordance already exists and is being used a
second time. That is a materially smaller fidelity risk than §4 assumed.

**Copy**, following the voice the prototype already sets:

- The action link: **`Say it back in your own words`**. Parallel in length and
  register to `View history for this card`, and precise about what is being asked
  — `Try again` would read as re-recording the same answer.
- The turn-3 prompt in the thread: prefaced **`In your words — `**, parallel to
  the follow-up's `One more — `, so it reads as a third kind of turn rather than
  a second probe. Reuses `TypeRole.followUp` (serif 21px) unchanged.

Thread shape: `Q → A → "One more — …" → A → score → "In your words — …" → A`.

Motion budget is unaffected — the re-attempt reuses `wcFade` and the existing
3-dot scoring indicator. No new animation.

### 5.5 Invariants to rewrite

If adopted, `AGENTS.md` and `spec.md` §LLM integration both change:

> ~~Maximum one follow-up per session, enforced server-side.~~
>
> **Maximum one *scored* follow-up per session, enforced server-side. At most one
> further coached re-attempt after the correction, which writes mastery signal and
> never reaches SM-2 or the displayed score.**

That rewrite landed, and its first clause was itself amended on 2026-08-13 when
the scored cap became two — see §5.1 and the current text in `AGENTS.md`. The
re-attempt clause is unchanged.

And a new invariant, which is the load-bearing one:

> **No turn that happens after the model has stated the correct mechanism may
> reach the scheduler.** Post-correction turns measure coached performance, not
> retention. Feeding one to `quality_for` inflates the interval by the ease factor
> precisely on the cards the user just got wrong.

`docs/DEVIATIONS.md` gets an entry recording the §1.1 argument as the extension
of §15 that it is.

## 6. Explicitly not proposed

- **Free-form chat.** No open turn-taking, no "ask me anything about this card."
- **A user→model question channel** (§1.3). Real gap, separate design.
- **Coaching for desk cards** (§1.4).
- **Any change to the probe band, the composite derivation, or `quality_for`.**
- **Any new mode, screen, or entry point.**

## 7. Resolved decisions

The four questions this document opened, and how they land. Nothing here blocks
implementation.

### 7.1 Copy and layout — resolved against an existing pattern

`Say it back in your own words` as a second secondary link in `resultActions`;
`In your words — ` as the thread preface. Full reasoning in §5.4. This was framed
as the one thing no source document authorizes; that turned out to be too
cautious. The handoff does not specify *this* action, but it does specify the
affordance class — a centered 13px muted text link under the score block — and
using it a second time is reuse, not new design. **The remaining risk is copy
only, and copy is cheap to change before it ships.**

### 7.2 Turn 3 writes no `Session` row, and no history indicator — resolved

Card History shows one numeral per session and turn 3 has no composite. A row
with a null score would break that contract; deriving a composite for it would
violate "the composite is derived in code from three axes" by inventing one from
a single axis. The `reattempt_*` columns already live on the turn-1/2 session row
(§5.1), so the data is retained without a second row.

**Nor does history gain a "re-attempted" marker.** `AGENTS.md` is explicit that
adding a signal to Card History is a change to what the product claims to
measure, not a display tweak. The history row means *this is how the card scored
unaided*, and a marker would quietly qualify that.

### 7.3 The next session's opening is unaffected, by construction — resolved

This question was built on a wrong premise on my part, and the correction is
load-bearing enough to state plainly: **`generate_question` is not called again
for a card that already has a canonical question.** `start_session` reuses
`card.canonical_question` verbatim and only generates when it is null. So
`mastery_summary` cannot influence the opening question of any session after the
first — the "targets weak areas" behavior in `QUESTION_RUBRIC` applies to a
card's *first* session only.

The live channel for `mastery_summary` on an established card is
`score_answer`'s context, not question generation. So:

- No explicit mechanism is needed or possible; there is nothing to soften.
- The re-attempt's real downstream effect is on how the *next answer is graded*,
  which is the correct place for it.
- The §5.2 rubric instruction matters more than it first appeared: a polluted
  summary biases scoring directly, with no question-generation step in between to
  dilute it.

This also means the feature cannot cause question drift on established cards,
which removes the main way it could have violated "a card's question is generated
once and then reused."

### 7.4 `reattempt_mechanism_accuracy` is stored, with a written expiry — resolved

Kept, because the one thing worth knowing after a few weeks of real use is
whether the re-attempt produces anything, and that is unanswerable without the
column. But it is stored on the same terms `docs/DEVIATIONS.md` uses for the
`push_log` limitation — an explicit trigger condition rather than an open-ended
"maybe later":

> **Trigger condition: after 20 recorded re-attempts, check whether
> `reattempt_mechanism_accuracy` clusters at the parrot end. If it carries no
> signal, drop the column and the §5.2 rubric branch with it.** A decorative
> column that survives its own review is worse than never adding it.

## 8. Implementation order

Backend first; every step is independently shippable and inert until the last.

1. Migration for the three `reattempt_*` columns (handwritten; apply to real
   Postgres before trusting it).
2. The turn-3 scoring call and its rubric — including the reconstruct-don't-parrot
   instruction — with an `effort_sweep.py` run to pick its effort rather than
   guessing it.
3. `POST /sessions/{id}/reattempt`, with exact committed-retry reconciliation,
   a 409 for changed content, and the assertion that it cannot touch the four
   SM-2 fields. This is the highest-value test surface in the change.
4. iOS: two new thread roles, the second secondary link, and the `<= 2` gate.
5. Documentation: the §5.5 invariant rewrites in `AGENTS.md` and `spec.md`, plus
   a `DEVIATIONS.md` entry recording §1.1 as the extension of §15 that it is.

Steps 1–3 ship with no user-visible change; the feature turns on at step 4.
