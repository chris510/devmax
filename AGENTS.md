# Devmax — Project Context (Agent Guide)

This file orients you to the whole project. Read it before touching anything; read
`spec.md` before touching the backend and `design_handoff_devmax_initial/README.md`
before touching the iOS client.

## What this is

A **private, single-user conversational spaced-repetition coach** for technical-interview
prep. A push arrives, you answer a question by voice or text, the model asks at most one
clarifying follow-up if the answer was shaky, then scores recall 0–5 and reschedules the
card with SM-2.

Sessions are **1–3 minutes, used half-awake or in line.** Every decision optimizes for
*speed into a session* and *honesty of signal* — never engagement mechanics. There are no
streaks, no XP, no badges, and no celebration animations. Do not add them.

**Status:** backend and iOS client both built; being prepared for a first deploy. The
backend has 172 passing tests against a real ASGI app, green on both SQLite and Postgres.
The whole stack has since been exercised locally end to end: schema applied to a real
Postgres 17, live Anthropic calls through both prompts, the iOS client compiled and run
against the real API on simulator and on a physical device, and a real APNs push
delivered to that device. Not yet deployed. `docs/RUNBOOK.md` is the path from here to a
push on a phone.

## The two source documents (both authoritative)

| Document | Owns |
|---|---|
| `spec.md` | The backend: schema, endpoints, SM-2, LLM prompt rules, and an explicit out-of-scope list. It says "build exactly what's described here" — take that literally. |
| `design_handoff_devmax_initial/` | The iOS client: final tokens, type, copy, motion, and 29 state screenshots, plus an HTML prototype used as a *design reference, not code to lift*. |
| `docs/STUDY-PLAN-SPEC.md` | Study Plan, end to end. Extends `spec.md` rather than amending it; `design_handoff_study_plan/` is its design source (V3.4 owns behaviour, V3.5 owns presentation). |

**Where the first two disagree, `spec.md` wins.** The handoff's "Network expectations" section was a
sketch written before the backend existed. Every delta is already resolved in one place —
`ios/Devmax/Services/APIClient.swift` — so no view knows about the mismatch. If you find
a new one, resolve it there, not in a screen.

## Load-bearing invariants

Break any of these and the product is subtly wrong in a way tests won't always catch.

- **`missed_count` never touches `ease_factor`.** Missing a review is a *compliance* signal,
  not a *retention* signal. Conflating them means a busy week at work trashes the ease factor
  on topics the user knows cold, and the scheduler then over-drills the wrong things.
- **Scoring returns three axes; the 0–5 composite is derived in code.** The model returns
  `mechanism_accuracy`, `trade_off_awareness`, and `failure_mode_awareness`;
  `llm.derive_composite` turns them into the number the app displays. Never ask the model for
  the composite — that was a source of scores that disagreed with the model's own reasoning.
- **Only `mechanism_accuracy` reaches the scheduler, in two buckets.** Not volunteering the
  failure modes is a depth gap; getting the mechanism wrong is a retention failure, and only
  the second should move the interval. The composite is a display concern — if you find it
  feeding SM-2 again, that's the regression.
- **Composite 2 fails SM-2; composite 3 passes.** Both trigger a follow-up in the app. These
  are two independent thresholds — do not collapse them into one constant.
- **A card's question is generated once and then reused.** `cards.canonical_question` is the
  same retrieval every review; regenerating per session puts every review in the
  weak-transfer regime. The follow-up probe still varies every time — that variation is
  wanted, and is not the same thing.
- **A practice session scores and writes history but never moves the schedule.** `ease_factor`,
  `interval_days`, `repetitions`, and `next_review_at` are the four fields a Review Sprint
  must leave alone. Mastery signal is written normally.
- **Maximum one *scored* follow-up per session, enforced server-side.** The model always
  writes a probe and returns a provisional score; `submit_answer` decides whether to use it
  based on `follow_up_used`. This is structural, not prompt-dependent — keep it that way.
  At most one further *coached re-attempt* may follow, and it never reaches SM-2 or the
  displayed score — see the next invariant.
- **No turn that happens after the model has stated the correct mechanism may reach the
  scheduler.** Turn 3 (`POST /sessions/{id}/reattempt`) is offered only when
  `mechanism_accuracy <= 2`, is user-initiated, and runs *after* the session is already
  `complete` and SM-2 already applied. It writes exactly three `reattempt_*` columns plus
  `card.mastery_summary` — never `score`, never the three axis columns, never the four SM-2
  fields. A post-correction turn measures coached performance, not retention; feeding one to
  `quality_for` would inflate the interval by the ease factor on precisely the cards just
  gotten wrong. `docs/multi-turn-coaching-design.md` is the design record.
- **`delivery_mode: 'desk'` cards never reach `/cards/due` and never trigger a push.** Coding
  problems need a keyboard and an hour, not a two-minute voice session.
- **The complete-answer path is a single transaction.** A partial write — answer saved, SM-2
  not applied — leaves a card permanently stuck. Score *before* writing anything so an LLM
  failure leaves session and card untouched.
- **Never call Claude from `/internal/trigger-review`.** Generating a question for a push
  that may never be opened wastes tokens and latency. Question generation happens on
  engagement, in `POST /cards/{id}/sessions`.
- **The poller carries no schedule — the settings row does.** `trigger-review` is a dumb
  15-minute poll; `windows`, `timezone` and `reviews_per_day` decide everything, so a
  window edited in the app takes effect on the next poll with no redeploy. Consequences
  that are load-bearing: `outside_window` is the *normal* response, at most one push per
  window (`already_pushed`), a card is never offered twice in one day, and a window under
  30 minutes is rejected because it could fall between polls. Do not reintroduce time
  arithmetic into the YAML.
- **`check-missed` must never clear `last_pushed_at`.** It is the only evidence that a
  push went out, and both the daily cap and the per-window guard read it. Which push was
  already counted lives on `missed_counted_at`.
- **Seeding never deletes.** `seed.py` dedupes by topic and only adds, so replacing a
  curriculum needs an explicit `--retire-file <manifest>`. Retirement is by named
  manifest, never by diffing against the current deck — that is what keeps `library/`,
  `modules/` and gap-driven cards out of reach. It hard-deletes and sessions cascade.
- **No Study Plan operation touches a card, a score, a session, a mastery summary,
  or any SM-2 field.** Completion, reopening, replanning, pausing, resuming,
  duplication, activation, and archiving are all plan-only. The single authorised
  exception is a committed card-proposal acceptance, which *creates* cards and
  still modifies none. It is structural rather than disciplinary: nothing is added
  to `cards`, linkage lives in `study_plan_card_links`, and
  `tests/test_study_plan_invariants.py` snapshots every column of both tables
  around every operation.
- **A Study Plan proposal is never applied from client-supplied placements.** The
  server recomputes it from the request's inputs against the current
  `plan.revision`; a stale `base_plan_revision` is a 409. That is what makes "never
  mutate the saved schedule until the user confirms" true rather than intended.
- **Global SM-2 review time is not part of plan capacity, and plan-local retrieval
  is.** Reviews are not owned by the active plan, so an anatomy plan can never be
  made to reflow by how many cards are due. Retrieval came from the guide, so it
  consumes capacity — while still not blocking week advancement.
- **Study Plan forecasts are plan-week precision only.** No response carries a
  field from which a completion *day* could be derived from weekly capacity.
- **Today loads the plan summary concurrently with due cards, and swallows its
  failure.** A Study Plan outage degrades one line to `PLAN · UNAVAILABLE`; it must
  never delay or block a due card.
- **Losing a spoken answer is the worst failure mode in the product.** `PATCH /sessions/{id}/draft`
  must stay cheap, idempotent, and never blocked behind anything slow. On the client, disk is
  the source of truth for instant rehydration; the server draft is the durable backup.
- **A question that failed to load is a *load* failure, never a submit failure.** `openCard`
  failing means no session was created, so nothing was said and nothing was saved: the answer
  control must be off, `sessionID` must be cleared — a leftover one posts the next answer
  against the *previous* card's session — and the only recovery is re-opening the card.
  Reusing the submit-failure strip here claimed a save that never happened and left a live
  mic over a dead session, which is the one thing the invariant above exists to prevent.
  This lives in `Stage.questionFailed`, not a flag beside `stage`: it answers `acceptsAnswer`
  and `footer` for itself, so "no session" and "answerable" cannot both be true.

## Design fidelity rules

Colors, type, spacing, radii, and **copy are final.** When something looks off, read the
prototype source (`design_handoff_devmax_initial/prototype/Devmax.dc.html`) for the
exact value rather than eyeballing the PNG — that is how the real fidelity bugs were found.

- **Motion is exactly four animations**: `wcFade`, `wcSettle`, `wcPulse`, and the 3-dot
  scoring indicator. No springs, no shimmer, no celebratory motion. The loading skeleton is
  static blocks.
- **Mono metadata is not blanket-uppercased.** Only the category tag is uppercased by style;
  every other mono string is written in the case the design shows it in, and several are
  deliberately lowercase (`3 days overdue`, `2 shaky · 1 cold`).
- **Accent `#57b6c2` is for primary buttons, score/status indicators, the recording ring, and
  the caret. Nothing else** in the UI. The app icon's bottom bar uses it too, deliberately —
  that is the only use outside a screen.
- **Score color is never the only signal** — the numeral is always present; the dot is
  decorative reinforcement.
- **Dark mode only.** Light mode is in scope for the product but not designed yet.
- **Three tier vocabularies, none of them interchangeable.** Today's mastery bands
  (`cold/shaky/solid/unrated`) answer "how is today's queue distributed". Coverage's tiers
  (`cold/shaky/developing/solid/untested`, from the last score alone) answer "where does the
  library need cards", and split the middle so a 3 reads differently from a 2. The backend's
  `/cards/overview` tiers share Coverage's *names* but not its definitions — they fold in
  ease factor and lapse timing. Do not merge them; `overview` still has no screen and is
  intentionally unconsumed.
- **The three scoring axes surface in exactly one place**: Coverage's rollup line. Everywhere
  else a session is a single 0–5 numeral. Adding an axis breakdown to the score block or Card
  History is a change to what the product claims to measure, not a display tweak.

## Repo map

```
devmax/
├── spec.md                          # Backend build spec — authoritative
├── design_handoff_devmax_initial/
│   ├── README.md                    # Design handoff — authoritative for iOS
│   ├── prototype/                   # HTML reference (read for exact values, don't lift)
│   └── screenshots/                 # 29 states; the fidelity bar
├── assets/app_icon/                 # Icon kit — `svg/` is the re-export source of truth
├── design_handoff_study_plan/       # Study Plan design. `legacy/` is superseded — don't implement it
├── api/                             # Python 3.12 / FastAPI / SQLModel / Postgres / Railway
│   ├── app/services/scheduler.py    # SM-2 — pure, the highest-value test surface
│   ├── app/services/study_plan_scheduler.py  # The weekly scheduler — pure, likewise
│   ├── app/services/study_plan.py            # Lifecycle, revisions, gate, duplicates
│   ├── app/services/study_plan_import.py     # The gate between the importer and the DB
│   ├── app/services/llm.py          # Question gen + scoring + guide import (Anthropic)
│   ├── app/services/cards.py        # due_label, tier classification, turn assembly
│   ├── app/routers/                 # cards, sessions, devices, settings, study_plan, internal
│   └── app/seed.py                  # --fixtures (design cards) or --file cards.json
├── ios/                             # SwiftUI; `xcodegen generate` makes the gitignored project
│   ├── Devmax/Design/            # Theme, Typography, Motion, ScoreStyle — tokens live here
│   ├── Devmax/Services/          # APIClient, MockAPI, Speech, Speaker, DraftStore,
│   │                             #   StudyPlanMock, GuideDraftStore, StudyReminderService
│   └── Devmax/Screens/           # Today, Conversation, History, Sprint, StudyPlan
└── .github/workflows/               # check-missed cron + manual trigger fallback
```

## Working here

```sh
# Backend
cd api && uv sync && uv run pytest -q && uv run ruff check .   # `.`, not `app tests`
uv run uvicorn app.main:app --reload --port 8083     # 8083 per the ~/dev port contract

# iOS
cd ios && xcodegen generate
xcodebuild -project Devmax.xcodeproj -scheme Devmax \
  -destination 'platform=iOS Simulator,name=iPhone 16e' build
```

Use a **390×844** simulator (iPhone 16e / 14 / 15) — that's the design frame.

### Verifying a UI change

Debug builds run on `MockAPI` fixtures, so every screen works with no server. The prototype's
Tweaks are launch environment variables, so any state — including failure paths — is one
command away:

```sh
SIMCTL_CHILD_WC_ROUTE=submit-failure SIMCTL_CHILD_WC_FAIL_SUBMIT=1 \
  xcrun simctl launch <device> com.christrinh.devmax
```

`simctl` passes an environment variable to the app only when it's prefixed
`SIMCTL_CHILD_`; the `--setenv` flag it once accepted is gone, and today's
`simctl` reads it as the device argument and fails with `Invalid device`.

`WC_ROUTE`: `question` `question-failure` `recording` `processing` `text` `followup` `score` `resume`
`submit-failure` `reattempt` `reattempt-answered` `history` `history-empty` `settings` `add`
`filter` `setup` (alias
`sprint-setup`) `coverage` `coverage-expanded` `recap` `recap-expanded`. An unrecognised
value falls through to the conversation question state rather than erroring, so check the
spelling. Also `WC_LOAD` (`auto|loading|error`),
`WC_RAIL_STYLE` (`dots|chips` — dots ships; chips exists only for the side-by-side) and
boolean `WC_EMPTY` `WC_FAIL_SUBMIT` `WC_FAIL_QUESTION` `WC_FAIL_ADD` `WC_TEXT_FIRST` `WC_TTS`
`WC_SIM_SPEECH` `WC_FAILED_MECHANISM`. Forced failures succeed on the retry, so each path
walks end to end. `WC_FAIL_QUESTION` fails `startSession` — the *load* failure, not the
submit one; the `question-failure` route sets it itself. `WC_FAILED_MECHANISM` forces a failing mechanism score, which is what
makes the coached re-attempt reachable; the two `reattempt` routes set it themselves, so
it only needs passing to see a failed mechanism on some *other* route.

Study Plan adds its own routes, all prefixed `study-plan` and dispatched *before*
the fall-through above, so a misspelled one lands on the plan overview rather than
silently opening a card: `study-plan-overview` `-overview-expanded`
`-overview-future` `-week` `-item` `-capacity` `-build` `-preview`
`-import-failure` `-replan` `-replan-invalid` `-fixed-recovery` `-reopen`
`-reopen-invalid` `-plans` `-no-active` `-complete` `-updates` `-retrieval-audit`
`-estimate-audit` `-dependency-audit` `-card-proposal` `-card-failure`
`-card-existing`. Their flags are `WC_PLAN_NO_ACTIVE` `WC_PLAN_SUMMARY_FAIL`
`WC_PLAN_FAIL_IMPORT` `WC_PLAN_FAIL_ADD_CARD` `WC_PLAN_REPLAN_INVALID`
`WC_PLAN_REOPEN_INVALID` `WC_PLAN_FIXED_RECOVERY`, plus `WC_PLAN_VARIANT`
(`anatomy` | `five-phase`) and `WC_PLAN_CARD_VARIANT` (`none` | `existing` |
`unsupported`).

The plan routes wait on `== .ready`, not `!= .loading` — the initial state is
`.idle`, so the obvious guard falls straight through before the first load starts
and the screenshot catches the wrong expansion state.

`WC_MOCK=0` swaps `MockAPI` for the real API — everything above describes fixtures.
`WC_BASE_URL` overrides where it points, which is how a device build reaches the Mac
(`http://<mac-lan-ip>:8083`, set in the Xcode scheme) without a personal address
landing in committed source. The server must be on `--host 0.0.0.0` for that;
bound to localhost it is unreachable from the phone.

**Screenshot the state and compare it to its PNG in `screenshots/` before calling a UI change
done.** That is the acceptance test.

### Before writing LLM code

Load the `claude-api` skill. Model IDs, structured-output shape, and prompt-caching rules
change faster than this file does — do not write Anthropic calls from memory.

## Known gaps

- **Study Plan is built and green, but its importer is verified by one live run.**
  `docs/CURRICULUM.md` was imported end to end against the real API — 12 weeks, 4
  phases, 72 items, good concise titles, and a capacity check that correctly
  reported the real curriculum needs ~15h/week rather than the 12 it was asked
  for. That run is what surfaced three real bugs (offsets recomputed rather than
  trusted, token-matched subject eligibility, `max_tokens` sized for thinking
  *plus* output). **The post-fix re-run did not happen: the Anthropic account ran
  out of credit.** The fixes are unit-tested; they are not live-tested.
- **The import takes about 11 minutes** at `effort: high` on a 10k-character
  guide. Fine for a once-a-quarter action, but the client needs to expect it, and
  `studyplan_effort` is the lever if that is too slow — `medium` is untested here.
- **Dynamic Type does not scale anywhere in the app.** `WCFont` builds fixed-size
  `UIFont`s with no `UIFontMetrics`, so screenshots at `accessibility-medium` are
  pixel-identical to default. Pre-existing and app-wide; see `docs/DEVIATIONS.md`
  §28.
- **Study Plan has no VoiceOver rotor pass.** Accessible names, headings, hints,
  44px targets, native `disabled`, and status-in-text are all implemented and
  checked in code; nobody has driven it with the screen reader on.


- **The curriculum is lesson-gated.** `api/cards.json` is a 54-card system-design recall
  spine: six cards in each of nine teaching weeks, followed by three weeks of mocks and
  gap-driven additions. Activate one cohort with `--activate-week N` only after its Hello
  Interview source lessons are complete. The retired 126-card plan lives at
  `api/archive/cards-legacy-126.json`; coding is a desk-only reference library, and
  company material is on demand. `docs/CURRICULUM.md` is authoritative for content.
- **Production is live on Railway.** `docs/DEPLOY-CHECKLIST.md` records the current state;
  `docs/RUNBOOK.md` is the procedure for reproducing it.
- **Anthropic and APNs have both been exercised for real, locally.** `services/llm.py` runs
  against the live API (see `scripts/effort_sweep.py`, which is also how `scoring_effort`
  was chosen), and a real APNs push has reached a physical iPhone from a local server using
  a team-scoped key configured for Sandbox & Production. Its ID and the `.p8` live in
  `api/.env`, never in the repo; the `.p8` cannot be re-downloaded, so if it is lost, revoke
  and reissue (Apple allows 2 keys per team).
- **APNs production is still untested.** A TestFlight build gets a *production* token, so
  `APNS_USE_SANDBOX` must flip to `false` and `WC_APS_ENVIRONMENT` to `production` together
  — a mismatch fails silently as `BadDeviceToken`.
- **The pooler has now been tested locally, and the hazard is real.** The whole suite
  passes through PgBouncer 1.25 in `transaction` mode, and so does the live app
  (writes plus 30 concurrent reads, no errors). The underlying incompatibility
  reproduces exactly as documented: raw asyncpg with its statement cache on, through a
  pooler that does not rewrite prepared statements, fails with
  `DuplicatePreparedStatementError: prepared statement "__asyncpg_stmt_1__" already
  exists`. Setting `statement_cache_size=0` fixes it, which is what `db.engine_kwargs`
  already does — so that setting is load-bearing, not defensive.

  **Two caveats before trusting a hosted pooler.** PgBouncer ≥1.21 defaults
  `max_prepared_statements=200` and rewrites prepared statements itself, which masks
  the bug entirely — the failure above only reproduces with it forced to `0`. So a
  green local test does not prove a hosted pooler is safe if it runs the legacy
  setting. And the *test suite* connects with its own engine in `conftest`, bypassing
  `engine_kwargs`, so suite-green through a pooler is weaker evidence than it looks.
  The app path is the one that matters.
- **`alembic revision --autogenerate` is disabled on purpose** (`target_metadata = None`).
  `SQLModel.metadata` diverges from the handwritten migration — the four CHECK constraints,
  every `server_default`, TEXT/SMALLINT vs VARCHAR/INTEGER — so autogenerate would emit a
  revision dropping the constraints. Write revisions by hand and apply them to a real
  Postgres before trusting them.

- **Review Sprint, Coverage and Session Recap are now compiled and screenshot-checked.**
  All five states (`setup`, `coverage`, `coverage-expanded`, `recap`, `recap-expanded`)
  build and were compared against their PNGs on an iPhone 16e. Coverage matched on the
  first render, nine-section order and `MECHANISM 2.7 · TRADE-OFFS 1.4 · FAILURE MODES 2.3`
  included. Four things did not, and are fixed: the category chips sorted alphabetically
  instead of in curriculum order, their count line rendered lowercase where the prototype
  uppercases the whole chip in CSS, `SprintPreviewRow` used an `HStack` that compressed the
  topic instead of `RecapRow`'s `FlowLayout`, and `waitForQuestion` returned early on a
  stale `.result` stage so the recap walk recorded one card instead of six.

  **A stale `DerivedData` directory will silently install an old binary.**
  `find … -name Devmax.app | head -1` is not deterministic when two exist, and the old one
  ran with the sprint routes falling through to Conversation — which reads exactly like an
  app bug. Confirm what you installed before believing a screenshot:
  `xcrun simctl get_app_container <dev> com.christrinh.devmax` and check its mtime.

  Give the async routes room. `coverage` waits on the library, and `recap` walks a whole
  six-card sprint through mock latency — that one needs ~40s before the screen settles, so
  a short `sleep` screenshots a half-finished walk.

Both migrations *have* been applied to a real Postgres: the partial index, the JSONB
column, the `::jsonb` settings seed, and all ten CHECK constraints were verified, along
with 0002's downgrade/re-upgrade round trip and its no-backfill claim (a pre-decomposition
row keeps its blended `score` and leaves every axis null). The whole suite runs against
Postgres via `TEST_DATABASE_URL` as well as SQLite. `api/docker-compose.yml` brings that
Postgres up locally on port 5435. That is how the `timestamptz` bug in `models.py` was
found — see `docs/DEVIATIONS.md` §6.

Where the code and `spec.md` disagree, `docs/DEVIATIONS.md` records why.

## Out of scope — do not build

Everything in `spec.md` §"Out of scope" (accounts, multi-tenancy, admin UI, gamification,
analytics, rate limiting, CORS, API versioning, server-side STT, task queues), plus light
mode, onboarding, and any motion beyond the four specified animations.
