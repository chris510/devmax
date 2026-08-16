# Deviations from `spec.md`

`AGENTS.md` makes `spec.md` authoritative for the backend, so anything built
differently is recorded here rather than left as a silent disagreement. Each entry
says what the spec asks for, what the code does, and why.

Nothing here is a feature addition. Everything is either a spec bug found by
running the thing, or a mechanism the spec describes that doesn't achieve what the
spec says it's for.

---

## 1. The cron carries no schedule; the settings row is the schedule

**Spec** (§GitHub Actions cron) prescribes:

```yaml
- cron: '10 14 * * *'   # 07:10 PT
- cron: '0 4 * * *'     # 21:00 PT
```

**We use** a 15-minute in-process poll that carries no notification schedule.

The original GitHub cron was UTC while the notification windows were local
(`America/Los_Angeles`). `14:10` UTC is 07:10 PDT but **06:10 PST**, and `04:00`
UTC is 21:00 PDT but **20:00 PST**. The windows were `07:10–08:30` and
`21:00–22:30`, so from November to March both daily fires would land outside every
window and return `{"sent": false, "reason": "outside_window"}` with HTTP 200 — a
green workflow and no pushes, for four months.

The first fix pinned fixed UTC times inside both windows, later a DST-paired morning
trigger, and made the workflow fail on `outside_window` so a narrowed window broke
loudly. All of that was arithmetic in a YAML file trying to *predict* a value the
database already held, and it had to be redone by hand every time a window moved.

**It is now a 15-minute loop in the single-replica API process, and that loop
encodes nothing else.** `_active_window_start` compares the current *local* time
against the settings row on every call, so DST is not a special case: `ZoneInfo`
resolves the offset for the wall time being tested. Changing a window in the app
takes effect on the next poll with no commit and no redeploy, which is the property
the paired schedule could never have.

The first provider for this dumb poll was GitHub Actions. Its own documentation
says scheduled events may be delayed or dropped, and the first production evening
proved that caveat was load-bearing rather than theoretical: the `7,37` schedule
ran at 20:24 and then 23:09, skipping the entire 21:00–22:30 window. Both green
runs returned `outside_window`; APNs was never called despite four due cards.

Railway cron was tried next. The image built successfully, but Railway failed at
container creation before user code started and produced no runtime logs. The exact
poller command succeeded against production from the same checkout, isolating that
failure to the second external scheduler rather than the API or credentials.
Putting the loop in the already-running API removes both failure surfaces. It calls
the authenticated endpoint over loopback, is disabled by default outside
production, and its configurable interval cannot exceed the 30-minute minimum
accepted window.
`railway.json` pins one replica; increasing that count now requires adding a
distributed lock first. The GitHub workflow remains `workflow_dispatch`-only as an
independent manual fallback.

What that costs, and why it is still the right trade:

- `outside_window` is the overwhelmingly common response (~94 of 96 daily runs).
  It is logged at INFO. Exhausted HTTP retries and `no_devices` are logged as
  errors, but one bad request never kills later polls.
- Polling inside a window would push repeatedly, so the endpoint gained a
  per-window guard (§17) — the poll had to become idempotent within a window.
- `SettingsIn` still rejects windows under 30 minutes. The 15-minute poll no
  longer strictly requires that floor, but keeping it gives each accepted window
  multiple delivery attempts and preserves the shipped settings contract.

## 2. `--weeks-through` cannot prevent the day-one flood

**Spec** (§Seeding): "Add a `--weeks-through N` flag that only loads cards with
`target_week <= N`, so the initial queue isn't flooded with 111 cards on day one."

The flag was implemented as specified, but it cannot do that job: it controls
*which* cards load, not *when* they come due, and every seeded card got
`next_review_at = today`. `--weeks-through 2` still put 38 cards in the queue at
once.

Due dates are dealt across each card's target week at the configured
`reviews_per_day` rate (one a day for desk cards, which never enter the push loop).
The 2026 curriculum revision added `--activate-week N`: it selects one learned
cohort and schedules it from `--start-date`, so lesson completion rather than the
calendar controls activation. `--weeks-through` remains for bulk verification.

## 3. Prompt caching is configured but never engages

**Spec** (§LLM integration) claims caching the rubric "cuts cached input cost ~90%".

The `cache_control: ephemeral` block is present and correctly shaped, but
`SCORING_RUBRIC` is ~410 tokens and `QUESTION_RUBRIC` ~166, both far below the
1024/2048-token minimum cacheable prefix. `cache_read_input_tokens` will always be
zero.

Left as-is for the three session rubrics. Padding one to 1024 tokens to save a
fraction of a cent at ~4 calls a day would make the prompt worse. The claim in
the spec is wrong for those calls; the code is harmless.

**Amended 2026-07-30: caching is now live on exactly one call.** The Study Plan
guide importer's `IMPORT_RUBRIC` is ~4,000 characters and clears the floor on its
own, and Opus 5's minimum cacheable prefix is 512 tokens rather than 1024. So
`_complete` grew a `cache_rubric` flag, `import_guide` sets it, and a measured
live run reported `cache_read_input_tokens: 3417` — the first non-zero cache read
in the product. The three session rubrics still pass `cache_rubric=False` and
still read zero, which is correct for them.

## 4. `cards.json` did not exist

**Spec** (§Seeding) describes it as "already generated — 111 cards from the study
plan".

It was not in the repo. The first authored version grew to 126 cards over six
weeks and is preserved at `api/archive/cards-legacy-126.json`. It was replaced by
the lesson-gated 54-card recall spine in `api/cards.json`; the curriculum rationale
and activation contract live in `docs/CURRICULUM.md`.

Replacing a manifest does not replace a database. `seed.py` only ever added, so the
swap left both decks live, and the retired cards — already overdue — sorted ahead of
the new ones in every push. `--retire-file` is the delete path that was missing;
see `docs/RUNBOOK.md` §Retiring a curriculum.

## 5. `POST /device-tokens` is insert-if-absent, not upsert

**Spec** (§`POST /device-tokens`): "Upsert on token."

With `token` as the primary key and only `kind` and `created_at` alongside it, and
exactly one `kind` in use, the two are behaviourally identical. Left as-is.

## 6. Timestamps are declared `timestamptz` in the models, not just the migration

Not a spec deviation so much as a bug the spec's schema implied and the code got
wrong. `models.py` declared every datetime as a bare `datetime` (naive) while
migration 0001 creates `timestamptz` and the app writes tz-aware values. The
asyncpg dialect casts bind parameters from the *model* type, so it emitted
`$n::TIMESTAMP WITHOUT TIME ZONE` and asyncpg rejected every insert with a
`DataError`. SQLite silently drops `tzinfo`, which is why 45 tests passed over it.

Found by running `alembic upgrade head` against real Postgres for the first time.

## 7. Config fails closed

**Spec** (§Environment variables) lists the variables but not what happens when
they're missing. The implementation defaulted `API_KEY` to `dev-api-key` and
`CRON_SECRET` to `dev-cron-secret`, so a deploy that forgot to set them would
boot healthy on a public hostname authenticated by two strings published in this
repo.

`DATABASE_URL`, `API_KEY`, and `CRON_SECRET` are now required, known placeholders
are refused, and the two secrets must differ — the spec calls for two *independent*
secrets, and only one of them is meant to ship inside the app binary.

## 8. Deployed to Railway, not Fly.io + Neon

**Spec** (§Stack): "**Deployment:** Fly.io, single small instance" and "**Postgres** —
Neon free tier".

Both are now Railway: the API and its Postgres are two services in one project. The
owner already pays for Railway for other apps and wants a single provider; that is a
deployment preference the spec has no stake in, and nothing in the code is
provider-specific. `fly.toml` is gone, `api/railway.json` replaces it, and Fly's
`release_command` maps onto Railway's `preDeployCommand` with the same guarantee —
migrations run to completion before the new container takes traffic.

Two knock-on changes were required, both in `app/db.py`:

- **`sslmode=require` now means encrypt-without-verify**, matching libpq, where only
  `verify-ca` and `verify-full` request certificate validation. The previous code
  mapped `require` to a fully verifying context, which is stricter than the URL asks
  for and fails against any provider fronting Postgres with a self-signed
  certificate — which is what Railway's TCP proxy uses. This is a correctness fix
  that happens to be what unblocks Railway.
- **`*.railway.internal` is treated as a trusted network** and gets no app-level TLS.
  Railway puts every service in an environment on an encrypted WireGuard mesh, and
  the Postgres image's certificate is self-signed, so demanding TLS there would fail
  for no security gain.

Note the deliberate asymmetry: `is_local_database`, which gates
`seed.py --fixtures`, does **not** treat `railway.internal` as local. A private
network address is still a production database. Merging the two predicates would let
the fixtures — invented session history and a fake in-progress draft — into a real
study queue.

`check-missed` stays on GitHub Actions because a delayed run is caught by the next
one and does not gate delivery. The load-bearing `trigger-review` poll moved into
the existing single-replica API after GitHub dropped every scheduled event inside
a real notification window and Railway cron failed before starting its container.
The settings row still performs every local-time decision.

## 9. `alembic` autogenerate is disabled

`env.py` pointed `target_metadata` at `SQLModel.metadata`, which deliberately
diverges from the handwritten migration: the four CHECK constraints, every
`server_default`, and TEXT/SMALLINT vs VARCHAR/INTEGER exist only in the migration.
`alembic revision --autogenerate` would have emitted a revision dropping the
constraints. `target_metadata` is now `None`; write revisions by hand.

## 10. The score is derived in code, not returned by the model

**Spec** (§LLM integration) has the scoring call return a single blended
`score: 0-5`, with the rubric asking the model to weigh three axes in its head.

The schema now asks for the three axes separately — `mechanism_accuracy`,
`trade_off_awareness`, `failure_mode_awareness` — and `derive_composite` computes
the 0–5 number from them. The bands are a direct restatement of the ones the
blended rubric described (0/1 mechanism wrong, 3 mechanism only, 4 + trade-offs,
5 complete), so `last_score`, Card History, and the session score block see no
change in meaning.

Two things needed this. Precise feedback has to know *which* axis was weak to
state the right content, and the scheduler needs mechanism accuracy as a
standalone number (§11). Deriving the composite also removes a real failure mode:
the model used to return a blended score that could disagree with its own stated
reasoning.

## 11. Scheduling gates on mechanism accuracy, in two buckets

**Spec** (§SM-2 implementation) feeds SM-2 the final session score, using its full
0–5 range for both the pass/fail branch and the ease-factor delta.

`quality_for` now derives SM-2's quality from `mechanism_accuracy` alone,
collapsed to two buckets — `again` below 3, `good` at 3 or above. The composite
score no longer reaches the scheduler at all; it is purely a display concern.

**The pass/fail branch is unchanged in behaviour.** `derive_composite` returns 2
or less for exactly the mechanism scores that fail, so "composite < 3" and
"mechanism < 3" select the same sessions. What changes is the ease factor: not
volunteering the failure modes on a topic the user can reconstruct correctly is a
depth gap, not a retention failure, and it used to drag the interval down.

The audit this follows describes ratings in FSRS terms; this codebase schedules
with SM-2, so the two ratings are mapped onto SM-2 qualities — `again` → 2 (the
mildest failing quality) and `good` → 4 (the ease-neutral one, delta 0.0). That
mapping is the one judgement call in the change, and it lives in
`RATING_QUALITY` so it can be re-tuned in one place.

## 12. Four denormalised columns on `cards`

`cards` gains `last_mechanism_accuracy`, `last_trade_off_awareness`,
`last_failure_mode_awareness`, and `last_reviewed_at`, written in the same
transaction as `last_score`.

Coverage's axis rollup is a mean *across cards* of each card's latest value, and
Review Sprint ranks on least-recently-reviewed. Both are one indexed read this
way; deriving them would mean a latest-session-per-card join on every load of two
screens. This mirrors the existing `last_score` denormalisation rather than
introducing a new pattern.

## 13. Practice-mode sessions

Not in `spec.md` at all — Review Sprint is a design-handoff feature that landed
after the spec was written.

`POST /cards/{id}/sessions?practice=true` marks the session. On completion it is
scored, written to the card's history, and updates the card's mastery signal
(`last_score`, the three axes, `mastery_summary`, `last_reviewed_at`) exactly like
a normal session — but `ease_factor`, `interval_days`, `repetitions`, and
`next_review_at` are left untouched, and `CompleteOut.practice` tells the client
to say so instead of quoting a schedule it didn't change.

The split is deliberate: a score earned in a sprint is real signal about what the
user knows, so hiding it from the mastery line would make Today and Coverage
lie. What a sprint must not do is move the review schedule, because the user was
promised it wouldn't.

## 14. Session Recap's practice footnote is conditional

The one place the **design handoff** is not followed literally.

`design_handoff_devmax_initial/README.md` §Screen 5 prints
`PRACTICE MODE · SCORES SAVED TO HISTORY, SCHEDULE UNCHANGED` as a fixed footnote,
and also routes *any* multi-card session to Session Recap — including a normal
daily run, which does reschedule every card it touched. Printing it there would
state something false on the one screen whose entire job is to be trusted.

The footnote and the `Run another` action are shown only in practice mode. The
handoff's two sentences disagree with each other; this resolves the disagreement
in favour of not misleading.

---

## 15. The follow-up probe band starts at 1, not 2

`spec.md` §LLM integration says "if the score would be **2 or 3** and
`follow_up_used` is false, return `status: follow_up`". `FOLLOW_UP_LOW` is 1.

The original band treats the probe as a *disambiguator*: a 2 or a 3 is an answer
that might be better than it read, so ask once more before committing a score. A
1 was considered settled — the mechanism was wrong, there is nothing to
disambiguate.

That is right about the scoring and wrong about the learning. A second, narrower
question is also the only retrieval attempt the session offers, and a wrong
mechanism from someone who engaged with the question is exactly the case where a
targeted probe changes what gets encoded. Corrective feedback alone is
recognition, not recall.

**0 stays excluded, and the boundary is the point.** A 0 is no recall at all.
Probing it asks a half-awake user to guess a second time before being told the
answer, which spends the session's scarcest resource — the ~5s scoring round trip
and the user's patience — on the case least likely to produce anything. A 0 still
resets the card to a one-day interval, so it comes back tomorrow regardless.

Nothing downstream changes. The probe still cannot fire twice (`follow_up_used`
is checked first), SM-2 still gates on `mechanism_accuracy` alone, and the
composite is still derived in code. This widens *when* the model's already-written
probe gets used — the model writes one on every call either way, so there is no
extra token cost on answers that don't probe.

`tests/test_llm.py::test_only_shaky_scores_probe` parametrises the whole 0–5
range, so both edges of the band are pinned.

---

## 16. A session may carry a third turn: the coached re-attempt

`spec.md` §LLM integration said "maximum one follow-up per session" (since amended
— see §30). A session can now hold one more turn after that — but not another
follow-up, and not another score.

This is §15's argument carried one step further. §15 widened the probe band down to
1 because *"corrective feedback alone is recognition, not recall"* — a targeted
second question is the only retrieval the session offers, and a wrong mechanism from
someone who engaged is exactly where it changes what gets encoded. That reasoning
does not stop at the probe. When `mechanism_accuracy <= 2`, `SCORING_RUBRIC`
requires feedback to state the correct mechanism outright, and then the session
ends. The user reads the right answer and closes the app, having never once produced
it themselves. The re-attempt is that missing turn.

**The scoring signal freezes at turn 2, and that is the whole design.** Turn 3
happens after the correction has been given, so it measures coached performance, not
retention. `POST /sessions/{id}/reattempt` runs against an already-`complete`
session with SM-2 already applied, and writes exactly three `reattempt_*` columns plus
`card.mastery_summary`. It cannot reach `quality_for`, cannot change `score` or the
three axes, and cannot touch `last_score` or the `last_*` axes — those describe the
unaided attempt, which is what Coverage and the tiers mean by a card's state.

**It is opt-in, and that is what protects the session budget.** The session completes
where it completed before; turn 3 is a tap on a secondary link under the score block,
so a half-awake user's session is exactly the length it was. The cap is three turns,
enforced by `reattempt_used` the same structural way `follow_up_used` enforces the
probe cap.

**The schema is deliberately hostile to a fourth turn.** Three scalar columns rather
than a `session_turns` table: a turns table would model turn 3 as "another
follow-up", which is precisely the flattening this rejects. Growing the cap should
require a migration and a decision, not a row.

**The grader is told the unaided score, and must say the turn was coached.** Turn 3
omits the turn-1/2 answers on purpose — grading against the failed attempt invites
scoring the delta — but the *score* is passed, because without it the model cannot
know it is grading a coached turn and writes summaries that read as unaided mastery.
Since `mastery_summary` is live context for the next `score_answer`, an over-generous
summary here is the one path by which turn 3 could reach a future scheduling
decision. The offer also expires once the card is reviewed again, so a stale session
cannot overwrite a newer review's summary.

`docs/multi-turn-coaching-design.md` is the full design record, including the
rejected alternative (a separate coaching mode) and why.
`tests/test_api.py::test_reattempt_never_touches_sm2_or_the_score` pins the write set.

---

## 17. One push per window, and a push record that survives being counted missed

The frequent poll (§1) breaks two things that a twice-daily cron hid, and both
fixes live in `/internal/trigger-review`.

**A poll is not a push.** Firing every 15 minutes inside an 80-minute window would
send several notifications and empty a `reviews_per_day` of 2 before evening opened.
So `_active_window` returns the matched window's local start instead of a bool, and
the endpoint refuses if any card was pushed at or after it — `already_pushed`. The
guard is per window rather than per day precisely so the evening window still gets
its own push.

**A card is not offered twice in one day.** Without that, the evening window would
re-push the ignored morning card. `due_count` still reports the whole queue — it is
the notification's "N due" and has to agree with `GET /cards/due` — but the
*selection* skips anything stamped today. A side effect is that every push in a day
lands on a distinct card, so the card count equals the push count and the
`reviews_per_day` cap became exact; that is what retired the `push_log` table this
section used to demand.

**`check-missed` stopped erasing the evidence.** It cleared `last_pushed_at` so a
push wasn't counted twice, but that field is the *only* record trigger-review has
that a push went out — clearing it handed the day's budget back, and would have
re-opened a window that had already been satisfied. Migration 0004 adds
`missed_counted_at`, which holds the `last_pushed_at` value already counted, so
`missed_counted_at < last_pushed_at` reads exactly as "this push is still
uncounted".

Migration 0025 adds the parallel `push_resolved_at` watermark for pushes that
were engaged rather than missed. Opening a session or trusted learning after a
push stamps that push's `last_pushed_at`; `check-missed` excludes it on every
later run without clearing delivery evidence or pretending it was counted
missed. Both watermarks name the push they resolve, not the time the resolution
job ran, so a later push naturally becomes eligible again.

**One bug found while doing it.** SQLite's `DATETIME` bind processor keeps a
tz-aware value's wall-clock fields and drops the offset, so the pre-existing
`last_pushed_at >= day_start` comparison bound a Pacific midnight against
UTC-stored timestamps and was off by the offset — correct on Postgres, wrong on
SQLite, and invisible because the `daily_limit` branch had no test. Local
boundaries are now `.astimezone(UTC)`'d before binding, and the read-side
normaliser that `sessions.py` already had was promoted to `deps.as_utc` and shared.

**That fix is at the wrong altitude, deliberately, and should be finished.** There
are now four call sites doing "make SQLite and Postgres agree about tzinfo" by
hand, and a fifth that does *not* — `services/cards.days_since_review` calls
`.astimezone(tz)` on a value that is naive under SQLite, where `.astimezone`
reads it as system local time rather than UTC. The correct fix is to make
`models.TZ_DATETIME` a `TypeDecorator` whose `process_bind_param` returns
`value.astimezone(UTC)` and whose `process_result_value` re-attaches UTC when
naive: a literal compared against a typed column inherits that column's bind
processor, so all four hand-written conversions delete, every `TZ_DATETIME`
column becomes correct by construction, and `days_since_review` is fixed without
being touched. It is *less* code than the status quo. It is not in this change
because it alters bind and result processing for all eight timestamp columns —
including code paths this change never touches — and no Postgres was available to
verify it. Do it on its own, against a real database.

---

## Deviation from the design handoff, not `spec.md`

`design_handoff_devmax_initial/README.md` §Assets says: *"None. No images, no
icons, no SVG."* Every screen still honors that — the launch screen is a bare
`#0D0F11` and no Swift file loads an image. But an iOS app needs an icon and the
handoff never specified one, so it came from a later kit, now in
`assets/app_icon/`. That kit's README owns the geometry and re-export rules; only
the two wiring decisions live here.

- **Only the dark master is shipped.** `AppIcon.appiconset` carries a single 1024
  in the default slot; iOS derives the dark and tinted home-screen variants
  itself, which flat art with high foreground/background contrast handles well.
  The kit's light pair and monochrome SVG are committed as delivered but unused.
- **The catalog copy is RGB, not RGBA — and stripping alpha is not a blanket
  rule.** App Store Connect rejects a marketing icon carrying an alpha channel,
  so `AppIcon.appiconset/icon-1024.png` is re-encoded to colortype 2, otherwise
  pixel-identical to the kit master. **Re-export and you must strip alpha again —
  but only on the 14 full-bleed squares**: the 10 `devmax-icon-{1024,512,180,167,
  152,120,87,80,60,40}.png` plus the 4 under `png/light/`. Their alpha is
  uniformly 255, so it is dead weight. The other 6 — the 4 `png/android/`
  foregrounds and both `devmax-icon-rounded-*.png` — carry real transparency, and
  flattening those destroys them.

Both rules are now enforced in CI. `scripts/check_icon_alpha.py` reads the PNG
IHDR colortype directly — no dependency, no decode — and asserts the catalog copy
is opaque and that the six real-transparency files still carry alpha. It runs as
the `icons` job in `.github/workflows/ci.yml`. It does *not* check the 14
full-bleed kit squares, which ship RGBA as delivered; stripping their dead alpha
matters only for what gets copied into the catalog.


---

# Study Plan deviations

`docs/STUDY-PLAN-SPEC.md` is authoritative for the feature; the entries below are
where it diverges from the design handoff in `design_handoff_study_plan/`, and
why. Nothing here weakens an existing invariant.

## 18. The reopen fixture's arithmetic is not reproducible

**V3.4 §3** says: "Week 4 sits on the 420-min override with 0 spare. Reopening
L4-01 returns 90 min → 510 against 420."

That contradicts the same section's canonical override, which counts L4-01's 90
minutes inside the 360 of completed work consumed by the 420-minute budget. If
completed work consumes capacity — and §3's fully itemised table says it does,
twice — then converting one completed item back to open cannot raise the week's
total. 420 stays 420; what changes is that 90 of those minutes become movable.

The only model that satisfies both figures is counting a reopened item twice
(once as time already spent, once as work to redo). That is arguably true of the
world, but it makes an item display "90 min" while consuming 180, permanently,
and nothing else in the design hints at it.

**We implement the consistent model**: completed work consumes capacity, and
reopening converts pinned minutes into movable ones without adding any. §3's
canonical table is treated as authoritative because it is the one with every row
listed and reconciling totals, and it is what `test_study_plan_scheduler.py`
asserts against.

Every *behaviour* the reopen frames demonstrate is implemented: reopen produces a
proposal, the proposal can be unresolved, Apply is natively disabled when it is,
no date moves until confirmation, and cards and SM-2 are untouched. Only the
illustrative 510 is not reachable, and the app shows the recomputed number.

## 19. `overview_title` is on phases and weeks, not items

V3.5 §8 lists items as gaining the field too. No screen consumes an item's short
title — Week detail and Item detail both show the full one — so the column would
be written and never read. Added to phases and weeks only.

## 20. Items reference their week by foreign key

V3.4 §12 models `plan_item.week_index` as a scalar. A real
`week_id` foreign key means a replan moves one column and cannot leave an item
pointing at a week that no longer exists. Ordering still comes from
`StudyPlanWeek.index`.

## 21. Plan→card linkage is a join table

The handoff does not say where the link lives. Putting it on `cards` would mean
Study Plan writes a column on a card, which is exactly the boundary the feature
is built around. `study_plan_card_links` makes the boundary structural: deleting
a plan removes links and leaves every card, score, session, and SM-2 field
byte-identical, which `test_study_plan_invariants.py` asserts directly.

## 22. A guide-draft table the handoff does not model

`study_plan_guide_drafts` persists the pasted guide, the duration, the capacity,
the mode, the deadline, the raw model response, and every user edit *before* a
plan exists. Without it "Retry a failed preview" would mean re-uploading the
guide and re-making every review decision. The iOS-side `GuideDraftStore` is the
second copy, for the case where the first preview never reached the server.

## 23. `plan.revision` for optimistic concurrency

V3.4 describes stale-proposal conflict behaviour but models no version anchor.
`study_plans.revision` is bumped by every material schedule write, every proposal
carries the `base_plan_revision` it was computed against, and applying a stale
one is a 409.

## 24. Phase order is a ceiling, not only a floor

Rule 3 says "preserve phase order". Implemented as both bounds: work never drifts
past the last week of its own phase. Without the ceiling, phase-2 overflow would
land in phase 4's weeks, the phase structure would be decorative, and the
scheduler could never report that it had run out of room — which is exactly the
state V3.4's STATE 6 exists to show.

Consequently "add one plan week" inserts a week **into the phase that needs it**
and shifts later weeks up, rather than appending to the end of the plan. An
appended week belongs to the last phase, so phase-2 overflow could not use it and
the option would never validate.

## 25. Concise titles may be one word

V3.5 §4 rule 2 says "2–5 words", but its own worked-example table is half
single words: `Databases`, `Coordination`, `Filtration`, `Acid–base`. The
enforced floor is therefore "not empty"; the real constraint is rule 7, which
disallows vague fragments. `Databases` passes and `Systems` does not, which is
the distinction the rule is actually drawing. A 28-character ceiling replaces the
soft "≤22 where possible".

## 26. Source offsets are recomputed, not validated

The importer is asked for character offsets into the guide. A live run against
`docs/CURRICULUM.md` returned 31 of 72 items with offsets pointing at the wrong
span while the excerpts themselves were verbatim — counting characters over a
ten-thousand-character document is the one part of the task the model is bad at.

Rejecting those would have blocked a good import on the flagship guide. So the
excerpt is treated as the source of truth and the offsets are recomputed by
locating it, tolerating whitespace differences. Once located, the excerpt is
re-read from the guide so the stored quote and the stored span are the same text.
An excerpt that appears nowhere in the guide is still a real failure — that one
means the item was invented rather than read.

This is the same principle as §"never trust model arithmetic", applied to the
other thing the model was asked to compute.

## 27. Subject eligibility matches tokens, not exact slugs

The first implementation used an exact allowlist of subject slugs. The live
import returned `senior-backend-interview-prep` and was refused — the flagship
use case, blocked on a slug variant the model had no way to predict. Matching is
now by token, against a technical vocabulary and a non-technical deny list, with
the deny list winning outright. Both keys still have to turn: the importer must
report the subject as supported *and* the slug must pass.

## 28. Dynamic Type does not scale, app-wide

Not a Study Plan deviation but confirmed while verifying one. `WCFont` builds
`UIFont` directly at a fixed point size and wraps it in `Font(...)`; that path has
no `UIFontMetrics` and no `relativeTo:`, so **no screen in the app responds to
Dynamic Type**, including every screen that predates Study Plan. Screenshots at
`accessibility-medium` are pixel-identical to default.

Study Plan is consistent with the rest of the app, and the density budget it was
designed against is a default-type budget. Making the app scale is a typography
change affecting all 29 existing screenshot comparisons and is deliberately not
bundled into this feature.

## 29. First-party resources, mapped cards, and Stretch are additive item metadata

V3.4 models generic imported work and counts Core, Optional, and Recurring rows
inside one weekly capacity. The reviewed first-party curriculum also needs to
name the exact Premium lesson or coding problem, reveal which approved recall
concepts follow that lesson, and offer up to twenty extra hours without turning a
motivated week into permanent overdue debt.

Those concerns are therefore item metadata, not new scheduler lanes:
`resources` opens external work without completing it; `mapped_recall_topics`
resolves existing active cards read-only after completion; and
`stretch_actions` has no status, reminder, progress, forecast, or carry-forward
semantics. The dependable 1,200 minutes remain ordinary scheduled rows and obey
all V3.4 allocation rules. The additional 1,200 minutes are explicitly advisory.

This also keeps the card boundary intact. A mapped topic is not a
`study_plan_card_link`, never creates or activates a card, and cannot make an
unapproved card actionable. External Premium URLs are navigation and provenance,
not trusted answer text.

## 30. Scored follow-ups are rows, and there may be two of them

`spec.md` §LLM integration said "maximum one follow-up per session", and
`sessions` carried exactly one probe in two scalar columns. Both are now amended:
the cap is `llm.MAX_SCORED_FOLLOW_UPS = 2`, and each probe is a `session_probes`
row (`session_id`, `idx`, `question`, `answer`) added by migration 0015. The spec
sections carry the amended text; this entry records why, and what it cost.

**The trigger is a gap in signal, not a wish for more turns.** The band rule
(§15) asks the first probe when the score would be 1–3, and that is a decision
about the *answer*. It has nothing to say about the case where the transcript
still cannot separate a 3 from a 4 after the probe — the model had no way to say
"I cannot score this honestly yet", so it guessed. `needs_more_evidence` is that
channel, and it is a request: at `probes_used == 1` the server grants it, at the
cap it refuses it, and at `probes_used == 0` it is ignored outright so a stray
`true` cannot widen who gets probed. §15 still governs probe #1 unchanged,
including 0 staying excluded.

**The schema reversal was prescribed, not drifted into.**
`docs/multi-turn-coaching-design.md` §5.1 chose scalar columns for the coached
re-attempt and wrote the condition for flipping: "if the cap ever legitimately
becomes N, the turns table is the right move and this decision should be reversed
on purpose". The cap became N. Only the *scored, pre-correction* probes moved —
every row in `session_probes` is scored and pre-correction by definition, which is
the same argument that keeps the `reattempt_*` and `coaching_*` columns scalar.
There is deliberately no `CHECK (idx <= 2)`: the cap is one decision, living in
one named constant, re-checked at the write site in `submit_answer` so a parser
bug cannot extend a session.

**The legacy scalars are frozen, not dropped.** `sessions.follow_up_question` and
`follow_up_answer` keep every historical row's own evidence and give the
downgrade somewhere to put a probe back. Nothing reads or writes them any more —
the replay guard, the resume question, the scoring input, `build_turns`, and the
seed fixtures all read `session_probes`. `follow_up_used` is the exception: it is
still written, and still true, because its meaning ("a scored probe was issued in
this session") did not change when the count moved.

**Downgrading past 0015 loses second probes.** The downgrade copies `idx = 1`
back into the scalars where they are null; there is no second pair of columns for
`idx = 2`, and inventing one would recreate the schema-level cap this migration
exists not to have. No score, axis, feedback, or SM-2 field is touched either
way.

**The V2 contract is amended, not activated.** `docs/SCORING-CONTRACT-V2-SPEC.md`
carries the two-stage truth table and the "at most two additional scoring calls"
budget, and the V2 parser enforces them fail-closed. `scoring_contract_version`
stays 1.
