# Claude explicit-evidence V2 A2

**Run date:** 2026-08-08 (America/Los_Angeles)

**Decision:** Passed; broader axis regression not yet run

## What ran

A2 evaluated `explicit-evidence-v2` on the five approved speech-noise cases not
used by the one-call A1 gate. A1 had already corrected the known blocking case;
A2 tested whether the stricter evidence ceilings damaged the rest of the same
reviewed family.

| Control | Value |
| --- | --- |
| Model | `claude-sonnet-5` |
| Effort | `low` |
| Cases | Five approved speech-noise regression cases |
| Concurrency | 1 |
| Prompt | Production rubric plus evaluation-only `explicit-evidence-v2` |
| Production change | None |
| Preflight ceiling | **$0.0574** |
| Paid calls | **5** |
| Calculated cost | **$0.045852** |

The fresh free preflight counted 15,866 input tokens and reserved 2,560 output
tokens. The paid stage made exactly five Message calls.

## Gate result

| Criterion | A2 result | Gate |
| --- | ---: | --- |
| Structured responses parsed | 5/5 | Pass |
| False Accuracy passes / failures | 0 / 0 | Pass |
| Accuracy exact / within one | 2/5 · 5/5 | Pass safety boundary |
| Composite exact / within one | **5/5 · 5/5** | **Pass** |
| Mean composite deviation | 0.00 | Pass |
| Depth exact / within one | 3/5 · 5/5 | Pass |
| Boundaries exact / within one | 2/5 · 5/5 | Pass |
| Feedback/evidence audit | 5/5 consistent | Pass |

No secondary axis reached 3. That is correct for these five answers: none
explicitly connected a choice to a cost or a trigger to a concrete adverse
outcome. The stage therefore validates V2's withholding behavior across the
regression remainder; it does not yet prove that V2 awards genuine secondary
evidence.

## Case matrix

| Case | Approved axes | V2 axes | Composite | Audit |
| --- | --- | --- | --- | --- |
| Delivery sequence | 5 / 1 / 1 | 4 / 2 / 1 | 3 → 3 | Feedback supplied the unstated time/detail tension while Depth remained below 3. |
| Identity boundary | 5 / 1 / 2 | 5 / 1 / 2 | 3 → 3 | Exact axes; the bare “not proof” guardrail did not become a concrete failure consequence. |
| Timeout retry | 5 / 2 / 2 | 5 / 1 / 1 | 3 → 3 | Feedback supplied both missing cost and adverse outcomes while both axes remained below 3. |
| Cursor pagination | 5 / 1 / 2 | 4 / 1 / 1 | 3 → 3 | Feedback introduced arbitrary-page cost and offset-drift harm without crediting either as recall. |
| Decision-driven estimation | 5 / 1 / 2 | 4 / 1 / 1 | 3 → 3 | Correct mechanism stayed passing; the unstated threshold consequences remained corrective feedback only. |

Every score/feedback pair satisfies the V2 contract. There are no unsupported
3–5 secondary axes and no feedback statement that contradicts its axis ceiling.

## A2 cost and resume proof

| Measure | Result |
| --- | ---: |
| Input tokens | 15,866 |
| Output tokens | 1,412 |
| Cache read / write tokens | 0 / 0 |
| Input cost at $2/M | $0.031732 |
| Output cost at $10/M | $0.014120 |
| **Calculated total** | **$0.045852** |
| Average per call | $0.009170 |
| Share of authorized ceiling | 79.9% |

A keyless exact resume reused all five fingerprints, scheduled zero new calls,
and reported a $0.0000 new-call ceiling.

## Combined A1 and A2 evidence

| Measure | V2 six-case result |
| --- | ---: |
| Composite exact / within one | **6/6 · 6/6** |
| Accuracy exact / within one | 2/6 · 6/6 |
| Depth exact / within one | 4/6 · 6/6 |
| Boundaries exact / within one | 3/6 · 6/6 |
| Accuracy false pass / failure | 0 / 0 |
| Feedback/evidence audit | 6/6 consistent |
| Input / output tokens | 19,059 / 1,683 |
| **Calculated cost** | **$0.054948** |

V1 used 15,327 input and 1,965 output tokens and cost $0.050304 on the same six
labels. V2's measured total was $0.004644, or 9.2%, higher. Its longer prompt
increased input cost, while shorter generated feedback offset part of that
increase. Six calls are too few to treat the output reduction or 9.2% delta as a
production forecast.

V1 produced five exact composites and retained the known two-point overgrade.
V2 produced six exact composites and removed it. This is meaningful candidate
evidence, but each case was sampled only once and all six tested answers were
supposed to withhold secondary-axis eligibility.

## Recommended next regression

The next risk is overcorrection: V2 may be good at withholding secondary credit
but too strict when a learner actually states a failure or trade-off. The six
approved `axis-isolation` cases test those directions independently.

Cost-minimized order:

| Stage | Cases | Calls | Counted input | Ceiling |
| --- | --- | ---: | ---: | ---: |
| B1 | `boundaries-only` | 3 | 9,586 | **$0.0346** |
| B2, only after B1 passes | `depth-only` | 3 | 9,620 | **$0.0346** |

B1 goes first because V2 specifically tightened Boundaries. All three B1 answers
have explicit trigger-to-harm relationships, approved Boundaries 4–5, approved
Depth 1, and composite 5. A valid B1 must keep Accuracy passing, award Boundaries
at least 3, keep Depth at most 2, return composite 5, and ground each high axis in
the learner's words.

B1 is only free-preflighted. It requires a separate authorization after this
result merges. B2, Terra, repeatability, prompt distillation, and production
promotion remain blocked.
