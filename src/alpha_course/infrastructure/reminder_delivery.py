"""Telegram delivery adapter."""

import asyncio
from html import escape

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNotFound,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from alpha_course.application.reminders import DeliveryResult, DeliveryStatus
from alpha_course.infrastructure.reminders import Delivery


def reminder_text(delivery: Delivery) -> str:
    if delivery.kind == "weekly_summary":
        return escape(delivery.event_text)
    text = "🔔 <b>Напоминание</b>\n\n" + escape(delivery.event_text)
    if delivery.link_title:
        text += "\n\n📚 " + escape(delivery.link_title)
    return text


def reminder_keyboard(delivery: Delivery) -> InlineKeyboardMarkup:
    if delivery.kind == "weekly_summary":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Отключить итоги недели", callback_data="ns:weekly:off")]
            ]
        )
    key = delivery.reminder_id.hex
    rows = []
    if delivery.link_url:
        rows.append([InlineKeyboardButton(text="📖 Открыть материал", url=delivery.link_url)])
    rows.append(
        [
            InlineKeyboardButton(text="✅ Готово", callback_data=f"rd:done:{key}"),
            InlineKeyboardButton(text="⏰ Через час", callback_data=f"rd:h1:{key}"),
            InlineKeyboardButton(text="📅 Завтра", callback_data=f"rd:d1:{key}"),
        ]
    )
    rows.append([InlineKeyboardButton(text="⚙️ Настроить", callback_data=f"rd:open:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_reminder(bot: Bot, delivery: Delivery) -> DeliveryResult:
    try:
        async with asyncio.timeout(30):
            message = await bot.send_message(
                chat_id=delivery.owner_id,
                text=reminder_text(delivery),
                reply_markup=reminder_keyboard(delivery),
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                parse_mode=ParseMode.HTML,
            )
    except TelegramRetryAfter as error:
        return DeliveryResult(DeliveryStatus.RETRY, retry_after=error.retry_after)
    except TelegramForbiddenError, TelegramNotFound:
        return DeliveryResult(DeliveryStatus.BLOCKED)
    except TelegramBadRequest as error:
        if "chat not found" in error.message.lower():
            return DeliveryResult(DeliveryStatus.BLOCKED)
        return DeliveryResult(DeliveryStatus.FAILED)
    except Exception:
        # A network failure does not establish whether Telegram accepted the message.
        return DeliveryResult(DeliveryStatus.UNKNOWN)
    return DeliveryResult(DeliveryStatus.SENT, message_id=message.message_id)
