"""Notification limits and the learning journey against real PostgreSQL roles."""

from datetime import UTC, datetime, time, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from test_database import engines as engines
from test_reminders import Clock

from alpha_course.application.engagement import JourneyDayNotReached
from alpha_course.application.reminders import DeliveryResult, DeliveryStatus, ReminderError
from alpha_course.domain.reminders import ReminderFrequency as Frequency
from alpha_course.infrastructure.engagement import EngagementStore
from alpha_course.infrastructure.models import ReminderOccurrence, ReminderRecord
from alpha_course.infrastructure.reminders import ReminderStore

pytestmark = pytest.mark.integration

STEPS = [
    {
        "title": f"Material {i}",
        "url": f"https://example.test/{i}",
        "material_id": f"m{i}",
        "task": f"step {i}",
    }
    for i in range(1, 8)
]


@pytest.fixture
async def env(engines):
    migrator, bot, worker = engines
    clock = Clock()
    owner = -(uuid4().int % (2**62) + 1)
    yield (
        ReminderStore(bot, clock=clock),
        ReminderStore(worker, clock=clock),
        EngagementStore(bot, clock=clock),
        clock,
        owner,
    )
    async with migrator.begin() as connection:
        where_owner = "SELECT id FROM app.reminders WHERE owner_id = :owner"
        params = {"owner": owner}
        await connection.execute(
            text(
                "DELETE FROM app.reminder_attempts WHERE occurrence_id IN (SELECT id FROM "
                "app.reminder_occurrences WHERE reminder_id IN (" + where_owner + "))"
            ),
            params,
        )
        await connection.execute(
            text("DELETE FROM app.reminder_occurrences WHERE reminder_id IN (" + where_owner + ")"),
            params,
        )
        for table in ("reminders", "journeys", "notification_settings"):
            await connection.execute(
                text(f"DELETE FROM app.{table} WHERE owner_id = :owner"), params
            )


async def occurrences(store, owner):
    async with store.sessions() as session:
        return (
            await session.scalars(
                select(ReminderOccurrence)
                .join(ReminderRecord, ReminderRecord.id == ReminderOccurrence.reminder_id)
                .where(ReminderRecord.owner_id == owner)
                .order_by(ReminderOccurrence.due_utc)
            )
        ).all()


async def test_settings_roundtrip(env):
    _, _, engagement, _, owner = env
    assert (await engagement.get_settings(owner)).weekly_limit == 7
    saved = await engagement.save_settings(owner, weekly_limit=3, timezone="Europe/Moscow")
    assert saved.weekly_limit == 3 and saved.quiet_enabled
    await engagement.save_settings(owner, quiet_start=time(23), enabled=False)
    again = await engagement.get_settings(owner)
    assert (again.weekly_limit, again.timezone, again.quiet_start, again.enabled) == (
        3,
        "Europe/Moscow",
        time(23),
        False,
    )
    with pytest.raises(ReminderError):
        await engagement.save_settings(owner, weekly_limit=99)
    with pytest.raises(ReminderError):
        await engagement.save_settings(owner, nonsense=1)


async def test_journey_creation_is_idempotent_and_hidden_from_list(env):
    bot, _, engagement, clock, owner = env
    key = uuid4()
    first = await engagement.create_journey(owner, key, "goal", "Etc/UTC", time(10), STEPS)
    second = await engagement.create_journey(owner, key, "goal", "Etc/UTC", time(10), STEPS)
    assert first.id == second.id
    assert [s.day for s in first.steps] == list(range(1, 8))
    assert first.steps[0].due_utc == datetime(2026, 1, 2, 10, tzinfo=UTC)
    assert first.steps[6].due_utc == datetime(2026, 1, 8, 10, tzinfo=UTC)
    assert await bot.list(owner) == []
    async with bot.sessions() as session:
        rows = (
            await session.scalars(select(ReminderRecord).where(ReminderRecord.owner_id == owner))
        ).all()
    assert len(rows) == 7 and {r.kind for r in rows} == {"journey"}
    assert rows[0].frequency == "once"
    # The journey does not use up the user's own five reminders.
    for _ in range(5):
        await bot.create(owner, uuid4(), clock.schedule(Frequency.DAILY))
    assert len(await bot.list(owner)) == 5
    with pytest.raises(ReminderError):
        await engagement.create_journey(owner, uuid4(), "other", "Etc/UTC", time(10), STEPS)
    assert (await engagement.get_journey(owner)).id == first.id
    assert await engagement.stop_journey(owner, first.id)
    again = await engagement.create_journey(owner, uuid4(), "other", "Etc/UTC", time(10), STEPS)
    assert again.id != first.id


async def test_mark_done_future_refused_and_completion(env):
    _, _, engagement, clock, owner = env
    journey = await engagement.create_journey(owner, uuid4(), "goal", "Etc/UTC", time(10), STEPS)
    with pytest.raises(JourneyDayNotReached):
        await engagement.mark_done(owner, journey.id, 1)
    assert await engagement.mark_done(owner + 1, journey.id, 1) is None
    clock.now = datetime(2026, 1, 2, 10, 5, tzinfo=UTC)
    updated = await engagement.mark_done(owner, journey.id, 1)
    assert updated.steps[0].done and updated.status == "active"
    assert (await engagement.mark_done(owner, journey.id, 1)).done_days == 1
    with pytest.raises(JourneyDayNotReached):
        await engagement.mark_done(owner, journey.id, 2)
    clock.now = datetime(2026, 1, 9, tzinfo=UTC)
    for day in range(1, 8):
        result = await engagement.mark_done(owner, journey.id, day)
    assert result.status == "done"
    assert await engagement.get_journey(owner) is None


async def test_stop_cancels_pending(env):
    _, worker, engagement, clock, owner = env
    journey = await engagement.create_journey(owner, uuid4(), "goal", "Etc/UTC", time(10), STEPS)
    clock.now = datetime(2026, 1, 2, 10, 1, tzinfo=UTC)
    await worker.plan()
    assert (await occurrences(worker, owner))[0].status == "pending"
    assert await engagement.stop_journey(owner, journey.id)
    assert not await engagement.stop_journey(owner + 1, journey.id)
    assert [o.status for o in await occurrences(worker, owner)] == ["cancelled"]
    assert await worker.claim() is None
    assert await engagement.get_journey(owner) is None


async def test_quiet_hours_postpone_and_space(env):
    bot, worker, engagement, clock, owner = env
    # Two reminders at 23:01 UTC fall into the default quiet window 22:00-08:00.
    clock.now = datetime(2026, 1, 1, 23, 0, tzinfo=UTC)
    for _ in range(2):
        await bot.create(owner, uuid4(), clock.schedule())
    clock.step(minutes=1)
    await worker.plan()
    assert await worker.claim() is None
    due = [o.due_utc for o in await occurrences(worker, owner)]
    assert sorted(due) == [
        datetime(2026, 1, 2, 8, tzinfo=UTC),
        datetime(2026, 1, 2, 9, tzinfo=UTC),
    ]
    clock.now = datetime(2026, 1, 2, 8, 1, tzinfo=UTC)
    delivery = await worker.claim()
    assert delivery is not None and await worker.authorize(delivery)
    assert await worker.claim() is None


async def test_quiet_hours_can_be_turned_off(env):
    bot, worker, engagement, clock, owner = env
    await engagement.save_settings(owner, quiet_enabled=False)
    clock.now = datetime(2026, 1, 1, 23, 0, tzinfo=UTC)
    await bot.create(owner, uuid4(), clock.schedule())
    clock.step(minutes=1)
    await worker.plan()
    assert await worker.claim() is not None


async def test_weekly_limit_postpones(env):
    bot, worker, engagement, clock, owner = env
    await engagement.save_settings(owner, weekly_limit=1, quiet_enabled=False)
    await bot.create(owner, uuid4(), clock.schedule())
    await bot.create(owner, uuid4(), clock.schedule())
    clock.step(minutes=1)
    await worker.plan()
    first = await worker.claim()
    assert first is not None and await worker.authorize(first)
    sent_at = clock.now
    await worker.complete(first, DeliveryResult(DeliveryStatus.SENT, message_id=1))
    clock.step(minutes=1)
    assert await worker.claim() is None
    pending = [o for o in await occurrences(worker, owner) if o.status == "pending"]
    assert [o.due_utc for o in pending] == [sent_at + timedelta(days=7)]
    clock.now = sent_at + timedelta(days=7, minutes=1)
    assert await worker.claim() is not None


async def test_disabled_cancels(env):
    bot, worker, engagement, clock, owner = env
    await engagement.save_settings(owner, enabled=False)
    reminder = await bot.create(owner, uuid4(), clock.schedule())
    clock.step(minutes=1)
    await worker.plan()
    assert await worker.claim() is None
    assert [o.status for o in await occurrences(worker, owner)] == ["cancelled"]
    assert (await bot.get(owner, reminder.id)).status == "finished"
