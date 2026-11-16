"""Exercise the worker with controlled transport and Telegram outcomes."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage

from alpha_course.application.reminders import DeliveryResult, DeliveryStatus
from alpha_course.entrypoints.worker import poll
from alpha_course.infrastructure.reminder_delivery import send_reminder
from alpha_course.infrastructure.reminders import Delivery


@pytest.fixture
def delivery():
    return Delivery(uuid4(), uuid4(), uuid4(), 123, "Текст <без разметки>")


async def test_telegram_html_text_is_escaped_and_uses_owner_chat(delivery):
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=42)))
    assert await send_reminder(bot, delivery) == DeliveryResult(DeliveryStatus.SENT, message_id=42)
    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == 123
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["text"] == "🔔 <b>Напоминание</b>\n\nТекст &lt;без разметки&gt;"
    rows = kwargs["reply_markup"].inline_keyboard
    key = delivery.reminder_id.hex
    assert [button.callback_data for button in rows[0]] == [
        f"rd:done:{key}",
        f"rd:h1:{key}",
        f"rd:d1:{key}",
    ]
    assert [button.text for button in rows[0]] == ["✅ Готово", "⏰ Через час", "📅 Завтра"]
    assert rows[1][0].callback_data == f"rd:open:{key}"
    assert len(rows) == 2
    assert all(len(b.callback_data.encode()) < 64 for row in rows for b in row)


async def test_telegram_material_link_and_title(delivery):
    delivery = replace(delivery, link_url="https://example.test/m", link_title="Курс <A>")
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    await send_reminder(bot, delivery)
    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["text"].endswith("\n\n📚 Курс &lt;A&gt;")
    first = kwargs["reply_markup"].inline_keyboard[0][0]
    assert (first.text, first.url) == ("📖 Открыть материал", "https://example.test/m")
    assert len(kwargs["reply_markup"].inline_keyboard) == 3


async def test_telegram_flood_control_and_ambiguous_timeout(delivery):
    error = TelegramRetryAfter(SendMessage(chat_id=123, text="test"), "retry", 20)
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=[error, TimeoutError()]))
    assert await send_reminder(bot, delivery) == DeliveryResult(
        DeliveryStatus.RETRY, retry_after=20
    )
    assert await send_reminder(bot, delivery) == DeliveryResult(DeliveryStatus.UNKNOWN)


async def test_worker_sends_only_after_persisted_authorization(delivery):
    stop = asyncio.Event()
    result = DeliveryResult(DeliveryStatus.SENT, message_id=42)

    async def send(value):
        assert value == delivery
        assert store.authorize.await_count == 2
        stop.set()
        return result

    store = SimpleNamespace(
        plan=AsyncMock(),
        claim=AsyncMock(side_effect=[delivery, delivery]),
        authorize=AsyncMock(side_effect=[False, True]),
        complete=AsyncMock(),
    )
    await poll(store, send, stop, 5)
    store.complete.assert_awaited_once_with(delivery, result)


async def test_worker_logs_delivered_event_only_after_successful_send(delivery):
    stop = asyncio.Event()

    async def send(value):
        stop.set()
        return DeliveryResult(DeliveryStatus.SENT, message_id=1)

    store = SimpleNamespace(
        plan=AsyncMock(),
        claim=AsyncMock(side_effect=[delivery, None]),
        authorize=AsyncMock(return_value=True),
        complete=AsyncMock(),
    )
    events = SimpleNamespace(log=AsyncMock())
    await poll(store, send, stop, 5, events)
    events.log.assert_awaited_once_with(123, "reminder_delivered")
