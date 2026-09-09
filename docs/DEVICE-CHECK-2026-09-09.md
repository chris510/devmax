# Physical-device check — September 9, 2026

The audit continued through iPhone Mirroring on the owner's iPhone 17 Pro Max.
This record distinguishes observations from simulator coverage and provider
acceptance from visible notification delivery.

## Release installation and authenticated reads

- App Store Connect showed build 13's upload as Complete and its status as Ready
  to Test. It had no assigned test group, so it did not appear on the phone.
- Assigned build 13 to the existing Founder Internal group, which contains one
  tester. No new tester, public link, or external group was added. The build
  changed to Testing.
- TestFlight then offered version 0.1.0 (13). Updated the existing installation;
  TestFlight showed Open and `devicectl` independently reported bundle build 13.
- The prior installation displayed a connection error. Build 13 loaded the
  authenticated production queue with eight cards and the Week 1 plan.
- Settings showed the existing Apple account, AI-processing choice, and enabled
  notification permission. No account or consent choice was changed.
- Library loaded eight active cards, two material sources, and one archived
  card. Archived History displayed its preserved unscored session and the
  Restore action. Restore was not invoked on the user's existing predecessor.
- The source issue is an August 15 `daily_import_limit` failure. Another source
  with the same title was confirmed August 17. Neither was retried or modified.

## Production push transport

Used the existing APNs payload/delivery function with production configuration
and a clearly labeled device-check notification targeting the visible estimation
card. This operator test made no database writes and did not call the review
poller, stamp a push, score an answer, or move a schedule.

Two tokens were registered for the same owner. The first was rejected as
BadDeviceToken; the other was accepted by production APNs. No token was deleted
by the operator test. Normal poller delivery already isolates tokens and removes
permanent rejections. Physical presentation and tap routing remain to be recorded
separately; APNs acceptance alone does not establish either.

At 18:57 UTC, a read-only database comparison confirmed that all nine cards'
schedule/mastery fields and all twelve completed sessions were unchanged.

## Device-discovered maintenance dismissal defect

On build 13, Card maintenance's Close button did not dismiss the sheet. Its
shared `SheetChrome` header cleared `AppState.sheet`, but History presents
maintenance from its own local binding. The button therefore changed unrelated
state and left the local presentation open.

The header now uses SwiftUI's presentation-scoped `dismiss` action. All callers
use native sheets, so the same header closes both local maintenance and the
app-owned capture, settings, capacity, and plan sheets. The change preserves the
existing layout and copy.

The new maintenance UI regression failed on build 13's implementation, then
passed with the fix. A companion test confirms that the global capture sheet
still closes. The full iOS suite passed **221 tests: 211 unit and 10 UI**.
The 390×844 [open sheet](audits/2026-09-09/maintenance-close-before.png) and
[dismissed sheet](audits/2026-09-09/maintenance-close-after.png) were visually
inspected. Build 14 was uploaded, assigned to the same Founder Internal group,
and installed through TestFlight. `devicectl` independently confirmed build 14.
Card maintenance's Close button then dismissed correctly on the physical phone.

## Device-discovered recovery preview draft loss

On build 14, entered a clearly labeled, unscored QA draft on API identity
boundary, which had no unfinished attempt. The existing estimation-card attempt
was left untouched. A read-only database query confirmed the exact QA text had
been saved. Closed the review, terminated only Unprompted through the app
switcher, and reopened it; process inspection confirmed a new app process. The
recovery banner and exact partial returned correctly.

Opening Options **without first choosing Resume answer**, then ending without a
score, exposed a separate defect: the saved partial was overwritten with empty
text. The preview used `storedPartial`, while persistence and input controls
used the still-empty `draft`. The QA attempt was abandoned with no score; its
test text was lost. At 19:22 UTC, all nine cards' scheduling/mastery fields and
all twelve completed scored sessions still matched the pre-test snapshot.

Recovered text now populates the active draft immediately. The resume banner
tracks presentation only, and starting to type or record dismisses it without
replacing the answer. This removes the second in-memory answer value and keeps
the existing contextual disk/server persistence and Start over tombstones.

Before the fix, the regression reproduced empty disk/server writes for both a
newer local draft and a server-only draft. UI tests also reproduced the lost
saved-partial section after ending and the empty Type instead editor. New
coverage additionally checks that recording continues from the recovered text
without submitting it. The full iOS suite passed **225 tests: 212 unit and
13 UI**, with no failures. The recovery preview, continued text editor, and
ended-history screenshots were inspected at 390×844. A signed build 15 archive
contains the fix; its installation and device checks are recorded when completed.

## Build 15 installation and recovery verification

Build 15 (source `18c2d9bdce549169ff491b6704e8af314e76ed4b`) was uploaded
at 19:36:13 UTC, completed processing, and was assigned to the existing one-person
Founder Internal group. TestFlight initially retained build 13 in its detail
page; restarting TestFlight refreshed the list to 15. Installed the explicitly
listed 15 and verified bundle build 15 independently with `devicectl`.

On build 15, entered a second labeled QA draft on API identity boundary without
submitting it. The server retained the exact 63-character text with no score.
Closed the review, terminated Unprompted through the app switcher, verified its
process was absent, and relaunched it. The process ID changed from 17726 to 17729.
The queue marked the card resumable, and the recovery preview displayed the exact
partial. Opened Options without tapping Resume answer. A read-only query then
confirmed the exact draft remained on the open session, fixing the observed
build 14 failure at its write boundary.

The final End without scoring tap is pending: Mirroring began returning
`noWindowsAvailable` for coordinate actions. Reconnecting handles, bringing the
window forward, and resetting the control session did not resolve it. Restarted
Mirroring; macOS now requires the owner's Mac login. The QA attempt remains open
and recoverable, with no score. No production database row was edited by an
operator workaround.

At 19:48:52 UTC, all nine cards' scheduling/mastery fields and all twelve completed
scored sessions still matched the pre-test snapshot. Build 15's local 225 tests
and all six hosted branch CI jobs passed. The PR checks for the same source also
passed. [Release evidence](audits/2026-09-09/ios-build15.json).

## Mirroring limits

Mirroring paused when the phone was unlocked during the check. Real microphone
capture cannot be certified through Mirroring: Apple's current documentation
states that microphone access is unavailable through this surface.
[Apple's iPhone Mirroring documentation](https://support.apple.com/en-us/120421).

The remaining device checks are the final unscored end/history observation,
interrupted-network recovery, visible production-push delivery/tap routing, and
direct-phone microphone/VoiceOver use. Process restart and recovery-preview exit
have passed on build 15.
