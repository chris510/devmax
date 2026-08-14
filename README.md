# Devmax

A private, single-user conversational spaced-repetition coach for technical-interview prep.
A push arrives, you answer a question by voice or text, the model probes once if the answer
was partial, then scores recall 0–5 and reschedules the card with SM-2. Sessions are 1–3
minutes.

Three screens — **Today**, **Conversation**, **Card History**. No tab bar, no onboarding,
no auth UI, no gamification.

## Layout

```
api/     FastAPI + SQLModel on Postgres, deployed to Railway — built to spec.md
ios/     SwiftUI app — built to design_handoff_devmax_initial/
assets/  App icon kit — svg/ is the re-export source of truth
.github/ Cron workflows that drive the push loop
```

Source documents, both authoritative:

- `spec.md` — the backend build spec (schema, endpoints, SM-2, LLM rules, out-of-scope list).
- `design_handoff_devmax_initial/` — the iOS design handoff: final tokens, type, copy,
  motion, and 18 state screenshots, plus an HTML prototype used as a design reference.

Where the two disagree, `spec.md` wins; the deltas are resolved once in `ios/Devmax/Services/APIClient.swift`.

## Backend

```sh
cd api
cp .env.example .env          # fill in DATABASE_URL, API_KEY, CRON_SECRET, ANTHROPIC_API_KEY
uv sync
uv run alembic upgrade head
uv run python -m app.seed --fixtures        # the three design-prototype cards + one desk card
uv run uvicorn app.main:app --reload --port 8083
```

`--fixtures` seeds exactly the cards every screenshot depicts, so the designs are
reproducible against a real server. For a real queue, activate a curriculum week only
after its source lessons are complete:

```sh
uv run python -m app.seed --file cards.json --activate-week 1 --start-date 2026-08-03
uv run python -m app.seed_study_plan --activate --start-date 2026-08-03
```

These are intentionally separate. The first activates the Week 1 review-card
cohort; the second creates the deterministic 12-week phase/week timeline
without calling an LLM or touching card scheduling.

The 12-week program is documented in `docs/CURRICULUM.md`. The base manifest has
nine six-card teaching cohorts; weeks 10–12 are reserved for mocks and gap-driven
cards.

Never load `--fixtures` into a real database: they carry invented session history and a fake
in-progress draft. The seeder refuses any non-local database without `--force`.

The three access-gating settings have no defaults — the app will not start without
`DATABASE_URL`, `API_KEY`, and `CRON_SECRET`, and refuses known placeholder values.

```sh
uv run pytest        # 107 tests; Anthropic and APNs are mocked, no live calls
uv run ruff check .  # `.`, not `app tests` — the narrower form skips alembic/

# The same suite against real Postgres, which is the only way to exercise JSONB,
# native UUID, timestamptz, and the CHECK constraints that live in the migration.
createdb devmax_test
DATABASE_URL=postgresql+asyncpg://localhost/devmax_test uv run alembic upgrade head
TEST_DATABASE_URL=postgresql+asyncpg://localhost/devmax_test uv run pytest
```

The two shared secrets are independent: client endpoints need `X-API-Key`, `/internal/*`
needs `X-Cron-Secret`.

## iOS

```sh
cd ios
cp Config/Secrets.example.xcconfig Config/Secrets.xcconfig   # paste the server's API_KEY
xcodegen generate
open Devmax.xcodeproj
```

Debug builds run against fixtures (`MockAPI`) so every screen works with no server; release
builds always use the real API. Point a debug build at a live server with `WC_MOCK=0`.

The endpoint and API key come from `Config/*.xcconfig` — Debug at `localhost:8083`, Release
at the Railway host — substituted into `Info.plist`. `Config/Secrets.xcconfig` is gitignored;
without it the app builds and returns a clean 401 rather than falling back to a shared
default. A device build also needs `DEVELOPMENT_TEAM` set in `project.yml`.

### Walking the designed states

The prototype's Tweaks are launch environment variables, so any state — including the
failure paths — can be reached in one command:

```sh
SIMCTL_CHILD_WC_ROUTE=score xcrun simctl launch <device> com.christrinh.devmax
```

`simctl` passes an environment variable to the app only when it's prefixed `SIMCTL_CHILD_`;
the `--setenv` flag it once accepted is gone, and today's `simctl` reads it as the device
argument and fails with `Invalid device`.

| Variable | Values |
|---|---|
| `WC_ROUTE` | `question` `recording` `processing` `text` `followup` `followup-second` `score` `resume` `submit-failure` `history` `history-empty` `settings` `add` `filter` `setup` (alias `sprint-setup`) `coverage` `coverage-expanded` `recap` `recap-expanded` |
| `WC_LOAD` | `auto` `loading` `error` |
| `WC_RAIL_STYLE` | `dots` (ships) `chips` (exists only for the side-by-side) |
| `WC_EMPTY` `WC_FAIL_SUBMIT` `WC_FAIL_ADD` `WC_TEXT_FIRST` `WC_TTS` `WC_SIM_SPEECH` `WC_SECOND_PROBE` | `1` / `0` |

`WC_MOCK=0` swaps `MockAPI` for the real API — everything above describes fixtures.
`WC_BASE_URL` overrides where it points.

Forced failures succeed on the retry, as in the prototype, so each failure path walks end to
end. Use a 390×844 simulator (iPhone 16e / 14 / 15) to match the design frame.

All of these are Debug-only: in a Release build every flag is pinned, so none of them can
change how the app behaves on a real phone.

## Deploying

`docs/RUNBOOK.md` is the ordered path from a clean repo to a push arriving on a phone —
accounts, secrets, the first Railway deploy, seeding, the first real Claude call, the device
build, and the triage steps for when a push doesn't show up.

`docs/DEVIATIONS.md` records where the code intentionally differs from `spec.md`, and why.

`docs/ADAPTIVE-STUDY-MVP.md` documents the source-grounded **Add lesson → Study → Lesson
results** workflow and the safe, local-only second-brain export path.

## Notes

- The app hides the system status bar because the design draws its own 44px mono status row.
- `missed_count` never touches the ease factor — missing a review is a compliance signal,
  not a retention signal.
- Only `conversational` cards enter the due queue and the push loop; `desk` cards are
  tracked and scheduled but never pushed.
