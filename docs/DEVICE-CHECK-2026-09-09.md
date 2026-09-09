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
inspected. A signed build 14 archive contains the fix; distribution and device
verification are recorded below when completed.

## Mirroring limits

Mirroring paused when the phone was unlocked during the check. Real microphone
capture cannot be certified through Mirroring: Apple's current documentation
states that microphone access is unavailable through this surface.
[Apple's iPhone Mirroring documentation](https://support.apple.com/en-us/120421).

The remaining device checks are unscored draft interruption/resume, visible
production-push delivery/tap routing, and direct-phone microphone/VoiceOver use.
