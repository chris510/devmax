# Handoff amendment: adaptive study pilot

**Status:** Approved implementation reference for the three-week pilot. This
amends only the focused-lesson path. The tokens, type, spacing, motion limits,
accessibility rules, and dark-only palette in
`design_handoff_devmax_initial/README.md` remain authoritative.

**Frame:** 390 × 844 points. Every state below is owned by
`LessonCheckScreen`; none belongs to Conversation, Session Recap, or the numeric
result block.

## Product boundary expressed in the UI

```text
formation (qualitative, immediate, unscored)
  -> source authority and explicit approval
  -> held state
  -> ordinary delayed Recall in Today / Conversation

transfer (research check, varied cue, unscored)
  -> response locked without feedback
  -> explicit source-backed debrief with a fresh exposure boundary
```

The words **score**, **mastery**, **result**, and **review complete** do not
describe formation or transfer. No state displays a numeral, `/ 5`, score color,
next interval, Session rail, or `Next card`. The accent remains reserved for
primary actions, input state, and status indicators.

## Shared frame

- `StatusBar` at 44 points, with the state label in the right slot.
- A 44-point `← Back` row beneath it.
- Scroll content uses 22-point horizontal padding, 18-point vertical rhythm,
  and 24 points of bottom breathing room.
- The footer has a hairline, 22-point horizontal padding, and the existing
  30-point safe-area padding.
- Primary question/title voice: Newsreader 27/34; activity prompt 21/27 in text
  mode; source blocks use IBM Plex Sans 14/18.
- Mono trust labels are 9.5 points, never smaller, and remain written in their
  designed case.
- Motion is limited to the existing fade and three-dot processing indicator.
  There is no settle animation because no score lands.

## Required 390 × 844 states

### `lesson-pilot-preview`

- Status: `FORMATION · UNSCORED`
- Title: `Make sense of it before Recall.`
- Intro explicitly says formation does not create a score or move the schedule.
- One bordered concept block shows only its title, source section, and—only for
  attempt-first—the canonical formation question.
- It never shows the excerpt, answer basis, rubric, recall candidates, or model
  correction.
- Attempt-first primary: `Attempt from memory`.
- Restudy primary: `Open grounded restudy`; the canonical question is absent.
- Transfer-available primary: `Begin research check`; its varied prompt is not
  shown until the write endpoint creates the check.
- A submitted transfer remains visible as `Open submitted response` after app
  termination; `debriefed` is complete and no longer appears as an entry point.
- There is no exclusion action before condition delivery. Approve/exclude is
  shown only after the durable exposure boundary and authority review.

### `lesson-pilot-attempt`

- Status: `SOURCE CLOSED · UNSCORED`
- Mono trust lines: `ATTEMPT FIRST · SOURCE CLOSED` and
  `NO ANSWER OR RUBRIC IS LOADED ON THIS SCREEN`.
- The stable question is the dominant 27-point serif element.
- A 104-point accent microphone is centered; `Type instead` remains available.

### `lesson-pilot-attempt-text`

- Same question and trust line at the top.
- A minimum 280-point serif editor and `SAVED ON THIS PHONE` beneath it.
- Primary: `Check explanation`. The word “submit” is avoided because the next
  state is qualitative formation, not a scored review.

### `lesson-pilot-resume`

- Title: `Your unfinished explanation is still here.`
- The exact local draft is visible in serif text.
- Notice: `Nothing has been scored or exposed.`
- Secondary `Discard`, primary `Resume`.

### `lesson-pilot-provider-failure`

- The editor and exact draft stay visible.
- Inline notice: `Couldn't check this explanation. Your words are safe on this
  phone.`
- Primary becomes `Try check again`; no toast or modal.

### `lesson-pilot-correction` / `lesson-pilot-authority`

- Status: `SOURCE AUTHORITY`.
- First block is a nonnumeric qualitative label, one of: `Accurate account`,
  `Mechanism missing`, `Misconception`, `Boundary missing`, or
  `Not enough evidence`.
- Then, in order: highest-value correction/grounded explanation, literal source
  excerpt, answer basis, canonical Recall question, five rubric fields, recall
  candidates, provenance/trust notice.
- Secondary `Exclude`; primary `Approve held Recall`.
- No numeric score language or schedule interval appears.
- `lesson-pilot-confirm-failure` keeps this authority visible with an inline
  confirmation error; retrying confirmation does not rerun or restamp formation.

### `lesson-pilot-restudy`

- The pre-exposure preview omitted the canonical question.
- After the durable write, this uses the same authority layout as attempt-first
  but labels the explanation `SOURCE-BACKED RESTUDY · UNSCORED` and has no
  qualitative outcome.

### `lesson-pilot-held`

- Status: `RECALL HELD`.
- Title: `Recall is held.`
- The server-owned availability date is visible.
- Copy says the first scored question appears through Today after the hold.
- It explicitly says formation did not write history, mastery, or SM-2 state.
- The only primary action is `Return to Today`; there is no review launcher.

### `lesson-pilot-recall-ready`

- Status: `RECALL READY`.
- Title: `Recall is ready in Today.`
- Copy distinguishes the delayed ordinary review from formation.
- `Return to Today` goes to the ordinary due flow; it does not construct a
  Session from this screen.

### `lesson-pilot-no-cards`

- Status: `NO RECALL CREATED`.
- This state appears only after an exposed concept is explicitly excluded and
  the zero-kept source is confirmed by the server.
- It records that no Recall card was created and never describes Recall as held.

### `lesson-pilot-transfer` / `lesson-pilot-transfer-text`

- Status: `RESEARCH CHECK · UNSCORED`.
- The frozen varied prompt is dominant serif copy.
- Copy states blinded review and no effect on Recall, mastery, or schedule.
- Voice and text paths share the same disk-first draft behavior as formation.

### `lesson-pilot-transfer-failure`

- The exact response stays in the editor.
- Inline notice says it is safe on the phone.
- Primary: `Try submit again`.

### `lesson-pilot-transfer-submitted`

- Title: `Response locked for blind review.`
- No correction or authority appears.
- Primary `Open source-backed debrief`; secondary `Done`.
- The notice states that debrief creates a fresh exposure boundary first.

### `lesson-pilot-transfer-debrief`

- Status remains `RESEARCH CHECK · UNSCORED`.
- Trust line: `TRANSFER DEBRIEF · NEW EXPOSURE BOUNDARY`.
- Uses the source-authority block rhythm and a single `Done` action.
- It never displays the human research judgment or a numeric result.

## Debug and acceptance matrix

Launch each route on a 390 × 844 simulator with `WC_MOCK=1` and compare the
frame against this amendment and the inherited token table:

```text
lesson-pilot-preview
lesson-pilot-attempt
lesson-pilot-attempt-text
lesson-pilot-resume
lesson-pilot-provider-failure
lesson-pilot-correction
lesson-pilot-authority
lesson-pilot-confirm-failure
lesson-pilot-restudy
lesson-pilot-held
lesson-pilot-recall-ready
lesson-pilot-no-cards
lesson-pilot-transfer
lesson-pilot-transfer-text
lesson-pilot-transfer-failure
lesson-pilot-transfer-submitted
lesson-pilot-transfer-debrief
```

Acceptance also requires VoiceOver-readable headings, a fully usable typed
path, 44-point minimum controls, app-termination draft recovery, and no answer
authority in the preview response or view tree.
