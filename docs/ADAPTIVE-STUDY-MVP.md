# Adaptive lesson MVP

> **Runtime record:** This document describes the nonpilot shipping first pass. The
> product direction approved on 2026-08-15 is specified in
> `ADAPTIVE-STUDY-PILOT-SPEC.md`: immediate lesson formation becomes unscored,
> answer disclosure creates a recall hold, and only delayed closed-book Recall
> may move SM-2 or qualify distillation. Do not widen capture to a browser or Mac
> surface until that pilot reaches its declared gate.

Devmax can turn source text you just read into a short, source-grounded recall session.
The feature extends the existing material, card, session, mastery, and SM-2 domains; it does
not introduce a second lesson scheduler or a second grading path.

## Product flow

1. From **Today**, choose **Add lesson**.
2. Add a title, choose the source type, optionally add the source URL, and paste the text you
   read. The URL is attribution only; Devmax does not fetch it.
3. Devmax proposes one to seven source-grounded concepts. Review the proposals before any
   cards are created.
4. Choose **Study**. Devmax asks one stable open-ended question per concept without showing
   the source. The existing session engine grades the answer, may ask up to two adaptive
   follow-ups, surfaces missing points, and updates the existing concept card.
5. **Lesson results** shows the concept scores, feedback, and next-review timing. The existing
   SM-2 scheduler remains the only scheduling authority.
6. After every confirmed concept has one completed, non-practice review, distill the lesson
   and preview or save its learning writeback bundle. Import that bundle through the local
   second-brain workflow; Devmax does not write into the vault directly.

The pasted source remains attached to the Devmax material so grading can stay grounded. Raw
source text and answer transcripts are never included in the bundle. Each export contains one
concise, graded concept record with a mental model, mechanism, gotchas, exactly five recall
prompts, and quiz evidence. It intentionally excludes scheduling, intervals, and live mastery;
Devmax remains the authority for those fields.

## Run locally

Start the API using the normal repository setup:

```sh
cd api
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8083
```

Then generate and run the iOS project:

```sh
cd ios
xcodegen generate
open Devmax.xcodeproj
```

Debug builds use `MockAPI` by default. Set `WC_MOCK=0` to exercise the live local API. The
account must have accepted Devmax's AI-processing disclosure before extraction or grading can
call the configured provider.

## Exercise the API directly

Create a lesson through the existing material endpoint. `source_text` must contain at least
200 readable characters; `source_url` is optional and is never fetched.

```sh
curl -sS http://localhost:8083/materials/imports \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: test-api-key' \
  -d '{
    "title": "Consistent hashing",
    "kind": "article",
    "source_url": "https://example.com/consistent-hashing",
    "source_text": "Modulo hashing sends a key to hash(key) modulo the node count. When the node count changes, most keys map somewhere else. Consistent hashing instead places keys and nodes on a ring so a membership change moves only nearby ranges; virtual nodes spread those ranges more evenly across physical nodes.",
    "import_path": "lesson",
    "intent": "already_studied"
  }'
```

The response is durable and may initially be `pending` or `processing`. Poll
`GET /materials/imports/{source_id}` until it is `ready` or `needs_attention`, review its
concept proposals, and confirm selected proposal IDs with:

```json
{"selected_topic_ids":["PROPOSAL_UUID"]}
```

at `POST /materials/imports/{source_id}/confirm`. Study the returned card IDs through the
ordinary session endpoints. `GET /materials/imports/{source_id}/progress` derives lesson
mastery from those cards and their completed sessions.

Once all confirmed concepts have a completed non-practice review:

```sh
curl -sS -X POST \
  -H 'X-API-Key: test-api-key' \
  http://localhost:8083/materials/imports/SOURCE_UUID/distill \
  -o /tmp/devmax-lesson-artifacts.json
```

The distillation endpoint is deterministic and idempotent. It uses confirmed concept
grounding plus unaided grade evidence, not raw conversations.

### Content provenance

Lesson imports classify the pasted content independently from attribution URL and source
genre. New lessons must choose exactly one of `exact_source_excerpt`, `learner_notes`,
`coached_correction`, or `ai_derived_summary` before confirmation can create cards. Rows
created before this field existed use `legacy_unspecified`; that value is never inferred and
cannot cross the lesson confirmation boundary until the learner chooses a classification.

The material and artifact APIs expose `content_provenance`. The strict, content-addressed
writeback bundle remains byte/key-compatible schema v1 and therefore does not include the new
field. Moving it into the portable bundle requires one coordinated schema-v2 release of both
the Devmax producer and second-brain importer; neither side should ship that contract alone.

## Prepare a second-brain writeback

Preview every concept note without touching the vault:

```sh
cd api
uv run python scripts/export_second_brain.py /tmp/devmax-lesson-artifacts.json
```

After reviewing the preview, save the validated JSON bundle:

```sh
uv run python scripts/export_second_brain.py \
  /tmp/devmax-lesson-artifacts.json \
  --output /tmp/devmax-learning-writeback.json
```

The saved envelope uses `schema: "second-brain.learning-writeback"` and `schema_version: 1`.
Its `export_id` is derived from canonical JSON, so consumers can detect an identical export.
Concept, card, probe, and session IDs are stable Devmax identifiers, while the source lineage
and version let an importer distinguish a newer distillation from a duplicate. The bundle
contains no raw source, learner answer, transcript, scheduling state, interval, or live mastery.

Direct `--write` and `--vault` use is deprecated and rejected. The second-brain importer owns
vault path validation, conflict handling, indexing, and Git safety. `--concept` remains
available only for Markdown preview; a saved bundle always preserves the complete contract.
On iOS, **Prepare export** creates the same privacy-bounded `.json` bundle for the existing
share flow.

## Verification

```sh
cd api
uv run pytest -q
uv run ruff check .

cd ../ios
xcodegen generate
xcodebuild -project Devmax.xcodeproj -scheme Devmax \
  -destination 'platform=iOS Simulator,name=iPhone 16e' build
```

Postgres-backed migration tests additionally require an explicit, disposable local
`TEST_DATABASE_URL`; they refuse non-loopback databases.

## Intentional first-pass boundaries

- URL fetching and Chrome-extension capture are not included. The URL is provenance only.
- Raw web pages are not archived to the second brain.
- Conversation transcripts are not treated as canonical knowledge.
- The hosted API never receives or hardcodes a local vault path.
- Existing vault notes are never overwritten automatically; a conflict requires a deliberate
  manual merge.

The next best iteration is a thin browser capture layer that sends title, URL, and selected
text into this validated learning loop after the loop itself has been used and measured.
