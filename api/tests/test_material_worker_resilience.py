import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import (
    FOUNDER_USER_ID,
    SOURCE_FAILED,
    SOURCE_PENDING,
    SOURCE_PROCESSING,
    MaterialSource,
)
from app.services import guide_import, materials
from tests.conftest import GUIDE


async def test_unexpected_worker_error_becomes_retryable_failure(db, monkeypatch) -> None:
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Unexpected parser failure",
        source_text=GUIDE,
        import_path="topics",
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()
    source_id = source.id
    factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(materials, "session_factory", factory)

    async def unexpected(*_args, **_kwargs):
        raise RuntimeError("raw provider-derived details")

    monkeypatch.setattr(materials, "_process_topics", unexpected)

    assert await materials.process_import(source_id) is True
    db.expire_all()
    current = await db.get(MaterialSource, source_id)
    assert current.status == SOURCE_FAILED
    assert current.error == "Import failed unexpectedly. Retry to continue."
    assert current.processing_run_id is None
    assert current.processing_heartbeat_at is None


async def test_recovery_scan_ignores_a_worker_with_a_fresh_heartbeat(
    db, monkeypatch
) -> None:
    pending = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Pending",
        source_text=GUIDE,
        status=SOURCE_PENDING,
    )
    fresh = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Fresh live claim",
        source_text=GUIDE,
        status=SOURCE_PROCESSING,
        processing_run_id=uuid.uuid4(),
        processing_heartbeat_at=datetime.now(UTC),
    )
    stale = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Stale claim",
        source_text=GUIDE,
        status=SOURCE_PROCESSING,
        processing_run_id=uuid.uuid4(),
        processing_heartbeat_at=(
            datetime.now(UTC) - guide_import.GUIDE_IMPORT_STALE_AFTER - timedelta(seconds=1)
        ),
    )
    db.add(pending)
    db.add(fresh)
    db.add(stale)
    await db.commit()
    factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(materials, "session_factory", factory)

    recoverable = set(await materials.recoverable_imports())

    assert pending.id in recoverable
    assert stale.id in recoverable
    assert fresh.id not in recoverable
