# Adaptive study pilot specification

**Status:** Product direction approved 2026-08-15. Implementation and participant
launch are not complete.

This document owns the approved experiment for turning a bounded source into
durable learning. It extends `PUBLIC-APP-SPEC.md` and amends the immediate-study
and distillation steps in `ADAPTIVE-STUDY-MVP.md`. The existing scoring,
scheduler, card-learning, consent, and Study Plan contracts remain authoritative.

Until this target is implemented behind the pilot gate,
`ADAPTIVE-STUDY-MVP.md` still describes runtime behavior. No Chrome extension,
macOS helper, OCR path, full-page capture, or multi-source synthesis may ship on
the strength of this approval.

## Decision

Approve a three-week, consented pilot of this learning loop:

```text
bounded source
  -> grounded concept proposal
  -> immediate source-closed formation
  -> source-backed correction and approval
  -> recall hold
  -> delayed closed-book Recall
  -> ordinary SM-2 review
  -> separate transfer check
  -> approved learning note
```

The immediate turn is an unscored formation activity. It may diagnose and coach,
but it is not a `Session`, is not review history, and changes no card or scheduler
state. The first delayed, unaided review remains the only event that can establish
Recall and move SM-2.

The pilot validates the learning loop before Devmax invests in a capture surface.
It is not an efficacy trial and cannot establish population-level learning gains.
The approved pressure-test recommendation includes the lightweight
attempt-first-versus-restudy comparison below; it does not authorize a reusable
experimentation platform or broader research program.

## Product claim under test

Devmax helps a learner convert material they chose into a small number of
source-grounded concepts, attempt an explanation before seeing the answer,
receive corrective coaching, and later retrieve the concept without support.

The product must keep these constructs separate:

| Construct | Product question | Evidence | Scheduler effect |
|---|---|---|---|
| **Formation** | Can the learner make sense of the concept with recent exposure and coaching? | Immediate source-closed explanation plus qualitative feedback | None |
| **Recall** | Can the learner reconstruct the essential account later, unaided? | Delayed ordinary review of the stable canonical question | Accuracy/Recall only, under the active scoring contract |
| **Transfer** | Can the learner apply or distinguish the concept under a changed cue? | Separate human-vetted application, comparison, or failure prompt | None during the pilot |

`captured`, `read`, `formed`, `recalled`, `transferred`, and `approved knowledge`
are distinct states. UI copy, API fields, metrics, and reports must not collapse
them.

## Load-bearing pilot invariants

1. **Formation is not a review.** It writes no `Session`, `SessionProbe`, numeric
   score, card rollup, mastery summary, review timestamp, or SM-2 field. A
   `practice=true` session is not an acceptable substitute because practice still
   writes scored history and mastery.
2. **Attempt precedes answer authority in the attempt-first condition.** Before
   submission, the learner may see the concept title and canonical question, but
   not the answer basis, source excerpt selected as authority, answer rubric,
   generated correction, or model-written response.
3. **Exposure commits before disclosure.** The server durably records
   `exposed_at` and `recall_not_before_at` before any response returns answer
   authority or corrective feedback. A client-only timestamp is insufficient.
4. **A proposal is still not a card.** Formation belongs to the proposal. The card
   is created only after the learner sees the authority and explicitly approves
   the concept.
5. **Confirmation preserves the boundary.** The confirmation transaction copies
   the proposal's exposure timestamps to the new card. The card may retain
   `next_review_at = today`, but due, push, Review Sprint, scheduled-session, and
   practice-session paths must exclude it until the gate opens.
6. **The existing recall delay remains a policy, not a scientific optimum.** The
   pilot uses the later of the next local calendar day and eight elapsed hours.
   Exact exposure-to-answer delay is recorded for analysis.
7. **Only delayed Recall moves the schedule.** It uses the ordinary stable
   canonical question, active scoring contract, session transaction, and SM-2
   implementation. The pilot does not introduce another scheduler or numeric
   grading contract.
8. **Transfer remains separate.** Its varied prompt and human research judgment
   never write card scores, history, mastery, or scheduling state. The pilot
   returns no correction after transfer submission; any later feedback feature
   must first establish a fresh exposure boundary.
9. **Distillation requires delayed evidence.** Formation or restudy alone cannot
   make a learning note eligible. Every confirmed concept needs at least one
   completed non-practice review after its exposure boundary.
10. **Source authority remains bounded.** Grounding proves correspondence to the
    supplied material, not external truth. Provenance stays visible, and
    unsupported material fails closed.
11. **The pilot does not optimize capture volume.** One ordinary source chunk may
    produce one to three concepts. More source material is narrowed or routed to
    Study Plan; it is not rewarded with more cards.
12. **No hidden research collection.** Pilot enrollment and human review are
    explicit. Domain metrics and aggregate reports contain identifiers, enums,
    counts, and timestamps only. Private product records and the restricted
    reviewer dataset may contain the consented source and response content needed
    for audit, but they have separate access, retention, withdrawal, and deletion
    rules and never enter general telemetry.

## Pilot product flow

### 1. Add a bounded lesson

Use the existing **Add lesson** surface and existing supported inputs:

- pasted article, documentation, course, or note text;
- a text-based PDF, TXT, or Markdown file whose text is extracted on-device; and
- an optional HTTP(S) URL used only for attribution.

There is no browser capture in this pilot. The source should be a coherent chunk
the learner can study in one sitting, not a whole book. Whole guides continue to
use Study Plan.

The user chooses what the text represents through the existing provenance
classification and confirms they may upload it. Confidential employer material,
personal records, pirated content, and unverified scanned PDFs are excluded.

### 2. Extract and audit concepts

The focused-lesson extraction and independent grounding pass remain in place.
For the pilot:

- propose one concept when the source supports one central mechanism;
- permit two or three only when each is independently useful and grounded;
- never return more than three for `import_path = lesson`; and
- ask the user to narrow the source when three concepts cannot represent it
  without material omission.

Experimental proposals are concierge-audited before activation. The untouched
model output remains available to the research audit, while any correction shown
to the participant is recorded as a human correction.

### 3. Select without revealing the answer

The first proposal screen shows only:

- concept title;
- source section label;
- the stable canonical question only for an assigned attempt-first concept; and
- whether the proposal passed structural grounding.

The screen does not preload or display the answer basis, authoritative excerpt,
rubric, recall-candidate answers, or correction. A restudy control never sees the
canonical question before authority disclosure, so it cannot silently self-test.
For an experimental source, the one predeclared target concept continues and all
other clean proposals receive an explicit `excluded` decision before condition
delivery. There is no post-exposure selection of the analysis target.

### 4. Run the assigned immediate condition

The server assigns experimental sources, not individual related concepts, to
one of two counterbalanced conditions.

#### A. Attempt-first formation

For each selected concept:

1. Show the stable question with the source closed.
2. Accept a voice or text explanation.
3. Save drafts locally immediately and to the server through a cheap,
   idempotent endpoint.
4. Evaluate the explanation qualitatively against the already-grounded
   authority. Allowed outcome labels are `accurate_account`,
   `missing_mechanism`, `misconception`, `missing_boundary`, and
   `insufficient_evidence`; no numeric value is returned or stored.
5. In one durable boundary, save the completed attempt and exposure timestamps
   before returning correction or answer authority.
6. Show one concise source-backed correction focused on the highest-value gap.

#### B. Source-backed restudy control

For each selected concept:

1. Do not require generation.
2. In one durable boundary, save the assigned condition and exposure timestamps.
3. Return the same underlying answer authority available to the attempt-first
   condition. It receives the standard grounded explanation rather than a
   personalized gap diagnosis.

Both conditions receive identical authority, the same recall hold, the same
delayed canonical Recall, and the same later transfer task. The pilot does not
test the already-rejected condition in which an immediate exposed answer moves
SM-2. This comparison evaluates the complete attempt-first product bundle—overt
generation plus personalized qualitative correction—against source-backed
restudy. It does not isolate a pure generation effect.

### 5. Review authority and confirm

After the boundary is durable, show:

- the literal source excerpt;
- answer basis;
- canonical question;
- the five grounded rubric fields;
- provenance classification; and
- any model uncertainty or human correction.

The learner explicitly approves or excludes every selected concept. Approval
creates the ordinary grounded card and copies the exposure boundary onto it.
Exclusion creates no card. Editing meaning requires a new grounding check before
confirmation, as it does today. An experimental target excluded after condition
delivery remains in its assigned funnel denominator and is never replaced by a
more convenient concept.

Confirmation ends on a quiet state such as **Recall available tomorrow**. It
does not open Conversation immediately.

### 6. Complete delayed Recall

When the existing exposure gate opens, the card enters the ordinary due system.
The first review:

- asks the frozen canonical question;
- accepts voice or text through the existing durable draft path;
- may issue at most two scored pre-correction probes;
- uses the active versioned scoring contract;
- writes history and card rollups in the existing complete-answer transaction;
  and
- lets Accuracy/Recall, and only that signal, reach SM-2.

Missing the review is a compliance outcome, not a zero knowledge score.

### 7. Complete a separate transfer check

Seven days after formation exposure, and only after first Recall is complete,
show one human-vetted prompt per experimental concept. Experimental sources have
one target concept, so this is also one prompt per experimental source. The prompt
changes the cue through an application, comparison, counterexample, constraint,
or failure condition.

The transfer attempt:

- is visibly labeled as a research check, not a scheduled review;
- uses a prompt frozen before the learner's first Recall;
- saves the exact prompt version and delay;
- acknowledges submission without revealing correction or answer authority
  during the experimental window;
- never changes the stable canonical question;
- never writes `sessions`, card mastery, or SM-2; and
- is judged later by reviewers blinded to condition and runtime score.

After the response is locked for blinded review, offer a source-backed transfer
debrief. The debrief endpoint must commit a fresh monotonic exposure boundary on
the card before returning correction or answer authority.

### 8. Distill only demonstrated learning

The existing distillation endpoint remains explicit and idempotent. It becomes
eligible only after every included concept has delayed non-practice review
evidence. The exported bundle remains privacy-bounded and excludes raw source,
answers, transcripts, scheduling state, and live mastery.

Formation feedback may inform the in-app lesson recap, but it is not quiz
evidence and may not enter the v1 learning-writeback bundle.

## Minimal server contract

The implementation may choose equivalent names, but it must preserve these
boundaries.

### Proposal preview versus authority

The ready-import response used before formation needs a preview representation
that omits answer authority. The current full `MaterialTopicOut` remains suitable
only after exposure.

```text
MaterialTopicPreviewOut
  id
  position
  section_title
  topic
  formation_question           canonical question for attempt-first; null for restudy
  status
  issue
  formation_state
```

Answer authority must be returned only by a write endpoint that first commits
the exposure boundary. Merely hiding fields already delivered to the screen is
not the target contract.

Use a separate pilot preview endpoint and additive models; do not replace the
required fields in the existing import response and break an installed client.
For an actively enrolled account, requests from a build below the pilot minimum
must receive a structured `pilot_upgrade_required` error rather than the legacy
authority-bearing experimental-source payload. Nonpilot accounts retain the
existing contract. Pilot calls carry an authenticated
`X-Devmax-Client-Build` integer added centrally by `APIClient`; absence counts as
below minimum for an enrolled account.

A successful formation submit or restudy write returns a
`MaterialTopicAuthorityOut` containing the excerpt, answer basis, rubric, and
recall candidates. `GET /materials/lesson-checks/{check_id}` never returns
authority; it returns status, question when assigned, and durable draft/result
metadata. Recovering or reopening authority requires an idempotent POST that
monotonically restamps proposal exposure before disclosure. Confirmation always
copies the latest boundary.

### Lesson check

Add one proposal-owned qualitative aggregate for formation and transfer. Reusing
the same draft/replay machinery keeps both outside `Session` while avoiding two
parallel unscored domains. There is at most one check of each `kind` per proposal.

```text
LessonCheck
  id
  user_id
  proposal_id
  card_id                     null until confirmation
  kind                        formation | transfer
  condition                   attempt_first | restudy | null
  prompt_level                canonical | application | failure_tradeoff
  prompt_version
  provider_route              frozen provider, model, and effort binding
  source_candidate_id
  prompt_text_snapshot
  prompt_rubric_version
  prompt_reviewer_id          pseudonymous
  prompt_approved_at
  status                      open | submitted | exposed
  draft_text
  answer_text
  qualitative_outcome
  feedback
  exposed_at
  recall_not_before_at
  available_at
  started_at
  submitted_at
  updated_at
```

`draft_text`, `answer_text`, and `feedback` are private product data, not
analytics properties. They cascade with source deletion and are excluded from
second-brain export and aggregate pilot datasets.

Recommended endpoints:

```text
POST  /materials/topics/{proposal_id}/formation-check
PATCH /materials/lesson-checks/{check_id}/draft
POST  /materials/lesson-checks/{check_id}/submit
POST  /materials/topics/{proposal_id}/restudy
POST  /materials/topics/{proposal_id}/transfer-check
GET   /materials/lesson-checks/{check_id}
POST  /materials/lesson-checks/{check_id}/authority
POST  /materials/lesson-checks/{check_id}/transfer-debrief
```

Required semantics:

- start and replay are idempotent;
- only the owning user can read or mutate the check;
- no endpoint accepts client-supplied condition, authority, feedback, exposure
  time, or recall gate;
- formation submission runs qualitative evaluation before writing partial
  completion; transfer submission only saves the blind response;
- a successful formation submit or restudy transaction commits exposure before
  its response can reveal feedback or authority;
- provider failure leaves the durable draft and proposal usable, with no answer
  disclosure;
- once a provider result is committed, replay cannot make a second paid
  evaluation; a crash after provider receipt but before commit is recorded as an
  indeterminate physical call and may require an explicit retry rather than an
  impossible exactly-once promise;
- formation evaluation rechecks AI consent and usage authorization at the
  physical provider boundary and follows the established account-lock order;
- every authority replay and transfer debrief extends exposure monotonically
  before its response; and
- no lesson check imports the ordinary session completion or scheduler writer.

Before writing the provider integration, load the repository's current
`claude-api` instructions. The qualitative result needs its own strict schema and
frozen prompt version; it must not parse and discard numeric output from the
ordinary scorer.

### Proposal audit record

The safety audit needs immutable evidence rather than an undocumented reviewer
spreadsheet. Add a restricted `LessonProposalAudit` record containing:

```text
id
source_id
proposal_id
extraction_route              provider, model, effort
extraction_prompt_version
grounding_gate_version
original_proposal_pack
original_grounding_findings
reviewer_id                   pseudonymous
reviewer_decision
reviewer_correction
reviewed_at
created_at
```

The original pack is never overwritten when a reviewer corrects what the
participant will see. This table is private pilot research data: it is excluded
from general telemetry and second-brain export, included in the research
withdrawal/deletion ledger, and removed under the pilot retention schedule.

### Confirmation changes

`POST /materials/imports/{source_id}/confirm` additionally requires, for each
selected pilot proposal:

- a completed `exposed` formation/restudy record;
- an explicit keep decision after authority disclosure; and
- a current passing grounding version.

For each created card, the same transaction sets:

```text
last_learning_exposure_at = formation.exposed_at
recall_not_before_at      = formation.recall_not_before_at
```

No other card, score, session, or SM-2 field is derived from formation.

### Transfer check

Transfer uses `LessonCheck(kind = transfer)`, never a practice session. Its
eligibility requires the first delayed Recall but has no scheduler or push lane.
For the pilot, `available_at` is formation `exposed_at + 7 days`; this is the
single timing anchor used by both server and protocol.

Prompts are drawn from the proposal's application/failure candidates and frozen
only after concierge review. The check stores the selected candidate ID, frozen
text and rubric version, reviewer pseudonym, and approval timestamp. Submission
saves the answer and a quiet completion state but reveals no correction or answer
authority during the experimental window. It therefore creates no new exposure
gate. The later transfer-debrief POST commits a fresh gate before returning
feedback. Human research scores live in the restricted pilot dataset, not on card
or session tables.

### Pilot enrollment and assignment

Enrollment must be explicit and durable. Store a cohort, consent timestamp,
withdrawal timestamp, randomization seed, and source-level assignment. The
server owns assignment, counterbalancing, and prompt/model version snapshots.
The client cannot select a favorable condition.

The pilot feature is available only to active enrolled accounts. A normal app
account is not silently enrolled by installing a build.

An assignment belongs to `(enrollment_id, source_lineage_id)`. Lock all six source
chunks, intended target concepts, pairings, and conditions before processing or
audit. The later binding from that intended target to its extracted proposal ID
does not change condition. Concierge grounding reviewers remain blind to
condition. Formation and restudy endpoints reject the opposite assigned
condition; a new version in the same lineage cannot silently rerandomize it.

After extraction and blinded audit, an idempotent assignment transaction binds
the predeclared target to its proposal ID and marks every other clean proposal
`excluded` with reason `pilot_non_target` before the participant opens condition
delivery. This satisfies the existing all-concepts-decision invariant. The
research operator uses a reviewed CLI; the pilot does not add an admin UI.

Withdrawal immediately blocks new pilot checks and research export but does not
delete or rewrite ordinary cards, sessions, mastery, or scheduling state. The
learner may continue normal study. Full account deletion remains the path that
deletes all account-owned study history; research copies follow the separate
withdrawal ledger and SLA below.

## Research measurement without an analytics system

General analytics and third-party telemetry remain out of scope. Measure the
pilot from domain records, a few explicit lifecycle timestamps, and consented
interviews rather than adding an event firehose.

Add these server-owned timestamps to `MaterialSource`:

```text
proposals_ready_at
review_opened_at
confirmed_at
```

`proposals_ready_at` and `confirmed_at` are written by the existing processing
and confirmation transactions. `POST /materials/imports/{source_id}/review-opened`
sets `review_opened_at` once and is idempotent. No arbitrary event metadata is
accepted from the client.

| Pilot measure | Source of truth |
|---|---|
| Source saved to proposals ready | `MaterialSource.created_at` to `proposals_ready_at` |
| Ready to concept review | `review_opened_at` |
| Review to confirmation | `confirmed_at` |
| Kept, edited, and excluded concepts | `MaterialTopicProposal` final status plus audit ledger |
| Time to first useful question | source creation to formation `LessonCheck.started_at` |
| Formation completion and condition | `LessonCheck(kind = formation)` |
| Exposure and Recall eligibility | formation check `exposed_at` and `recall_not_before_at` |
| First delayed Recall | first completed non-practice `Session` after the boundary |
| Transfer completion | `LessonCheck(kind = transfer)` |
| Backlog age | unconfirmed source age and status |
| Distillation | `MaterialSource.distilled_at` |
| Provider latency and cost | existing privacy-safe `LLMUsage` rows |
| Trust, effort, capture friction, and continuation intent | consented survey/interview responses |

A read-only `api/scripts/lesson_pilot_report.py` may aggregate these rows into a
restricted participant-level report. It is not an admin dashboard. Never place
source text, URLs, titles, filenames, answers, transcripts, rubrics, feedback,
model output, or note content in that aggregate.

## Three-week protocol

### Recruitment

Recruit 10–12 adults with the goal of finishing with at least eight completers.
Each participant must:

- be actively learning technical material for a real deadline four to eight
  weeks away;
- use the current iOS app and study in English;
- contribute six bounded source chunks they are permitted to upload;
- narrow each experimental chunk to one primary concept;
- spend roughly ten minutes per day during the pilot; and
- not already rate themselves four or five out of five familiar with the target
  concepts.

Prefer one broad domain, such as system design or application-side AI, to reduce
content heterogeneity. Recruit two reserves. Compensation is for completing
activities and interviews, never for high scores.

### Experimental design

Use a counterbalanced within-participant comparison:

- six experimental source chunks per participant, each narrowed to one primary
  concept so source assignment does not create an 18-card pilot burden;
- pair chunks by self-rated familiarity, source type, and reviewer-estimated
  difficulty;
- randomize one whole source chunk in each pair to attempt-first and the other to
  restudy;
- never split closely related concepts from one source across conditions; and
- counterbalance condition order across participants and days.

Lock all six chunks, target concepts, pairings, and assignments before any
condition-specific screen is shown. Define a **retained participant** as someone
who remains enrolled through the exit visit, regardless of how many assigned
activities they complete. Report every funnel against both all enrolled and
retained participants; never define the denominator by task completion.

Do not use a scored pretest; it would itself become a learning event. Record
self-rated familiarity and predicted difficulty for matching only.

Both conditions get the same delayed canonical Recall and day-seven transfer
check. Naturally added material is useful adoption evidence but stays outside
the randomized comparison.

### Timeline

#### Preparation

- Freeze the pilot build, model, prompt/schema versions, consent copy, domain
  timestamp schema, randomization manifest, and human audit rubric.
- Exercise success, retry, resume, deletion, provider failure, app termination,
  and late-Recall states.
- Concierge-audit every experimental proposal before activation.

#### Day 0

- Obtain pilot and AI-processing consent.
- Explain provider transmission, withdrawal, and deletion.
- Check source suitability and choose six chunks.
- Record learning objective, familiarity, and predicted difficulty.
- Demonstrate one nonexperimental source-to-delayed-Recall flow.

#### Days 1–5

- Prepare the six experimental sources.
- Run the assigned immediate activity after concierge approval.
- Ask one short effort/trust question after each activity.

#### Days 2–7

- Complete first Recall at the next practical study window after the existing
  gate opens.
- Record exact exposure-to-answer and due-to-answer delays.
- Before answering, record whether the learner revisited the source, discussed
  the concept, or used another tool since formation. Never exclude that case
  after the fact; report it as outside exposure.
- Begin ordinary scheduling only after this review.
- Conduct a short check-in after the second complete loop.

#### Days 7–14

- Continue ordinary reviews.
- Complete one blind varied-transfer prompt per experimental source, normally
  when the fixed seven-day-after-exposure gate opens.
- After the transfer response is locked, provide the gated source-backed debrief.
- Permit voluntary sources and observe backlog behavior.
- Conduct a midpoint interview around day 10.

#### Days 15–21

- Continue ordinary reviews.
- Conduct an exit interview and offer source/account deletion or export.
- Ask about continued use, willingness to pay, and the concrete reasons more
  material was or was not added.

## Measurement definitions

- **Import reliability:** experimental sources reaching `ready` or an actionable
  `needs_attention` state without unrecovered failure divided by saved
  experimental sources; report processing latency separately.
- **Ready-to-activity conversion:** experimental sources completing their
  assigned immediate activity within 48 hours of processing readiness divided by
  ready experimental sources.
- **End-to-end conversion:** experimental sources completing their assigned
  immediate activity within 48 hours of processing readiness divided by all
  saved experimental sources.
- **No-semantic-edit approval:** original proposals approved unchanged or with
  copy-only edits divided by original proposals presented.
- **Critical grounding failure:** an essential claim, canonical answer, or scoring
  criterion unsupported or contradicted by the supplied authority.
- **Time to value:** wall-clock time from source save to first useful question and
  from ready-source open to immediate-activity completion. Provider wait and
  self-reported effort are reported separately; server timestamps are not called
  active foreground time.
- **First-Recall completion:** eligible first Recalls submitted within 48 hours of
  the gate opening divided by first Recalls whose gates opened.
- **First-Recall outcome:** blinded human 0–5 essential-account judgment of the
  initial delayed answer before any follow-up probe. Judge the full pre-correction
  transcript separately for product/scorer validation. Runtime V1 Accuracy and
  composite are reported separately; the composite is never relabeled Recall.
- **Transfer outcome:** separate blinded human 0–2 judgment of the frozen varied
  prompt. It is never fed to SM-2.
- **Backlog age:** saved or ready sources untouched after three, seven, and
  fourteen days.
- **Review burden:** due count, wall-clock session duration, abandonment, and
  self-reported daily effort and overload.
- **Capture friction:** intended captures postponed or abandoned specifically
  because paste or file handling was cumbersome.

Aggregate primary metrics per participant before reporting cohort summaries.
Concepts are nested inside sources and participants; they are not independent
learners.

The directional learning estimand is each participant's mean paired difference
in initial-answer human score, attempt-first minus restudy, across the three
preassigned source pairs. Assigned sources remain in adherence and safety
denominators regardless of approval or completion. The complete-pair estimate is
secondary and is shown beside worst- and best-case sensitivity bounds that assign
missing condition responses the bottom or top of the 0–5 scale. No post-treatment
definition of a “usable pair” is allowed.

## Human audit and outcome scoring

### Grounding audit

Audit every untouched experimental proposal before participant activation.
For each answer basis, canonical question, and rubric field, label:

- `supported`;
- `unsupported`;
- `contradicted`; or
- `unverifiable`.

Block critical failures. A correction can make the participant experience safe,
but quality metrics remain attached to the original output.

### Blinded outcome judgment

- One technically qualified reviewer judges every delayed Recall and transfer
  response.
- A second blinded reviewer independently judges at least 25%, every borderline
  result, and every disagreement greater than one point.
- When staffing permits, the concierge grounding reviewer is not an outcome
  reviewer. Outcome packets omit formation answers, personalized correction,
  condition, participant identity, and runtime model score; they contain only the
  frozen authority/rubric and response material needed to judge correctness.
- Freeze the 0–5 essential-account and 0–2 transfer rubrics before reviewing
  responses.
- Adjudicate disagreements greater than one point and report agreement.
- Audit speech-transcription failures separately from knowledge failures.

Do not mix the runtime V1 composite, V2 Recall target, formation outcome, and
transfer judgment into one number.

## Predeclared decision gates

These are product decision thresholds, not scientific constants.

### Mandatory safety gates

- Zero critical unsupported claims reach a participant.
- Every immediate activity leaves sessions, scored history, mastery, and all four
  SM-2 fields untouched.
- Every answer-authority reveal has a committed exposure boundary first.
- No undisclosed provider use, cross-user exposure, or source-deletion failure.
- Any violation pauses the pilot immediately and blocks an extension decision.

### Core-loop green

- At least 90% of saved experimental sources reach `ready` or actionable
  `needs_attention` without an unrecovered processing failure.
- At least 70% of ready experimental sources complete the immediate activity
  within 48 hours, and at least 60% do so from the all-saved denominator.
- At least 70% of first delayed Recalls complete within 48 hours of eligibility.
- At least 75% of retained participants finish three full
  source-to-delayed-Recall loops.
- At least half of participants voluntarily add a nonexperimental source after
  onboarding.
- At least 75% of retained participants, with a minimum of six people, choose to
  continue weekly after the pilot.
- Median daily burden stays at or below roughly ten minutes, and the majority of
  individual Recall sessions remain within one to three minutes.

### Content and scoring green

- At least 70% of original proposals need no semantic correction.
- At least 90% of canonical questions are independently judged answerable from
  their supplied authority.
- Runtime Accuracy and human essential-account judgments are within one point on
  at least 85% of audited delayed answers, with no systematic condition bias. A
  failure blocks score-trust claims but does not by itself falsify retrieval
  learning.

### Supportive learning signal

The sample is too small for an efficacy claim. Advance only when:

- the cohort median complete-pair difference is greater than the predeclared harm
  margin of -0.5 on the frozen 0–5 human essential-account rubric;
- fewer than three retained participants are at least one point worse on average
  under attempt-first;
- at least 60% of retained participants with a completed pair have a positive
  participant-level difference; and
- the cohort median complete-pair difference is at least 0.5.

These are deliberately demanding directional product heuristics, not stable
statistical cutoffs. Report every participant effect, sign counts, randomization-
based intervals where supported, and the missing-response sensitivity bounds.
Transfer is exploratory because ordinary condition-dependent review histories
can differ before day seven; it cannot decide whether attempt-first advances.

### Extension gate

A Chrome selected-text prototype may be proposed only when all mandatory safety
and core-loop gates pass and manual web capture is either:

- a top-two repeated friction for most retained participants; or
- responsible for roughly 30% of observed intended web captures being postponed
  or abandoned.

Apply the same rule independently to a macOS PDF Share Extension. If learners
capture frequently but do not approve concepts or return for Recall, increasing
capture volume is a stop signal, not an extension opportunity.

## Analysis cautions

- Preserve every assignment and report missingness by condition.
- Never score a missed review as an incorrect answer; report adherence and
  knowledge separately.
- Record exact exposure count and delay because ordinary reviews affect later
  retention.
- Report outside source/tool exposure by condition and in sensitivity views;
  never remove it post hoc.
- Freeze prompts and models during the randomized set. A material prompt change
  starts a new cohort.
- Treat transfer as exploratory because ordinary SM-2 may create unequal review
  exposure before the transfer check.
- Report novelty, motivated-sample, demand-characteristic, source-difficulty,
  speech-recognition, and control-condition contamination risks.
- Avoid population efficacy, clinical, educational-outcome, or causal marketing
  claims from this pilot.

## Privacy and operations

- Use the existing versioned AI-processing consent path and add explicit pilot
  research consent covering random assignment, human review of formation, Recall,
  and transfer responses, temporarily withheld transfer feedback, transcript
  handling, and the implementation's actual audio behavior.
- Show the exact source payload and configured provider before transmission.
- Explain deletion precisely: deleting a material source removes its raw source,
  proposals, and source-owned lesson checks, but existing cards and Session
  history follow the current archive/history invariants. Full account deletion is
  required to delete all account-owned study history.
- Do not place raw research responses or sources in the repository.
- Keep the randomization manifest and reviewer dataset access-controlled.
- Maintain a withdrawal/deletion ledger keyed by pseudonymous enrollment ID. On
  withdrawal, remove row-level copies from active reviewer files and research
  exports within 14 days and from research backups within 30 days. Delete the
  remaining row-level reviewer dataset and pseudonym linkage no later than 90
  days after pilot close; retain only genuinely non-identifying aggregates.
- Do not promise audio deletion or retention behavior that the implementation
  does not actually provide.
- Do not export raw source, formation answers, transfer answers, transcripts,
  scores, or scheduling state to the second brain.

Pause immediately for a critical unsupported teaching claim, disclosure failure,
cross-user exposure, loss of an answer draft, or exposure-boundary/scheduler
violation.

## Implementation workstreams

### P0 — Contract and fixtures

- Freeze formation outcome schema and prompt version.
- Create reviewed source/proposal/formation fixtures across complete,
  mechanism-missing, misconception, boundary-missing, and insufficient-evidence
  cases.
- Freeze the pilot consent copy, audit rubric, domain timestamps, and assignment
  algorithm.

### P1 — Backend pilot boundary

- Add handwritten migration
  `api/alembic/versions/0020_adaptive_study_pilot.py`; update the Postgres test
  truncate list and migration round-trip coverage.
- Add lesson-check, proposal-audit, enrollment, and assignment storage through a
  handwritten migration tested on real Postgres in `api/app/models.py`.
- Add the preview, check, progress, and confirmation wire contracts in
  `api/app/schemas.py`.
- Split proposal preview from answer-authority disclosure in
  `api/app/routers/materials.py`.
- Add durable formation draft, submit, authority replay, restudy, transfer, and
  transfer-debrief endpoints in the same material-owned router.
- Commit proposal-level exposure before disclosure and copy it during card
  creation.
- Factor the existing monotonic exposure calculation in
  `api/app/services/cards.py` so card learning and proposal confirmation cannot
  drift on timezone or gate extension.
- Limit focused extraction to one to three concepts in
  `api/app/services/llm.py` and `api/app/services/materials.py`.
- Add a dedicated strict qualitative lesson-check schema and prompt in
  `api/app/services/llm.py`; do not reuse the numeric scorer or a coaching prompt
  that assumes a Recall score already exists.
- Reuse the current consent, usage-authorization, physical-call audit, and
  account-lock ordering at the provider boundary. If disclosure copy changes,
  follow the catalog/minimum-build activation sequence.
- Keep all ordinary due/session/scheduler paths unchanged and rely on the
  existing card recall gate.
- Add the three source lifecycle timestamps, the idempotent review-opened write,
  and a read-only participant-level reporting script without an analytics SDK.
- Preserve source deletion cascades and include private lesson checks,
  enrollment/assignment records, and linked proposal-audit records in account
  export/deletion behavior through `api/app/routers/authentication.py` and its
  response schema. Research file copies still follow the withdrawal ledger.

### P2 — iOS pilot flow

- Add lesson-check wire models and calls in
  `ios/Devmax/Models/PublicModels.swift` and
  `ios/Devmax/Services/APIClient.swift`, including the central pilot build header,
  with matching `PublicMock` behavior.
- Add formation states to `ios/Devmax/App/PublicOnboardingState.swift` rather
  than the ordinary conversation state machine.
- Add a lesson-check disk draft store using the same durability pattern as
  `DraftStore` and `PracticeDebriefDraftStore`.
- Add a focused `LessonCheckScreen` under `ios/Devmax/Screens/Onboarding/`; it may
  reuse voice/text components but never the ordinary result block or run entry.
- Show preview, source-closed attempt/restudy, qualitative correction, authority
  approval, held state, and resume/failure states.
- Remove the pilot path's immediate call to `beginLessonStudy`.
- Route the first real question through the ordinary due/Conversation flow only
  after the server gate opens.
- Add transfer research-check states that never render a numeric score.

### P3 — Verification

Backend tests must prove:

- formation changes no `Card`, `Session`, `SessionProbe`, score, mastery, history,
  or SM-2 field;
- no authority or correction can return before the exposure commit;
- authority cannot be returned by GET, and every POST replay extends the
  proposal/card boundary monotonically;
- an enrolled account on a below-minimum client cannot retrieve the legacy
  authority-bearing payload;
- confirmation copies the exact server-owned boundary;
- due, push, Review Sprint, and both session modes reject the held card;
- ordinary review becomes available when the gate opens and moves SM-2 normally;
- distillation rejects formation-only evidence;
- committed formation/restudy/confirm results replay without another paid call,
  and crash-gap physical calls remain auditable;
- opposite-condition endpoints and source-lineage rerandomization are rejected;
- original proposal audit records cannot be overwritten by reviewer correction;
- blind transfer submission changes no card field, while transfer debrief changes
  only the two learning-exposure fields;
- cross-user access fails; and
- deletion cascades through private formation and transfer data.

Primary backend coverage belongs in `api/tests/test_materials.py`, with shared
gate tests in `api/tests/test_card_learning.py`, strict qualitative-contract
tests in `api/tests/test_llm.py`, account lifecycle checks in
`api/tests/test_authentication.py`, and the handwritten migration round trip in
`api/tests/test_migration_round_trips_postgres.py`.

iOS tests and mock routes must cover:

- attempt-first and restudy;
- voice and text drafts;
- app termination and resume;
- provider failure before disclosure;
- correction and authority review;
- exclude and confirm;
- held Recall copy;
- late Recall; and
- transfer submit and failure.

Update `ios/DevmaxTests/LessonWorkflowTests.swift` and wire-format tests; add
focused disk/server draft recovery tests. The new screen also needs explicit
debug routes for screenshot acceptance.

The implementation is not complete until the relevant 390x844 states are
screenshot-compared against an approved design handoff.

### P4 — Internal dogfood and participant launch

- Run the complete flow on internal, nonexperimental material first.
- Audit provider calls, replay, deletion, and the exposure boundary in production-
  equivalent logs.
- Freeze the release candidate.
- Enroll the pilot cohort only after mandatory invariants pass on SQLite,
  Postgres, simulator, and a physical device.

## Explicitly deferred

- Chrome extension or browser-store submission.
- macOS app, menu-bar app, or Share Extension.
- full-page website extraction or server-side URL fetching.
- OCR or image understanding.
- video transcript scraping.
- multi-source synthesis and claim merging.
- cloud-drive integrations.
- automatic note writes or vault access from the hosted service.
- changing the global recall-delay policy.
- feeding transfer into mastery or SM-2.
- general-purpose analytics, experimentation, or task-queue infrastructure.

## Decision after the pilot

| Outcome | Decision |
|---|---|
| Safety, core-loop, and content gates pass; manual web capture is a repeated blocker | Propose a thin selected-text Chrome capture experiment |
| Gates pass; local PDF handoff is the repeated blocker | Propose a macOS PDF Share Extension experiment |
| Learners capture but do not approve or return for delayed Recall | Stop capture expansion and repair the core loop |
| Learners mainly value summaries rather than unaided learning | Reconsider this product wedge |
| Grounding, scoring, privacy, or exposure invariants fail | Stop and repair before adding participants or surfaces |

Passing this pilot authorizes a capture-surface proposal. It does not by itself
authorize a public extension launch.
