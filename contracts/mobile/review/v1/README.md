# Mobile review contract fixtures — v1

These language-neutral fixtures are the first shared seam for the FastAPI,
SwiftUI, and Android clients. FastAPI and Android validate them in M0; wiring
SwiftUI to the same files is a promotion gate. The wire fixtures retain the
backend's snake_case field names and are deliberately small enough to review by
eye.

M0 covers only the session-entry and draft-preservation boundary:

- `cards_due.raft.json` mirrors `GET /cards/due` before a session exists.
- `cards_due.raft.resumed.json` mirrors the same queue after a draft exists.
- `session_start.raft.new.json` mirrors a new `POST /cards/{id}/sessions` response.
- `session_start.raft.resumed.json` mirrors the same live session after a draft exists.
- `speech_trace.raft.json` is deterministic client-test input, not an API response.

Each due-card/session pair is a coherent API snapshot: the new pair has
`resumable: false`, an empty draft, and `resumed: false`; the resumed pair has
`resumable: true`, a non-empty draft, and `resumed: true`. The session responses
intentionally share one session ID. That is the server's real same-session
resume contract; the iOS `MockAPI` currently creates a new UUID for its resumed
fixture and must not be treated as wire authority.

Answer, follow-up, score, authentication, and push fixtures belong to later
milestones. M0 is a feasibility gate for fold/resize and exact draft survival.
