# Devmax public app — product specification

Status: authoritative public-app extension, approved for implementation on
2026-08-07. This document owns accounts, authentication, per-user ownership,
public onboarding, guide ingestion, and the subject-agnostic vocabulary. The
existing `spec.md` and `docs/STUDY-PLAN-SPEC.md` remain authoritative for review,
scoring, scheduling, and Study Plan behaviour. The load-bearing invariants in
`AGENTS.md` continue to win over every document.

Implementation is intentionally incremental. During the account migration the
existing shared API key may authenticate only the founder account so the shipped
single-user client keeps working. New accounts never receive or use that key.
Once the founder has completed the one-time claim and a bearer-token build is
deployed, the compatibility path is removed in a separate migration.

## Product definition

Devmax is a conversational study coach for durable understanding. A user brings
material, reviews the topics Devmax extracts, then answers short questions by
voice or text. Devmax scores the answer, asks at most one clarifying follow-up,
and schedules the topic for later retrieval.

The launch product is subject-agnostic for knowledge a learner can explain in
their own words: concepts, rules, mechanisms, processes, relationships,
conditions, exceptions, and limitations. The flagship examples are
software-engineering interview preparation, law, medicine, and anatomy. The app
does not claim professional authority in any of those fields; the user's
material remains the source.

The launch product does not score visual identification, anatomy labeling,
calculations, coding exercises, or long-form writing. Import may preserve those
activities inside a Study Plan, but it must classify them as unsupported for
conversational review rather than silently turn them into cards.

The product continues to optimize for 1–3 minute sessions, speed into a review,
and honest recall signal. It does not add streaks, XP, badges, leaderboards,
celebration animation, or engagement pressure.

## Launch content boundary

There are three supported starting points:

1. **Bring a guide.** Paste text or import a text-based PDF, TXT, or Markdown
   file. Devmax proposes a Study Plan and review topics from that source.
2. **Start with topics.** Add a small list manually and begin reviewing without
   a plan.
3. **Choose a Devmax collection.** Use a reviewed, versioned starter collection
   in a subject for which Devmax owns trustworthy material.

Devmax does not generate an authoritative curriculum from a one-line goal at
launch. If no reviewed collection exists, the honest paths are bringing material
or entering topics manually.

Photos, image-only or scanned PDFs, websites, and cloud-drive integrations are
not launch inputs. A file whose usable text cannot be extracted is rejected
without beginning a paid import or losing the local draft.

## Review material before creation

Guide import is durable, asynchronous, and review-first:

- The verbatim source is saved locally before sign-in and durably on the account
  before processing.
- Sign in is required before the first paid extraction call.
- Import runs as a persistent background job. Closing the app or leaving the
  screen does not cancel it.
- The UI reports saved, processing, ready, needs-attention, and failed states.
  It never promises a duration, shows a fake percentage, or uses indefinite
  skeleton motion.
- Devmax extracts proposed topics and a short source-grounded answer anchor for
  each topic.
- Small imports use a topic list. Large imports are grouped by the source's own
  weeks, modules, or sections and allow section-level approval.
- Possible duplicates, unsupported activities, missing answer anchors,
  ambiguous source placement, and low-confidence results enter a separate
  **Needs attention** queue.
- The user can rename, remove, merge, or add a topic and can inspect the source
  anchor behind every proposal.
- Nothing becomes a review card until the user confirms.
- Questions are generated once when a confirmed topic first enters a session,
  then reused on later reviews.
- Building a weekly Study Plan is offered after topic confirmation; it is never
  required before the first review.

An extraction failure keeps the guide intact. Retrying does not require a
re-paste and does not create duplicate cards. When proposals are ready, they
remain discoverable inside Today; a notification may also announce readiness if
the user has already granted permission.

## Study Plan is the recommended guide path

Importing a guide offers two explicit outcomes:

1. **Build a Study Plan** — recommended. Before processing, collect a target
   duration or deadline, weekly study capacity, and plan intent.
2. **Just create review topics** — skips weekly planning and produces only
   source-grounded topic proposals.

Plan intent is required for the first path:

- **Practice material I've already studied.** Confirmed topics may enter
  retrieval immediately.
- **Learn and practice over time.** A topic becomes eligible for conversational
  review only after the user completes the relevant lesson or study item.

If the requested guide does not fit the duration and capacity, Devmax never
silently compresses, omits, or deprioritizes material. It shows the honest
required workload and asks the user to increase weekly capacity, extend the
duration, or reduce included scope. The saved plan is unchanged until the user
confirms a recomputed proposal.

## Source versions

A confirmed guide is an immutable source version. Editing it creates a new
draft, reruns import in the background, and produces a comparison of added,
changed, and removed material plus the proposed plan and review-topic impact.
Applying a new source version requires explicit confirmation. It does not
rewrite existing scores, review history, mastery summaries, or any SM-2 field.

## Manual topic grounding

A manually entered topic requires a user-confirmed answer anchor or source
excerpt under **A good answer should include…** before scored practice begins.
Devmax may help format that anchor but may not substitute unsupported model
knowledge for a trusted source.

## Universal scoring contract

Every answer is evaluated on the same three axes:

| Axis | Question it answers |
|---|---|
| **Accuracy** | Was the essential concept, rule, process, or relationship correct? |
| **Depth** | Did the answer explain the reasoning, structure, causality, or application? |
| **Boundaries** | Did it recognize relevant conditions, exceptions, limitations, trade-offs, or failure cases? |

Each axis is 0–5. The model returns the three axes and evidence; code derives the
single 0–5 composite shown in ordinary review UI.

Only **Accuracy** reaches the spaced-repetition scheduler. Depth and Boundaries
identify where coaching or new cards may help, but they do not turn incomplete
elaboration into a retention failure.

Examples of the same contract:

- System design: mechanism correctness / reasoning and trade-offs / limits and
  failure modes.
- Law: rule correctness / application and rationale / exceptions and scope.
- Anatomy: structure or process correctness / functional relationships /
  variants, constraints, and clinical boundaries supported by the guide.

The user always sees the stable labels Accuracy, Depth, and Boundaries. The
subject examples inform the scorer; they do not rename the product vocabulary.

## Onboarding flow

```text
Welcome
  → Choose material
      → Paste text / import file → Sign in
          → Build a Study Plan
              → Choose intent, duration/deadline, and weekly capacity
              → Background import → Review grouped proposals and exceptions
              → Resolve capacity if needed → Confirm plan
          → Just create review topics
              → Background import → Review grouped proposals and exceptions
      → Add topics manually → Add answer anchors → Review topics
      → Devmax collection → Collection detail → Review topics
  → If already studied: Try one real review → Explain first score
      → Choose daily review pace → Request notification permission → Today
  → If learning over time: Today / Week 1
      → Complete first study item → First legitimate review → Explain first score
      → Choose daily review pace → Request notification permission
```

The user can inspect choices and prepare material before signing in. Identity is
required before the first paid model call. Failed sign-in returns to the
prepared state without losing anything.

Notification permission is requested only after the user has seen the review
loop and chosen a window. Declining is a valid completed-onboarding state.

Microphone and speech-recognition permission are requested only when the user
first chooses voice. Text remains available throughout.

## Existing owner path

The current production data becomes the founder account before public signup.
The existing owner completes a one-time secure claim and returns directly to
Today. The upgrade must not replay onboarding or alter any card, session, score,
mastery summary, plan, notification window, or SM-2 field.

The public app must not expose a general endpoint for claiming legacy data.

## Today with no material

A signed-in account may legitimately have no cards. Today shows:

- **Add something you want to understand.**
- Primary: **Add study material**
- Secondary: **Add a few topics**
- Tertiary: **Browse Devmax collections**

No sample scores, fake queue, or empty Study Plan are created.

## Settings information architecture

The existing Today settings sheet remains the fast path for reviews per day,
notification windows, and read-aloud. It gains a **More settings** action.

The full Settings screen contains:

1. **Study material** — collections, imported guides, topics, and Study Plans.
2. **Reviews** — daily count, notification windows, read-aloud, and default
   voice/text choice.
3. **Notifications** — current iOS permission state and a system-settings link.
4. **Account** — Apple identity, sign out, and account deletion.
5. **Data & privacy** — export, deletion, retention, and model-processing copy.
6. **About** — app version, collection versions, help, privacy, and terms.

Sign out removes local credentials and local drafts from the device after
confirmation. Account deletion is separate and explains server-side deletion
before confirmation.

## Privacy copy that is part of the design

Before the first guide is processed:

> Devmax sends the guide text needed to propose your review topics to its AI
> provider. Your source stays attached to your account so you can review or
> delete it later.

Before the first answer is scored:

> Your answer and the relevant study material are sent for scoring. Devmax
> receives the transcript, not an audio recording. iOS handles speech
> recognition; on-device availability depends on the device and language.

The production policy must state retention, provider training treatment, export,
and deletion behavior. The UI must not promise specifics the deployed system
does not enforce.

## Multi-user technical boundary

The shared API key embedded in the current app is not a public authentication
system. Public signup requires:

- users and Sign in with Apple identities;
- short-lived access tokens and rotating refresh tokens in Keychain;
- user ownership on cards, settings, device tokens, guide drafts, and Study Plan
  roots;
- authenticated scoping on every read and write;
- cross-user isolation tests for every router;
- one settings record per user;
- per-user notification polling using that user's timezone and windows;
- per-user LLM usage accounting and launch quotas;
- account export and deletion;
- abuse protection and rate limits on paid model calls.

Existing scheduler and coaching invariants remain unchanged except for the
intentional axis migration from Mechanism / Trade-offs / Failure Modes to
Accuracy / Depth / Boundaries.

### Authentication contract

- The iOS client uses Sign in with Apple and sends the identity token,
  single-use authorization code, and request nonce to the API over TLS.
- The API verifies Apple's signature, issuer, audience, expiry, and nonce, then
  validates the authorization code with Apple before creating or resuming an
  account. The stable Apple subject, not an email address, identifies a person.
- Devmax access and refresh credentials are opaque, randomly generated tokens.
  Only SHA-256 token hashes are stored. Access tokens are short-lived; refresh
  tokens rotate on every use and a replay revokes the affected login family.
- Access tokens are sent as `Authorization: Bearer <token>`. Refresh tokens are
  used only at `/auth/refresh` and stored in the iOS Keychain.
- `/auth/apple` and `/auth/refresh` are the only unauthenticated client routes.
  Cron routes continue to require `X-Cron-Secret`; health remains public.
- The legacy `X-API-Key` maps to the founder user only during migration. It can
  neither choose a user nor claim legacy data.
- Authentication failures reveal no account-existence detail.

### Ownership contract

Ownership is stored on aggregate roots: cards, settings, device tokens, Study
Plans, and guide drafts. Sessions inherit ownership through their card; Study
Plan children inherit it through their plan. Every object lookup first scopes to
the authenticated root owner, including UUID lookups, duplicate checks,
idempotency lookups, and resumable-session queries. A foreign object is reported
as not found rather than forbidden.

The notification poller iterates settings per user. Daily caps, window guards,
due-card selection, APNs tokens, and missed-review checks never aggregate across
accounts.

## Axis migration

The existing technical-interview data maps without reinterpretation:

- `mechanism_accuracy` → `accuracy`
- `trade_off_awareness` → `depth`
- `failure_mode_awareness` → `boundaries`

Existing composites remain byte-identical. Existing session history and all
four SM-2 fields remain untouched. Migration tests snapshot every affected
session and card before and after the schema change.

## Launch acceptance criteria

- A new user can prepare material, sign in, complete one review, and reach Today
  without creating a Study Plan when choosing the topics-only path.
- A text-based PDF, TXT, or Markdown guide can be imported; unsupported or
  image-only files fail without losing the local draft.
- A background import survives navigation and app termination and later appears
  as ready, needs attention, or failed.
- A 16-week guide can be reviewed by source section without requiring the user
  to inspect every clean proposal one by one.
- An over-capacity plan offers only increase capacity, extend duration, or reduce
  scope; it never silently drops material.
- Learn-and-practice plans never score a topic before its study item is complete.
- A manual topic cannot begin scored practice without a confirmed answer anchor.
- Applying a new guide version changes no saved plan or card until confirmation
  and never rewrites existing review history or SM-2 state.
- A pasted law or anatomy guide produces source-grounded topic proposals using
  the same visible scoring vocabulary as a system-design guide.
- No guide-processing failure loses text or creates cards.
- Notification denial does not block onboarding.
- A second user cannot read or mutate any first-user object, including by UUID.
- The founder account retains every existing card, session, plan, score, and
  schedule value.
- All new screens pass the existing 390×844 dark-mode visual review and preserve
  the four-motion limit.
