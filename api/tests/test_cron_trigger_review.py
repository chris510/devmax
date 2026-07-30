import json
from io import BytesIO
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from app.cron_trigger_review import PollError, poll_trigger_review


class FakeResponse:
    def __init__(self, body: dict[str, object]) -> None:
        self.body = json.dumps(body).encode()

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_poller_posts_secret_to_trigger_endpoint() -> None:
    captured: dict[str, object] = {}

    def opener(request: Request, *, timeout: float) -> FakeResponse:
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["secret"] = request.get_header("X-cron-secret")
        captured["timeout"] = timeout
        return FakeResponse({"sent": False, "reason": "outside_window"})

    result = poll_trigger_review(
        "https://devmax.example/",
        "cron-secret",
        opener=opener,
    )

    assert result == {"sent": False, "reason": "outside_window"}
    assert captured == {
        "url": "https://devmax.example/internal/trigger-review",
        "method": "POST",
        "secret": "cron-secret",
        "timeout": 60,
    }


def test_poller_retries_server_errors() -> None:
    calls = 0
    sleeps: list[float] = []

    def opener(request: Request, *, timeout: float) -> FakeResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError(
                request.full_url,
                503,
                "unavailable",
                hdrs=None,
                fp=BytesIO(b'{"detail":"unavailable"}'),
            )
        return FakeResponse({"sent": True, "due_count": 4})

    result = poll_trigger_review(
        "https://devmax.example",
        "cron-secret",
        opener=opener,
        sleep=sleeps.append,
        attempts=2,
        retry_delay_seconds=0.25,
    )

    assert result == {"sent": True, "due_count": 4}
    assert calls == 2
    assert sleeps == [0.25]


def test_poller_does_not_retry_client_errors() -> None:
    calls = 0

    def opener(request: Request, *, timeout: float) -> FakeResponse:
        nonlocal calls
        calls += 1
        raise HTTPError(
            request.full_url,
            401,
            "unauthorized",
            hdrs=None,
            fp=BytesIO(b"{}"),
        )

    with pytest.raises(PollError, match="HTTP 401"):
        poll_trigger_review(
            "https://devmax.example",
            "wrong-secret",
            opener=opener,
            sleep=lambda _: None,
        )

    assert calls == 1


@pytest.mark.parametrize(
    "base_url",
    ["", "devmax.example", "ftp://devmax.example"],
)
def test_poller_rejects_invalid_base_url(base_url: str) -> None:
    with pytest.raises(PollError, match="absolute http"):
        poll_trigger_review(base_url, "cron-secret")
