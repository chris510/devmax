# Production recovery and release — September 9, 2026

This is the first implementation step following the project audit. The API
deployment is pending completion of the final release checks below.

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

## Gates still being completed

- First hosted CI run including the new iOS and backup jobs.
- Updated API deployment, predeploy migration, and public `/ready` verification.
- Physical-device push/review and interrupted-network recovery.
- Provider billing ceilings and the post-fix live generic importer run.
- Railway IaC migration before its December 1, 2026 legacy-config cutoff;
  preserve migrations, `/ready`, one API replica, and the daily backup schedule.

Scoring V2 and pilot enrollment remain governed by their existing qualification,
disclosure, client-build, and participant gates. This release does not activate
them or approve additional curriculum cards.
