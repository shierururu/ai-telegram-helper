"""Parse Russian reminder requests into unconfirmed drafts."""

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from alpha_course.domain.reminders import ReminderFrequency

_INTENT = re.compile(r"(?i)\b(?:напомни(?:те)?(?:\s+мне)?|поставь(?:те)?\s+напоминание)\b")
_WEEKDAYS = {
    "понедельник": 0,
    "вторник": 1,
    "среду": 2,
    "четверг": 3,
    "пятницу": 4,
    "субботу": 5,
    "воскресенье": 6,
}
_RELATIVE = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
_WEEKDAY = "|".join(_WEEKDAYS)
_DATE = r"\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4}"
_DAY = re.compile(rf"\b(?:{'|'.join(_RELATIVE)}|{_WEEKDAY}|{_DATE})\b")
_DAILY = r"ежедневно|каждый день"
_WEEKLY = (
    r"еженедельно|каждую неделю|по (?:понедельникам|вторникам|средам|"
    rf"четвергам|пятницам|субботам|воскресеньям)|кажд(?:ый|ую|ое) (?:{_WEEKDAY})"
)
_REPEAT = re.compile(rf"\b(?:{_DAILY}|{_WEEKLY})\b")
_PLURAL_WEEKDAYS = dict(
    zip(
        (
            "понедельникам",
            "вторникам",
            "средам",
            "четвергам",
            "пятницам",
            "субботам",
            "воскресеньям",
        ),
        _WEEKDAYS,
        strict=True,
    )
)
_TIME = re.compile(r"(?<![\w.:+-])(?:(?:в|к)\s+)?(\d{1,2}):(\d{2})(?![\w.:+-])")
_HOUR = re.compile(r"\b(?:в|к)\s+(\d{1,2})(?![\w.:+-])")
_WRITTEN_HOURS = {
    "час": 1,
    "два": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
}
_WRITTEN_TIME = re.compile(rf"\bв (?:{'|'.join(_WRITTEN_HOURS)}) (?:утра|дня|вечера|ночи)\b")
_ORDINAL_HOURS = {
    "первого": 1,
    "второго": 2,
    "третьего": 3,
    "четвёртого": 4,
    "пятого": 5,
    "шестого": 6,
    "седьмого": 7,
    "восьмого": 8,
    "девятого": 9,
    "десятого": 10,
    "одиннадцатого": 11,
    "двенадцатого": 12,
}
_HALF_HOUR = re.compile(rf"\bв половине (?:{'|'.join(_ORDINAL_HOURS)}) (?:утра|дня|вечера|ночи)\b")
_ZONE = re.compile(r"\b(?:по москве|по московскому времени|europe/moscow)\b")
_SCHEDULE_PART = (
    rf"(?:{_REPEAT.pattern}|{_WRITTEN_TIME.pattern}|{_HALF_HOUR.pattern}|"
    rf"{_TIME.pattern}|{_HOUR.pattern}|"
    rf"(?:\bв\s+)?{_DAY.pattern}|{_ZONE.pattern})"
)
_SCHEDULE = re.compile(rf"{_SCHEDULE_PART}(?:[\s,;]+{_SCHEDULE_PART})*")


class ReminderInputModel(Protocol):
    async def complete(self, instruction: str, payload: str) -> str: ...


@dataclass(frozen=True)
class ReminderInput:
    event: str = field(repr=False)
    day: date | None
    local_time: time | None
    frequency: ReminderFrequency
    pending_day: str | None
    timezone: str | None

    @property
    def local_at(self) -> datetime | None:
        if self.day is None or self.local_time is None:
            return None
        return datetime.combine(self.day, self.local_time)


def resolve_reminder_day(
    pending_day: str | None, *, now: datetime, timezone: str | None, local_time: time | None = None
) -> date | None:
    if pending_day is None:
        return None
    local_now = now.astimezone(ZoneInfo(timezone)).replace(tzinfo=None) if timezone else None
    day = _resolve_day(pending_day, local_now.date() if local_now else None)
    if day and local_now and local_time and datetime.combine(day, local_time) <= local_now:
        if pending_day in _WEEKDAYS:
            return day + timedelta(days=7)
        if pending_day == "daily":
            return day + timedelta(days=1)
        return None
    return day


def _resolve_day(pending_day: str, today: date | None) -> date | None:
    try:
        if "." in pending_day:
            return datetime.strptime(pending_day, "%d.%m.%Y").date()
        if "-" in pending_day:
            return date.fromisoformat(pending_day)
    except ValueError:
        return None
    if today is None:
        return None
    if pending_day == "daily":
        return today
    if pending_day in _RELATIVE:
        return today + timedelta(days=_RELATIVE[pending_day])
    if pending_day in _WEEKDAYS:
        return today + timedelta(days=(_WEEKDAYS[pending_day] - today.weekday()) % 7)
    return None


def _normalize_time(value: str) -> time | None:
    if match := (_TIME.fullmatch(value) or _HOUR.fullmatch(value)):
        try:
            return time(int(match[1]), int(match[2]) if match.lastindex == 2 else 0)
        except ValueError:
            return None
    *_, hour_word, period = value.split()
    half_hour = _HALF_HOUR.fullmatch(value)
    hour = (_ORDINAL_HOURS if half_hour else _WRITTEN_HOURS)[hour_word]
    if period in {"дня", "вечера"} and hour < 12:
        hour += 12
    elif period in {"утра", "ночи"} and hour == 12:
        hour = 0
    return time((hour - 1) % 24, 30) if half_hour else time(hour)


async def _assisted_time(value: str, model: ReminderInputModel | None) -> time | None:
    value = " ".join(value.split())
    # Only full matches of the time grammar reach the provider.
    if not any(pattern.fullmatch(value) for pattern in (_TIME, _HOUR, _WRITTEN_TIME, _HALF_HOUR)):
        return None
    expected = _normalize_time(value)
    if model is None or expected is None:
        return expected
    try:
        result = json.loads(
            await model.complete(
                "Преобразуй указанное время в 24-часовой формат. Верни только JSON "
                '{"hour": целое число от 0 до 23, "minute": целое число от 0 до 59}.',
                value,
            )
        )
    except RuntimeError, TypeError, ValueError:
        return expected
    if (
        isinstance(result, dict)
        and result.keys() == {"hour", "minute"}
        and type(result["hour"]) is int
        and type(result["minute"]) is int
        and result["hour"] == expected.hour
        and result["minute"] == expected.minute
    ):
        return time(result["hour"], result["minute"])
    return expected


async def parse_reminder_input(
    text: str,
    *,
    now: datetime,
    timezone: str | None = None,
    model: ReminderInputModel | None = None,
) -> ReminderInput | None:
    if not (intent := _INTENT.search(text)):
        return None
    before = text[: intent.start()].strip(" ,:;—-")
    before = before.lower().removeprefix("пожалуйста").strip(" ,:;—-")
    if before and not _SCHEDULE.fullmatch(before):
        return None
    after = text[intent.end() :].strip(" ,:;—-")
    # Pull a leading schedule out of the event without sending any event words off-device.
    prefix = _SCHEDULE.match(after.lower())
    timing = " ".join((before, after[: prefix.end()] if prefix else "")).lower()
    event = (after[prefix.end() :] if prefix else after).strip(" ,:;—-")
    event_end = len(event.rstrip(" .!?,;"))
    for suffix in _SCHEDULE.finditer(event[:event_end].lower()):
        if suffix.end() == event_end and suffix.start() > 0:
            event = event[: suffix.start()].rstrip(" ,;")
            timing += f" {suffix[0]}"
            break
    zone = "Europe/Moscow" if _ZONE.search(timing) else timezone
    repeat = _REPEAT.search(timing)
    frequency = ReminderFrequency.ONCE
    if repeat:
        frequency = (
            ReminderFrequency.DAILY if re.fullmatch(_DAILY, repeat[0]) else ReminderFrequency.WEEKLY
        )
    timing_without_repeat = _REPEAT.sub("", timing)
    day_match = _DAY.search(timing_without_repeat)
    pending_day = day_match[0] if day_match else None
    if pending_day is None and repeat and frequency is ReminderFrequency.WEEKLY:
        token = repeat[0].split()[-1]
        pending_day = _PLURAL_WEEKDAYS.get(token, token if token in _WEEKDAYS else None)
    if pending_day is None and frequency is ReminderFrequency.DAILY:
        pending_day = "daily"
    local_time = None
    if match := (
        _TIME.search(timing)
        or _HOUR.search(timing)
        or _WRITTEN_TIME.search(timing)
        or _HALF_HOUR.search(timing)
    ):
        local_time = await _assisted_time(match[0], model)
    day = resolve_reminder_day(pending_day, now=now, timezone=zone, local_time=local_time)
    return ReminderInput(event, day, local_time, frequency, pending_day, zone)
