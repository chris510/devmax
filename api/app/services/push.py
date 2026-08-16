"""Resilient APNs delivery. Token auth stays in memory; no key file is written."""

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)

_SEND_TIMEOUT_SECONDS = 8
_INVALID_TOKEN_REASONS = frozenset(
    {"baddevicetoken", "devicetokennotfortopic", "unregistered"}
)
_apns_client: Any | None = None


@dataclass(frozen=True)
class PushDelivery:
    sent: int
    attempted: int
    invalid_tokens: frozenset[str] = frozenset()

    @property
    def failed(self) -> int:
        return self.attempted - self.sent

    def __bool__(self) -> bool:
        return self.sent > 0


def _get_apns_client() -> Any:
    """Build one connection pool per process instead of a TLS pool per push."""
    global _apns_client
    if _apns_client is None:
        settings = get_settings()
        # Imported lazily so tests and local runs need no valid .p8 just to import.
        from aioapns import APNs

        _apns_client = APNs(
            key=settings.apns_private_key,
            key_id=settings.apns_key_id,
            team_id=settings.apns_team_id,
            topic=settings.apns_bundle_id,
            use_sandbox=settings.apns_use_sandbox,
            max_connections=10,
            max_connection_attempts=2,
        )
    return _apns_client


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:12]


async def _send_one(
    *, apns: Any, token: str, title: str, body: str, card_id: uuid.UUID
) -> tuple[bool, bool]:
    from aioapns import NotificationRequest, PushType

    request = NotificationRequest(
        device_token=token,
        message={
            "aps": {"alert": {"title": title, "body": body}, "sound": "default"},
            "card_id": str(card_id),
        },
        notification_id=str(uuid.uuid4()),
        time_to_live=4 * 60 * 60,
        priority=10,
        # A retry for this review replaces an earlier pending delivery instead
        # of showing duplicate notifications on the same device.
        collapse_key=f"review-{card_id}",
        push_type=PushType.ALERT,
    )
    try:
        response = await asyncio.wait_for(
            apns.send_notification(request), timeout=_SEND_TIMEOUT_SECONDS
        )
    except TimeoutError:
        log.warning("apns timeout token_fingerprint=%s", _token_fingerprint(token))
        return False, False
    except Exception as exc:  # transport errors must not abort other devices
        log.warning(
            "apns transport failure token_fingerprint=%s type=%s",
            _token_fingerprint(token),
            type(exc).__name__,
        )
        return False, False

    if response.is_successful:
        return True, False
    description = (response.description or "unknown").lower()
    invalid = description in _INVALID_TOKEN_REASONS
    log.warning(
        "apns rejected token_fingerprint=%s reason=%s permanent=%s",
        _token_fingerprint(token),
        description,
        invalid,
    )
    return False, invalid


async def send_push(
    *, tokens: list[str], title: str, body: str, card_id: uuid.UUID
) -> PushDelivery:
    """Push one review notification to every registered device.

    The payload carries the card id so the client can deep-link straight into
    that session. Each device is isolated and bounded by a timeout; permanent
    APNs rejections are returned so the caller can remove stale registrations.
    """
    settings = get_settings()
    if not tokens:
        return PushDelivery(sent=0, attempted=0)
    if not settings.apns_private_key:
        log.warning("apns not configured: skipping push for card=%s", card_id)
        return PushDelivery(sent=0, attempted=0)

    apns = _get_apns_client()
    results = await asyncio.gather(
        *(
            _send_one(apns=apns, token=token, title=title, body=body, card_id=card_id)
            for token in tokens
        )
    )
    invalid_tokens = frozenset(
        token for token, (_sent, invalid) in zip(tokens, results, strict=True) if invalid
    )
    sent = sum(1 for successful, _invalid in results if successful)
    return PushDelivery(
        sent=sent,
        attempted=len(tokens),
        invalid_tokens=invalid_tokens,
    )
