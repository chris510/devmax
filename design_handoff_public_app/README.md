# Devmax public app — design handoff

This handoff extends the existing Devmax design system for a multi-user,
subject-agnostic launch. The product decisions live in
`docs/PUBLIC-APP-SPEC.md`. Existing Today, Conversation, History, Review Sprint,
and Study Plan designs remain authoritative for their current states.

## Prototype

Open `prototype/Public App v1.html`. It is a design reference, not production
code. The top rail switches among the new 390×844 states; use the `screen` query
parameter to open a state directly, for example:

```text
prototype/Public App v1.html?screen=welcome
prototype/Public App v1.html?screen=material
prototype/Public App v1.html?screen=guide
prototype/Public App v1.html?screen=signin
prototype/Public App v1.html?screen=topics
prototype/Public App v1.html?screen=manual
prototype/Public App v1.html?screen=collections
prototype/Public App v1.html?screen=pace
prototype/Public App v1.html?screen=scoring
prototype/Public App v1.html?screen=reminders
prototype/Public App v1.html?screen=empty
prototype/Public App v1.html?screen=settings
prototype/Public App v1.html?screen=privacy
prototype/Public App v1.html?screen=returning
prototype/Public App v1.html?screen=extracting
prototype/Public App v1.html?screen=extract-error
prototype/Public App v1.html?screen=signin-error
prototype/Public App v1.html?screen=topic-edit
prototype/Public App v1.html?screen=collection-detail
prototype/Public App v1.html?screen=reminders-denied
prototype/Public App v1.html?screen=delete-account
prototype/Public App v1.html?screen=file-import
prototype/Public App v1.html?screen=file-error
prototype/Public App v1.html?screen=plan-path
prototype/Public App v1.html?screen=plan-intent
prototype/Public App v1.html?screen=plan-setup
prototype/Public App v1.html?screen=import-background
prototype/Public App v1.html?screen=import-ready
prototype/Public App v1.html?screen=topics-grouped
prototype/Public App v1.html?screen=needs-attention
prototype/Public App v1.html?screen=capacity-conflict
prototype/Public App v1.html?screen=plan-preview
prototype/Public App v1.html?screen=manual-anchor
prototype/Public App v1.html?screen=guide-update
prototype/Public App v1.html?screen=learn-branch
```

## New states in this pass

| State | Purpose |
|---|---|
| Welcome | Product promise without permissions or account friction |
| Choose material | Guide, manual topics, or reviewed collection |
| Paste guide | Source-first import with AI-processing disclosure |
| Account handoff | Sign in only when material is ready to process or save |
| Review topics | Confirm extracted topics before card creation |
| Manual topics | Fast entry without pretending there is a curriculum |
| Collections | Reviewed starter material and an honest unavailable-subject boundary |
| Set pace | Review count, window, and read-aloud before OS permission |
| Enable reminders | Request notification permission after the first review |
| No-material Today | Honest empty account with three ways forward |
| Full Settings | Stable navigation for material, reviews, account, privacy |
| Data & privacy | Plain-language processing, export, and deletion controls |
| Existing owner | One-time founder account claim without replaying onboarding |
| Extracting | Static, source-preserving progress without invented precision |
| Extraction failure | Retry with the full guide and account state preserved |
| Sign-in failure | Account recovery without losing prepared material |
| Topic edit | Rename, exclude, or merge before any card is created |
| Collection detail | Version, outline, and sources before adding material |
| Notifications denied | Valid completion with Today still fully usable |
| Delete account | Explicit scope with the safe action kept dominant |
| File import | Paste or select a supported text-based file without overstating format support |
| File validation failure | Reject image-only or unreadable material before paid processing |
| Plan path | Recommend a time-bound Study Plan while preserving the topics-only route |
| Plan intent | Distinguish already-studied retrieval from lesson-gated learning |
| Plan setup | Collect duration/deadline and weekly capacity before background import |
| Background import | Persistent saved state with no fake timing or progress |
| Import ready | Re-entry point after a long-running import finishes |
| Grouped proposals | Section-level approval for long guides |
| Needs attention | Exception-first review for ambiguous and unsupported proposals |
| Capacity conflict | Increase capacity, extend duration, or reduce scope—nothing is silently dropped |
| Plan preview | Confirm the recomputed schedule before any saved plan changes |
| Manual answer anchor | Require a trusted basis for scored manual topics |
| Guide update | Compare source versions and proposed impact before applying |
| Learn branch | End setup at Week 1 and defer scoring until the material has been studied |

## Design decisions

- The app keeps its existing dark palette, typography, spacing, radii, and
  motion limits.
- Onboarding is a temporary stack, not a new tab system.
- The primary path is **Bring a guide**. It supports pasted text and text-based
  PDF, TXT, or Markdown files. Manual topics are the fastest fallback; Devmax
  collections are available only where reviewed content exists.
- **Build a Study Plan** is the recommended guide outcome. **Just create review
  topics** is a deliberate, quieter alternative.
- Study Plans distinguish material already studied from material being learned.
  The latter is lesson-gated and never produces a scored cold attempt before the
  relevant study item is complete.
- Import is a persistent background state, not a linear loading screen. The copy
  makes no duration promise and uses no fake progress percentage.
- Long guides are reviewed by source section. Only exceptions require individual
  attention.
- Capacity conflicts expose the real workload and never silently drop material.
- Guide import first creates proposals, never cards. The confirmation screen
  keeps the source visible and makes exclusions reversible before saving.
- Manual topics require a user-confirmed answer anchor before scoring.
- Confirmed sources are versioned. Re-import produces a comparison and never
  applies changes before confirmation.
- Everyday scoring continues to show one 0–5 composite. Accuracy, Depth, and
  Boundaries appear in detailed coverage only.
- Settings stays a quick sheet on Today and gains a full destination through
  **More settings**.
- The existing owner does not see ordinary onboarding. A one-time claim binds
  the already-deployed data to the owner's Apple identity.

## Copy rules

- Use **study material** as the umbrella term.
- Use **guide** for pasted source text.
- Use **topic** for a proposed or confirmed review unit.
- Use **collection** for Devmax-authored material.
- Never call an AI-extracted topic a fact, course, or curriculum.
- Never imply medical or legal authority. The user's source remains the source.
- Do not uppercase ordinary mono copy; only labels explicitly shown uppercase.

## Native implementation verification

The public flow is implemented in SwiftUI. Native iPhone 16e renders for every
handoff route live in `screenshots/native/`; each image is 1170×2532 pixels,
corresponding to the required 390×844 point frame at 3× scale.

The implementation includes collection and guide-version management, safe Apple
sign-in cancellation and retry, the first real-review bridge, notification grant
and denial outcomes, account export, and guarded account deletion. The existing
app-wide Dynamic Type limitation remains documented in `docs/DEVIATIONS.md`; it
is not introduced by this flow. VoiceOver labels, native disabled states, minimum
tap targets, reduced-motion-safe transitions, and keyboard-backed text entry are
preserved in the new screens.
