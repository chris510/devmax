# Claude explicit-evidence V2 B2

**Run date:** 2026-08-09 (America/Los_Angeles)

**Decision:** Failed; all broader V2 stages stopped

## What ran

B2 evaluated `explicit-evidence-v2` on the three approved `depth-only`
axis-isolation cases. B1 had shown that V2 could award explicit failure-mode
evidence. B2 tested the symmetric requirement: whether an explicit
choice-to-cost relationship reliably activates Depth 3–5 while Boundaries
remains low.

| Control | Value |
| --- | --- |
| Model | `claude-sonnet-5` |
| Effort | `low` |
| Cases | Three approved `depth-only` axis-isolation cases |
| Concurrency | 1 |
| Prompt | Production rubric plus evaluation-only `explicit-evidence-v2` |
| Production change | None |
| Preflight ceiling | **$0.0346** |
| Paid calls | **3** |
| Calculated cost | **$0.028280** |

The fresh free preflight counted 9,620 input tokens and reserved 1,536 output
tokens. The paid stage made exactly three Message calls.

## Gate result

| Criterion | Result | Gate |
| --- | ---: | --- |
| Structured responses parsed | 3/3 | Pass |
| False Accuracy passes / failures | 0 / 0 | Pass |
| Accuracy exact / within one | 2/3 · 3/3 | Pass safety boundary |
| Composite exact / within one | 2/3 · 3/3 | Pass tolerance only |
| Mean composite deviation | 0.33 | Informational |
| Depth exact / within one | **1/3 · 2/3** | **Fail activation** |
| Boundaries exact / within one | 3/3 · 3/3 | Pass withholding |
| Feedback/evidence audit | **2/3 consistent** | **Fail** |

The hard B2 gate required every correct explicit trade-off to earn Depth at
least 3. One case received Depth 2 against approved 5 even though Claude's own
feedback recognized the learner-stated trade-off. The derived composite moved
only one point, but the underlying axis missed by three and violated the V2
eligibility decision.

## Case matrix

| Case | Approved axes | V2 axes | Composite | Audit |
| --- | --- | --- | --- | --- |
| Delivery sequence | 5 / 4 / 1 | 5 / 4 / 1 | 4 → 4 | Exact. “Deferring detail protects enough interview time, though later deep dives must be chosen carefully” correctly activated Depth. |
| Timeout retry | 5 / 5 / 1 | 5 / 4 / 1 | 4 → 4 | Within one. Tracking keys adds state/coordination and backoff reduces pressure while delaying recovery; both relationships earned Depth 4. |
| Decision-driven estimation | 5 / 5 / 1 | **4 / 2 / 1** | **4 → 3** | Blocking failure. The learner explicitly traded skipping unrelated arithmetic for saved interview time while retaining attention to real capacity limits. Feedback acknowledged that trade-off but Depth remained ineligible. |

## Why the blocking case fails V2

The learner said:

> Skipping unrelated arithmetic saves interview time, but you still have to
> notice when a design approaches a real capacity limit.

That is an explicit, correct choice-to-benefit/tension relationship. It meets
V2's stated eligibility definition even without a concrete cardinality number.
The missing estimate may reduce mechanism completeness or lower a high Depth
score within the eligible 3–5 range; it cannot make the stated trade-off absent.

Claude's feedback said the learner “state[d] the general trade-off that skipping
arithmetic saves time” and then assigned Depth 2 because no number was estimated.
The feedback and axis therefore disagree about eligibility. The approved label
and review note remain frozen: the timing-versus-missed-bound trade-off is clear.

V2 implements a hard ceiling when evidence is absent, but it does not state the
opposite direction strongly enough: once a correct explicit relationship exists,
the axis must enter 3–5. “Eligible” was treated as permission rather than a floor.

## Cost and resume proof

| Measure | Result |
| --- | ---: |
| Input tokens | 9,620 |
| Output tokens | 904 |
| Cache read / write tokens | 0 / 0 |
| Input cost at $2/M | $0.019240 |
| Output cost at $10/M | $0.009040 |
| **Calculated total** | **$0.028280** |
| Average per call | $0.009427 |
| Share of authorized ceiling | 81.7% |

A keyless exact resume reused all three fingerprints, scheduled zero new calls,
and reported a $0.0000 new-call ceiling.

## Cumulative V2 evidence

Across A1, A2, B1, and B2:

| Measure | Twelve-call result |
| --- | ---: |
| Composite exact / within one | 11/12 · **12/12** |
| Accuracy exact / within one | 6/12 · 12/12 |
| Depth exact / within one | 8/12 · **11/12** |
| Boundaries exact / within one | 7/12 · 12/12 |
| Accuracy false pass / failure | 0 / 0 |
| Feedback/evidence audit | 11/12 consistent |
| Input / output tokens | 38,265 / 3,420 |
| **Calculated cost** | **$0.110730** |

V2 fixed the original false-positive Boundaries problem, withheld unsupported
secondary axes across six noisy answers, and activated three explicit failure
relationships. B2 shows it can still produce a false-negative trade-off
eligibility decision. Scheduler safety remains intact because Accuracy never
crossed the pass/fail boundary, but the display signal is not reliable enough
for production.

## Decision and next candidate

Do not run more V2 families, repeats, higher effort, Terra, or a broader release
pack. More sampling cannot repair a prompt with a known internal eligibility
contradiction.

The next useful work is offline `explicit-evidence-v3` design with a bidirectional
eligibility rule:

1. If no correct explicit learner relationship exists, the relevant secondary
   axis must be 0–2.
2. If at least one correct explicit relationship exists, that axis must be 3–5.
3. Completeness, specificity, and coverage choose the value within the eligible
   band; they cannot move a valid relationship back across the threshold.
4. Missing mechanism details affect Accuracy or the 3–5 completeness value, not
   whether an independently stated trade-off exists.

V3 must remain evaluation-only, add deterministic prompt/fingerprint tests, and
start with the single failed decision-driven-estimation case. It needs a fresh
free preflight and separate authorization before any paid call.
