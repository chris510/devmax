# Design handoff — Study Plan

The approved handoff for the Study Plan feature, copied verbatim from the V3.5 design
archive. Two documents are authoritative and they own different things.

| File | Authority | Owns |
|---|---|---|
| `Study Plan v3.4 Notes.md` | **Behavior** | The 10-rule weekly scheduler, the canonical Week 4 arithmetic, fixed-deadline recovery, the five-question card gate, atomic/idempotent acceptance, deterministic duplicate checking, dependency provenance, lifecycle, and the data model. |
| `Study Plan v3.5 Notes.md` | **Presentation** | Density, content hierarchy per level, `overview_title` and its generation rules, display copy and casing, the accessibility matrix, and the 390×844 render budget. |
| `Study Plan v3.5.dc.html` | **Presentation** | 12 rendered frames at 390×844. Open in a browser with `support.js` beside it. |

Where the two disagree, **V3.4 wins on behavior and V3.5 wins on display**. V3.5 explicitly
reopened no scheduling, correctness, or lifecycle decision — it changed hierarchy, copy, and
added one display-only field.

Where either disagrees with `AGENTS.md`, **`AGENTS.md` wins**. The engineering translation
of both documents, including every intentional divergence, is `docs/STUDY-PLAN-SPEC.md`;
that file is what the code is built against.

## `legacy/` — do not implement

`legacy/Study Plan v3.4.dc.html` is kept only so the density comparison in
`Study Plan v3.5 Notes.md` §2 can be checked against something. **Its Plan overview frame
is the superseded design.** It still renders, and V3.5 removed all of it:

- the `NOW · WEEK 4 · TECHNOLOGIES` card and its `NEXT BLOCK` line
- four phase-description paragraphs on the overview
- three `BUILDS ON …` dependency labels
- the persistent `EACH WEEK · LEARN → PRACTICE → RETRIEVE` line
- a second, duplicated completion forecast
- internal item IDs (`L4-01`, `P4-03`, `R4-02`) on Week detail and Item detail

None of those may appear in the app. Everything else in that file — the replan, recovery,
reopen, card-proposal, dependency-audit, Plans-sheet, capacity-sheet, and plan-complete
frames — is current and is the reference for those screens, because V3.5 deliberately left
every decision screen unchanged.

## Fixture caveat

Curriculum content in both boards is illustrative. `docs/CURRICULUM.md` is the real
curriculum; the mock fixtures in `ios/Devmax/Services/MockAPI.swift` reproduce the V3.4
Week 4 table exactly because its arithmetic is what the scheduler tests assert against.
