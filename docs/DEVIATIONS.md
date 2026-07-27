# Deviations from `spec.md`

`AGENTS.md` makes `spec.md` authoritative for the backend, so anything built
differently is recorded here rather than left as a silent disagreement. Each entry
says what the spec asks for, what the code does, and why.

Nothing here is a feature addition. Everything is either a spec bug found by
running the thing, or a mechanism the spec describes that doesn't achieve what the
spec says it's for.

---

## 1. The cron schedule breaks for four months a year

**Spec** (§GitHub Actions cron) prescribes:

```yaml
- cron: '10 14 * * *'   # 07:10 PT
- cron: '0 4 * * *'     # 21:00 PT
```

**We use** `20 15 * * *` and `10 5 * * *`.

GitHub cron is UTC and does not observe DST; the notification windows are local
(`America/Los_Angeles`) and do. `14:10` UTC is 07:10 PDT but **06:10 PST**, and
`04:00` UTC is 21:00 PDT but **20:00 PST**. The windows are `07:10–08:30` and
`21:00–22:30`, so from November to March both daily fires land outside every window
and return `{"sent": false, "reason": "outside_window"}` with HTTP 200 — a green
workflow and no pushes, for four months.

Both windows are wider than the 60-minute shift, so a fixed UTC time exists inside
them under either offset: `15:10–15:30` and `05:00–05:30` UTC. The comment in
`spec.md` about tolerating cron *lateness* doesn't cover a schedule that is an hour
*early*.

The workflows now also read the response body and fail on `outside_window`, so if
a window is ever narrowed below the shift the breakage is loud instead of silent.

## 2. `--weeks-through` cannot prevent the day-one flood

**Spec** (§Seeding): "Add a `--weeks-through N` flag that only loads cards with
`target_week <= N`, so the initial queue isn't flooded with 111 cards on day one."

The flag was implemented as specified, but it cannot do that job: it controls
*which* cards load, not *when* they come due, and every seeded card got
`next_review_at = today`. `--weeks-through 2` still put 38 cards in the queue at
once.

Due dates are now dealt across each card's target week at the configured
`reviews_per_day` rate (one a day for desk cards, which never enter the push loop),
so seeding all 111 puts exactly 2 conversational cards in the queue per day.
`--weeks-through` is kept as a safety valve. Adds `--start-date` so later loads
align to the same week boundaries.

## 3. Prompt caching is configured but never engages

**Spec** (§LLM integration) claims caching the rubric "cuts cached input cost ~90%".

The `cache_control: ephemeral` block is present and correctly shaped, but
`SCORING_RUBRIC` is ~410 tokens and `QUESTION_RUBRIC` ~166, both far below the
1024/2048-token minimum cacheable prefix. `cache_read_input_tokens` will always be
zero.

Left as-is. Padding a rubric to 1024 tokens to save a fraction of a cent at ~4
calls a day would make the prompt worse. The claim in the spec is wrong; the code
is harmless.

## 4. `cards.json` did not exist

**Spec** (§Seeding) describes it as "already generated — 111 cards from the study
plan".

It was not in the repo. Authored to the contract `seed.py` consumes: 111 cards over
six weeks, 84 conversational and 27 desk, split by the spec's category →
`delivery_mode` mapping. See `api/cards.json`.

## 5. `POST /device-tokens` is insert-if-absent, not upsert

**Spec** (§`POST /device-tokens`): "Upsert on token."

With `token` as the primary key and only `kind` and `created_at` alongside it, and
exactly one `kind` in use, the two are behaviourally identical. Left as-is.

## 6. Timestamps are declared `timestamptz` in the models, not just the migration

Not a spec deviation so much as a bug the spec's schema implied and the code got
wrong. `models.py` declared every datetime as a bare `datetime` (naive) while
migration 0001 creates `timestamptz` and the app writes tz-aware values. The
asyncpg dialect casts bind parameters from the *model* type, so it emitted
`$n::TIMESTAMP WITHOUT TIME ZONE` and asyncpg rejected every insert with a
`DataError`. SQLite silently drops `tzinfo`, which is why 45 tests passed over it.

Found by running `alembic upgrade head` against real Postgres for the first time.

## 7. Config fails closed

**Spec** (§Environment variables) lists the variables but not what happens when
they're missing. The implementation defaulted `API_KEY` to `dev-api-key` and
`CRON_SECRET` to `dev-cron-secret`, so a deploy that forgot to set them would
boot healthy on a public hostname authenticated by two strings published in this
repo.

`DATABASE_URL`, `API_KEY`, and `CRON_SECRET` are now required, known placeholders
are refused, and the two secrets must differ — the spec calls for two *independent*
secrets, and only one of them is meant to ship inside the app binary.

## 8. Deployed to Railway, not Fly.io + Neon

**Spec** (§Stack): "**Deployment:** Fly.io, single small instance" and "**Postgres** —
Neon free tier".

Both are now Railway: the API and its Postgres are two services in one project. The
owner already pays for Railway for other apps and wants a single provider; that is a
deployment preference the spec has no stake in, and nothing in the code is
provider-specific. `fly.toml` is gone, `api/railway.json` replaces it, and Fly's
`release_command` maps onto Railway's `preDeployCommand` with the same guarantee —
migrations run to completion before the new container takes traffic.

Two knock-on changes were required, both in `app/db.py`:

- **`sslmode=require` now means encrypt-without-verify**, matching libpq, where only
  `verify-ca` and `verify-full` request certificate validation. The previous code
  mapped `require` to a fully verifying context, which is stricter than the URL asks
  for and fails against any provider fronting Postgres with a self-signed
  certificate — which is what Railway's TCP proxy uses. This is a correctness fix
  that happens to be what unblocks Railway.
- **`*.railway.internal` is treated as a trusted network** and gets no app-level TLS.
  Railway puts every service in an environment on an encrypted WireGuard mesh, and
  the Postgres image's certificate is self-signed, so demanding TLS there would fail
  for no security gain.

Note the deliberate asymmetry: `is_local_database`, which gates
`seed.py --fixtures`, does **not** treat `railway.internal` as local. A private
network address is still a production database. Merging the two predicates would let
the fixtures — invented session history and a fake in-progress draft — into a real
study queue.

The GitHub Actions crons stay where they are. Railway cron is also UTC, so it would
inherit the identical DST problem from §1, and it requires a service that exits on
completion — a second service to make one HTTP call.

## 9. `alembic` autogenerate is disabled

`env.py` pointed `target_metadata` at `SQLModel.metadata`, which deliberately
diverges from the handwritten migration: the four CHECK constraints, every
`server_default`, and TEXT/SMALLINT vs VARCHAR/INTEGER exist only in the migration.
`alembic revision --autogenerate` would have emitted a revision dropping the
constraints. `target_metadata` is now `None`; write revisions by hand.

---

## Known limitation, not a deviation

`/internal/trigger-review` enforces `reviews_per_day` by counting *cards stamped
today* rather than pushes, and `check-missed` clears `last_pushed_at`, erasing that
evidence. With two cron fires a day and `reviews_per_day: 2` the branch is
unreachable, so this is inert today. Fixing it properly needs a `push_log` table.

**Trigger condition: if the cron moves to hourly, or `reviews_per_day` rises above
the number of daily fires, add `push_log` first.**

---

## Deviation from the design handoff, not `spec.md`

`design_handoff_devmax_initial/README.md` §Assets says: *"None. No images, no
icons, no SVG."* That is true of every screen — the codebase still has zero
`Image( )` calls, and the `✕ ← + ▼ ▍` glyphs are text characters. But an iOS app
needs an icon, and the handoff never specified one, so it came from a later icon
kit that now lives in `assets/app_icon/` (the "Cache stack" mark: three offset
rounded bars, bottom bar in the accent `#57b6c2`, so icon and UI stay in sync).

Three things about how it's wired:

- **Only the dark master is shipped.** `AppIcon.appiconset` carries a single
  1024 in the default slot. iOS derives the dark and tinted home-screen variants
  itself, which flat art with high foreground/background contrast handles well.
  The kit's light pair and monochrome SVG are committed as delivered but unused.
- **The catalog PNG is RGB, not RGBA.** Every PNG in the kit carries an alpha
  channel (uniformly opaque, but present), and App Store Connect rejects a
  marketing icon that has one. The copy under `AppIcon.appiconset` is re-encoded
  to colortype 2 and is otherwise pixel-identical to the kit master. **Re-export
  from `assets/app_icon/svg/` and you must strip alpha again.**
- **Nothing was pre-rounded.** iOS applies the squircle mask; the
  `devmax-icon-rounded-*.png` files in the kit are for platforms that don't.

The launch screen is still a bare `#0D0F11` with no image, per the handoff.
