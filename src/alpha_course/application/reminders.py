"""Calendar rules and outcomes for confirmed reminders."""

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from alpha_course.domain.reminders import ReminderFrequency, require_timezone


class ReminderError(ValueError):
    """A validation or management error safe for display."""


class NotFound(ReminderError):
    pass


class StaleReminder(ReminderError):
    pass


class AmbiguousTime(ReminderError):
    def __init__(self, options: tuple[datetime, ...]) -> None:
        super().__init__("Это местное время встречается дважды. Выбери смещение UTC.")
        self.options = options


def local_options(local_at: datetime, zone: ZoneInfo) -> tuple[datetime, ...]:
    options = []
    seen = set()
    for fold in (0, 1):
        candidate = local_at.replace(tzinfo=zone, fold=fold)
        utc = candidate.astimezone(UTC)
        if utc not in seen and utc.astimezone(zone).replace(tzinfo=None) == local_at:
            options.append(candidate)
            seen.add(utc)
    return tuple(options)


@dataclass(frozen=True)
class Schedule:
    event_text: str = field(repr=False)
    local_at: datetime
    timezone: str
    frequency: ReminderFrequency
    fold: int | None = None
    # Original day of month for monthly/yearly repeats; keeps 31 after a short month.
    anchor_day: int | None = None

    @property
    def day_of_month(self) -> int:
        return self.anchor_day or self.local_at.day

    def first_due(self) -> datetime:
        if not 1 <= len(self.event_text.strip()) <= 1000:
            raise ReminderError("Укажи событие длиной от 1 до 1000 символов.")
        if self.local_at.tzinfo is not None:
            raise ReminderError("Укажи местное время отдельно от часового пояса.")
        if self.frequency not in tuple(ReminderFrequency):
            raise ReminderError("Выбери повторение: разово, каждый день, неделю, месяц или год.")
        try:
            require_timezone(self.timezone)
            options = local_options(self.local_at, ZoneInfo(self.timezone))
        except ValueError, OverflowError:
            raise ReminderError("Проверь дату и часовой пояс IANA.") from None
        if not options:
            raise ReminderError("Такого местного времени в этот день нет. Выбери другое время.")
        if self.fold is None and len(options) > 1:
            raise AmbiguousTime(options)
        if self.fold is not None and (
            type(self.fold) is not int or not 0 <= self.fold < len(options)
        ):
            raise ReminderError("Выбери действительное смещение UTC.")
        return options[self.fold or 0].astimezone(UTC)


def _clamped(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, monthrange(year, month)[1]))


def next_due(schedule: Schedule, current_due: datetime, after: datetime) -> datetime | None:
    if schedule.frequency == ReminderFrequency.ONCE:
        return None
    zone = ZoneInfo(schedule.timezone)
    clock = schedule.local_at.time()
    first_day = current_due.astimezone(zone).date()
    if schedule.frequency in (ReminderFrequency.MONTHLY, ReminderFrequency.YEARLY):
        return _next_calendar_due(schedule, zone, first_day, after)
    step = 7 if schedule.frequency == ReminderFrequency.WEEKLY else 1
    # Skip whole calendar periods. Recheck the day before the cutoff to allow
    # offset changes at midnight, then resolve each candidate in local time.
    day_span = (after.astimezone(zone).date() - first_day).days
    days = max(1, (day_span - 1) // step) * step
    while True:
        day = first_day + timedelta(days=days)
        options = local_options(datetime.combine(day, clock), zone)
        if options:
            due = options[0].astimezone(UTC)
            if due > after:
                return due
        days += step


def _next_calendar_due(
    schedule: Schedule, zone: ZoneInfo, first_day: date, after: datetime
) -> datetime | None:
    yearly = schedule.frequency == ReminderFrequency.YEARLY
    anchor = schedule.day_of_month
    clock = schedule.local_at.time()
    # Month index counted from year zero; yearly steps twelve months at a time.
    step = 12 if yearly else 1
    index = first_day.year * 12 + first_day.month - 1
    # Start no later than the period before the cutoff to tolerate offset changes.
    cutoff = after.astimezone(zone).date()
    skip = (cutoff.year * 12 + cutoff.month - 1 - index - 1) // step
    index += max(1, skip) * step
    while index < 9999 * 12:
        year, month = divmod(index, 12)
        day = _clamped(year, month + 1, anchor)
        options = local_options(datetime.combine(day, clock), zone)
        if options:
            due = options[0].astimezone(UTC)
            if due > after:
                return due
        index += step
    return None


class DeliveryStatus(StrEnum):
    SENT = "sent"
    FAILED = "failed"
    RETRY = "retry"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DeliveryResult:
    status: DeliveryStatus
    message_id: int | None = None
    retry_after: int = 0
