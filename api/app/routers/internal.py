from datetime import UTC, datetime, time, timedelta

from fastapi import APIRouter, Depends
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import get_session
from app.models import DELIVERY_CONVERSATIONAL, Card, DeviceToken, Session, Settings
from app.routers.deps import get_settings_row, now_in
from app.schemas import TriggerResult
from app.services.push import send_push

router = APIRouter(prefix="/internal", tags=["internal"])

# A card counts as missed if it was pushed this long ago with nothing started since.
MISSED_AFTER = timedelta(hours=4)


def _in_window(settings: Settings, at: datetime) -> bool:
    now = at.time()
    for window in settings.windows:
        if not window.get("on"):
            continue
        start = time.fromisoformat(window["from"])
        end = time.fromisoformat(window["to"])
        if start <= now <= end:
            return True
    return False


@router.post("/trigger-review", response_model=TriggerResult)
async def trigger_review(db: AsyncSession = Depends(get_session)) -> TriggerResult:
    """Called by GitHub Actions cron.

    Deliberately does not call Claude: generating a question for a push that may
    never be opened wastes tokens and latency. Question generation happens on
    engagement, in POST /cards/{id}/sessions.
    """
    settings = await get_settings_row(db)
    local_now = now_in(settings.timezone)

    # GitHub Actions cron is best-effort and can run minutes late, so the window
    # check happens here rather than being assumed by the schedule.
    if not _in_window(settings, local_now):
        return TriggerResult(sent=False, reason="outside_window")

    today = local_now.date()
    day_start = datetime.combine(today, time.min, tzinfo=local_now.tzinfo)
    pushed_today = (
        await db.exec(
            select(Card).where(
                col(Card.last_pushed_at).is_not(None),
                col(Card.last_pushed_at) >= day_start,
            )
        )
    ).all()
    if len(pushed_today) >= settings.reviews_per_day:
        return TriggerResult(sent=False, reason="daily_limit")

    due = (
        await db.exec(
            select(Card)
            .where(
                Card.delivery_mode == DELIVERY_CONVERSATIONAL,
                col(Card.next_review_at) <= today,
            )
            .order_by(col(Card.next_review_at).asc(), col(Card.ease_factor).asc())
        )
    ).all()
    if not due:
        return TriggerResult(sent=False, reason="nothing_due")

    top = due[0]
    tokens = [t.token for t in (await db.exec(select(DeviceToken))).all()]
    delivered = await send_push(
        tokens=tokens,
        title=f"{len(due)} due",
        body=top.topic,
        card_id=top.id,
    )

    # Reporting sent=True when nothing was delivered — no registered device, or APNs
    # credentials not configured — would stamp last_pushed_at, and check-missed would
    # then increment missed_count four hours later for a push the user never received.
    # missed_count is the product's only compliance signal; don't corrupt it.
    if not delivered:
        return TriggerResult(sent=False, reason="no_devices", due_count=len(due))

    top.last_pushed_at = datetime.now(UTC)
    db.add(top)
    await db.commit()

    return TriggerResult(sent=True, card_id=top.id, due_count=len(due))


@router.post("/check-missed")
async def check_missed(db: AsyncSession = Depends(get_session)) -> dict[str, int]:
    """Increment missed_count for pushes that went unanswered.

    Never touches ease_factor. Missing a review is a compliance signal, not a
    retention signal — conflating them would let a busy week trash the ease
    factor on topics the user knows cold.
    """
    cutoff = datetime.now(UTC) - MISSED_AFTER
    candidates = (
        await db.exec(
            select(Card).where(
                col(Card.last_pushed_at).is_not(None),
                col(Card.last_pushed_at) <= cutoff,
            )
        )
    ).all()

    marked = 0
    for card in candidates:
        started_since = (
            await db.exec(
                select(Session).where(
                    Session.card_id == card.id,
                    col(Session.started_at) >= card.last_pushed_at,
                )
            )
        ).first()
        if started_since is not None:
            continue
        card.missed_count += 1
        # Cleared so the same push isn't counted again on the next run.
        card.last_pushed_at = None
        db.add(card)
        marked += 1

    await db.commit()
    return {"marked_missed": marked}
