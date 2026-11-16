"""Schedule records only; no scheduling or delivery behavior is implemented."""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("A timezone-aware UTC datetime is required")


def require_timezone(value: str) -> None:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError, ValueError:
        raise ValueError("A valid IANA timezone is required") from None


class ReminderFrequency(StrEnum):
    ONCE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


class ReminderStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    FINISHED = "finished"


class OccurrenceStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Profile:
    user_id: str
    business_stage: str | None = None
    sector: str | None = None
    task: str | None = None
    timezone: str | None = None
    field_provenance: tuple[tuple[str, str], ...] = ()
    revision: int = 1
    depth: str | None = None

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValueError("Profile revision must be positive")
        if self.timezone is not None:
            require_timezone(self.timezone)


@dataclass(frozen=True)
class Reminder:
    id: str
    owner_id: str
    event_text: str = field(repr=False)
    frequency: ReminderFrequency
    local_time: time
    timezone: str
    next_due_utc: datetime
    confirmed_at_utc: datetime
    status: ReminderStatus = ReminderStatus.ACTIVE
    version: int = 1
    weekday: int | None = None
    month_day: int | None = None

    def __post_init__(self) -> None:
        require_timezone(self.timezone)
        require_utc(self.next_due_utc)
        require_utc(self.confirmed_at_utc)
        if self.local_time.tzinfo is not None:
            raise ValueError("Local schedule time must be naive; timezone is stored separately")
        if self.version < 1:
            raise ValueError("Schedule version must be positive")
        if self.frequency == ReminderFrequency.WEEKLY:
            if type(self.weekday) is not int or not 0 <= self.weekday <= 6:
                raise ValueError("Weekly schedule requires weekday from 0 to 6")
        elif self.weekday is not None:
            raise ValueError("Weekday is only permitted for weekly schedules")
        if self.frequency in (ReminderFrequency.MONTHLY, ReminderFrequency.YEARLY):
            if type(self.month_day) is not int or not 1 <= self.month_day <= 31:
                raise ValueError("Monthly and yearly schedules require a day from 1 to 31")
        elif self.month_day is not None:
            raise ValueError("Month day is only permitted for monthly and yearly schedules")
        if self.next_due_utc <= self.confirmed_at_utc:
            raise ValueError("Confirmed schedule must have a future first occurrence")


@dataclass(frozen=True)
class Occurrence:
    reminder_id: str
    schedule_version: int
    due_utc: datetime
    status: OccurrenceStatus = OccurrenceStatus.PENDING
    lease_until_utc: datetime | None = None
    lease_token: str | None = field(default=None, repr=False)
    attempt_id: str | None = None
    message_id: str | None = None

    def __post_init__(self) -> None:
        require_utc(self.due_utc)
        if self.lease_until_utc is not None:
            require_utc(self.lease_until_utc)
        if self.schedule_version < 1:
            raise ValueError("Schedule version must be positive")

    @property
    def key(self) -> tuple[str, int, datetime]:
        return self.reminder_id, self.schedule_version, self.due_utc
