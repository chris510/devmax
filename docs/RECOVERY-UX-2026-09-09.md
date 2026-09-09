# Review recovery and discovery — September 9, 2026

This amendment implements the recovery and discovery findings in the project
audit. It inherits the initial handoff's colors, typography, spacing, and motion.

## End an attempt, then study

Conversation's Close action continues to save a resumable attempt. The new
Options action snapshots the spoken/text draft, stops capture, releases the
conversation's ownership, and opens that card's History. It cannot run during
submission or a draft reset. The microphone and Close controls have spoken labels.

History receives an additive `active_session` object with the session ID,
practice flag, and current turn index. It offers Resume review/practice or End
without scoring. The latest matching local draft must upload successfully before
the abandonment request. An old turn's draft cannot be substituted. A failed
request retains the attempt and offers retry; a conflict reloads server state.
Only a successful detail reload can expose Learn. Learning then records its normal
recall delay, independently of abandonment.

The existing abandonment endpoint preserves draft/session evidence and changes
no card field. History now returns the session status and an `unscored_draft` only
for abandoned sessions. A saved partial is labeled separately from scored turns;
an unscored row uses a dash and a textual status, never an invented zero.

## Recover archived cards

Library → Archived cards uses `GET /cards?lifecycle=archived`. The default list
remains active-only, and both queries apply ownership. No archive query is used by
Today, Sprint, Coverage, or the push loop. Archived History has a textual status
and no review/learning launcher; Maintain card → Restore uses the existing
lineage-aware endpoint. Restoring refreshes discovery and preserves history and
scheduling; a conflicting active replacement still blocks restore.

## Unknown is not empty

Today shows Checking while an empty library is loading and a retry state if it
fails. Only a successful empty response offers the no-material state. Library
loads reject superseded results. Plan failures retain a cached plan destination;
without one, the plan link opens the plan list rather than asserting no plan
exists. Only a known no-active-plan response routes directly to creation.

## Readability and larger text

Bundled serif, sans, italic, and mono fonts now scale with Dynamic Type through
SwiftUI's custom-font API. Default-size design tokens remain unchanged. At
accessibility sizes, Today and Plan Item put their headings in the scroll region,
History stacks its heading, and Conversation gives navigation and its topic
separate rows. Long conversation labels use at most two visual lines and retain
their full accessibility label. The History score column scales with its numeral.

The pilot's held state says when the first scored review becomes available and
that the lesson check was unscored, without exposing implementation terminology.

## Verification

- SQLite: 1,293 passed, 44 PostgreSQL-only skips.
- PostgreSQL 18: 1,337 passed, no skips.
- iOS: 225 passed (212 unit, thirteen UI), no skips after the device-discovered sheet
  dismissal and recovery-preview fixes. The earlier final layout-only
  corrections also passed a separate eight-UI-test run.
- The iOS Release configuration builds for a generic physical device.
- Configuration validation errors omit secret-bearing input values; 96 focused
  configuration tests and the final full backend suites pass.
- New API tests cover ownership, explicit archive filtering, current follow-up
  identity, idempotent abandonment, partial preservation, and unchanged card fields.
- New client tests cover draft-before-abandon ordering, failed-upload retry, stale
  turn isolation, active-conversation exclusion, and unknown discovery.
- UI walkthroughs exercise a typed attempt with failed abandonment/retry/Learn,
  a spoken partial with resume, and archive/leave/discover/restore.
- All four font variants grow at accessibility size 3. At the largest setting,
  UI tests verify review and Plan Item actions remain reachable; assertions on
  button height establish that the requested text size actually took effect.
- Physical testing found that maintenance's Close header targeted the unrelated
  global sheet binding. The shared header now dismisses its actual presentation;
  UI tests cover both local maintenance and app-owned capture sheets. See the
  [device check](DEVICE-CHECK-2026-09-09.md).
- Recovered text is the active draft before Resume is tapped. Tests cover
  preview exit with local/server recovery, unscored ending, and continuing through
  typing or recording. The three `recovery-preview*.png` states were inspected.

Screenshots are in `docs/audits/2026-09-09/`: `unfinished-review.png`,
`end-review-failure.png`, `ended-review.png`, `learn-after-ended-review.png`,
`resumed-spoken-partial.png`, `archived-library.png`, and `restored-card.png`.
They were inspected at the 390×844 design frame. Device/APNs proof remains in the
separate production release record; these fixture tests make no provider calls.
The `largest-text-*.png` images cover Today, Conversation, and Plan Item at the
maximum category. A full VoiceOver rotor pass and every-screen large-text review
remain open; font scaling alone does not establish either.

Reproduce the failure flow with `WC_ROUTE=review-end-failure`,
`WC_TEXT_FIRST=1`, and `WC_TTS=0`. Type an answer, select Options, end it, retry,
then select Study source again. Existing `question` and `history` routes exercise
spoken resume and archive recovery respectively.

For recovery-preview regressions, launch `WC_ROUTE=resume` with `WC_TTS=0`.
Without choosing Resume answer, open Options, end the attempt without a score,
and expand the ended row: the exact saved partial must remain. In a fresh
fixture launch, Type instead must carry that same text into the editor.
`WC_SIM_SPEECH=1` also exercises continuing capture from the recovered partial;
use Type instead to finish capture without scoring it.
