"""Short-lived poller for the Railway trigger-review cron service."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DEFAULT_ATTEMPTS = 4
DEFAULT_RETRY_DELAY_SECONDS = 5
DEFAULT_TIMEOUT_SECONDS = 60


class Response(Protocol):
    def __enter__(self) -> Response: ...

    def __exit__(self, *args: object) -> None: ...

    def read(self) -> bytes: ...


OpenUrl = Callable[..., Response]
Sleep = Callable[[float], None]


class PollError(RuntimeError):
    """The trigger endpoint could not be called successfully."""


def _validated_url(api_base_url: str) -> str:
    base_url = api_base_url.rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PollError("API_BASE_URL must be an absolute http(s) URL")
    return f"{base_url}/internal/trigger-review"


def _decode_result(body: bytes) -> dict[str, Any]:
    try:
        result = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PollError("trigger-review returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise PollError("trigger-review returned a non-object JSON response")
    return result


def poll_trigger_review(
    api_base_url: str,
    cron_secret: str,
    *,
    opener: OpenUrl = urlopen,
    sleep: Sleep = time.sleep,
    attempts: int = DEFAULT_ATTEMPTS,
    retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """POST the dumb poll endpoint, retrying only transient failures."""
    if not cron_secret:
        raise PollError("CRON_SECRET is unset")
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    request = Request(
        _validated_url(api_base_url),
        method="POST",
        headers={"X-Cron-Secret": cron_secret},
    )

    for attempt in range(1, attempts + 1):
        try:
            with opener(request, timeout=timeout_seconds) as response:
                return _decode_result(response.read())
        except HTTPError as exc:
            retryable = exc.code >= 500
            detail = exc.read().decode("utf-8", errors="replace")
            error = PollError(f"trigger-review returned HTTP {exc.code}: {detail}")
        except (TimeoutError, URLError) as exc:
            retryable = True
            error = PollError(f"trigger-review request failed: {exc}")

        if not retryable or attempt == attempts:
            raise error
        sleep(retry_delay_seconds)

    raise AssertionError("unreachable")


def main() -> int:
    try:
        result = poll_trigger_review(
            os.environ.get("API_BASE_URL", ""),
            os.environ.get("CRON_SECRET", ""),
        )
    except PollError as exc:
        print(f"trigger-review poll failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    if result.get("reason") == "no_devices":
        print(
            "nothing delivered: no registered device token or APNs credentials",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
