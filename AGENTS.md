# Warm Cache — Project Context (Agent Guide)

This file orients you to the whole project. Read it before touching anything; read
`spec.md` before touching the backend and `design_handoff_warmcache_initial/README.md`
before touching the iOS client.

## What this is

A **private, single-user conversational spaced-repetition coach** for technical-interview
prep. A push arrives, you answer a question by voice or text, the model asks at most one
clarifying follow-up if the answer was shaky, then scores recall 0–5 and reschedules the
card with SM-2.

Sessions are **1–3 minutes, used half-awake or in line.** Every decision optimizes for
*speed into a session* and *honesty of signal* — never engagement mechanics. There are no
streaks, no XP, no badges, and no celebration animations. Do not add them.

**Status:** backend and iOS client both built and verified. The backend has 45 passing
tests against a real ASGI app; the iOS client has been compared state-by-state against the
design screenshots in a 390×844 simulator. Not yet deployed, and not yet run against a real
Neon database or a live Anthropic key.

## The two source documents (both authoritative)

| Document | Owns |
|---|---|
| `spec.md` | The backend: schema, endpoints, SM-2, LLM prompt rules, and an explicit out-of-scope list. It says "build exactly what's described here" — take that literally. |
| `design_handoff_warmcache_initial/` | The iOS client: final tokens, type, copy, motion, and 18 state screenshots, plus an HTML prototype used as a *design reference, not code to lift*. |

**Where they disagree, `spec.md` wins.** The handoff's "Network expectations" section was a
sketch written before the backend existed. Every delta is already resolved in one place —
`ios/WarmCache/Services/APIClient.swift` — so no view knows about the mismatch. If you find
a new one, resolve it there, not in a screen.

## Load-bearing invariants

Break any of these and the product is subtly wrong in a way tests won't always catch.

- **`missed_count` never touches `ease_factor`.** Missing a review is a *compliance* signal,
  not a *retention* signal. Conflating them means a busy week at work trashes the ease factor
  on topics the user knows cold, and the scheduler then over-drills the wrong things.
- **Score 2 fails SM-2; score 3 passes.** Both trigger a follow-up in the app. These are two
  independent thresholds — do not collapse them into one constant.
- **Maximum one follow-up per session, enforced server-side.** The model always writes a
  probe and returns a provisional score; `submit_answer` decides whether to use it based on
  `follow_up_used`. This is structural, not prompt-dependent — keep it that way.
- **`delivery_mode: 'desk'` cards never reach `/cards/due` and never trigger a push.** Coding
  problems need a keyboard and an hour, not a two-minute voice session.
- **The complete-answer path is a single transaction.** A partial write — answer saved, SM-2
  not applied — leaves a card permanently stuck. Score *before* writing anything so an LLM
  failure leaves session and card untouched.
- **Never call Claude from `/internal/trigger-review`.** Generating a question for a push
  that may never be opened wastes tokens and latency. Question generation happens on
  engagement, in `POST /cards/{id}/sessions`.
- **Losing a spoken answer is the worst failure mode in the product.** `PATCH /sessions/{id}/draft`
  must stay cheap, idempotent, and never blocked behind anything slow. On the client, disk is
  the source of truth for instant rehydration; the server draft is the durable backup.

## Design fidelity rules

Colors, type, spacing, radii, and **copy are final.** When something looks off, read the
prototype source (`design_handoff_warmcache_initial/prototype/Warm Cache.dc.html`) for the
exact value rather than eyeballing the PNG — that is how the real fidelity bugs were found.

- **Motion is exactly four animations**: `wcFade`, `wcSettle`, `wcPulse`, and the 3-dot
  scoring indicator. No springs, no shimmer, no celebratory motion. The loading skeleton is
  static blocks.
- **Mono metadata is not blanket-uppercased.** Only the category tag is uppercased by style;
  every other mono string is written in the case the design shows it in, and several are
  deliberately lowercase (`3 days overdue`, `2 shaky · 1 cold`).
- **Accent `#57b6c2` is for primary buttons, score/status indicators, the recording ring, and
  the caret. Nothing else.**
- **Score color is never the only signal** — the numeral is always present; the dot is
  decorative reinforcement.
- **Dark mode only.** Light mode is in scope for the product but not designed yet.
- Today's mastery bands (`cold/shaky/solid/unrated`) are **not** the backend's
  `/cards/overview` tiers (`untested/shaky/developing/solid/cold`). Different vocabularies
  answering different questions — do not merge them. `overview` has no screen in this design
  and is intentionally unconsumed.

## Repo map

```
warmcache/
├── spec.md                          # Backend build spec — authoritative
├── design_handoff_warmcache_initial/
│   ├── README.md                    # Design handoff — authoritative for iOS
│   ├── prototype/                   # HTML reference (read for exact values, don't lift)
│   └── screenshots/                 # 18 states; the fidelity bar
├── api/                             # Python 3.12 / FastAPI / SQLModel / Neon / Fly.io
│   ├── app/services/scheduler.py    # SM-2 — pure, the highest-value test surface
│   ├── app/services/llm.py          # Question gen + scoring (Anthropic)
│   ├── app/services/cards.py        # due_label, tier classification, turn assembly
│   ├── app/routers/                 # cards, sessions, devices, settings, internal
│   └── app/seed.py                  # --fixtures (design cards) or --file cards.json
├── ios/                             # SwiftUI; `xcodegen generate` makes the gitignored project
│   ├── WarmCache/Design/            # Theme, Typography, Motion, ScoreStyle — tokens live here
│   ├── WarmCache/Services/          # APIClient, MockAPI, Speech, Speaker, DraftStore
│   └── WarmCache/Screens/           # Today, Conversation, History
└── .github/workflows/               # trigger-review + check-missed cron
```

## Working here

```sh
# Backend
cd api && uv sync && uv run pytest -q && uv run ruff check app tests
uv run uvicorn app.main:app --reload --port 8083     # 8083 per the ~/dev port contract

# iOS
cd ios && xcodegen generate
xcodebuild -project WarmCache.xcodeproj -scheme WarmCache \
  -destination 'platform=iOS Simulator,name=iPhone 16e' build
```

Use a **390×844** simulator (iPhone 16e / 14 / 15) — that's the design frame.

### Verifying a UI change

Debug builds run on `MockAPI` fixtures, so every screen works with no server. The prototype's
Tweaks are launch environment variables, so any state — including failure paths — is one
command away:

```sh
xcrun simctl launch --setenv WC_ROUTE=submit-failure --setenv WC_FAIL_SUBMIT=1 \
  <device> com.christrinh.warmcache
```

`WC_ROUTE`: `question` `recording` `text` `followup` `score` `resume` `submit-failure`
`history` `history-empty` `settings` `add` `filter`. Also `WC_LOAD` (`auto|loading|error`)
and boolean `WC_EMPTY` `WC_FAIL_SUBMIT` `WC_FAIL_ADD` `WC_TEXT_FIRST` `WC_TTS`
`WC_SIM_SPEECH`. Forced failures succeed on the retry, so each path walks end to end.

**Screenshot the state and compare it to its PNG in `screenshots/` before calling a UI change
done.** That is the acceptance test.

### Before writing LLM code

Load the `claude-api` skill. Model IDs, structured-output shape, and prompt-caching rules
change faster than this file does — do not write Anthropic calls from memory.

## Known gaps

- **`cards.json` is missing.** `spec.md` §Seeding says the 111-card study plan is "already
  generated," but it isn't in the repo. `app/seed.py` implements the documented contract;
  until the file lands, `--fixtures` seeds the cards every screenshot depicts.
- **Never run against real Postgres.** The migration's JSONB column and partial index have
  only been exercised as SQLite variants in tests. Apply `alembic upgrade head` to a Neon
  branch before trusting deployment.
- **No live Anthropic or APNs call has been made.** Both are mocked everywhere.

## Out of scope — do not build

Everything in `spec.md` §"Out of scope" (accounts, multi-tenancy, admin UI, gamification,
analytics, rate limiting, CORS, API versioning, server-side STT, task queues), plus light
mode, onboarding, and any motion beyond the four specified animations.
