# Scoring Contract V2 — recall plus qualitative coaching

**Status:** approved product and architecture target; not active in production.

**Decision date:** 2026-08-10.

This document owns the target scoring contract, its historical-data boundary,
the migration of every score consumer, and the release gates for activating it.
Until the activation stage in this document is complete, the V1 three-axis
contract in `spec.md` remains the runtime contract. A change must never mix V1
and V2 semantics without the version and compatibility rules below.

This document does not authorize a provider change or a live evaluation. Claude
remains the production scorer. Provider selection is a separate decision.

## Decision

Devmax will measure one numeric learning signal: **Recall**, scored 0–5. Recall
is the V2 product name for the existing Accuracy signal. It remains the only
value allowed to reach SM-2.

Depth and Boundaries stop being numeric mastery claims. They become optional,
qualitative coaching directions:

- **Depth practice** asks for reasoning, a causal link, application, or a
  trade-off supported by the card's trusted material.
- **Boundary practice** asks for a condition, exception, limitation, or failure
  case supported by the card's trusted material.

There is no V2 composite. The score shown after a V2 review is the Recall score
itself. Code does not derive it from secondary values, and the model is never
asked to return numeric Depth or Boundaries.

## Why V2 exists

The provider experiments established a useful asymmetry:

- the scheduler-critical Accuracy bucket was materially more reliable; and
- precise Depth and Boundaries labels remained unstable, including when a
  separate structured-evidence extraction step returned valid quotations.

Keeping unstable secondary labels in a displayed composite made the ordinary
0–5 numeral look more factual than the evidence supported. Retrying or choosing
the more favorable model result would make the system less reproducible. A
second extraction call increased cost and latency without removing the semantic
judgment problem.

The product already has the correct scheduling boundary: only Accuracy reaches
SM-2. V2 makes the rest of the product equally honest. It preserves useful
deeper practice without representing it as a calibrated mastery measurement.

## Product vocabulary

| V1 term | V2 term | V2 meaning |
| --- | --- | --- |
| Accuracy | Recall | Correctness and completeness of the essential account, 0–5 |
| Depth score | Depth practice | Optional qualitative practice, never a score |
| Boundaries score | Boundary practice | Optional qualitative practice, never a score |
| Composite score | Removed | No derived V2 value exists |
| Score | Recall score | The single 0–5 numeral shown for a V2 review |

The word **mastery** may still describe a broad product goal or an existing UI
band, but no V2 interface may imply that Recall alone measures total mastery.

## Non-negotiable invariants

1. **Recall is the only numeric learning signal in V2.** The scoring response
   contains no numeric Depth, Boundaries, or composite field.
2. **Only Recall reaches SM-2.** The existing two-bucket mapping in
   `scheduler.quality_for` remains byte-for-byte equivalent.
3. **The complete-answer path remains one transaction.** Scoring completes
   before any answer, score, card, or schedule write begins.
4. **At most one scored follow-up is enforced server-side.** A prompt cannot
   weaken this limit.
5. **Nothing after corrective feedback reaches the score or scheduler.** The
   existing coached re-attempt and the new qualitative practice turn are
   post-result work.
6. **Practice mode writes honest review history but does not reschedule.** Its
   four protected SM-2 fields remain unchanged.
7. **History is retained without reinterpretation.** V1 composite values are
   never overwritten, deleted, or silently relabeled as Recall.
8. **Averages never mix meanings.** Recall averages use Recall values only.
   Composite-only legacy rows are visible but excluded.
9. **Qualitative coaching never writes card mastery or schedule state.** It is
   an optional session note, not retention evidence.
10. **No partial rollout may make one field mean two things.** Every scored row
    carries a contract version, and every client can distinguish Recall from a
    legacy composite before V2 is activated.

## The scored review loop

The ordinary session remains 1–3 minutes and preserves the current interaction
shape.

### Turn 1

The learner answers the canonical question by voice or text. The scorer receives
the same trusted grounding and transcript it receives today, then returns:

```json
{
  "recall_score": 0,
  "feedback": "",
  "follow_up_question": "",
  "mastery_summary": ""
}
```

All four keys are required. `recall_score` is an integer from 0 through 5.
`follow_up_question` is non-empty only when the provisional score is 1–3 and no
follow-up has already been used. The server still validates that policy and,
not the model, decides whether a candidate probe is shown.

### Scored follow-up policy

The server uses the provisional Recall score:

| Provisional Recall | Server action | Reason |
| --- | --- | --- |
| 0 | Complete; show correction | The essential account is absent or wrong; a narrow probe would reveal coached, not unaided, performance. |
| 1–3 | Ask one scored follow-up | One clarification can distinguish partial retrieval from a missing essential link. |
| 4–5 | Complete | Recall is already sufficient; deeper exploration belongs to optional qualitative practice. |

This preserves the implemented V1 lower-bound decision documented in
`docs/DEVIATIONS.md`: a score of 0 is corrected rather than probed. It also
preserves the server-side `follow_up_used` guard.

If a follow-up is used, the second scoring call sees both learner turns and
returns the final Recall result. There is no third scored call and no retry that
selects between valid results.

### Feedback and mastery summary

Scored feedback explains the essential-account gap or confirms what was
recalled. It may mention a directly relevant omission from the trusted answer
basis, but it does not assign or imply a Depth or Boundaries grade.

`mastery_summary` becomes recall-only. It may describe whether the essential
account was recalled unaided, recovered after one probe, or remained missing.
It must not record an unmeasured claim such as “weak boundaries” or “strong
depth.”

### Coached re-attempt after failed Recall

The accepted `POST /sessions/{id}/reattempt` design remains unchanged. It is
available only when final Recall is 0–2, after the result and correction. It may
update only the existing `reattempt_*` columns and the permitted coached
summary. It never changes the displayed Recall score, scored axes, or SM-2.

The re-attempt is mutually exclusive with qualitative practice in the same
session. A failed essential account needs correction before elaboration.

## Optional qualitative practice

A final Recall score of 3–5 may offer **Go one level deeper** after the result.
This is deliberately opt-in: no extra provider call, latency, or cost occurs
unless the learner submits an answer.

### Deterministic focus selection

The server chooses the focus; a model does not grade secondary axes to choose it.

1. The first completed qualitative turn for a card uses Depth practice.
2. The next completed turn for that card uses Boundary practice.
3. Later completed turns alternate.
4. Dismissing or abandoning the offer does not advance the alternation.

This can be derived from completed qualitative session rows. No denormalized
“last depth score” or “last boundary score” is created.

The fixed prompt frames are:

- **Depth:** “One level deeper — what reasoning, causal link, application, or
  trade-off matters here?”
- **Boundary:** “One level deeper — what condition, exception, limitation, or
  failure case matters here?”

The scorer may specialize the wording using only the trusted coaching anchor,
but specialization is not required to show the offer. The learner answer may
receive one concise `coaching_feedback` string. It receives no score, tier,
pass/fail label, scheduler result, or mastery-summary update.

### Qualitative write boundary

At most one qualitative practice turn is allowed per session. Its endpoint may
write only:

- `sessions.coaching_focus` (`depth` or `boundaries`);
- `sessions.coaching_question`;
- `sessions.coaching_answer`; and
- `sessions.coaching_feedback`.

It must not change `sessions.score`, `sessions.accuracy`, any legacy axis,
`cards.last_*`, `cards.mastery_summary`, or any SM-2 field. Duplicate submission
returns a conflict and never makes a second model call.

Card History may show the completed turn in an expanded row as **Depth practice**
or **Boundary practice**, followed by the question, learner answer, and coaching
note. It has no numeral and is excluded from every average and tier. The existing
post-correction re-attempt remains hidden from ordinary history as specified in
`docs/multi-turn-coaching-design.md`.

## Latency and cost contract

V2 removes the evaluated structured-evidence prepass, numeric-secondary grading,
best-of-N selection, and semantic retries. The request budget is structural:

- one scoring call for every submitted initial answer;
- at most one additional scoring call when the server uses the 1–3 follow-up;
- zero calls merely to offer qualitative practice; and
- at most one additional call only after the learner submits an optional
  qualitative answer.

Scoring and qualitative calls have separate purpose tags and token/cost meters.
Their structured schemas use bounded feedback fields and explicit output caps.
No implementation may hide a new provider call inside Coverage, Today, push,
history, focus selection, or migration. Exact price claims require separately
approved live token counts; this contract promises call bounds, not a price
that could drift with prompts or provider rates.

## Trusted grounding

V2 does not weaken source authority. A card still requires a canonical question,
trusted provenance, an answer basis, and a complete rubric before activation.

The target rubric vocabulary is subject-agnostic:

| V2 key | Existing compatible key | Purpose |
| --- | --- | --- |
| `essential_account` | `mechanism` | Authority for numeric Recall |
| `acceptable_alternative` | `acceptable_alternative` | Equivalent valid account |
| `depth_extension` | `trade_off` | Authority for optional Depth practice |
| `boundary_extension` | `failure_mode` | Authority for optional Boundary practice |
| `misconception` | `misconception` | Likely incorrect account to distinguish |

The migration adapter accepts both key sets. Existing JSON is not regenerated
and no provider call is needed to migrate it. New material writes the V2 keys
after activation. The activation gate remains fail-closed if the required
authority is missing.

## Persistence and history boundary

### Version markers

Add these compatibility fields before changing scoring behavior:

- `sessions.scoring_contract_version SMALLINT NOT NULL DEFAULT 1`, constrained
  to `1` or `2`;
- `cards.last_score_contract_version SMALLINT NULL`, constrained to `1` or `2`;
  and
- the four nullable qualitative session fields listed above.

The migration backfills existing scored sessions to version 1 and existing
scored cards to version 1. Unscored cards keep a null last-score version.

### Existing score columns

Do not rename or duplicate the database's numeric signal during this migration.
For V2 rows:

- `sessions.accuracy` stores Recall;
- `sessions.score` stores the same Recall value as a temporary compatibility
  field;
- `sessions.depth` and `sessions.boundaries` are null;
- `cards.last_accuracy` stores the latest Recall;
- `cards.last_score` stores that same Recall temporarily;
- `cards.last_depth` and `cards.last_boundaries` are cleared when the first V2
  completion becomes the card's latest result; and
- both version markers become 2.

This equality is transitional compatibility, not a new composite definition.
No V2 code calls `derive_composite`.

### Legacy rows

V1 rows retain their original composite and axes exactly:

- a V1 session with `accuracy` exposes that value as historical Recall and its
  original `score` separately as `legacy_composite_score`;
- a pre-decomposition session with only `score` is labeled legacy composite and
  has no Recall value;
- a card with `last_accuracy` may use it for current Recall-based sorting and
  bands; and
- a card with only `last_score` is `unrated` for Recall until a new review
  supplies one.

No migration recomputes, copies, or deletes a historical score.

## API compatibility contract

The compatibility release adds explicit semantics before removing old fields.

### Card reads

`DueCard` and `CardSummary` add:

- `recall_score: integer | null`, sourced from `last_accuracy`;
- `score_kind: "recall" | "legacy_composite" | "unrated"`; and
- `scoring_contract_version: 1 | 2 | null`.

Existing `last_score`, `last_accuracy`, `last_depth`, and `last_boundaries`
remain during the dual-read window. New clients never derive V2 behavior from
`last_score`, `last_depth`, or `last_boundaries`.

### Session history

Each history row adds:

- `recall_score: integer | null`;
- `legacy_composite_score: integer | null`;
- `scoring_contract_version: 1 | 2`; and
- the nullable qualitative fields.

For V2, `legacy_composite_score` is null even though the deprecated `score`
compatibility field equals Recall. The version marker, not numeric coincidence,
defines meaning.

### Answer completion

The V2 completion response adds `recall_score` and keeps `score` as a deprecated
alias with the same value. The client renders `recall_score` whenever present.

Old clients continue to function during the rollout. V2 cannot be activated
until the deployed client understands the versioned fields.

### Active-contract capability

The bootstrap/settings response exposes
`active_scoring_contract_version: 1 | 2`. A V2-capable client contains both
presentation paths and selects one from this server value; it does not infer the
active product contract from whichever card happened to load. Old clients may
remain supported while the value is 1. Moving it to 2 requires the minimum-app
version gate because an old onboarding screen would otherwise describe V1 while
receiving V2 results.

## Consumer migration

Every current consumer has an explicit V2 outcome:

| Consumer | V2 behavior |
| --- | --- |
| SM-2 | Uses Recall through the unchanged Accuracy bucket mapping. |
| Follow-up | Uses provisional Recall 1–3; 0 and 4–5 complete. |
| Result score block | Shows the Recall numeral with the existing `/ 5 RECALL` label. |
| Today bands and filters | Use latest Recall; composite-only legacy cards are unrated. |
| Card History rows | Show Recall when available; composite-only rows are visibly legacy. |
| Card History average | Becomes `AVG RECALL` and excludes composite-only rows. |
| Review Sprint ranking | Uses latest Recall, never composite or a secondary axis. |
| Depth-repair Sprint | Removed; optional qualitative practice replaces the unsupported numeric repair claim. |
| Session Recap | Averages Recall only and labels the value `/5 AVG RECALL`. |
| Coverage category tiers | Use latest Recall and keep their distinct five-tier vocabulary. |
| Coverage axis rollup | Removed; no numeric Accuracy/Depth/Boundaries mean is shown. |
| `/cards/overview` | Uses `last_accuracy` plus its existing scheduler/lapse context. Its tier definitions remain distinct from Coverage. |
| Study Plan card proposals | Weak-card retrieval uses latest Recall `<= 3`; context reads `Existing · recall N`. |
| Mastery summary | Describes essential-account Recall only. |
| Public onboarding | Explains one Recall score and optional ungraded coaching. |
| Mock fixtures | Model realistic Recall, legacy, and qualitative states without manufacturing secondary scores. |

The Study Plan safety boundary is unchanged: selecting an existing weak card may
read Recall, but no Study Plan operation writes a card, score, session, mastery
summary, or scheduler field.

## Required design amendment

V2 preserves the existing dark tokens, typography, spacing, motion limits,
score-color redundancy, and interaction density. The implementation PR still
requires new 390×844 reference states for changed semantics and layout:

1. score result with the Recall-only explanation;
2. optional **Go one level deeper** offer;
3. Depth-practice answer and feedback;
4. Boundary-practice answer and feedback;
5. Card History containing V2 Recall and composite-only legacy rows;
6. Coverage without the axis rollup or depth-repair action;
7. Recall-ranked Review Sprint Setup;
8. Recall-only Session Recap; and
9. public onboarding's first-score explanation.

Approved onboarding copy direction:

- Eyebrow: `YOUR FIRST SCORE`
- Title: `One recall score. Coaching without grades.`
- Body: `Recall measures whether the essential account was correct. It is the
  only signal that schedules the topic. When useful, you can practice going
  deeper or testing a boundary — without turning those answers into mastery
  scores.`

Final line wrapping and screen copy remain a design-handoff decision. No new
animation is authorized.

## Rollout plan

### Stage 0 — approve the contract

Merge this decision package. Production stays on V1. No provider calls occur.

### Stage 1 — add storage and wire compatibility

Add version markers, qualitative fields, explicit Recall response fields, and
dual-read tests. Backfill only version metadata. Keep V1 scoring active.

Stop if a migration changes any historical numeric value or any SM-2 field.

### Stage 2 — migrate consumers and designs

Ship a client that reads explicit Recall semantics and correctly renders V1,
V2, and composite-only history. Implement the replacement axis rollup, depth
repair, averages, ranking, Study Plan wording, and onboarding copy behind the
server-declared active contract. Keep that declaration on V1, so the shipped UI
still presents V1 until activation.

Stop if any ordinary UI labels a V1 composite as Recall or mixes it into a
Recall average.

### Stage 3 — implement V2 scoring behind configuration

Add the V2 structured-output schema and prompt behind an explicit scoring
contract setting whose production default remains 1. Preserve the current
provider and current transaction boundary. Implement qualitative practice as a
separate, opt-in endpoint.

Stop if the V2 response can contain numeric secondary axes, if `derive_composite`
is reachable from a V2 completion, or if coaching can touch card state.

### Stage 4 — offline and local acceptance

Run frozen synthetic cases for Recall bucket agreement, follow-up behavior,
response validity, deterministic replay fixtures, and qualitative write-set
tests. Use mocked provider results for exhaustive consumer tests. Any live
provider canary requires a separate payload, transmission, and spend approval.

### Stage 5 — activate V2

Activate only after a compatible client is deployed and the migration telemetry
can distinguish V1 and V2 rows. Change the scoring-contract setting; do not
change the provider in the same release.

### Stage 6 — remove compatibility code later

After the minimum supported client no longer reads deprecated score fields,
remove V1 runtime branches and secondary-axis consumers. Retain raw historical
columns and version markers. Destructive historical cleanup is not authorized.

## Implementation work breakdown

This is the reviewed file-level sequence, not permission to combine all stages
into one release.

### Backend compatibility PR

- Add the handwritten version/coaching migration and its Postgres
  downgrade/re-upgrade test.
- Extend `api/app/models.py` with only the approved compatibility fields.
- Extend `api/app/schemas.py` with explicit Recall, score-kind, version, and
  qualitative response fields while retaining V1 wire fields.
- Expose the active-contract capability from the existing bootstrap/settings
  path; do not add a second source of user settings.
- Centralize V1/V2 projection in a service helper so `cards`, `sessions`, and
  Study Plan do not independently infer score meaning.
- Update `api/app/routers/cards.py` and `api/app/services/cards.py` to expose the
  projection without changing V1 selection behavior yet.
- Add migration snapshots proving every score and SM-2 column is unchanged.

### iOS dual-read and design PR

- Add versioned score types in `ios/Devmax/Models/Models.swift`; do not pass raw
  integer fields into views without their meaning.
- Move Today bands, Recall filters, Recall averages, Coverage tiers, Sprint
  ranking, and recap aggregation in `ios/Devmax/App/AppState.swift` to the
  explicit Recall projection.
- Keep color and numeral mapping in `ios/Devmax/Design/ScoreStyle.swift`, but
  make its input a Recall value or a named legacy state.
- Amend `TodayScreen.swift`, `ConversationScreen.swift`,
  `CardHistoryScreen.swift`, `SprintSetupScreen.swift`,
  `CoverageScreen.swift`, `SessionRecapScreen.swift`, and
  `PublicOnboardingScreens.swift` according to the nine required references.
- Remove the Depth/Boundaries repair kind only after no route or fixture can
  create it.
- Add mock routes for mixed history and both qualitative focuses, then run the
  standard 390×844 screenshot comparison.

### V2 scorer PR

- Add a separate V2 schema and parser in `api/app/services/llm.py`; leave the V1
  parser intact for rollback.
- Keep `api/app/services/scheduler.py` unchanged except, if helpful, for naming
  aliases whose tests prove identical output.
- Branch completion in `api/app/routers/sessions.py` by explicit contract
  version, with the same score-before-write transaction boundary.
- Add the qualitative endpoint beside re-attempt, with a dedicated schema and
  strict four-column write snapshot.
- Normalize old and new grounding keys in `api/app/services/card_lifecycle.py`
  without rewriting stored JSON.
- Change `api/app/routers/study_plan.py` weak-card ordering and explanation only
  after the compatibility projection is available.

### Activation PR

- Verify the minimum deployed client supports contract version 2.
- Enable V2 scoring configuration without changing the model/provider.
- Record V1/V2 request counts, token usage, invalid-response rate, latency, and
  follow-up rate separately; collect no new learner-content analytics.
- Keep an immediate configuration rollback to V1 until the acceptance window is
  complete.
- Remove no database columns and rewrite no history.

## Acceptance matrix

### Scheduler and scoring

- For every Recall value 0–5 and every scheduler starting state, V2 produces the
  same SM-2 output as V1 Accuracy.
- The V2 response schema rejects `depth`, `boundaries`, `composite`, and
  model-supplied scheduler quality.
- Follow-up truth table is exactly: 0 no; 1–3 yes; 4–5 no.
- A session cannot accept more than one scored follow-up, including replay and
  concurrent-submission cases.
- A valid V2 completion writes `score == accuracy == recall_score`, null legacy
  secondary axes, and contract version 2.
- An LLM failure leaves session, card, mastery summary, and schedule untouched.
- Practice mode writes the Recall result and leaves all four SM-2 fields alone.
- The failed-Recall re-attempt preserves its existing write-set and never
  changes Recall.

### Qualitative practice

- Only completed Recall 3–5 sessions are eligible.
- Failed Recall offers re-attempt, not qualitative practice.
- Focus begins at Depth and alternates only after completed turns.
- Dismissal does not advance focus.
- One session permits one qualitative turn and no retry after a valid response.
- The endpoint's before/after snapshot differs only in its four allowed session
  fields.
- Qualitative text never changes a score, average, tier, mastery summary, card
  proposal rank, or schedule.

### Migration and compatibility

- Upgrade and downgrade round trips preserve all historical numeric values.
- Existing scored rows receive version 1; unscored cards remain null-versioned.
- V1 decomposed sessions expose Accuracy as Recall and retain composite
  separately.
- Composite-only sessions remain visible, visibly legacy, and excluded from
  Recall averages.
- Old clients can complete a V1 session during the dual-read stage.
- A V2-capable client keeps the V1 presentation while the active-contract
  capability is 1.
- V2 activation is blocked until the minimum supported app build understands
  the versioned contract and V2 presentation.

### Consumer correctness

- Today, Coverage, Review Sprint, `/cards/overview`, and Study Plan weak-card
  selection use Recall and never composite or secondary axes.
- Card History and Session Recap average Recall only.
- Coverage contains no numeric secondary-axis rollup or depth-repair action.
- The only numeric review score visible for V2 is Recall.
- Qualitative practice is visibly unscored anywhere it appears.
- VoiceOver labels communicate Recall and do not rely on score color.
- All nine amended states are screenshot-compared at 390×844.

## Explicitly out of scope

- changing SM-2 or its Accuracy/Recall bucket mapping;
- changing the production provider as part of V2;
- retries, best-of-N selection, or favorable-result fallback;
- a second model call that extracts evidence before scoring;
- numeric confidence, readiness, Depth, Boundaries, or coaching scores;
- automatic card creation from a qualitative coaching answer;
- rewriting, averaging, deleting, or backfilling historical composites;
- using the post-correction re-attempt or qualitative turn as retention evidence;
- analytics, engagement mechanics, or new motion; and
- a live canary without a separate explicit transmission and spend approval.

## Superseded decision

This target supersedes the product direction in
`docs/SECONDARY-AXIS-ARCHITECTURE-DECISION.md`, which retained the three-axis
composite while a stronger whole-scorer was evaluated. That experiment was
useful evidence, not wasted work: it demonstrated why a provider swap alone
could not make the secondary mastery claims sufficiently dependable. The V1
document remains the historical record for those experiments.
