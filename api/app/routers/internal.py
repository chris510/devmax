import logging
from datetime import UTC, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlmodel import col, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import get_session
from app.models import DELIVERY_CONVERSATIONAL, Card, DeviceToken, Session, Settings
from app.routers.deps import as_utc, get_settings_row, now_in
from app.schemas import TriggerResult
from app.services.push import send_push

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])

# A card counts as missed if it was pushed this long ago with nothing started since.
MISSED_AFTER = timedelta(hours=4)


def _active_window_start(settings: Settings, at: datetime) -> datetime | None:
    """The local start of the enabled window `at` falls inside, or None.

    The start instant is all the per-window guard needs; the window's identity is
    not returned because nothing consumes it.

    On overlap the latest-starting window wins — taking the earlier one would let
    a push already spent in it suppress the window the user is actually in.

    A malformed window is skipped rather than raised on, so one bad entry costs
    its own window and not its neighbours. But if *every* enabled window is
    unparseable the poll raises: `outside_window` is the normal answer ~46 times
    a day, so quietly returning it would turn a broken settings row into "no push
    ever arrives again" with nothing to notice it by. A 500 fails the cron run,
    which is the breakage signal.
    """
    now = at.time()
    starts: list[time] = []
    parsed = 0
    malformed = 0
    for window in settings.windows:
        if not window.get("on"):
            continue
        try:
            start = time.fromisoformat(window["from"])
            end = time.fromisoformat(window["to"])
        except (KeyError, TypeError, ValueError):
            malformed += 1
            logger.warning("skipping malformed notification window: %r", window)
            continue
        parsed += 1
        if start <= now <= end:
            starts.append(start)

    if not starts:
        if malformed and not parsed:
            raise HTTPException(
                status_code=500,
                detail=f"every enabled notification window is unparseable ({malformed})",
            )
        return None
    # `at.tzinfo` is the ZoneInfo itself, not a fixed offset, so combine()
    # re-resolves the offset for that wall time and stays right across DST.
    return datetime.combine(at.date(), max(starts), tzinfo=at.tzinfo)


@router.post("/trigger-review", response_model=TriggerResult)
async def trigger_review(db: AsyncSession = Depends(get_session)) -> TriggerResult:
    """Polled by a short-lived Railway cron service; decides whether to push.

    The workflow encodes no schedule beyond "often". Everything about *when* a push
    goes out — the notification windows, the timezone they are read in, and the
    daily budget — lives in the settings row, so changing a window in the app takes
    effect on the next poll with no commit or redeploy. The cron used to carry a
    hand-maintained UTC approximation of those windows, which drifted out of
    agreement with them for four months of every year; see docs/DEVIATIONS.md §1.

    Most polls land outside every window and return `outside_window`. That is the
    expected steady state, not a failure.

    Deliberately does not call Claude: generating a question for a push that may
    never be opened wastes tokens and latency. Question generation happens on
    engagement, in POST /cards/{id}/sessions.
    """
    settings = await get_settings_row(db)
    local_now = now_in(settings.timezone)

    window_start_local = _active_window_start(settings, local_now)
    if window_start_local is None:
        return TriggerResult(sent=False, reason="outside_window")

    # Both boundaries are converted to UTC before they are bound into a query.
    # SQLite's DATETIME bind processor keeps the wall-clock fields and drops the
    # offset, so binding a Pacific midnight against UTC-stored timestamps compares
    # 00:00 against 07:00 and shifts the day by the offset. Postgres is right
    # either way; only normalising makes the two backends agree.
    today = local_now.date()
    day_start = datetime.combine(today, time.min, tzinfo=local_now.tzinfo).astimezone(UTC)
    window_start = window_start_local.astimezone(UTC)

    # Both guards below read the same fact, so they share one query. `window_start`
    # is always >= `day_start` — a window begins on the day it belongs to — so the
    # newest push today answers "has this window fired" and the count answers "is
    # the day spent". Aggregates rather than entities: a `select(Card)` here
    # hydrated every matching row, unbounded TEXT columns and all, to produce a
    # count and a boolean. `IS NOT NULL` is implied by the `>=`.
    pushed_today, latest_push = (
        await db.exec(
            select(func.count(), func.max(col(Card.last_pushed_at))).where(
                col(Card.last_pushed_at) >= day_start
            )
        )
    ).one()

    # One push per window, not one per poll. Without this a frequent poll fires
    # repeatedly across an 80-minute window and burns the whole day's budget
    # before the evening window ever opens.
    if latest_push is not None and as_utc(latest_push) >= window_start:
        return TriggerResult(sent=False, reason="already_pushed")
    if pushed_today >= settings.reviews_per_day:
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

    # `due_count` stays the whole queue — it becomes the notification's "N due" and
    # has to agree with what GET /cards/due shows. Only the *selection* skips cards
    # pushed earlier today, so the evening window advances to the next card instead
    # of repeating the morning's. One unanswered card is not worth two
    # notifications. Filtered here rather than in SQL simply because `due` is
    # already materialised for the count, so a second predicate would buy nothing.
    candidates = [
        c for c in due if c.last_pushed_at is None or as_utc(c.last_pushed_at) < day_start
    ]
    if not candidates:
        return TriggerResult(sent=False, reason="already_offered", due_count=len(due))

    top = candidates[0]
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

    # From the injected clock, not `datetime.now(UTC)`: the stamp has to land inside
    # the window that was just matched, and a direct clock read is unpatchable, so
    # under a pinned test clock it could fall outside it.
    top.last_pushed_at = local_now.astimezone(UTC)
    db.add(top)
    await db.commit()

    return TriggerResult(sent=True, card_id=top.id, due_count=len(due))


@router.post("/check-missed")
async def check_missed(db: AsyncSession = Depends(get_session)) -> dict[str, int]:
    """Increment missed_count for pushes that went unanswered.

    Never touches ease_factor. Missing a review is a compliance signal, not a
    retention signal — conflating them would let a busy week trash the ease
    factor on topics the user knows cold.

    Nor does it touch `last_pushed_at`. The push that was counted is recorded on
    `missed_counted_at` instead, because `trigger-review` reads `last_pushed_at`
    to answer both "how many pushes today" and "has this window already been
    satisfied" — clearing it handed the day's budget back and would re-open a
    spent window. A card becomes eligible again when a later push moves
    `last_pushed_at` past the stamp.
    """
    cutoff = datetime.now(UTC) - MISSED_AFTER
    candidates = (
        await db.exec(
            select(Card).where(
                col(Card.last_pushed_at).is_not(None),
                col(Card.last_pushed_at) <= cutoff,
                or_(
                    col(Card.missed_counted_at).is_(None),
                    col(Card.missed_counted_at) < col(Card.last_pushed_at),
                ),
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
        # Records *which push* was counted, not when it was counted, so the
        # predicate above reads exactly as "this push is still uncounted" and a
        # re-run is a no-op.
        card.missed_counted_at = card.last_pushed_at
        db.add(card)
        marked += 1

    await db.commit()
    return {"marked_missed": marked}
