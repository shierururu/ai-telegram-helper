"""Conversation flow for reminder drafts and owner-scoped management."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from html import escape
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from alpha_course.application.reminder_input import ReminderInput, resolve_reminder_day
from alpha_course.application.reminders import AmbiguousTime, ReminderError, Schedule
from alpha_course.domain.reminders import ReminderFrequency
from alpha_course.infrastructure.models import ReminderRecord
from alpha_course.telegram.pickers import (
    CITY_ZONES,
    MAX_PICKER_YEAR,
    MINUTE_PAGE_COUNT,
    calendar_keyboard,
    date_keyboard,
    hour_keyboard,
    minute_keyboard,
    time_keyboard,
)

FREQUENCIES = {
    ReminderFrequency.ONCE: "Один раз",
    ReminderFrequency.DAILY: "Каждый день",
    ReminderFrequency.WEEKLY: "Каждую неделю",
    ReminderFrequency.MONTHLY: "Каждый месяц",
    ReminderFrequency.YEARLY: "Каждый год",
}
MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
ZONE_CITIES = {zone: city for city, zone in CITY_ZONES.items()}


def keyboard(*buttons: tuple[str, str]) -> InlineKeyboardMarkup:
    """One button per row."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=action)] for label, action in buttons
        ]
    )


def keyboard_rows(*rows: tuple[tuple[str, str], ...]) -> InlineKeyboardMarkup:
    """Several buttons per row: keyboard_rows(((label, data), (label, data)), ...)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=action) for label, action in row]
            for row in rows
            if row
        ]
    )


@dataclass
class Draft:
    event: str = field(default="", repr=False)
    local_at: datetime | None = None
    timezone: str = ""
    suggested_timezone: str | None = None
    calendar_month: date | None = None
    selected_day: date | None = None
    entered_time: time | None = None
    pending_day: str | None = None
    choose_hour: bool = False
    choose_frequency: bool = False
    awaiting_time: bool = False
    selected_hour: int | None = None
    minute_page: int = 0
    frequency: ReminderFrequency = ReminderFrequency.ONCE
    fold: int | None = None
    id: UUID = field(default_factory=uuid4)
    action_key: UUID = field(default_factory=uuid4)
    expires: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(minutes=30))
    target: UUID | None = None
    version: int = 0
    link_url: str | None = None
    link_title: str | None = None

    def reset_time(self) -> None:
        self.local_at, self.fold = None, None
        self.selected_day, self.selected_hour = None, None
        self.entered_time, self.pending_day = None, None
        self.choose_hour, self.choose_frequency = False, False
        self.awaiting_time = False
        self.calendar_month, self.minute_page = None, 0
        self.action_key = uuid4()

    @property
    def today(self) -> date:
        return datetime.now(ZoneInfo(self.timezone)).date()

    @property
    def key(self) -> str:
        return self.action_key.hex

    @property
    def schedule(self) -> Schedule:
        assert self.local_at is not None
        return Schedule(
            self.event,
            self.local_at,
            self.timezone,
            self.frequency,
            self.fold,
        )

    def apply_input(self, parsed: ReminderInput) -> None:
        if parsed.event:
            self.event = parsed.event
        self.timezone = parsed.timezone or self.timezone
        self.selected_day = parsed.day or self.selected_day
        if parsed.day or parsed.pending_day:
            self.pending_day = None if parsed.day else parsed.pending_day
        self.entered_time = parsed.local_time or self.entered_time
        if parsed.frequency != ReminderFrequency.ONCE:
            self.frequency = parsed.frequency
        self.resolve_input()

    def resolve_input(self) -> None:
        if not self.timezone:
            return
        if self.pending_day:
            self.selected_day = resolve_reminder_day(
                self.pending_day,
                now=datetime.now(UTC),
                timezone=self.timezone,
                local_time=self.entered_time,
            )
            self.pending_day = None
        if self.selected_day and self.entered_time:
            self.local_at = datetime.combine(self.selected_day, self.entered_time)


def with_cancel(markup: InlineKeyboardMarkup, cancel: tuple[str, str]) -> InlineKeyboardMarkup:
    label, action = cancel
    return InlineKeyboardMarkup(
        inline_keyboard=[
            *markup.inline_keyboard,
            [InlineKeyboardButton(text=label, callback_data=action)],
        ]
    )


async def get_draft(state: FSMContext) -> Draft | None:
    draft = (await state.get_data()).get("draft")
    if draft is not None and draft.expires <= datetime.now(UTC):
        await state.update_data(draft=None)
        return None
    return draft


async def show_draft(message: Message, draft: Draft, *, edit: bool = False, hint: str = "") -> None:
    reply = message.edit_text if edit else message.answer
    cancel = ("✖️ Отмена", f"r:x:{draft.key}")
    if not draft.event:
        await reply(
            "🔔 <b>О чём напомнить?</b>\nМожно сразу целиком: «завтра в 15:30 отправить счёт».",
            parse_mode="HTML",
        )
        return
    if not draft.timezone:
        await reply(
            "📍 <b>В каком ты городе?</b>\nНапиши город или регион, например «Казань» или "
            "«Владивосток». Это нужно, чтобы напоминание пришло вовремя.",
            parse_mode="HTML",
            reply_markup=keyboard(cancel),
        )
        return
    if draft.awaiting_time:
        await reply(
            "✏️ <b>Когда напомнить?</b>\nНапиши как удобно:\n"
            "• <i>завтра в 18:00</i>\n• <i>через 2 часа</i>\n"
            "• <i>каждый понедельник в 9 утра</i>\n• <i>15 числа каждого месяца в 10:00</i>",
            reply_markup=keyboard(("📅 Выбрать в календаре", f"r:t:{draft.key}"), cancel),
            parse_mode="HTML",
        )
        return
    if draft.local_at is None:
        if draft.selected_day is None:
            today = draft.today
            text = hint + "📅 В какой день напомнить?"
            picker = (
                calendar_keyboard(draft.key, draft.calendar_month, today)
                if draft.calendar_month
                else date_keyboard(draft.key)
            )
        elif draft.selected_hour is None:
            text = f"📅 {draft.selected_day:%d.%m.%Y} · 🕒 Выбери время"
            picker = (
                hour_keyboard(draft.key)
                if draft.choose_hour
                else time_keyboard(
                    draft.key, draft.selected_day, datetime.now(ZoneInfo(draft.timezone))
                )
            )
        else:
            day, hour = draft.selected_day, draft.selected_hour
            text = f"📅 {day:%d.%m.%Y} · 🕒 {hour:02d}:__ · Выбери минуты"
            picker = minute_keyboard(draft.key, draft.minute_page)
        await reply(text, reply_markup=with_cancel(picker, cancel))
        return
    try:
        due = draft.schedule.first_due()
    except AmbiguousTime as error:
        await reply(
            str(error),
            reply_markup=keyboard(
                *[
                    (option.strftime("UTC%z"), f"r:o:{draft.key}:{option.fold}")
                    for option in error.options
                ]
            ),
        )
        return
    except ReminderError as error:
        draft.reset_time()
        await show_draft(message, draft, edit=edit, hint=str(error) + "\n\n")
        return
    if due <= datetime.now(UTC):
        draft.reset_time()
        await show_draft(message, draft, edit=edit, hint="Это время уже прошло.\n\n")
        return
    if draft.choose_frequency:
        await reply(
            "🔁 <b>Как часто напоминать?</b>",
            reply_markup=keyboard(
                *[
                    (("• " if value == draft.frequency else "") + label, f"r:f:{draft.key}:{value}")
                    for value, label in FREQUENCIES.items()
                ]
            ),
            parse_mode="HTML",
        )
        return
    await reply(
        f"🔔 <b>Проверь напоминание</b>\n\n📝 {escape(draft.event)}\n"
        f"🕒 {escape(format_due(due, draft.timezone))}\n"
        f"🔁 {FREQUENCIES[draft.frequency]}"
        + (f"\n📚 {escape(draft.link_title)}" if draft.link_title else ""),
        reply_markup=keyboard_rows(
            (("✅ Подтвердить", f"r:c:{draft.key}"),),
            (("🕒 Время", f"r:w:{draft.key}"), ("🔁 Повтор", f"r:v:{draft.key}")),
            (("📍 Другой город", f"r:g:{draft.key}"), cancel),
        ),
        parse_mode="HTML",
    )


def apply_picker(draft: Draft, action: str, value: str | None) -> bool:
    if action == "z" and value and value.isascii() and value.isdecimal() and not draft.timezone:
        zones = tuple(CITY_ZONES.values())
        if len(value) <= 2 and int(value) < len(zones):
            draft.timezone = zones[int(value)]
            draft.resolve_input()
            return True
    if not draft.timezone or draft.local_at is not None:
        return False
    if action == "m" and value is None and draft.selected_day is None:
        draft.calendar_month = draft.today.replace(day=1)
        return True
    if action == "q" and value in {"0", "1"} and draft.selected_day is None:
        draft.selected_day = draft.today + timedelta(days=int(value))
        draft.resolve_input()
        return True
    if action == "a" and value is None and draft.selected_day and draft.selected_hour is None:
        draft.choose_hour = True
        return True
    if action in {"m", "y"} and draft.selected_day is None and value:
        length = 6 if action == "m" else 8
        if len(value) != length or not value.isascii() or not value.isdecimal():
            return False
        try:
            selected = datetime.strptime(value, "%Y%m" if action == "m" else "%Y%m%d").date()
        except ValueError:
            return False
        today = draft.today
        lower = today.replace(day=1) if action == "m" else today
        if lower <= selected and selected.year <= MAX_PICKER_YEAR:
            draft.calendar_month = selected.replace(day=1)
            if action == "y":
                draft.selected_day = selected
                draft.resolve_input()
            return True
    if value is None or not value.isascii() or not value.isdecimal() or len(value) > 2:
        return False
    number = int(value)
    if (
        action == "s"
        and draft.selected_day
        and draft.selected_hour is None
        and number in (9, 12, 15, 18, 21)
    ):
        draft.local_at = datetime.combine(draft.selected_day, time(number))
        return True
    if action == "h" and draft.selected_day and draft.selected_hour is None and number < 24:
        draft.selected_hour = number
        return True
    if draft.selected_day and draft.selected_hour is not None:
        if action == "n" and number < 60:
            draft.local_at = datetime.combine(draft.selected_day, time(draft.selected_hour, number))
            return True
        if action == "p" and number < MINUTE_PAGE_COUNT and number != draft.minute_page:
            draft.minute_page = number
            return True
    return False


def format_due(due: datetime, timezone: str) -> str:
    zone = ZoneInfo(timezone)
    local = due.astimezone(zone)
    day = f"{local.day} {MONTHS_GENITIVE[local.month - 1]}"
    if local.year != datetime.now(zone).year:
        day += f" {local.year}"
    time_label = f"{local:%H:%M}"
    if local.utcoffset() != local.replace(fold=1 - local.fold).utcoffset():
        offset = local.strftime("%z")
        time_label += f" (UTC{offset[:3]}:{offset[3:5]})"
    return f"{day} в {time_label} · {timezone_label(timezone)}"


def timezone_label(timezone: str) -> str:
    return ZONE_CITIES.get(timezone, timezone)


def reminder_timing(record: ReminderRecord) -> str:
    due = (
        format_due(record.next_due_utc, record.timezone)
        if record.next_due_utc
        else f"Ожидает отправки · {timezone_label(record.timezone)}"
    )
    return f"{due} · {FREQUENCIES[record.frequency]}"


def management_buttons(record: ReminderRecord) -> tuple[tuple[str, str], ...]:
    suffix = f"{record.id.hex}:{record.version}"
    return (
        ("✏️ Изменить", f"r:e:{suffix}"),
        ("🔁 Частота", f"rd:freq:{record.id.hex}"),
        ("🔕 Отключить", f"r:d:{suffix}"),
    )


def management_markup(record: ReminderRecord) -> InlineKeyboardMarkup:
    """Card of one active reminder: edit, frequency and disable, then back to the list."""
    edit, frequency, disable = management_buttons(record)
    return keyboard_rows((edit, frequency), (disable,), (("‹ Все напоминания", "r:list"),))
