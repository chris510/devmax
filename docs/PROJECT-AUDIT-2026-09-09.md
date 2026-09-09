# Devmax project audit — 2026-09-09

Devmax has a well-tested review engine, but it is not yet a verified, complete
interview-preparation workflow. The largest gaps are the difference between the
checkout and production, the small amount of approved review content, and a few
recovery paths that exist in the API but cannot be reached naturally in the app.

For personal use, prioritize trustworthy content and reliable recovery over more
features. The app can support mechanism retention; your ability to deliver an
unseen design under interview constraints still needs separate practice evidence.

This audit started at `837cafd` with a clean working tree. It includes local fixes,
source inspection, both database suites, simulator tests, visual inspection, and
read-only public production probes. It does not certify zero bugs. No production
data, scoring policy, curriculum approval, deployment, or provider setting was
changed during that initial pass. The follow-up implementation and production
recovery work is recorded in [the rollout record](PRODUCTION-RECOVERY-2026-09-09.md).

## Follow-up implementation status

The findings below preserve the initial audit evidence. Subsequent work:

| Work | Current result |
|---|---|
| Production and recovery | Recovery API `da76d46` deployed; schema `0025`; real bucket backup restored; daily job configured. TestFlight build 15 installed; authenticated Library, process-restart draft recovery, and recovery-preview exit verified. Production APNs accepted a test notification; visible delivery/tap routing and first future cron execution still need verification. |
| Week 2–3 content | Corrected six draft authorities/questions and prepared [Week 2](WEEK-2-CONTENT-REVIEW-2026-09-09.md) and [Week 3](WEEK-3-CONTENT-REVIEW-2026-09-09.md) review packets. Owner approval and actual lesson completion remain required. |
| Unfinished attempts | Explicit Options → History → End without scoring → Learn, with draft preservation, failed-request retry, and spoken resume. Physical testing found and fixed recovery-preview draft loss and an inert maintenance Close button; see [device evidence](DEVICE-CHECK-2026-09-09.md). |
| Archived discovery | Library has an owned archived list and a tested restore path. |
| Unknown discovery | Library checking/failure states are distinct from empty; unavailable plans open a cached destination or plan list. |
| Readability | Dynamic Type scales; targeted maximum-size layouts and review controls checked. Pilot implementation jargon removed; README and runtime amendment index updated. VoiceOver device pass remains open. |
| Learning quality | Two live V1 canary cases retained the correct binary retention outcome, but one over-scored secondary detail and failed the numeric gate. V2 and pilot remain inactive. |
| Generic importer | One live post-fix run completed in 872.6 seconds with 73/73 source excerpts resolving. Capacity and content-review gates prevent automatic creation; no plan was saved. |

[Recovery/UI implementation and evidence](RECOVERY-UX-2026-09-09.md): 1,293 SQLite
tests passed with 44 PostgreSQL-only skips, all 1,337 passed on PostgreSQL 18, and
225 iOS tests passed (212 unit, 13 UI). These replace the earlier baseline counts for the follow-up
code. All six hosted CI jobs also passed for both the deployed branch and PR
revision. Hosted release and backup evidence is in the separate rollout record.

## Verified results

| Check | Result | What it establishes |
|---|---|---|
| SQLite backend suite | 1,287 passed; 44 PostgreSQL-only tests skipped | Existing API, scheduler, scoring-contract, ownership, import, and plan regressions pass |
| Fresh Postgres 17 migration and complete suite | All 1,331 passed, no skips | Migration and database-specific constraints/concurrency tests pass, including the suite's isolated migration round trips |
| iOS baseline | 193 unit tests + 2 UI tests passed | The existing suite did not catch the startup and History problems below |
| iOS after fixes | 199 unit tests + 3 UI tests passed | New request-ordering, History recovery, and UI retry regressions pass with the existing suite |
| Ruff | Passed across `api/` | Includes migrations and operational scripts |
| Python dependency advisory audit | No known vulnerabilities reported | Current installed dependency advisory check; not a container/OS vulnerability scan |
| Actionlint and whitespace check | Passed | Workflow syntax and patch formatting are valid |
| Real HTTP walkthrough | 21 requests plus state assertions passed | Separate uvicorn process and disposable Postgres; capture → grounding → activation → session → draft/resume → abandon → Learn/hold → archive/restore → weekday settings |
| iOS → real local API → Postgres | Session opened and canonical question rendered | Real LiveAPI decoding/navigation with seeded Week 1 data; no question-generation call was needed |
| Screenshots | Eight fixture states and one real-API state inspected at 390×844 logical size | Selected visual/recovery acceptance, not an exhaustive all-screen accessibility or pixel-diff pass |

The local HTTP walkthrough confirmed that learning leaves history, scores, and all
four SM-2 fields unchanged, removes the card from due, and rejects premature
practice. It also confirmed that an in-progress answer blocks source exposure and
that explicit abandonment unlocks it without a score.

The local simulator smoke used a copied Debug app bundle with a disposable local
key and loopback URL, legacy founder authentication, and disabled server consent
enforcement. The test account recorded a decline; no external provider call was
made. This is evidence for the local client/API contract, not for Sign in with
Apple, production consent enforcement, or live scoring quality.

Evidence: [HTTP assertions](audits/2026-09-09/local-http-smoke.json),
[verification record](audits/2026-09-09/verification.json), and the screenshots below.
Full local XCTest bundles are `/tmp/devmax-audit-ios.xcresult` and
`/tmp/devmax-audit-ios-fixed.xcresult` while those temporary files remain available.

## Fixed in this checkout

| Priority | Problem and trigger | Result |
|---|---|---|
| P1 | Due cards arrive, but a plan/captures request stalls. `loadToday` awaited those optional results before publishing `.ready`. | Each request now publishes independently. Due cards become usable immediately; a queue outage also no longer discards an available plan summary. |
| P2 | A foreground refresh starts before a review; a newer post-review refresh finishes first. The older response then puts stale cards back into Today. | Refresh identity guards prevent superseded responses from changing the queue or auxiliary state. Local learning holds are also respected when a pending due response arrives. |
| P2 | A Card History network or decoding error leaves only the Back button and an empty screen. | Explicit loading/failure/ready states, a Retry action on the same card, and request identity guards. Card-maintenance loading errors are also visible instead of silent. |
| P2 | CI has extensive backend coverage but does not compile or test the iOS app. | Added a macOS job that generates the project and runs unit/UI tests on an iPhone 16e, retaining XCTest results. Its first hosted run remains unverified until this change is pushed. |

The startup delay and stale-response tests failed against the original
implementation before the fix. History tests cover failed requests, malformed
responses, retry, and a superseded error; the UI test taps Retry and verifies that
the same card's history appears.

Relevant implementation: `ios/Devmax/App/AppState.swift`,
`ios/Devmax/App/CardHistoryState.swift`,
`ios/Devmax/Screens/History/CardHistoryScreen.swift`, and `.github/workflows/ci.yml`.
The new `history-failure` fixture and its recovery presentation are documented in
the initial handoff. CI uses Xcode 26.3, which matches the local test toolchain and
is listed in the [official macOS runner inventory](https://github.com/actions/runner-images/blob/main/images/macos/macos-15-Readme.md).

## Open findings, ranked for this personal study app

### P1 — The deployed app has not met the current repository release contract

On September 9, the public Railway endpoint returned **401 for `/ready`**, while
`/health` returned 200 with consent policy metadata but without the checkout's
`ai_consent_enforcement_enabled` field. The checkout exposes `/ready` publicly
and checks migration head `0025`. The same broad discrepancy was already recorded
in the August backend audit.

This confirms an observable deployment/checkout mismatch. It does not identify
the exact deployed commit or prove that any particular migration is absent;
deployment inspection must settle that. The historical deploy checklist also
leaves backup/PITR, a restore drill, production APNs, and the post-fix live importer
run unverified. An unchecked entry is missing evidence, not proof the control is
absent.

**Completion criterion:** record the deployed backend revision and installed iOS
build, verify recoverable backups, deploy the intended release through the existing
runbook, and observe `/ready` reporting the expected head. Then exercise one real
device review, loss/recovery of connectivity, and the appropriate push environment.
Keep V2 scoring and pilot activation subject to their separate existing gates.

Sources: `api/app/main.py`, `api/app/auth.py`, `docs/DEPLOY-CHECKLIST.md`,
`docs/BACKEND-AUDIT-2026-08-15.md`, and `docs/RUNBOOK.md`.

### P1 — Most of the planned recall curriculum is not ready to activate

Direct inspection of the current manifests produced:

| Content | Approved | Draft review | Missing complete authority | Total |
|---|---:|---:|---:|---:|
| System-design recall spine | 6 | 13 | 35 | 54 |
| AI foundations | 0 | 4 | 0 | 4 |

The six approved cards are Week 1. All twelve Week 2–3 cards are authored drafts;
the thirteenth draft is the historical Twitter fan-out card. The other 35 base
cards lack canonical questions and complete answer authority. These counts agree
with the prior card-actionability audit; they are still unresolved today.

The twelve-week, version-6 plan exists, but completing its lessons does not create
missing mapped cards. The app correctly returns `Not ready`. The source-review
gate prevents fabricated authority; bypassing it would damage learning quality.

**Recommendation:** review and approve the six Week 2 drafts after their source
lessons, then Week 3. Author later cards from the concepts you actually miss in
design practice. Do not activate all 54 to make the library look complete.
Each released card needs an accessible source, a correct essential account,
accepted alternatives, a focused question, and reviewed sample answers around
the pass/fail boundary.

Sources: `api/cards.json`, `api/modules/ai-foundations.json`, `api/app/seed.py`,
`api/app/routers/study_plan.py`, `docs/CURRICULUM.md`, and
`docs/CARD-ACTIONABILITY-AUDIT-2026-08-11.md`.

### P2 — An unfinished review can trap the learner away from Learn and maintenance

Reproduction: open a review, decide you need to study the source, close
Conversation, and return to History. Closing preserves the live session for
resume. The backend therefore reports `learning_available=false`, and maintenance
is blocked. The iOS API protocol has no abandonment method or corresponding
learner action. A live practice session similarly prevents switching to scheduled
review until that session is finished or abandoned.

The backend behavior is correct: it must not show an answer during a scored
session. The missing piece is an explicit client choice to **end the unscored
attempt and study**, preserving the partial answer and leaving schedule/history
unchanged. The local HTTP walkthrough verified that the existing abandonment
endpoint supports that transition. Ordinary close must continue to mean resume
later; silently abandoning on close would introduce data-loss surprises.

**Completion criterion:** voice and text users can explicitly end a live attempt,
recover from a failed abandon request, and then choose Learn; no numeric score or
SM-2 update occurs. Add a UI test for the whole transition.

Sources: `AppState.finish`, `DevmaxAPI`, `CardHistoryScreen.cardActions`,
`api/app/routers/sessions.py` (`abandon_session`), and
`api/tests/test_card_learning.py` (`test_learning_refuses_to_reveal_authority_during_a_live_session`).

### P2 — Archive is reversible in storage but lacks a recovery discovery path

Reproduction: archive a card, leave its history, and open Library → Review cards.
The list contains only active cards; the API list is also filtered to active
cards. Restore exists inside a known card's maintenance sheet, but there is no
archived-card list to find that sheet again. A surviving plan/material link may
retain an ID in some contexts, which is not a dependable recovery flow.

**Completion criterion:** add an explicit archived view in Library, backed by an
ownership-filtered archive query. Opening archived history must not launch a
review; restore must preserve history/scheduling and respect replacement-lineage
conflicts. Archived cards must remain outside Today, Coverage, Sprint, and push.

Sources: `api/app/routers/cards.py` (`list_cards`, `restore_card`),
`ios/Devmax/Screens/Today/LibraryScreen.swift`, and `CardMaintenanceSheet`.

### P2 — Immediate lesson performance and delayed Recall still differ by rollout

The nonpilot lesson path explicitly scores an immediate explanation after reading
and lets that scheduled session update SM-2. The approved pilot separates unscored
formation from delayed Recall, but only for enrolled sources and supported builds.
The ordinary Learn endpoint also enforces a delay. Those protections should not
be assumed to apply to every Add lesson flow.

For your studying, an immediate explanation is useful feedback but weak evidence
of durable recall. Use the supported Learn/delayed-review path and treat the
pilot as an experiment until its gates are met. Avoid using high same-sitting
scores as evidence that a topic is retained or interview-ready. The fixed
eight-hour/next-day boundary is a product rule, not a demonstrated personalized
optimum.

**Completion criterion:** verify which path your account actually uses, run the
approved pilot with reviewed sources, and compare delayed explanations and unseen
applications. Do not silently switch scoring contracts or enroll production data
as part of a bug fix.

Sources: `docs/ADAPTIVE-STUDY-MVP.md`, `docs/ADAPTIVE-STUDY-PILOT-SPEC.md`, and
`docs/SCORING-CONTRACT-V2-SPEC.md`.

### P2 — Empty-library and plan outages can still suggest the wrong next action

Today checks `queue.isEmpty && library.isEmpty` to show its no-material state,
without checking whether the library finished loading. On a cold launch with
nothing due, a slow or failed library request can look like a new account with no
material. Separately, when the plan summary is unavailable and no cached plan ID
exists, tapping the plan line opens plan creation, despite the unavailable label.

**Completion criterion:** distinguish confirmed-empty, checking, and unavailable;
offer a retry for failed discovery. Preserve cached plan navigation when possible
and do not route an unknown existing-plan state directly to creation.

Sources: `TodayScreen.body`, `TodayScreen.planLine`, and `AppState.loadLibrary`.

### P3 — Accessibility, long labels, and documentation need a focused pass

`WCFont` still creates fixed-size fonts; Dynamic Type does not scale the app.
VoiceOver navigation through Study Plan has no recorded device pass. The real
Week 1 session also demonstrates a long curriculum title occupying three lines
of conversation chrome. These are usability limits even when the normal fixture
fits nicely.

Use shorter display labels while preserving card identities and questions, and
validate default plus large text, VoiceOver order, and all bottom actions on the
design device. Some pilot copy exposes implementation terms such as “server-owned
hold” and “SM-2 state”; replacing those with the actual next review time and
“your review schedule is unchanged” would reduce reading effort.

The README still describes three screens, no auth UI, and 107 tests. Older
sections of `spec.md` describe the pre-amendment composite scheduler and question
regeneration, while current invariants supersede them. Consolidate these records
before future agent work treats historical text as current behavior.

## Learning and feature priorities

The highest-value loop is: learn from a trusted source, reconstruct it later
without help, attempt an unfamiliar design, capture the specific failure, then
review the narrow mechanism that would have changed your decision.

Retrieval practice has experimental support for retention and transfer beyond
repeated studying. Butler's experiments measured later inferential questions;
they did not establish that app scores predict engineering interviews. This is
support for the practice method, not validation of Devmax's scoring model or
readiness claims. [Study and abstract](https://profiles.wustl.edu/en/publications/repeated-testing-produces-superior-transfer-of-learning-relative-/).

Recommended sequence:

1. Reconcile production and backup evidence, then ship the verified recovery fixes.
2. Make Week 2 and Week 3 content genuinely usable through source/answer review.
3. Complete the explicit abandon-to-Learn and archived-card recovery flows.
4. Fix discovery states so “not loaded” never looks like “nothing exists.”
5. Keep doing complete, timed system designs and use observed gaps to choose new
   cards. Record the constraint you missed, the decision it changed, and the
   mechanism to reconstruct next time; avoid turning every exercise into cards.
6. Validate the existing pilot before broadening capture surfaces or adding more
   AI features. Make larger-text and VoiceOver verification part of UI acceptance.

The strongest case for more features now is reducing the friction of source
capture and practice debriefs. That becomes the priority if ordinary use shows
those steps prevent you from studying. The current evidence instead shows ready
content and recovery are the tighter constraints. A week of real usage with
failure notes and delayed-review outcomes could change that ranking.

## Visual evidence and limits

Inspected: [Today](audits/2026-09-09/today.png),
[History](audits/2026-09-09/history.png),
[History failure](audits/2026-09-09/history-failure.png),
[question](audits/2026-09-09/question.png),
[submit failure](audits/2026-09-09/submit-failure.png),
[Learn](audits/2026-09-09/learning.png),
[plan item](audits/2026-09-09/plan-item.png),
[pilot hold](audits/2026-09-09/pilot-held.png), and
[real API question](audits/2026-09-09/local-api-question.png).

History was compared with `card-history.png`; the new failure reuses the existing
load-failure typography/button pattern. Learn and pilot hold were compared with
their matching handoff PNGs. Today was compared with the initial handoff, whose
older screenshot predates the current plan, capture, and footer additions; it is
not a pixel-equivalent baseline. Dynamic dates and simulator safe areas also
differ. The inspected screens showed no new clipping of primary actions.

Not exercised in the initial pass: real microphone recognition and audio interruptions, physical
device background termination, Sign in with Apple against Apple, paid model
scoring or guide extraction, production pushes, production backup restoration,
the current container/OS vulnerability inventory, and the new hosted iOS CI job.
The follow-up status above and rollout record supersede this initial list:
provider calls, the container scan, backup restoration, and hosted CI have since
been exercised. Device checks and scoring qualification remain open; mocked
provider tests cannot substitute for calibration or prove that the app improves
your learning.
