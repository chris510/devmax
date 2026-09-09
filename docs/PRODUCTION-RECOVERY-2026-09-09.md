# Production recovery and release — September 9, 2026

This is the first implementation step following the project audit. The API is
deployed and recoverable; physical-device and provider-control checks remain open.

## Deployed result

At 17:27 UTC, Railway deployed commit `51d3c2197b8e88cecf3a3dcadd933c98fb55f551`
as deployment `1f192ff0-69e6-4672-acb6-8302ce203ca2`. The predeploy migration
advanced `0019` to `0025`; `/live`, `/ready`, and `/health` returned 200, with
`/ready` reporting `0025`. Unauthenticated card access still returned 401.
The single-replica review poller restarted normally and returned `already_pushed`.

The postdeployment comparison matched every original column in all 26 existing
application tables against the untouched predeployment restore. No curriculum
activation, pilot enrollment, learner session, score, or schedule was changed.
A new archive of the upgraded database was uploaded and verified at 17:29 UTC
(182,895 bytes). See [production verification](audits/2026-09-09/production-after-deploy.json)
and [postmigration backup](audits/2026-09-09/backup-after-migration.json).

Both hosted CI runs passed all six jobs, including iOS and backup validation:
[branch CI](https://github.com/chris510/devmax/actions/runs/34382412742) and
[PR CI](https://github.com/chris510/devmax/actions/runs/34382432488).

### Recovery API and internal TestFlight build

The second rollout deployed `da76d46232954b653b8b174cc763b7553f7c54db` as
`707fc772-62f9-4f88-b857-467c541ab998`. Schema remains `0025`, and all three health
probes return 200. Both unauthenticated requests and the disabled legacy shared
key return 401. Protected recovery responses were verified against the isolated
production restore; a production bearer-session walkthrough remains device work.
See [deployed probes](audits/2026-09-09/recovery-api-deployed.json) and
[container reads](audits/2026-09-09/recovery-api-container.json).

The initial upload `befa0f9c-1cba-4339-9b82-9881bf679118` failed before Docker
started because `--path-as-root api` removed the `/api` directory expected by
the service. The existing deployment stayed healthy. Uploading the repository
root preserved `/api/railway.json`, the Dockerfile, migration command, readiness
check, and one API replica; that rollout succeeded.

All six hosted CI jobs passed on the deployed source in both the
[branch run](https://github.com/chris510/devmax/actions/runs/34388739780) and
[PR run](https://github.com/chris510/devmax/actions/runs/34388934929), including
219 iOS tests. Final local results are
1,337 PostgreSQL 18 tests and 1,293 SQLite tests with 44 PostgreSQL-only skips.
The last configuration fix removes secret-bearing input dictionaries from
startup validation messages while retaining their field/error explanations.

Release build **13** was archived and exported for **internal TestFlight only**,
then uploaded successfully at 18:34:46 UTC. This build overrides the repository's
build 10 with `CURRENT_PROJECT_VERSION=13`, above the installed developer build
12. The exported IPA has production APNs, `get-task-allow=false`, mocks off, the
Railway URL, and empty API/claim bootstrap secrets. Xcode reports that the
uploaded package is processing; this does not prove TestFlight readiness,
installation, or delivery to a production APNs token. The archive's development
signature is expected before export; the distributed IPA's signature is the
one checked. [Build evidence](audits/2026-09-09/ios-build13.json).

### Generic importer rerun

The existing operator CLI completed one live import of the current 19.7k-character
curriculum in **872.6 seconds**, returning four phases, twelve weeks, and 73
items. Every source excerpt resolves against the guide. No database was written.

The guide declares 20 hours/week; the smoke requested 15. The gate correctly
withheld creation for over-capacity weeks and also flagged 16 inferred estimates,
eight possible omissions, five proposed retrieval items, and fourteen inferred
dependencies. This closes the missing post-fix provider/schema/offset run; it
does not approve the generated plan. The deterministic first-party manifest
remains the appropriate path for the built-in curriculum. Billed output tokens
and total cost were not reported by the existing CLI.
[Sanitized evidence](audits/2026-09-09/live-generic-import.json).

## Verified starting state

- Railway project `devmax`, production environment, API deployment
  `8cfd0195-268c-469d-b2ab-156417ce12a1`, commit `90a0def` from August 16.
- The production database was at schema `0019`; `/ready` returned 401.
- Consent enforcement was enabled with the combined Anthropic/OpenAI v2
  disclosure. Numeric scoring remained V1 and OpenAI routing was off.
- Production APNs was selected. Logs repeatedly rejected one token with
  `BadDeviceToken`; delivery to the currently installed app still needs proof.
- The connected iPhone has Unprompted 0.1.0 build 12, marked as a developer build.
  This alone does not establish its APNs entitlement or TestFlight distribution.
- GitHub main had advanced to `b9ef5ff`. Those navigation and rollout fixes were
  merged into this work without discarding the initial audit changes.

## Release fixes and tests

GitHub's container job reproduced a real startup failure: changing the base image
to Python 3.14 conflicts with `requires-python >=3.12,<3.13`. uv downloaded Python
3.12 under `/root`, making `uvicorn` inaccessible to the non-root runtime user.
The fix restores Python 3.12, pins the refreshed official image digest, disables
automatic interpreter downloads, and keeps Dependabot's Python updates within
the supported minor version. CI now retains startup logs when a container exits.

- 1,331 backend tests passed on a migrated PostgreSQL 18 database.
- 207 iOS tests passed after integrating current main (204 unit and 3 UI).
- The production container loaded the existing production configuration with
  scoring V1, OpenAI off, consent enforcement on, and APNs sandbox false.
- Trivy found no Python vulnerabilities, no secrets, and no high/critical OS
  vulnerabilities with available fixes. It still reports 54 OS findings without
  available fixes; this is not a claim of a vulnerability-free image.
- Ruff, Actionlint, whitespace checks, and three backup-retention tests passed.

The live V1 canary made two synthetic, reviewed-fixture calls at the configured
shipping model/effort for $0.0205. Both retention pass/fail classifications were
correct, but the numeric review gate failed: a noisy yet adequate explanation
received depth 3 instead of 1 and composite 4 instead of 3. This is unresolved
calibration evidence, not a fully passing live-scoring qualification. No learner
session or scheduling state was written. Keep this case in the later learning-
quality work; do not change scoring policy or claim score precision from two calls.
See [the canary record](audits/2026-09-09/v1-live-canary.json).

The first Postgres-18 suite invocation targeted an empty database before its
migrations had been applied. Its fixture setup errors were corrected by following
the documented migration prerequisite; the subsequent full run passed.

## Backups that were actually restored

Railway's Backups page restricts native backups and PITR to Pro. The account uses
Hobby, had no scheduled backups or WAL archive, and held only an August 23
platform security-patch snapshot. CLI operations misleadingly returned
`OAUTH_INSUFFICIENT_GRANT`, including after successful reauthentication.
The account subscription was not upgraded.

A fresh private logical dump was restored to isolated PostgreSQL 18. A second
copy migrated from `0019` to `0025`. All original columns in all 26 pre-existing
application tables matched, including nine cards, fourteen sessions, two plans,
260 plan items, drafts, scores, mastery, and SM-2 fields. There were no owner
orphans, invalid constraints, or duplicate live sessions. The updated container
served `/live`, `/ready`, `/health`, authenticated reads, and account export on
the migrated copy; unauthenticated card access returned 401.

The new `database-backup` service reads production through a dedicated SELECT-only
role and writes to the private `production-recovery` bucket. It carries no model,
APNs, application-auth, or review-poller credentials. Every upload is downloaded
and SHA-256 checked before retention removes any old archive. It retains 31 days
and at least three copies. Both the local end-to-end run and the first Railway
run logged `database_backup_verified`.

The schedule is explicitly configured and queried as **10:00 UTC daily**, with
the first future run at **2026-09-10 10:00 UTC**. New services no longer read
`railway.json`; the effective cron and retry configuration were applied through
the supported service API and recorded in `ops/backups/configure.graphql`.

A backup downloaded from the private bucket was also restored into a new empty
database. Download plus restore took 1.14 seconds for the current small dataset.
This measures a warm local drill, not total incident response time. The intended
RPO is ≤24 hours subject to successful scheduled jobs. There is no older PITR
window, and project/account deletion could remove both database and bucket.

Evidence: [migration and data comparison](audits/2026-09-09/production-recovery.json),
[bucket restore](audits/2026-09-09/bucket-restore.json), and
[backup operations](../ops/backups/README.md). Raw database archives and credentials
remain outside the repository in a private local directory.

## Remaining checks

- Finish Apple processing, install internal TestFlight build 13, and verify a
  physical-device push, review, and interrupted-network recovery. iPhone Mirroring
  most recently reported that the phone was in use; earlier it required the
  owner's Mac authentication. No learner answer was simulated.
- Observe the first scheduled backup on September 10; successful manual/deployment
  runs and the configured cron establish setup, not future execution.
- Provider billing ceilings and billed token/cost accounting for the live import.
- Railway IaC migration before its December 1, 2026 legacy-config cutoff;
  preserve migrations, `/ready`, one API replica, and the daily backup schedule.

Scoring V2 and pilot enrollment remain governed by their existing qualification,
disclosure, client-build, and participant gates. This release does not activate
them or approve additional curriculum cards.
