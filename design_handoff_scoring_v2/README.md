# Scoring Contract V2 — design amendment

This amendment is authoritative only when
`active_scoring_contract_version == 2`. The original handoff continues to own
all tokens, typography, spacing, motion, and V1 presentation.

The V2 design removes numeric Depth, Boundaries, and composite claims. Recall
is the only numeral. Optional deeper practice is qualitative, opt-in, and uses
the existing conversation thread without adding motion or a new destination.

## Approved states

All references use the authoritative 390×844 iPhone 16e frame.

| State | Reference | Behavioral contract |
| --- | --- | --- |
| Recall result and offer | `screenshots/recall-result.png` | One `/ 5 RECALL` numeral; **Go one level deeper** is secondary and opt-in. |
| Depth question | `screenshots/depth-practice.png` | Fixed qualitative prompt in follow-up typography; no score input or new metric. |
| Depth feedback | `screenshots/depth-practice-answered.png` | Learner answer plus qualitative note; original Recall remains the only numeral. |
| Boundary question | `screenshots/boundary-practice.png` | Same layout with the deterministic boundary prompt. |
| Boundary feedback | `screenshots/boundary-practice-answered.png` | Qualitative note only; no pass/fail or mastery claim. |
| Mixed history | `screenshots/history-mixed.png` | Recall rows remain numeric; composite-only history is visibly legacy and excluded from Recall average. |
| Coverage | `screenshots/coverage-recall.png` | Recall tiers only; no axis rollup or Depth-repair action. |
| Review Sprint | `screenshots/sprint-recall.png` | Lowest Recall then least-recently-reviewed; no secondary-axis sprint kind. |
| Session Recap | `screenshots/recap-recall.png` | Recall-only average labeled `/ 5 AVG RECALL`. |
| Public onboarding | `screenshots/onboarding-recall.png` | Explains one Recall score and coaching without grades. |

## Copy

- Eyebrow: `YOUR FIRST SCORE`
- Title: `One recall score. Coaching without grades.`
- Body: `Recall measures whether the essential account was correct. It is the
  only signal that schedules the topic. When useful, you can practice going
  deeper or testing a boundary — without turning those answers into mastery
  scores.`
- Result action: `Go one level deeper`
- Recap label: `/ 5 AVG RECALL`
- Sprint ranking: `Lowest Recall and least recently reviewed first`

## Reproduction

Debug builds use `WC_SCORING_V2=1` to expose the V2 capability with `MockAPI`.
The new routes are `coaching`, `coaching-answered`, `coaching-boundary`, and
`coaching-boundary-answered`; existing `history`, `coverage`, `sprint-setup`,
`recap`, and `score` routes select their V2 presentation through the same
flag. These fixtures make no provider call.
