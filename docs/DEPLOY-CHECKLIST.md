# Devmax — deploy checklist

**A live state document. Tick things off as they land.** `docs/RUNBOOK.md` is the
detailed procedure; this is the running status, the ordering, and the traps that
cost time. When both disagree, the RUNBOOK is the procedure and this is the state.

Last updated: **2026-08-13**. Live at
`https://devmax-production.up.railway.app` (Railway project `devmax`,
services `devmax` + `Postgres`).

---

## Where things stand

| | Item | State |
|---|---|---|
| ✅ | Backend test suite green on SQLite **and** Postgres 17 | done |
| ✅ | iOS client built, 15 wire-format tests, all screens screenshot-checked | done |
| ✅ | Schema applied to real Postgres 17; migrations 0001–0003 round-tripped | done |
| ✅ | Anthropic exercised live through both prompts (and the re-attempt rubric) | done |
| ✅ | APNs push delivered to a **physical iPhone** from a local server | done |
| ✅ | Pooler verified against PgBouncer 1.25 `transaction` mode | done |
| ✅ | **Apple Developer paid membership** | **done — longest lead time is behind you** |
| ✅ | Railway project + Postgres (18, not 17 — migrations applied clean) | done |
| ✅ | Six app variables set; migrations 0001–0003 ran via `preDeployCommand` | done |
| ✅ | Two GitHub repo secrets; manual trigger fallback confirmed | done |
| ✅ | Activate week 1 (6 corrected conversational cards, fresh from 2026-07-31) | done |
| ✅ | **Retire the legacy 126-card deck** — production verified at only the six current cards before the source-audited reset | done |
| ✅ | **Source-audit and reset the curriculum** — 54-card manifest corrected; six empty production cards atomically replaced; settings and device preserved | done |
| ✅ | **Reliable review polling** — 15-minute loop enabled in the single API replica; first authenticated production poll returned the expected `outside_window` | done |
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

**1. Decide the polling question before you merge anything else.**
`check-missed` still runs on GitHub Actions, while production `trigger-review`
polling runs every 15 minutes inside the single API replica. Set
`REVIEW_POLLER_ENABLED=true` only after the backend, APNs credentials, and device
registration are ready. Keep the GitHub trigger workflow as a manual fallback.
(RUNBOOK §2.)

> **This is the step that bit.** The GitHub trigger was first left disabled, then,
> once enabled, its scheduled events were delayed or dropped across an entire live
> notification window. A standalone Railway cron also failed before its container
> started. The in-process loop removes both external scheduling dependencies; do not
> scale the API above one replica without adding a distributed poll lock.

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
configuration that exercises the `useMockAPI` gate — and on any build, tap the mic and
confirm it records *your voice*, not the fixture paragraph.

**7. APNs variables, then the first push.** (RUNBOOK §7.)

---

## Traps that will cost you an hour each

**Loading a curriculum never removes the last one.** `seed.py` dedupes by topic and
only adds, so activating a new deck alongside an old one leaves both — and the old
one, being overdue, wins every push. Retire explicitly:
`--retire-file archive/cards-legacy-126.json --dry-run`, then `--confirm`. It is a
hard delete and sessions cascade. RUNBOOK §Retiring a curriculum.

**Seed with `railway ssh`, not `railway run`.** These are first-deploy bootstrap
commands, not recurring weekly operations.
```sh
railway ssh --service <api> \
  "python -m app.seed --file cards.json --activate-week 1 --start-date <today>"
railway ssh --service <api> \
  "python -m app.seed_study_plan --activate --start-date <monday-of-this-week>"
```
`railway run` executes locally with Railway's variables injected, so `DATABASE_URL`
points at `postgres.railway.internal` and never connects. `railway ssh` also needs a
registered key first (`railway ssh keys add`) and prompts once to trust the host.
`seed.py` dedupes by topic, so re-running a cohort is idempotent.
`--activate-week` schedules that cohort from the supplied date while preserving its
curriculum week metadata. **Use `--file`, never
`--fixtures`**: the fixtures carry invented history and a fake 14-hour-old draft that
renders a bogus resume banner on a real card.

The Study Plan command is a second, independent seed: 12 weeks, four phases,
116 scheduled items, exactly 20 scheduled hours per week, an untracked optional
20-hour stretch menu, no LLM call, and no card or SM-2 writes. A same-version
rerun is a no-op. The one-time version-2/version-3→v4 upgrade preserves the same
plan id but requires the old plan to be pristine; any progress, personalization,
override, or advancement makes that legacy rewrite fail closed rather than
misattach history. Its explicit `--start-date` becomes the corrected start for
that legacy upgrade. The content-version-6 upgrade keeps the same 116 stable
keys, weeks, minutes, and dependency semantics. Its two materially replaced
lessons teach online data migration and quorum-safe replication. The manifest
retains the eight version-5 fresh-work markers and adds both replacements, so a
direct version-4 to version-6 upgrade has the full ten-key protection even
without an intermediate revision. Unfinished items receive the reviewed content
update; a completed item marked
`requires_fresh_completion` is preserved in full so replacement work cannot
receive retroactive credit. Skipped-work debt is also protected by the revision
ledger and survives later marker removal and same-version confirmation runs;
completion and upgrade writes share an item snapshot guard. The
item-detail response carries `plan_revision`, and the current client echoes that
loaded revision on completion. A stale value returns 409, reloads the item, and
requires a new explicit tap. Deploy the backend first: an older client remains
compatible for conventional and generic items but cannot complete protected
fresh-work keys without the revision token.
Completing a mapped Learn item makes its already-owned, grounded cards actionable
through Card History. Missing or unapproved future cards—including the draft AI
foundations overlay—remain `Not ready`; they are never created or activated by
elapsed calendar time.

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

**Scoring Contract V2 is implemented but deliberately inactive.** Keep
`SCORING_CONTRACT_VERSION=1` until the compatible iOS build has been deployed and
the activation checks in RUNBOOK §Scoring Contract V2 activation and rollback
have been completed. Activation changes only that setting; do not combine it
with a model or provider change. Rollback first sets
`OPENAI_V2_SCORING_MODE=off`, then returns the contract setting to `1` in the
same deploy; enabled OpenAI routing deliberately cannot boot against V1. All
versioned history remains intact.

**OpenAI V2 scoring is also implemented but deliberately inactive.** Keep
`OPENAI_V2_SCORING_MODE=off`; do not provision an allowlist or enable live
shadow/primary routing during V2 activation. The separate human-pack, three-run,
100-event shadow, cost, consent, and owner-canary gates are binding in
`docs/OPENAI-V2-SCORING-ROLLOUT.md`. Migrations 0016–0017 may land safely while
dark; they add provider/audit plus nullable material-import and Study Plan
preview claim metadata only. They rewrite no score, guide, preview, status, or
scheduling state; the disposable Postgres test proves the 0016→0017→0016→0017
round trip before deployment.

Before a separately approved shadow stage, predeclare a fresh
`OPENAI_V2_SCORING_SHADOW_STAGE_ID` UUID. Qualification is the immutable,
inclusive ordinal 1–100 export for that exact stage; pending/incomplete intents
or missing terminal rows are not replaceable, and `--event-count` values other
than 100 are diagnostic only. Pass the exact deployed
`OPENAI_V2_SCORING_QUALIFICATION_EXPIRES_AT` to the reporter with
`--expected-qualification-expires-at`; a mismatch or any selected event at or
after that deadline blocks qualification.
