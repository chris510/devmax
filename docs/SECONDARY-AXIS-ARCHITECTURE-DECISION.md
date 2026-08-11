# Secondary-axis architecture decision

**Decision:** Keep Accuracy, Depth, Boundaries, and the derived composite as the
product contract. Do not replace the displayed score with Accuracy alone and do
not add structured-evidence V1 as a preprocessing stage.

**Next experiment:** Test GPT-5.6 Sol as a direct whole-scorer on the unchanged
production contract, beginning with the separately authorized six-case stage in
[`OPENAI-SOL-SECONDARY-AXIS-CANARY.md`](OPENAI-SOL-SECONDARY-AXIS-CANARY.md).

Production remains on Claude until the complete staged gate passes and a
separate provider-adapter design is reviewed.

## Why this decision was needed

The Terra V8 diagnostic preserved the scheduler-critical Accuracy bucket but
missed secondary-axis labels. Structured evidence V1 then showed that requiring
exact learner quotes does not solve the semantic problem: Terra returned valid
substrings while misclassifying what several substrings proved.

That left two apparent options:

1. keep the three-axis contract and test a more capable model; or
2. prevent uncertain secondary axes from affecting the displayed composite.

The second option initially looked simpler because the scheduler already uses
Accuracy alone. A repository-wide consumer audit shows that it is not a routing
change. It is a broad product redesign.

## Consumer audit

| Consumer | Current dependency | Consequence of removing secondary axes |
| --- | --- | --- |
| SM-2 scheduling | Accuracy only, collapsed to `again` or `good` | None. This boundary is already correct and remains load-bearing. |
| Follow-up decision | Derived composite 2-3 triggers the one allowed probe | Correct-but-thin answers would need a new probe policy. |
| Feedback | The scorer supplies the weaker of Depth or Boundaries when Accuracy passes | Coaching would need a new content-selection contract. |
| Session score block | One 0-5 composite numeral | The meaning of every newly displayed score would change. |
| Today mastery bands | Latest composite | Queue distribution and labels would change. |
| Coverage tiers | Latest composite | Category cold/shaky/developing/solid counts would change. |
| Coverage rollup | Direct means of Accuracy, Depth, and Boundaries | The only visible axis decomposition and its product claim would disappear. |
| Depth-repair Sprint | Selects cards by low Depth or Boundaries | The targeted practice path would disappear or need replacement. |
| Review Sprint ranking | Ranks by latest composite | Suggested practice sets would change. |
| Session Recap and Card History | Composite values and averages | Historical and new sessions would no longer be comparable. |
| Study Plan retrieval suggestions | Existing cards with low composite | Plan-local retrieval candidates would change. |
| Persistence and API | Three session axes, three denormalized card axes, and composite fields | Requires contract, compatibility, migration, fixture, and client work. |

The scheduler is intentionally isolated from secondary-axis noise, but the
study product is not. Depth and Boundaries select coaching, summarize mastery,
and create the targeted repair loop. Replacing the composite with Accuracy
would silently change what Devmax claims to measure while leaving historical
scores with the old meaning.

## Options considered

### Accuracy-only display and reporting — rejected

This would reduce dependence on unreliable secondary grading, but only by
removing designed product behavior. It conflicts with the design rule that the
three axes surface in Coverage and would require a new definition for the 0-5
score, follow-up threshold, mastery bands, Coverage, depth repair, and history.
It may be a valid future product direction, but it needs an explicit new spec
and design handoff rather than being introduced as a provider optimization.

### Structured evidence followed by score calibration — rejected

V1 validated response shape and exact learner substrings but failed semantic
eligibility at 8/12 decisions. A second model call would add latency, cost, and
another nondeterministic boundary without proving the first call reliable. The
architecture moved the attribution error rather than removing it.

### Terra whole-scorer or favorable-result fallback — rejected

Terra remains promising on Accuracy, but it failed the display-signal gates.
Production cannot know the reviewed answer at runtime, so it also cannot retry
or fall back merely because a valid result looks surprising. Such a policy
would select favorable samples and make scoring irreproducible.

### Stronger direct whole-scorer — selected for evaluation

The direct scorer preserves one model call, the production schema, code-derived
composite, follow-up behavior, feedback contract, stored history, and all client
consumers. The only experimental variable is the provider model. This is the
smallest next test that can answer whether the existing product contract is
achievable without redesigning it.

OpenAI documents GPT-5.6 Sol as its frontier model for complex professional work
and lists Responses plus Structured Outputs support. It is materially more
expensive than Terra at $5 per million input tokens and $30 per million output
tokens, so the experiment starts with a six-call stop-stage:
<https://developers.openai.com/api/docs/models/gpt-5.6-sol>.

## Eval design implications

The six cases used in repeated prompt work are no longer suitable as the only
decision set. OpenAI's evaluation guidance recommends task-specific data,
typical and edge cases, human-calibrated labels, scoped automatic metrics, and
human judgment. It also notes that multistep workflows add more independently
nondeterministic model interactions:
<https://developers.openai.com/api/docs/guides/evaluation-best-practices>.

The Sol experiment therefore:

- returns to the single-call production scorer;
- uses 24 already approved release cases absent from recorded OpenAI live
  result stages;
- freezes six balanced cases as the first stop-stage and 18 as the remainder;
- requires both the existing within-one reviewed gate and exact 0-2/3-5
  secondary-axis bucket agreement; and
- retains a manual feedback and grounding audit.

## Production boundary

No result from the six-case canary alone authorizes production. A candidate
provider must pass the unique held-out cases and repeatability stages before an
adapter is designed. Any adapter must still preserve the rule that only
Accuracy reaches SM-2, keep Claude as the default until explicitly switched,
and never hide a valid semantic disagreement behind retries or fallback.
