# Adaptive study pilot operator runbook

This is the access-controlled path for preparing the six experimental lesson
chunks. It intentionally has no participant-facing HTTP or admin surface. Run
it from `api/` against the intended database. Never commit a participant
manifest, source text, reviewer correction, or report output to the repository.

## Prepare and process one participant

1. Record explicit research consent. The timestamp must be the actual consent
   instant, not the time the command happens to run. The CLI accepts only the
   frozen supported pilot-consent version and rejects a timestamp more than the
   five-minute clock-skew allowance in the future.

   ```sh
   uv run python scripts/lesson_pilot_operator.py enroll \
     --user-id <user-uuid> \
     --cohort pilot-2026-08 \
     --consent-version adaptive-study-pilot-research-v1 \
     --consented-at <utc-timestamp> \
     --randomization-seed <access-controlled-random-seed> \
     --confirm
   ```

2. Print the live, privacy-safe contract metadata. Copy the returned
   `version_snapshot` object unchanged into every one of the six local manifest
   entries. Provisioning and processing both reject a snapshot that does not
   match the running extraction route, prompt versions, grounding gate, consent
   version, or minimum client build.

   ```sh
   uv run python scripts/lesson_pilot_operator.py contract \
     --consent-version adaptive-study-pilot-research-v1
   ```

3. Prepare an access-controlled JSON array with exactly six objects. Every
   object needs these fields:

   ```json
   {
     "source_id": "<pre-generated-uuid>",
     "source_lineage_id": "<different-pre-generated-uuid>",
     "title": "<private title>",
     "source_text": "<consented bounded source text>",
     "source_url": "<optional attribution URL>",
     "content_provenance": "learner_notes",
     "kind": "notes",
     "original_filename": "",
     "mime_type": "text/plain",
     "intent": "learn",
     "pair_index": 1,
     "intended_target": "position:1",
     "version_snapshot": {}
   }
   ```

   Pairs must be 1–3, with exactly two sources in each pair. Do not include a
   `condition` or `sequence_index`: both are deterministically derived from the
   enrollment's access-controlled randomization seed and the six frozen source
   lineages using the versioned assignment algorithm in `version_snapshot`.
   Manifest order therefore cannot choose a convenient condition or sequence.
   The target must be the non-content-bearing key `position:1`, `position:2`, or
   `position:3`. Topic text is never persisted in the assignment row, which
   remains after the content-bearing source is deleted.

4. Atomically create all six sources as drafts and freeze all six assignments.
   No provider work starts in this step. Exact retries return the same rows;
   changed manifests fail closed.

   ```sh
   uv run python scripts/lesson_pilot_operator.py provision \
     --enrollment-id <enrollment-uuid> \
     --manifest <private-six-source-manifest.json> \
     --confirm
   ```

5. Explicitly release the frozen set to processing. Assigned pilot imports use
   the consent-checked pilot operation lane and still count every physical call
   against the account-wide model-call ceiling; they do not consume the public
   three-guide daily lane. The command waits for all six workers and prints only
   IDs, status enums, and counts. Review every returned status before continuing.

   ```sh
   uv run python scripts/lesson_pilot_operator.py process \
     --enrollment-id <enrollment-uuid> \
     --confirm
   ```

## Review, bind, and freeze transfer

Extraction creates an immutable audit record for every proposal before any
reviewer correction. Reviewers remain blind to condition. Record one decision
for every proposal; a `corrected` decision requires a complete grounded
correction pack in an access-controlled JSON file.

```sh
uv run python scripts/lesson_pilot_operator.py review \
  --proposal-id <proposal-uuid> \
  --reviewer-id <pseudonymous-reviewer-id> \
  --decision approved \
  --confirm
```

After every proposal from the source has a recorded review, bind the one
predeclared target. This transaction checks the frozen extraction route and
grounding evidence, binds the target, and marks every other proposal
`pilot_non_target`.

```sh
uv run python scripts/lesson_pilot_operator.py bind \
  --assignment-id <assignment-uuid> \
  --proposal-id <matching-proposal-uuid> \
  --confirm
```

Before the participant completes the first delayed Recall, freeze one reviewed
application or failure/trade-off prompt from the proposal's grounded candidate
list. The candidate index is one-based. The command never prints the prompt.

```sh
uv run python scripts/lesson_pilot_operator.py approve-transfer \
  --assignment-id <assignment-uuid> \
  --candidate-index 4 \
  --reviewer-id <pseudonymous-reviewer-id> \
  --approved-at <utc-timestamp> \
  --confirm
```

## Restricted report and withdrawal

The reporting script outputs pseudonymous identifiers, enums, counts, and
timestamps only. It excludes source and response content, transcripts, rubrics,
feedback, provider output, runtime scores, scheduling state, and notes. An
explicit output path is mandatory; participant-level reports are never printed
to stdout. The file is created exclusively with mode `0600`, and an existing
path is never opened or overwritten.

```sh
uv run python scripts/lesson_pilot_report.py \
  --enrollment-id <enrollment-uuid> \
  --output <access-controlled-new-file.json>
```

Withdrawal immediately removes the enrollment from this report and blocks new
pilot checks. It does not rewrite ordinary cards or review history. The separate
research withdrawal/deletion ledger still owns reviewer-file and backup SLAs.

```sh
uv run python scripts/lesson_pilot_operator.py withdraw \
  --enrollment-id <enrollment-uuid> \
  --withdrawn-at <utc-timestamp> \
  --confirm
```
