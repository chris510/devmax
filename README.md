# Warm Cache

A private, single-user conversational spaced-repetition coach for technical-interview prep.
A push arrives, you answer a question by voice or text, the model probes once if the answer
was partial, then scores recall 0–5 and reschedules the card with SM-2. Sessions are 1–3
minutes.

Three screens — **Today**, **Conversation**, **Card History**. No tab bar, no onboarding,
no auth UI, no gamification.

## Layout

```
api/     FastAPI + SQLModel on Postgres (Neon), deployed to Fly.io — built to spec.md
ios/     SwiftUI app — built to design_handoff_warmcache_initial/
.github/ Cron workflows that drive the push loop
```

Source documents, both authoritative:

- `spec.md` — the backend build spec (schema, endpoints, SM-2, LLM rules, out-of-scope list).
- `design_handoff_warmcache_initial/` — the iOS design handoff: final tokens, type, copy,
  motion, and 18 state screenshots, plus an HTML prototype used as a design reference.

Where the two disagree, `spec.md` wins; the deltas are resolved once in `ios/WarmCache/Services/APIClient.swift`.

## Backend

```sh
cd api
cp .env.example .env          # fill in DATABASE_URL, API_KEY, CRON_SECRET, ANTHROPIC_API_KEY
uv sync
uv run alembic upgrade head
uv run python -m app.seed --fixtures        # the three design-prototype cards + one desk card
uv run uvicorn app.main:app --reload --port 8083
```

`--fixtures` seeds exactly the cards every screenshot depicts, so the designs are
reproducible against a real server. Once `cards.json` (the 111-card study plan) lands, use
`uv run python -m app.seed --file cards.json --weeks-through 2` instead.

```sh
uv run pytest        # 45 tests; Anthropic and APNs are mocked, no live calls
uv run ruff check app tests
```

The two shared secrets are independent: client endpoints need `X-API-Key`, `/internal/*`
needs `X-Cron-Secret`.

## iOS

```sh
cd ios
xcodegen generate
open WarmCache.xcodeproj
```

Debug builds run against fixtures (`MockAPI`) so every screen works with no server; release
builds always use the real API. Point a debug build at a live server with `WC_MOCK=0`.

### Walking the designed states

The prototype's Tweaks are launch environment variables, so any state — including the
failure paths — can be reached in one command:

```sh
xcrun simctl launch --setenv WC_ROUTE=score <device> com.christrinh.warmcache
```

| Variable | Values |
|---|---|
| `WC_ROUTE` | `question` `recording` `text` `followup` `score` `resume` `submit-failure` `history` `history-empty` `settings` `add` `filter` |
| `WC_LOAD` | `auto` `loading` `error` |
| `WC_EMPTY` `WC_FAIL_SUBMIT` `WC_FAIL_ADD` `WC_TEXT_FIRST` `WC_TTS` `WC_SIM_SPEECH` | `1` / `0` |

Forced failures succeed on the retry, as in the prototype, so each failure path walks end to
end. Use a 390×844 simulator (iPhone 16e / 14 / 15) to match the design frame.

## Notes

- The app hides the system status bar because the design draws its own 44px mono status row.
- `missed_count` never touches the ease factor — missing a review is a compliance signal,
  not a retention signal.
- Only `conversational` cards enter the due queue and the push loop; `desk` cards are
  tracked and scheduled but never pushed.
