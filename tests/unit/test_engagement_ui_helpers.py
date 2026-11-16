from datetime import time

import pytest

from alpha_course.application.engagement import NotificationSettings
from alpha_course.telegram.engagement import parse_clock, settings_html, settings_markup


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("09:00", time(9)),
        ("в 8 вечера", time(20)),
        ("18.30", time(18, 30)),
        ("в 7", time(7)),
        ("25:00", None),
        ("когда-нибудь", None),
    ],
)
def test_parse_clock(text, expected):
    assert parse_clock(text) == expected


def test_settings_screen_shows_limits_and_toggles():
    settings = NotificationSettings(timezone="Europe/Moscow")
    html = settings_html(settings)
    assert "22:00" in html and "08:00" in html and "7" in html
    callbacks = [b.callback_data for row in settings_markup(settings).inline_keyboard for b in row]
    assert {"ns:e", "ns:q", "ns:l:3", "ns:l:7", "ns:l:14", "ns:tz"} <= set(callbacks)


@pytest.mark.asyncio
async def test_weekly_offer_once_without_automatic_consent():
    from dataclasses import replace
    from types import SimpleNamespace

    from alpha_course.telegram.engagement import offer_weekly_summary

    class Store:
        settings = NotificationSettings()
        async def get_settings(self, owner):
            return self.settings
        async def save_settings(self, owner, **changes):
            self.settings = replace(self.settings, **changes)
            return self.settings

    calls = []
    async def answer(text, **kwargs):
        calls.append((text, kwargs))
    store = Store()
    message = SimpleNamespace(answer=answer)
    await offer_weekly_summary(message, 1, store)
    assert "воскресеньям в 19:00" in calls[0][0]
    assert "по Москве" in calls[0][0]
    assert store.settings.weekly_summary_asked is True
    assert store.settings.weekly_summary is False
    buttons = calls[0][1]["reply_markup"].inline_keyboard
    assert {button.callback_data for row in buttons for button in row} == {
        "ns:weekly:on", "ns:weekly:off"
    }
    await offer_weekly_summary(message, 1, store)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_activity_middleware_reaches_child_router_and_ignores_groups():
    from datetime import UTC, datetime

    from aiogram import Bot, Dispatcher, Router
    from aiogram.types import Chat, Message, Update, User

    from alpha_course.entrypoints.bot import ActivityMiddleware

    class Events:
        calls = []
        async def log(self, owner, kind):
            self.calls.append((owner, kind))
    events = Events()
    dispatcher = Dispatcher()
    dispatcher.message.middleware(ActivityMiddleware(events))
    child = Router()
    async def action(message):
        pass
    child.message.register(action)
    dispatcher.include_router(child)
    bot = Bot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
    try:
        for update_id, chat in enumerate([Chat(id=1, type="private"), Chat(id=-1, type="group")]):
            message = Message(message_id=1, date=datetime.now(UTC), chat=chat,
                              from_user=User(id=1, is_bot=False, first_name="Test"), text="action")
            await dispatcher.feed_update(bot, Update(update_id=update_id, message=message))
        assert events.calls == [(1, "user_active")]
    finally:
        await bot.session.close()


def test_settings_without_timezone_explains_moscow_weekly_time():
    assert "итоги — в 19:00 по Москве" in settings_html(NotificationSettings())
