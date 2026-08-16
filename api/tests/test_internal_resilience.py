import asyncio
import uuid
from datetime import UTC, datetime

from app.models import Card, Settings, User
from app.routers import internal
from app.schemas import TriggerResult
from app.services.push import PushDelivery

from .conftest import CRON_HEADERS, make_card


async def test_bad_settings_row_does_not_abort_other_users(client, db, monkeypatch) -> None:
    founder_settings = await db.get(Settings, 1)
    founder_settings.timezone = "not/a-timezone"
    db.add(founder_settings)

    user_id = uuid.uuid4()
    db.add(User(id=user_id, onboarding_completed=True))
    await db.flush()
    db.add(
        Settings(
            user_id=user_id,
            timezone="UTC",
            windows=[{"label": "all day", "from": "00:00", "to": "23:59:59", "on": True}],
        )
    )
    db.add(
        make_card(
            user_id=user_id,
            next_review_at=datetime.now(UTC).date(),
        )
    )
    await db.commit()

    async def delivered(**_kwargs):
        return PushDelivery(sent=1, attempted=1)

    monkeypatch.setattr(internal, "send_push", delivered)
    response = await client.post("/internal/trigger-review", headers=CRON_HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "sent": True,
        "reason": "batch",
        "processed_users": 2,
        "sent_count": 1,
        "failed_count": 1,
        "reasons": {"sent": 1},
    }


async def test_review_accounts_run_with_bounded_parallelism(monkeypatch) -> None:
    active = 0
    maximum_active = 0

    async def evaluate(_settings, _user_id, _db):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return TriggerResult(sent=False, reason="nothing_due")

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def commit(self):
            return None

        async def rollback(self):
            return None

    monkeypatch.setattr(internal, "_trigger_review_for_user", evaluate)
    outcomes = await internal._evaluate_review_targets(
        [
            (user_id, Settings(user_id=user_id))
            for user_id in (uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        ],
        FakeSession,
        concurrency=2,
    )

    assert maximum_active == 2
    assert all(result and result.reason == "nothing_due" for result in outcomes)


async def test_permanent_apns_rejection_removes_only_stale_registration(
    client, db, in_window, monkeypatch
) -> None:
    from app.models import DeviceToken
    card: Card = make_card()
    db.add(card)
    db.add(DeviceToken(token="valid", user_id=card.user_id))
    db.add(DeviceToken(token="stale", user_id=card.user_id))
    await db.commit()

    async def partly_delivered(**_kwargs):
        return PushDelivery(
            sent=1,
            attempted=2,
            invalid_tokens=frozenset({"stale"}),
        )

    monkeypatch.setattr(internal, "send_push", partly_delivered)
    response = await client.post("/internal/trigger-review", headers=CRON_HEADERS)

    assert response.status_code == 200
    assert response.json()["sent"] is True
    assert await db.get(DeviceToken, "valid") is not None
    assert await db.get(DeviceToken, "stale") is None


async def test_provider_failure_is_not_reported_as_missing_devices(
    client, db, in_window, monkeypatch
) -> None:
    from app.models import DeviceToken
    card = make_card()
    db.add(card)
    db.add(DeviceToken(token="transient", user_id=card.user_id))
    await db.commit()

    async def failed(**_kwargs):
        return PushDelivery(sent=0, attempted=1)

    monkeypatch.setattr(internal, "send_push", failed)
    response = await client.post("/internal/trigger-review", headers=CRON_HEADERS)

    assert response.json()["reason"] == "delivery_failed"
    await db.refresh(card)
    assert card.last_pushed_at is None
