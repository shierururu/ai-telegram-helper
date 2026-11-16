"""Notification limits and the seven-day learning journey (pure rules, no I/O)."""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from alpha_course.application.reminders import ReminderError
from alpha_course.domain.reminders import require_timezone

WINDOW = timedelta(days=7)
POSTPONE_SPACING = timedelta(minutes=60)
JOURNEY_DAYS = 7
MAX_WEEKLY_LIMIT = 50


class JourneyDayNotReached(ReminderError):
    """The day of the journey has not started yet, so it cannot be marked as done."""


@dataclass(frozen=True)
class NotificationSettings:
    timezone: str | None = None
    quiet_enabled: bool = True
    quiet_start: time = time(22)
    quiet_end: time = time(8)
    weekly_limit: int = 7
    enabled: bool = True
    weekly_summary: bool = False
    weekly_summary_asked: bool = False

    def __post_init__(self) -> None:
        if self.timezone is not None:
            try:
                require_timezone(self.timezone)
            except ValueError:
                raise ReminderError("Проверь часовой пояс IANA.") from None
        if type(self.weekly_limit) is not int or not 0 <= self.weekly_limit <= MAX_WEEKLY_LIMIT:
            raise ReminderError(f"Лимит уведомлений в неделю: от 0 до {MAX_WEEKLY_LIMIT}.")
        for value in (self.quiet_start, self.quiet_end):
            if type(value) is not time or value.tzinfo is not None:
                raise ReminderError("Укажи время без часового пояса.")
        if self.quiet_start == self.quiet_end:
            raise ReminderError("Начало и конец тихих часов не должны совпадать.")


def _in_quiet(clock: time, start: time, end: time) -> bool:
    if start < end:
        return start <= clock < end
    return clock >= start or clock < end


def shift_for_quiet_hours(due_utc: datetime, tz: str, start: time, end: time) -> datetime:
    """Return due_utc, or the next end of the quiet window if due_utc falls inside it."""
    zone = ZoneInfo(tz)
    local = due_utc.astimezone(zone)
    clock = local.replace(tzinfo=None).time()
    if not _in_quiet(clock, start, end):
        return due_utc
    day: date = local.date()
    if start >= end and clock >= start:
        # The window crosses midnight and we are in its evening part.
        day += timedelta(days=1)
    shifted = datetime.combine(day, end, tzinfo=zone).astimezone(UTC)
    return shifted if shifted > due_utc else due_utc + timedelta(minutes=1)


def next_slot_after_limit(
    sent_times_utc: Sequence[datetime], limit: int, due_utc: datetime, now: datetime
) -> datetime | None:
    """Rolling seven-day limit: None if due_utc may be sent, else when the oldest send expires."""
    moment = max(due_utc, now)
    window = sorted(sent for sent in sent_times_utc if moment - WINDOW < sent <= moment)
    if len(window) < limit:
        return None
    if limit <= 0:
        return None
    return window[len(window) - limit] + WINDOW


@dataclass(frozen=True)
class JourneyStep:
    day: int
    title: str
    url: str | None
    material_id: str | None
    task: str
    reminder_id: UUID | None = None
    due_utc: datetime | None = None
    done: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "title": self.title,
            "url": self.url,
            "material_id": self.material_id,
            "task": self.task,
            "reminder_id": str(self.reminder_id) if self.reminder_id else None,
            "due_utc": self.due_utc.isoformat() if self.due_utc else None,
            "done": self.done,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> JourneyStep:
        return cls(
            day=int(data["day"]),
            title=data.get("title") or "",
            url=data.get("url"),
            material_id=data.get("material_id"),
            task=data.get("task") or "",
            reminder_id=UUID(data["reminder_id"]) if data.get("reminder_id") else None,
            due_utc=datetime.fromisoformat(data["due_utc"]) if data.get("due_utc") else None,
            done=bool(data.get("done")),
        )

    def mark_done(self) -> JourneyStep:
        return replace(self, done=True)


@dataclass(frozen=True)
class Journey:
    id: UUID
    owner_id: int
    goal: str = field(repr=False)
    timezone: str
    local_time: time
    status: str
    steps: tuple[JourneyStep, ...]
    created_at: datetime | None = None

    @property
    def done_days(self) -> int:
        return sum(step.done for step in self.steps)


ACTION_PHRASES = (
    "познакомься с материалом и выпиши главную идею",
    "посмотри материал и отметь, что применимо к твоему делу",
    "выбери одно действие из материала и запланируй его",
    "сделай первый небольшой шаг по материалу",
    "закрепи: повтори ключевые мысли и дополни свой план",
    "примени материал на практике к своей задаче",
    "подведи итоги недели и реши, что делать дальше",
)


def fallback_steps(goal: str, cards: Sequence[Any]) -> list[dict[str, Any]]:
    """Seven plain steps over the given free materials; never invents links."""
    del goal  # the phrases are goal-neutral; the goal is stored with the journey itself
    if not cards:
        return []
    steps = []
    for index, phrase in enumerate(ACTION_PHRASES):
        card = cards[index % len(cards)]
        steps.append(
            {
                "title": card.title,
                "url": card.url,
                "material_id": card.id,
                "task": phrase,
            }
        )
    return steps
