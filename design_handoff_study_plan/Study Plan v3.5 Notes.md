# Study plan V3.5 — density and progressive-disclosure pass

Design-only. No production files touched. **V3.4 remains the behavioural source of truth** —
this pass changes presentation hierarchy and display copy, plus one additive display field
(`overview_title`). No scheduling, correctness, or lifecycle decision was reopened.

Board: `Study Plan v3.5.dc.html` (12 frames, 390×844).

Governing principle: *the farther out the user is, the more visual and compressed the
information; the deeper they go, the more specific and instructional it becomes.*

---

## 1. V3.4 → V3.5 density change log

| Element | V3.4 | V3.5 |
|---|---|---|
| Phase description paragraph ×4 | "See how concrete systems implement and trade off those foundations." etc. | **Removed** from the overview. Preserved in the data model; surfaced only where it earns its place (plan creation, an opened supporting detail) |
| `BUILDS ON FOUNDATIONS` dependency labels ×3 | on every phase row | **Removed** from the ambient map. Dependency reasoning appears in replans and the dependency audit, where it changes a decision |
| `NOW · WEEK 4 · TECHNOLOGIES` + `NEXT BLOCK` card | between header and map | **Removed.** The current phase row already carries position; the header carries `Week 4 of 12` |
| `EACH WEEK · LEARN → PRACTICE → RETRIEVE` | persistent on every visit | **Removed** from the active overview |
| Forecast | in header meta *and* implied by phase status | **One** forecast line: `Est. completion · week of 19 Oct` |
| Collapsed phase height | 3 lines (label+status, description) | **2 lines** — name + status, then week range with current-week position |
| Week rows in an expanded phase | full guide titles ("Coordination, stream processing, and approximate structures") | `overview_title` ("Coordination"), one line |
| Header metadata | two mono lines, uppercase | subject in sans, `Week 4 of 12 · Flexible`, forecast — sentence case |
| Week detail header | 4 capacity-adjacent lines | 2 facts: `3 of 5 Core complete` · `11h of 12h planned →` |
| "Retrieval doesn't block the week" | Week header | Retrieve section subheading |
| "Reviews continue in Today…" | Week header | quiet note under the Retrieve section |
| Internal IDs (`L4-01`, `P4-03`, `R4-02`) | visible on Week detail rows and Item detail meta | **Removed** from all user-facing screens; retained in notes, fixture tables, and engineering annotations |
| Review plan | four audit rows with paragraphs on success | summary block, then one status line per check; only exceptions carry an arrow and a destination |
| Decision screens | explanatory | **unchanged, deliberately** |
| Item detail | explanatory | **unchanged** except the internal ID leaves the metadata |

Lines removed from the default overview: **13** (4 descriptions ≈ 5 rendered lines, 3 dependency
labels, 2 NOW-card lines, 1 rhythm line, 1 duplicated forecast, plus the rules and padding that
carried them). Nothing that answers one of the five overview questions was removed.

## 2. Before / after — the overview

Bottom row of the board, same plan, same scroll position, current phase expanded in the "before".

| | V3.4 | V3.5 |
|---|---|---|
| Phase headers visible without scrolling | 2 (Foundations, Technologies) | **4 of 4** |
| Lines before the first phase row | 7 | 4 |
| Lines per collapsed phase | 3 | 2 |
| Curriculum-description paragraphs | 4 | 0 |
| Representations of "you are in Week 4" | 4 (header, NOW card, phase status, week row) | 1 primary (header) + 1 positional (current phase row) |
| Completion forecasts on screen | 1 in header, restated in the NOW block's implication | 1 |
| Internal IDs on screen | 0 on overview, 11 on Week detail | 0 everywhere |
| Feels like | a document about the plan | a map of the plan |

**Above the fold in V3.5:** plan title, subject, `Week 4 of 12 · Flexible`,
`Est. completion · week of 19 Oct`, and all four phases with status and week range — the complete
answer set for "where am I / what's done / what's next / how much is left / when does it end."

**Information moved deeper, not deleted:** phase descriptions → plan creation and supporting
detail; dependency reasoning → replans and the dependency audit; the weekly rhythm explanation →
onboarding and Preview; capacity breakdown → the capacity sheet; retrieval and review boundaries →
the Retrieve section; full week titles → Week detail and Item detail; internal IDs → engineering
notes.

**Renamed for display only:** status words are now sentence case (`Complete`, `Current`, `Next`,
`Later`, `Upcoming`); `PLANNED · 11H / 12H` became `11h of 12h planned`;
`ACTIVE · CORE · 3 OF 5 COMPLETE` became `3 of 5 Core complete`.

## 3. Content hierarchy specification

**Today — "What should I do now?"**
Due-card hierarchy unchanged. One plan line:
`PLAN · WEEK 4 · TECHNOLOGIES · NEXT TUE 19:00 →`. No phase descriptions, capacity, or progress.

**Plan overview — "Where am I in the journey?"**
Header: `Study plan` / subject / `Week 4 of 12 · Flexible` / `Est. completion · week of 19 Oct`.
Timeline: one row per phase, two lines each — `N · Name` + status, then `Weeks X–Y` with
`· Week 4` on the current phase only. One quiet revision line at the bottom. Nothing else.
Budget: all 3–5 phases in the first viewport · ≤2 lines per collapsed phase · no descriptions ·
no repeated position block · no IDs · one forecast · one phase expanded at a time.

**Expanded phase** — phase header plus its week rows: `Week 4` · `overview_title` · status. No
descriptions, no per-week estimates, no item counts unless a count signals an actionable problem.

**Week detail — "What am I studying this week?"**
`Week 4` / `Technologies` / `3 of 5 Core complete` / `11h of 12h planned →`. Sections carry their
own subheads: `LEARN · Core · 240 min`, `PRACTICE · Core · 210 min · Optional · 90 min`,
`RETRIEVE · 2 of 4 done · Doesn't block the week`, with the reviews note under Retrieve. Rows show
completion state, title, estimate, and `· Optional` only where it applies.

**Item detail — "What exactly do I need to understand or produce?"**
Full title, phase and week, Core/Optional, Why this matters, Done when, Source, Study block,
Estimate, Retrieval support, Notes, complete and reopen. Newsreader retained for Why/Done-when. No
word limit.

**Decision screens — "What changes if I make this choice?"**
Lead with the outcome, then the arithmetic: what changed, why, what stays the same, which weeks are
affected, whether Core changes, whether the deadline moves, what happens after confirmation.
Length is not the constraint here; informed consent is.

## 4. Concise-title generation rules

Two levels, both stored:

```
plan_item / phase / week {
  full_title       // preserved from, or closely derived from, the guide
  overview_title   // 2–5 words, generated during Preview plan
}
```

Rules the generator follows:

1. Name the subject of the week, not the activity list.
2. 2–5 words, ≤22 characters where possible.
3. Never a truncation or an ellipsis — generate a meaningful label instead.
4. Keep the guide's own terminology; don't introduce vocabulary the user never wrote.
5. Stay subject-agnostic: no interview-prep framing in a law or anatomy plan.
6. Unique within its phase; if two weeks would collide, qualify the second.
7. Ambiguous fragments are disallowed ("Systems", "Part 2", "Advanced").

Worked examples:

| Full title | Overview title |
|---|---|
| Relational and NoSQL systems | Databases |
| Streaming, search, and large-object delivery | Streaming & search |
| Coordination, stream processing, and approximate structures | Coordination |
| Caching, sharding, consistent hashing, and CAP | Caching & sharding |
| Cardiovascular electrical conduction and rhythm | Cardiac conduction |
| Glomerular filtration and tubular transport | Filtration |
| Acid–base balance and renal compensation | Acid–base |
| Constitutional standards of judicial review | Judicial review |

No new confirmation burden: short titles are generated silently and edited through the existing
plan-editing flow. They are display-only and never used in scheduling, dependency, or audit logic.

## 5. Subject-agnostic proof

STATE 9 renders the same overview for a 10-week anatomy plan: four phases (Cell & tissue,
Cardiovascular, Respiratory & renal, Integration), `Week 4 of 10 · Flexible`, and the
Cardiovascular phase expanded to `Cardiac cycle` / `Cardiac conduction` / `Circulation`. Same
compression, same two-line rows, no technical-interview vocabulary anywhere. Long clinical titles
compress without truncation.

## 6. Accessibility matrix

**P** = implemented in this prototype · **S** = required in SwiftUI · **N** = not represented.

| Behaviour | Mechanism | Status |
|---|---|---|
| Short visual title, full accessible name | phase button `aria-label` = "Expand Cardiovascular phase. Weeks 3–5 · Week 4. Current."; week row `aria-label` uses the **full** guide title, not the overview title | P |
| No compactness from truncation | no `text-overflow`, no ellipsis, no fixed-height text box anywhere; `text-wrap: pretty` on every wrapping string | P |
| Expansion state announced | `aria-expanded` + `aria-controls` on native `<button>`; expands and collapses for real | P |
| Status in text, not colour | `Complete` / `Current` / `Next` / `Later` / `Upcoming` are words; the accent dot and colour are reinforcement | P |
| Reading order follows plan order | DOM order is phase 1 → 4, week 1 → n; the rail is `aria-hidden` | P |
| Dynamic Type reflows rather than overlaps | STATE 10 renders the same overview at ~1.45× type: rows wrap to two lines, the map scrolls, nothing clips or overlaps, and no type size was reduced to keep one screen | P |
| Scrolling acceptable at accessibility sizes | the map container scrolls; the one-viewport target applies at default type only | P |
| Enter / Space activation | native `<button type="button">` throughout | P |
| Native radios for exclusive choice | unchanged from V3.4 | P |
| Home / End in radio groups | **not claimed** | N |
| Native `disabled` for blocked actions | unchanged | P |
| Escape dismisses, focus restored | unchanged | P |
| Focus trapped while a sheet is presented | unchanged | P |
| Initial focus on presentation | landing target present; live transition not stageable on a board | S |
| Background inert behind a sheet | — | S |
| 44px minimum row height | overview collapsed phase rows 44.5px (62.3px when a description wraps in the legacy comparison, 94.5px at 1.45× type) · overview week rows 44.8px · week-detail item rows 45px · capacity affordance 44px (negative margin keeps its optical position) | P |
| Stepper 44×44 | the capacity sheet is not part of this 12-frame set; unchanged and verified in V3.4 | inherited |
| 10px mono floor, 13px body floor | unchanged | P |

## 7. 390×844 render verification

| Check | Result |
|---|---|
| All 4 phase headers visible in the default overview without scrolling | yes — header block ends ~150px, four two-line rows plus rail occupy ~250px |
| All 4 phase headers still visible with the current phase expanded | yes — the three week rows add ~130px and the fourth phase stays above the fold |
| All 4 phase headers visible with the *last* phase expanded | yes (STATE 4) |
| Anatomy plan, 4 phases, current expanded | yes (STATE 9) |
| Horizontal overflow | none in any frame |
| Clipped or ellipsised copy | none |
| Internal IDs on any user-facing screen | none |
| More than one phase expanded | never — one at a time, enforced in state |
| Mono below 10px / body below 13px | none |
| Dynamic Type frame overlap | none — reflows and scrolls |

## 8. Information removed, relocated, or renamed

**Removed from user-facing screens entirely:** internal item IDs; the `EACH WEEK · LEARN →
PRACTICE → RETRIEVE` line; the `NOW` card; the duplicated forecast.

**Relocated deeper:** phase descriptions (→ plan creation / supporting detail); `BUILDS ON …`
dependency labels (→ replans and the dependency audit); "Retrieval doesn't block the week" (→
Retrieve section subhead); "Reviews continue in Today…" (→ note under Retrieve); the plan-work /
retrieval / capacity breakdown (→ capacity sheet); full week titles (→ Week detail, Item detail).

**Renamed for display only:** status words to sentence case; `PLANNED · 11H / 12H` →
`11h of 12h planned`; `ACTIVE · CORE · 3 OF 5 COMPLETE` → `3 of 5 Core complete`; header metadata
from uppercase mono to sentence-case sans plus one mono line.

**Preserved unchanged:** every V3.4 behaviour; the vertical timeline rail, node treatment, and
status vocabulary; Item detail's explanatory sections and editorial typography; all decision-screen
copy; the four motion patterns; the accent's restricted role.

**Added:** `overview_title` on phases, weeks, and items — display-only, generated during Preview,
user-editable, never used in logic.

## 9. Acceptance

A user opening Plan overview sees the whole journey — four phases, current position, and one
forecast — before reading about any individual part of it. The default overview does not scroll to
reveal the final phase at default type. Week detail reads as a work list; Item detail as a complete
brief; decision screens still explain enough to choose safely.

No blockers. The two open items from V3.4 remain open product decisions, unaffected by this pass:
what a repeatedly missed study-block reminder should do, and whether Duplicate carries the guide
text.
