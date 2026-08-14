# Adaptive lesson MVP

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
   and preview or write its learning notes to the local second-brain vault.

The pasted source remains attached to the Devmax material so grading can stay grounded. Raw
source text and answer transcripts are never written to the second brain. Each vault export is
one concise, graded concept note containing a mental model, mechanism, gotchas, five recall
prompts, and quiz evidence.

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

## Export to the second brain

Preview every concept note without touching the vault:

```sh
cd api
uv run python scripts/export_second_brain.py /tmp/devmax-lesson-artifacts.json
```

After reviewing the preview, write all concept notes as one all-or-none local operation:

```sh
uv run python scripts/export_second_brain.py \
  /tmp/devmax-lesson-artifacts.json \
  --write \
  --vault /absolute/path/to/second-brain
```

The writer requires the vault's `CLAUDE.md`, `wiki/_index.md`, and `log.md`; requires the
vault to be on a clean `main` branch; refuses existing concept notes and unsafe paths; and
updates the notes, index, and log together. It never commits or pushes. Use `--concept` with a
concept title or slug to preview or export only one concept.

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
