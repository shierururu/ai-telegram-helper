"""Callback handling for delivered reminders, using a fake store and real aiogram types."""

from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from aiogram.types import CallbackQuery, Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.types import User as TgUser

from alpha_course.application.reminders import NotFound
from alpha_course.domain.reminders import ReminderFrequency
from alpha_course.telegram.reminder_actions import (
    create_reminder_actions_router,
    frequency_keyboard,
    management_card,
    same_time_schedule,
)


def record(**changes):
    values = dict(
        id=uuid4(),
        owner_id=5,
        event_text="Сдать <отчёт>",
        frequency="weekly",
        local_time=time(10),
        timezone="Europe/Moscow",
        weekday=3,
        month_day=None,
        next_due_utc=datetime.now(UTC) + timedelta(days=1),
        status="active",
        version=2,
        link_url="https://example.test/m",
        link_title="Материал",
    )
    values.update(changes)
    return SimpleNamespace(**values)


def make_query(data, *, user=5, chat=5, reply_markup=None):
    message = Message(
        message_id=77,
        date=datetime.now(UTC),
        chat=Chat(id=chat, type="private"),
        text="x",
        reply_markup=reply_markup,
    )
    return CallbackQuery(
        id="1",
        from_user=TgUser(id=user, is_bot=False, first_name="T"),
        chat_instance="c",
        message=message,
        data=data,
    )


def handler(store, events):
    router = create_reminder_actions_router(store, events)
    return router.callback_query.handlers[0].callback


MATERIAL = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📖 Открыть материал", url="https://example.test/m")],
        [InlineKeyboardButton(text="✅ Готово", callback_data="rd:done:x")],
    ]
)


async def test_done_edits_message_keeps_material_button_and_logs():
    item = record()
    store = SimpleNamespace(get=AsyncMock(return_value=item))
    events = SimpleNamespace(log=AsyncMock())
    query = make_query(f"rd:done:{item.id.hex}", reply_markup=MATERIAL)
    with (
        patch.object(Message, "edit_text", AsyncMock()) as edit,
        patch.object(CallbackQuery, "answer", AsyncMock()),
    ):
        await handler(store, events)(query)
    store.get.assert_awaited_once_with(5, item.id)
    assert edit.await_args.args[0] == "✅ Отмечено. Так держать!"
    rows = edit.await_args.kwargs["reply_markup"].inline_keyboard
    assert [[b.url for b in row] for row in rows] == [["https://example.test/m"]]
    events.log.assert_awaited_once_with(5, "reminder_done", reminder_id=str(item.id))


async def test_snooze_creates_copy_and_reports_time():
    item = record(frequency="once")
    copy = record(next_due_utc=datetime.now(UTC) + timedelta(hours=1))
    store = SimpleNamespace(get=AsyncMock(return_value=item), snooze=AsyncMock(return_value=copy))
    events = SimpleNamespace(log=AsyncMock())
    query = make_query(f"rd:h1:{item.id.hex}")
    with (
        patch.object(Message, "edit_text", AsyncMock()) as edit,
        patch.object(CallbackQuery, "answer", AsyncMock()),
    ):
        await handler(store, events)(query)
    store.snooze.assert_awaited_once_with(5, item.id, "h1", "77")
    assert edit.await_args.args[0].startswith("⏰ Напомню ")
    events.log.assert_awaited_once_with(5, "reminder_snoozed", delay="h1")


async def test_foreign_or_unknown_reminder_is_rejected_without_side_effects():
    store = SimpleNamespace(get=AsyncMock(return_value=None), snooze=AsyncMock())
    query = make_query(f"rd:d1:{uuid4().hex}", user=9, chat=9)
    answer = AsyncMock()
    with (
        patch.object(Message, "edit_text", AsyncMock()) as edit,
        patch.object(CallbackQuery, "answer", answer),
    ):
        await handler(store, None)(query)
    edit.assert_not_awaited()
    store.snooze.assert_not_awaited()
    assert answer.await_args.kwargs["show_alert"] is True


async def test_malformed_callback_is_reported_stale():
    answer = AsyncMock()
    with patch.object(CallbackQuery, "answer", answer):
        await handler(SimpleNamespace(), None)(make_query("rd:done:zzz"))
    assert answer.await_args.kwargs["show_alert"] is True


async def test_open_sends_management_card_as_new_message():
    item = record()
    store = SimpleNamespace(get=AsyncMock(return_value=item))
    query = make_query(f"rd:open:{item.id.hex}")
    with (
        patch.object(Message, "answer", AsyncMock()) as send,
        patch.object(CallbackQuery, "answer", AsyncMock()),
    ):
        await handler(store, None)(query)
    text, markup = management_card(item)
    assert send.await_args.args[0] == text
    assert send.await_args.kwargs["reply_markup"] == markup


async def test_set_frequency_saves_with_same_time_and_current_version():
    item = record(frequency="daily", weekday=None)
    updated = record(id=item.id, frequency="monthly", month_day=15, version=3)
    store = SimpleNamespace(
        get=AsyncMock(return_value=item), update=AsyncMock(return_value=updated)
    )
    query = make_query(f"rd:setf:{item.id.hex}:m")
    with (
        patch.object(Message, "edit_text", AsyncMock()),
        patch.object(CallbackQuery, "answer", AsyncMock()),
    ):
        await handler(store, None)(query)
    args = store.update.await_args.args
    assert args[:3] == (5, item.id, 2)
    assert args[3].frequency == ReminderFrequency.MONTHLY
    assert args[3].timezone == "Europe/Moscow"


async def test_set_frequency_reports_domain_errors():
    item = record()
    store = SimpleNamespace(
        get=AsyncMock(return_value=item), update=AsyncMock(side_effect=NotFound("нет"))
    )
    answer = AsyncMock()
    with patch.object(CallbackQuery, "answer", answer):
        await handler(store, None)(make_query(f"rd:setf:{item.id.hex}:d"))
    assert answer.await_args.args[0] == "нет"


def test_frequency_keyboard_has_five_choices_and_short_callbacks():
    rows = frequency_keyboard(record()).inline_keyboard
    callbacks = [row[0].callback_data for row in rows]
    assert [c.rsplit(":", 1)[-1] for c in callbacks[:5]] == ["o", "d", "w", "m", "y"]
    assert all(len(c.encode()) < 64 for c in callbacks)


def test_same_time_schedule_keeps_local_time_and_anchor():
    item = record(
        frequency="monthly", month_day=31, next_due_utc=datetime(2026, 2, 28, 7, tzinfo=UTC)
    )
    schedule = same_time_schedule(item, ReminderFrequency.YEARLY)
    assert schedule.local_at == datetime(2026, 2, 28, 10)
    assert schedule.day_of_month == 31


def test_card_for_inactive_reminder_offers_only_snooze():
    card_text, markup = management_card(record(status="finished"))
    assert "не активно" in card_text
    assert [b.callback_data.split(":")[1] for b in markup.inline_keyboard[0]] == ["h1", "d1"]
