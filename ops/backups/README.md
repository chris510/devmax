# Database backups

Railway's September 9, 2026 Backups screen restricts native volume backups and
PITR to Pro. This job supplies daily logical recovery on the existing Hobby plan.
It does not provide point-in-time recovery or recovery from deletion of the whole
Railway project/account. Keep a separately controlled copy before destructive
maintenance or a provider migration.

The `database-backup` service runs at **10:00 UTC daily**. It reads Postgres over
Railway's private network using a dedicated `devmax_backup` role with SELECT
permissions, uploads to the private `production-recovery` bucket, downloads the
whole object, and compares SHA-256 before reporting success. Only then may it
remove its own daily archives older than 31 days, preserving at least three.
Provider, APNs, application-auth, and cron credentials are absent from this job.

The job exits after each run. `database_backup_verified` is success;
`database_backup_failed` or an absent successful run for more than 24 hours needs
investigation. A green deploy alone does not prove that a later scheduled run ran.
RPO ≤24 hours is the intended schedule, subject to successful jobs; there is no
historical recovery point before the first successful backup.

Required variables (configure through Railway; never commit their values):

| Variable | Purpose |
|---|---|
| `BACKUP_DATABASE_URL` | `postgresql://` URL for the read-only backup role |
| `BACKUP_S3_ENDPOINT`, `BACKUP_S3_BUCKET` | Private bucket endpoint and name |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` | Bucket-scoped credentials |
| `AWS_REQUEST_CHECKSUM_CALCULATION`, `AWS_RESPONSE_CHECKSUM_VALIDATION` | `when_required` for S3 compatibility; the job independently verifies every downloaded byte |

The database role needs CONNECT, USAGE on `public`, SELECT on its tables and
sequences, and matching default privileges for the migration owner (`postgres`).
It has no INSERT/UPDATE/DELETE or schema-creation grant. If the migration owner or
schema changes, update those default privileges and verify a backup afterward.
`pg_dump --no-owner --no-acl` deliberately excludes the deployment roles and
credentials; recreate them separately when restoring onto a new provider.

Deploy from the repository root after configuring those variables:

```sh
python3 -m unittest discover -s ops/backups
railway up ops/backups --path-as-root --service database-backup --ci
railway logs --service database-backup --lines 30
```

New Railway services no longer read `railway.json`. Configure the daily schedule
through the service settings or `configure.graphql`, passing the backup service's
ID and production environment ID with `railway api --file ... --var ...`. Verify
`serviceInstance.cronSchedule` and `nextCronRunAt` after applying; the first
successful upload is not evidence that a schedule exists. The existing API is a
legacy service and still reads `api/railway.json` until Railway's announced
December 1, 2026 cutoff. Its eventual IaC migration must preserve the migration
command, `/ready` healthcheck, and one-replica constraint.

To run a recovery drill, use bucket credentials locally to download one complete
`.dump` object, verify its `sha256` object metadata, and restore it into a **new
isolated Postgres 18**. Never point the restore command at the live database.
Run `pg_restore --no-owner --no-acl --exit-on-error` against that empty database.
Before migrating, record its Alembic revision and counts. Make a second copy for
`alembic upgrade head`, run the app with model/APNs keys absent and the review
poller disabled, and verify `/ready`, authenticated reads, and account export.
Compare all original card/session/plan columns with the untouched restore.

Record the selected backup time, start/end times, actual data loss, schema heads,
counts, and test results. The September 9 evidence is in
[`production-recovery.json`](../../docs/audits/2026-09-09/production-recovery.json).
A checksum round trip proves storage integrity; only a successful restore drill
proves that the archive is usable. Repeat a drill monthly and after provider or
Postgres-major-version changes.

References: [Railway's logical-backup guide](https://docs.railway.com/guides/postgres-backups-restores)
and [bucket billing](https://docs.railway.com/storage-buckets/billing).
