import asyncio
import hashlib
import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import load_only
from sqlmodel import col, delete, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import get_session, sibling_session_factory
from app.models import (
    ALL_ISO_WEEKDAYS,
    DELIVERY_CONVERSATIONAL,
    LIVE_STATUSES,
    Card,
    DeviceToken,
    Session,
    Settings,
    User,
)
from app.routers.deps import as_utc, now_in
from app.schemas import TriggerBatchResult, TriggerResult
from app.services.card_lifecycle import active_card_filter, recall_available_filter
from app.services.push import send_push

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])

# A card counts as missed if it was pushed this long ago with nothing started since.
MISSED_AFTER = timedelta(hours=4)
REVIEW_ACCOUNT_CONCURRENCY = 4


def _unresolved_push_filters(cutoff: datetime):
    return (
        col(Card.last_pushed_at).is_not(None),
        col(Card.last_pushed_at) <= cutoff,
        or_(
            col(Card.missed_counted_at).is_(None),
            col(Card.missed_counted_at) < col(Card.last_pushed_at),
        ),
        or_(
            col(Card.push_resolved_at).is_(None),
            col(Card.push_resolved_at) < col(Card.last_pushed_at),
        ),
    )


def _first_real_local_datetime(day: date, wall_time: time, zone: tzinfo) -> datetime:
    """Resolve a wall time, advancing through a timezone gap when necessary.

    Attaching ZoneInfo directly does not reject a nonexistent spring-forward
    time; it creates an imaginary instant with the pre-transition offset. The
    round trip detects that case. Advancing to the first real minute gives every
    poll in the window the same guard boundary. Ambiguous fall-back times use
    fold=0, so both occurrences still belong to one delivery slot.
    """
    naive = datetime.combine(day, wall_time)
    for minute_offset in range(24 * 60 + 1):
        candidate_naive = naive + timedelta(minutes=minute_offset)
        candidate = candidate_naive.replace(tzinfo=zone, fold=0)
        round_trip = candidate.astimezone(UTC).astimezone(zone)
        if round_trip.replace(tzinfo=None) == candidate_naive:
            return candidate
    raise ValueError(f"no real local instant on or after {naive.isoformat()}")


def _active_window_start(settings: Settings, at: datetime) -> datetime | None:
    """The local start of the enabled window `at` falls inside, or None.

    The start instant is all the per-window guard needs; the window's identity is
    not returned because nothing consumes it.

    On overlap the latest-starting window wins. Taking the earlier one would let
    a push already spent in it suppress the window the user is actually in.

    A malformed window is skipped rather than raised on, so one bad entry costs
    its own window and not its neighbours. But if *every* enabled window
    scheduled for today is unparseable, the poll raises: `outside_window` is the
    normal answer roughly 85 times a day, so quietly returning it would turn a broken
    settings row into "no push ever arrives again" with nothing to notice it by.
    A 500 fails the cron run, which is the breakage signal.
    """
    if at.tzinfo is None:
        raise ValueError("notification-window evaluation requires an aware datetime")

    starts: list[datetime] = []
    parsed = 0
    malformed = 0
    for window in settings.windows:
        if not window.get("on"):
            continue
        try:
            days = window.get("days", ALL_ISO_WEEKDAYS)
            if (
                not isinstance(days, list | tuple)
                or not days
                or any(isinstance(day, bool) or not isinstance(day, int) for day in days)
                or any(day < 1 or day > 7 for day in days)
                or len(set(days)) != len(days)
            ):
                raise ValueError("invalid ISO weekdays")
            if at.isoweekday() not in days:
                continue
            start = time.fromisoformat(window["from"])
            end = time.fromisoformat(window["to"])
            if start.tzinfo is not None or end.tzinfo is not None:
                raise ValueError("notification-window times must not carry UTC offsets")
            start_minutes = start.hour * 60 + start.minute
            end_minutes = end.hour * 60 + end.minute
            if end_minutes <= start_minutes:
                raise ValueError("notification-window end must follow its start")
        except (KeyError, TypeError, ValueError):
            malformed += 1
            logger.warning("skipping malformed notification window: %r", window)
            continue
        parsed += 1

        start_local = _first_real_local_datetime(at.date(), start, at.tzinfo)
        end_local = _first_real_local_datetime(at.date(), end, at.tzinfo)
        if end_local <= start_local:
            # If the whole nominal range falls in a spring-forward gap, both
            # boundaries resolve to the first real minute. Preserve the user's
            # configured wall-clock duration from that point instead of silently
            # dropping this one weekly slot.
            end_local = start_local + timedelta(minutes=end_minutes - start_minutes)
        if start_local <= at <= end_local:
            starts.append(start_local)

    if not starts:
        if malformed and not parsed:
            raise HTTPException(
                status_code=500,
                detail=f"every enabled notification window is unparseable ({malformed})",
            )
        return None
    return max(starts)


async def _trigger_review_for_user(
    user_id: uuid.UUID, db: AsyncSession
) -> TriggerResult:
    """Polled by the production API process; decides whether to push.

    The poller encodes no schedule beyond "often". Everything about *when* a push
    goes out. The notification windows, the timezone they are read in, and the
    daily budget live in the settings row, so changing a window in the app takes
    effect on the next poll with no commit or redeploy. The cron used to carry a
    hand-maintained UTC approximation of those windows, which drifted out of
    agreement with them for four months of every year; see docs/DEVIATIONS.md §1.

    Most polls land outside every window and return `outside_window`. That is the
    expected steady state, not a failure.

    Deliberately does not call Claude: generating a question for a push that may
    never be opened wastes tokens and latency. Question generation happens on
    engagement, in POST /cards/{id}/sessions.
    """
    # A user-row KEY SHARE lock conflicts with account deletion without blocking
    # ordinary foreign-key inserts. Taking it first preserves the account-wide
    # User → Settings → Card order: deletion cannot cascade through Cards and
    # then wait on Settings while this transaction does the reverse.
    user = await db.get(
        User,
        user_id,
        with_for_update={"read": True, "key_share": True},
        populate_existing=True,
    )
    if user is None:
        return TriggerResult(sent=False, reason="outside_window")

    # Serialize every poll for one account on its settings row. Card locks keep
    # Learn/session creation safe, but two polls can choose two different cards;
    # without this account-level lock both can pass the same window and daily
    # guards before either stamps its card. Different accounts still run in
    # parallel because each owns a different row.
    locked_settings = (
        await db.exec(
            select(Settings)
            .where(Settings.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if locked_settings is None:
        return TriggerResult(sent=False, reason="outside_window")
    settings = locked_settings

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
    if local_now.tzinfo is None:
        raise ValueError("notification polling requires an aware local clock")
    day_start = _first_real_local_datetime(today, time.min, local_now.tzinfo).astimezone(UTC)
    window_start = window_start_local.astimezone(UTC)

    # Both guards below read the same fact, so they share one query. `window_start`
    # is always >= `day_start`. A window begins on the day it belongs to, so the
    # newest push today answers "has this window fired" and the count answers "is
    # the day spent". Aggregates rather than entities: a `select(Card)` here
    # hydrated every matching row, unbounded TEXT columns and all, to produce a
    # count and a boolean. `IS NOT NULL` is implied by the `>=`.
    pushed_today, latest_push = (
        await db.exec(
            select(func.count(), func.max(col(Card.last_pushed_at))).where(
                Card.user_id == user_id, col(Card.last_pushed_at) >= day_start
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

    due_filter = (
        Card.user_id == user_id,
        active_card_filter(),
        recall_available_filter(local_now.astimezone(UTC)),
        Card.delivery_mode == DELIVERY_CONVERSATIONAL,
        col(Card.next_review_at) <= today,
    )
    live_session_exists = (
        select(Session.id)
        .where(
            Session.card_id == Card.id,
            col(Session.status).in_(LIVE_STATUSES),
        )
        .exists()
    )
    # The final candidate is locked before it becomes observable outside the
    # database. Learn and session creation take this same Card lock, so either
    # their eligibility-changing commit wins first and this query re-evaluates the
    # row, or this push wins and they wait until its stamp is durable. The lock is
    # deliberately held through APNs; without a reservation column, releasing it
    # earlier re-opens the exact selection-to-send race this boundary closes.
    top = (
        await db.exec(
            select(Card)
            .options(load_only(Card.id, Card.topic, Card.last_pushed_at))
            .where(
                *due_filter,
                ~live_session_exists,
                or_(
                    col(Card.last_pushed_at).is_(None),
                    col(Card.last_pushed_at) < day_start,
                ),
            )
            .order_by(col(Card.next_review_at).asc(), col(Card.ease_factor).asc())
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).first()
    if top is None:
        # Distinguish an empty/gated queue from a queue whose cards were all
        # offered earlier today. This count occurs after any contended candidate
        # update committed, rather than reporting the stale pre-lock queue.
        due_count = (
            await db.exec(select(func.count()).select_from(Card).where(*due_filter))
        ).one()
        if not due_count:
            return TriggerResult(sent=False, reason="nothing_due")
        return TriggerResult(sent=False, reason="already_offered", due_count=due_count)

    # Session creation owns this same card lock, but keep the state check explicit:
    # a session committed before this lock must turn the candidate into engagement,
    # not a notification about work already in progress.
    live = (
        await db.exec(
            select(Session.id).where(
                Session.card_id == top.id,
                col(Session.status).in_(LIVE_STATUSES),
            )
        )
    ).first()
    if live is not None:
        due_count = (
            await db.exec(select(func.count()).select_from(Card).where(*due_filter))
        ).one()
        await db.commit()
        return TriggerResult(
            sent=False,
            reason="already_offered",
            due_count=due_count,
        )

    # Another poll may have passed the cheap guards and then waited on this row.
    # Re-read both account-level guards after acquiring the candidate lock so two
    # concurrent polls cannot advance to different cards inside one window.
    pushed_today, latest_push = (
        await db.exec(
            select(func.count(), func.max(col(Card.last_pushed_at))).where(
                Card.user_id == user_id, col(Card.last_pushed_at) >= day_start
            )
        )
    ).one()
    if latest_push is not None and as_utc(latest_push) >= window_start:
        await db.commit()  # release the candidate lock before returning
        return TriggerResult(sent=False, reason="already_pushed")
    if pushed_today >= settings.reviews_per_day:
        await db.commit()  # release the candidate lock before returning
        return TriggerResult(sent=False, reason="daily_limit")

    # This is the queue after the candidate was serialized with Learn/session,
    # so the notification count does not include a card that gated while the
    # poll was waiting for its lock.
    due_count = (
        await db.exec(select(func.count()).select_from(Card).where(*due_filter))
    ).one()
    tokens = [
        t.token
        for t in (await db.exec(select(DeviceToken).where(DeviceToken.user_id == user_id))).all()
    ]
    delivery = await send_push(
        tokens=tokens,
        title=f"{due_count} due",
        body=top.topic,
        card_id=top.id,
    )
    if delivery.invalid_tokens:
        await db.exec(
            delete(DeviceToken).where(
                DeviceToken.user_id == user_id,
                col(DeviceToken.token).in_(delivery.invalid_tokens),
            )
        )

    # Reporting sent=True when nothing was delivered, either because there is no
    # registered device or APNs credentials are not configured, would stamp
    # last_pushed_at, and check-missed would
    # then increment missed_count four hours later for a push the user never received.
    # missed_count is the product's only compliance signal; don't corrupt it.
    if not delivery:
        await db.commit()  # no stamp, but release the candidate lock promptly
        reason = "delivery_failed" if delivery.attempted else "no_devices"
        return TriggerResult(sent=False, reason=reason, due_count=due_count)

    # From the injected clock, not `datetime.now(UTC)`: the stamp has to land inside
    # the window that was just matched, and a direct clock read is unpatchable, so
    # under a pinned test clock it could fall outside it.
    top.last_pushed_at = local_now.astimezone(UTC)
    db.add(top)
    await db.commit()

    return TriggerResult(sent=True, card_id=top.id, due_count=due_count)


async def _evaluate_review_target(
    user_id: uuid.UUID,
    factory: async_sessionmaker[AsyncSession],
) -> TriggerResult | None:
    """Evaluate one account in an isolated transaction."""
    async with factory() as account_db:
        try:
            result = await _trigger_review_for_user(user_id, account_db)
            # Several normal early returns are read-only. End their transaction
            # before releasing the worker slot so a later failure cannot affect it.
            await account_db.commit()
            return result
        except Exception as exc:
            await account_db.rollback()
            fingerprint = hashlib.sha256(str(user_id).encode()).hexdigest()[:12]
            logger.error(
                "review evaluation failed user_fingerprint=%s type=%s",
                fingerprint,
                type(exc).__name__,
            )
            return None


async def _evaluate_review_targets(
    user_ids: list[uuid.UUID],
    factory: async_sessionmaker[AsyncSession],
    *,
    concurrency: int,
) -> list[TriggerResult | None]:
    """Evaluate an arbitrary account set with a fixed number of workers."""
    target_iterator = iter(user_ids)

    async def worker() -> list[TriggerResult | None]:
        return [
            await _evaluate_review_target(user_id, factory)
            for user_id in target_iterator
        ]

    worker_outcomes = await asyncio.gather(*(worker() for _ in range(concurrency)))
    return [result for batch in worker_outcomes for result in batch]


@router.post("/trigger-review", response_model=TriggerResult | TriggerBatchResult)
async def trigger_review(
    db: AsyncSession = Depends(get_session),
) -> TriggerResult | TriggerBatchResult:
    """Evaluate every account independently on one dumb poll.

    A one-user installation keeps the original response byte shape. Once there
    are multiple users, the aggregate reports counts while each user's window,
    daily cap, cards, and APNs tokens remain isolated inside the helper above.
    """
    rows = (await db.exec(select(Settings))).all()
    if not rows:
        return TriggerResult(sent=False, reason="outside_window")

    if len(rows) == 1:
        return await _trigger_review_for_user(rows[0].user_id, db)

    # Snapshot identities before ending the discovery transaction. Each account
    # then gets an independent session and locks its current settings row, so an
    # eight-second APNs timeout for one account cannot serially consume the
    # poller's entire 60-second deadline.
    user_ids = [row.user_id for row in rows]
    await db.rollback()
    factory, concurrency = sibling_session_factory(
        db, max_concurrency=REVIEW_ACCOUNT_CONCURRENCY
    )
    outcomes = await _evaluate_review_targets(
        user_ids, factory, concurrency=concurrency
    )
    results = [result for result in outcomes if result is not None]
    failed_count = len(outcomes) - len(results)

    sent_count = sum(1 for result in results if result.sent)
    reasons: dict[str, int] = {}
    for result in results:
        reason = result.reason or "sent"
        reasons[reason] = reasons.get(reason, 0) + 1
    return TriggerBatchResult(
        sent=sent_count > 0,
        processed_users=len(rows),
        sent_count=sent_count,
        failed_count=failed_count,
        reasons=reasons,
    )


@router.post("/check-missed")
async def check_missed(db: AsyncSession = Depends(get_session)) -> dict[str, int]:
    """Increment missed_count for pushes that went unanswered.

    Never touches ease_factor. Missing a review is a compliance signal, not a
    retention signal. Conflating them would let a busy week trash the ease
    factor on topics the user knows cold.

    Nor does it touch `last_pushed_at`. The push that was counted is recorded on
    `missed_counted_at` instead, because `trigger-review` reads `last_pushed_at`
    to answer both "how many pushes today" and "has this window already been
    satisfied". Clearing it handed the day's budget back and would re-open a
    spent window. A card becomes eligible again when a later push moves
    `last_pushed_at` past the stamp.
    """
    cutoff = datetime.now(UTC) - MISSED_AFTER
    candidate_ids = (
        await db.exec(
            select(Card.id).where(
                active_card_filter(),
                *_unresolved_push_filters(cutoff),
            )
        )
    ).all()
    # The discovery query is intentionally lock-free. Close its transaction, then
    # serialize and commit one card at a time so a large missed batch never holds
    # earlier locks while it works through later candidates.
    await db.commit()

    marked = 0
    for card_id in candidate_ids:
        card = (
            await db.exec(
                select(Card)
                .where(
                    Card.id == card_id,
                    active_card_filter(),
                    *_unresolved_push_filters(cutoff),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).first()
        if card is None:
            await db.commit()
            continue

        # Opening the trusted learning material is engagement with the push even
        # though it deliberately creates no scored session. It must suppress a
        # miss only when that exposure happened after this particular push.
        if (
            card.last_learning_exposure_at is not None
            and as_utc(card.last_learning_exposure_at) >= as_utc(card.last_pushed_at)
        ):
            card.push_resolved_at = card.last_pushed_at
            db.add(card)
            await db.commit()
            continue
        started_since = (
            await db.exec(
                select(Session.id).where(
                    Session.card_id == card.id,
                    col(Session.started_at) >= card.last_pushed_at,
                )
            )
        ).first()
        if started_since is not None:
            card.push_resolved_at = card.last_pushed_at
            db.add(card)
            await db.commit()
            continue
        card.missed_count += 1
        # Records *which push* was counted, not when it was counted, so the
        # predicate above reads exactly as "this push is still uncounted" and a
        # re-run is a no-op.
        card.missed_counted_at = card.last_pushed_at
        db.add(card)
        await db.commit()
        marked += 1

    return {"marked_missed": marked}
