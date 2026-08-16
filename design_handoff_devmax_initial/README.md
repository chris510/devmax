# Handoff: Devmax — conversational spaced-repetition study coach

> **Approved scoring amendment:**
> [`../docs/SCORING-CONTRACT-V2-SPEC.md`](../docs/SCORING-CONTRACT-V2-SPEC.md)
> owns the target Recall-only score semantics, qualitative coaching states, and
> the list of screens that require new 390×844 references. The visual tokens,
> typography, spacing, motion limits, and all unaffected states in this handoff
> remain authoritative. The score, Coverage, Sprint, Recap, History, and
> onboarding descriptions below remain V1 references until V2 is activated.

## Overview

Devmax is a private, single-user mobile app for technical-interview prep. It pushes a
question about a concept, the user answers by voice or text, the model asks one clarifying
follow-up if the answer is shaky, then scores recall 0–5 and reschedules the card (SM-2).
Sessions are 1–3 minutes, used half-awake or in line — every decision optimizes for
*speed into a session* and *honesty of signal*, never engagement mechanics.

Three core screens: **Today** (queue), **Conversation** (the core loop), **Card History**,
plus a **Review Sprint** mode built on top of them — **Review Sprint Setup** (build a topic
set) and **Session Recap** (what a multi-card run produced). No tab bar, no onboarding, no
auth UI, no gamification.

The mode was originally scoped as "Mock Interview" and renamed: questions inside a session
are not linked to each other (no interviewer persona, no cross-topic follow-up), so
"interview" claimed more continuity than the feature delivers. Mechanism unchanged, copy only.

Review Sprint sessions run in **practice mode**: each answer is scored and written to that
card's history exactly like a normal session, but SM-2 scheduling fields (interval, next
review date) are left untouched. This is stated on screen in both new screens' footnotes and
in the session-end schedule line.

## About the design files

`prototype/Devmax.dc.html` (plus its runtime `prototype/support.js`) is a **design
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

**Review sprint button:** bordered secondary button (1px `#21262a`, 12px radius, 14px
padding, 15px `#a8afb5`), sitting between the quick-add link and `Start`. Always visible,
including when the queue is empty or the fetch failed — it draws from the whole card library,
not from what's due. Deliberately lower weight than `Start`, which stays the dominant daily
action. Opens Review Sprint Setup.

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
- **Read questions aloud:** the local preference toggle and its explanatory line.
- **Review reminders:** a destination row with the dynamic summary `Up to N/week` or
  `Off`. The dedicated editor owns recurrence and persistence; the fast sheet does not
  duplicate those controls.
- **Save changes:** persists the local read-aloud draft. **More settings** opens the full
  Settings destination.

### Review reminder editor

- One row per window (Morning, Evening): a 34×20px toggle, the label, native local start
  and end time controls, and seven compact day selectors ordered **M T W T F S S**.
  Selected days use the restrained chip treatment and remain legible without colour.
  Tapping a day changes weekly nudge frequency in the draft. A present day list must
  contain at least one selection; turn the window off to silence it while preserving
  its days.
- Enabled windows that share a selected day must use different start times. A
  conflicting start or a range under 30 minutes shows an inline error and disables
  Save. The footer has explicit **Cancel** and **Save changes** actions; a failed write
  leaves the draft visible for retry. Onboarding offers the same weekday recurrence with
  compact time chips and advances only after the server confirms the write.
- The summary reads `Up to N reminders per week · due cards only`. For each ISO day,
  count enabled windows selecting it, cap that count by the normalized daily value,
  then sum all seven days. The compatibility wire field `reviews_per_day` is normalized
  to the enabled-window count within its supported 1–6 range (minimum one when all are
  off). It never controls how many cards are due or changes a card's spaced-repetition
  interval.

Window days use ISO weekday numbers on the wire (`1 = Monday` … `7 = Sunday`). A
missing `days` field renders as all seven selected so a pre-weekday settings row remains
an everyday schedule. This compatibility fallback must not be presented as an inferred
recommendation.

The fast sheet is reachable only from Today. The Review reminder editor is reachable
from that sheet and from full Settings.

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

**Chrome:** a `✕` (19px, `#7c848b`) top-left returns to Today; the mono right-aligned label
shows the **current card's topic**, uppercase, in a multi-card session (`MVCC IN POSTGRES`),
otherwise the card's category.

**Topic progress rail** (`screenshots/conversation-rail-dots.png`): whenever a session has
more than one card, a second row sits directly beneath the chrome (2px top / 10px bottom
padding, 24px horizontal, 8px gap) — the `✕` row is untouched, the rail is glanceable
secondary information, not navigation. This replaces the old `CARD 2 OF 3` label and applies
to any multi-card session, not just Review Sprint.

- **Dot stepper (shipped option).** One dot per card: not yet reached = 7px hollow ring,
  1px `#21262a`; current = 9px filled `accent`; covered = 7px filled in that card's score
  colour (`score-low`/`mid`/`high`). The current topic's name carries the literal
  information in the chrome slot, so the rail itself can stay abstract.
- **Label chips (alternative, prototyped)** (`screenshots/conversation-rail-chips.png`):
  a horizontally scrollable single-line strip, mono 9.5px uppercase chips, 5/8px padding,
  8px radius, truncated to 15 characters + `…`; same three-state colour treatment, current
  chip `accent` border + accent wash and auto-scrolled to centre. More informative, visibly
  busier at 6–10 items. Toggle between the two with the `railStyle` tweak.

**Decision:** ship the **dot stepper**. Both are built, so the chip variant stays behind
`railStyle` for side-by-side comparison, but `dots` is the default and the one to build —
it reads at a glance and matches the app's one-quiet-line preference. Drop the chip path if
it hasn't won the argument by build time.

Rail state changes (hollow → accent → score colour) use `wcFade`. Dots/chips are **not
tappable** in this pass. Because Review Sprint reuses this screen unchanged, its interruption
and failure states — resume-partial-answer banner, text-input fallback, submit failure with
the transcript preserved — apply in a sprint run exactly as in a daily session; none of them
are special-cased on practice mode (verified in the prototype). The status bar right
slot reads `READING ALOUD` while TTS is on. No other card metadata on this screen.

**Thread** (scroll container, 24px horizontal padding, auto-scrolled to bottom on every new
turn/stage change):
- Question — serif 25px, `#f2f4f5`, top of thread (`screenshots/conversation-question.png`).
- User answer — right-aligned bubble, max-width 84%, `#171b1e` on `#1f2427`, 14px radius,
  13/15px padding, 15px/1.5 `#cfd4d8`.
- Live transcript — same position, **no bubble**, 15px/1.5 `#a8afb5` (AA-compliant on
  `#0d0f11`) with a trailing accent `▍` caret (`screenshots/conversation-recording.png`).
- Follow-up question — serif 21px, prefaced "One more — " so it reads as a probe, not a new
  card (`screenshots/conversation-followup.png`). Max **two** follow-ups per session; the
  second is prefaced "Last one — " and is asked only when the model still lacks the signal
  to score.
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
  `CHOOSE THE HONEST NEXT STEP`. Meta line reads `NEW CARD`. A grounded card has
  an accent **Learn this card** action. When it is still in Today's due queue, a
  secondary **Review now — I already know it** action opens Conversation. The
  review action is withheld for non-due or learning-gated cards, so History
  cannot become an unscheduled review launcher.
- A due card with history puts closed-book **Review now** first and keeps
  **Study source again** secondary and low-weight. A non-due card may show only
  the Study action. Choosing it intentionally creates the same recall delay as
  first exposure; it never starts a scored turn.

### Learn Card (`screenshots/card-learning.png`)

This is an explicit answer-exposure state, reached only by choosing Learn from
Card History. The server records the exposure boundary before the screen receives
trusted material.

- Header: `← Card` and the topic at 23px/600 with category metadata.
- Reuse Study Plan's labelled `Block` rhythm. In order: `START HERE` (source
  label, precise section, and tappable source when present), `CORE EXPLANATION`,
  then only the non-empty teaching fields `ESSENTIAL IDEA`, `VALID ALTERNATIVE`,
  `GO DEEPER`, `BOUNDARY OR FAILURE`, and `COMMON TRAP`.
- The exact canonical review question is absent. This screen teaches the model
  of the answer; it does not train recognition of the stable retrieval cue.
- Footer trust line: `SCORED REVIEW AVAILABLE <DATE/TIME> · YOUR SCHEDULE WASN'T
  CHANGED`, followed by **Done for now**. There is no answer control and no route
  directly from this screen into Conversation.
- Each labelled learning section is a VoiceOver heading so the long lesson can
  be traversed with the rotor.
- Loading and transport failure are static and retryable; no shimmer and no new
  motion. A stale `409` availability conflict instead says the card changed and
  returns to Card History for the current action rather than retrying in a loop.

---

## Screen 4 — Review Sprint Setup (`screenshots/sprint-setup-default.png`)

**Purpose:** build the topic set before starting, closer to "tap Start" than "fill in a form".
The suggested set is already built when the screen opens; everything on it is optional
refinement.

**Header:** `← Today` back link (13px, same as Card History), title `Review sprint`
30px/600 −.02em, and a mono status line beneath it — `13 CARDS IN LIBRARY`,
`N CARDS IN FILTER` when categories are selected, `CHECKING` while loading, `OFFLINE` on
failure.

**Controls, in order:**
1. **Category filter** — one chip per category (9 total), mono 10px uppercase, 6/10/7px
   padding, 9px radius, multi-select. Selected = `accent` border + `accent-wash` fill +
   `#cfe9ed` label, matching the Quick Add schedule segments. No selection = full library.
   Each chip carries a **tier-count annotation** on a second line, mono 9.5px `meta-faint`
   (`#8fc7cf` when the chip is selected) — the category's most urgent count, in priority
   order: `N shaky` (cold + shaky), else `N untested`, else `N developing`, else
   `N solid`. Same category-grouped tier data that powers Coverage, so weak-area targeting
   is visible where it gets acted on.
2. **Session size** — the bordered − / value / + stepper component
   (bordered − / value / +), range 4–10, default 6. Sub-line: "Weakest and least recently
   reviewed first".
3. **Shuffle** — 13px `#8b9299` text action, right-aligned opposite the mono `WALK ORDER`
   label. Regenerates the set from the current filter and size; instant, no loading state.

**Suggested set:** the pool is ranked weakest-score-first, then least-recently-reviewed;
unrated cards sort as weakest. The top `size + 4` are shuffled and `size` are taken, then
re-sorted back into rank order so the walk always opens on the weakest card.

**Topic preview list:** Today's row anatomy minus the meta line — topic 16.5px/500, mono
category tag, mastery summary 13.5px `#949ba1`, score column (numeral + dot). Rows are
**not tappable** here; this is a preview, not a queue. Removing or reordering a single card
is out of scope for this pass and is the first thing worth adding if auto-suggestion turns
out to need correction often.

**Link to Coverage:** a low-weight 13px `meta` text action beneath the preview list,
`View full coverage →`, opening Screen 6. The only entry point to Coverage in this pass —
this is the moment someone is already thinking in category gaps.

**Trust footnote**, mono 10.5px `#4e565b`, directly above the button:
`PRACTICE MODE · WON'T CHANGE YOUR REVIEW SCHEDULE`

**Primary button:** `Start — N cards`, accent fill, identical to Today's Start.

### States

- **Loading** (`screenshots/sprint-setup-loading.png`): Today's skeleton rows (3), same bar
  geometry, mono `LOADING CARDS` beneath. No shimmer. Controls stay visible and live.
- **Empty** (`screenshots/sprint-setup-empty.png`): when the filtered pool holds fewer than 4
  cards (the session-size minimum) — serif 20px "Not enough cards in these categories yet."
  The chips stay live so the selection can be widened immediately; no separate clear button,
  and the Start button is withheld.
- **Load failure** (`screenshots/sprint-setup-load-failure.png`): Today's `LoadFailure`
  pattern verbatim — "Couldn't reach the server." + mono `LAST SYNCED 06:12 · 3 CARDS CACHED`
  + secondary **Retry**. No red, no icon. Status line reads `OFFLINE`.

---

## Screen 5 — Session Recap (`screenshots/session-recap.png`)

**Purpose:** replaces the silent "last card finishes → back to Today". Shown once, after the
final card of any multi-card session is scored. **The transition is a manual tap, not an
automatic screen swap:** on every card except the last the session-end button reads
**Next card**; on the last card of a run that same button reads **See recap** (a single-card
session keeps **Done**).

- **Title:** serif 24px `#f2f4f5` "Session recap", in Card History's title position.
- **Aggregate score block:** the single-card score block treatment — 46px/600 tabular numeral
  in the colour of the rounded average's band, mono 13px `/ 5 AVERAGE` beside it. No new
  number presentation, no chart.
- **Per-topic results:** Setup's row anatomy with this run's scores — topic, mono category,
  score numeral, ▼ caret. Tapping expands inline to that card's feedback in serif 17px,
  reusing Card History's accordion behaviour exactly, one row open at a time
  (`screenshots/session-recap-expanded.png`).
- **Trust footnote:** `PRACTICE MODE · SCORES SAVED TO HISTORY, SCHEDULE UNCHANGED`
- **Actions:** primary **Done** (accent, returns to Today); centred 13px **Run another**
  below it, which returns to Setup with the filter and session size preserved.
- **Copy:** feedback keeps the scoring rubric's tone — specific, never congratulatory. No
  celebration, no streaks, no share, no confetti.

During a Review Sprint the session-end schedule line reads
`PRACTICE MODE · SCHEDULE UNCHANGED` in place of `NEXT REVIEW · … · INTERVAL …`.

---

## Screen 6 — Coverage (`screenshots/coverage.png`)

**Purpose:** a standing, category-grouped view of mastery across the whole library — for
deciding where the study guide needs more cards, fewer cards, or rebalancing. Not a daily
habit screen. Reached only from `View full coverage →` on Review Sprint Setup; add a second
entry point later only if people ask for it.

**Read-only.** It surfaces the gap, it doesn't fix it. Card authoring stays in Quick Add
(one at a time) or the seed data (bulk) — no card-authoring UI here.

**Header:** `← Review sprint` back link (13px), title `Coverage` 30px/600, mono status line
`13 CARDS · 9 CATEGORIES` (`CHECKING` / `OFFLINE` in the other states).

**Axis rollup:** one mono line directly beneath the status line — `MECHANISM 4.1 ·
TRADE-OFFS 2.8 · FAILURE MODES 3.2` — mono 10.5px `meta-dim`, letter-spacing .06em, each
value the mean of that axis across every scored card in the library. Scoring runs on three
axes internally; this is the only place that decomposition surfaces, because "which axis is
systemically weak" is the question Coverage exists to answer. No bars, no colour, no
tappability. Hidden when no card has been scored. In the prototype the per-card axis values
are derived illustratively from each card's stored score — real data comes from the backend's
three-axis fields.

**Tiers:** five, derived from each card's last score — `untested` (none), `cold` (0–1),
`shaky` (2), `developing` (3), `solid` (4–5). Colours reuse the score bands:
cold `score-low`, shaky/developing `score-mid`, solid `score-high`, untested `score-none`.
No new metric — this is the same per-card tier data as Today's mastery line, re-sliced by
category.

**Section per category**, separated by `hairline`, 14/15px padding:
- Category name 16.5px/500 with the card count mono 10px `meta-dim` right-aligned
  (`2 CARDS` / `1 CARD`).
- One mono 11px tier line in Today's mastery-band idiom — `1 shaky · 1 developing`,
  zero-count tiers omitted, each segment tappable. The open segment switches from
  `#7c848b` to its tier colour with a 1px underline, exactly like the Today band filter.
- Tapping expands that tier's cards beneath: topic 14px `text-secondary`, mono 9.5px
  `meta-faint` aside (`9D SINCE REVIEW`, or the due label for cards in the queue), and the
  last score right-aligned in its score colour. One tier open at a time
  (`screenshots/coverage-expanded.png`).
- **Sections sort worst-first, by an exact comparator** (no sort control, no per-render
  judgment): primary key `shaky + cold` count descending; tie-break on `untested` count
  descending; final tie-break alphabetical by category name. Deterministic for any data set.
- Footnote under the list, mono 10px `meta-faint`: `TAP A TIER TO LIST ITS CARDS · READ ONLY`

**Deliberately not shown:** any week-by-week pace indicator. A seeded `target_week` describes
the original seed order, not real progress after SM-2 has moved each card's cadence, so
showing it as pace would state something false with a lot of visual confidence. A card's
planned week is acceptable only as a `meta-faint` aside inside an expanded tier list.

### States

- **Loading:** the same static skeleton + mono `LOADING CARDS` as Setup.
- **Load failure:** Today's `LoadFailure` pattern verbatim.
- **No empty state** — every category has at least one card by construction.

---

### Out of scope for Review Sprint and Coverage (this pass)

Manual reordering or removal inside a generated set · saved/named recurring sets ·
cross-topic conversational continuity (scoring stays card-by-card) · streaks, badges or any
celebratory end-of-session treatment · tap-to-preview on the progress rail · in-app card
creation or editing from Coverage · a week-by-week pace view of any kind.

---

## Navigation

Today is home. Card History is reached from a Today row's topic name, or from
"View history for this card" on the session-end state. Card History may enter
Learn Card for grounded material, or Conversation through its due-only review
action. Learn Card returns only to History or Today and never enters Conversation.
Otherwise Conversation is entered from a row tap, Start, or Review Sprint Setup's
Start, and exited with `✕` / Done. Review Sprint Setup
is reached only from Today and exits via `← Today` or by starting a session; Coverage is
reached only from Setup and returns there; Session Recap
appears after the last card of any multi-card session and exits to Today or back to Setup.
No tab bar. No modal stack deeper than one sheet.

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
| `perDay`, `windows[]` | int, `[{label,on,from,to,days:[1...7]}]` | compatibility daily cap + weekday-aware notification windows; missing `days` = every day |
| `screen` (added values) | `setup` \| `recap` | the two Review Sprint screens |
| `setupLoad` | `loading` → `ready` \| `error` | Setup's library fetch; `Retry` re-runs it |
| `setupCats` | `string[]` | selected category filters; empty = whole library |
| `setupSize` | int 4–10 | session size, default 6 |
| `seed` | int | bumped by Shuffle; regenerates the suggested set |
| `practice` | bool | practice mode (sprint run) — suppresses SM-2 writes, swaps the schedule line |
| `covOpen` | `null` \| `{cat, tier}` | expanded tier on Coverage |
| `run` | `[{id, topic, category, score, feedback}]` | this session's results; drives rail colours and the recap |
| `recapOpen` | `null` \| index | expanded recap row |

### Transitions

`idle → rec` (mic) → `proc` (stop / submit) → `follow` if 2 ≤ score ≤ 3 and no follow-up
yet, else `result`. `follow → recFollow → proc → follow` again if the server asks a second
probe, else `result`. `proc → follow` may occur **up to twice** in a session; the server
decides, and the second probe reads "Last one — ". Any `proc` failure →
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

- `prototype/Devmax.dc.html` — the prototype (all screens and states). Open in a browser.
- `prototype/support.js` — its runtime; required for the prototype to run, not part of the design.
- `screenshots/` — every state referenced above:
  `today-default`, `today-loading`, `today-load-failure`, `today-empty`,
  `today-settings-sheet`, `today-quick-add`, `quickadd-failure`,
  `conversation-question`, `conversation-recording`, `conversation-followup`,
  `conversation-score`, `conversation-text-input`, `conversation-resume`,
  `conversation-submit-failure`, `card-history`, `card-history-expanded`,
  `card-history-empty`, `today-mastery-filter`,
  `sprint-setup-default`, `sprint-setup-loading`, `sprint-setup-empty`,
  `sprint-setup-load-failure`, `conversation-rail-dots`, `conversation-rail-chips`
  (detail crop), `session-recap`, `session-recap-expanded`, `sprint-setup-coverage-link`
  (detail crop), `coverage`, `coverage-expanded`.

### Exercising the states in the prototype

The prototype exposes toggles (Tweaks): `loadState` (`auto` / `loading` / `error`),
`failSubmit`, `failAdd`, `emptyQueue`, `textFirst`, `ttsEnabled`, and `railStyle`
(`dots` / `chips`) for the two progress-rail options. `loadState` also drives Review Sprint
Setup's loading and failure states. The library holds 13 cards across the 9 categories — the
three due cards plus ten that exist only for Review Sprint. Forced failures succeed on
the retry so each failure path can be walked end to end. Transcripts type themselves out to
stand in for streaming STT.
