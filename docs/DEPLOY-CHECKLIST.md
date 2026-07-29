# Devmax — deploy checklist

**A live state document. Tick things off as they land.** `docs/RUNBOOK.md` is the
detailed procedure; this is the running status, the ordering, and the traps that
cost time. When both disagree, the RUNBOOK is the procedure and this is the state.

Last updated: **2026-07-29**. Live at
`https://devmax-production.up.railway.app` (Railway project `profound-purpose`,
services `devmax` + `Postgres`).

---

## Where things stand

| | Item | State |
|---|---|---|
| ✅ | Backend built, 182 tests green on SQLite **and** Postgres 17 | done |
| ✅ | iOS client built, 15 wire-format tests, all screens screenshot-checked | done |
| ✅ | Schema applied to real Postgres 17; migrations 0001–0003 round-tripped | done |
| ✅ | Anthropic exercised live through both prompts (and the re-attempt rubric) | done |
| ✅ | APNs push delivered to a **physical iPhone** from a local server | done |
| ✅ | Pooler verified against PgBouncer 1.25 `transaction` mode | done |
| ✅ | **Apple Developer paid membership** | **done — longest lead time is behind you** |
| ✅ | Railway project + Postgres (18, not 17 — migrations applied clean) | done |
| ✅ | Six app variables set; migrations 0001–0003 ran via `preDeployCommand` | done |
| ✅ | Two GitHub repo secrets; first green cron run confirmed | done |
| ☐ | Reset the legacy 126-card curriculum; activate week 1 (6 conversational cards) | after curriculum deploy |
| ✅ | First real Claude call — question gen + both scoring turns, SM-2 once | done |
| ✅ | APNs variables — four set, key parses in-container, warning gone | done |
| ✅ | iOS Release build installed on the iPhone; token registered | done |
| ✅ | **First push delivered, tapped, and a session opened** | done |
| ☐ | `reattempt_effort` sweep | independent — can happen any time |

---

## Order, and why it is this order

**The deploy is complete — steps 1 through 7 are all done.** They are kept below as the
record of what was done and why, and as the procedure for doing it again.

Verified end to end on 2026-07-29: a Release build signed with the team provisioning
profile is installed on the iPhone, `aps-environment=development` matches
`APNS_USE_SANDBOX=true`, and a real push was delivered, tapped, and turned into a
session with a live question-generation call — the whole loop, in production.

**To fire a push outside a notification window** (this is how the first one was
tested), widen the window through the app's own API rather than editing the settings
row by hand, then restore it:

```sh
curl -sS -H "X-API-Key: $API_KEY" $B/settings > /tmp/settings_backup.json
# edit the Evening window's `to` to 23:59, PUT it back, fire trigger-review, then:
curl -sS -X PUT -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d @/tmp/settings_backup.json $B/settings
```

**1. Decide the cron question before you merge anything else.**
Scheduled workflows only fire from the default branch, so a merge makes
`trigger-review` and `check-missed` live. Until the backend is deployed **and** both
GitHub secrets exist, they fail — 8 red runs a day, each with an email. Either do
step 2 the same day, or disable both workflows in the repo's Actions tab now and
re-enable at the end of step 4. (RUNBOOK §2.)

**2. Railway: project, Postgres, variables, deploy, domain.** (RUNBOOK §3.)
Root directory is `api`. `preDeployCommand` is `alembic upgrade head`; healthcheck is
`/health`. `DATABASE_URL` must be the **private** `${{Postgres.DATABASE_URL}}`
reference, rewritten to start `postgresql+asyncpg://`.

**3. Two GitHub repo secrets**, once the domain exists: `API_BASE_URL` (with
`https://`) and `CRON_SECRET` (byte-identical to Railway's).

**4. Seed.** (RUNBOOK §4.) See the seed-date trap below — this is the one step where
a wrong value is annoying to undo.

**5. First Claude call by hand**, with deploy logs open. (RUNBOOK §5.) Drive one
session through the follow-up branch rather than discovering a problem when a push
arrives at 07:10.

**6. iOS Release build to the device.** (RUNBOOK §6.) Release is the only
configuration that exercises the `simulateSpeech` gate — tap the mic and confirm it
records *your voice*, not the fixture paragraph.

**7. APNs variables, then the first push.** (RUNBOOK §7.)

---

## Traps that will cost you an hour each

**Seed with `railway ssh`, not `railway run`.** Activate a cohort only after its
source lessons are complete.
```sh
railway ssh --service <api> \
  "python -m app.seed --file cards.json --activate-week 1 --start-date <today>"
```
`railway run` executes locally with Railway's variables injected, so `DATABASE_URL`
points at `postgres.railway.internal` and never connects. `railway ssh` also needs a
registered key first (`railway ssh keys add`) and prompts once to trust the host.
`seed.py` dedupes by topic, so re-running a cohort is idempotent.
`--activate-week` schedules that cohort from the supplied date while preserving its
curriculum week metadata. **Use `--file`, never
`--fixtures`**: the fixtures carry invented history and a fake 14-hour-old draft that
renders a bogus resume banner on a real card.

**`APNS_USE_SANDBOX` and the app's `aps-environment` must flip together.**
Development ↔ `true`, production ↔ `false`. A TestFlight build gets a *production*
token, so `APNS_USE_SANDBOX=false` and `WC_APS_ENVIRONMENT=production` change in the
same sitting. A mismatch fails **silently** as `BadDeviceToken` — no error you'd
notice, just no push.

**The `.p8` cannot be re-downloaded.** It lives in `api/.env` and never in the repo.
If it is lost, revoke and reissue — Apple allows 2 keys per team. `APNS_PRIVATE_KEY`
is multi-line; paste the whole file including the `BEGIN`/`END` lines and do not
collapse the newlines.

**Rotating `API_KEY` bricks the phone** until you install a new build, because it is
compiled into the binary. Only rotate alongside a build and install, never remotely.
`CRON_SECRET` lives in two places (Railway + GitHub) — update both within the same
minute, then run the workflow manually to confirm.

**`simctl --setenv` is dead.** Use the `SIMCTL_CHILD_` prefix:
```sh
SIMCTL_CHILD_WC_MOCK=0 xcrun simctl launch <device> com.christrinh.devmax
```
Today's `simctl` reads `--setenv`'s next argument as the device and fails with
`Invalid device`.

**A stale `DerivedData` can silently install an old binary.** Confirm what you
installed before believing a screenshot:
`xcrun simctl get_app_container <device> com.christrinh.devmax`, then check its mtime.

---

## If the endpoint turns out to be pooled

Railway's direct Postgres URL is **not** pooled, so this probably won't bite — but it
would if you move to Supabase, Neon, or put PgBouncer in front of anything.

The fix is already in `db.engine_kwargs` (`statement_cache_size=0`), and it is
load-bearing rather than defensive. The failure it prevents is loud:

```
asyncpg.exceptions.DuplicatePreparedStatementError:
prepared statement "__asyncpg_stmt_1__" already exists
```

Two things that make a local test falsely green, both worth knowing before you trust
one: PgBouncer ≥1.21 defaults `max_prepared_statements=200` and rewrites prepared
statements itself, hiding the bug unless forced to `0`; and the test suite builds its
own engine in `conftest`, bypassing `engine_kwargs` entirely — so exercise the *app*,
not the suite. Full reproduction recipe in RUNBOOK §3.

---

## Verification, in one block

```sh
B=https://<your-app>.up.railway.app
curl -sS $B/health                                        # {"status":"ok"}
curl -sS -o /dev/null -w '%{http_code}\n' $B/cards/due     # 401
curl -sS -H "X-API-Key: $API_KEY" $B/cards/due             # [] before seeding
curl -sS -X POST -H "X-Cron-Secret: $CRON_SECRET" $B/internal/trigger-review
```

`/health` passing proves asyncpg, greenlet, the private-network address, and the
schema all work in one call. Before APNs variables exist, the deploy log should show
an `APNS_PRIVATE_KEY is unset` warning and no tracebacks.

---

## The one engineering task still open

**`reattempt_effort` is unswept.** It is inherited from `scoring_effort` (`"low"`),
not measured. `scripts/effort_sweep.py` is hardwired to `settings.scoring_effort` and
`run_case`, so it needs a re-attempt path before the value is calibrated. Flagged in
`app/config.py`. Independent of the deploy — it can happen before or after.
