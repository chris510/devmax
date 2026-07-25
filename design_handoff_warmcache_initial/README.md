# Handoff: Warm Cache — conversational spaced-repetition study coach

## Overview

Warm Cache is a private, single-user mobile app for technical-interview prep. It pushes a
question about a concept, the user answers by voice or text, the model asks one clarifying
follow-up if the answer is shaky, then scores recall 0–5 and reschedules the card (SM-2).
Sessions are 1–3 minutes, used half-awake or in line — every decision optimizes for
*speed into a session* and *honesty of signal*, never engagement mechanics.

Three screens: **Today** (queue), **Conversation** (the core loop), **Card History**.
No tab bar, no onboarding, no auth UI, no gamification.

## About the design files

`prototype/Warm Cache.dc.html` (plus its runtime `prototype/support.js`) is a **design
reference built in HTML** — a working prototype of look, motion, and state transitions.
It is **not production code to lift**. Recreate these screens in the target codebase's
own environment (React Native / SwiftUI / React web — whatever the app actually is),
using its existing navigation, networking, and component patterns. If no app environment
exists yet, pick the framework appropriate to a single-user mobile app with push
notifications and audio capture, and implement the designs there.

Seed data, question text, scores, and feedback strings in the prototype are **fixtures**
standing in for API responses. The typing-out of the transcript is a fake stand-in for
streaming speech-to-text.

## Fidelity

**High-fidelity.** Colors, type, spacing, radii, and copy are final and should be matched
exactly. Motion is deliberately minimal (three keyframes total, listed below) — do not add
transitions, springs, shimmer, or celebratory animation beyond what's specified.

## Device frame

Designed at **390 × 844** (iPhone 14/15 logical size), dark mode first. Light mode is in
scope for the product but not designed yet; the palette below is the dark set.

---

## Design tokens

### Color

| Token | Hex | Use |
|---|---|---|
| `bg` | `#0d0f11` | App background (near-black, never pure) |
| `surface` | `#14171a` | Sheets (settings, quick-add) |
| `bubble` | `#171b1e` | User answer bubble fill |
| `bubble-border` | `#1f2427` | User answer bubble border |
| `hairline` | `#191d20` | Row dividers, section rules |
| `border` | `#21262a` | Secondary button / control borders |
| `border-strong` | `#23282c` | Sheet borders, input borders, time chips |
| `border-hover` | `#3a4248` | Secondary button hover border |
| `skeleton-1` | `#1b1f22` | Loading placeholder, primary line |
| `skeleton-2` | `#171a1d` | Loading placeholder, secondary line |
| `skeleton-3` | `#14171a` | Loading placeholder, tertiary line |
| `text` | `#e8eaec` | Primary UI text |
| `text-strong` | `#f2f4f5` | Question text |
| `text-serif` | `#dfe3e5` | Feedback / mastery summary |
| `text-secondary` | `#cfd4d8` | Answer bubbles, sheet body, error copy |
| `text-muted` | `#a8afb5` | Live (in-progress) transcript, secondary buttons |
| `text-dim` | `#949ba1` | Row mastery summary |
| `meta` | `#8b9299` / `#7c848b` | Labels, tertiary actions |
| `meta-dim` | `#6b7378` / `#5e666c` | Mono metadata |
| `meta-faint` | `#4e565b` / `#545c61` | Footnotes, transcript labels |
| `accent` | `#57b6c2` | Primary buttons, score/status indicators, recording ring, caret. **Nothing else.** |
| `accent-hover` | `#6ec6d1` | Primary button hover |
| `accent-ink` | `#06232a` | Text on accent fill |
| `accent-wash` | `rgba(87,182,194,.10)` | Selected segment / active mic fill |
| `accent-line` | `#26343a` | Resume / inline-error container border |
| `accent-surface` | `#10171a` | Resume / inline-error container fill |
| `score-low` | `#c0705a` | Scores 0–1 |
| `score-mid` | `#c2a35a` | Scores 2–3 |
| `score-high` | `#6fa77f` | Scores 4–5 |
| `score-none` | `#6b7378` | No score yet (renders as `—`) |

Score color is **never the only signal** — the numeral is always present, with the dot as
secondary reinforcement.

### Type

- **Question voice:** Newsreader (serif). Main question 25px/1.32, follow-up 21px/1.32,
  letter-spacing −.01em, `#f2f4f5`. Also used for: empty-queue line (22px/1.4), mastery
  summary in history (19px/1.45), score feedback (18.5px/1.5), quick-add sheet title (20px).
- **UI:** IBM Plex Sans. Screen title 30px/600 (−.02em) · history title 24px/600 · row topic
  16.5px/500 (−.01em) · body & buttons 15–15.5px · row summary 13.5px/1.45 · secondary
  actions 13px.
- **Metadata:** IBM Plex Mono, uppercase, 9.5–12.5px, letter-spacing .04–.12em.
  Used for: date/status line, category tags, due labels, section labels, transcript role
  labels, schedule lines, mic label, status bar.
- Body copy uses `text-wrap: pretty` everywhere it wraps.
- Nothing below 10px in mono chrome; nothing below 13px in read copy.

### Spacing, radii, misc

- Screen padding: 22px horizontal (24px inside Conversation), 14px on the Today list
  container with 8px per-row inset so rows extend visually to the edge hairline.
- Row: 15px top / 16px bottom padding, 14px gap to score column, score column 34px wide.
- Radii: primary button 13–14px · secondary button 11–13px · sheet 20px top corners ·
  answer bubble 14px · inline error / resume 12px · input 12–13px · chips 8–10px ·
  score dot & toggles 999px.
- Mic button 76px circle inside an 84px hit area; pulsing ring is a 1px accent border.
- Bottom safe area: 30px bottom padding on all fixed bottom bars.
- Status bar row: 44px tall, mono 11px, bottom-aligned.

### Motion (complete list)

| Name | Spec | Where |
|---|---|---|
| `wcFade` | 250–350ms ease, opacity 0→1 + translateY 6px→0 | new thread turn, sheets, inline errors, empty state, expanded transcript |
| `wcSettle` | 450ms cubic-bezier(.2,.7,.3,1), opacity 0→1, translateY 10px→−2px→0 | score block landing |
| `wcPulse` | 1.8s ease-out infinite, scale 1→1.45, opacity .55→0 | recording ring |
| dots | 1s infinite, opacity .25→1→.25, 180ms stagger ×3 | "SCORING" indicator (3 × 3px accent dots) |

No shimmer on the loading skeleton — static blocks only.

---

## Screen 1 — Today

**Purpose:** answer "what's due and how am I doing" in under two seconds.

**Layout:** vertical stack — status bar (44px) · header block (30px title + mono date line,
SETTINGS pill right-aligned) · scrollable row list (flex 1) · fixed bottom block (quick-add
link, then primary Start button).

**Mastery distribution line** (directly under the date line): a single mono 11px row of
bands derived from each due card's last score — `2 shaky · 1 cold` (0–1 = cold, 2–3 = shaky,
4–5 = solid, no score yet = unrated). Zero-count bands are omitted; segments wrap with 7px
column gap and are individually `white-space: nowrap`. Tapping a band filters the row list
to it: the tapped segment switches from `#7c848b` to that band's score color with a 1px
underline in the same color, and Start's count follows the filtered set. Tapping the active
band clears the filter. Not a dashboard — no percentages, no bars, no second line.
See `screenshots/today-mastery-filter.png`.

**Header date line** reads `FRI 24 JUL · <status>` where status is `CHECKING` (loading),
`OFFLINE` (fetch failed), `NOTHING DUE`, or `N CARDS DUE`.

**Row anatomy** (`screenshots/today-default.png`):
1. Topic name, 16.5px/500, with a 1px dotted `#2c3238` underline — this underline is the
   affordance for "tap for history". Hover: underline → accent, text → `#fff`.
2. Category tag, mono 10px uppercase `#6b7378`, baseline-aligned beside the topic.
3. Mastery summary, 13.5px `#949ba1`, one line of rolling signal ("solid on ring mechanics,
   shaky on virtual nodes").
4. Mono meta line, 10.5px, wrapping with 8px column gap / 3px row gap, each chip
   `white-space: nowrap`: due label · `· resumable` (accent, only when a partial answer is
   stored) · `· missed 2×` (the quiet `missed_count` indicator — never styled as a warning).
5. Score column: numeral 17px/600 tabular in the score color, 6px dot below in the same color.

**Tap targets:** the row (anywhere but the topic name) starts a Conversation with that card;
the topic name opens Card History (`stopPropagation`). A one-line mono footnote under the
list states this: `TAP A TOPIC NAME FOR ITS HISTORY` (hidden while loading/failed).

**Sort:** most-overdue / weakest first.

**Start button:** appears only when >1 card is due. Label `Start — N cards`. Accent fill,
`#06232a` text, 17px padding, 14px radius. Walks the queue in order without returning to Today.

**Quick-add affordance:** low-weight `+ Ask about something else`, 13.5px `#8b9299`, always
visible — including in the empty and error states.

### States

- **Loading** (`screenshots/today-loading.png`): three static skeleton rows matching row
  geometry — bars of height 12/10/8px at widths 58/84/34%, 46/72/34%, 63/79/34%, plus a
  12px square in the score column; mono `LOADING QUEUE` beneath. Target resolve time ≲1s;
  design assumes near-instant, so no progress affordance beyond this.
- **Load failure** (`screenshots/today-load-failure.png`): list area replaced by
  "Couldn't reach the server." (15.5px `#cfd4d8`) + mono note
  `LAST SYNCED 06:12 · 3 CARDS CACHED` + a secondary **Retry** button (1px `#21262a`, 11px
  radius). No red, no icon, no banner. Header status reads `OFFLINE`.
- **Empty** (`screenshots/today-empty.png`): serif "Nothing due until tomorrow, 7:10." then
  a mono `COMING UP` list of the next scheduled topics with times, 14px `#7c848b` /
  mono 12px `#5e666c`. Quick-add stays.

### Settings sheet (`screenshots/today-settings-sheet.png`)

Bottom sheet over a `rgba(4,5,6,.72)` scrim; tapping the scrim closes. `#14171a` fill,
20px top radius, 22px padding, 30px bottom.

- Title "Settings" 17px/600 + "Close" 13px on the right.
- **Reviews per day:** label + dynamic sub-line ("2 pushes, spread across windows"), with a
  bordered stepper (− / value / +), clamped 1–6, default 2.
- **Notification windows:** one row per window (Morning, Evening) — a 34×20px toggle
  (accent fill/knob when on, `#4a5257` knob when off), the label, and two tappable mono
  time chips `07:10 – 08:30`. Tapping a chip advances it through the allowed times
  (`06:30, 07:10, 07:45, 08:30, 12:15, 18:40, 21:00, 22:30`). Helper line: "Tap a time to
  shift the window. Reviews are pushed inside these ranges only."

Settings are reachable **only** from Today — not a destination.

### Quick-add sheet (`screenshots/today-quick-add.png`)

- Serif title "What do you want to be quizzed on?" + Close.
- Single text input, 15.5px, `#0f1214` fill, focus border → accent.
- `SCHEDULE` segmented pair: **Now — top of queue** / **Next review** (selected = accent
  border + accent wash + `#cfe9ed` text).
- Mono line `DELIVERY · CONVERSATIONAL REVIEW` — new cards default to conversational
  delivery (eligible for push/session review); category defaults to `Unsorted`.
- Primary **Add card**. In flight: label "Adding…", opacity .55, taps ignored.
- **Failure** (`screenshots/quickadd-failure.png`): sheet stays open, typed topic intact,
  inline box "Couldn't submit — the topic is still here. Try again." and the button becomes
  **Try again**. Never closes the sheet on error.
- On success the card is prepended to the queue with `lastScore: null` (renders `—` in
  `score-none`), summary "no signal yet", due label "added just now" / "queued for next review".

---

## Screen 2 — Conversation

**Purpose:** one continuous thread; no screen change per turn.

**Chrome:** a `✕` (19px, `#7c848b`) top-left returns to Today; mono right-aligned label shows
`CARD 1 OF 3` in a multi-card session, otherwise the card's category. The status bar right
slot reads `READING ALOUD` while TTS is on. No other card metadata on this screen.

**Thread** (scroll container, 24px horizontal padding, auto-scrolled to bottom on every new
turn/stage change):
- Question — serif 25px, `#f2f4f5`, top of thread (`screenshots/conversation-question.png`).
- User answer — right-aligned bubble, max-width 84%, `#171b1e` on `#1f2427`, 14px radius,
  13/15px padding, 15px/1.5 `#cfd4d8`.
- Live transcript — same position, **no bubble**, 15px/1.5 `#a8afb5` (AA-compliant on
  `#0d0f11`) with a trailing accent `▍` caret (`screenshots/conversation-recording.png`).
- Follow-up question — serif 21px, prefaced "One more — " so it reads as a probe, not a new
  card (`screenshots/conversation-followup.png`). Max **one** follow-up per session.
- Scoring indicator — mono `SCORING` + 3 pulsing accent dots, inline, left-aligned. Never a
  full-screen spinner.

**Input (bottom, fixed):**
- Voice primary: 76px circle, 1px `#2c3238` border, 18px accent dot centered. Recording:
  border → accent, fill → accent wash, dot → 20px with 4px radius (stop glyph), plus the
  pulsing ring. Mono label under it: `TAP TO ANSWER` / `LISTENING — TAP TO STOP` /
  `TAP TO KEEP GOING` (resume). Below that, a 13px `Type instead` link.
- Text path (`screenshots/conversation-text-input.png`): 3-row textarea (`#14171a`,
  focus border accent) + primary **Submit answer** + secondary **Voice**. Swapping input mode
  carries the text across (transcript → draft and back) and never navigates away.

**Session end** (`screenshots/conversation-score.png`): above a hairline —
score numeral 46px/600 tabular in the score color, mono `/ 5 RECALL`; serif 18.5px/1.5
per-session feedback (variable length, expect 1–4 lines); mono schedule line
`NEXT REVIEW · 27 JUL · INTERVAL 3D`. Actions: **Next card** (accent) + **Done** (secondary)
when more cards remain, otherwise a single **Done**; plus a centered 13px
"View history for this card". No rating prompt, no engagement prompt.

**Interruption / resume** (`screenshots/conversation-resume.png`): if a partial answer was
stored, the thread opens with a bordered accent-surface card: "You were mid-answer here 14
hours ago. Your partial answer was saved." + **Resume answer** (accent, continues the
transcript where it stopped) / **Start over** (secondary, clears it). The stored partial
renders in live-transcript styling beneath the question.

**Submit failure** (`screenshots/conversation-submit-failure.png`) — the highest-stakes
failure in the app:
1. Remove the optimistic answer from the thread, restore the text to the transcript (voice
   mode) or the draft (text mode) verbatim.
2. Rewind the stage to pre-submit (`idle`, or `follow` if the failure happened on a
   follow-up answer) so the mic/submit control is live again.
3. Show an inline strip directly above the control, same treatment as the resume banner:
   "Couldn't submit — your answer is saved." + **Try again**.
No toast, no full-screen error, no data loss. Retrying re-posts the same payload.

---

## Screen 3 — Card History (`screenshots/card-history.png`)

- Back link `← Today` (13px), then topic 24px/600, mono category tag, then the **mastery
  summary** in serif 19px/1.45 — the single most useful line, always above the fold.
- Mono meta line: `3 SESSIONS · AVG 3.0 · 3 DAYS OVERDUE`.
- Reverse-chronological session rows: score numeral 15.5px/600 in score color (16px column),
  mono date `21 JUL · 06:51`, one-line note 14px `#a8afb5`, and a ▼/▲ caret.
- Tapping a row expands **inline** (`screenshots/card-history-expanded.png`), indented 30px,
  showing the full transcript as label/value pairs: mono 9.5px labels `QUESTION` /
  `YOUR ANSWER` / `FOLLOW-UP` / `SCORE & FEEDBACK`; questions in serif 17px `#dfe3e5`,
  answers 14.5px `#a8afb5`, the score line colored by score. One row open at a time.
- **Empty** (`screenshots/card-history-empty.png`): "No sessions yet." + mono
  `FIRST REVIEW · TODAY, NEXT IN QUEUE`. Meta line reads `NEW CARD`.

---

## Navigation

Today is home. Card History is reached from a Today row's topic name, or from
"View history for this card" on the session-end state. Conversation is entered from a row
tap or Start, and exited with `✕` / Done. No tab bar. No modal stack deeper than one sheet.

---

## State model

Client state the prototype exercises (name them however the codebase prefers):

| State | Values | Notes |
|---|---|---|
| `load` | `loading` → `ready` \| `error` | Today fetch; `Retry` re-runs it |
| `screen` | `today` \| `conv` \| `history` | |
| `queue`, `qIndex` | card ids + cursor | one-card entry vs full Start session |
| `stage` | `idle` \| `rec` \| `proc` \| `follow` \| `recFollow` \| `result` | Conversation machine |
| `thread` | `[{role: 'q'\|'a', text, follow?}]` | render order = source of truth |
| `liveText` / `draft` | string | in-progress transcript / typed answer; must survive submit failure and backgrounding |
| `inputMode` | `voice` \| `text` | text carried across the swap |
| `submitError`, `retried` | bool | inline retry; cleared on new recording / next card |
| `resume` | bool | partial answer present for this card |
| `result` | `{score, feedback, schedule}` | server-scored |
| `filter` | `null` \| `cold` \| `shaky` \| `solid` \| `new` | mastery-band filter on Today |
| `sheet` | `null` \| `settings` \| `add` | |
| `addPending`, `addError` | bool | quick-add in-flight / failed |
| `perDay`, `windows[]` | int, `[{label,on,from,to}]` | settings |

### Transitions

`idle → rec` (mic) → `proc` (stop / submit) → `follow` if 2 ≤ score ≤ 3 and no follow-up
yet, else `result`. `follow → recFollow → proc → result`. Any `proc` failure →
back to `idle`/`follow` + `submitError`. `result → Next card` resets to `idle` with the next
card's question; `result → Done` → Today.

### Network expectations

- `GET /due` on app open → Today rows. Skeleton immediately; failure → error state with a
  cached-count note (surface real "last synced" data if available).
- `POST /answers` with `{cardId, transcript, isFollowUp}` → `{score, feedback, nextReview,
  followUpQuestion?}`. The client shows `SCORING` while in flight; a rejected/timed-out
  request must return the transcript to the user (see submit failure).
- `POST /cards` with `{topic, schedule: 'now'|'next', deliveryMode: 'conversational',
  category: 'Unsorted'}` → the new card. Failure keeps the sheet + input.
- Partial transcripts persist locally (app backgrounded mid-answer) and re-hydrate the resume
  banner on return.
- TTS auto-read of the question when enabled; a text-only path must always be fully usable.

## Accessibility

- Score numeral always accompanies score color; the dot is decorative reinforcement only.
- Live transcript color `#a8afb5` on `#0d0f11` clears AA — don't darken it further to
  distinguish "in progress".
- Mic button 76px (hit area 84px); all bottom actions ≥ 44px tall.
- Full text-only path for every voice interaction.

## Assets

None. No images, no icons, no SVG — the `✕`, `←`, `+`, `▼`/`▲`, `▍` glyphs are text
characters. Fonts are Google Fonts: **Newsreader** (400, 500, 400 italic) and
**IBM Plex Sans** / **IBM Plex Mono** (400, 500, 600).

## Files

- `prototype/Warm Cache.dc.html` — the prototype (all screens and states). Open in a browser.
- `prototype/support.js` — its runtime; required for the prototype to run, not part of the design.
- `screenshots/` — every state referenced above:
  `today-default`, `today-loading`, `today-load-failure`, `today-empty`,
  `today-settings-sheet`, `today-quick-add`, `quickadd-failure`,
  `conversation-question`, `conversation-recording`, `conversation-followup`,
  `conversation-score`, `conversation-text-input`, `conversation-resume`,
  `conversation-submit-failure`, `card-history`, `card-history-expanded`,
  `card-history-empty`, `today-mastery-filter`.

### Exercising the states in the prototype

The prototype exposes toggles (Tweaks): `loadState` (`auto` / `loading` / `error`),
`failSubmit`, `failAdd`, `emptyQueue`, `textFirst`, `ttsEnabled`. Forced failures succeed on
the retry so each failure path can be walked end to end. Transcripts type themselves out to
stand in for streaming STT.
