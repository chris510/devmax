# Study Plan — captured states

iPhone 16e, 390×844, dark, default type unless noted. Captured from the app
running on `MockAPI` fixtures via the `WC_ROUTE` values in `AGENTS.md`.

Compare against the V3.5 frames in `../Study Plan v3.5.dc.html` and the decision
frames in `../legacy/Study Plan v3.4.dc.html` — remembering that the legacy
board's *overview* frame is superseded and must not be matched.

| File | Route / flags |
|---|---|
| `today-plan-line` | default launch |
| `today-no-active-plan` | `WC_PLAN_NO_ACTIVE=1` |
| `today-plan-unavailable` | `WC_PLAN_SUMMARY_FAIL=1` — due cards must still load |
| `overview-collapsed` | `study-plan-overview` |
| `overview-current-expanded` | `study-plan-overview-expanded` |
| `overview-final-expanded` | `study-plan-overview-future` |
| `overview-five-phase` / `-final` | `WC_PLAN_VARIANT=five-phase` |
| `overview-anatomy` | `WC_PLAN_VARIANT=anatomy` |
| `overview-dynamic-type`, `week-dynamic-type` | `content_size accessibility-medium` |
| `overview-reduce-motion` | Reduce Motion on |
| `week-detail`, `item-detail`, `capacity-sheet` | the drill-down |
| `build-guide`, `review-plan`, `import-failure` | creation |
| `replan`, `replan-invalid`, `fixed-recovery` | schedule decisions |
| `reopen`, `reopen-invalid` | reopen decisions |
| `card-proposal`, `card-none-suggested`, `card-existing`, `card-add-failure`, `card-unsupported-subject` | the gate |
| `audit-estimate`, `audit-retrieval`, `audit-dependency` | preview audits |
| `plans-sheet`, `plans-no-active`, `plan-updates`, `plan-complete` | lifecycle |

## What the captures confirm

- All phase headers visible without scrolling at default type, for four and five
  phases, collapsed and with the current or final phase expanded.
- No internal item ids on any user-facing screen.
- No exact completion date anywhere — every forecast is `week of <date>`.
- Status is a word before it is a colour on every row.
- Apply renders natively disabled on `replan-invalid` and `reopen-invalid`.
- Today's due cards render normally with the plan endpoint failing.

## Known gap

**Dynamic Type is not honoured anywhere in the app**, so the two
`-dynamic-type` captures are pixel-identical to their defaults. `WCFont` builds
fixed-size `UIFont`s with no `UIFontMetrics`; this predates Study Plan and
affects every screen. See `docs/DEVIATIONS.md` §28.
