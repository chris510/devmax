# OpenAI V2 scoring rollout

**Status:** dark implementation prepared; rollout authorization remains at Stage 0.

**Decision date:** 2026-08-13.

This branch includes the default-off production seam, consent/audit plumbing,
and offline qualification/reporting tools so later gates can be exercised
without redesigning the runtime. Merging it does **not** activate OpenAI in
production, transmit a learner answer, make a paid API call, change the active
scoring contract, or authorize a later live stage automatically. Every stage
that transmits content, spends API credit, or changes production routing
requires its own explicit approval after the prior stage passes.

## Relationship to Scoring Contract V2

This is a separate provider authorization layered **after**
[`SCORING-CONTRACT-V2-SPEC.md`](SCORING-CONTRACT-V2-SPEC.md). It does not amend,
replace, or accelerate that specification.

The order is load-bearing:

1. activate Recall-only V2 with Claude still authoritative;
2. stabilize V2 on Claude and close its activation window;
3. qualify the exact Luna scoring contract offline and in three fresh trials;
4. run an owner-only, consented shadow with Claude still authoritative;
5. only after every gate passes, request a separate owner-only primary canary;
6. measure that canary for 30 consecutive days before considering expansion.

V2 activation and a provider change must not occur in the same release, deploy,
or acceptance window. If V2 is not active and stable on Claude, this rollout
stops. Provider rollback returns scoring to **Claude V2**, not to V1; a V2
contract rollback remains the separate procedure owned by the V2 specification.

The first Claude V2 stabilization window was rolled back on 2026-08-14 after
two otherwise valid responses in one session supplied surplus probe candidates
outside the server's legal band. Parser-policy version 2 preserves the same
turn decisions while ignoring those candidates. The version bump invalidates
all earlier provider qualification fingerprints; Stage 1 must pass again on
Claude before a new Stage 2/3 evidence set can authorize anything.

## Decision

The only candidate OpenAI production path is Recall-only V2 scoring through
`gpt-5.6-luna`. OpenAI documents Luna as supporting the Responses API,
reasoning-effort controls, and structured outputs, which are the capabilities
the V2 grader requires. See the official
[GPT-5.6 Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

This does not authorize moving any other workload. V1 scoring, canonical
question generation, failed-Recall re-attempt grading, qualitative coaching,
Study Plan import, card proposals, and every other model call remain on Claude.
OpenAI Batch is evaluation infrastructure only and is not an interactive
production scoring path.

## Terms used by the gates

### Behavioral decision

A behavioral decision is one of the outcomes that changes what the product does:

- `follow_up` versus `complete`; or
- on a completed score, scheduler bucket `again` for Recall 0–2 versus `good`
  for Recall 3–5;
- Today's mastery band (`cold`, `shaky`, or `solid`); or
- Coverage tier (`cold`, `shaky`, `developing`, or `solid`).

A numeric change that crosses either boundary is a behavioral flip. A valid
score that differs from Claude, a prior score, or an expected score is not a
technical failure and cannot be retried or replaced merely because it is
surprising.

### Successful scoring call

A successful scoring call returns one strict-schema response that passes the
production V2 parser for the exact turn state. An incomplete response, refusal,
timeout, transport failure, non-success HTTP status, malformed JSON, schema
failure, or V2 parser failure is not successful.

### Typed technical failure

Only the following typed outcomes are technical failures eligible for the one
Claude fallback in primary mode:

- connection or transport error;
- timeout;
- non-success provider status;
- provider refusal or incomplete response;
- response body or structured output that is not usable JSON;
- strict-schema violation;
- missing required usage/response metadata when the adapter cannot safely
  normalize the response; or
- rejection by the production V2 parser, including a missing candidate for a
  server-required follow-up. A surplus candidate ignored by parser-policy
  version 2 is a successful response, not a fallback-eligible failure.

A schema-valid, parser-valid Recall result is never a technical failure,
regardless of its numeral, feedback, disagreement with history, disagreement
with Claude in a prior shadow, or apparent confidence.

### Shadow event

One shadow event is one eligible, consented V2 scored turn sent to both
providers under the frozen contract. Claude's result alone is authoritative.
Luna's result is evaluation evidence and has no user-visible or persistent
product effect.

### Qualification fingerprint

The qualification fingerprint is a SHA-256 digest over the exact production
candidate contract, including at minimum:

- the exact Luna model identifier;
- the exact V2 rubric/prompt bytes;
- the canonicalized strict JSON schema;
- the exact reasoning effort;
- the output-token cap; and
- the required `safety_identifier` field name and request placement, with its
  dynamic value replaced by a non-user placeholder; and
- the scoring-contract and fingerprint format versions; and
- the V2 parser-policy and downstream product-decision policy versions.

The deployment value must equal the fingerprint that passed qualification. A
model alias change, prompt edit, schema edit, effort change, output-cap change,
parser/decision-policy change, or fingerprint-format change invalidates
qualification and fails closed to Claude. The matching digest also carries an
explicit UTC deadline: elapsed evidence fails closed even when the alias string
and digest still match. Tags, review notes, provider credentials, and user
identifiers are not part of this contract fingerprint.

Evaluation case identity uses a separate semantic fingerprint so Claude and
Luna results can be paired without pretending their provider/model identifiers
are equal. Resume fingerprints remain transport-specific and cannot be used to
join providers or to turn a repeated trial into a replay.

## Non-negotiable production boundary

1. **Default off is the kill switch.** OpenAI V2 scoring mode defaults to
   `off`. `off` prevents every new OpenAI transmission and routes subsequent
   scoring to Claude. It cannot recall a request already transmitted.
2. **Routing is server-owned and allowlisted.** Shadow and primary modes require
   exactly one explicit server-side owner UUID. Multiple distinct UUIDs fail
   startup until a later rollout decision changes that code boundary. The client cannot select a
   provider, submit an allowlist value, or opt itself into a canary.
3. **Current consent is mandatory.** OpenAI scoring work is accepted only when
   AI-consent enforcement is active and the owner has granted the current
   disclosure naming both Anthropic and OpenAI. The scoring path holds that
   account gate through its provider work, so a completed decline or withdrawal
   blocks every later scoring transmission. Multi-minute guide imports remain
   Anthropic-only and use the separately disclosed durable-acceptance boundary.
4. **The exact, unexpired qualification is mandatory.** A missing, malformed,
   expired, stale, or mismatched qualification fingerprint routes to Claude and
   records no OpenAI success. The deadline is rechecked at session selection,
   intent planning, and immediately before every physical OpenAI transmission.
5. **Only V2 Recall may route to Luna.** Contract V1 and every non-numeric or
   post-correction/coaching model purpose stay on Claude.
6. **One OpenAI attempt means one attempt.** There is no OpenAI retry,
   best-of-N, semantic retry, or favorable-result selection.
7. **At most one Claude fallback is permitted.** It is available only after a
   typed technical Luna failure. If that Claude call also fails, the existing
   scoring-failure path returns; there is no third provider call.
8. **A valid Luna score never falls back.** Valid-score disagreement is
   evaluation evidence, not a fallback trigger. Primary mode does not call
   Claude merely to adjudicate a valid Luna result.
9. **The transaction boundary stays unchanged.** A primary result or its single
   fallback must finish before any answer, score, mastery summary, or scheduler
   write begins. Shadow output never reaches that transaction.
10. **Provider affinity cannot defeat the kill switch.** A session may remember
    its selected provider so scored follow-ups are comparable, but switching the
    global mode to `off`, removing the owner UUID, withdrawing consent, or
    detecting a fingerprint mismatch prevents its next OpenAI call.

The intended deployment controls are:

| Control | Required behavior |
| --- | --- |
| `SCORING_CONTRACT_VERSION` | Must already be `2` before any OpenAI mode is eligible. |
| `OPENAI_V2_SCORING_MODE` | `off` by default; later `shadow` or `primary` only by explicit stage approval. |
| `OPENAI_V2_SCORING_USER_IDS` | Exactly one server-side owner UUID for shadow or primary; multiple distinct UUIDs fail startup. |
| `OPENAI_V2_SCORING_MODEL` | Exact qualified Luna model identifier. |
| `OPENAI_V2_SCORING_EFFORT` | Exact qualified reasoning effort. |
| `OPENAI_V2_SCORING_QUALIFICATION_FINGERPRINT` | Exact 64-character digest from the passed qualification. |
| `OPENAI_V2_SCORING_QUALIFICATION_EXPIRES_AT` | Exact UTC deadline frozen into all four fresh Stage 3 manifests; shadow/primary startup requires it to be future but no more than 30 days ahead. |
| `AI_CONSENT_ENFORCEMENT_ENABLED` | Must be true before shadow or primary transmission. |
| `OPENAI_API_KEY` | Separately funded API-platform key; never committed or returned to a client. |
| `OPENAI_SAFETY_IDENTIFIER_SECRET` | Independent HMAC secret generated from at least 32 random bytes (`openssl rand -hex 32`); never reuse an app or provider credential. |

## Billing and credential boundary

OpenAI API usage requires its own API-platform billing and `OPENAI_API_KEY`.
ChatGPT or Codex subscription credits cannot substitute for API billing or
authorize a backend request. The key is a deployment secret, separate from the
Anthropic key, app authentication, cron secrets, and the secret used to derive
a privacy-preserving OpenAI safety identifier.

Published prices can change. The
[OpenAI API pricing page](https://developers.openai.com/api/docs/pricing) is a
preflight input, not an authorization. Every live evaluation stage must use
fresh exact token counts or a conservative upper bound, print an upward-rounded
cost ceiling, and receive an explicit `--max-cost-usd` approval before spending.

## Stage 0 — land the decision record and dark tooling

Stage 0 may land inert evaluation, transport, routing, consent, audit, and
reporting code ahead of the live sequence. That code must remain unreachable
from learner traffic while the rollout authorization is still at Stage 0.

- Do not set an OpenAI production environment variable.
- Do not enable shadow or primary mode.
- Do not transmit a frozen case or learner answer.
- Do not call a token-count endpoint with production content.
- Do not make a paid OpenAI or Anthropic evaluation call.
- Do not mark a candidate human label approved.

Passing Stage 0 means the sequence is recorded and the dark implementation is
ready for later gates. It does not mean V2, a paid evaluation, shadow mode, or
primary mode has passed.

### Dark implementation in this branch

| Artifact | Purpose |
| --- | --- |
| `scripts/v2_recall_eval.py` | Shared production-builder/parser semantics, human-label validation, fingerprints, and behavior gates. |
| `scripts/v2_recall_sweep.py` | Non-retried Claude V2 baseline runner with token preflight, budget refusal, and durable JSONL. |
| `scripts/openai_bakeoff.py v2-recall` | Non-retried Luna V2 runner with the same request contract and spend controls. |
| `scripts/v2_recall_compare.py` | One-Claude/three-fresh-Luna comparison, full behavior gates, latency/tokens, and the 85% cost gate. |
| `scripts/v2_recall_text_review.py` | Offline draft/check tool for the mandatory human review of Luna feedback and mastery text. |
| `scripts/openai_shadow_report.py` | Read-only first-100-event audit from privacy-safe `llm_usage` exports. |
| `app/services/openai_responses.py` | Strict one-attempt Responses transport; no product routing by itself. |
| `app/services/scoring_provider.py` | Server-owned allowlist, frozen route, qualification fingerprint, safety identifier, and comparison decisions. |

Run each command with `--help` before use. Paid runners still refuse pending
labels and require an explicit maximum spend. The checked-in
`grounded_recall_v2_cases_stage2_draft.json` now contains 38 pending cases over
18 Week 1–3 cards and exercises every required boundary and risk shape. It is a
review worksheet, not an approved qualification pack: all 38 labels and all 12
Week 2–3 card groundings remain explicitly unapproved.

## Stage 1 — activate and stabilize V2 on Claude

Complete every activation gate in `SCORING-CONTRACT-V2-SPEC.md` with Claude
still handling scoring. The compatible client must be deployed, telemetry must
distinguish V1 and V2, and the active contract may then move to V2 without any
provider change.

The Claude V2 stabilization window must close with no unresolved Recall,
follow-up, transaction, history, consent, or scheduler defect. Record the V2
request count, valid-response rate, latency, follow-up distribution, Recall
distribution, and scheduler-bucket distribution without adding learner-content
analytics. There is no OpenAI shadow during this stage.

The 2026-08-15 reactivation was rolled back after one of five scoring calls hit
the original 2,048-token ceiling and returned truncated JSON. The two completed
sessions remained transactionally correct and the earlier surplus-candidate
failure did not recur. Parser-policy version 2 now pairs with the established
8,000-token scoring ceiling. Because the output cap is part of the qualification
fingerprint and spend preflight, all prior Luna evidence remains ineligible.

A written stabilization sign-off is required before Stage 2. A provider canary
must not become a way to debug the V2 product contract.

## Stage 2 — freeze the human-approved qualification pack

Build a source-grounded pack with all of these properties:

- at least **18 distinct approved cards**;
- cards from at least **three curriculum weeks**, with per-week counts reported;
- trusted canonical question, answer basis, complete rubric, and provenance for
  every card;
- human-approved labels for every case, with explicit `review_status: approved`
  and a non-empty review note;
- every Recall value **0, 1, 2, 3, 4, and 5** represented;
- terminal cases on both sides of every product boundary: Recall 1/2 and 3/4
  for mastery presentation, plus Recall 2/3 for scheduling, in every represented
  week;
- initial-turn cases on both sides of the follow-up boundaries, including 0/1
  and 3/4 behavior;
- one-probe insufficiency and two-probe cap cases so `follow_up`/`complete`
  behavior is tested at every legal turn state; and
- answer-shape risk coverage for speech noise, self-correction, fluent adjacent
  jargon, source-compatible alternatives, stale summary contradiction, and
  follow-up-anchored evidence.

Expected labels come from human review against trusted grounding, never from
Claude, Luna, a composite projection, or an assistant approving its own draft.
Changing any answer, expected Recall, expected flow, probe transcript, question,
grounding, prompt, schema, effort, model, or output cap invalidates the affected
fingerprint and requires review again.

Offline validation must prove unique case names, complete authority, explicit
approval, all Recall values and decision boundaries, exact production V2
request construction, and fail-closed parsing. Stage 2 makes no provider call.

The current curriculum manifest has complete approved grounding only for the
six Week 1 cards. Content version 6 adds complete `draft_review` grounding for
the twelve Week 2–3 cards and the 38-case file covers all three weeks, but no
assistant or automation may convert those source claims, rubrics, questions, or
case labels to `approved`. The owner must review each one against its linked
source. Do not copy Week 1 authority or relabel the draft to bypass this gate.

## Stage 3 — run three independent fresh Luna trials

First run a credential-free preflight and preserve its selected case list,
qualification fingerprint, request count, and cost ceiling. Each paid trial
then requires a separate payload, transmission, and spend approval.
The Claude runner uses only its local conservative input bound during a dry run,
even if `ANTHROPIC_API_KEY` happens to be present in the shell. Calling
Anthropic's non-generating token-count endpoint is a separate content
transmission and therefore requires the explicit `--exact-input-counts` flag.
For a paid V2 trial, both runners require explicit current input, output,
cached-input/cache-read, and cache-write prices; their built-in model tables are
not accepted as trial authorization. `--max-cost-usd` must cover the displayed
upward-rounded ceiling, not merely the unrounded internal estimate.

Run the entire frozen pack against Luna **three times** under these rules:

- standard interactive Responses path, not Batch;
- exact same model, prompt, schema, effort, and output cap in every trial;
- one fixed, versioned synthetic 64-hex `safety_identifier` for qualification
  traffic, never a real or derived learner identifier;
- `store: false`;
- one request and no retry per case;
- three new result files from three fresh requests;
- no `--resume` reuse between trials;
- no best-of-three, majority vote, favorable-result fallback, or discarded
  replica; and
- stop before the next trial if the current trial fails its gate.

Choose one explicit UTC `--qualification-expires-at` deadline for the Claude
baseline and all three Luna trials. It must be later than every run start and no
more than 30 days after any of them. Before the first paid call in each trial,
the runner flushes a run manifest as
the first JSONL row. It binds one run UUID to the provider, requested model,
Stage 2 pack fingerprint, and every selected invocation fingerprint, case,
effort, and qualification fingerprint. All selected calls are awaited even if
one fails. Each invocation then gets exactly one flushed, explicitly typed
`success` or `failure` evidence row; failure rows retain response ID, returned
model, elapsed time, and billable usage whenever the provider supplied them.
The manifest also freezes its strict UTC creation time and the common
qualification deadline, the approved max cost, all four explicit rates, the
input-count method, every per-invocation input count, the input total, the
estimated output allowance, and the approved estimated ceiling. The comparator
recomputes that ceiling and requires its supplied comparison rates to match the
rates approved in each run manifest. A Luna manifest additionally freezes the
synthetic safety identifier, its non-user classification, and its format
version; the comparator rejects a missing, substituted, or reclassified value.
Only after all started calls have durable evidence does the runner fail the
trial. A partial artifact is therefore evidence of a failed trial, never a
smaller pack that can be compared favorably.

### Human text-quality gate for each Luna artifact

Recall and flow agreement are necessary but insufficient because a primary
Luna result also supplies the feedback shown to the learner and the replacement
`mastery_summary`. After each fresh Luna run, generate an **unapproved** review
template offline:

```sh
cd api
python scripts/v2_recall_text_review.py draft \
  --luna /path/to/luna-run-1.jsonl \
  --cases /path/to/grounded-recall-v2-approved.json \
  --grounding-manifest cards.json \
  --output /path/to/luna-run-1-text-review.json \
  --reviewer '<human reviewer identity>'
```

The draft tool never approves a response. A human must inspect every successful
case—not a favorable sample—against its trusted answer basis and rubric. For
each case, including every low-Recall correction and every passing response,
the reviewer must add nonempty notes and explicitly set these seven checks to
`true` only after verifying them:

- the feedback is grounded in the approved source;
- it contains no unsupported correction;
- it makes no numeric claim about a secondary scoring axis; and
- the mastery summary describes Recall only;
- the mastery summary accurately distinguishes what was recalled unaided from
  what appeared only after one or two probes—it must not credit a prompted
  detail as unaided recall;
- the mastery summary makes no broad or unmeasured mastery claim beyond the
  reviewed card and transcript; and
- both feedback and mastery summary are concise and direct.

The two score-dependent booleans are mutually exclusive and use the response's
actual Recall, not its expected human label:

- at Recall 0–2,
  `low_recall_feedback_states_correct_essential_account` must be `true` and
  `passing_feedback_is_appropriately_direct` must remain `false`. The feedback
  must plainly state the source-grounded correct essential account; a vague
  correction fails this check;
- at Recall 3–5, the values reverse. Passing feedback must directly identify
  what essential account was recalled and, when a gap is useful, the single
  most valuable omitted or misframed detail. Generic praise or an indirect
  recap fails this check.

Only after doing that review may the human change the case and document
statuses from `pending` to `approved`, set the seven unconditional checks and
the one applicable score check to `true`, leave the non-applicable score check
`false`, add overall notes, and add a timezone-aware `reviewed_at` timestamp.
Validate the completed file offline:

```sh
python scripts/v2_recall_text_review.py check \
  --luna /path/to/luna-run-1.jsonl \
  --cases /path/to/grounded-recall-v2-approved.json \
  --grounding-manifest cards.json \
  --review /path/to/luna-run-1-text-review.json
```

Each attestation is bound to the run UUID, full evidence SHA-256, manifest
SHA-256, requested model, and every case's case name, semantic fingerprint,
provider response ID, exact decoded-feedback SHA-256, and exact decoded-mastery-
summary SHA-256. It also embeds and hashes the exact hydrated trusted case and
provider-prepared request rebuilt from `--cases` and `--grounding-manifest`.
Drafting, checking, and final comparison all require those same trusted inputs;
each success is replayed through the production V2 parser with the case's real
probe count. Changing, swapping, replacing, trimming, or normalizing the case,
request, result, feedback, or mastery text after review makes the attestation
stale. Review format version 3 is required; older drafts must be regenerated so
none of the trusted-case bindings or expanded quality checks is silently
absent. The comparator requires exactly one
approved attestation for each of the three run UUIDs and rejects missing,
duplicate, unapproved, incomplete, stale, or tampered reviews.

Every response must satisfy the strict schema and the production flow-sensitive
V2 parser using the real number of probes in that case. Each actual Recall must
be within one point of its human label, and there must be **zero behavioral
flips** against the reviewed expectation or across the three trials:

- no `follow_up`/`complete` flip;
- no terminal `again`/`good` flip at Recall 2/3; and
- no terminal Today/Coverage mastery-band flip at Recall 1/2, 2/3, or 3/4; and
- no missing-answer hallucination presented as a valid score.

Report all numeric deviations, even when they stay within the same behavior
bucket. Also report input, cached-input, cache-write, and output token usage
(the provider's output total includes reasoning tokens), latency, technical
failures, and actual cost for every trial. Passing three
trials authorizes only preparation of the consented shadow; it does not
authorize production routing.

Use `v2_recall_compare.py` only with one fresh Claude artifact and exactly three
distinct fresh Luna artifacts. Pass `--luna-text-review` exactly three times for
their approved human reviews; matching is by immutable evaluation run UUID, not
argument order. Supply current provider prices explicitly; the
tool rejects copied response IDs/rows, resumed replicas, semantic mismatches,
encoded failures, behavioral flips, and a cost-per-success reduction below 85%.
It requires `--qualification-expires-at` to equal all four manifests, rejects
non-UTC, future, mixed, or elapsed evidence, and rejects a human text review
dated before its run, after the comparison clock, or at/after expiry.
It also requires the first-row manifest and an exact one-to-one match between
its invocation list and the evidence rows, so deleting a failed or inconvenient
call makes the artifact invalid rather than improving its metrics.
Pass the exact stabilized production Claude requested/returned model and effort
as `--expected-claude-model` and `--expected-claude-effort`. The comparator
requires both the Claude rows and their manifest to match them; an Opus, higher-
effort, or otherwise substituted baseline cannot inflate Claude cost and make
Luna appear to clear the savings gate.
Request a pinned model snapshot whose returned response-model identifier exactly
matches the requested identifier; an alias resolving to a different snapshot is
not exact qualification evidence.

## Stage 4 — prepare the owner-only shadow, still default off

Land the transport, routing, consent, usage, and observability code with
`OPENAI_V2_SCORING_MODE=off`. Tests must prove:

- V1 and non-scoring calls cannot reach OpenAI;
- a non-allowlisted UUID cannot reach OpenAI;
- missing current consent cannot reach OpenAI;
- a fingerprint mismatch cannot reach OpenAI;
- an expired qualification cannot reach OpenAI, including for a session opened
  before the deadline whose next scored turn arrives after it;
- `off` prevents every new OpenAI call;
- shadow output cannot change the API response, session, card, mastery summary,
  schedule, or follow-up chosen by Claude;
- provider errors cannot replace the authoritative Claude result, and any added
  shadow latency is measured and remains inside the owner-only canary;
- raw learner text is not added to telemetry; and
- secrets never appear in logs, exports, responses, or result records.

Only after those tests pass may the owner explicitly approve the production
configuration for Stage 5.

## Stage 5 — collect 100 consented real V2 shadow events

Enable `shadow` only for the single explicit owner UUID and only after that
owner granted the current Anthropic-and-OpenAI disclosure. Claude remains the
sole authority for all 100 events:

- Claude determines follow-up versus completion;
- Claude supplies the response shown to the owner;
- Claude alone supplies the persisted Recall, feedback, and mastery summary;
- Claude alone reaches SM-2; and
- a Luna timeout, failure, or disagreement cannot alter that outcome. The dark
  candidate may add latency while its comparison and usage are captured, so the
  shadow report must measure that cost and stop if it harms session usability.

Before enabling shadow, generate and deploy one fresh UUID as
`OPENAI_V2_SCORING_SHADOW_STAGE_ID`. Every eligible shadow intent receives the
next durable, account-serialized ordinal in that predeclared stage, starting at
1. Use **exactly ordinals 1 through 100**; do not replace an inconvenient,
pending, incomplete, or missing-terminal event with a later success. Each
attempted Luna call, including a technical failure, remains in the denominator
and cost record. A scored follow-up is a separate event because it is a separate
scoring call.

For each event, retain only the minimum comparison data: provider, frozen
fingerprint, turn/probe count, valid/invalid status, behavioral decision,
Recall numeral when valid, latency, token usage, cost, and typed failure reason.
Do not retain a new analytics copy of the question, answer, probe text, trusted
source, feedback, or mastery summary.

The 100-event shadow passes only if:

- all 100 events used current consent, an allowlisted owner UUID, V2, the
  exact qualified fingerprint, and started before the exact deployed
  qualification expiry;
- all 100 Luna calls returned successful V2 results;
- there were zero Luna/Claude behavioral flips in follow-up/completion or the
  terminal scheduler/Today/Coverage branches;
- no shadow value reached a product write or user-visible response;
- observed Luna cost per successful scoring call is at least **85% lower** than
  the paired Claude value; and
- latency, numeric Recall disagreement, token usage, and cost are reported in
  full rather than filtered to exact matches.

If the observed real mix lacks a legal turn state or one side of a product
boundary, extend the shadow under a new explicit event budget; do not waive
Stage 2/3 evidence and do not silently replace the first 100-event report.

Export all intent rows whose `details.shadow_stage_id` equals the predeclared
stage UUID and all terminal rows whose `scoring_event_id` belongs to those
intents. The export must be inclusive from ordinal 1; never use an offset or a
`LIMIT 100` query that can silently return ordinals 2–101. Confirm the intent
ordinals contain 1–100, then run `openai_shadow_report.py --event-count 100
--expected-shadow-stage-id <uuid> --expected-qualification-expires-at <strict-UTC>`
with the exact deployed fingerprint and expiry, current explicit
Anthropic/OpenAI rates, the exact requested and returned model IDs for both
providers, and the single expected owner UUID. The reporter rejects a
row for any other user, unknown export/detail/call fields,
mixed or mismatched models or qualification expiry, an event at or after that
expiry, and a successful call without a provider response ID before it computes
cost. A pre-call intent is independently committed before
every V2 provider orchestration. Pending/incomplete intents reserve outstanding
daily-call capacity and are a hard audit gap. The reporter requires contiguous
stage ordinals 1–100 exactly; a 99-event, 200-event, or later-offset report is
diagnostic and cannot pass qualification.

### Cost-per-success calculation

Use provider invoice rates and observed billable tokens for the shadow period:

```text
Luna cost per success = all Luna scoring charges / valid Luna V2 results
Claude cost per success = all paired Claude scoring charges / valid Claude V2 results
reduction = 1 - (Luna cost per success / Claude cost per success)
```

`all Luna scoring charges` includes spend on invalid, refused, incomplete, or
otherwise failed Luna calls so failures cannot make the candidate look cheaper.
The duplicate Claude call required by shadow is evaluation overhead and is not
part of a projected steady-state Luna primary cost, but both provider totals
must be reported. The gate is `reduction >= 0.85`; a rounded display of 85% is
not sufficient when the unrounded result is below it.

Passing Stage 5 authorizes a separate primary-canary decision. It does not flip
the production mode itself.

## Stage 6 — request an owner-only Luna primary canary

A separate explicit production approval may set `primary` for only the owner
UUID allowlist and only under the qualified fingerprint. Luna is primary only
for V2 Recall scoring, including its scored follow-ups.

The runtime sequence is exactly:

1. make one Luna request;
2. if it returns a valid V2 result, use it without calling Claude;
3. if and only if it raises a typed technical failure, make exactly one Claude
   V2 scoring call with the same prepared semantic request;
4. use the valid Claude fallback if it succeeds; otherwise return the existing
   scoring failure without writing partial state.

There is no fallback because Luna and Claude might disagree, because Recall
moved sharply from history, because feedback seems unusual, or because a score
crossed 2/3. Production has no expected label, so using Claude to overrule a
valid Luna score would be an unobservable best-of-two policy.

The primary canary does not expand the allowlist. Expansion requires completion
of Stage 7 and another explicit provider decision.

## Rollback

The immediate provider rollback is:

1. set `OPENAI_V2_SCORING_MODE=off`;
2. verify new scoring calls route to Claude V2 and no new OpenAI request IDs
   appear;
3. remove the affected UUIDs from the allowlist if additional containment is
   needed; and
4. rotate or remove the OpenAI API key if credential compromise is suspected.

Rollback is mandatory for any consent or allowlist escape, unqualified
fingerprint, OpenAI call outside V2 Recall, more than one OpenAI attempt, more
than one Claude fallback, fallback on a valid Luna result, partial product
write, shadow output reaching product state, or inability to attribute cost and
failures correctly. The owner may also invoke the kill switch for any quality,
latency, reliability, or billing anomaly without waiting for a numeric threshold.

Already completed sessions and history are not rewritten. A request transmitted
before the switch cannot be recalled, but its late result must not re-enable
OpenAI routing. Provider rollback leaves `SCORING_CONTRACT_VERSION=2`; changing
the scoring contract is a separate V2-spec rollback.

## Stage 7 — measure 30 consecutive days at whole-account scope

Keep the primary canary owner-only for 30 consecutive days under one unchanged
qualification fingerprint. Because one qualification expires no more than 30
days after its fresh evidence, renew the same exact contract with another
separately approved four-run qualification before its deadline if the shadow
and measurement cannot fit inside one window; an uninterrupted, same-fingerprint
renewal does not erase already collected account telemetry. A fingerprint
change, an elapsed deadline, provider rollback, material
telemetry gap, or routing defect ends the window; a later canary starts a fresh
30-day measurement.

The final report must use actual provider usage and billing, not pack
projections, and include:

- all OpenAI Devmax charges;
- all Anthropic Devmax charges, including the single-fallback path and every
  workload that intentionally remains on Claude;
- combined whole-account model cost;
- the comparable Claude-only baseline, normalized for scoring-call volume and
  workload mix;
- Luna V2 success, typed-failure, and Claude-fallback counts;
- all-in primary scoring cost per successful call, including failed Luna spend
  and Claude fallback spend;
- scoring p50/p95 latency, including fallback latency;
- Recall and follow-up distributions at aggregate level;
- confirmation that the >=85% scoring cost-per-success advantage remained true;
  and
- actual whole-account savings, stated separately from scoring-path savings.

The whole account is not expected to inherit Luna's scoring percentage because
question generation, coaching, imports, proposals, and other workloads remain
on Claude. The report must show that difference rather than extrapolate the
scoring rate across unrelated calls.

No allowlist expansion, default-provider change, removal of Claude fallback, or
claim of whole-account savings is authorized until this 30-day report is
reviewed and a new explicit decision is recorded.

## Stage-gate summary

| Stage | Required evidence | What passing authorizes |
| --- | --- | --- |
| 0. Decision + dark tooling | Decision record and inert tested plumbing; zero live or paid calls | Preparing Claude V2 activation under its own spec |
| 1. Claude V2 stabilization | V2 active and stable with Claude authoritative | Building the qualification pack |
| 2. Human pack | >=18 approved cards, >=3 weeks, Recall 0–5 and all decision boundaries | A separately approved Luna trial 1 |
| 3. Three Luna trials | Three fresh full-pack runs; zero behavioral flips | Building the dark shadow path |
| 4. Shadow preparation | Default-off, consent, allowlist, fingerprint, isolation, and kill-switch tests | A separately approved owner shadow |
| 5. Real shadow | First 100 consented owner events; Claude authoritative; zero flips; >=85% lower cost/success | Requesting a separate primary canary approval |
| 6. Owner primary | Luna only for V2 Recall; one typed-failure Claude fallback | Starting the owner-only 30-day window |
| 7. Whole-account measurement | 30 consecutive days of complete provider and fallback accounting | A new decision; no automatic expansion |

## Explicitly not authorized here

- any OpenAI or Anthropic evaluation call;
- any production environment or routing change;
- simultaneous V2 and provider activation;
- use of ChatGPT or Codex subscription credits as backend API billing;
- OpenAI for V1, question generation, coaching, re-attempt, imports, proposals,
  or any non-V2-Recall purpose;
- client-selected provider routing;
- a non-consented or non-allowlisted shadow;
- retrying Luna, calling multiple Claude fallbacks, or choosing a favorable
  valid score;
- changing SM-2, follow-up decisions, trusted grounding, transaction boundaries,
  or historical scores;
- expanding beyond the owner UUID allowlist; or
- claiming production or whole-account savings before the 30-day report.
