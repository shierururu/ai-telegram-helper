"""The bot exposes one dated journey; stale keyboards cannot create an old plan."""

import pytest

from alpha_course.telegram.menu import ROUTE, SETTINGS
from tests.unit.telegram_fakes import Harness, synthetic_journey


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [ROUTE, "/route", "/plan", "pl:show", "pl:done:0", "pl:refresh"])
async def test_route_entries_offer_seven_days_without_creating(action):
    rig = Harness(with_engagement=True)
    try:
        if action.startswith("pl:"):
            await rig.click(action)
        else:
            await rig.send(action)
        assert "План на 7 дней" in rig.last.text
        assert "Какая у тебя цель" in rig.last.text
        assert (await rig.state_data())["jn_wait"] == "goal"
        assert rig.engagement.journey is None
        assert rig.assistant.plan_calls == []
    finally:
        await rig.aclose()


@pytest.mark.asyncio
async def test_route_entries_show_same_active_journey():
    rig = Harness(with_engagement=True)
    rig.engagement.journey = synthetic_journey()
    try:
        await rig.send(ROUTE)
        expected = rig.last.text
        await rig.click("pl:done:0")
        assert rig.last.text == expected
        assert "День 7" in expected
        assert rig.engagement.journey.done_days == 1
    finally:
        await rig.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [SETTINGS, "/settings"])
async def test_settings_resets_flows_and_city_does_not_subscribe(action):
    rig = Harness(with_engagement=True)
    try:
        await rig.set_state_data(
            ai_help=True,
            draft={"unfinished": True},
            profile_field="goal",
            jn_wait="time",
            jn_draft={"unfinished": True},
        )
        await rig.send(action)
        data = await rig.state_data()
        assert not data["ai_help"]
        assert all(data[key] is None for key in ("draft", "profile_field", "jn_wait", "jn_draft"))
        assert "Настройки" in rig.last.text and "Итоги недели" in rig.last.text
        await rig.click("ns:tz")
        await rig.send("Казань")
        assert rig.engagement.settings.timezone == "Europe/Moscow"
        assert rig.engagement.settings.weekly_summary is False
        await rig.click("ns:weekly:on")
        assert rig.engagement.settings.weekly_summary is True
        await rig.click("ns:weekly:off")
        assert rig.engagement.settings.weekly_summary is False
    finally:
        await rig.aclose()


@pytest.mark.asyncio
async def test_preview_requires_explicit_confirmation_before_creating():
    from unittest.mock import AsyncMock

    rig = Harness(with_engagement=True)
    rig.engagement.settings = rig.engagement.settings.__class__(timezone="Europe/Moscow")
    steps = [
        {
            "material_id": "free1",
            "title": "Материал",
            "url": "https://example.test/free1",
            "task": f"Шаг {day}",
        }
        for day in range(1, 8)
    ]
    rig.assistant.journey_steps = AsyncMock(return_value=steps)
    rig.engagement.create_journey = AsyncMock(return_value=synthetic_journey())
    try:
        await rig.send(ROUTE)
        await rig.send("найти клиентов")
        await rig.click("jn:t:0900")
        assert "День 7" in rig.last.text
        rig.engagement.create_journey.assert_not_awaited()
        assert rig.engagement.settings.weekly_summary is False
        await rig.click("jn:go")
        rig.engagement.create_journey.assert_awaited_once()
        assert rig.engagement.settings.weekly_summary is False
    finally:
        await rig.aclose()
