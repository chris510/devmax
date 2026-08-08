# Devmax — Backend Build Spec

Handoff spec for scaffolding the Devmax backend. Build exactly what's
described here. Anything not specified is out of scope — see "Out of
scope" at the bottom before adding anything.

## Context

Devmax is a single-user conversational spaced-repetition study app for
technical interview prep. A scheduled job pushes a notification; the user
opens the iOS app, gets asked a question about a concept, answers by voice
or text, gets one follow-up probe if the answer was partial, then gets
scored 0–5. The score drives an SM-2 schedule that decides when the topic
comes back.

This backend serves that iOS client and receives a cron webhook. There is
exactly one user. Design accordingly: no multi-tenancy, no auth system, no
horizontal scaling concerns.

---

## Stack

- **Python 3.12**, **FastAPI**, **SQLModel** (async), **asyncpg**
- **Postgres** — Neon free tier (serverless, scales to zero)
- **Alembic** for migrations
- **Anthropic Python SDK** for question generation and scoring
- **Deployment:** Fly.io, single small instance
- **Scheduler:** GitHub Actions cron (external), hits an internal endpoint
- **Push:** APNs, token-based auth (.p8 key), via `aioapns` or equivalent
- **Dependency management:** `uv` (preferred) or `pip` + `requirements.txt`

Rationale for Python over Go: the core logic is LLM orchestration, where
the Anthropic SDK is first-class. Traffic is a handful of requests per
day, so Go's performance and footprint advantages don't apply.

---

## Project structure

```
devmax-api/
  app/
    main.py              # FastAPI app, middleware, router registration
    config.py            # pydantic-settings, env vars
    db.py                # async engine, session dependency
    models.py            # SQLModel table definitions
    schemas.py           # request/response Pydantic models
    auth.py              # X-API-Key middleware
    routers/
      cards.py
      sessions.py
      devices.py
      settings.py
      internal.py        # cron webhook
    services/
      llm.py             # question generation + scoring (Anthropic calls)
      scheduler.py       # SM-2 implementation
      push.py            # APNs client
  alembic/
  tests/
  fly.toml
  Dockerfile
  pyproject.toml
```

Keep SM-2 (`services/scheduler.py`) and LLM calls (`services/llm.py`) as
pure-ish functions independent of FastAPI request context so they're
directly unit-testable.

---

## Environment variables

```
DATABASE_URL             postgresql+asyncpg://...   (Neon)
API_KEY                  shared secret for the iOS client
CRON_SECRET              separate secret for /internal/* endpoints
ANTHROPIC_API_KEY
APNS_KEY_ID
APNS_TEAM_ID
APNS_BUNDLE_ID
APNS_PRIVATE_KEY         .p8 contents (not a file path — store as secret)
APNS_USE_SANDBOX         bool, default true
LOG_LEVEL                default INFO
```

---

## Auth

Two independent shared secrets, both checked in middleware:

- Client endpoints require header `X-API-Key: <API_KEY>`
- `/internal/*` endpoints require header `X-Cron-Secret: <CRON_SECRET>`

Reject with 401 and no body detail on mismatch. Use
`secrets.compare_digest` for the comparison. No JWT, no OAuth, no user
table — this is deliberate, not an omission to be "improved."

---

## Data model

Four primary tables plus a settings singleton. Pending captures are structurally
separate from cards so an ungrounded observation cannot accidentally become due,
trigger a push, or enter scoring.

### `cards`

| column | type | notes |
|---|---|---|
| `id` | UUID PK | |
| `topic` | text NOT NULL | e.g. "Consistent hashing" |
| `category` | text NOT NULL | e.g. "Core Concept", "Unsorted" |
| `pattern` | text NULL | e.g. "Multi-source BFS" |
| `source_company` | text NULL | e.g. "Anthropic" |
| `target_week` | int NULL | from the study plan |
| `delivery_mode` | text NOT NULL | `'conversational'` or `'desk'` |
| `canonical_question` | text NULL | generated or approved once, then reused |
| `source_url` / `source_section` / `source_label` | text NOT NULL DEFAULT '' | trusted provenance |
| `answer_basis` | text NOT NULL DEFAULT '' | concise trusted answer authority |
| `answer_rubric` | jsonb NOT NULL DEFAULT `{}` | mechanism, alternative, trade-off, failure mode, misconception |
| `lifecycle_status` | text NOT NULL DEFAULT `'active'` | `'active'` or `'archived'` |
| `archived_at` | timestamptz NULL | recoverable removal time |
| `replaces_card_id` / `replaced_by_card_id` | UUID NULL | question-replacement lineage |
| `ease_factor` | real NOT NULL DEFAULT 2.5 | SM-2, floor 1.3 |
| `interval_days` | int NOT NULL DEFAULT 1 | |
| `repetitions` | int NOT NULL DEFAULT 0 | consecutive successful reviews |
| `next_review_at` | date NOT NULL | |
| `last_score` | smallint NULL | 0–5 |
| `mastery_summary` | text NOT NULL DEFAULT '' | rolling 1–2 sentences |
| `missed_count` | int NOT NULL DEFAULT 0 | compliance only — never feeds SM-2 |
| `created_at` / `updated_at` | timestamptz | |

Index on `next_review_at`. Partial index on `(next_review_at) WHERE
delivery_mode = 'conversational' AND lifecycle_status = 'active'` since that is
the hot query. Every active-card selection uses the same lifecycle predicate.

**`delivery_mode` matters:** `'desk'` cards (coding problems, Tier 2
practical builds) are tracked and scheduled but are **never** returned by
`/cards/due` and never trigger a push. They need a keyboard and an hour,
not a two-minute voice session. Only `'conversational'` cards enter the
push loop.

### `pending_captures`

Fast observations awaiting review. Fields: `id`, `topic`, optional `context`,
`status`, the three provenance strings, `answer_basis`, `answer_rubric`,
`canonical_question`, optional `activated_card_id`, and timestamps. A pending
capture is never returned from a card endpoint. Activation requires provenance,
an answer basis, all five rubric fields, and a canonical question in one atomic
transaction. Replaying a successful activation returns the same card.

### `sessions`

| column | type | notes |
|---|---|---|
| `id` | UUID PK | |
| `card_id` | UUID FK → cards, ON DELETE CASCADE | |
| `question_asked` | text NOT NULL | the opening question |
| `follow_up_question` | text NULL | set if a follow-up was issued |
| `answer_text` | text NOT NULL DEFAULT '' | first answer |
| `follow_up_answer` | text NOT NULL DEFAULT '' | second answer if any |
| `draft_text` | text NOT NULL DEFAULT '' | in-progress, unsubmitted |
| `score` | smallint NULL | final score, null until complete |
| `feedback` | text NOT NULL DEFAULT '' | one-line, generated |
| `follow_up_used` | bool NOT NULL DEFAULT false | |
| `status` | text NOT NULL | `'open'` / `'awaiting_follow_up'` / `'complete'` / `'abandoned'` |
| `started_at` | timestamptz NOT NULL DEFAULT now() | |
| `ended_at` | timestamptz NULL | |

Index on `(card_id, started_at DESC)`.

A card is **resumable** if it has a session with
`status IN ('open','awaiting_follow_up')` and non-empty `draft_text`.

### `device_tokens`

| column | type |
|---|---|
| `token` | text PK |
| `kind` | text NOT NULL DEFAULT `'apns'` |
| `created_at` | timestamptz |

### `settings`

Single row, `id` fixed to 1, enforced with a CHECK constraint.

| column | type | default |
|---|---|---|
| `id` | int PK CHECK (id = 1) | 1 |
| `reviews_per_day` | int NOT NULL | 2 |
| `windows` | jsonb NOT NULL | `[{"label":"Morning","from":"07:10","to":"08:30","on":true},{"label":"Evening","from":"21:00","to":"22:30","on":true}]` |
| `timezone` | text NOT NULL | `'America/Los_Angeles'` |

Seed this row in a migration.

---

## SM-2 implementation (`services/scheduler.py`)

This is the one piece of logic that must be exactly right. Implement it as
a pure function and unit test it directly.

```python
def apply_sm2(
    ease_factor: float,
    interval_days: int,
    repetitions: int,
    quality: int,          # 0-5, the FINAL session score
    today: date,
) -> tuple[float, int, int, date]:
    """Returns (new_ease_factor, new_interval_days, new_repetitions,
    next_review_at)."""
```

Algorithm:

1. If `quality >= 3` (successful recall):
   - `repetitions == 0` → `interval = 1`
   - `repetitions == 1` → `interval = 6`
   - otherwise → `interval = round(interval_days * ease_factor)`
   - `repetitions += 1`
2. If `quality < 3` (failed recall):
   - `repetitions = 0`
   - `interval = 1`
3. Always update ease factor:
   `EF' = EF + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))`
   Clamp to a minimum of **1.3**. No upper clamp needed in practice but
   cap at 3.0 for sanity.
4. `next_review_at = today + timedelta(days=interval)`

Notes that matter:

- **Quality is the final session score**, after any follow-up. A session
  that scored 2, got a follow-up, and ended at 4 feeds SM-2 a 4.
- **Score 2 is a failure, score 3 is a pass** under SM-2 — even though
  both trigger a follow-up in the app's flow. These are two independent
  thresholds; don't collapse them.
- **`missed_count` never touches this function.** Missing a review is a
  compliance signal, not a retention signal. Conflating them means a busy
  week at work would trash the ease factor on topics the user actually
  knows cold, causing the scheduler to over-drill the wrong things.

---

## LLM integration (`services/llm.py`)

Model: `claude-sonnet-5` for scoring, `claude-haiku-4-5` for question
generation (question generation is the easier task; scoring is where model
quality actually matters). Make the model a config value per function, not
a hardcoded constant, so it can be swapped during calibration.

Both functions return structured JSON — instruct the model to return JSON
only, no preamble or code fences; parse defensively and retry once on
parse failure.

Use prompt caching on the system prompt / rubric block, which is
byte-identical across every call. It cuts cached input cost ~90%. Not
material at this volume, but it's a one-line change and the rubric is the
largest fixed part of each request.

### `generate_question(card) -> str`

Input context: `topic`, `category`, `pattern`, `source_company`,
`mastery_summary`, `last_score`, and the questions asked in the last 3
sessions (to avoid repeats).

Prompt shape:

> You are running a spaced-repetition recall session for a senior backend
> engineer preparing for interviews at Anthropic, OpenAI, and Google.
> Generate ONE question about the given topic that forces the engineer to
> reconstruct the mechanism from memory rather than recite a definition.
> Prefer concrete scenarios ("you add a sixth node to a five-node ring —
> what moves?") over open prompts ("explain consistent hashing"). If a
> mastery summary indicates a specific weak area, target that area. Do not
> repeat any of the recent questions listed. Return JSON:
> `{"question": "..."}`

### `score_answer(card, session, answer, is_follow_up) -> ScoreResult`

Returns one of two shapes:

```json
{"status": "follow_up", "question": "One more — ..."}
{"status": "complete", "score": 3, "feedback": "...",
 "mastery_summary": "..."}
```

Rules to encode in the prompt:

- Score 0–5 on **mechanism accuracy, trade-off awareness, and failure-mode
  awareness** — not on fluency, length, or confidence.
- If the score would be 2 or 3 **and** `follow_up_used` is false, return
  `status: "follow_up"` with a probe targeting the specific gap instead of
  scoring. Otherwise return `status: "complete"`.
- **Maximum one follow-up per session** — if `follow_up_used` is already
  true, always return `complete`. A session may carry at most one further
  *coached re-attempt* after completion; it is a separate call
  (`llm.score_reattempt`) on a separate endpoint and never reaches SM-2.
  See `docs/multi-turn-coaching-design.md`.
- `feedback` is one or two sentences, specific to what was said and what
  was missed. Not generic encouragement.
- `mastery_summary` is a rewritten rolling summary (1–2 sentences,
  lowercase fragment style, e.g. "solid on ring mechanics, shaky on
  virtual nodes"). It replaces the card's previous summary.

Answers arrive as voice transcripts, so they'll be conversational,
disfluent, and may contain transcription errors. Instruct the model to
score the substance and not penalize verbal filler or obvious
speech-to-text artifacts.

---

## Endpoints

All client endpoints require `X-API-Key`. All return JSON. Use appropriate
status codes (404 for unknown ids, 409 for state conflicts, 422 for
validation).

### `GET /cards/due?limit=10`

Returns conversational-mode cards where `next_review_at <= today`, ordered
by most overdue first, then by lowest `ease_factor`.

```json
[
  {
    "id": "uuid",
    "topic": "Consistent hashing",
    "category": "Core Concept",
    "mastery_summary": "solid on ring mechanics, shaky on virtual nodes",
    "last_score": 2,
    "due_label": "3 days overdue",
    "resumable": true,
    "missed_count": 0
  }
]
```

`due_label` is computed server-side ("due today", "3 days overdue") so the
client doesn't reimplement date math.

### `GET /cards?sort=next_review|weakest&mode=conversational|desk|all`

Full card list for browsing. Defaults: `sort=next_review`, `mode=all`.

### `GET /cards/{id}`

Card fields plus its session history:

```json
{
  "id": "uuid", "topic": "...", "category": "...",
  "mastery_summary": "...", "last_score": 3,
  "ease_factor": 2.36, "interval_days": 3,
  "next_review_at": "2026-07-27", "missed_count": 0,
  "sessions": [
    {
      "id": "uuid", "date": "2026-07-21T06:51:00Z", "score": 2,
      "feedback": "Explained the ring, stalled on virtual nodes.",
      "turns": [
        {"role": "question", "text": "..."},
        {"role": "answer", "text": "..."},
        {"role": "follow_up", "text": "..."},
        {"role": "answer", "text": "..."},
        {"role": "score", "text": "2 — ..."}
      ]
    }
  ]
}
```

Build `turns` server-side from the session columns — the client shouldn't
assemble transcript ordering.

### `GET /cards/overview`

Mastery classification across all cards. This is the "what do I spend my
desk hour on" endpoint.

Derive a tier per card — do not store it, compute it:

- **`untested`** — `repetitions == 0`
- **`shaky`** — `last_score <= 2` or `ease_factor < 2.0`
- **`developing`** — `repetitions` in 1–2 and `last_score` in 3–4
- **`solid`** — `repetitions >= 3` and `ease_factor >= 2.5` and
  `last_score >= 4`
- **`cold`** — would otherwise be `solid`, but
  `today > next_review_at + (2 * interval_days)`

Evaluate in order: `cold` first (it overrides `solid`), then `shaky`,
then `untested`, then `developing`, then `solid`. Anything that matches
nothing falls to `developing`.

```json
{
  "counts": {"untested": 12, "shaky": 4, "developing": 9,
             "solid": 6, "cold": 2},
  "shaky": [
    {"id": "uuid", "topic": "Raft leader election",
     "mastery_summary": "fuzzy on log-matching safety", "last_score": 2}
  ],
  "cold": [
    {"id": "uuid", "topic": "Consistent hashing",
     "mastery_summary": "solid on ring mechanics...",
     "days_overdue": 11}
  ]
}
```

Include `?mode=conversational|desk|all` (default `all`) — the desk-mode
breakdown is what tells the user which coding patterns have gone stale.

**Why `cold` is the tier that matters:** "never learned it" and "knew it
cold three weeks ago and let it lapse" are different problems needing
different responses, and nothing else in the API distinguishes them.

### `POST /cards`

Returns 409. Interactive card creation goes through `/captures`; there is no
endpoint that creates an ungrounded active card.

### Capture and activation

- `POST /captures` stores only a topic and optional context.
- `GET /captures` lists non-activated inbox items by newest first.
- `PATCH /captures/{id}` saves provenance, answer basis, rubric, and question edits.
- `POST /captures/{id}/question` generates the question only after grounding is
  complete; it is idempotent unless `regenerate=true` is explicit.
- `POST /captures/{id}/activate` accepts `schedule: "now"|"next"` and atomically
  creates a fresh active card at the normal SM-2 defaults.
- `DELETE /captures/{id}` discards only a pending capture.

### Card maintenance

- `GET /cards/{id}/maintenance` returns grounding and lifecycle metadata.
- `PATCH /cards/{id}/grounding` grounds a legacy card without changing schedule
  or history. Its question cannot be rewritten after history exists.
- `POST /cards/{id}/archive` removes the card from every active selection while
  preserving history and all SM-2 fields.
- `POST /cards/{id}/restore` reverses archive unless an active replacement exists.
- `POST /cards/{id}/replace` creates a fresh blank-history card with the edited
  question, archives the predecessor, and records both lineage links.

### `POST /cards/{id}/sessions`

Called when the user taps into a card to start reviewing — **not** when
the push fires.

Behavior:
- Reject an archived card.
- If an existing session for this card has status `'open'` or
  `'awaiting_follow_up'`, return that session instead of creating a new
  one (this is what makes resume work).
- Otherwise generate a question via `llm.generate_question`, create a
  session with `status: 'open'`, return it.
- Supply the card's trusted answer basis and rubric to question generation.

```json
{
  "session_id": "uuid",
  "question": "You're adding a node to a consistent-hashing ring...",
  "is_follow_up": false,
  "draft_text": "",
  "resumed": false
}
```

When resuming, `draft_text` carries the saved partial answer and
`resumed: true`.

### `PATCH /sessions/{id}/draft`

```json
{"draft_text": "so the key space is a ring of hashes and..."}
```

Persists an in-progress answer. The client calls this periodically while
recording or typing (debounced, roughly every few seconds). Returns 204.
This is what makes the "you were mid-answer" resume state possible after
the app is backgrounded — losing a spoken answer is the worst failure
mode in the product, so this endpoint should be cheap, idempotent, and
never blocked behind anything slow.

### `POST /sessions/{id}/answers`

```json
{"text": "so the key space is a ring of hashes..."}
```

Behavior:
1. Persist the answer to `answer_text` (or `follow_up_answer` if
   `status == 'awaiting_follow_up'`), clear `draft_text`.
2. Call `llm.score_answer`.
3. If `status: "follow_up"` — store `follow_up_question`, set
   `follow_up_used = true`, set session status to `'awaiting_follow_up'`,
   return:
   ```json
   {"status": "follow_up", "question": "One more — ..."}
   ```
4. If `status: "complete"` — store `score`, `feedback`; set session status
   `'complete'` and `ended_at`; update the card's `last_score` and
   `mastery_summary`; run `apply_sm2` and persist the new
   `ease_factor`/`interval_days`/`repetitions`/`next_review_at`. Return:
   ```json
   {
     "status": "complete",
     "score": 3,
     "feedback": "Good on ring mechanics...",
     "next_review_at": "2026-07-27",
     "interval_days": 3
   }
   ```

Steps 1–4 for the complete case must be a **single transaction** — a
partial write here (answer saved, SM-2 not applied) leaves a card
permanently stuck.

Return 409 if the session is already `'complete'`.

### `POST /device-tokens`

```json
{"token": "abc123...", "kind": "apns"}
```

Upsert on token. Returns 204.

### `GET /settings` / `PUT /settings`

```json
{
  "reviews_per_day": 2,
  "timezone": "America/Los_Angeles",
  "windows": [
    {"label": "Morning", "from": "07:10", "to": "08:30", "on": true},
    {"label": "Evening", "from": "21:00", "to": "22:30", "on": true}
  ]
}
```

### `POST /internal/trigger-review`

Requires `X-Cron-Secret`. Called by GitHub Actions on a schedule.

Behavior:
1. Check current time (in the configured timezone) against enabled
   windows. If outside all of them, return `{"sent": false, "reason":
   "outside_window"}` and do nothing.
2. Check how many pushes have already been sent today against
   `reviews_per_day`. If at limit, return `{"sent": false, "reason":
   "daily_limit"}`.
3. Query due conversational cards. If none, return `{"sent": false,
   "reason": "nothing_due"}`.
4. Send one APNs push naming the top due topic and the count — e.g.
   title `"3 due"`, body `"Consistent hashing"`. Payload includes the
   card id so the client can deep-link straight into that session.
5. Return `{"sent": true, "card_id": "...", "due_count": 3}`.

**Do not call Claude in this endpoint.** Generating a question for a push
that may never be opened wastes tokens and latency. Question generation
happens in `POST /cards/{id}/sessions`, on actual engagement.

### `POST /internal/check-missed`

Requires `X-Cron-Secret`. Runs a few times a day.

For any card that was pushed more than 4 hours ago with no session started
since, increment `missed_count`. **Never modify `ease_factor` here.**
Track `last_pushed_at` on the card to make this query possible.

### `GET /health`

No auth. Returns `{"status": "ok"}` plus a DB connectivity check. Used by
Fly.io health checks.

---

## Seeding

Include a management script that loads `cards.json` (the 54-card,
nine-teaching-week recall spine documented in `docs/CURRICULUM.md`) into the
`cards` table. The manifest carries source and activation metadata for
auditability; the runtime stores only the card fields above. The script must set
`delivery_mode` by category:

- `'desk'` — Coding Warmup, Coding Pattern, Tier 2 Practical Build
- `'conversational'` — everything else (Core Concept, Key Technology,
  System Design Pattern, System Design Problem, Company-Specific Problem,
  Behavioral)

Add an `--activate-week N` flag that loads exactly one curriculum week and
schedules it from `--start-date`. This is the production path: lesson
completion, not the calendar, controls when a cohort enters review.

Keep `--weeks-through N` for local verification and clean-room bulk imports.
Due dates must still be staggered so a bulk import does not flood the queue.

---

## GitHub Actions cron

```yaml
# .github/workflows/trigger-review.yml
on:
  schedule:
    - cron: '10 14 * * *'   # 07:10 PT
    - cron: '0 4 * * *'     # 21:00 PT
  workflow_dispatch:
```

A single `curl` POST to `/internal/trigger-review` with the
`X-Cron-Secret` header from repo secrets. Include `workflow_dispatch` so
it can be triggered manually for testing. A second workflow on a ~4-hourly
schedule hits `/internal/check-missed`.

Note: GitHub Actions cron is best-effort and can be delayed by several
minutes under load. That's acceptable here — the endpoint validates
against the notification window itself, so a late trigger either still
lands in-window or correctly no-ops.

---

## Testing

At minimum:

- **`apply_sm2` unit tests** — first review, second review, third+
  review, failure reset, ease-factor floor at 1.3, a full multi-review
  sequence. This is the highest-value test surface in the codebase.
- **Tier classification unit tests** — one case per tier, plus the
  precedence cases: a card that qualifies as both `solid` and `cold`
  must classify as `cold`; a card with `repetitions == 0` and
  `last_score == null` must classify as `untested`, not `shaky`.
- **Follow-up flow integration test** — mock the LLM, assert that a score
  of 2 with `follow_up_used=false` returns a follow-up, and that the same
  score with `follow_up_used=true` completes.
- **Transaction integrity** — assert that an LLM failure mid-scoring
  leaves the session and card unchanged, not half-written.
- **Auth** — wrong/missing key returns 401 on both client and internal
  endpoints.

Mock all Anthropic and APNs calls in tests. No live API calls in CI.

---

## Operational notes

- Log every LLM call with token counts and latency — cheap to add now,
  and it's the only thing that will meaningfully affect cost.
- Fly.io: single machine, `min_machines_running = 0` is fine. Cold starts
  are acceptable because the cron trigger tolerates latency and the user
  taps into the app seconds after a push, not milliseconds.
- Neon scales to zero; expect a cold-start delay on the first query after
  idle. Set a generous connection timeout.
- No rate limiting needed (one user). No CORS needed (native client only).

---

## Study Plan

Study Plan is a later, separately authorised feature and its authoritative
specification is **`docs/STUDY-PLAN-SPEC.md`**. It extends this document rather
than amending it: the schema, endpoints, SM-2 rules, and LLM prompts described
above are unchanged by it.

Two properties of that extension matter from here:

- **No Study Plan operation modifies a card, a score, a session, a mastery
  summary, or any SM-2 field.** The only path that creates cards is a separately
  confirmed, gated, atomic card-proposal acceptance, and no path modifies an
  existing one. Linkage lives in `study_plan_card_links` — nothing is added to
  the `cards` table.
- **A card created that way passes the same grounding gate and starts at the same
  defaults as Capture activation.** The approved question is persisted as
  `canonical_question`, so the first review reuses it rather than generating one.

Where `docs/STUDY-PLAN-SPEC.md` and this file disagree about Study Plan, that
file wins. Where either disagrees with `AGENTS.md`'s load-bearing invariants,
`AGENTS.md` wins.

---

## Out of scope — do not build

- User accounts, registration, login, JWT, OAuth, password reset
- Multi-tenancy or any `user_id` columns
- Admin dashboard or web UI of any kind
- Streaks, XP, badges, or any gamification beyond `missed_count`
- Email, analytics, or third-party telemetry
- Rate limiting, CORS, or API versioning
- Speech-to-text — the iOS client transcribes on-device and sends text
- Retry queues, background workers, or a task queue (Celery, RQ, etc.) —
  everything here is request-scoped

Also out of scope for Study Plan (`docs/STUDY-PLAN-SPEC.md` restates these in
full): readiness scores or percentages, exact-day completion forecasts derived
from weekly capacity, calendar integrations, server-side study-block reminders,
actual study-time tracking, automatic creation of generic cards, automatic
schedule mutations, and a second review scheduler.
