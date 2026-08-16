from contextlib import AbstractAsyncContextManager

from alembic.config import Config
from alembic.script import ScriptDirectory

from app import main


class _Result:
    def __init__(self, rows=()) -> None:
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Session:
    def __init__(self, revision: str) -> None:
        self.revision = revision

    async def exec(self, statement):
        if "alembic_version" in str(statement):
            return _Result([(self.revision,)])
        return _Result()


class _Context(AbstractAsyncContextManager):
    def __init__(self, revision: str) -> None:
        self.session = _Session(revision)

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return None


def test_expected_schema_revision_tracks_the_single_alembic_head() -> None:
    config = Config("alembic.ini")
    assert ScriptDirectory.from_config(config).get_heads() == [
        main.EXPECTED_SCHEMA_REVISION
    ]


async def test_liveness_has_no_authentication_or_database_dependency(client) -> None:
    response = await client.get("/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


async def test_readiness_reports_exact_migration_and_enforcement(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(
        main,
        "session_factory",
        lambda: _Context(main.EXPECTED_SCHEMA_REVISION),
    )

    response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "schema_revision": main.EXPECTED_SCHEMA_REVISION,
        "ai_consent_enforcement_enabled": False,
    }


async def test_readiness_rejects_a_schema_behind_the_app(client, monkeypatch) -> None:
    monkeypatch.setattr(main, "session_factory", lambda: _Context("0021"))

    response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "reason": "schema_mismatch",
        "expected_schema_revision": main.EXPECTED_SCHEMA_REVISION,
    }
