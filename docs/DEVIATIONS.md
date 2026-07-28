# Deviations from `spec.md`

`AGENTS.md` makes `spec.md` authoritative for the backend, so anything built
differently is recorded here rather than left as a silent disagreement. Each entry
says what the spec asks for, what the code does, and why.

Nothing here is a feature addition. Everything is either a spec bug found by
running the thing, or a mechanism the spec describes that doesn't achieve what the
spec says it's for.

---

## 1. The cron schedule breaks for four months a year

**Spec** (§GitHub Actions cron) prescribes:

```yaml
- cron: '10 14 * * *'   # 07:10 PT
- cron: '0 4 * * *'     # 21:00 PT
```

**We use** `20 15 * * *` and `10 5 * * *`.

GitHub cron is UTC and does not observe DST; the notification windows are local
(`America/Los_Angeles`) and do. `14:10` UTC is 07:10 PDT but **06:10 PST**, and
`04:00` UTC is 21:00 PDT but **20:00 PST**. The windows are `07:10–08:30` and
`21:00–22:30`, so from November to March both daily fires land outside every window
and return `{"sent": false, "reason": "outside_window"}` with HTTP 200 — a green
workflow and no pushes, for four months.

Both windows are wider than the 60-minute shift, so a fixed UTC time exists inside
them under either offset: `15:10–15:30` and `05:00–05:30` UTC. The comment in
`spec.md` about tolerating cron *lateness* doesn't cover a schedule that is an hour
*early*.

The workflows now also read the response body and fail on `outside_window`, so if
a window is ever narrowed below the shift the breakage is loud instead of silent.

## 2. `--weeks-through` cannot prevent the day-one flood

**Spec** (§Seeding): "Add a `--weeks-through N` flag that only loads cards with
`target_week <= N`, so the initial queue isn't flooded with 111 cards on day one."

The flag was implemented as specified, but it cannot do that job: it controls
*which* cards load, not *when* they come due, and every seeded card got
`next_review_at = today`. `--weeks-through 2` still put 38 cards in the queue at
once.

Due dates are now dealt across each card's target week at the configured
`reviews_per_day` rate (one a day for desk cards, which never enter the push loop),
so seeding all 111 puts exactly 2 conversational cards in the queue per day.
`--weeks-through` is kept as a safety valve. Adds `--start-date` so later loads
align to the same week boundaries.

## 3. Prompt caching is configured but never engages

**Spec** (§LLM integration) claims caching the rubric "cuts cached input cost ~90%".

The `cache_control: ephemeral` block is present and correctly shaped, but
`SCORING_RUBRIC` is ~410 tokens and `QUESTION_RUBRIC` ~166, both far below the
1024/2048-token minimum cacheable prefix. `cache_read_input_tokens` will always be
zero.

Left as-is. Padding a rubric to 1024 tokens to save a fraction of a cent at ~4
calls a day would make the prompt worse. The claim in the spec is wrong; the code
is harmless.

## 4. `cards.json` did not exist

**Spec** (§Seeding) describes it as "already generated — 111 cards from the study
plan".

It was not in the repo. Authored to the contract `seed.py` consumes: 111 cards over
six weeks, 84 conversational and 27 desk, split by the spec's category →
`delivery_mode` mapping. See `api/cards.json`.

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

The GitHub Actions crons stay where they are. Railway cron is also UTC, so it would
inherit the identical DST problem from §1, and it requires a service that exits on
completion — a second service to make one HTTP call.

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

`spec.md` §LLM integration says "maximum one follow-up per session". A session can
now hold one more turn after that — but not another follow-up, and not another
score.

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

## Known limitation, not a deviation

`/internal/trigger-review` enforces `reviews_per_day` by counting *cards stamped
today* rather than pushes, and `check-missed` clears `last_pushed_at`, erasing that
evidence. With two cron fires a day and `reviews_per_day: 2` the branch is
unreachable, so this is inert today. Fixing it properly needs a `push_log` table.

**Trigger condition: if the cron moves to hourly, or `reviews_per_day` rises above
the number of daily fires, add `push_log` first.**

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
