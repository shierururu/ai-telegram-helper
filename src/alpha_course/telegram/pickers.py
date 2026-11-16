"""Inline keyboards for choosing a reminder's local date, time and zone."""

from calendar import Calendar
from datetime import date, datetime, time, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

MAX_PICKER_YEAR = 2099
MINUTE_PAGE_SIZE = 20
MINUTE_PAGE_COUNT = 60 // MINUTE_PAGE_SIZE
CITY_ZONES = {
    "Москва": "Europe/Moscow",
    "Екатеринбург": "Asia/Yekaterinburg",
    "Калининград": "Europe/Kaliningrad",
    "Самара": "Europe/Samara",
    "Омск": "Asia/Omsk",
    "Новосибирск": "Asia/Novosibirsk",
    "Иркутск": "Asia/Irkutsk",
    "Якутск": "Asia/Yakutsk",
    "Владивосток": "Asia/Vladivostok",
    "Магадан": "Asia/Magadan",
    "Петропавловск-Камчатский": "Asia/Kamchatka",
}
MONTHS = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)
WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def _button(label: str, action: str, key: str, value: str = "") -> InlineKeyboardButton:
    suffix = f":{value}" if value else ""
    return InlineKeyboardButton(text=label, callback_data=f"r:{action}:{key}{suffix}")


def timezone_keyboard(key: str, suggested: str | None = None) -> InlineKeyboardMarkup:
    buttons = [_button(city, "z", key, str(index)) for index, city in enumerate(CITY_ZONES)]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    if suggested in CITY_ZONES.values():
        button = buttons[tuple(CITY_ZONES.values()).index(suggested)]
        rows.insert(0, [button.model_copy(update={"text": f"Как в прошлый раз · {button.text}"})])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def date_keyboard(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("Сегодня", "q", key, "0"), _button("Завтра", "q", key, "1")],
            [_button("Календарь", "m", key)],
        ]
    )


def time_keyboard(key: str, day: date, now: datetime) -> InlineKeyboardMarkup:
    choices = [
        _button(f"{hour:02d}:00", "s", key, str(hour))
        for hour in (9, 12, 15, 18, 21)
        if (day, time(hour)) > (now.date(), now.time())
    ]
    rows = [choices[index : index + 3] for index in range(0, len(choices), 3)]
    rows.append([_button("Другое время", "a", key), _button("‹ Дата", "t", key)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def calendar_keyboard(key: str, month: date, today: date) -> InlineKeyboardMarkup:
    month = month.replace(day=1)
    previous = (month - timedelta(days=1)).replace(day=1)
    following = (month + timedelta(days=31)).replace(day=1)
    previous_button = _button("‹", "m", key, previous.strftime("%Y%m"))
    next_button = _button("›", "m", key, following.strftime("%Y%m"))
    if previous < today.replace(day=1):
        previous_button = _button("·", "i", key)
    if following.year > MAX_PICKER_YEAR:
        next_button = _button("·", "i", key)
    title = f"{MONTHS[month.month - 1]} {month.year}"
    rows = [
        [previous_button, _button(title, "i", key), next_button],
        [_button(weekday, "i", key) for weekday in WEEKDAYS],
    ]
    for week in Calendar().monthdatescalendar(month.year, month.month):
        rows.append(
            [
                _button(str(day.day), "y", key, day.strftime("%Y%m%d"))
                if day.month == month.month and day >= today
                else _button("·", "i", key)
                for day in week
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def hour_keyboard(key: str) -> InlineKeyboardMarkup:
    rows = [
        [_button(f"{hour:02d}", "h", key, f"{hour:02d}") for hour in range(start, start + 6)]
        for start in range(0, 24, 6)
    ]
    rows.append([_button("‹ Дата", "t", key)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def minute_keyboard(key: str, page: int = 0) -> InlineKeyboardMarkup:
    start_minute = page * MINUTE_PAGE_SIZE
    rows = [
        [_button(f"{minute:02d}", "n", key, f"{minute:02d}") for minute in range(start, start + 5)]
        for start in range(start_minute, start_minute + MINUTE_PAGE_SIZE, 5)
    ]
    ranges = [
        f"{start:02d}–{start + MINUTE_PAGE_SIZE - 1:02d}"
        for start in range(0, 60, MINUTE_PAGE_SIZE)
    ]
    rows.append(
        [
            _button(f"• {label}", "i", key)
            if index == page
            else _button(label, "p", key, str(index))
            for index, label in enumerate(ranges)
        ]
    )
    rows.append([_button("‹ Часы", "b", key)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
