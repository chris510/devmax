# Study plan V3.4 — final simplification and engineering closure

Design-only. No production SwiftUI, backend, schema, or migration files touched.
Board: `Study Plan v3.4.dc.html` (20 frames, 390×844). V3.3 kept for comparison.

Architecture and visual direction unchanged: **Today → Plan overview → Week detail → Item detail.**

---

## 1. V3.3 → V3.4 change log

| # | V3.3 | V3.4 |
|---|---|---|
| 1 | Slot allocator, exact-date forecasts, Availability frame | **Removed.** No `availability_slots`, `slot_overrides`, `projected_completion_precision`, or `forecast_end_slot_id`. No bin-packing scheduler. Forecasts are plan-week only: `Est. completion · Week 12 · week of 19 Oct`. Optional study blocks stay as local reminders — not capacity, not dependencies, not forecast inputs |
| 2 | Allocation order implied by prose | One deterministic 10-rule weekly scheduler (§2). Items are atomic for MVP, with an explicit "doesn't fit in a single study week" outcome |
| 3 | Fixture totals didn't reconcile (660 min claimed, 450 min listed; conflicting slot totals; inconsistent calendar) | One canonical Week 4 fixture, every item listed with ID, type, priority, estimate, dependency, original and proposed week. Every weekly total equals the sum of its rows (§3) |
| 4 | `reserved_review_minutes` inside plan capacity | **Removed.** Global SM-2 reviews are not owned by the plan and can't make it reflow. Plan capacity includes plan-local retrieval only. Week detail carries one capacity line plus one quiet sentence |
| 5 | Fixed-deadline copy implied a completion day | `Fits before the 18 Oct deadline.` The 68h/44h recovery is itemised down to the minute (§5) |
| 6 | Failed card candidate still offered "Add back" | Gate has no bypass. `SUGGESTED` (5-of-5 only, selectable, counted) and `NOT SUGGESTED` (title, failed question, reason, no action). Count copy matches: "1 focused card suggested" |
| 7 | Acceptance described but not modelled | `card_proposal_acceptance` record with idempotency key, request hash, proposal revision, and status; committed in the same transaction as the cards (§7) |
| 8 | Duplicate checking left to semantic judgement | Exact normalized-topic match is deterministic and blocking. Possible overlap becomes a visible user choice: Keep the new one / Use existing card / Edit / Skip (§8) |
| 9 | Week detail showed five capacity-adjacent lines | Four lines: Core completion, retrieval completion, `PLANNED · 11H / 12H →`, and "Reviews continue in Today and don't change this plan." |

Preserved without change: flexible default, optional fixed deadline, confirmed replans, Core /
Optional / plan-local Retrieval, Learn → Practice → Retrieve, completion separate from mastery and
readiness, at most one active plan with zero valid, completed separate from archived, reopen
preserving cards / scores / sessions / mastery / SM-2, atomic card creation after explicit approval,
dependency provenance, native accessibility behaviour, the dark Devmax system, progressive
disclosure, no gamification, no readiness percentage.

## 2. The deterministic weekly scheduler

Minutes internally. Hours are display only.

```
effective_capacity_minutes(week) =
    week.override_minutes ?? plan.default_weekly_capacity_minutes

scheduled_plan_minutes(week) =
    learn_minutes + practice_minutes + plan_local_retrieval_minutes
    // Core, Optional, and recurring retrieval all consume capacity.
    // Global SM-2 review time is NOT included.
```

Allocation order, applied in this sequence:

1. Preserve completed work in place.
2. Preserve hard dependency order.
3. Preserve phase order.
4. Preserve original guide order within a phase.
5. Place Core before Optional.
6. Place plan-local retrieval after its source item.
7. Carry work forward when the current week is full.
8. Never move work backward across an unmet hard dependency.
9. Never remove Core work automatically.
10. Never mutate the saved schedule until the user confirms the proposal.

Items are **atomic**: one item never splits across weeks. If a single item exceeds its week's
capacity the scheduler returns *"This item does not fit in a single study week."* and offers:
increase that week's capacity · edit the estimate · move the item outside the plan.

Validation:

```
validate(proposal):
    for each affected_week:
        if scheduled_plan_minutes > effective_capacity_minutes: UNRESOLVED
    for each hard dependency:
        if prerequisite finishes after dependent opens: UNRESOLVED
    if plan.mode == FIXED and final_scheduled_plan_week ends after deadline: UNRESOLVED
    if a Core item was removed without confirmation: INVALID
    else VALID
```

`Apply adjustment` is a native `disabled` button unless the **currently selected** option
validates. Finishing early is valid.

## 3. Canonical Week 4 fixture

**Plan:** Senior backend interview · default 720 min/week (12h) · flexible ·
est. completion Week 12, week of 19 Oct.

**Baseline Week 4 · 660 / 720 min.** Every item in the week:

| ID | Title | Phase | Type | Priority | Est. | Depends on | Status | Orig. week | Proposed week |
|---|---|---|---|---|---|---|---|---|---|
| L4-01 | PostgreSQL query and write paths | Technologies | Learn | Core | 90 | — | complete | 4 | 4 |
| L4-02 | Redis, DynamoDB, and Cassandra | Technologies | Learn | Core | 90 | — | complete | 4 | 4 |
| L4-03 | API gateway mechanics | Technologies | Learn | Core | 60 | L4-02 | open | 4 | **4** |
| P4-01 | Three timed coding sessions | Technologies | Practice | Core | 120 | — | complete | 4 | 4 |
| P4-02 | Begin the behavioral story catalog | Technologies | Practice | Core | 90 | — | open | 4 | **6** |
| P4-03 | Rewrite your consistent-hashing explanation | Technologies | Practice | Optional | 30 | — | open | 4 | **5** |
| P4-04 | Read one published architecture write-up | Technologies | Practice | Optional | 60 | — | open | 4 | **6** |
| R4-01 | Closed-book explanation of the read and write paths | Technologies | Retrieve | Recurring | 30 | L4-01 | complete | 4 | 4 |
| R4-02 | Explain when you'd choose Cassandra over DynamoDB | Technologies | Retrieve | Recurring | 30 | L4-02 | complete | 4 | 4 |
| R4-03 | Redraw the gateway request flow from memory | Technologies | Retrieve | Recurring | 30 | L4-03 | open | 4 | **5** |
| R4-04 | Weekly closed-book pass on Weeks 1–3 | Technologies | Retrieve | Recurring | 30 | — | open | 4 | **6** |

Sum = 90+90+60+120+90+30+60+30+30+30+30 = **660 min** ✓ (matches the displayed baseline)

**The override.** User sets Week 4 to **420 min (7h)**.

- Completed work preserved (rule 1): L4-01 90 + L4-02 90 + P4-01 120 + R4-01 30 + R4-02 30 = **360 min**
- Room left for open work: 420 − 360 = **60 min**
- Open work needing placement: L4-03 60 + P4-02 90 + P4-03 30 + P4-04 60 + R4-03 30 + R4-04 30 = **300 min**
- L4-03 is Core, its prerequisite L4-02 is complete, and it is first in guide order → placed in the 60 min (rule 5)
- **Overflow = 300 − 60 = 240 min**

**Placement of all 240 minutes.** Each item goes to the earliest week at or after its
dependency-satisfied week that has room (rules 2–7). Week 5 baseline 660/720 → 60 min slack;
Week 6 baseline 480/720 → 240 min slack.

| Item | Est. | Week 5 slack before | Placed | Week 6 slack before | Placed |
|---|---|---|---|---|---|
| P4-02 (Core) | 90 | 60 — doesn't fit | — | 240 | **Week 6** |
| P4-03 (Optional) | 30 | 60 | **Week 5** | — | — |
| P4-04 (Optional) | 60 | 30 — doesn't fit | — | 150 | **Week 6** |
| R4-03 (Recurring, after L4-03 in Week 4) | 30 | 30 | **Week 5** | — | — |
| R4-04 (Recurring) | 30 | 0 | — | 90 | **Week 6** |

Placed: 60 min → Week 5, 180 min → Week 6. **60 + 180 = 240 ✓ · 0 unresolved.**

**Recalculated totals**

| Week | Before | After | Capacity | Fits |
|---|---|---|---|---|
| 4 | 660 | 360 + 60 = **420** | 420 (override) | yes |
| 5 | 660 | 660 + 30 + 30 = **720** | 720 | yes, exactly full |
| 6 | 480 | 480 + 90 + 60 + 30 = **660** | 720 | yes, 60 min slack |
| 7–12 | unchanged | unchanged | 720 | nothing reached them |

**Forecast: Est. completion · Week 12 · week of 19 Oct — unchanged.** L5-01 depends on L4-03,
which stays in Week 4, so no hard dependency is violated and no phase moves. No day is claimed.

**Unresolved variant (STATE 6).** Same 240 min overflow, but Weeks 5 and 6 are each at 690/720 →
60 min of slack in total, 180 min unresolved. The selected option defers P4-03 30 + P4-04 60 +
R4-03 30 + R4-04 30 = 150 min, leaving Core P4-02 (90 min) against 60 min of slack → **30 min
unresolved, validation fails, Apply disabled.** Core is never dropped to force a fit. Two other
options do validate: add one plan week (720 fresh min, forecast moves to Week 13, week of 26 Oct),
or raise Weeks 5 and 6 to 840 min each (300 min of slack ≥ 240).

**Reopen (STATES 8 and 9).** Week 4 sits on the 420-min override with 0 spare. Reopening L4-01
returns 90 min → 510 against 420.
Option A raises the week override to 510 min → 510/510, nothing carries forward, forecast
unchanged, Apply enabled.
Option B carries L4-03 (60 min) into Week 5 instead: Week 4 becomes 450/420 and Week 5 becomes
780/720 → 90 min unresolved across two weeks, Apply disabled.

## 4. Global reviews stay outside plan capacity

`reserved_review_minutes` is gone from Study Plan scheduling. The global Devmax review queue is not
owned by the active plan, so an anatomy or law plan can never be made to reflow by SM-2 volume.
Plan capacity includes plan-local retrieval activities because those came from the imported guide.

Week detail carries exactly:

```
ACTIVE · CORE · 3 OF 5 COMPLETE
RETRIEVAL · 2 OF 4 DONE · DOESN'T BLOCK THE WEEK
PLANNED · 11H / 12H →
Reviews continue in Today and don't change this plan.
```

One budget, one affordance. Card confirmation states the consequence in the other direction:
*"These cards join Today's review schedule. Their reviews don't change this Study Plan."*

## 5. Fixed-deadline recovery, itemised to 1,440 minutes

Remaining plan work **68h = 4,080 min** (Core 44h · Optional 15h · recurring retrieval 9h).
Available **44h = 2,640 min** (4 plan weeks × 11h). Gap **24h = 1,440 min**.

**Option 1 · Reduce scope.** Deferred Optional items:

| ID | Title | Week | Est. |
|---|---|---|---|
| O-01 | Read one published architecture write-up | 9 | 60 |
| O-02 | Second architecture write-up | 10 | 60 |
| O-03 | Company engineering-blog pass | 11 | 90 |
| O-04 | Extra timed coding set · graphs | 9 | 120 |
| O-05 | Extra timed coding set · dynamic programming | 10 | 120 |
| O-06 | Third full system design | 10 | 180 |
| O-07 | Behavioral story polish · two stories | 11 | 120 |
| O-08 | Target-company overlay reading | 11 | 90 |
| O-09 | Mock-interview retro write-up | 12 | 60 |
| | **Optional subtotal** | | **900** |

Deferred recurring retrieval:

| ID | Title | Week | Est. |
|---|---|---|---|
| R-11 | Weekly closed-book pass | 9 | 30 |
| R-12 | Weekly closed-book pass | 10 | 30 |
| R-13 | Weekly closed-book pass | 11 | 30 |
| R-14 | Weekly closed-book pass | 12 | 30 |
| R-15 | Cumulative diagram redraw | 11 | 90 |
| R-16 | Cumulative diagram redraw | 12 | 90 |
| R-17 | Behavioral rehearsal aloud | 12 | 120 |
| R-18 | Design-gap explanation pass | 10 | 120 |
| | **Recurring subtotal** | | **540** |

**900 + 540 = 1,440 min ✓** Remaining after deferral = 4,080 − 1,440 = 2,640 min = 44h = exactly the
Core work, against 2,640 min available. No Core item touched. Validation: fits before 18 Oct.

**Option 2 · Increase capacity.** 18h × 4 plan weeks = 4,320 min ≥ 4,080 min. Nothing deferred, no
Core change. Validation: fits before 18 Oct.

**Option 3 · Edit the proposal.** 0 of 1,440 min reconciled; validation pending, primary disabled.

"Compress" remains removed: merging two practice blocks changes when work happens, not how much
there is.

## 6. Curriculum-honesty gate

All five questions apply per candidate. Failing any one makes the candidate **not selectable and not
counted**.

1. What source lesson or observed failure justifies the card?
2. Can its mechanism be reconstructed in under two minutes?
3. Does it test a scenario rather than a definition?
4. Would a different answer change an interview decision or reveal a real gap?
5. Is it more useful than spending the same review budget on an existing weak card?

Two sections, no bypass:

- **SUGGESTED** — 5-of-5 candidates only. Select / Edit / Skip. Counted in the Add label.
- **NOT SUGGESTED** — title, the failed question, a one-line reason, **no Add back**.

STATE 10 shows one passing card ("Postgres write path", gate 5 of 5) and one rejected
("Autovacuum thresholds · FAILED Q3 · TESTS A DEFINITION, NOT A SCENARIO"). The header reads
"1 focused card suggested". STATE 11 shows both candidates failing and no Add action at all.
Writing a card by hand through Add card remains available and is separate from the proposal.

Proposals stay limited to subjects covered by the technical scoring rubric. Law, anatomy, and
language plans use plan-local retrieval activities and create no Devmax cards.

## 7. Atomic, idempotent card acceptance

```
card_proposal_acceptance {
  id
  proposal_id
  idempotency_key
  request_hash              // selected ids + edited content
  proposal_revision
  status: processing | committed | failed
  created_card_ids
  committed_at
}

POST /study-plans/{plan_id}/card-proposals/{proposal_id}/accept
  { selected_item_ids, edits, idempotency_key, proposal_revision }
```

- All selected cards are created in one transaction; the acceptance record commits inside it.
- A retry after commit returns the original response and creates nothing.
- A retry after rollback may safely run again.
- Same key + different `request_hash` → conflict.
- Editing the proposal creates a new `proposal_revision` and a new acceptance intent.
- The UI shows `ADDED` only after a committed response.
- A response lost after commit cannot produce duplicates on retry.

STATE 12 is the rollback: "Couldn't add the card. Your selection and edits are still here." with
`ACCEPTANCE ROLLED BACK · NO CARD ROWS AND NO ACCEPTANCE RECORD WERE COMMITTED`. STATE 13 is the
retry: same key and hash, no committed acceptance found, duplicate check re-run, card and record
committed together, `1 card added` shown only afterwards.

## 8. Deterministic duplicate checking

Normalize topic: trim, lowercase, collapse internal whitespace, apply the same punctuation rules.

- **Exact normalized-topic match = existing card.** Never proposed, never duplicated.
- Category may be shown to explain a match but never permits a duplicate topic.
- The exact check runs again **inside the acceptance transaction**. If a duplicate appeared since
  Preview, acceptance aborts and that candidate refreshes as `EXISTING`. The batch is never
  partially created while claiming atomic success.
- **Possible semantic overlap** is surfaced, never decided silently: `POSSIBLE EXISTING CARD` with
  the existing card's write date, score, and due date, and four actions — Keep the new one · Use
  existing card · Edit the proposal · Skip (STATE 14).

## 9. Dependency provenance

```
plan_item_dependency {
  prerequisite_item_id  dependent_item_id
  kind: hard | soft
  source: imported | inferred | user_added
  confidence  rationale  source_excerpt  confirmed_at
}
```

Strong imported ordering (explicit prerequisite language) is accepted automatically and is not put
in front of the user. Only uncertain, contradictory, or schedule-changing links reach the audit
(STATE 15: 31 imported · 6 inferred · 1 needs confirmation). An unconfirmed dependency that changes
a forecast takes the conservative ordering and explains itself: *"We kept these topics in order
because the guide appears to make one depend on the other."*

## 10. Lifecycle

```
plan { status: active | paused | completed | archived, paused_at?, completed_at?, archived_at? }
```

At most one active; zero is valid (Today shows `PLAN · ADD A STUDY GUIDE →`). Completed is not
archived — both are listed separately in the Plans sheet.

**Duplicate** copies guide provenance, phases and items, dependencies, estimates and user edits.
It resets item completion, retrieval completion, dates, and reminder settings. It never copies
cards, scores, sessions, SM-2 state, or plan history.

## 11. Final accessibility matrix

**P** = implemented in this prototype · **S** = required in SwiftUI · **N** = not represented.

| Behaviour | Mechanism | Status |
|---|---|---|
| Enter / Space activation | native `<button type="button">` throughout; no `role="button"` divs | P |
| Exclusive choice | native `<input type="radio">` sharing a `name` inside `role="radiogroup"` + `aria-label`; arrows and Space are native | P |
| Home / End in radio groups | **not claimed** | N |
| Disclosure state | `<button aria-expanded aria-controls>`; expands and collapses for real | P |
| Disabled actions | native `disabled` + `aria-disabled="true"`; nothing focusable carries `aria-disabled` | P |
| Escape dismisses | `onKeyDown` sets real state; the dialog unmounts | P |
| Focus restored to trigger | stored ref focused after the state commit; the post-dismiss frame shows the ring on the trigger | P |
| Tab / Shift+Tab trapped while open | handler wraps at the dialog's first and last focusable node | P |
| Initial focus on presentation | `<h2 tabindex="-1">` present as the landing target; the live transition can't be staged on a canvas board | S |
| Background inert behind a sheet | — | S |
| No focus into hidden content | dismissal unmounts rather than hides | P |
| Status square not a control | `aria-hidden` span; the row is the button | P |
| 44×44 targets | stepper buttons exactly 44×44 with a 32×30 glyph inside; rows ≥44px | P |
| Value change announced | `aria-live="polite"` on the hours readout | P |
| Failure announced | `role="alert"` on the card-add failure strip | P |
| Status expressed in text | every state is a word as well as a colour | P |
| Larger accessibility text reflow | — | N |
| VoiceOver heading order / rotor | — | S |

## 12. Final data-model implications

**Removed from the proposal:** `plan.availability_slots`, `week.slot_overrides`,
`projected_completion_precision`, `forecast_end_slot_id`, `week.reserved_review_minutes`.

**Final shape:**

```
plan {
  id  title  subject
  guide_text                        // stored verbatim
  status: active | paused | completed | archived
  mode: flexible | fixed
  deadline?                         // exact user constraint
  default_weekly_capacity_minutes
  forecast_end_plan_week            // integer plan week; display adds "week of <date>"
  paused_at?  completed_at?  archived_at?
}

week { plan_id  index  override_minutes?  advanced_at? }

plan_item {
  id  plan_id  week_index  phase_index  guide_order
  type: learn | practice | retrieve
  priority: core | optional | recurring
  estimate_minutes                  // 30-min increments, atomic
  estimate_source: imported | generated | user_edited
  estimate_confidence: high | medium | needs_review
  source_start  source_end  source_excerpt
  done_when  status  completed_at?  reopened_at?
  source_item_id?                   // retrieval generated from a Learn/Practice item
  origin: imported | generated | manual
  approved_at?                      // generated retrieval, approved in Preview
}

plan_item_dependency { … as §9 }

card_proposal { id  source_plan_item_id  revision  reason
                gate_results[5]  duplicate_check_result  disposition }

card_proposal_acceptance { … as §7 }

plan_revision { kind  before  after  created_at  reversible }
plan_duplication { source_plan_id  copied_at }
```

Untouched by plan progress, replanning, reopening, pausing, and resuming: cards, scores, sessions,
mastery, SM-2 fields. Only a committed `card_proposal_acceptance` creates cards.

## 13. Mechanical verification

| Check | Result |
|---|---|
| IBM Plex Mono below 10px | none |
| Body copy below 13px | none |
| `div role="button"` | none |
| Nested interactive controls | none |
| Focusable element with `aria-disabled="true"` | none |
| Radio group without native arrow behaviour | none |
| Stepper target below 44×44 | none |
| Slot-level scheduling anywhere | none — the Availability frame and all slot fields are gone |
| Exact completion day or time claimed from weekly capacity | none — every forecast is a plan week |
| Displayed replan where a week exceeds capacity beside an enabled Apply | none |
| Weekly total not equal to the sum of its listed items | none — §3 reconciles 660, 420, 720, 660 |
| Overflow minutes unaccounted for | none — 240 = 60 + 180 |
| Recovery option summarised without item IDs | none — §5 lists 17 items totalling 1,440 min |
| `Added` before commit | none |
| Bypass on a failed gate candidate | none — NOT SUGGESTED has no actions |
| `Undo completion` copy | none |
| Second visible workload budget | none — one `PLANNED · 11H / 12H` line |

## 14. Conclusion

**Ready for engineering specification. No unresolved behavior affects schema or scheduling.**

The four items that previously blocked schema work are closed: slot packing is removed rather than
specified, review-reserve estimation is removed from plan capacity, the slot-toggle control no
longer exists to design, and duplicate checking is an exact normalized-topic rule with the
ambiguous case escalated to the user instead of guessed.

Two things remain **product decisions rather than blockers** — either answer is implementable, and
neither changes the schema or the scheduler:

- Whether a study-block reminder that is repeatedly missed should prompt anything at all. Current
  design: it does not.
- Whether an archived plan's `Duplicate` should carry the original guide text or re-prompt for a
  fresh paste. Current design: it carries the guide, and Preview runs again over it.

Fixture content remains illustrative: `AGENTS.md`, `docs/CURRICULUM.md`, and
`design_handoff_devmax_initial/` are not reachable from this project, so replace week and item
content with the real curriculum. The five gate questions in §6 are embedded, so nothing in the
design depends on that document any more.
