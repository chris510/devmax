"""One private logical backup, verified by download, then bounded retention.

No restore command exists here. This job only reads Postgres. Database and S3
credentials are environment variables and never appear in command arguments or
logs. Railway's cron starts a fresh process daily and expects it to exit.
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

PREFIX = "postgres/daily/"
BACKUP_KEY = re.compile(r"^postgres/daily/\d{8}T\d{6}Z-[0-9a-f]{32}\.dump$")


def expired_keys(objects: list[dict], now: datetime) -> list[str]:
    """Retain 31 days and at least three copies; ignore every unrelated key."""
    owned = []
    for item in objects:
        if not BACKUP_KEY.fullmatch(item["Key"]):
            continue
        modified = datetime.fromisoformat(item["LastModified"].replace("Z", "+00:00"))
        if modified.tzinfo is None:
            raise ValueError("Backup timestamp must include its timezone")
        owned.append((modified, item["Key"]))
    owned.sort(reverse=True)
    cutoff = now - timedelta(days=31)
    return [key for modified, key in owned[3:] if modified < cutoff]


def run(command: list[str], *, env: dict | None = None) -> str:
    return subprocess.run(
        command, env=env, check=True, capture_output=True, text=True, timeout=600
    ).stdout


def s3(*args: str) -> str:
    return run(
        [
            "aws",
            "--endpoint-url",
            os.environ["BACKUP_S3_ENDPOINT"],
            "--no-cli-pager",
            "s3api",
            *args,
        ]
    )


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    os.umask(0o077)
    source = urlsplit(os.environ["BACKUP_DATABASE_URL"])
    if source.scheme not in {"postgres", "postgresql"} or not source.hostname:
        raise ValueError("A PostgreSQL backup source is required")
    pg_env = os.environ | {
        "PGHOST": source.hostname,
        "PGPORT": str(source.port or 5432),
        "PGDATABASE": unquote(source.path.lstrip("/")),
        "PGUSER": unquote(source.username or ""),
        "PGPASSWORD": unquote(source.password or ""),
        "PGCONNECT_TIMEOUT": "20",
        "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=300000",
        # Railway's private network is encrypted; public database reads use TLS.
        "PGSSLMODE": "disable" if source.hostname.endswith(".railway.internal") else "require",
    }
    bucket = os.environ["BACKUP_S3_BUCKET"]
    now = datetime.now(UTC)
    key = f"{PREFIX}{now:%Y%m%dT%H%M%SZ}-{uuid4().hex}.dump"
    with tempfile.TemporaryDirectory(prefix="postgres-backup-") as temp:
        dump, downloaded = Path(temp) / "database.dump", Path(temp) / "downloaded.dump"
        run(
            ["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(dump)],
            env=pg_env,
        )
        run(["pg_restore", "--list", str(dump)])
        size = dump.stat().st_size
        if size == 0:
            raise ValueError("Empty database archive")
        digest = sha256(dump)
        s3(
            "put-object",
            "--bucket",
            bucket,
            "--key",
            key,
            "--body",
            str(dump),
            "--metadata",
            json.dumps({"sha256": digest}),
            "--content-type",
            "application/octet-stream",
        )
        s3("get-object", "--bucket", bucket, "--key", key, str(downloaded))
        if sha256(downloaded) != digest:
            raise ValueError("Downloaded archive does not match the backup")
        # AWS CLI paginates list-objects-v2 automatically. Retention runs only
        # after a new copy has survived a full upload/download checksum check.
        objects = json.loads(s3("list-objects-v2", "--bucket", bucket, "--prefix", PREFIX))
        removed = expired_keys(objects.get("Contents", []), now)
        for old_key in removed:
            s3("delete-object", "--bucket", bucket, "--key", old_key)
        print(
            json.dumps(
                {
                    "event": "database_backup_verified",
                    "key": key,
                    "bytes": size,
                    "sha256": digest,
                    "expired_copies_removed": len(removed),
                }
            )
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 — redact credentials at the process boundary
        # Subprocess errors may contain connection details. The operation fails
        # visibly without turning the provider log into a credential store.
        print(json.dumps({"event": "database_backup_failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
