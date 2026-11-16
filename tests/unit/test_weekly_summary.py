"""Weekly praise only reports explicit facts, in the user's local calendar."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from alpha_course.application.weekly_summary import next_summary, same_sunday, weekly_progress
from alpha_course.infrastructure.reminder_delivery import reminder_keyboard
from alpha_course.infrastructure.reminders import Delivery

NOW = datetime(2026, 11, 15, 16, tzinfo=UTC)


def event(day, kind, material=None, **payload):
    return SimpleNamespace(
        id=f"{day}:{kind}:{material}",
        created_at=datetime(2026, 11, day, 12, tzinfo=UTC),
        kind=kind,
        material_id=material,
        payload=payload,
    )


def test_self_reports_unique_and_undo_removes_completion():
    rows = [
        event(9, "learning_marked_done", "a", material_kind="course"),
        event(10, "learning_marked_done", "a", material_kind="course"),
        event(11, "learning_marked_done", "b", material_kind="article"),
        event(12, "learning_marked_undone", "a"),
        event(13, "material_click", "c"),
        event(14, "reminder_delivered"),
    ]
    progress = weekly_progress(rows, "Europe/Moscow", NOW)
    assert (progress.materials, progress.courses, progress.active_days) == (1, 0, 4)
    assert progress.learning_streak == 1
    assert "самоотметки" in progress.format_text()


def test_streak_crosses_monday_and_background_is_not_activity():
    rows = [event(day, "user_active") for day in (7, 8, 9, 10)]
    rows += [event(10, "user_active"), event(11, "reminder_delivered")]
    result = weekly_progress(rows, "Europe/Moscow", NOW)
    assert result.active_streak == 4
    assert result.active_days == 2


def test_questions_tasks_and_zero_activity():
    rows = [
        event(9, "question_asked"),
        event(10, "reminder_done", reminder_id="r"),
        event(10, "reminder_done", reminder_id="r"),
        event(11, "reminder_done"),
    ]
    result = weekly_progress(rows, "Europe/Moscow", NOW)
    assert (result.questions, result.tasks) == (1, 1)
    assert "нет отмеченных достижений" in weekly_progress([], "UTC", NOW).format_text()


def test_local_sunday_and_schedule_boundary():
    due = next_summary(datetime(2026, 11, 14, 20, tzinfo=UTC), "Asia/Tokyo")
    assert due == datetime(2026, 11, 15, 10, tzinfo=UTC)
    assert next_summary(due, "Asia/Tokyo").day == 22
    assert same_sunday(due, due, "Asia/Tokyo")
    assert not same_sunday(due, datetime(2026, 11, 15, 15, tzinfo=UTC), "Asia/Tokyo")


def test_summary_keyboard_only_has_optout():
    delivery = Delivery(uuid4(), uuid4(), uuid4(), 1, "text", kind="weekly_summary")
    buttons = [button for row in reminder_keyboard(delivery).inline_keyboard for button in row]
    assert [button.callback_data for button in buttons] == ["ns:weekly:off"]


async def test_worker_generates_local_summary_before_authorizing_and_sending():
    import asyncio
    from unittest.mock import AsyncMock

    from alpha_course.application.reminders import DeliveryResult, DeliveryStatus
    from alpha_course.application.weekly_summary import WeeklyProgress
    from alpha_course.entrypoints.worker import poll

    stop = asyncio.Event()
    delivery = Delivery(
        uuid4(),
        uuid4(),
        uuid4(),
        1,
        "old",
        kind="weekly_summary",
        timezone="Europe/Moscow",
        due_utc=NOW,
    )
    store = SimpleNamespace(
        plan=AsyncMock(),
        claim=AsyncMock(side_effect=[delivery, None]),
        authorize=AsyncMock(return_value=True),
        complete=AsyncMock(),
        clock=lambda: NOW,
    )
    events = SimpleNamespace(
        weekly_progress=AsyncMock(return_value=WeeklyProgress(active_days=2)), log=AsyncMock()
    )

    async def send(actual):
        assert "активных дней в боте: 2" in actual.event_text
        assert actual.kind == "weekly_summary"
        stop.set()
        return DeliveryResult(DeliveryStatus.SENT, message_id=2)

    await poll(store, send, stop, 0, events)
    events.weekly_progress.assert_awaited_once_with(1, "Europe/Moscow", NOW)
    store.authorize.assert_awaited_once()
    store.complete.assert_awaited_once()
    events.log.assert_awaited_once_with(1, "weekly_summary_delivered")
