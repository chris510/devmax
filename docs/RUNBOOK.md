# Devmax — Deploy and Operations Runbook

Everything needed to get from a clean repo to a push arriving on a phone, and to
diagnose it when one doesn't.

The backend runs in production on **Railway** — both the API and its Postgres, in
project `devmax`. Steps that need your credentials are marked **(you)**. The
production schema, API, first-party plan, Anthropic calls, and APNs delivery have
all been exercised; the local Postgres and PgBouncer verification remains the
reproduction path described in §3.

---

## 0. What you need before starting

| Account | Used for | Notes |
|---|---|---|
| Railway | The API and Postgres | The single API replica also owns the dumb trigger poll. |
| Anthropic | Question generation + scoring | Set a low monthly spend cap; expect cents at ~4 calls/day. |
| Apple Developer | Push notifications | **Paid membership required.** A free personal team cannot carry the Push Notifications entitlement. Longest lead time — start here. |
| GitHub | CI, check-missed, and the manual push fallback | Already have it; two repo secrets to add. |

---

## 1. Secrets

Generate the two long-lived shared secrets fresh and make them different. The
founder cutover also uses one temporary secret; it must differ from both and is
removed immediately after the claim (`app/config.py` enforces all three rules).

```sh
openssl rand -base64 32   # API_KEY
openssl rand -base64 32   # CRON_SECRET
openssl rand -hex 32      # FOUNDER_CLAIM_TOKEN (temporary; xcconfig-safe)
```

| Secret | Where it lives | Also held by |
|---|---|---|
| `DATABASE_URL` | Railway service variable | Reference the Postgres service, don't paste — see §3 |
| `API_KEY` | Railway until compatibility code is removed; ignored once its flag is false | The already-installed legacy build; never a future public iOS build |
| `CRON_SECRET` | Railway API service | GitHub repo secret. **Never** in the app. |
| `FOUNDER_CLAIM_TOKEN` | Railway during founder cutover only | A local claim-capable build only; never commit or ship it publicly |
| `ANTHROPIC_API_KEY` | Railway | — |
| `APNS_KEY_ID` | Railway | — |
| `APNS_TEAM_ID` | Railway | — |
| `APNS_BUNDLE_ID` | Railway | `com.christrinh.devmax` |
| `APNS_PRIVATE_KEY` | Railway | The `.p8` file, offline |
| `API_BASE_URL` | GitHub repo secret | Your Railway public domain |

`APNS_USE_SANDBOX`, `LOG_LEVEL`, and
`AI_CONSENT_REQUIRED_POLICY_VERSION` are ordinary Railway variables, not
secrets. The last one is a release gate: do not advance it as part of an
ordinary code deploy.

### Rotation

- **`CRON_SECRET`** lives in Railway and GitHub. Update Railway, then the GitHub
  secret within the same minute, then run the fallback workflow manually to
  confirm.
- **`API_KEY`** is inside the legacy pre-claim build, so rotating it before the
  bearer cutover bricks that install. Once the founder's Keychain bearer works,
  first disable `LEGACY_API_KEY_AUTH_ENABLED`, then rotate `API_KEY` immediately;
  bearer clients are unaffected and the shipped legacy value can never revive.

---

## 2. Merging starts the GitHub schedule

Scheduled workflows only fire from the default branch, so the merge is what makes
`check-missed` live. Until the backend is deployed and the two GitHub secrets exist
it will fail. `trigger-review.yml` is deliberately manual-only; production delivery
uses the API's own loop configured in §3.

Either do §3 promptly, or disable `check-missed` in the repo's Actions tab and
re-enable it at the end of §3. CI itself will pass on merge.

---

## 3. Railway **(you)**

### Create the project

1. New project → **Deploy from GitHub repo** → this repo.
2. On the service: **Settings → Root Directory → `api`**. The repo is a monorepo
   and `railway.json`, the `Dockerfile`, and `pyproject.toml` all live under `api/`.
   Railway reads `api/railway.json` once the root directory is set.
3. **New → Database → Add PostgreSQL** in the same project.

`api/railway.json` already pins the rest: build from the Dockerfile, `alembic
upgrade head` as the `preDeployCommand`, and `/ready` as the healthcheck.
The deployed code expects the single Alembic head to be exactly `0025`; readiness
fails closed if the migration step did not reach it.

### Enable the trigger-review poll

On the API service, add:

```
REVIEW_POLLER_ENABLED = true
```

`REVIEW_POLL_INTERVAL_SECONDS` defaults to `900` and normally should not be set.
Configuration rejects values over 1800 seconds because the shortest accepted
notification window is 30 minutes.

The poller starts with the API, waits five seconds for Uvicorn to begin accepting
loopback requests, then calls `/internal/trigger-review` every 15 minutes. It is
disabled by default so a local server using `api/.env` cannot send a real push.
`railway.json` pins one replica; do not increase that count without first adding a
distributed poll lock.

### Wire the database

On the API service, add a variable:

```
DATABASE_URL = ${{Postgres.DATABASE_URL}}
```

That reference resolves to the **private** address
(`postgres.railway.internal`), which is what you want: it stays on Railway's
encrypted WireGuard mesh, costs no egress, and skips a public round trip.

Do **not** paste `DATABASE_PUBLIC_URL` here. It works, but it exits to the public
internet and is fronted by a self-signed certificate.

**One rewrite is required:** Railway hands you `postgresql://`, and this app uses
the async driver. The value must start `postgresql+asyncpg://`. Either store it
already rewritten, or set it from the reference and edit the scheme.

### If the endpoint is pooled

Railway's direct Postgres URL is not pooled, so this usually does not apply — but
check before assuming, and check again if you ever move to a pooled provider
(Supabase, Neon, or PgBouncer in front of anything).

`db.engine_kwargs` already sets `statement_cache_size=0` and
`prepared_statement_cache_size=0`, which is the correct and sufficient fix. This
has been verified locally against PgBouncer 1.25 in `transaction` mode: the full
suite passes, and so does the live app under concurrent load.

The failure it prevents is loud and specific, so you will know it if you see it:

```
asyncpg.exceptions.DuplicatePreparedStatementError:
prepared statement "__asyncpg_stmt_1__" already exists
HINT: ... pgbouncer with pool_mode set to "transaction" ...
```

If that appears in the logs, `DATABASE_URL` is reaching a transaction-mode pooler
*and* one of those two settings is not taking effect — check that the URL still
goes through `engine_kwargs` rather than being handed to `create_async_engine`
directly.

To reproduce the whole thing locally before trusting a hosted pooler:

```sh
docker run -d --name pgb --network devmax-api_default -p 6432:5432 \
  -e DB_HOST=postgres -e DB_PORT=5432 -e DB_USER=postgres -e DB_PASSWORD=postgres \
  -e POOL_MODE=transaction -e AUTH_TYPE=scram-sha-256 \
  -e MAX_PREPARED_STATEMENTS=0 \
  edoburu/pgbouncer:latest
```

`MAX_PREPARED_STATEMENTS=0` is the important flag — PgBouncer ≥1.21 defaults it to
200 and rewrites prepared statements itself, which hides the bug completely. Point
`DATABASE_URL` at port 6432 and exercise the app, not just the test suite: the
suite builds its own engine in `conftest` and never calls `engine_kwargs`.

### Set the remaining variables

```
API_KEY            = <openssl rand -base64 32>
CRON_SECRET        = <a different openssl rand -base64 32>
FOUNDER_CLAIM_TOKEN = <a third, temporary openssl rand -hex 32 value>
# Explicitly on only so the already-installed private build survives this deploy.
LEGACY_API_KEY_AUTH_ENABLED = true
ANTHROPIC_API_KEY  = <your key>
# A code deploy may understand v2, but production still requires the installed client.
AI_CONSENT_REQUIRED_POLICY_VERSION = anthropic-2026-08-12-v1
AI_CONSENT_ENFORCEMENT_ENABLED = true
APNS_USE_SANDBOX   = true
LOG_LEVEL          = INFO
```

`AI_CONSENT_ENFORCEMENT_ENABLED` is required rather than defaulted. Set it
explicitly on every environment; production should remain `true` after the
consent migration and client cutover have been verified.

APNs secrets come later, in §6 — the app boots fine without them and logs a warning.

### Deploy, then expose it

Deploy. The `preDeployCommand` runs `alembic upgrade head` against Postgres in a
separate step **before** the new container takes traffic. That is the migration's
first contact with a real database in this project; it creates the schema *and*
seeds the settings row that `/internal/trigger-review` reads on its first query.

Then **Settings → Networking → Generate Domain** to get the public hostname, and add
the two GitHub repo secrets: `API_BASE_URL` (that domain, with `https://`) and
`CRON_SECRET` (identical to Railway's).

### Verify

```sh
B=https://<your-app>.up.railway.app
curl -sS $B/live                                                    # {"status":"alive"}
curl -sS $B/ready                 # {"status":"ready","schema_revision":"0025",...}
curl -sS $B/health                # status plus consent-policy metadata
curl -sS -o /dev/null -w '%{http_code}\n' $B/cards/due               # 401
curl -sS -H "X-API-Key: $API_KEY" $B/cards/due                       # []
curl -sS -X POST -H "X-Cron-Secret: $CRON_SECRET" $B/internal/trigger-review
```

`/live` answers only whether the process can serve HTTP and deliberately never
waits on a dependency. `/ready` proves asyncpg, the private-network database
address, and the exact `0025` schema head work together; Railway should gate
traffic on this endpoint. `/health` also checks the database, but its operational
purpose is reporting the required/latest consent policy, minimum iOS build, and
whether consent enforcement is enabled. Do not use `/health` as a migration-head
check. Deploy logs should show the `APNS_PRIVATE_KEY is unset` warning (expected at
this stage) and no tracebacks.

---

## 4. Seeding **(you)**

```sh
railway ssh --service <api-service> \
  "python -m app.seed --file cards.json --activate-week 1 --start-date <today>"
railway ssh --service <api-service> \
  "python -m app.seed_study_plan --activate --start-date <monday-of-this-week>"
```

**It must be `railway ssh`, not `railway run`.** `railway run` executes the process
on your machine with Railway's variables injected — and `DATABASE_URL` resolves to
`postgres.railway.internal`, which only exists inside Railway's network. The seed
has to run *in* the container. First connection prompts once to trust
`ssh.railway.com`, and `railway ssh` needs a registered key
(`railway ssh keys add`) before it will connect at all.

`--activate-week` schedules the selected curated launch cohort as a fresh week
beginning on `--start-date`. Use the day its source lessons were completed.
Re-running the same cohort is idempotent because `seed.py` deduplicates by topic.
This is bootstrap/recovery tooling, not a weekly product workflow.

**Use `--file`, never `--fixtures`.** The fixtures carry invented session history
and a fake 14-hour-old in-progress draft that would render a bogus resume banner on
a real card. `seed.py` refuses a non-local database without `--force`, and that
check treats `postgres.railway.internal` as real — a private address is still
production. Note the guard sits *inside* the `--fixtures` branch
(`app/seed.py:331`): the `--file` path is ungated, so nothing stops a wrong
`--start-date` but the dedupe.

The base manifest contains 54 conversational cards: six in each of nine teaching
weeks. The first-party Study Plan maps every base topic—and the four separate AI
foundation topics—to the exact Learn item that teaches it. An owned, active,
grounded card opens through Card History after the item is complete; a missing or
unapproved future card displays `Not ready` and is not created or activated.
Completing an eligible Practice item can still offer an optional, unscored
debrief; only a submitted debrief plus trusted answer authority can open its
proposal gate. Do not activate later cohorts just because their calendar week
arrived. Coding patterns live in `api/library/` and overlays live in
`api/modules/`; neither is part of the base seed.

The second command bootstraps or upgrades the separate first-party Study Plan
that powers Today's plan line and the phase/week timeline. It makes no LLM call
and never writes cards, sessions, scores, mastery, or SM-2 state. The committed
content-version-6 manifest is 12 weeks, four phases, 116 scheduled items, and
exactly 20 scheduled hours per week, plus an untracked 20-hour stretch menu. It
retains the canonical version-4 lineage key and reviewed version-2/version-3
aliases; the manifest version makes a same-version rerun a no-op and an older
binary still sees the newer lineage and refuses a downgrade. The one-time
legacy→v4 content overhaul upgrades in place only when the old plan is still pristine—
Week 1/revision 1 with no progress, notes,
reminders, overrides, or advancement—and otherwise fails without changing it.
This prevents old item keys from attaching history to unrelated new work. On
that pristine legacy upgrade only, the explicit `--start-date` becomes the
corrected plan start; later version upgrades preserve it. Version 6 retains the
eight version-5 `requires_fresh_completion` keys and adds `V4-W2-L3` and
`V4-W3-L2` because they replace historical lesson content with online migration
and quorum-safe replication. The ten-key union protects a direct version-4 to
version-6 upgrade even when no intermediate revision ledger exists. An
already-complete row is preserved rather than receiving retroactive credit, and
the revision records the skipped key. That debt remains in the revision ledger
if a future release drops its marker, and idempotent reruns keep printing the
warning until fresh content is applied while the item is unfinished. Completion
and upgrade writes use the same plan lock plus an item snapshot guard, so two
simultaneous database writers cannot merge old completion with new content.
Item detail returns `plan_revision`; the current client sends that loaded value
on completion, and the server rejects a stale value before writing. The client
then reloads and requires another explicit tap. Deploy the backend before the
client during a rolling release: older clients may still complete conventional
or generic items, but protected fresh-work keys return 409 until a current
client supplies the revision. `--activate` refuses to displace another
active plan when creating a new one; pause that plan
in the app first if switching is intentional. Use the Monday containing the first
practice day so Week 1 aligns with the timeline's calendar labels.

### AI-foundation review and activation

`modules/ai-foundations.json` deliberately ships with
`grounding_status: "draft_review"`. Do not change all four statuses mechanically
and do not count `modules/ai-application.json` as a substitute. Follow
`docs/AI-SYSTEMS-FOUNDATIONS.md`'s operator checklist, then approve and activate
one mapped cohort only after its Learn item is complete:

```sh
railway ssh --service <api> \
  "python -m app.seed --file modules/ai-foundations.json --activate-week <N> --start-date <lesson-completion-day>"
```

The seed fails closed while any selected entry remains draft or has incomplete
authority. Weeks 1, 2, and 4 are independent cohorts; a later cohort is not
unlocked by elapsed calendar time.

There is no combined wipe-and-seed operation. Card retirement is explicit and
destructive; Study Plan bootstrap is additive and independent.

### Retiring a curriculum

**Loading never deletes.** `seed.py` dedupes by topic and only ever adds, so
swapping one curriculum for another leaves both in the database — and since the old
deck's cards are already overdue, they sort ahead of the new ones and every push
draws from the retired material. That is exactly what happened to the legacy
126-card deck; the swap looked like it had failed when in fact the new cards were
seeded and simply outranked.

```sh
railway ssh --service <api> \
  "python -m app.seed --retire-file archive/cards-legacy-126.json --dry-run"
railway ssh --service <api> \
  "python -m app.seed --retire-file archive/cards-legacy-126.json --confirm"
```

`--dry-run` prints the matched topics and the session count and stops; nothing is
deleted without `--confirm`. The manifest is the delete list — retirement never
diffs against the current deck, so `library/`, `modules/`, and gap-driven cards from
Capture activation are structurally out of reach. `archive/` ships in the image
(`.dockerignore` doesn't exclude it), so the path above resolves inside the
container.

This is a **hard delete**, and `sessions.card_id` cascades: the retired cards' answer
history goes with them. A test pins that the legacy manifest shares no topic with any
live deck — if that ever fails, stop, because the prune would take a live card.

Afterwards, confirm the queue actually changed:

```sh
curl -sS -H "X-API-Key: $API_KEY" $B/cards | jq length
curl -sS -H "X-API-Key: $API_KEY" $B/cards/due | jq -r '.[].topic'
```

Due dates are staggered so exactly `reviews_per_day` conversational cards come due
per day. Check it:

```sql
SELECT next_review_at, count(*) FILTER (WHERE delivery_mode='conversational')
FROM cards GROUP BY 1 ORDER BY 1 LIMIT 7;
```

---

## 5. First real Claude call **(you)**

`app/services/llm.py` has never executed against the real API. Drive one session by
hand with the deploy logs open, rather than discovering a problem when a push arrives.

```sh
CARD=$(curl -sS -H "X-API-Key: $API_KEY" $B/cards/due | jq -r '.[0].id')
SESSION=$(curl -sS -X POST -H "X-API-Key: $API_KEY" $B/cards/$CARD/sessions)
echo "$SESSION"                       # a concrete scenario, not a definition prompt
SID=$(echo "$SESSION" | jq -r .session_id)

# A deliberately partial answer, to exercise the follow-up branch.
curl -sS -X POST -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"text":"something about a ring of hashes, not sure beyond that"}' \
  $B/sessions/$SID/answers                                  # -> status: follow_up

# A thin answer to probe 1: enough to be scored, not enough to separate adjacent
# scores. The model may answer `needs_more_evidence` here, in which case this
# returns a second `follow_up` prefaced "Last one — ". Either outcome is correct;
# the cap is two, so the turn after a second probe always completes.
curl -sS -X POST -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"text":"virtual nodes help spread things out, I think"}' \
  $B/sessions/$SID/answers                  # -> status: follow_up OR complete
curl -sS -X POST -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"text":"virtual nodes spread each server over many ring positions"}' \
  $B/sessions/$SID/answers                  # -> status: complete (409 if already)
```

If a second probe was issued, confirm it was recorded as a row rather than
overwriting the first:

```sql
select idx, left(question, 40), left(answer, 40)
  from session_probes where session_id = '<SID>' order by idx;
```

Logs should show `llm model=... ms=... in=... out=...`. Confirm SM-2 applied exactly
once:

```sql
SELECT ease_factor, interval_days, repetitions, next_review_at FROM cards WHERE id='...';
```

If the model IDs 404, they're config values: set `SCORING_MODEL` / `QUESTION_MODEL`
as Railway variables. Load the `claude-api` skill before changing them.

---

## 6. iOS build **(you, on a Mac)**

```sh
cd ios
cp Config/Secrets.example.xcconfig Config/Secrets.xcconfig
```

Keep `WC_API_KEY` empty. It exists only for the already-installed private
compatibility build and must not enter a new Debug, TestFlight, or App Store
binary; all new builds persist bearer/refresh credentials in Keychain.

Set `WC_BASE_URL` in `Config/Release.xcconfig` to your Railway domain, and
`DEVELOPMENT_TEAM` in `project.yml` to your Team ID, then:

```sh
xcodegen generate
xcodebuild -project Devmax.xcodeproj -scheme Devmax \
  -destination 'platform=iOS Simulator,name=iPhone 16e' test
```

Check against the live server before going to a device:

```sh
SIMCTL_CHILD_WC_MOCK=0 xcrun simctl launch <device> com.christrinh.devmax
```

`simctl` only forwards a variable to the app when it is prefixed `SIMCTL_CHILD_`.
The `--setenv` flag it once accepted is gone — today's `simctl` reads the next
argument as the device and fails with `Invalid device`.

Today should load real cards, and **Card History must render non-blank** — that's
the proof for the date-decoding fix.

### One-time founder Apple claim

Before opening public signup, configure all five Sign in with Apple variables
from `api/.env.example` plus the temporary `FOUNDER_CLAIM_TOKEN` above. Use the
local claim-capable build to send its fresh Apple identity token, one-time
authorization code, and nonce to `POST /auth/founder/apple-claim` with
`X-Founder-Claim-Token`. `X-API-Key` alone must return 401.

Create the uncommitted direct-device override with
`cp Config/FounderMigrationSecrets.example.xcconfig Config/FounderMigrationSecrets.xcconfig`,
paste only `FOUNDER_CLAIM_TOKEN`, and supply that file to the controlled Debug
device build:

```sh
xcodegen generate
xcodebuild -project Devmax.xcodeproj -scheme DevmaxFounderMigration \
  -destination 'id=<physical-device-identifier>' build
```

The migration scheme's Run action uses the isolated `FounderMigration`
configuration; its Archive action deliberately uses `Release`, which force-clears
the claim token. Install the Run product over the existing app—do not uninstall
first—then delete the local secret file immediately after verification. Release
configuration force-clears both bootstrap secrets, so a TestFlight/App Store
archive cannot carry either one.

The successful response is a normal Devmax bearer/refresh pair. Confirm the app
stored it in Keychain, `/auth/me` returns the fixed founder ID, and Today/History/
Study Plan still show the existing data. Only then delete `FOUNDER_CLAIM_TOKEN`
from Railway, set `LEGACY_API_KEY_AUTH_ENABLED=false`, replace `API_KEY` with a
new random value that has never shipped in a binary, and restart the service.
The rotation ensures an accidental future re-enable cannot revive build 2's
embedded key. The same claim route and an old `X-API-Key` request must both
return 401 while the verified bearer still works. A
fresh Apple proof for the same subject is accepted only while that temporary
token remains configured, so a lost first response is recoverable during this
verification window.

Then build to the device and tap the mic: confirm it records *your voice*, not a
fixture paragraph. Any configuration proves this now — `simulateSpeech` defaults off
wherever there is a real microphone, Debug included — so it no longer has to wait for
a Release build. Still build **Release** before shipping: `useMockAPI` keeps the
`isDebug`-only gate, so Release remains the only configuration that proves it.

---

## 7. First push **(you, strictly in order)**

Add on the Railway service:

```
APNS_KEY_ID      = <key id>
APNS_TEAM_ID     = <team id>
APNS_BUNDLE_ID   = com.christrinh.devmax
APNS_PRIVATE_KEY = <the entire .p8 contents, including the BEGIN/END lines>
```

`APNS_PRIVATE_KEY` is multi-line. Railway's variable editor accepts that directly —
paste the whole file, don't collapse the newlines.

`APNS_USE_SANDBOX` must match the app's `aps-environment`: **development ↔ true**,
production ↔ false. A mismatch fails silently as `BadDeviceToken`.

1. Launch the Release build, grant notification permission.
2. **Confirm a token arrived without printing it:**
   `SELECT COUNT(*) FROM device_tokens;` — zero means registration failed
   (check the device console for `devmax: APNs registration failed`) or the build
   is still in mock mode.
3. Confirm `GET /cards/due` is non-empty.
4. Don't wait for the poll. Widen a window to cover now via `PUT /settings` (at
   least 30 minutes, or the write is rejected 422) and include today's ISO weekday
   in that window's `days`. Omitting `days` deliberately means every day and is
   also valid for this compatibility test. Run the Trigger review workflow manually,
   expect `{"sent": true, "card_id": ..., "due_count": N}` and a banner.
   **Restore the real windows and selected days immediately** — see §Scheduling.
5. Tap the notification → it should deep-link into that card. Answer by voice.
6. Dispatch the workflow a second time while still inside that widened window and
   confirm `{"sent": false, "reason": "already_pushed"}`. That one response is what
   keeps a frequent poll from emptying the day's budget in one window.
7. Confirm an unattended API-process poll inside a window. The loop begins five
   seconds after each deploy and then every 15 minutes; it is not wall-clock aligned.

---

## Scheduling

**The poller carries no notification schedule.** The single-replica API process
polls `/internal/trigger-review` every 15 minutes. The endpoint decides for itself
whether to push.

Everything about *when* lives in the settings row: each window's `days`, local
time range and on/off state, the account `timezone`, and the `reviews_per_day`
daily safety cap. Change a selected day or a window in the app and the next poll
obeys it — no commit, no redeploy, and no DST arithmetic in the poller, because the
comparison happens in the user's local time on the server. That replaced a
hand-maintained UTC approximation of the windows which disagreed with them for
four months a year (`docs/DEVIATIONS.md` §1).

Window `days` are ISO weekday numbers: `1` is Monday through `7` for Sunday. A
missing field means every day so rows and clients from before weekday-aware
windows retain their original behavior. A present list is unique and non-empty;
turn the window off to silence it while preserving those days. For
example, `[1,3,5]` makes that window eligible Monday, Wednesday, and Friday.
Increasing weekly nudge frequency means selecting another day, not increasing
`reviews_per_day`. Two enabled windows may reuse a start time only when their
selected days are disjoint; `PUT /settings` rejects equal starts on a shared day
because those windows would collapse to one idempotency boundary.

The client summary `Up to N reminders per week` is an upper bound. For each ISO
day, count enabled windows selecting it, cap that count at `reviews_per_day`, then
sum the seven capped counts. Do not count only the union of selected days: two
windows on one day can produce two nudges. The due-only check may still make the
delivered count lower than this maximum.

This is only a **nudge schedule**. SM-2 and `next_review_at` remain the review
schedule. A selected day does not make a card due and never guarantees a push;
the endpoint remains quiet unless an eligible conversational card is already due.
Changing days, times, or the daily cap must not rewrite any card schedule field.

Consequences worth knowing:

- **Most runs return `outside_window`.** With the two everyday default windows,
  that is ~85 of 96 runs a day; on an unselected weekday every run does. It is the
  expected steady state and no longer fails the job.
- **At most one push per eligible window.** The endpoint refuses a second push
  inside a selected-day window that already produced one, and returns
  `already_pushed`. The poll takes the account-deletion boundary before its settings
  row, then holds the settings lock through delivery. Concurrent polls for one
  account serialize without inverting deletion's child-row order, while different
  accounts remain parallel.
- **The daily cap remains a backstop.** `reviews_per_day` counts delivered pushes
  on the user's local calendar day across every eligible window. It is not a
  weekly-frequency setting and does not cap the due queue.
- **Due-only is absolute.** Selected weekdays and open windows never create a
  notification when `GET /cards/due` has no eligible conversational card.
- **A window must be at least 30 minutes long.** `PUT /settings` rejects anything
  shorter with a 422. The 15-minute cadence gives every accepted window multiple
  attempts; keeping the 30-minute product constraint preserves that margin.
- **DST gaps and folds remain one window.** A start inside a spring-forward gap
  resolves to the first real local minute; a range wholly inside that gap resumes
  there for its configured wall-clock duration. Both occurrences of a fall-back
  hour share the first occurrence's guard boundary, so neither transition permits
  a second push from the same window.
  There is deliberately no *maximum* — widening a window to cover now is how you
  test a push.

The poll originally lived on GitHub Actions. On its first unattended production
day, GitHub created no run during the entire 21:00–22:30 window: the surrounding
runs landed at 20:24 and 23:09 and both returned `outside_window`. GitHub documents
scheduled events as delayable and droppable, so `trigger-review.yml` is now
`workflow_dispatch`-only.

A Railway cron service was tried next. Its Docker image built, but container
creation failed before the poller started and produced no runtime logs. The exact
command succeeded against production outside Railway cron, isolating the failure
to that provider path. Keep the GitHub workflow as a manual fallback, but do not
restore its `schedule` block while the in-process poller is enabled.

`check-missed` remains a GitHub schedule. A delayed or dropped bookkeeping run is
caught by the next run and cannot prevent a notification from being delivered.

---

## Triage: no push arrived

In order.

1. **Did the API poller run?** Search the `devmax` service logs for
   `trigger-review poll`. Startup logs must include
   `review poller enabled interval_seconds=900`. A 500
   means every relevant enabled notification window in `settings` is unparseable
   (including malformed `days`) — the one configuration fault the endpoint refuses
   to answer quietly, precisely because `outside_window` is otherwise
   indistinguishable from working normally.
2. **`{"sent": false, "reason": ...}`** — each reason is specific. All but the last
   are routine at a 15-minute poll and none of them fail the job:
   - `outside_window` — no enabled window selects the current local ISO weekday
     and contains the current local time. The usual answer is ~85 times on a day
     selected by both default windows, and every time on an unselected day. If it
     is unexpectedly constant, check `GET /settings`: the window's `days`, its
     on/off state, its time range, and a timezone that no longer matches the user.
   - `already_pushed` — this window already produced a push.
   - `already_offered` — the window is free, but every due card already went out
     earlier today. A card is never pushed twice in one day.
   - `nothing_due` — legitimately nothing due.
   - `daily_limit` — already sent `reviews_per_day` pushes today.
   - `no_devices` — nothing was delivered: no registered token, or APNs credentials
     missing. `last_pushed_at` is deliberately *not* stamped in this case, so
     `missed_count` stays honest.
3. **Deploy logs** — `apns rejected token=...` means APNs took the request and
   refused it. Almost always the sandbox/production mismatch.
4. **`SELECT COUNT(*) FROM device_tokens`** — zero means the phone never registered;
   never print bearer-like device tokens into a terminal transcript.
5. **The phone** — Focus mode, notification settings, or permission denied at first
   launch (there is no second prompt; delete and reinstall).

## Triage: the app can't reach Postgres

- **`sslmode` / `channel_binding` errors from asyncpg** — shouldn't happen; `db.py`
  strips both. If you see one, something is passing the URL to a raw driver.
- **`self-signed certificate in certificate chain`** — you're on
  `DATABASE_PUBLIC_URL` without `?sslmode=require`. Railway's proxy uses a
  self-signed cert, and with no sslmode the app defaults to full verification.
  Either switch to the private `${{Postgres.DATABASE_URL}}` (preferred) or append
  `?sslmode=require`, which means encrypt-without-verify, per libpq.
- **DNS failure on `postgres.railway.internal`** — the private address only resolves
  from inside Railway. Locally, use `DATABASE_PUBLIC_URL` with `?sslmode=require`.
- **`InvalidPasswordError` / driver mismatch** — check the scheme is
  `postgresql+asyncpg://`, not the `postgresql://` Railway gives you.

## Triage: a card is stuck

`POST /sessions/{id}/answers` scores before it writes anything, and the complete
path is one transaction, so an LLM failure leaves the session and card untouched
rather than half-written. A session stuck in `open` or `awaiting_follow_up` is
resumable by design — starting a session on that card in the same scheduled or
practice mode returns the existing one. An opposite-mode start returns
`409 session_mode_conflict`; explicitly `POST /sessions/{id}/abandon` first if
the saved draft should be retained but the answer should not be scored. Abandon
is idempotent, preserves the draft, and never changes the card's schedule.

## Production signals, limits, and recovery

The application emits the signals below, but the repository cannot prove that a
Railway log drain, paging destination, provider billing limit, backup policy, or
restore job is configured. Treat each external control as incomplete until its
current setting and one test notification or restore are recorded in the deploy
checklist.

### Minimum alert set

| Signal | Alert threshold | First response |
|---|---|---|
| Readiness | Any `/ready` 503 for two consecutive checks, or a deploy that never becomes ready | Check the returned `reason`. `database_unavailable` points at connectivity/pool pressure; `schema_mismatch` means traffic must stay off until the single Alembic head is `0025`. Compare `/live`: live-but-not-ready is a dependency or migration fault, not a dead process. |
| Review poll | Any batch response with `failed_count > 0`; two consecutive in-window `delivery_failed` or unexpected `no_devices` results | Search for `trigger-review batch had failures` and the per-account fingerprint. Manually call the cron endpoint once after fixing it; never stamp a card as pushed by hand. |
| Import leases | A `processing` material source or pending Study Plan draft whose heartbeat is more than two minutes old; warn when any source remains `pending` for 15 minutes | The worker heartbeat runs every 15 seconds, the stale lease is two minutes, the material sweeper runs every minute, and only two material imports run concurrently. Look for `material import worker failed`, `material import sweep failed`, or `study-plan preview heartbeat failed`; retry through the API rather than rewriting status rows. |
| Database pool | Any connection checkout/queue timeout, or provider connection utilization above 80% for five minutes; warn if `/ready` latency rises above two seconds | Inspect Railway Postgres connections and app logs. Long model calls must not hold a connection; find the route that does before increasing pool size. If a pooler was introduced, re-check both prepared-statement cache settings in §3. |
| APNs token health | Notify on every `apns rejected ... permanent=true`; page if the rejection removes the last usable token or produces two consecutive in-window `delivery_failed` results | Permanent `BadDeviceToken`, topic mismatch, and `Unregistered` responses are removed automatically. Logs contain only a token fingerprint. Confirm `APNS_USE_SANDBOX` matches the installed build before asking the device to register again. |
| Paid-model budget | Warn at 75% and urgent at 90% of both `LLM_CALLS_PER_DAY`/`GUIDE_IMPORTS_PER_DAY` and the provider billing limit; page on an unexpected budget 429 | The application counters are safeguards, not a billing ceiling. Identify the operation from `llm_usage`; do not raise a limit until retries, shadow calls, and import volume are understood. |

The import lease query below is safe to use for triage on Postgres. A stale row
should normally be reclaimed by the next sweep; repeated appearance is the alert,
not permission to edit it manually.

```sql
SELECT id, status, updated_at, processing_heartbeat_at
FROM material_sources
WHERE (status = 'pending' AND updated_at < now() - interval '15 minutes')
   OR (status = 'processing'
       AND (processing_heartbeat_at IS NULL
            OR processing_heartbeat_at < now() - interval '2 minutes'));

SELECT id, status, updated_at, processing_run_id, processing_heartbeat_at
FROM study_plan_guide_drafts
WHERE status = 'pending'
  AND ((processing_run_id IS NULL
        AND updated_at < now() - interval '2 minutes')
       OR (processing_run_id IS NOT NULL
           AND (processing_heartbeat_at IS NULL
                OR processing_heartbeat_at < now() - interval '2 minutes')));
```

### Provider-enforced spend ceilings

`LLM_CALLS_PER_DAY` and `GUIDE_IMPORTS_PER_DAY` are account-level, best-effort
application checks. Concurrent requests can cross a count boundary, and the
in-process provider-admission limit only bounds concurrency in one API replica.
Neither is a hard financial control.

In the Anthropic console—and in the OpenAI project before any V2 provider stage—
configure the smallest provider-enforced monthly spending limit and rate limits
that support the rollout. Add billing notifications at 75% and 90%. If a provider
offers an alert-only budget rather than a hard stop, document that distinction and
use a restricted/prepaid project or another provider-enforced ceiling. Test the
warning path with a deliberately low temporary threshold, then restore the
recorded production value. These settings are **unverified production state**
until that evidence exists.

### Backup/PITR status and restore drill

As of this audit, the repository contains no evidence that Railway backups or
point-in-time recovery are enabled, what their retention is, or that a restore has
ever completed. Do not infer recoverability from a successful migration or test
suite. Until the controls below are verified, the actual RPO and RTO are unknown.
Use **RPO ≤ 24 hours** and **RTO ≤ 4 hours** as provisional operating targets, not
as guarantees.

Verify the provider setting and run this drill before calling the targets met,
then repeat the restore at least monthly and after a database-provider change:

1. In Railway, record the Postgres backup/PITR feature, retention window, latest
   recoverable timestamp, region, and who can initiate a restore. Enable a daily
   backup or PITR policy capable of the provisional RPO if it is absent.
2. Choose a recovery point several hours old and use Railway's documented restore
   operation to create a **new isolated Postgres service**. Never restore over the
   production database and never attach the production API service to the clone.
3. Give a local verification process temporary access to the clone. Keep
   `REVIEW_POLLER_ENABLED=false`; omit APNs and model-provider credentials so a
   smoke test cannot send a push or make a paid call.
4. Before running any migration, execute `DATABASE_URL=<restore-url> uv run
   alembic current`. For this release the single revision must be `0025`. Run
   `uv run alembic heads` locally and confirm it also reports only `0025`; an
   older restore point is acceptable only when its age explains the revision.
5. Record row counts for `users`, `cards`, `sessions`, `study_plans`,
   `material_sources`, and `llm_usage`. Run orphan checks for owned rows against
   `users`, inspect several recent timestamps, and verify one known account can
   be exported from the isolated app without exposing credentials in the drill
   record.
6. Start the restored app locally with the safeguards from step 3 and confirm
   `/live`, `/ready`, `/health`, and an authenticated read. If the chosen restore
   predates `0025`, make a second clone or snapshot, run `alembic upgrade head`
   there, and repeat the checks; do not mutate the untouched restore evidence.
7. Record the recovery point, start/end time, measured data loss, measured restore
   time, schema revision, counts, operator, and result. Delete the disposable
   service and revoke its temporary credential when the evidence is complete.

### Production-only validation still open

- APNs sandbox delivery has reached a physical device. A TestFlight build uses a
  production APNs token, and the coordinated switch to
  `APNS_USE_SANDBOX=false` plus `WC_APS_ENVIRONMENT=production` has not yet been
  exercised end to end.
- The generic Study Plan importer fixes are unit-tested, but the post-fix live
  Anthropic rerun was blocked by account credit. Repeat the reviewed guide import
  once the provider budget is funded, retaining latency, token, validation, and
  retry evidence without copying guide or model text into logs.
- Confirm production has deployed through migration `0025`, `/ready` returns that
  exact revision, the backup/PITR drill passes, and provider-enforced spending
  ceilings and their alert destinations are recorded.

## Scoring Contract V2 activation and rollback

V2 is dark-launched. `SCORING_CONTRACT_VERSION` defaults to `1`; merging or
deploying its code does not change production scoring.

Activate only after the compatible iOS build is installed on every supported
device:

1. With the server still on V1, open `GET /settings` in that build and confirm
   `active_scoring_contract_version` renders the V1 UI without decoding errors.
2. Exercise Today, a scored result, mixed Card History, Coverage, Review Sprint,
   and Session Recap. V1 composites must remain V1; no screen may call them
   Recall merely because the integers happen to match.
3. Set `SCORING_CONTRACT_VERSION=2` on Railway and deploy without changing
   `SCORING_MODEL`, `SCORING_EFFORT`, or any provider credential.
4. Confirm `GET /settings` returns `2`, then complete one ordinary review at each
   final Recall band needed to prove both branches: `0–2` offers the existing
   re-attempt; `3–5` offers **Go one level deeper**. Follow-ups must match the
   amended truth table in `docs/SCORING-CONTRACT-V2-SPEC.md`: on the initial
   answer a provisional `1–3` probes and `0`/`4–5` completes; after one answered
   probe only `needs_more_evidence: true` probes again; at two answered probes
   the turn always completes. A missing candidate for a server-granted turn is
   a 503. A surplus candidate or insufficiency claim on a completing turn is
   ignored and logged without its text; confirm it cannot create a probe, alter
   Recall, or add a provider call.
5. Verify the new session row: `score = accuracy`, `depth IS NULL`,
   `boundaries IS NULL`, and `scoring_contract_version = 2`. Verify the card's
   `last_score = last_accuracy`, secondary axes are null, and
   `last_score_contract_version = 2`.
6. Submit one qualitative turn and verify exactly the four `coaching_*` session
   columns changed. The card, mastery summary, original score, and four SM-2
   fields must be unchanged.
7. Inspect logs for the `score` and `coaching` model calls, including model,
   latency, cache/input/output tokens, and invalid structured responses. No live
   canary beyond the answers you intentionally submit is part of deployment.
   A scoring response ending with `stop_reason=max_tokens` is an activation
   failure even when a manual resubmission succeeds: roll back, preserve the
   failed-call audit, and verify the fingerprinted 8,000-token scoring ceiling
   before another window.

Immediate rollback is configuration-only: first set
`OPENAI_V2_SCORING_MODE=off`, then set `SCORING_CONTRACT_VERSION=1` in the same
deployment change and redeploy. The server deliberately refuses to boot with an
OpenAI mode enabled against V1. Existing V2 rows remain versioned and
visible; do not reverse migration 0011, clear coaching fields, copy Recall into a
legacy composite, or rewrite history. Sessions already created retain the
contract version they started with, so a rollback cannot change their scorer
mid-session.

## OpenAI V2 Recall scoring — staged rollout and kill switch

The binding decision and every quality/cost gate live in
`docs/OPENAI-V2-SCORING-ROLLOUT.md`. The runtime ships dark:
`OPENAI_V2_SCORING_MODE=off` is both the default and the immediate provider kill
switch. Do not set `shadow` or `primary` while activating V2; first complete and
sign off the Claude-only V2 stabilization window above.

Before any OpenAI transmission:

1. Apply migrations 0016–0017. Verify historical `sessions.scoring_route` and
   `llm_usage.details` are empty JSON objects; 0017 adds nullable run/heartbeat
   metadata to material imports and direct Study Plan guide previews so one
   claimant owns each paid transmission and its result. Neither migration
   rewrites a score, guide, preview, status, or scheduling field. The disposable
   Postgres migration test exercises 0016→0017→0016→0017 and verifies those
   data/default claims in both directions.
2. Deploy the combined-consent client and server procedure in **Public privacy
   and Sign in with Apple operations** below. Confirm the allowlisted account has
   granted the exact current policy, including the dual-provider shadow and
   pseudonymous OpenAI safety-identifier disclosure.
3. Pass the human-reviewed 18-card/three-week frozen pack and all three fresh
   Luna trials. Copy only the production qualification digest printed by the
   `v2-recall` runner and the strict UTC expiry accepted by all four run
   manifests and the comparator; never invent or recompute a digest from a
   different prompt. The expiry may be at most 30 days after every fresh run.
4. Set the separately funded `OPENAI_API_KEY`, exact qualified model and effort,
   qualification fingerprint, `OPENAI_V2_SCORING_QUALIFICATION_EXPIRES_AT`,
   independent safety-identifier secret, a freshly generated predeclared
   `OPENAI_V2_SCORING_SHADOW_STAGE_ID` UUID, and the owner UUID allowlist. Leave
   the mode `off`, deploy, and run the default-off,
   non-allowlist, fingerprint-mismatch, consent, and V1-isolation checks.
   Generate the safety-identifier secret from at least 32 random bytes with
   `openssl rand -hex 32`; do not reuse an app, cron, founder, Anthropic, or
   OpenAI credential.
5. After a separate approval, use `shadow` for stage ordinals 1–100 exactly.
   Claude remains authoritative. Shadow may add candidate latency; measure it,
   and stop if it harms the 1–3 minute session experience. Luna output must not
   affect any response or write. Export the predeclared stage inclusively from
   ordinal 1 with both its intent and terminal rows; pass the same UUID to
   `openai_shadow_report.py --expected-shadow-stage-id`, pass the exact deployed
   deadline with `--expected-qualification-expires-at`, and keep `--event-count
   100`. The reporter requires every selected event to start before that exact
   expiry. Pending/incomplete intents and missing providers are non-replaceable
   failures; counts other than 100 are diagnostic only.
6. Only after the shadow clears zero behavioral flips and at least 85% lower
   observed cost per successful scoring call may a separate approval set
   `primary` for the owner UUID. A valid Luna result is authoritative. Exactly
   one no-retry Claude call is allowed only after a typed Luna technical or V2
   contract failure.

Every physical shadow or fallback call consumes a separate daily-budget row.
Before orchestration, a content-free intent reserves the expected physical calls;
terminal rows finalize it atomically, while an incomplete trace retains only its
outstanding reservation and returns 503 before any score or schedule mutation.
`llm_usage.details` records provider, model, route, outcome, request ID, latency,
tokens, fingerprint, scoring-event/session correlation, consent/allowlist
verification, the deployed qualification expiry, and comparison/fallback
metadata; it must never contain the
question, answer, grounding, feedback, or mastery text.

Provider rollback is one variable: set `OPENAI_V2_SCORING_MODE=off` and deploy.
Confirm subsequent V2 scoring uses Claude and no new OpenAI request IDs appear.
The global `off` value, current-consent withdrawal, removal from the UUID
allowlist, an expired qualification, or a fingerprint mismatch all prevent
another OpenAI transmission. Expiry is checked again for already-open sessions
immediately before each physical OpenAI request.
Leave `SCORING_CONTRACT_VERSION=2` during a provider rollback; V2 contract
rollback is the separate procedure above.

## The daily push cap — resolved, no `push_log` needed

This section used to warn that moving to a frequent cron required a `push_log`
table first, because the cap counted *cards stamped today* rather than pushes, and
`check-missed` cleared `last_pushed_at` out from under it. The move happened; the
table was not needed.

Two changes closed the gap instead. `check-missed` now records which push it
counted on `missed_counted_at` (migration 0004) rather than erasing
`last_pushed_at`, so the evidence survives. And `trigger-review` never offers a card
it already pushed today, so every push in a day lands on a distinct card — which
makes the card count *equal* the push count, and the cap exact. Migration 0025 adds
`push_resolved_at`: when a session starts or trusted learning is opened after a
particular push, `check-missed` stamps that push as engaged and stops rediscovering
it on every later run. A newer push moves `last_pushed_at` past both resolution
stamps and becomes independently eligible for bookkeeping.

Reintroduce a `push_log` only if a card ever needs to be pushed twice in one day.

## Public privacy and Sign in with Apple operations

The source of truth for App Store privacy answers is
`docs/APP-STORE-PRIVACY-CHECKLIST.md`. The public privacy policy is
`https://devmax-recall.christrinh5.chatgpt.site/privacy`.

Migration 0014 already supports arbitrary provider names and policy versions; a
combined Anthropic and OpenAI disclosure needs no new migration. Before deploying
a new policy version, deploy code support while leaving
`AI_CONSENT_REQUIRED_POLICY_VERSION` on the policy already shipped. This is a
compatibility deploy, not activation: the old v1 client and the v2-capable client
both work, a v2 choice may be recorded early, and every provider route remains
within the required policy's provider set. `/health` must report the expected
required policy, latest supported policy, and minimum iOS build.

Then ship the consent-capable client that renders both providers and explicitly
describes simultaneous shadow transmission plus the stable pseudonymous OpenAI
safety identifier. It sends its exact `policy_version` with grant or decline.
After that minimum build is distributed and a renewed choice is recorded, change
`AI_CONSENT_REQUIRED_POLICY_VERSION` to the combined version and redeploy. Confirm
the three `/health` fields, verify `/auth/me` reports
`ai_processing_allowed=true`, confirm a legacy grant now receives 409 without a
write, and confirm legacy decline plus withdrawal still succeed without
authorizing processing. Keep
OpenAI scoring disabled throughout; its startup validator independently refuses
to boot unless the combined policy is required.

The public policy must say that OpenAI scoring uses the Responses API with
`store: false`: response application state is not retained for that request, but
standard API data is not used for training only by default and default
abuse-monitoring logs may contain prompts and responses for up to 30 days. Do not
describe `store: false` as Zero Data Retention. Keep the public policy, App Store
privacy answers, and both provider-policy links synchronized with the client.

In Apple Developer → Certificates, Identifiers & Profiles → Identifiers →
`com.christrinh.devmax` → Sign in with Apple → Configure, set the server-to-server
notification endpoint to:

```text
https://devmax-production.up.railway.app/auth/apple/notifications
```

The endpoint accepts only Apple-signed JWTs for this app identifier. A verified
`consent-revoked` or `account-deleted` event clears the stored Apple refresh
authorization and revokes all live Unprompted sessions. Study data is preserved;
permanent deletion remains the explicit in-app Delete account operation.
