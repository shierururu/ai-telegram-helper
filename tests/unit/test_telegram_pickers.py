from datetime import date
from uuid import uuid4
from zoneinfo import ZoneInfo

from alpha_course.telegram.pickers import (
    CITY_ZONES,
    MAX_PICKER_YEAR,
    MINUTE_PAGE_COUNT,
    calendar_keyboard,
    hour_keyboard,
    minute_keyboard,
    timezone_keyboard,
)


def test_calendar_aligns_leap_day_and_disables_past_days():
    today = date(2028, 2, 28)
    rows = calendar_keyboard("key", today, today).inline_keyboard

    assert [button.text for button in rows[0]] == ["·", "Февраль 2028", "›"]
    assert rows[0][2].callback_data == "r:m:key:202803"
    assert [button.text for button in rows[1]] == ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    assert all(button.callback_data == "r:i:key" for week in rows[2:-1] for button in week)
    assert [button.callback_data for button in rows[-1][:2]] == [
        "r:y:key:20280228",
        "r:y:key:20280229",
    ]
    assert all(button.callback_data == "r:i:key" for button in rows[-1][2:])


def test_calendar_navigation_crosses_year_but_stops_at_limit():
    today = date(MAX_PICKER_YEAR - 1, 12, 1)
    january = calendar_keyboard("key", date(MAX_PICKER_YEAR, 1, 1), today)
    december = calendar_keyboard("key", date(MAX_PICKER_YEAR, 12, 1), today)

    assert january.inline_keyboard[0][0].callback_data == f"r:m:key:{MAX_PICKER_YEAR - 1}12"
    assert december.inline_keyboard[0][2].callback_data == "r:i:key"


def test_time_picker_exposes_every_hour_and_minute():
    hours = hour_keyboard("key").inline_keyboard[:-1]
    assert [button.callback_data for row in hours for button in row] == [
        f"r:h:key:{hour:02d}" for hour in range(24)
    ]

    pages = [minute_keyboard("key", page).inline_keyboard for page in range(MINUTE_PAGE_COUNT)]
    assert [button.callback_data for rows in pages for row in rows[:-2] for button in row] == [
        f"r:n:key:{minute:02d}" for minute in range(60)
    ]
    for page, rows in enumerate(pages):
        assert [button.callback_data for button in rows[-2]] == [
            "r:i:key" if index == page else f"r:p:key:{index}" for index in range(MINUTE_PAGE_COUNT)
        ]


def test_cities_map_to_timezones_and_callbacks_fit_telegram_limit():
    key = uuid4().hex
    cities = timezone_keyboard(key)
    assert [
        (button.text, button.callback_data) for row in cities.inline_keyboard for button in row
    ] == [(city, f"r:z:{key}:{index}") for index, city in enumerate(CITY_ZONES)]
    assert all(ZoneInfo(zone).key == zone for zone in CITY_ZONES.values())

    today = date(2028, 2, 1)
    keyboards = [cities, calendar_keyboard(key, today, today), hour_keyboard(key)]
    keyboards.extend(minute_keyboard(key, page) for page in range(MINUTE_PAGE_COUNT))
    assert all(
        1 <= len(button.callback_data.encode()) <= 64
        for markup in keyboards
        for row in markup.inline_keyboard
        for button in row
    )
