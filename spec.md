# Devmax — Backend Build Spec

> **Public-app extension:** `docs/PUBLIC-APP-SPEC.md` is authoritative for
> accounts, authentication, per-user ownership, onboarding, and guide ingestion.
> The single-user statements and related out-of-scope bullets below describe the
> original release and are superseded only in those areas. This file continues to
> own review, scoring, scheduling, and the existing endpoints.

> **Approved scoring target:** `docs/SCORING-CONTRACT-V2-SPEC.md` owns the
> approved Recall-only numeric contract, qualitative-coaching boundary,
> historical compatibility rules, and staged consumer migration. The three-axis
> contract below remains the production runtime until V2's activation gate is
> complete. Do not implement a partial semantic migration.

Handoff spec for scaffolding the Devmax backend. Build exactly what's
described here. Anything not specified is out of scope — see "Out of
scope" at the bottom before adding anything.

## Context

Devmax is a single-user conversational spaced-repetition study app for
technical interview prep. A scheduled job pushes a notification; the user
opens the iOS app, gets asked a question about a concept, answers by voice
or text, gets up to two follow-up probes — the first if the answer was
partial, a second only if the model still lacks the signal to score honestly
— then gets scored 0–5. The score drives an SM-2 schedule that decides when the topic
comes back.

This backend serves that iOS client and receives a cron webhook. The original
release had exactly one user. The public-app extension adds multi-user ownership
and Sign in with Apple while preserving all scheduler and coaching behaviour
described here.

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

## Legacy auth

Two independent shared secrets, both checked in middleware:

- Client endpoints require header `X-API-Key: <API_KEY>`
- `/internal/*` endpoints require header `X-Cron-Secret: <CRON_SECRET>`

Reject with 401 and no body detail on mismatch. Use
`secrets.compare_digest` for the comparison. The shared client key is now a
migration-only founder compatibility path. New accounts use the bearer-token
contract in `docs/PUBLIC-APP-SPEC.md`; cron auth remains unchanged.

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
| `last_learning_exposure_at` | timestamptz NULL | when trusted learning authority was most recently shown |
| `recall_not_before_at` | timestamptz NULL | separate eligibility gate; never an SM-2 schedule field |
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
| `follow_up_question` | text NULL | **frozen legacy** — see `session_probes` |
| `answer_text` | text NOT NULL DEFAULT '' | first answer |
| `follow_up_answer` | text NOT NULL DEFAULT '' | **frozen legacy** — see `session_probes` |
| `draft_text` | text NOT NULL DEFAULT '' | in-progress, unsubmitted |
| `score` | smallint NULL | final score, null until complete |
| `feedback` | text NOT NULL DEFAULT '' | one-line, generated |
| `follow_up_used` | bool NOT NULL DEFAULT false | true once any probe was issued |
| `status` | text NOT NULL | `'open'` / `'awaiting_follow_up'` / `'complete'` / `'abandoned'` |
| `started_at` | timestamptz NOT NULL DEFAULT now() | |
| `ended_at` | timestamptz NULL | |

Index on `(card_id, started_at DESC)`.

The two `follow_up_*` text columns held the single probe a session could once
carry. They are **kept and frozen** by migration 0015: every historical row keeps
its own evidence and the downgrade has somewhere to put a probe back, but nothing
reads or writes them. `follow_up_used` is still written, and still means "a scored
probe was issued in this session".

A card is **resumable** if it has a session with
`status IN ('open','awaiting_follow_up')` and non-empty `draft_text`.

### `session_probes`

| column | type | notes |
|---|---|---|
| `id` | UUID PK | |
| `session_id` | UUID FK → sessions, ON DELETE CASCADE | |
| `idx` | smallint NOT NULL | 1-based probe order, `CHECK (idx >= 1)` |
| `question` | text NOT NULL | the probe as asked |
| `answer` | text NOT NULL DEFAULT '' | `''` while this probe is unanswered |
| `created_at` | timestamptz NOT NULL | |

Unique on `(session_id, idx)`.

One row per scored follow-up, written unanswered the moment its question is
issued. `status = 'awaiting_follow_up'` means "a scored probe is pending"; *which*
one is the last row, and its `answer` is empty. There is deliberately **no
`CHECK (idx <= 2)`**: the cap is one decision and it lives in one place,
`llm.MAX_SCORED_FOLLOW_UPS`, re-checked at the write site.

Every row here is scored and pre-correction by definition. That is why the coached
re-attempt and the qualitative coaching turn stay in scalar columns on `sessions`
— they happen after the correction has been stated and never reach SM-2.

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
| `windows` | jsonb NOT NULL | `[{"label":"Morning","from":"07:10","to":"08:30","on":true,"days":[1,2,3,4,5,6,7]},{"label":"Evening","from":"21:00","to":"22:30","on":true,"days":[1,2,3,4,5,6,7]}]` |
| `timezone` | text NOT NULL | `'America/Los_Angeles'` |

Seed this row in a migration.

`reviews_per_day` is the maximum number of notification pushes on one local
calendar day. It is not the number of cards that become due and it is not a
weekly review-frequency target. SM-2 remains the only scheduler for existing
cards.

Each notification window may carry `days`, a non-empty set of unique ISO weekday numbers:
`1` is Monday through `7` for Sunday. A window is eligible only when it
is on, the current local weekday is selected, and the current local time is
inside its range. Missing `days` means all seven days. That fallback is required
for rows and clients from before weekday-aware windows; writes normalize the
omission to `[1,2,3,4,5,6,7]`. The On toggle is how a window is silenced; its
weekday selection remains non-empty so enabling it restores the saved recurrence.
The app changes weekly nudge frequency by selecting or deselecting days on windows;
those choices never move `next_review_at`.
Two enabled windows that select any of the same weekdays must have distinct local
start times. The per-window idempotency boundary is that local start instant, so
accepting an equal-start collision would advertise two slots that can deliver only one.

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

### `score_answer(card, session, answer, probes) -> ScoreResult`

`probes` is the session's scored follow-ups as ordered `(question, answer)`
pairs — empty on the initial answer, and when the learner is answering probe
*k* the k-th pair carries the text they just submitted. `probes_used =
len(probes)` is what every rule below is keyed on.

Returns one of two shapes:

```json
{"status": "follow_up", "question": "One more — ..."}
{"status": "complete", "score": 3, "feedback": "...",
 "mastery_summary": "..."}
```

Rules to encode in the prompt:

- Score 0–5 on **mechanism accuracy, trade-off awareness, and failure-mode
  awareness** — not on fluency, length, or confidence.
- The model also returns `needs_more_evidence`: true only when the transcript
  cannot honestly distinguish adjacent scores and one further probe would
  settle it. It reports missing signal, not a wrong answer — a wrong essential
  account is scored, not probed.
- Two-stage follow-up policy, decided in the parser, never by the model:
  - `probes_used == 0`: if the score would be 2 or 3, return
    `status: "follow_up"` with a probe targeting the specific gap. This is the
    band rule, unchanged; a stray `needs_more_evidence` cannot widen it.
  - `0 < probes_used < MAX_SCORED_FOLLOW_UPS`: probe again only if
    `needs_more_evidence` is true.
  - `probes_used == MAX_SCORED_FOLLOW_UPS`: always `complete`.
- `follow_up_question` is a candidate, not permission to extend the session.
  A candidate is required when the server's rule grants another scored turn.
  A surplus candidate or insufficiency claim is ignored when Recall or the cap
  requires completion, and is recorded only as content-free contract telemetry.
  This preserves the otherwise valid score and answer without letting model text
  widen the band or exceed the cap.
- **Maximum `MAX_SCORED_FOLLOW_UPS` (2) scored follow-ups per session.** The cap
  is structural: both parsers enforce it and `submit_answer` re-checks it at the
  write site, so a prompt alone can never extend a session. A session may carry
  at most one further *coached re-attempt* after completion; it is a separate
  call (`llm.score_reattempt`) on a separate endpoint and never reaches SM-2.
  See `docs/multi-turn-coaching-design.md`.
- The probe's preface names the turn: "One more — " for the first,
  "Last one — " for the second.
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

Returns conversational-mode cards where `next_review_at <= today` and
`recall_not_before_at IS NULL OR recall_not_before_at <= now`, ordered by most
overdue first, then by lowest `ease_factor`.

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

Full card list for browsing. Defaults: `sort=next_review`, `mode=all`. Learning-
gated cards remain visible here for Coverage and history. Each summary includes
nullable `recall_not_before_at`; clients exclude a future value from Review
Sprint while the session endpoint remains the authoritative backstop.

### `GET /cards/{id}`

Card fields plus its session history:

```json
{
  "id": "uuid", "topic": "...", "category": "...",
  "mastery_summary": "...", "last_score": 3,
  "ease_factor": 2.36, "interval_days": 3,
  "next_review_at": "2026-07-27", "missed_count": 0,
  "recall_not_before_at": null,
  "learning_available": true,
  "source_label": "Reviewed source", "source_section": "Relevant section",
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

`learning_available` means the card has complete trusted learning authority and
no live answer currently owns the card. It is false while a session is open or
awaiting a follow-up; the learning POST rechecks this under the card lock.

Build `turns` server-side from the session row and its `session_probes` — the
client shouldn't assemble transcript ordering. The shape is `question`,
`answer`, then **0–2 `follow_up`/`answer` pairs** in `idx` order, then `score`;
the example above shows the one-probe case. Load the probes for all of a card's
sessions in one query — per-session loading is an N+1 that grows with the card's
history.

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
- `POST /cards/{id}/restore` reverses archive only when no other member of the
  full replacement lineage is active.
- `POST /cards/{id}/replace` creates a fresh blank-history card with the edited
  question, archives the predecessor, and records both lineage links. A restored
  historical member with a newer archived successor must not be replaced: doing
  so would overwrite or fork the scalar lineage. Archive, restore, and replace
  lock the immutable oldest member first, then the full lineage in deterministic
  order, so concurrent operations can never leave two related cards active.

### `POST /cards/{id}/learning`

The explicit first-exposure or repair path. This endpoint performs no model call
and returns no canonical question. It locks the owned active card, refuses to
expose an answer while a live session exists, validates trusted authority, writes
the exposure boundary, commits, and only then returns:

```json
{
  "card_id": "uuid",
  "topic": "Consistent hashing",
  "category": "Core Concept",
  "source_label": "Reviewed source",
  "source_section": "Virtual nodes",
  "source_url": "https://example.com/source",
  "source_excerpt": "",
  "core_explanation": "...",
  "essential_account": "...",
  "acceptable_alternative": "...",
  "depth_extension": "...",
  "boundary_extension": "...",
  "misconception": "...",
  "recall_available_at": "2026-08-12T07:00:00Z"
}
```

The response normalizes either stored rubric vocabulary into the stable learning
shape. The core explanation uses `answer_basis`, falling back to an imported
`answer_anchor`; `source_excerpt` may supplement it. Missing authority fails
closed. `recall_available_at` is the later of the start of the next day in the
user's configured timezone and eight hours after exposure. Reopening material
records a new exposure and extends the boundary from that moment. This keeps the
minimum separation honest when the learner revisits the answer near expiry.

Learning changes no score, session, mastery summary, or SM-2 field. The card is
withheld from due, push, and Review Sprint until the gate expires. A direct
normal or `practice=true` session start must also reject it before question
generation or usage accounting.

### `POST /cards/{id}/sessions`

Called when the user taps into a card to start reviewing — **not** when
the push fires.

Behavior:
- Reject an archived card.
- Reject a card whose learning exposure gate has not expired, for scheduled and
  practice sessions alike.
- If an existing session for this card has status `'open'` or
  `'awaiting_follow_up'` **in the requested scheduled/practice mode**, return that
  session instead of creating a new one (this is what makes resume work). Reject
  an opposite-mode request with `409 session_mode_conflict`; silently crossing
  that boundary would change whether submission moves SM-2.
- Otherwise generate a question via `llm.generate_question`, create a
  session with `status: 'open'`, return it.
- Supply the card's trusted answer basis and rubric to question generation.
- Generate outside the card lock, then compare every generation input after the
  final card lock. If grounding or other question context changed during the
  provider call, account for and discard that result and retry once from the
  fresh authority; never persist a canonical question built from stale inputs.

```json
{
  "session_id": "uuid",
  "question": "You're adding a node to a consistent-hashing ring...",
  "is_follow_up": false,
  "turn_index": 0,
  "draft_text": "",
  "resumed": false,
  "practice": false
}
```

When resuming, `draft_text` carries the saved partial answer and
`resumed: true`. When the resumed session is `'awaiting_follow_up'`,
`is_follow_up` is true and `question` is the **pending probe's** question — the
last `session_probes` row, the one still unanswered — not the card's opening
question and not an earlier probe. `turn_index` is `0` for the opening answer
and the pending probe's 1-based `idx` thereafter. It is the session-scoped turn
coordinate the client echoes on draft and answer writes.

### `PATCH /sessions/{id}/draft`

```json
{"draft_text": "so the key space is a ring of hashes and...", "turn_index": 0}
```

Persists an in-progress answer. The client calls this periodically while
recording or typing (debounced, roughly every few seconds). Returns 204.
This is what makes the "you were mid-answer" resume state possible after
the app is backgrounded — losing a spoken answer is the worst failure
mode in the product, so this endpoint should be cheap, idempotent, and
never blocked behind anything slow. The update is conditional on `turn_index`
still being current and is otherwise an acknowledged no-op. This condition is
checked in the `UPDATE` itself, after a separate ownership-filtered lock of only
the session row. `/answers` takes that same short row barrier immediately before
its transcript/probe write, after every provider call; `/draft` never locks the
card. The draft's conditional `UPDATE` is a fresh statement after the barrier,
so Postgres cannot retain a command-start view where probe N was unanswered and
repopulate turn N+1 after the answer commit. `turn_index` is optional only for
compatibility with older clients; their writes are accepted only while the
session remains live. Durable server drafts are a scored, pre-correction recovery
path. Re-attempt and qualitative-coaching turns happen after the session is
complete and have no server reopen path, so a draft PATCH in those states is
intentionally a 204 no-op; the client keeps only its contextual local
crash-recovery copy.

### `POST /sessions/{id}/abandon`

Explicitly changes a live `open` or `awaiting_follow_up` session to `abandoned`
and records `ended_at`. It preserves the draft and every previously submitted
turn for recovery, writes no score, and changes no card or SM-2 field. The
transition is idempotent for an already-abandoned session and rejects a completed
session. It takes the same card lock as answer submission, so an abandon/submit
race has one winner rather than scoring an abandoned answer. Returns 204.

### `POST /sessions/{id}/answers`

```json
{"text": "so the key space is a ring of hashes...", "turn_index": 0}
```

Behavior:
1. Reject empty or whitespace-only text with 422 before consent, provider work,
   or state mutation. Empty string is the durable unanswered-probe sentinel.
2. Load the session's probes in `idx` order. The answer belongs to
   `answer_text` on the first turn, and to the pending probe's `answer`
   (the last row, still empty) once `status == 'awaiting_follow_up'`.
   Clear `draft_text`.
3. Call `llm.score_answer` with the probe pairs, **before writing anything**.
   A retry carries the same `turn_index`; session id plus turn index identifies
   the submitted turn, and stored-text equality proves that key was not reused
   with different content. A committed retry returns the pending probe or
   terminal result without another model call. This is intentionally not
   text-only: the same spoken answer is valid evidence on adjacent questions.
4. If `status: "follow_up"` — insert a `session_probes` row at
   `idx = len(probes) + 1` holding the new question, set `follow_up_used = true`,
   set session status to `'awaiting_follow_up'`, return:
   ```json
   {"status": "follow_up", "question": "One more — ...", "turn_index": 1}
   ```
   Refuse a follow-up returned at `MAX_SCORED_FOLLOW_UPS`: the parsers already
   do, and the write site checks again so the cap survives a parser bug.
5. If `status: "complete"` — store `score`, `feedback`; set session status
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

Steps 2–5 for the complete case must be a **single transaction** — a
partial write here (answer saved, SM-2 not applied) leaves a card
permanently stuck. The follow-up case is one transaction too: the answered
probe (or `answer_text`), the new probe row, and the session status commit
together, so a session can never be `'awaiting_follow_up'` with no pending
probe to answer.

An exact turn-aware retry of the answer that completed the card returns the same
terminal shape without rescoring or rescheduling, but only while this session is
still the card's latest review and its current schedule fields can reconstruct
that response. Return 409 for a different turn, changed text under the same turn
index, a terminal request from a pre-index client, or a result superseded by a
later review.

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
    {"label": "Morning", "from": "07:10", "to": "08:30", "on": true,
     "days": [1, 3, 5]},
    {"label": "Evening", "from": "21:00", "to": "22:30", "on": true,
     "days": [2, 4]}
  ]
}
```

The window `days` are the nudge schedule. They do not make cards due, promise a
push on every selected day, or alter the SM-2 interval. A selected window with no
due conversational card remains quiet. `reviews_per_day` is a daily safety cap
across all eligible windows. The UI's honest weekly maximum is:

```text
sum(min(reviews_per_day, enabled windows selecting that ISO day) for day in 1...7)
```

This is a maximum, not a promise: due-only selection may deliver fewer or none.
`PUT /settings` rejects a window shorter than 30 minutes and equal local start
times on intersecting enabled weekdays.

### `POST /internal/trigger-review`

Requires `X-Cron-Secret`. Called by GitHub Actions on a schedule.

Behavior:
1. In the configured timezone, check the current ISO weekday and local time
   against enabled windows. A window with missing `days` is eligible every day.
   If a configured start falls in a spring-forward gap, its guard boundary is the
   first real local minute after the gap. If the entire range is nonexistent, the
   range resumes there for its configured wall-clock duration. During a fall-back
   fold, both occurrences share the first occurrence's boundary and therefore remain one window.
   If no window selects today and contains the current time, return
   `{"sent": false, "reason": "outside_window"}` and do nothing.
   Evaluation takes the user's deletion-conflicting boundary first, then holds that
   user's settings-row lock through delivery. Concurrent polls cannot spend the same
   account window on two different cards, account deletion cannot invert the child-row
   lock order, and other users remain independent.
2. Check how many pushes have already been sent today against
   `reviews_per_day`. If at limit, return `{"sent": false, "reason":
   "daily_limit"}`.
3. Query due conversational cards. The window schedule never creates due work.
   If none, return `{"sent": false,
   "reason": "nothing_due"}`.
4. Send at most one APNs push for this eligible window, naming the top due topic
   and the count — e.g.
   title `"3 due"`, body `"Consistent hashing"`. Payload includes the
   card id so the client can deep-link straight into that session.
5. Return `{"sent": true, "card_id": "...", "due_count": 3}`.

**Do not call Claude in this endpoint.** Generating a question for a push
that may never be opened wastes tokens and latency. Question generation
happens in `POST /cards/{id}/sessions`, on actual engagement.

### `POST /internal/check-missed`

Requires `X-Cron-Secret`. Runs a few times a day.

For any card that was pushed more than 4 hours ago with neither a session started
nor a learning exposure since, increment `missed_count`. **Never modify
`ease_factor` here.**
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
  of 2 on the initial answer returns a follow-up, that a second probe is
  issued only on `needs_more_evidence`, that the third scored turn always
  completes, and that a follow-up returned at the cap is refused with the
  session and card unchanged.
- **Transaction integrity** — assert that an LLM failure mid-scoring
  leaves the session and card unchanged, not half-written.
- **Auth** — wrong/missing key returns 401 on both client and internal
  endpoints.
- **Notification windows** — selected and unselected ISO weekdays,
  missing `days` as every day, invalid/duplicate/empty lists, timezone
  and DST boundaries, one push per eligible window, the daily cap across
  windows, and due-only silence on a selected day.

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

- Admin dashboard or web UI of any kind
- Streaks, XP, badges, or any gamification beyond `missed_count`
- Email, analytics, or third-party telemetry
- CORS or API versioning
- Speech-to-text — the iOS client transcribes on-device and sends text
- Retry queues, background workers, or a task queue (Celery, RQ, etc.) —
  everything here is request-scoped

Also out of scope for Study Plan (`docs/STUDY-PLAN-SPEC.md` restates these in
full): readiness scores or percentages, exact-day completion forecasts derived
from weekly capacity, calendar integrations, server-side study-block reminders,
actual study-time tracking, automatic creation of generic cards, automatic
schedule mutations, and a second review scheduler.
