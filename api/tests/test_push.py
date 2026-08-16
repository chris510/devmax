import asyncio
import uuid
from types import SimpleNamespace

from app.services import push


class _Response:
    def __init__(self, *, successful: bool, description: str | None = None) -> None:
        self.is_successful = successful
        self.description = description


class _APNs:
    def __init__(self, responses) -> None:
        self.responses = iter(responses)
        self.requests = []

    async def send_notification(self, request):
        self.requests.append(request)
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        if result == "wait":
            await asyncio.Event().wait()
        return result


def _configured() -> SimpleNamespace:
    return SimpleNamespace(apns_private_key="private")


async def test_push_isolates_devices_and_reports_permanent_rejections(monkeypatch) -> None:
    apns = _APNs(
        [
            _Response(successful=True),
            _Response(successful=False, description="Unregistered"),
            ConnectionError("transport detail that must not escape"),
        ]
    )
    monkeypatch.setattr(push, "get_settings", _configured)
    monkeypatch.setattr(push, "_get_apns_client", lambda: apns)

    delivery = await push.send_push(
        tokens=["good", "stale", "transient"],
        title="1 due",
        body="Consistent hashing",
        card_id=uuid.uuid4(),
    )

    assert delivery.sent == 1
    assert delivery.attempted == 3
    assert delivery.failed == 2
    assert delivery.invalid_tokens == frozenset({"stale"})
    assert all(request.collapse_key.startswith("review-") for request in apns.requests)
    assert all(request.time_to_live == 4 * 60 * 60 for request in apns.requests)


async def test_push_without_configuration_does_not_attempt_delivery(monkeypatch) -> None:
    monkeypatch.setattr(
        push, "get_settings", lambda: SimpleNamespace(apns_private_key="")
    )

    delivery = await push.send_push(
        tokens=["registered"], title="1 due", body="Raft", card_id=uuid.uuid4()
    )

    assert delivery == push.PushDelivery(sent=0, attempted=0)
