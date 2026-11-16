"""PostgreSQL transactions for reminder management and the delivery queue."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from alpha_course.application.engagement import (
    POSTPONE_SPACING,
    WINDOW,
    NotificationSettings,
    next_slot_after_limit,
    shift_for_quiet_hours,
)
from alpha_course.application.reminders import (
    DeliveryResult,
    DeliveryStatus,
    NotFound,
    ReminderError,
    Schedule,
    StaleReminder,
    local_options,
    next_due,
)
from alpha_course.application.weekly_summary import same_sunday
from alpha_course.domain.reminders import OccurrenceStatus as State
from alpha_course.domain.reminders import ReminderFrequency, ReminderStatus
from alpha_course.infrastructure.models import (
    NotificationSettingsRecord,
    ReminderAttempt,
    ReminderOccurrence,
    ReminderRecord,
)

LATE_WINDOW = timedelta(minutes=30)
LEASE = timedelta(seconds=120)
ACTIVE_LIMIT = 5
BATCH_SIZE = 50


@dataclass(frozen=True)
class Delivery:
    reminder_id: UUID
    occurrence_id: UUID
    token: UUID
    owner_id: int
    event_text: str = field(repr=False)
    link_url: str | None = field(default=None, repr=False)
    link_title: str | None = field(default=None, repr=False)
    kind: str = "normal"
    timezone: str | None = None
    due_utc: datetime | None = None


KEEP: Any = object()


def settings_from_record(record: NotificationSettingsRecord | None) -> NotificationSettings:
    if record is None:
        return NotificationSettings()
    return NotificationSettings(
        timezone=record.timezone,
        quiet_enabled=record.quiet_enabled,
        quiet_start=record.quiet_start,
        quiet_end=record.quiet_end,
        weekly_limit=record.weekly_limit,
        enabled=record.enabled,
        weekly_summary=record.weekly_summary,
        weekly_summary_asked=record.weekly_summary_asked,
    )


class ReminderStore:
    def __init__(
        self, engine: AsyncEngine, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.clock = clock

    def _save_schedule(self, reminder: ReminderRecord, schedule: Schedule) -> None:
        due = schedule.first_due()
        now = self.clock()
        if due <= now:
            raise ReminderError("Выбери дату и время в будущем.")
        reminder.event_text = schedule.event_text.strip()
        reminder.local_time = schedule.local_at.time()
        reminder.timezone = schedule.timezone
        reminder.frequency = schedule.frequency
        reminder.weekday = (
            schedule.local_at.weekday() if schedule.frequency == ReminderFrequency.WEEKLY else None
        )
        reminder.month_day = (
            schedule.day_of_month
            if schedule.frequency in (ReminderFrequency.MONTHLY, ReminderFrequency.YEARLY)
            else None
        )
        reminder.next_due_utc = due
        reminder.confirmed_at_utc = now
        reminder.rules_version = 1

    async def create(
        self,
        owner_id: int,
        operation_id: UUID,
        schedule: Schedule,
        *,
        link_url: str | None = None,
        link_title: str | None = None,
        limit: int = ACTIVE_LIMIT,
    ) -> ReminderRecord:
        async with self.sessions.begin() as session:
            # Serialize the per-user limit check, even before the first reminder exists.
            await session.execute(select(func.pg_advisory_xact_lock(owner_id)))
            prior = await session.scalar(
                select(ReminderRecord).where(
                    ReminderRecord.owner_id == owner_id, ReminderRecord.operation_id == operation_id
                )
            )
            if prior is not None:
                return prior
            active = await session.scalar(
                select(func.count())
                .select_from(ReminderRecord)
                .where(
                    ReminderRecord.owner_id == owner_id,
                    ReminderRecord.status == ReminderStatus.ACTIVE,
                    ReminderRecord.kind == "normal",
                )
            )
            if active >= limit:
                raise ReminderError("У тебя уже пять активных напоминаний. Отключи одно из них.")
            reminder = ReminderRecord(
                owner_id=owner_id,
                operation_id=operation_id,
                status=ReminderStatus.ACTIVE,
                version=1,
            )
            reminder.link_url = link_url
            reminder.link_title = link_title
            self._save_schedule(reminder, schedule)
            session.add(reminder)
            return reminder

    async def list(self, owner_id: int) -> list[ReminderRecord]:
        async with self.sessions() as session:
            return list(
                (
                    await session.scalars(
                        select(ReminderRecord)
                        .where(
                            ReminderRecord.owner_id == owner_id,
                            ReminderRecord.status == ReminderStatus.ACTIVE,
                            ReminderRecord.kind == "normal",
                        )
                        .order_by(ReminderRecord.next_due_utc.nulls_last(), ReminderRecord.id)
                    )
                ).all()
            )

    async def _owned(
        self, session: AsyncSession, owner_id: int, reminder_id: UUID
    ) -> ReminderRecord:
        reminder = await session.scalar(
            select(ReminderRecord)
            .where(ReminderRecord.id == reminder_id, ReminderRecord.owner_id == owner_id)
            .with_for_update()
        )
        if reminder is None:
            raise NotFound("Напоминание не найдено.")
        return reminder

    async def get(self, owner_id: int, reminder_id: UUID) -> ReminderRecord | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(ReminderRecord).where(
                    ReminderRecord.id == reminder_id, ReminderRecord.owner_id == owner_id
                )
            )

    async def snooze(
        self, owner_id: int, reminder_id: UUID, kind: str, token: str = ""
    ) -> ReminderRecord:
        """Create a one-off copy due in an hour (kind "h1") or tomorrow at the same time ("d1").

        The same (reminder, kind, token) always maps to one copy, so repeated taps are safe.
        """
        original = await self.get(owner_id, reminder_id)
        if original is None or original.kind == "weekly_summary":
            raise NotFound("Напоминание не найдено.")
        zone = ZoneInfo(original.timezone)
        now = self.clock()
        if kind == "h1":
            local = (now + timedelta(hours=1)).astimezone(zone)
            local_at = local.replace(tzinfo=None, second=0, microsecond=0)
            ambiguous = len(local_options(local_at, zone)) > 1
            fold = local.fold if ambiguous else None
        elif kind == "d1":
            local_at = datetime.combine(
                now.astimezone(zone).date() + timedelta(days=1), original.local_time
            )
            fold = 0 if len(local_options(local_at, zone)) > 1 else None
        else:
            raise ReminderError("Неизвестный вариант переноса.")
        operation_id = uuid5(NAMESPACE_URL, f"snooze:{reminder_id.hex}:{kind}:{token}")
        schedule = Schedule(
            original.event_text, local_at, original.timezone, ReminderFrequency.ONCE, fold
        )
        # A snooze may exceed the normal limit a little so it never blocks a recurring reminder.
        return await self.create(
            owner_id,
            operation_id,
            schedule,
            link_url=original.link_url,
            link_title=original.link_title,
            limit=2 * ACTIVE_LIMIT,
        )

    async def update(
        self,
        owner_id: int,
        reminder_id: UUID,
        version: int,
        schedule: Schedule,
        *,
        link_url: str | None = KEEP,
        link_title: str | None = KEEP,
    ) -> ReminderRecord:
        async with self.sessions.begin() as session:
            reminder = await self._owned(session, owner_id, reminder_id)
            if reminder.version != version or reminder.status != ReminderStatus.ACTIVE:
                raise StaleReminder("Напоминание уже изменилось. Открой список заново.")
            self._save_schedule(reminder, schedule)
            if link_url is not KEEP:
                reminder.link_url = link_url
            if link_title is not KEEP:
                reminder.link_title = link_title
            reminder.version += 1
            await self._cancel_pending(session, reminder.id)
            return reminder

    async def disable(
        self, owner_id: int, reminder_id: UUID, *, version: int | None = None
    ) -> ReminderRecord:
        async with self.sessions.begin() as session:
            reminder = await self._owned(session, owner_id, reminder_id)
            if reminder.status == ReminderStatus.ACTIVE:
                if version is not None and reminder.version != version:
                    raise StaleReminder("Напоминание уже изменилось. Открой список заново.")
                await self._disable(session, reminder)
            return reminder

    async def _disable(self, session: AsyncSession, reminder: ReminderRecord) -> None:
        reminder.status = ReminderStatus.DISABLED
        reminder.version += 1
        reminder.next_due_utc = None
        await self._cancel_pending(session, reminder.id)

    async def _cancel_pending(self, session: AsyncSession, reminder_id: UUID) -> None:
        await session.execute(
            update(ReminderOccurrence)
            .where(
                ReminderOccurrence.reminder_id == reminder_id,
                ReminderOccurrence.status.in_((State.PENDING, State.CLAIMED)),
            )
            .values(status=State.CANCELLED, token=None, lease_until_utc=None)
        )

    async def plan(self) -> None:
        now = self.clock()
        cutoff = now - LATE_WINDOW
        due_query = select(ReminderRecord).where(
            ReminderRecord.status == ReminderStatus.ACTIVE,
            ReminderRecord.next_due_utc <= now,
        )
        async with self.sessions() as session:
            ids = (
                await session.scalars(
                    due_query.with_only_columns(ReminderRecord.id)
                    .order_by(ReminderRecord.next_due_utc, ReminderRecord.id)
                    .limit(BATCH_SIZE)
                )
            ).all()
        for reminder_id in ids:
            async with self.sessions.begin() as session:
                reminder = await session.scalar(
                    due_query.where(ReminderRecord.id == reminder_id).with_for_update(
                        skip_locked=True
                    )
                )
                if reminder is None:
                    continue
                due = reminder.next_due_utc
                local = due.astimezone(ZoneInfo(reminder.timezone)).replace(tzinfo=None)
                schedule = Schedule(
                    reminder.event_text,
                    local,
                    reminder.timezone,
                    ReminderFrequency(reminder.frequency),
                    anchor_day=reminder.month_day,
                )
                following = next_due(schedule, due, now)
                reminder.next_due_utc = following
                if due < cutoff and following is not None:
                    # A previous cursor may be stale while this period is still deliverable.
                    due = next_due(schedule, due, cutoff - timedelta(microseconds=1))
                    if due > now:
                        continue
                expired = due < cutoff
                await session.execute(
                    insert(ReminderOccurrence)
                    .values(
                        id=uuid4(),
                        reminder_id=reminder.id,
                        version=reminder.version,
                        due_utc=due,
                        status=State.EXPIRED if expired else State.PENDING,
                        attempts=0,
                        next_attempt_utc=due,
                    )
                    .on_conflict_do_nothing(constraint="reminder_occurrence_unique")
                )
                if expired and reminder.kind != "weekly_summary":
                    reminder.status = ReminderStatus.FINISHED

    @staticmethod
    def _eligible(reminder: ReminderRecord, occurrence: ReminderOccurrence) -> bool:
        return reminder.status == ReminderStatus.ACTIVE and reminder.version == occurrence.version

    @staticmethod
    def _finish(reminder: ReminderRecord, occurrence: ReminderOccurrence, status: State) -> None:
        occurrence.status = status
        occurrence.lease_until_utc = None
        if (
            status not in (State.PENDING, State.CLAIMED, State.SENDING)
            and ReminderStore._eligible(reminder, occurrence)
            and reminder.frequency == ReminderFrequency.ONCE
        ):
            reminder.status = ReminderStatus.FINISHED

    async def _sent_times(
        self, session: AsyncSession, owner_id: int, since: datetime
    ) -> list[datetime]:
        sent_at = func.coalesce(ReminderAttempt.ended_at_utc, ReminderAttempt.started_at_utc)
        return list(
            (
                await session.scalars(
                    select(sent_at)
                    .join(
                        ReminderOccurrence, ReminderOccurrence.id == ReminderAttempt.occurrence_id
                    )
                    .join(ReminderRecord, ReminderRecord.id == ReminderOccurrence.reminder_id)
                    .where(
                        ReminderRecord.owner_id == owner_id,
                        ReminderAttempt.outcome == DeliveryStatus.SENT,
                        sent_at > since,
                    )
                )
            ).all()
        )

    async def _apply_notification_rules(
        self,
        session: AsyncSession,
        reminder: ReminderRecord,
        occurrence: ReminderOccurrence,
        now: datetime,
    ) -> bool:
        """Cancel or postpone the occurrence per the owner's limits; True if it must not be sent."""
        settings = settings_from_record(
            await session.get(NotificationSettingsRecord, reminder.owner_id)
        )
        if reminder.kind == "weekly_summary" and (
            not settings.weekly_summary
            or not same_sunday(occurrence.due_utc, now, reminder.timezone)
        ):
            self._finish(reminder, occurrence, State.CANCELLED)
            return True
        if not settings.enabled or settings.weekly_limit == 0:
            self._finish(reminder, occurrence, State.CANCELLED)
            return True
        if reminder.kind == "weekly_summary":
            # Changing zones or toggling consent cannot create a second summary
            # in the same local calendar week, including an uncertain send.
            prior = (
                (
                    await session.execute(
                        select(ReminderAttempt.started_at_utc)
                        .join(
                            ReminderOccurrence,
                            ReminderOccurrence.id == ReminderAttempt.occurrence_id,
                        )
                        .where(
                            ReminderOccurrence.reminder_id == reminder.id,
                            ReminderOccurrence.id != occurrence.id,
                            or_(
                                ReminderAttempt.outcome.in_(
                                    (DeliveryStatus.SENT, DeliveryStatus.UNKNOWN)
                                ),
                                ReminderAttempt.outcome.is_(None),
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            zone_info = ZoneInfo(reminder.timezone)
            local_day = now.astimezone(zone_info).date()
            monday = local_day - timedelta(days=local_day.weekday())
            if any(moment.astimezone(zone_info).date() >= monday for moment in prior):
                self._finish(reminder, occurrence, State.CANCELLED)
                return True
        moment = max(occurrence.due_utc, now)
        target = moment
        zone = settings.timezone or reminder.timezone
        sent = (
            await self._sent_times(session, reminder.owner_id, moment - WINDOW)
            if settings.weekly_limit
            else []
        )
        for _ in range(4):
            before = target
            if settings.quiet_enabled:
                target = shift_for_quiet_hours(
                    target, zone, settings.quiet_start, settings.quiet_end
                )
            slot = next_slot_after_limit(sent, settings.weekly_limit, target, now)
            if slot is not None:
                target = slot
            if target == before:
                break
        if target == moment:
            return False
        others = (
            await session.scalars(
                select(ReminderOccurrence.next_attempt_utc)
                .join(ReminderRecord, ReminderRecord.id == ReminderOccurrence.reminder_id)
                .where(
                    ReminderRecord.owner_id == reminder.owner_id,
                    ReminderOccurrence.status == State.PENDING,
                    ReminderOccurrence.id != occurrence.id,
                    ReminderOccurrence.next_attempt_utc > target - POSTPONE_SPACING,
                )
                .order_by(ReminderOccurrence.next_attempt_utc)
            )
        ).all()
        for other in others:
            if abs(other - target) < POSTPONE_SPACING:
                target = other + POSTPONE_SPACING
        clash = await session.scalar(
            select(ReminderOccurrence.id).where(
                ReminderOccurrence.reminder_id == reminder.id,
                ReminderOccurrence.version == occurrence.version,
                ReminderOccurrence.due_utc == target,
                ReminderOccurrence.id != occurrence.id,
            )
        )
        if clash is not None:
            self._finish(reminder, occurrence, State.CANCELLED)
            return True
        if reminder.kind == "weekly_summary" and not same_sunday(
            occurrence.due_utc, target, reminder.timezone
        ):
            self._finish(reminder, occurrence, State.EXPIRED)
            return True
        occurrence.status = State.PENDING
        occurrence.token = None
        occurrence.lease_until_utc = None
        if reminder.kind != "weekly_summary":
            occurrence.due_utc = target
        occurrence.next_attempt_utc = target
        return True

    async def claim(self) -> Delivery | None:
        now = self.clock()
        ready = or_(
            and_(
                ReminderOccurrence.status == State.PENDING,
                ReminderOccurrence.next_attempt_utc <= now,
            ),
            and_(
                ReminderOccurrence.status.in_((State.CLAIMED, State.SENDING)),
                ReminderOccurrence.lease_until_utc <= now,
            ),
        )
        async with self.sessions() as session:
            candidates = (
                await session.execute(
                    select(ReminderOccurrence.reminder_id, ReminderOccurrence.id)
                    .where(ready)
                    .order_by(ReminderOccurrence.next_attempt_utc, ReminderOccurrence.id)
                    .limit(BATCH_SIZE)
                )
            ).all()
        for reminder_id, occurrence_id in candidates:
            async with self.sessions.begin() as session:
                reminder = await session.scalar(
                    select(ReminderRecord)
                    .where(ReminderRecord.id == reminder_id)
                    .with_for_update(skip_locked=True)
                )
                if reminder is None:
                    continue
                occurrence = await session.scalar(
                    select(ReminderOccurrence).where(ReminderOccurrence.id == occurrence_id, ready)
                )
                if occurrence is None:
                    continue
                if occurrence.status == State.SENDING:
                    attempt = await session.get(ReminderAttempt, occurrence.token)
                    attempt.outcome = DeliveryStatus.UNKNOWN
                    attempt.ended_at_utc = now
                    self._finish(reminder, occurrence, State.UNKNOWN)
                    continue
                if not self._eligible(reminder, occurrence):
                    self._finish(reminder, occurrence, State.CANCELLED)
                    continue
                if now > occurrence.due_utc + LATE_WINDOW:
                    self._finish(reminder, occurrence, State.EXPIRED)
                    continue
                if await self._apply_notification_rules(session, reminder, occurrence, now):
                    continue
                occurrence.status = State.CLAIMED
                occurrence.token = uuid4()
                occurrence.lease_until_utc = now + LEASE
                return Delivery(
                    reminder.id,
                    occurrence.id,
                    occurrence.token,
                    reminder.owner_id,
                    reminder.event_text,
                    reminder.link_url,
                    reminder.link_title,
                    reminder.kind,
                    reminder.timezone,
                    occurrence.due_utc,
                )
        return None

    async def authorize(self, delivery: Delivery) -> bool:
        async with self.sessions.begin() as session:
            reminder = await self._owned(session, delivery.owner_id, delivery.reminder_id)
            occurrence = await session.get(ReminderOccurrence, delivery.occurrence_id)
            now = self.clock()
            if (
                occurrence is None
                or occurrence.reminder_id != reminder.id
                or occurrence.token != delivery.token
                or occurrence.status != State.CLAIMED
                or occurrence.lease_until_utc <= now
            ):
                return False
            if not self._eligible(reminder, occurrence):
                self._finish(reminder, occurrence, State.CANCELLED)
                return False
            if now > occurrence.due_utc + LATE_WINDOW:
                self._finish(reminder, occurrence, State.EXPIRED)
                return False
            settings = settings_from_record(
                await session.get(NotificationSettingsRecord, reminder.owner_id)
            )
            if not settings.enabled or settings.weekly_limit == 0:
                self._finish(reminder, occurrence, State.CANCELLED)
                return False
            if reminder.kind == "weekly_summary" and await self._apply_notification_rules(
                session, reminder, occurrence, now
            ):
                return False
            occurrence.status = State.SENDING
            occurrence.lease_until_utc = now + LEASE
            occurrence.attempts += 1
            session.add(
                ReminderAttempt(id=delivery.token, occurrence_id=occurrence.id, started_at_utc=now)
            )
            return True

    async def complete(self, delivery: Delivery, result: DeliveryResult) -> None:
        async with self.sessions.begin() as session:
            if result.status == DeliveryStatus.BLOCKED:
                await session.execute(select(func.pg_advisory_xact_lock(delivery.owner_id)))
            reminder = await self._owned(session, delivery.owner_id, delivery.reminder_id)
            occurrence = await session.get(ReminderOccurrence, delivery.occurrence_id)
            if (
                occurrence is None
                or occurrence.reminder_id != reminder.id
                or occurrence.token != delivery.token
                or occurrence.status not in (State.SENDING, State.UNKNOWN)
            ):
                return
            attempt = await session.get(ReminderAttempt, delivery.token)
            now = self.clock()
            attempt.ended_at_utc = now
            attempt.outcome = result.status
            attempt.message_id = result.message_id
            if result.status == DeliveryStatus.RETRY and occurrence.status == State.SENDING:
                delay = timedelta(
                    seconds=max(5 if occurrence.attempts == 1 else 15, result.retry_after)
                )
                if not self._eligible(reminder, occurrence):
                    status = State.CANCELLED
                elif reminder.kind == "weekly_summary" and not same_sunday(
                    occurrence.due_utc, now + delay, reminder.timezone
                ):
                    status = State.EXPIRED
                elif now + delay > occurrence.due_utc + LATE_WINDOW:
                    status = State.EXPIRED
                elif occurrence.attempts < 3:
                    status = State.PENDING
                    occurrence.next_attempt_utc = now + delay
                else:
                    status = State.FAILED
            else:
                status = {
                    DeliveryStatus.BLOCKED: State.FAILED,
                    DeliveryStatus.FAILED: State.FAILED,
                    DeliveryStatus.SENT: State.SENT,
                    DeliveryStatus.UNKNOWN: State.UNKNOWN,
                    DeliveryStatus.RETRY: State.FAILED,
                }[result.status]
            # Invalidate all current work for a permanently unavailable user.
            if result.status == DeliveryStatus.BLOCKED:
                reminders = await session.scalars(
                    select(ReminderRecord)
                    .where(
                        ReminderRecord.owner_id == reminder.owner_id,
                        ReminderRecord.status == ReminderStatus.ACTIVE,
                    )
                    .with_for_update()
                )
                for active_reminder in reminders:
                    await self._disable(session, active_reminder)
            self._finish(reminder, occurrence, status)
