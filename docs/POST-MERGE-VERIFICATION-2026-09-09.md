# Post-merge scoring investigation and backup verification

PR [#120](https://github.com/chris510/devmax/pull/120) merged on September 9 at
20:28:59 UTC as `abaf2342d92f116b2c5c081a2b32d7bea6826398`.
Production `/ready` still returns 200, schema `0025`, and consent enforcement
enabled. No scoring setting or learner data changed during this investigation.

## Scoring finding

The September canary is a recurrence of the documented V1 secondary-axis
calibration problem. It is not evidence for changing the reviewed answer labels
or activating another provider.

Replaying both original request fingerprints with provider credentials absent
made **zero new paid calls**. The reviewed gate correctly exited 1:

| Synthetic answer | Reviewed Accuracy / Depth / Boundaries | Recorded result | Composite |
|---|---|---|---|
| Delivery sequence, noisy transcript | 5 / 1 / 1 | 4 / 3 / 2 | 3 expected, 4 recorded |
| Delivery sequence, fluent architecture jargon | 1 / 1 / 0 | 1 / 1 / 0 | 1 expected and recorded |

The noisy answer gives the correct order but no independent trade-off or failure
analysis. The model's own feedback identifies the missing explanation for that
order. Depth 3 versus reviewed 1 fails the within-one gate. Accuracy 4 versus 5
does not cross the scheduler's retention boundary. Both canary cases preserve
the expected scheduling bucket, but two cases cannot establish calibration.

The runtime rubric allows reasoning, **structure**, causality, or application
to count toward Depth. That overlaps with the essential sequence itself, while
the reviewed fixture expects separate secondary evidence. This is a plausible
contributor, not proof of the model's internal reasoning. The same transcript
cannot justify treating the V1 composite as a precise mastery measurement.

The affected consumer behavior is real: `derive_composite` promotes correct
Accuracy with Depth above 2 and Boundaries at most 2 from 3 to 4. V1's initial
probe band is 1–3, so this change would also remove probe eligibility for an
otherwise identical first-turn response. The canary is a scorer evaluation,
not a recorded learner session; no actual learner probe or schedule changed.

Eight evaluation-only prompt variants already explored narrower evidence
requirements. The [V8 broader follow-up stage](CLAUDE-EXPLICIT-EVIDENCE-V8-FOLLOWUP-RESULTS.md)
failed after a focused pass. The [Sol secondary-axis canary](OPENAI-SOL-SECONDARY-AXIS-CANARY-RESULTS.md)
also over-credited secondary evidence despite correct Accuracy. Another small
prompt tweak or repeated sampling until a pass would not resolve that evidence.

The existing [V2 decision](SCORING-CONTRACT-V2-SPEC.md) remains the recommended
path: one numeric Recall score with qualitative deeper coaching. This has a
trade-off: numeric secondary trends disappear, but their prior calibration did
not justify displaying them as reliable measurements. V2 itself still needs
live qualification after its earlier parser and output-limit failures; changing
the label alone cannot prove provider reliability.

## Offline verification and remaining activation gate

**248 tests passed** on the merged code:

```sh
cd api
uv run pytest -q tests/test_llm.py tests/test_scoring_v2.py \
  tests/test_scoring_contract.py tests/test_scoring_provider.py \
  tests/test_v2_recall_sweep.py tests/test_v2_recall_eval.py
```

These cover the 8,000-token scoring limit, surplus-probe handling, strict V2
responses, Recall-only completion, replay, capped probes, failure atomicity,
Practice scheduling isolation, qualitative write boundaries, and evaluation
guards. They use mocked provider responses and are not a new live calibration
or the entire iOS/consumer acceptance matrix. The earlier build-15 iOS and
full backend release checks remain in the September release record.

Production remains V1. The next scoring milestone is the existing Claude-only
V2 stabilization gate: frozen approved payloads and labels, current request
fingerprints and spend ceiling, the updated acceptance evidence, compatible
client/telemetry, and a separately approved activation window. The checked-in
38-case V2 draft pack is not owner-approved qualification evidence. Do not
silently promote its labels, reuse obsolete output-limit fingerprints, enable
OpenAI, or reopen stopped paid experiments.

The original canary is retained in
[`v1-live-canary.json`](audits/2026-09-09/v1-live-canary.json); the keyless replay
summary is in [`post-merge-scoring.json`](audits/2026-09-09/post-merge-scoring.json).
Replaying cached records checks the gate and fingerprint match, not model
repeatability.

## Backup finding and correction

The live backup service had the expected daily schedule but policy `NEVER`
instead of the intended `ON_FAILURE`. Reapplying the original combined mutation
returned success yet still read back `NEVER`. A second mutation containing only
the retry settings read back `ON_FAILURE` with two retries.

`ops/backups/configure.graphql` now uses two ordered root mutations: configure
the schedule, then the retry policy. Applying the corrected file to the dedicated
backup service succeeded; readback confirms all four fields:

| Field | Verified value |
|---|---|
| Schedule | 10:00 UTC daily |
| Next scheduled run | September 10, 2026, 10:00 UTC / 3:00 a.m. PDT |
| Retry policy | ON_FAILURE |
| Maximum retries | 2 |

The setting-order behavior was reproduced directly against Railway; it is not
inferred from a successful deployment. Railway documents the distinct
[restart-policy meanings](https://docs.railway.com/deployments/restart-policy)
and the [cron requirement to exit after each job](https://docs.railway.com/cron-jobs).
No failed-job retry was induced, so actual retry execution remains untested.

A fresh download of the latest existing archive, made September 9 at 17:29 UTC,
matched its stored SHA-256 and restored successfully into a disposable Postgres
18 container with networking disabled. The restore contains schema `0025`,
9 cards, 14 sessions (12 completed/scored), 2 study plans, and 260 plan items.
All restored constraints are validated. The drill took 14.52 seconds including
download and container startup. No production restore or learner-data write
occurred; the temporary container and downloaded copy were removed.

This is recovery from an existing snapshot, not a new snapshot or proof of
zero data loss since 17:29 UTC. It also does not repeat the separately recorded
application and migration drill. Three backup objects existed at verification.
The full sanitized result is
[`post-merge-backup-restore.json`](audits/2026-09-09/post-merge-backup-restore.json).

The **first automatic run is still pending**. A task follow-up named
`Verify Devmax scheduled backup` is scheduled for September 10 at 3:15 a.m. PDT.
It must correlate an automatic execution, completion event, object key, and
full-download checksum; a green build or a manual archive cannot satisfy it.
It reports completion, failure, or required user action once and then pauses.
No phone interaction is needed for this verification.
