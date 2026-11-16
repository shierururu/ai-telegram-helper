"""PostgreSQL storage for notification settings and the seven-day learning journey."""

from collections.abc import Callable
from dataclasses import fields, replace
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from alpha_course.application.engagement import (
    JOURNEY_DAYS,
    Journey,
    JourneyDayNotReached,
    JourneyStep,
    NotificationSettings,
)
from alpha_course.application.reminders import ReminderError, Schedule, local_options
from alpha_course.application.weekly_summary import next_summary
from alpha_course.domain.reminders import ReminderFrequency, ReminderStatus
from alpha_course.infrastructure.models import (
    JourneyRecord,
    NotificationSettingsRecord,
    ReminderRecord,
)
from alpha_course.infrastructure.reminders import ReminderStore, settings_from_record

SETTING_FIELDS = tuple(item.name for item in fields(NotificationSettings))
STALE_AFTER = timedelta(days=2)


def _journey(record: JourneyRecord) -> Journey:
    return Journey(
        id=record.id,
        owner_id=record.owner_id,
        goal=record.goal,
        timezone=record.timezone,
        local_time=record.local_time,
        status=record.status,
        steps=tuple(JourneyStep.from_json(item) for item in record.steps),
        created_at=record.created_at,
    )


class EngagementStore:
    def __init__(
        self, engine: AsyncEngine, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.clock = clock
        self._reminders = ReminderStore(engine, clock=clock)

    async def get_settings(self, owner_id: int) -> NotificationSettings:
        async with self.sessions() as session:
            return settings_from_record(await session.get(NotificationSettingsRecord, owner_id))

    async def save_settings(self, owner_id: int, **changes: Any) -> NotificationSettings:
        """Upsert the given fields (timezone, quiet_enabled, quiet_start, quiet_end,
        weekly_limit, enabled); unspecified fields keep their value."""
        unknown = set(changes) - set(SETTING_FIELDS)
        if unknown:
            raise ReminderError("Неизвестная настройка уведомлений.")
        async with self.sessions.begin() as session:
            await session.execute(select(func.pg_advisory_xact_lock(owner_id)))
            record = await session.get(NotificationSettingsRecord, owner_id)
            updated = replace(settings_from_record(record), **changes)
            values = {name: getattr(updated, name) for name in SETTING_FIELDS}
            await session.execute(
                insert(NotificationSettingsRecord)
                .values(owner_id=owner_id, **values)
                .on_conflict_do_update(index_elements=["owner_id"], set_=values)
            )
            reminder = await session.scalar(
                select(ReminderRecord)
                .where(
                    ReminderRecord.owner_id == owner_id,
                    ReminderRecord.kind == "weekly_summary",
                )
                .with_for_update()
            )
            if updated.weekly_summary:
                if updated.timezone is None:
                    raise ReminderError("Для итогов недели укажи часовой пояс.")
                if reminder is None:
                    reminder = ReminderRecord(
                        owner_id=owner_id,
                        operation_id=uuid5(NAMESPACE_URL, f"weekly-summary:{owner_id}"),
                        kind="weekly_summary",
                        status=ReminderStatus.ACTIVE,
                        version=1,
                    )
                    session.add(reminder)
                if (
                    reminder.next_due_utc is None
                    or reminder.timezone != updated.timezone
                    or reminder.status != ReminderStatus.ACTIVE
                ):
                    if reminder.id is not None:
                        reminder.version += 1
                        await self._reminders._cancel_pending(session, reminder.id)
                    due = next_summary(self.clock(), updated.timezone)
                    self._reminders._save_schedule(
                        reminder,
                        Schedule(
                            "Итоги недели",
                            due.astimezone(ZoneInfo(updated.timezone)).replace(tzinfo=None),
                            updated.timezone,
                            ReminderFrequency.WEEKLY,
                        ),
                    )
                    reminder.status = ReminderStatus.ACTIVE
            elif reminder is not None and reminder.status == ReminderStatus.ACTIVE:
                await self._reminders._disable(session, reminder)
            return updated

    async def create_journey(
        self,
        owner_id: int,
        operation_id: UUID,
        goal: str,
        timezone: str,
        local_time: time,
        steps: list[dict[str, Any]],
    ) -> Journey:
        """Create seven one-time reminders, one per day starting tomorrow (local time)."""
        goal = goal.strip()
        if not 1 <= len(goal) <= 300:
            raise ReminderError("Опиши цель длиной от 1 до 300 символов.")
        if len(steps) != JOURNEY_DAYS:
            raise ReminderError("В маршруте должно быть ровно семь шагов.")
        try:
            zone = ZoneInfo(timezone)
        except ValueError, OverflowError:
            raise ReminderError("Проверь часовой пояс IANA.") from None
        if local_time.tzinfo is not None:
            raise ReminderError("Укажи местное время без часового пояса.")
        now = self.clock()
        first_day = now.astimezone(zone).date() + timedelta(days=1)
        async with self.sessions.begin() as session:
            await session.execute(select(func.pg_advisory_xact_lock(owner_id)))
            prior = await session.scalar(
                select(JourneyRecord).where(
                    JourneyRecord.owner_id == owner_id, JourneyRecord.operation_id == operation_id
                )
            )
            if prior is not None:
                return _journey(prior)
            active = (
                await session.scalars(
                    select(JourneyRecord)
                    .where(JourneyRecord.owner_id == owner_id, JourneyRecord.status == "active")
                    .with_for_update()
                )
            ).all()
            for old in active:
                last_due = max(
                    (
                        JourneyStep.from_json(item).due_utc or now - 10 * STALE_AFTER
                        for item in old.steps
                    ),
                    default=now,
                )
                if last_due + STALE_AFTER >= now:
                    raise ReminderError(
                        "У тебя уже есть активный маршрут на неделю. Останови его и начни новый."
                    )
                old.status = "stopped"
            journey_id = uuid4()
            built: list[JourneyStep] = []
            for index, raw in enumerate(steps):
                day = index + 1
                task = str(raw["task"]).strip()
                title = str(raw.get("title") or "").strip()
                local_at = datetime.combine(first_day + timedelta(days=index), local_time)
                options = local_options(local_at, zone)
                if not options:
                    local_at += timedelta(hours=1)  # the wall-clock time is skipped by DST
                    options = local_options(local_at, zone)
                schedule = Schedule(
                    f"📅 День {day} из {JOURNEY_DAYS}: {task}",
                    local_at,
                    timezone,
                    ReminderFrequency.ONCE,
                    0 if len(options) > 1 else None,
                )
                reminder = ReminderRecord(
                    owner_id=owner_id,
                    operation_id=uuid5(journey_id, f"day-{day}"),
                    status=ReminderStatus.ACTIVE,
                    version=1,
                    kind="journey",
                    link_url=raw.get("url"),
                    link_title=title or None,
                )
                self._reminders._save_schedule(reminder, schedule)
                reminder.id = uuid4()
                session.add(reminder)
                built.append(
                    JourneyStep(
                        day=day,
                        title=title,
                        url=raw.get("url"),
                        material_id=raw.get("material_id"),
                        task=task,
                        reminder_id=reminder.id,
                        due_utc=reminder.next_due_utc,
                    )
                )
            record = JourneyRecord(
                id=journey_id,
                owner_id=owner_id,
                operation_id=operation_id,
                goal=goal,
                timezone=timezone,
                local_time=local_time,
                status="active",
                steps=[step.to_json() for step in built],
            )
            session.add(record)
            await session.flush()
            await session.refresh(record)
            return _journey(record)

    async def get_journey(self, owner_id: int) -> Journey | None:
        async with self.sessions() as session:
            record = await session.scalar(
                select(JourneyRecord)
                .where(JourneyRecord.owner_id == owner_id, JourneyRecord.status == "active")
                .order_by(JourneyRecord.created_at.desc(), JourneyRecord.id)
                .limit(1)
            )
            return None if record is None else _journey(record)

    async def mark_done(self, owner_id: int, journey_id: UUID, day: int) -> Journey | None:
        """Mark a day as done once its reminder time has arrived (idempotent)."""
        async with self.sessions.begin() as session:
            record = await session.scalar(
                select(JourneyRecord)
                .where(JourneyRecord.id == journey_id, JourneyRecord.owner_id == owner_id)
                .with_for_update()
            )
            if record is None or record.status == "stopped":
                return None
            steps = [JourneyStep.from_json(item) for item in record.steps]
            step = next((item for item in steps if item.day == day), None)
            if step is None:
                raise ReminderError("В маршруте нет такого дня.")
            if step.done:
                return _journey(record)
            if step.due_utc is not None and step.due_utc > self.clock():
                raise JourneyDayNotReached("Этот день ещё не наступил.")
            steps = [item.mark_done() if item.day == day else item for item in steps]
            record.steps = [item.to_json() for item in steps]
            if all(item.done for item in steps):
                record.status = "done"
            return _journey(record)

    async def stop_journey(self, owner_id: int, journey_id: UUID) -> bool:
        """Stop the journey, disable its reminders and cancel their pending deliveries."""
        async with self.sessions.begin() as session:
            record = await session.scalar(
                select(JourneyRecord)
                .where(JourneyRecord.id == journey_id, JourneyRecord.owner_id == owner_id)
                .with_for_update()
            )
            if record is None:
                return False
            if record.status == "active":
                record.status = "stopped"
            reminder_ids = [item["reminder_id"] for item in record.steps if item.get("reminder_id")]
            reminders = await session.scalars(
                select(ReminderRecord)
                .where(
                    ReminderRecord.id.in_([UUID(value) for value in reminder_ids]),
                    ReminderRecord.owner_id == owner_id,
                    ReminderRecord.status == ReminderStatus.ACTIVE,
                )
                .with_for_update()
            )
            for reminder in reminders:
                await self._reminders._disable(session, reminder)
            return True
