# Warm Cache — Deploy and Operations Runbook

Everything needed to get from a clean repo to a push arriving on a phone, and to
diagnose it when one doesn't.

The backend runs on **Railway** — both the API and its Postgres, in one project.
Steps that need your credentials are marked **(you)**. Nothing here has been run
against real Railway or APNs yet; the schema and the app have been verified against
a local Postgres 16, but the first real deploy is still ahead.

---

## 0. What you need before starting

| Account | Used for | Notes |
|---|---|---|
| Railway | The API *and* Postgres | Two services in one project. |
| Anthropic | Question generation + scoring | Set a low monthly spend cap; expect cents at ~4 calls/day. |
| Apple Developer | Push notifications | **Paid membership required.** A free personal team cannot carry the Push Notifications entitlement. Longest lead time — start here. |
| GitHub | The two cron workflows | Already have it; two repo secrets to add. |

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
| `CRON_SECRET` | Railway | GitHub repo secret. **Never** in the app. |
| `ANTHROPIC_API_KEY` | Railway | — |
| `APNS_KEY_ID` | Railway | — |
| `APNS_TEAM_ID` | Railway | — |
| `APNS_BUNDLE_ID` | Railway | `com.christrinh.warmcache` |
| `APNS_PRIVATE_KEY` | Railway | The `.p8` file, offline |
| `API_BASE_URL` | GitHub repo secret | Your Railway public domain |

`APNS_USE_SANDBOX` and `LOG_LEVEL` are ordinary Railway variables, not secrets.

### Rotation

- **`CRON_SECRET`** lives in two places. Update Railway, then the GitHub secret,
  within the same minute, then run the workflow manually to confirm. A missed cron
  in between is a harmless no-op, but don't rotate on a day you care.
- **`API_KEY`** is also inside an installed binary. Rotating it bricks the phone
  until you install a new build. Only rotate alongside a build and install; never
  remotely.

---

## 2. Merging is what starts the crons

Scheduled workflows only fire from the default branch, so the merge is what makes
`trigger-review` and `check-missed` live. Until the backend is deployed and the two
GitHub secrets exist they will fail — 8 red runs a day, with email.

Either do §3 promptly, or disable both workflows in the repo's Actions tab and
re-enable them at the end of §3. CI itself will pass on merge.

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
railway run --service <api-service> \
  python -m app.seed --file cards.json --weeks-through 6 --start-date 2026-08-03
```

**Use `--file`, never `--fixtures`.** The fixtures carry invented session history
and a fake 14-hour-old in-progress draft that would render a bogus resume banner on
a real card. `seed.py` refuses any non-local database without `--force`, and that
check treats `postgres.railway.internal` as real — a private address is still
production.

**Record the `--start-date`.** Re-running with the same value is idempotent (dedupe
is by topic); a different value misaligns the week boundaries.

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
xcodebuild -project WarmCache.xcodeproj -scheme WarmCache \
  -destination 'platform=iOS Simulator,name=iPhone 16e' test
```

Check against the live server before going to a device:

```sh
xcrun simctl launch --setenv WC_MOCK=0 <device> com.christrinh.warmcache
```

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
APNS_BUNDLE_ID   = com.christrinh.warmcache
APNS_PRIVATE_KEY = <the entire .p8 contents, including the BEGIN/END lines>
```

`APNS_PRIVATE_KEY` is multi-line. Railway's variable editor accepts that directly —
paste the whole file, don't collapse the newlines.

`APNS_USE_SANDBOX` must match the app's `aps-environment`: **development ↔ true**,
production ↔ false. A mismatch fails silently as `BadDeviceToken`.

1. Launch the Release build, grant notification permission.
2. **Confirm the token arrived before anything else:**
   `SELECT token, created_at FROM device_tokens;` — empty means registration failed
   (check the device console for `warmcache: APNs registration failed`) or the build
   is still in mock mode.
3. Confirm `GET /cards/due` is non-empty.
4. Don't wait for the cron. Widen a window to cover now via `PUT /settings`, run the
   Trigger review workflow manually, expect `{"sent": true, "card_id": ...,
   "due_count": N}` and a banner. **Restore the real windows immediately** — see
   §Scheduling.
5. Tap the notification → it should deep-link into that card. Answer by voice.
6. Confirm an unattended scheduled fire at 15:20 UTC.

---

## Scheduling

Two GitHub Actions crons, in UTC, at `20 15` and `10 5`. These are *not* the obvious
translations of the 07:10 and 21:00 window starts, and the difference matters.

GitHub cron doesn't observe DST but the windows are local and do. Both windows are
wider than the one-hour shift (80 and 90 minutes), so a fixed UTC time exists that
lands inside them under either offset:

- morning `07:10–08:30` local → `15:10–15:30` UTC → **`20 15`** (08:20 PDT / 07:20 PST)
- evening `21:00–22:30` local → `05:00–05:30` UTC → **`10 5`** (22:10 PDT / 21:10 PST)

**This only holds while every enabled window stays wider than 60 minutes and
contains the chosen time.** If you narrow a window in `PUT /settings`, the trigger
workflow will start failing with `outside_window` — that failure is deliberate, and
it means the schedule and the windows have drifted apart. Fix one or the other.

The crons stay on GitHub Actions rather than moving to Railway cron. Railway cron is
also UTC, so it would inherit the identical DST problem, and it needs a service that
exits on completion — a second service just to make one HTTP call. GitHub Actions is
already where the code lives and the workflow can assert on the response body.

Scheduled workflows are disabled after 60 days of repository inactivity. GitHub
emails first; click Enable in the Actions tab.

---

## Triage: no push arrived

In order.

1. **Did the workflow run?** Actions tab. A red run shows the response body.
2. **`{"sent": false, "reason": ...}`** — each reason is specific:
   - `outside_window` — schedule/window drift, see above. The job fails on this.
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

## Known limitation: the daily push cap

`/internal/trigger-review` counts *cards stamped today*, not pushes, and
`check-missed` clears `last_pushed_at`. With two cron fires a day and
`reviews_per_day: 2` the branch is unreachable, so this is currently harmless.
**If you ever move to an hourly cron or raise `reviews_per_day`, add a `push_log`
table first** — otherwise the cap can be exceeded.
