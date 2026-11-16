"""City and time understanding for reminders (rules first, model as fallback)."""

import json
from datetime import UTC, datetime, time

import pytest

from alpha_course.application.schedule_ai import parse_when, resolve_timezone, zone_by_rules
from alpha_course.domain.reminders import ReminderFrequency
from alpha_course.telegram.router import _normalize_time

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)  # 15:00 in Moscow, a Tuesday


class FakeModel:
    def __init__(self, reply: dict | str | None = None) -> None:
        self.reply = reply
        self.payloads: list[str] = []

    async def complete(self, instruction: str, payload: str, **kwargs) -> str:
        self.payloads.append(payload)
        if self.reply is None:
            raise RuntimeError("model down")
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


@pytest.mark.parametrize(
    ("text", "zone"),
    [
        ("Москва", "Europe/Moscow"),
        ("я из Казани", "Europe/Moscow"),
        ("по екатеринбургу", "Asia/Yekaterinburg"),
        ("Новосибирск", "Asia/Novosibirsk"),
        ("владивосток", "Asia/Vladivostok"),
        ("Калининград", "Europe/Kaliningrad"),
        ("Самара", "Europe/Samara"),
    ],
)
def test_zone_by_rules(text: str, zone: str) -> None:
    assert zone_by_rules(text) == zone


def test_zone_by_rules_unknown() -> None:
    assert zone_by_rules("Урюпинск") is None


async def test_resolve_timezone_uses_rules_without_model() -> None:
    model = FakeModel({"timezone": "Asia/Tokyo"})
    assert await resolve_timezone("Омск", model) == "Asia/Omsk"
    assert model.payloads == []


async def test_resolve_timezone_asks_model_for_unknown_city_and_validates() -> None:
    assert await resolve_timezone("Урюпинск", FakeModel({"timezone": "Europe/Volgograd"})) == (
        "Europe/Volgograd"
    )
    assert await resolve_timezone("Урюпинск", FakeModel({"timezone": "Mars/Olympus"})) is None
    assert await resolve_timezone("Урюпинск", FakeModel({"timezone": None})) is None
    assert await resolve_timezone("Урюпинск", FakeModel(None)) is None
    assert await resolve_timezone("Урюпинск", None) is None


async def test_resolve_timezone_skips_model_for_time_phrases() -> None:
    model = FakeModel({"timezone": "Europe/Moscow"})
    assert await resolve_timezone("завтра в 15:00", model) is None
    assert model.payloads == []


async def test_parse_when_rules_cover_simple_phrases() -> None:
    when = await parse_when("завтра в 18:00", now=NOW, timezone="Europe/Moscow", model=None)
    assert when is not None and when.complete
    assert when.local_time == time(18, 0)
    assert when.frequency is ReminderFrequency.ONCE


async def test_parse_when_weekly_by_rules() -> None:
    when = await parse_when(
        "каждый понедельник в 9:00", now=NOW, timezone="Europe/Moscow", model=None
    )
    assert when is not None and when.frequency is ReminderFrequency.WEEKLY
    assert when.local_time == time(9, 0)


async def test_parse_when_model_handles_relative_time() -> None:
    model = FakeModel({"date": "2026-11-10", "time": "17:00", "frequency": "once", "place": None})
    when = await parse_when("через 2 часа", now=NOW, timezone="Europe/Moscow", model=model)
    assert when is not None
    assert (when.day.isoformat(), when.local_time) == ("2026-11-10", time(17, 0))
    sent = json.loads(model.payloads[0])
    assert sent["now_local"] == "2026-11-10T15:00" and sent["weekday"] == "вторник"


async def test_parse_when_model_monthly_and_city() -> None:
    model = FakeModel(
        {"date": "2026-11-15", "time": "10:00", "frequency": "monthly", "place": "Омск"}
    )
    when = await parse_when("15 числа каждого месяца в 10", now=NOW, timezone=None, model=model)
    assert when is not None and when.frequency is ReminderFrequency.MONTHLY
    assert when.timezone == "Asia/Omsk"


async def test_parse_when_rejects_past_model_date_and_survives_failures() -> None:
    past = FakeModel({"date": "2026-11-01", "time": "10:00", "frequency": "once"})
    assert await parse_when("через час", now=NOW, timezone="Europe/Moscow", model=past) is None
    assert (
        await parse_when("через час", now=NOW, timezone="Europe/Moscow", model=FakeModel("x"))
        is None
    )
    assert (
        await parse_when("через час", now=NOW, timezone="Europe/Moscow", model=FakeModel(None))
        is None
    )


async def test_parse_when_masks_personal_details_before_the_model() -> None:
    model = FakeModel({"date": "2026-11-11", "time": "10:00", "frequency": "once"})
    await parse_when(
        "завтра утром, звонить +7 916 123-45-67", now=NOW, timezone="Europe/Moscow", model=model
    )
    assert "916" not in model.payloads[0]


@pytest.mark.parametrize(
    ("raw", "fixed"),
    [
        ("завтра в 15 00 позвонить", "завтра в 15:00 позвонить"),
        ("напомни в 9.30 про встречу", "напомни в 9:30 про встречу"),
        ("в 15:30 уже норм", "в 15:30 уже норм"),
        ("27.11 в 10 заплатить", "27.11 в 10 заплатить"),
    ],
)
def test_normalize_time(raw: str, fixed: str) -> None:
    assert _normalize_time(raw) == fixed
