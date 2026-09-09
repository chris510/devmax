# Devmax / Unprompted

A conversational study coach for technical interviews and engineering learning.
The iOS app is named **Unprompted**. This deployment is used personally; the code
supports Sign in with Apple and per-user ownership.

The useful loop is: learn from a trusted source, explain the mechanism later
without help, attempt an unfamiliar design, and capture the specific gap.
Reviews take 1–3 minutes by voice or text, with up to two pre-correction probes.
There are no streaks, XP, badges, or celebration mechanics.

## Current behavior

- Today shows due reviews independently of optional plan and library requests.
- Conversation saves drafts locally and to the server. Close preserves resume;
  Options → End without scoring explicitly ends an attempt before source study.
- Card History preserves scored sessions and ended, unscored partial answers.
  Library includes active cards, archived-card recovery, sources, and captured gaps.
- Learn shows approved authority without a score and delays recall until the
  later of eight hours after exposure and the next local day.
- Study Plan schedules source lessons, Python coding, full designs, and mocks.
  Plan completion does not change card scheduling or create missing cards.
- Review Sprint writes practice history and mastery but leaves SM-2 unchanged.

Production still uses **V1 scoring**: three model axes produce a code-derived
0–5 composite for display; only Accuracy's pass/fail bucket reaches SM-2.
V2 Recall-only scoring remains gated. The adaptive-study pilot separates unscored
formation from delayed Recall for explicitly enrolled sources; nonpilot lesson
imports still use the legacy flow. Neither app scores nor same-sitting performance
certify interview readiness.

## Source documents

Read [AGENTS.md](AGENTS.md) before changing the project. The backend starts with
[spec.md](spec.md), with its current-runtime amendments and linked extensions.
The [initial iOS handoff](design_handoff_devmax_initial/README.md) owns presentation;
Study Plan and pilot handoffs amend their respective flows.

[Curriculum](docs/CURRICULUM.md) owns lesson prerequisites and content release.
Only the six Week 1 base cards are approved. The
[Week 2 review](docs/WEEK-2-CONTENT-REVIEW-2026-09-09.md) is prepared; future drafts
must not be activated merely because their calendar week arrived.

[Runbook](docs/RUNBOOK.md) owns deployment and recovery procedures.
[Deploy checklist](docs/DEPLOY-CHECKLIST.md) records verified production state.
[September audit](docs/PROJECT-AUDIT-2026-09-09.md) and
[release evidence](docs/PRODUCTION-RECOVERY-2026-09-09.md) distinguish tested work
from outstanding device, content, and calibration gates.

## Local development

The backend uses Python 3.12, FastAPI, SQLModel, Alembic, and PostgreSQL. The API
runs on Railway with one replica and an internal 15-minute review poller.

```sh
cd api
cp .env.example .env
# Fill the required local values described in .env.example.
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8083
```

For a disposable local screenshot database, `uv run python -m app.seed --fixtures`
creates synthetic history. Never load fixtures into a real learner account.
For real content, follow the reviewed cohort and prerequisite procedure in
[Curriculum](docs/CURRICULUM.md); use an intentional current start date.

```sh
cd ios
cp Config/Secrets.example.xcconfig Config/Secrets.xcconfig
xcodegen generate
xcodebuild -project Devmax.xcodeproj -scheme Devmax \
  -destination 'platform=iOS Simulator,name=iPhone 16e' test
```

Debug uses MockAPI by default; Release uses the real API. Set `WC_MOCK=0` and
`WC_BASE_URL` for a local API walkthrough. A phone requires the Mac's LAN address
and uvicorn bound to `--host 0.0.0.0`. Follow the runbook for Apple sign-in and
founder migration; the legacy shared API-key path is disabled by default.
Internal operations use a separate `X-Cron-Secret`.

## Verification

Run backend checks **from api/** so Alembic resolves its configuration:

```sh
cd api
uv run pytest -q
uv run ruff check .

# An isolated, already-migrated test database is mandatory: fixtures truncate it.
DATABASE_URL=postgresql+asyncpg://localhost/devmax_test uv run alembic upgrade head
TEST_DATABASE_URL=postgresql+asyncpg://localhost/devmax_test uv run pytest -q
```

CI runs SQLite, PostgreSQL, iOS unit/UI tests, container readiness, backup-job
validation, and icon validation. Live provider calls are separate, reviewed
experiments and never run as part of the normal test suite.

Use an iPhone 16e at 390×844 logical points for design comparisons:

```sh
SIMCTL_CHILD_WC_ROUTE=history-failure SIMCTL_CHILD_WC_TTS=0 \
  xcrun simctl launch <device> com.christrinh.devmax
```

`AGENTS.md` lists fixture routes, including question/submit failures, Study Plan,
pilot states, and recovery. `SIMCTL_CHILD_` is required; `simctl --setenv` is not
supported. Compare screenshots with the applicable handoff before shipping UI.

## Backups and operational limits

The private `database-backup` Railway service runs at 10:00 UTC daily, uses a
SELECT-only role, and verifies each private bucket upload by downloading it and
checking SHA-256. Retention is 31 days with at least three copies. A bucket archive
was restored and the production migration verified on September 9.

See [backup operations](ops/backups/README.md) for recovery. This logical backup
has no PITR window and shares the Railway project's failure boundary. The first
future scheduled run, physical-device production push, provider spending controls,
and live generic-importer rerun remain explicit release checks.
