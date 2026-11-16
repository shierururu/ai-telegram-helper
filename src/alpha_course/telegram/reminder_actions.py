"""Inline actions on delivered reminders: done, snooze, open card, change frequency."""

import logging
import re
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from alpha_course.application.reminders import ReminderError, Schedule, local_options
from alpha_course.domain.reminders import ReminderFrequency, ReminderStatus
from alpha_course.infrastructure.events import EventStore
from alpha_course.infrastructure.models import ReminderRecord
from alpha_course.infrastructure.reminders import ReminderStore
from alpha_course.telegram.reminders import (
    FREQUENCIES,
    format_due,
    keyboard,
    keyboard_rows,
    management_markup,
    reminder_timing,
)

logger = logging.getLogger(__name__)

PATTERN = re.compile(r"rd:(done|h1|d1|open|freq|setf):([0-9a-f]{32})(?::([odwmy]))?")
FREQUENCY_CODES = {
    "o": ReminderFrequency.ONCE,
    "d": ReminderFrequency.DAILY,
    "w": ReminderFrequency.WEEKLY,
    "m": ReminderFrequency.MONTHLY,
    "y": ReminderFrequency.YEARLY,
}
NOT_FOUND = "Напоминание не найдено. Открой список напоминаний."


def is_journey(record: ReminderRecord) -> bool:
    """A step of the 7-day plan: it is managed together with the plan, not on its own."""
    return getattr(record, "kind", "normal") == "journey"


def management_card(record: ReminderRecord) -> tuple[str, InlineKeyboardMarkup]:
    """Text and buttons for one reminder, shared with the reminders list."""
    if is_journey(record):
        return (
            f"📅 {record.event_text}\n\nЭто шаг плана на 7 дней. Управлять им — в самом плане.",
            keyboard_rows(
                (("📅 Открыть план", "jn:show"), ("⏹ Остановить план", "jn:stop")),
                (("‹ Все напоминания", "r:list"),),
            ),
        )
    if record.status != ReminderStatus.ACTIVE:
        key = record.id.hex
        return (
            f"🔔 {record.event_text}\n\nЭто напоминание уже не активно. Напомнить ещё раз?",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="⏰ Через час", callback_data=f"rd:h1:{key}"),
                        InlineKeyboardButton(text="📅 Завтра", callback_data=f"rd:d1:{key}"),
                    ],
                    [InlineKeyboardButton(text="‹ Все напоминания", callback_data="r:list")],
                ]
            ),
        )
    return (
        f"🔔 {record.event_text}\n\n{reminder_timing(record)}",
        management_markup(record),
    )


def frequency_keyboard(record: ReminderRecord) -> InlineKeyboardMarkup:
    codes = {value: code for code, value in FREQUENCY_CODES.items()}
    return keyboard(
        *[
            (
                ("• " if value == record.frequency else "") + label,
                f"rd:setf:{record.id.hex}:{codes[value]}",
            )
            for value, label in FREQUENCIES.items()
        ],
        ("‹ Назад", f"rd:open:{record.id.hex}"),
    )


def link_markup(message: Message) -> InlineKeyboardMarkup | None:
    """Keep only the material button of a delivered reminder."""
    markup = getattr(message, "reply_markup", None)
    rows = [
        [button for button in row if button.url]
        for row in (markup.inline_keyboard if markup else [])
    ]
    rows = [row for row in rows if row]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def same_time_schedule(record: ReminderRecord, frequency: ReminderFrequency) -> Schedule:
    zone = ZoneInfo(record.timezone)
    local = record.next_due_utc.astimezone(zone)
    local_at = local.replace(tzinfo=None)
    fold = local.fold if len(local_options(local_at, zone)) > 1 else None
    return Schedule(
        record.event_text,
        local_at,
        record.timezone,
        frequency,
        fold,
        anchor_day=record.month_day,
    )


def create_reminder_actions_router(reminders: ReminderStore, events: EventStore | None) -> Router:
    router = Router(name="reminder_actions")
    router.callback_query.filter(
        F.message.chat.type == "private",
        lambda query: query.message is not None and query.from_user.id == query.message.chat.id,
    )

    async def log(owner: int, kind: str, **payload) -> None:
        if events is not None:
            await events.log(owner, kind, **payload)

    @router.callback_query(F.data.startswith("rd:"))
    async def handle(query: CallbackQuery) -> None:
        match = PATTERN.fullmatch(query.data or "")
        if match is None or not isinstance(query.message, Message):
            await query.answer("Кнопка устарела.", show_alert=True)
            return
        action, key, code = match.groups()
        owner, message, reminder_id = query.from_user.id, query.message, UUID(hex=key)
        try:
            record = await reminders.get(owner, reminder_id)
            if record is None:
                await query.answer(NOT_FOUND, show_alert=True)
                return
            if action == "done":
                await message.edit_text(
                    "✅ Отмечено. Так держать!", reply_markup=link_markup(message), parse_mode=None
                )
                await log(owner, "reminder_done", reminder_id=str(reminder_id))
            elif action in ("h1", "d1"):
                token = str(message.message_id)
                copy = await reminders.snooze(owner, reminder_id, action, token)
                await message.edit_text(
                    f"⏰ Напомню {format_due(copy.next_due_utc, copy.timezone)}",
                    reply_markup=link_markup(message),
                    parse_mode=None,
                )
                await log(owner, "reminder_snoozed", delay=action)
            elif action == "open":
                text, markup = management_card(record)
                await message.answer(text, reply_markup=markup, parse_mode=None)
                await log(owner, "reminder_open")
            elif action == "freq":
                if record.status != ReminderStatus.ACTIVE or is_journey(record):
                    await query.answer(NOT_FOUND, show_alert=True)
                    return
                await message.edit_text(
                    f"🔔 {record.event_text}\n\n🔁 Как часто напоминать?",
                    reply_markup=frequency_keyboard(record),
                    parse_mode=None,
                )
            elif action == "setf" and code:
                if record.status != ReminderStatus.ACTIVE or is_journey(record):
                    await query.answer(NOT_FOUND, show_alert=True)
                    return
                frequency = FREQUENCY_CODES[code]
                updated = await reminders.update(
                    owner, reminder_id, record.version, same_time_schedule(record, frequency)
                )
                text, markup = management_card(updated)
                await message.edit_text(text, reply_markup=markup, parse_mode=None)
                await log(owner, "reminder_frequency_changed", frequency=frequency.value)
        except ReminderError as error:
            await query.answer(str(error), show_alert=True)
            return
        await query.answer()

    return router
