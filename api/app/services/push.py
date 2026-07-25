"""APNs client. Token-based auth (.p8), no file on disk — the key is a secret."""

import logging
import uuid

from app.config import get_settings

log = logging.getLogger(__name__)


async def send_push(
    *, tokens: list[str], title: str, body: str, card_id: uuid.UUID
) -> int:
    """Push one review notification to every registered device.

    The payload carries the card id so the client can deep-link straight into
    that session. Returns the number of devices reached.
    """
    settings = get_settings()
    if not tokens:
        return 0
    if not settings.apns_private_key:
        log.warning("apns not configured — skipping push for card=%s", card_id)
        return 0

    # Imported lazily so tests and local runs don't require the APNs dependency
    # or a valid .p8 key just to import the app.
    from aioapns import APNs, NotificationRequest, PushType

    apns = APNs(
        key=settings.apns_private_key,
        key_id=settings.apns_key_id,
        team_id=settings.apns_team_id,
        topic=settings.apns_bundle_id,
        use_sandbox=settings.apns_use_sandbox,
    )

    sent = 0
    for token in tokens:
        request = NotificationRequest(
            device_token=token,
            message={
                "aps": {"alert": {"title": title, "body": body}, "sound": "default"},
                "card_id": str(card_id),
            },
            push_type=PushType.ALERT,
        )
        response = await apns.send_notification(request)
        if response.is_successful:
            sent += 1
        else:
            log.warning("apns rejected token=%s… status=%s", token[:8], response.description)
    return sent
