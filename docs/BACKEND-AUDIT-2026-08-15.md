# Backend audit — 2026-08-15

This is the implementation and operations record for the August backend audit.
It describes the repository state, not the currently deployed Railway state.
Anything under **Production evidence still required** remains open until it is
verified against production and recorded in `docs/DEPLOY-CHECKLIST.md`.

## Remediation delivered in the repository

| Area | Result |
|---|---|
| Authentication | Apple authorization freshness and replay receipts are durable; authorization-event ordering is independent; nonce/session cleanup is bounded; account export includes all owned records while sanitizing credentials and device identifiers. |
| Ownership and concurrency | Live-session, card-lineage, capture activation, topic creation, Study Plan revision, debrief, and proposal acceptance races are serialized or enforced by database constraints. Practice and scheduled sessions cannot silently resume across modes. |
| Request abuse | Request bodies are bounded before routing; public auth routes are rate-limited; writes have per-credential/IP and process-wide pre-routing admission; authenticated provider routes additionally acquire per-account/global paid-work admission after user resolution and before provider transmission. |
| Storage abuse | Manual and imported material sources plus Study Plan guides/plans have account-level count and character quotas. Preview edits are restricted to their immutable import graph, and oversized request fields, acceptance batches, and device collections fail before unbounded growth. |
| Provider safety | Paid-call consent/budget authorization happens at the transmission boundary. Prompt data is delimited as untrusted material, and provider/model exceptions are sanitized before they reach API responses or logs. |
| Push delivery | APNs connections are reused, per-device and per-account sends are bounded concurrently, duplicate notifications collapse, permanent token failures are fingerprint-logged and removed, and one account's failure cannot abort or starve the batch. |
| Background imports | Import concurrency is bounded, leases heartbeat without pinning a database connection, stale work is recovered continuously, and an unexpected worker exception cannot leave work permanently invisible. |
| Scheduling bookkeeping | `last_pushed_at` remains immutable delivery evidence; `missed_counted_at` records a miss, while migration 0025's `push_resolved_at` records engagement and prevents repeated historical scans. |
| Deployment safety | `/live` is dependency-free liveness; `/ready` requires database connectivity and exactly Alembic head `0025`; `/health` reports consent-policy/enforcement state. The committed Railway configuration gates on `/ready`. |
| Supply chain and CI | Runtime dependency floors include published security fixes; CI covers SQLite, Postgres migrations/full tests, dependency audit, a fixed-vulnerability gate, complete high/critical inventory retention, and SBOM publication; the pinned Debian Trixie container runs as a non-root user and external actions are commit-pinned. |

The load-bearing product invariants in `AGENTS.md`, `spec.md`, and the extension
specifications still govern these controls. Security hardening does not authorize
score rewrites, schedule changes, history deletion, cross-user access, or new
product behavior.

## Verification evidence

- The pre-rebase hardening image was 98,021,042 bytes, ran as `app:app`, and
  used pinned
  Python 3.12.14 Debian Trixie and uv base-image digests.
- A local scan of that pre-rebase image, using a freshly downloaded advisory
  database, found no
  remediable high/critical operating-system or Python findings. It also found
  23 currently unfixed high/critical Debian findings (19 high, 4 critical; 20
  affected and 3 fix-deferred), no Python findings, and no embedded secret
  findings. CI retains that complete
  inventory as an artifact instead of hiding it while failing the release on
  any high/critical finding for which a fix exists.
- The integrated SQLite suite passed 1,258 tests with 42 PostgreSQL-only cases
  skipped; Ruff, actionlint, and the local Python dependency audit all passed.
- The definitive integrated PostgreSQL run applied an empty database through
  migration `0025`, then passed all 1,300 tests with no failures or skips. An explicit
  `0025` → `0023` → `0025` migration round trip also passed on a disposable
  database.
- On 2026-08-16, a read-only probe of the public Railway URL showed the older
  `/health` response shape, while `/live` and `/ready` returned 401. The local
  Railway CLI was not linked to a project. This is positive evidence that the
  repository hardening and readiness routes have **not** yet been deployed, not
  evidence that production passed the new release gate.

## Production evidence still required

| Control | Current evidence | Completion evidence |
|---|---|---|
| Hardening deployment | Public probe on 2026-08-16 confirms the pre-hardening response shape: `/live` and `/ready` return 401 and `/health` lacks readiness/schema fields | `/ready` returns 200 with `schema_revision: 0025`; authenticated smoke test passes; deploy logs have no migration/readiness errors |
| Backup and PITR | Not established by repository or prior deploy notes | Railway retention and latest recovery point recorded; isolated monthly restore drill meets provisional RPO ≤24h and RTO ≤4h; restored data/head/ownership checks pass |
| Provider financial ceiling | Application budgets and concurrency limits exist; provider-side settings are not recorded | Provider-enforced spend/rate limits recorded for every enabled provider; 75% and 90% notifications reach an owner; alert-only controls are labeled as such |
| Operational alerts | Required signals are documented; a connected alert destination is not evidenced here | Test alerts arrive for `/ready`, review batch failure, stale import lease, pool pressure, permanent APNs token removal, and model-budget pressure |
| APNs production environment | A real sandbox push reached a physical device | A TestFlight build receives a push with `APNS_USE_SANDBOX=false` and production `aps-environment`, then opens the intended card |
| Generic guide importer | Post-fix behavior is unit-tested; the live rerun stopped for lack of Anthropic credit | One reviewed guide completes live with expected structure, latency/tokens/retries recorded, without learner/provider text copied into logs |

## Release gate

1. Run the full SQLite and Postgres suites, lint, dependency audit, image scan,
   and migration upgrade/downgrade/upgrade verification.
2. Verify the production backup/PITR setting and provider hard spending ceiling
   before exposing the new deployment.
3. Deploy migrations through `0025`; keep traffic gated on `/ready`.
4. Verify `/live`, `/ready`, `/health`, authentication, one read, one draft write,
   and a manual review poll. Do not use a paid call or push for the basic smoke
   test.
5. Connect and test the alert rules in `docs/RUNBOOK.md`, then run the isolated
   restore drill. APNs production and the generic importer remain separate,
   explicitly observed validation windows.
