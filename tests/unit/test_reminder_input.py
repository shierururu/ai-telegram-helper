"""Natural language reminder drafts and the external context boundary."""

from datetime import UTC, date, datetime, time

import pytest

from alpha_course.application.reminder_input import parse_reminder_input, resolve_reminder_day
from alpha_course.domain.reminders import ReminderFrequency

NOW = datetime(2026, 11, 9, 22, tzinfo=UTC)
MOSCOW = "Europe/Moscow"


@pytest.mark.parametrize(
    ("text", "event", "day", "hour", "frequency"),
    [
        ("Завтра в 15:30 напомни отправить счёт", "отправить счёт", 11, time(15, 30), "once"),
        ("В пятницу в 9 напомни позвонить", "позвонить", 13, time(9), "once"),
        ("Напомни оплатить услуги каждый день в 9", "оплатить услуги", 10, time(9), "daily"),
        ("По пятницам в 10 напомни проверить план", "проверить план", 13, time(10), "weekly"),
        ("Напомни 12.11.2026 в 12:45", "", 12, time(12, 45), "once"),
        ("Напомни проверить почту завтра в 9", "проверить почту", 11, time(9), "once"),
        ("Завтра в девять вечера напомни про звонок", "про звонок", 11, time(21), "once"),
        (
            "В пятницу в половине шестого утра напомни про поезд",
            "про поезд",
            13,
            time(5, 30),
            "once",
        ),
    ],
)
async def test_parses_reminder_slots(text, event, day, hour, frequency) -> None:
    draft = await parse_reminder_input(text, now=NOW, timezone=MOSCOW)
    assert draft is not None
    assert draft.event == event
    assert draft.day == date(2026, 11, day)
    assert draft.local_time == hour
    assert draft.frequency is ReminderFrequency(frequency)


async def test_relative_day_waits_for_timezone() -> None:
    draft = await parse_reminder_input("Завтра в 15:30 напомни проверить план", now=NOW)
    assert draft is not None
    assert draft.timezone is None
    assert draft.day is None
    assert draft.pending_day == "завтра"
    assert resolve_reminder_day(draft.pending_day, now=NOW, timezone=MOSCOW) == date(2026, 11, 11)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Каждый вторник в 0 напомни проверить план", date(2026, 11, 17)),
        ("Ежедневно в 0 напомни проверить план", date(2026, 11, 11)),
        ("Сегодня в 0 напомни проверить план", None),
    ],
)
async def test_timezone_selection_resolves_the_next_occurrence(text, expected) -> None:
    draft = await parse_reminder_input(text, now=NOW)
    assert draft is not None
    assert draft.day is None
    assert draft.local_time == time(0)
    assert (
        resolve_reminder_day(
            draft.pending_day, now=NOW, timezone=MOSCOW, local_time=draft.local_time
        )
        == expected
    )


async def test_invalid_schedule_does_not_become_confirmable() -> None:
    draft = await parse_reminder_input("31.02.2026 в 28:74 напомни сверить счета", now=NOW)
    assert draft is not None
    assert draft.event == "сверить счета"
    assert draft.local_at is None
    assert await parse_reminder_input("Как сверить счета?", now=NOW) is None
    assert await parse_reminder_input("Как работает команда «напомни»?", now=NOW) is None


class FakeModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.payloads: list[str] = []

    async def complete(self, instruction: str, payload: str) -> str:
        self.payloads.append(payload)
        return self.response


@pytest.mark.parametrize(
    "response",
    [
        '{"hour": 21, "minute": 0}',
        '{"hour": 21, "minute": false}',
        '{"hour": 21, "minute": 0, "date": "2026-11-12"}',
        '{"hour": 22, "minute": 0}',
        "not JSON",
    ],
)
async def test_model_sees_only_allowlisted_timing_and_output_is_checked(response: str) -> None:
    model = FakeModel(response)
    draft = await parse_reminder_input(
        "Завтра в девять вечера напомни написать Анне anna@example.test",
        now=NOW,
        timezone=MOSCOW,
        model=model,
    )
    assert draft is not None
    assert draft.event == "написать Анне anna@example.test"
    assert draft.local_time == time(21)
    assert model.payloads == ["в девять вечера"]


async def test_numeric_time_is_sent_to_model_with_deterministic_fallback() -> None:
    model = FakeModel('{"hour": 16, "minute": 30}')
    draft = await parse_reminder_input(
        "Завтра в 15:30 напомни отправить счёт", now=NOW, timezone=MOSCOW, model=model
    )
    assert draft is not None
    assert draft.local_time == time(15, 30)
    assert model.payloads == ["в 15:30"]
