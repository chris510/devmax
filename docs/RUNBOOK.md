# Warm Cache — Deploy and Operations Runbook

Everything needed to get from a clean repo to a push arriving on a phone, and to
diagnose it when one doesn't.

Steps that need your credentials are marked **(you)**. Nothing here has been run
against real Fly, Neon, Anthropic, or APNs yet — the schema and the app have been
verified against a local Postgres 16, but the first real deploy is still ahead.

---

## 0. What you need before starting

| Account | Used for | Notes |
|---|---|---|
| Neon | Postgres | Free tier. Scales to zero. |
| Fly.io | The API | Single `shared-cpu-1x` machine, `sjc`. |
| Anthropic | Question generation + scoring | Set a low monthly spend cap; expect cents at ~4 calls/day. |
| Apple Developer | Push notifications | **Paid membership required.** A free personal team cannot carry the Push Notifications entitlement. Longest lead time — start here. |

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
| `DATABASE_URL` | Fly | — |
| `API_KEY` | Fly | `ios/Config/Secrets.xcconfig`, inside the app binary |
| `CRON_SECRET` | Fly | GitHub repo secret. **Never** in the app. |
| `ANTHROPIC_API_KEY` | Fly | — |
| `APNS_KEY_ID` | Fly | — |
| `APNS_TEAM_ID` | Fly | — |
| `APNS_BUNDLE_ID` | Fly | `com.christrinh.warmcache` |
| `APNS_PRIVATE_KEY` | Fly | The `.p8` file, offline |
| `API_BASE_URL` | GitHub repo secret | `https://warm-cache-api.fly.dev` |

`APNS_USE_SANDBOX` and `LOG_LEVEL` are in `fly.toml`'s `[env]`, not secrets.

### Rotation

- **`CRON_SECRET`** lives in two places. Update Fly, then the GitHub secret, within
  the same minute, then `workflow_dispatch` the trigger workflow to confirm. A
  missed cron in between is a harmless no-op, but don't rotate on a day you care.
- **`API_KEY`** is also inside an installed binary. Rotating it bricks the phone
  until you install a new build. Only rotate alongside a build and install; never
  remotely.

---

## 2. Neon **(you)**

1. Create a project and database.
2. Copy the **direct** connection string — *not* the pooled `...-pooler...` host.
   PgBouncer in transaction mode breaks asyncpg's prepared statements. (`db.py`
   sets `statement_cache_size=0` so the pooled host is survivable rather than
   silently broken, but the direct endpoint is the right choice for one user.)
3. Rewrite the scheme `postgresql://` → `postgresql+asyncpg://`.

The trailing `?sslmode=require&channel_binding=require` can stay — those are
libpq-only parameters that asyncpg rejects, and `db.py` strips them and negotiates
TLS itself. It works either way.

---

## 3. First deploy **(you)**

Order matters: the app fails closed on missing secrets, so they go in *before* the
first deploy, and all in one call (each `fly secrets set` restarts the machine).

```sh
cd api
fly auth login
fly launch --no-deploy --name warm-cache-api --region sjc   # keep the existing fly.toml

fly secrets set -a warm-cache-api \
  DATABASE_URL='postgresql+asyncpg://...' \
  API_KEY='...' \
  CRON_SECRET='...' \
  ANTHROPIC_API_KEY='...'

fly deploy
```

`fly.toml`'s `[deploy] release_command` runs `alembic upgrade head` in a temporary
machine before any app machine starts. **This is the migration's first contact with
Neon.** It creates the schema *and* seeds the settings row that
`/internal/trigger-review` reads on its very first query.

Then add the GitHub repo secrets `API_BASE_URL` and `CRON_SECRET`.

### Verify

```sh
B=https://warm-cache-api.fly.dev
curl -sS $B/health                                    # {"status":"ok"}
curl -sS -o /dev/null -w '%{http_code}\n' $B/cards/due            # 401
curl -sS -H "X-API-Key: $API_KEY" $B/cards/due        # []
curl -sS -X POST -H "X-Cron-Secret: $CRON_SECRET" $B/internal/trigger-review
```

`/health` passing proves asyncpg, greenlet, TLS, and Neon wake-from-zero all work
in one call. `fly logs` should show the `APNS_PRIVATE_KEY is unset` warning (expected
at this stage) and no tracebacks.

---

## 4. Seeding **(you)**

```sh
fly ssh console -a warm-cache-api \
  -C "python -m app.seed --file /app/cards.json --weeks-through 6 --start-date 2026-08-03"
```

**Use `--file`, never `--fixtures`.** The fixtures carry invented session history
and a fake 14-hour-old in-progress draft that would render a bogus resume banner on
a real card. `seed.py` refuses a `neon.tech` URL without `--force`.

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
hand with `fly logs` open, rather than discovering a problem when a push arrives.

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

`fly logs` should show `llm model=... ms=... in=... out=...`. Confirm SM-2 applied
exactly once:

```sql
SELECT ease_factor, interval_days, repetitions, next_review_at FROM cards WHERE id='...';
```

If the model IDs 404, they're config values: `fly secrets set SCORING_MODEL=...`.
Load the `claude-api` skill before changing them.

---

## 6. iOS build **(you, on a Mac)**

```sh
cd ios
cp Config/Secrets.example.xcconfig Config/Secrets.xcconfig   # paste the server's API_KEY
```

Set `DEVELOPMENT_TEAM` in `project.yml` to your Team ID, then:

```sh
xcodegen generate
xcodebuild -project WarmCache.xcodeproj -scheme WarmCache \
  -destination 'platform=iOS Simulator,name=iPhone 16e' test
```

Check against a live server before going to a device:

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

```sh
fly secrets set -a warm-cache-api \
  APNS_KEY_ID='...' APNS_TEAM_ID='...' APNS_BUNDLE_ID='com.christrinh.warmcache'
fly secrets set -a warm-cache-api APNS_PRIVATE_KEY="$(cat AuthKey_XXXXXXXX.p8)"
```

`APNS_USE_SANDBOX` must match the app's `aps-environment`: **development ↔ true**,
production ↔ false. A mismatch fails silently as `BadDeviceToken`.

1. Launch the Release build, grant notification permission.
2. **Confirm the token arrived before anything else:**
   `SELECT token, created_at FROM device_tokens;` — empty means registration failed
   (check the device console for `warmcache: APNs registration failed`) or the build
   is still in mock mode.
3. Confirm `GET /cards/due` is non-empty.
4. Don't wait for the cron. Widen a window to cover now via `PUT /settings`,
   `workflow_dispatch` the Trigger review workflow, expect
   `{"sent": true, "card_id": ..., "due_count": N}` and a banner. **Restore the real
   windows immediately** — see the constraint in §Scheduling below.
5. Tap the notification → it should deep-link into that card. Answer by voice.
6. Confirm an unattended scheduled fire at 15:20 UTC.

---

## Scheduling

Two crons, in UTC, at `20 15` and `10 5`. These are *not* the obvious translations
of the 07:10 and 21:00 window starts, and the difference matters.

GitHub cron doesn't observe DST but the windows are local and do. Both windows are
wider than the one-hour shift (80 and 90 minutes), so a fixed UTC time exists that
lands inside them under either offset:

- morning `07:10–08:30` local → `15:10–15:30` UTC → **`20 15`** (08:20 PDT / 07:20 PST)
- evening `21:00–22:30` local → `05:00–05:30` UTC → **`10 5`** (22:10 PDT / 21:10 PST)

**This only holds while every enabled window stays wider than 60 minutes and
contains the chosen time.** If you narrow a window in `PUT /settings`, the trigger
workflow will start failing with `outside_window` — that failure is deliberate, and
it means the schedule and the windows have drifted apart. Fix one or the other.

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
3. **`fly logs`** — `apns rejected token=...` means APNs took the request and
   refused it. Almost always the sandbox/production mismatch.
4. **`SELECT * FROM device_tokens`** — empty means the phone never registered.
5. **The phone** — Focus mode, notification settings, or permission denied at first
   launch (there is no second prompt; delete and reinstall).

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
