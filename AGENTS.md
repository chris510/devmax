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
backend has 147 passing tests against a real ASGI app, green on both SQLite and Postgres.
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

**Where they disagree, `spec.md` wins.** The handoff's "Network expectations" section was a
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
- **Maximum one follow-up per session, enforced server-side.** The model always writes a
  probe and returns a provisional score; `submit_answer` decides whether to use it based on
  `follow_up_used`. This is structural, not prompt-dependent — keep it that way.
- **`delivery_mode: 'desk'` cards never reach `/cards/due` and never trigger a push.** Coding
  problems need a keyboard and an hour, not a two-minute voice session.
- **The complete-answer path is a single transaction.** A partial write — answer saved, SM-2
  not applied — leaves a card permanently stuck. Score *before* writing anything so an LLM
  failure leaves session and card untouched.
- **Never call Claude from `/internal/trigger-review`.** Generating a question for a push
  that may never be opened wastes tokens and latency. Question generation happens on
  engagement, in `POST /cards/{id}/sessions`.
- **Losing a spoken answer is the worst failure mode in the product.** `PATCH /sessions/{id}/draft`
  must stay cheap, idempotent, and never blocked behind anything slow. On the client, disk is
  the source of truth for instant rehydration; the server draft is the durable backup.

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
  the caret. Nothing else.**
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
├── api/                             # Python 3.12 / FastAPI / SQLModel / Postgres / Railway
│   ├── app/services/scheduler.py    # SM-2 — pure, the highest-value test surface
│   ├── app/services/llm.py          # Question gen + scoring (Anthropic)
│   ├── app/services/cards.py        # due_label, tier classification, turn assembly
│   ├── app/routers/                 # cards, sessions, devices, settings, internal
│   └── app/seed.py                  # --fixtures (design cards) or --file cards.json
├── ios/                             # SwiftUI; `xcodegen generate` makes the gitignored project
│   ├── Devmax/Design/            # Theme, Typography, Motion, ScoreStyle — tokens live here
│   ├── Devmax/Services/          # APIClient, MockAPI, Speech, Speaker, DraftStore
│   └── Devmax/Screens/           # Today, Conversation, History, Sprint (setup/coverage/recap)
└── .github/workflows/               # trigger-review + check-missed cron
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

`WC_ROUTE`: `question` `recording` `text` `followup` `score` `resume` `submit-failure`
`history` `history-empty` `settings` `add` `filter` `setup` `coverage` `coverage-expanded`
`recap` `recap-expanded`. Also `WC_LOAD` (`auto|loading|error`),
`WC_RAIL_STYLE` (`dots|chips` — dots ships; chips exists only for the side-by-side) and
boolean `WC_EMPTY` `WC_FAIL_SUBMIT` `WC_FAIL_ADD` `WC_TEXT_FIRST` `WC_TTS`
`WC_SIM_SPEECH`. Forced failures succeed on the retry, so each path walks end to end.

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

- **`cards.json` is missing.** `spec.md` §Seeding says the 111-card study plan is "already
  generated," but it isn't in the repo. `app/seed.py` implements the documented contract;
  until the file lands, `--fixtures` seeds only the four cards the screenshots depict — three
  of them conversational, so the push rotation exhausts in days.
- **Not yet deployed.** No Railway project exists yet. `docs/RUNBOOK.md` is the ordered path
  from a clean repo to a push arriving on a phone.
- **Anthropic and APNs have both been exercised for real, locally.** `services/llm.py` runs
  against the live API (see `scripts/effort_sweep.py`, which is also how `scoring_effort`
  was chosen), and a real APNs push has reached a physical iPhone from a local server using
  a team-scoped key configured for Sandbox & Production. Its ID and the `.p8` live in
  `api/.env`, never in the repo; the `.p8` cannot be re-downloaded, so if it is lost, revoke
  and reissue (Apple allows 2 keys per team).
- **APNs production is still untested.** A TestFlight build gets a *production* token, so
  `APNS_USE_SANDBOX` must flip to `false` and `WC_APS_ENVIRONMENT` to `production` together
  — a mismatch fails silently as `BadDeviceToken`.
- **The pooler is untested.** The schema is verified against real Postgres, but a hosted
  Postgres fronted by pgbouncer is a known friction point for asyncpg and prepared
  statements. Smoke-test one real connection before trusting a deploy.
- **`alembic revision --autogenerate` is disabled on purpose** (`target_metadata = None`).
  `SQLModel.metadata` diverges from the handwritten migration — the four CHECK constraints,
  every `server_default`, TEXT/SMALLINT vs VARCHAR/INTEGER — so autogenerate would emit a
  revision dropping the constraints. Write revisions by hand and apply them to a real
  Postgres before trusting them.

- **Review Sprint, Coverage and Session Recap have not been compiled or screenshotted.**
  They were written against the prototype and the handoff on a machine with no Xcode, so
  they carry no build and no fidelity check. Before trusting them: `xcodegen generate`,
  build, then walk `WC_ROUTE=setup`, `coverage`, `coverage-expanded`, `recap`,
  `recap-expanded` and compare each against its PNG. The Coverage section order and the
  axis rollup were checked against `coverage.png` by hand — the comparator reproduces the
  screenshot's nine-section order exactly, and the `MockAPI` fixtures produce its
  `MECHANISM 2.7 · TRADE-OFFS 1.4 · FAILURE MODES 2.3` — but that is arithmetic, not a
  rendered screen.

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
