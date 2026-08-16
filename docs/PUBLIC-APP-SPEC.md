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
voice or text. Devmax scores the answer, asks at most two clarifying follow-ups,
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
- Exactly one worker may own an import run. It atomically claims a pending row,
  renews a short heartbeat during the provider wait, and may store a result only
  while its run token still matches. Startup recovery takes over only an expired
  claim; a late old-worker result is discarded. Retry is available only from the
  failed state, so duplicate taps cannot schedule duplicate paid transmissions.
- The direct Study Plan preview path applies the same ownership rule even though
  its HTTP request waits for completion: the new draft is claimed once, Retry
  atomically accepts only `failed`, a heartbeat preserves an active long call,
  and only the matching run token may store the preview. A concurrent Retry is
  rejected before consent authorization or provider transmission.
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

Focused adaptive lessons have a stricter approved target in
`ADAPTIVE-STUDY-PILOT-SPEC.md`. A just-read source or newly revealed answer
authority is fresh learning, not evidence for **already studied**: immediate
formation is unscored, disclosure creates a recall hold, and only the later
closed-book session may move SM-2 or qualify distillation. The shipping first
pass remains documented in `ADAPTIVE-STUDY-MVP.md` until that target is
implemented behind its pilot gate.

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

> **Approved V2 amendment:**
> [`SCORING-CONTRACT-V2-SPEC.md`](SCORING-CONTRACT-V2-SPEC.md) replaces numeric
> Depth and Boundaries with optional qualitative practice and makes Recall (the
> existing Accuracy signal) the displayed 0–5 value. The contract below remains
> the active V1 behavior until the versioned storage, dual-read client, consumer
> migration, and activation gates in that document are complete. Public
> onboarding must migrate with the contract; it must not describe V2 while the
> server still emits V1 semantics.

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
      → Choose reminder days and windows → Request notification permission → Today
  → If learning over time: Today / Week 1
      → Complete first study item → First legitimate review → Explain first score
      → Choose reminder days and windows → Request notification permission
```

The user can inspect choices and prepare material before signing in. Identity is
required before the first paid model call. Failed sign-in returns to the
prepared state without losing anything.

Notification permission is requested only after the user has seen the review
loop and chosen reminder days and a window. Declining is a valid
completed-onboarding state.

Microphone and speech-recognition permission are requested only when the user
first chooses voice. Text remains available throughout.

## Existing owner path

The current production data becomes the founder account before public signup.
The existing owner completes a one-time secure claim and returns directly to
Today. The upgrade must not replay onboarding or alter any card, session, score,
mastery summary, plan, notification window, or SM-2 field.

`POST /auth/founder/apple-claim` is the sole migration path. It binds a fresh,
fully verified Apple proof to the fixed founder row only and requires a temporary
`X-Founder-Claim-Token` that is distinct from both deployed shared secrets. The
legacy API key alone cannot call it, and an unset claim token disables it. A
fresh proof for the already-bound same Apple subject may reissue bearer tokens
after a lost response; a reused nonce, different subject, or subject owned by
another user fails closed. Remove the temporary deployment token after the
returned credentials are verified in Keychain. The public app must not expose a
general endpoint for claiming legacy data.

## Today with no material

A signed-in account may legitimately have no cards. Today shows:

- **Add something you want to understand.**
- Primary: **Add study material**
- Secondary: **Add a few topics**
- Tertiary: **Browse Devmax collections**

No sample scores, fake queue, or empty Study Plan are created.

## Settings information architecture

The Today settings sheet is the fast path for read-aloud and the current review
reminder summary. Notification-window editing is a dedicated destination with an
explicit save action; that destination includes each window's selected weekdays.
The sheet also has a **More settings** action.

Each enabled window is eligible for at most one review push when a conversational
card is due on one of its selected weekdays. Two enabled windows therefore means
up to two reminders on a day selected by both, one means up to one, and zero means
reminders are off. This does not cap Today's queue or reschedule existing cards.
The legacy `reviews_per_day` wire field remains during migration and is normalized
to the enabled-window count within its supported 1–6 range, with a minimum stored
value of one when all windows are off.

The full Settings screen contains:

1. **Study material** — collections, imported guides, topics, and Study Plans.
2. **Reviews** — due-card behavior, read-aloud, the available voice/text modes,
   and a dedicated weekday-aware reminder-window editor.
3. **Notifications** — current iOS permission state and a system-settings link.
4. **Account** — Apple identity, sign out, and account deletion.
5. **Data & privacy** — export, deletion, retention, and model-processing copy.
6. **About** — app version, collection versions, help, privacy, and terms.

## Review reminder contract

Notification cadence and spaced repetition are separate product concepts.
SM-2 decides when a card becomes due. Reminder settings decide only when Devmax
may draw attention to an already-due conversational card; they never reschedule
a card, manufacture due work, or promise a notification on a selected day.

Each notification window stores a non-empty set of ISO weekdays (`1 = Monday` through
`7 = Sunday`), an on/off state, a local start and end time, and a label.
Missing weekdays mean all seven days for rolling clients and pre-weekday data.
The On toggle silences a window while preserving its day selection. The UI
changes weekly nudge frequency by selecting days directly and
shows `Up to N reminders per week`. Compute `N` by counting enabled windows for
each ISO day, capping that day's count by the normalized `reviews_per_day`, then
summing the seven results. This is a maximum rather than a promise because due-only
selection may send less. In the simple one-window case, moving from two to three
times a week is one additional selected day. The existing
`reviews_per_day` wire field remains a server-enforced daily safety cap during
migration; the client normalizes it to the enabled-window count, with a minimum
stored value of one when all windows are off and a maximum of six, instead of
exposing it as a separate cadence control.

The server's frequent poll remains deliberately schedule-free. For each user it
reads that user's timezone and windows, checks the current local ISO weekday and
time, enforces at most one push per eligible window and the daily cap, and then
selects only an already-due conversational card. IANA timezone evaluation keeps
wall-clock windows stable across DST. A selected day with nothing due stays quiet.
Enabled windows on intersecting weekdays must have distinct local start times so
each visible window maps to one idempotent delivery slot. Onboarding edits a draft,
shows invalid span/start collisions inline, and advances only after the settings
write succeeds; a rejected or failed write never becomes the local saved schedule.
The dedicated reminder editor follows the same confirmed-save rule and remains
open with retry and discard actions after a failed write.

Sign out removes local credentials and local drafts from the device after
confirmation. Account deletion is separate and explains server-side deletion
before confirmation.

## Privacy copy that is part of the design

Before either paid path is available, the app names Anthropic and OpenAI,
describes the guide/title/plan settings or question/transcript/answer
authority/context sent, states which provider may perform each purpose, and
records a versioned Allow or Decline decision on the server. A grant must
identify a disclosure version covering every provider the deployment requires.
Decline records the rendered version when known, but an older decline remains a
valid global refusal so a legacy client can always continue without AI;
withdrawal likewise remains available without a version. A current grant is required at every physical
provider-call boundary. Direct calls keep that decision serialized through the
provider call. A multi-minute guide import instead rechecks consent and records
a durable authorization while the consent lock is held immediately before each
provider transmission, then releases the lock while the response runs. Hidden
SDK retries are disabled; an explicit parse retry must cross the boundary again.
Withdrawal or deletion that wins before authorization blocks that transmission.
Once a request has been authorized and handed to the provider client, neither
action can recall it; account deletion discards any result that returns later. A
material disclosure change requires consent again.

Consent-policy support and production activation are separate. The server may
understand a newer disclosure before requiring it so backend deployment cannot
strand an installed client. The one shipped pre-versioned client is interpreted
only as the exact Anthropic-only v1 disclosure it rendered; it does not authorize
OpenAI. A newer disclosure may satisfy an older required provider set, but never
the reverse for a grant. A decline authorizes no provider and therefore stays
valid across a policy advance. The required policy advances explicitly only after the cataloged
minimum iOS build is distributed. Any OpenAI mode fails startup unless the
combined Anthropic-and-OpenAI policy is the required version.

If an owner-only provider shadow is enabled, the disclosure also states that the
same answer context is sent to Anthropic and OpenAI simultaneously, Anthropic
alone controls the shown and saved result, and OpenAI receives a stable
pseudonymous safety identifier derived from the account UUID—not the Apple
credential, name, or email.

Before the first guide is processed:

> Devmax sends the guide text needed to propose your review topics to its AI
> provider. Your source stays attached to your account so you can review or
> delete it later.

Deleting an imported source removes that source and its source-owned transient
guide draft/raw import response. If the user explicitly created a Study Plan
from it, the plan retains its own guide provenance until account deletion. The
Data & privacy screen and public policy must state that distinction rather than
implying that source removal also erases an already-created plan.

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
- per-user notification polling using that user's timezone and weekday-aware windows;
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
- `/auth/apple` and `/auth/refresh` are the only generally unauthenticated client
  routes. The founder claim route authenticates with its dedicated temporary
  header; cron routes continue to require `X-Cron-Secret`, and health remains
  public. While the founder claim token is configured, `/auth/apple` may resume
  an already-linked identity but cannot create a new account. Removing that token
  after verification opens signup without coupling it to the founder's lifetime.
- The legacy `X-API-Key` maps to the founder user only during migration. It can
  neither choose a user nor claim legacy data, and is accepted only while the
  temporary, fail-closed-by-default `LEGACY_API_KEY_AUTH_ENABLED` switch is
  explicitly on.
- Authentication failures reveal no account-existence detail.

### Ownership contract

Ownership is stored on aggregate roots: cards, settings, device tokens, Study
Plans, and guide drafts. Sessions inherit ownership through their card; Study
Plan children inherit it through their plan. Every object lookup first scopes to
the authenticated root owner, including UUID lookups, duplicate checks,
idempotency lookups, and resumable-session queries. A foreign object is reported
as not found rather than forbidden.

The notification poller iterates settings per user. Selected weekdays, daily
caps, window guards, due-card selection, APNs tokens, and missed-review checks
never aggregate across accounts. The poller carries no weekday or wall-clock
schedule of its own.

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
- A user can change an enabled reminder window from two selected weekdays to
  three without a redeploy, and the change moves no due date or SM-2 field.
- A missing notification-window day list behaves as every day; an unselected
  weekday, an exhausted daily cap, a spent window, and an empty due queue each
  remain distinct server outcomes.
- A second user cannot read or mutate any first-user object, including by UUID.
- The founder account retains every existing card, session, plan, score, and
  schedule value.
- All new screens pass the existing 390×844 dark-mode visual review and preserve
  the four-motion limit.
