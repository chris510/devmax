# Devmax — Deploy and Operations Runbook

Everything needed to get from a clean repo to a push arriving on a phone, and to
diagnose it when one doesn't.

The backend runs on **Railway** — both the API and its Postgres, in one project.
Steps that need your credentials are marked **(you)**. Nothing here has been run
against real Railway yet. APNs *has* been exercised for real — a push has reached a
physical iPhone from a local server — and the schema and app are verified against a
local Postgres 17, including through a PgBouncer running in `transaction` mode (see
§3). The first real deploy is still ahead.

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

Nine values. Generate the two shared secrets fresh, and make them different from
each other — the app refuses to boot otherwise (`app/config.py`).

```sh
openssl rand -base64 32   # API_KEY
openssl rand -base64 32   # CRON_SECRET
```

| Secret | Where it lives | Also held by |
|---|---|---|
| `DATABASE_URL` | Railway service variable | Reference the Postgres service, don't paste — see §3 |
| `API_KEY` | Railway | `ios/Config/Secrets.xcconfig`, inside the app binary |
| `CRON_SECRET` | Railway API service | GitHub repo secret. **Never** in the app. |
| `ANTHROPIC_API_KEY` | Railway | — |
| `APNS_KEY_ID` | Railway | — |
| `APNS_TEAM_ID` | Railway | — |
| `APNS_BUNDLE_ID` | Railway | `com.christrinh.devmax` |
| `APNS_PRIVATE_KEY` | Railway | The `.p8` file, offline |
| `API_BASE_URL` | GitHub repo secret | Your Railway public domain |

`APNS_USE_SANDBOX` and `LOG_LEVEL` are ordinary Railway variables, not secrets.

### Rotation

- **`CRON_SECRET`** lives in Railway and GitHub. Update Railway, then the GitHub
  secret within the same minute, then run the fallback workflow manually to
  confirm.
- **`API_KEY`** is also inside an installed binary. Rotating it bricks the phone
  until you install a new build. Only rotate alongside a build and install; never
  remotely.

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
upgrade head` as the `preDeployCommand`, and `/health` as the healthcheck.

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
ANTHROPIC_API_KEY  = <your key>
APNS_USE_SANDBOX   = true
LOG_LEVEL          = INFO
```

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
curl -sS $B/health                                                  # {"status":"ok"}
curl -sS -o /dev/null -w '%{http_code}\n' $B/cards/due               # 401
curl -sS -H "X-API-Key: $API_KEY" $B/cards/due                       # []
curl -sS -X POST -H "X-Cron-Secret: $CRON_SECRET" $B/internal/trigger-review
```

`/health` passing proves asyncpg, greenlet, the private-network address, and the
schema all work in one call. Deploy logs should show the `APNS_PRIVATE_KEY is unset`
warning (expected at this stage) and no tracebacks.

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
weeks. After the launch cohort, progress in the app: completing a supported Study
Plan lesson opens its recall-card proposal gate automatically, and the user reviews
and accepts useful cards before any are created. Do not activate later cohorts just
because their calendar week arrived. Coding patterns live in `api/library/` and
company overlays live in `api/modules/`; neither is part of the base seed.

The second command bootstraps the separate first-party Study Plan that powers
Today's plan line and the phase/week timeline. It makes no LLM call and never
reads or writes cards, sessions, scores, mastery, or SM-2 state. The committed
manifest is 12 weeks, four phases, 72 plan items, and 12 hours per week. Its
stable seed key makes the command idempotent. `--activate` refuses to displace
another active plan; pause that plan in the app first if switching is intentional.
Use the Monday containing the first practice day so Week 1 aligns with the
timeline's calendar labels. It is a one-time deployment action; ongoing study and
card creation happen in the app.

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
`POST /cards` are structurally out of reach. `archive/` ships in the image
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
curl -sS -X POST -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"text":"virtual nodes spread each server over many ring positions"}' \
  $B/sessions/$SID/answers                                  # -> status: complete
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
cp Config/Secrets.example.xcconfig Config/Secrets.xcconfig   # paste the server's API_KEY
```

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

Then build **Release** to the device: Release is the only configuration where the
`simulateSpeech` gate is exercised. Tap the mic and confirm it records *your voice*,
not a fixture paragraph.

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
2. **Confirm the token arrived before anything else:**
   `SELECT token, created_at FROM device_tokens;` — empty means registration failed
   (check the device console for `devmax: APNs registration failed`) or the build
   is still in mock mode.
3. Confirm `GET /cards/due` is non-empty.
4. Don't wait for the poll. Widen a window to cover now via `PUT /settings` (at
   least 30 minutes, or the write is rejected 422), run the Trigger review workflow
   manually, expect `{"sent": true, "card_id": ..., "due_count": N}` and a banner.
   **Restore the real windows immediately** — see §Scheduling.
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

Everything about *when* lives in the settings row: `windows`, `timezone`, and
`reviews_per_day`. Change a window in the app and the next poll obeys it — no
commit, no redeploy, and no DST arithmetic anywhere, because the comparison happens
in local time on the server. That replaced a hand-maintained UTC approximation of
the windows which disagreed with them for four months a year (`docs/DEVIATIONS.md`
§1).

Consequences worth knowing:

- **Most runs return `outside_window`.** At a 15-minute poll that is ~94 of 96 runs
  a day. It is the expected steady state and no longer fails the job.
- **At most one push per window.** The endpoint refuses a second push inside a
  window that already produced one, and returns `already_pushed`.
- **A window must be at least 30 minutes long.** `PUT /settings` rejects anything
  shorter with a 422. The 15-minute cadence gives every accepted window multiple
  attempts; keeping the 30-minute product constraint preserves that margin.
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
   means every enabled notification window in `settings` is unparseable — the one
   configuration fault the endpoint refuses to answer quietly, precisely because
   `outside_window` is otherwise indistinguishable from working normally.
2. **`{"sent": false, "reason": ...}`** — each reason is specific. All but the last
   are routine at a 15-minute poll and none of them fail the job:
   - `outside_window` — no enabled window contains the current local time. The
     usual answer, ~94 times a day. If it is *always* this, check `GET /settings`:
     a window turned off, or a timezone that no longer matches where you are.
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
4. **`SELECT * FROM device_tokens`** — empty means the phone never registered.
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
resumable by design — starting a session on that card returns the existing one.
There is no `abandoned` transition; nothing sets it.

## The daily push cap — resolved, no `push_log` needed

This section used to warn that moving to a frequent cron required a `push_log`
table first, because the cap counted *cards stamped today* rather than pushes, and
`check-missed` cleared `last_pushed_at` out from under it. The move happened; the
table was not needed.

Two changes closed the gap instead. `check-missed` now records which push it
counted on `missed_counted_at` (migration 0004) rather than erasing
`last_pushed_at`, so the evidence survives. And `trigger-review` never offers a card
it already pushed today, so every push in a day lands on a distinct card — which
makes the card count *equal* the push count, and the cap exact.

Reintroduce a `push_log` only if a card ever needs to be pushed twice in one day.
