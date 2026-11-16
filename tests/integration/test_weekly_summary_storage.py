"""Sunday summaries reuse durable reminder delivery and actual database roles."""

import asyncio
from datetime import UTC, datetime, time, timedelta

import pytest
from test_database import engines as engines
from test_engagement import env as env
from test_engagement import occurrences

from alpha_course.application.reminders import DeliveryResult, DeliveryStatus
from alpha_course.domain.reminders import OccurrenceStatus
from alpha_course.infrastructure.events import EventStore
from alpha_course.infrastructure.reminders import ReminderStore

pytestmark = pytest.mark.integration


async def subscribe(engagement, owner):
    return await engagement.save_settings(
        owner, weekly_summary=True, weekly_summary_asked=True, timezone="Europe/Moscow"
    )


async def sunday(env):
    bot, worker, engagement, clock, owner = env
    clock.now = datetime(2026, 11, 14, 12, tzinfo=UTC)
    await subscribe(engagement, owner)
    clock.now = datetime(2026, 11, 15, 16, tzinfo=UTC)
    await worker.plan()
    return bot, worker, engagement, clock, owner


async def test_optin_persist_single_and_cancel_claimed(env):
    bot, worker, engagement, clock, owner = await sunday(env)
    assert not await bot.list(owner)
    delivery = await worker.claim()
    assert delivery.kind == "weekly_summary"
    await subscribe(engagement, owner)
    assert len(await occurrences(worker, owner)) == 1
    await engagement.save_settings(owner, weekly_summary=False)
    assert not await worker.authorize(delivery)
    assert (await occurrences(worker, owner))[0].status == OccurrenceStatus.CANCELLED
    assert not (await engagement.get_settings(owner)).weekly_summary


async def test_concurrent_workers_once_and_restart(env):
    _, worker, engagement, clock, owner = await sunday(env)
    other = ReminderStore(worker.sessions.kw["bind"], clock=clock)
    claims = await asyncio.gather(worker.claim(), other.claim())
    deliveries = [item for item in claims if item is not None]
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert await worker.authorize(delivery)
    await worker.complete(delivery, DeliveryResult(DeliveryStatus.SENT, message_id=1))
    await subscribe(engagement, owner)
    await other.plan()
    assert await other.claim() is None
    clock.now += timedelta(days=7)
    await other.plan()
    assert (await other.claim()).kind == "weekly_summary"


async def test_quiet_crosses_monday_and_restart_does_not_catch_up(env):
    _, worker, engagement, clock, owner = await sunday(env)
    await engagement.save_settings(owner, quiet_start=time(18), quiet_end=time(8))
    assert await worker.claim() is None
    assert (await occurrences(worker, owner))[0].status == OccurrenceStatus.EXPIRED
    clock.now += timedelta(days=1)
    await worker.plan()
    assert await worker.claim() is None


async def test_worker_can_read_summary_events(env, engines):
    bot, worker, _, clock, owner = await sunday(env)
    events = EventStore(bot.sessions.kw["bind"])
    await events.log(owner, "user_active")
    result = await EventStore(worker.sessions.kw["bind"]).weekly_progress(
        owner, "Europe/Moscow", datetime.now(UTC)
    )
    assert result.active_days == 1
    assert result.materials == 0
    from sqlalchemy import text

    from alpha_course.infrastructure.events import user_key

    async with engines[0].begin() as connection:
        await connection.execute(
            text("DELETE FROM app.events WHERE user_key=:key"), {"key": user_key(owner)}
        )


async def test_summary_timezone_change_recalculates_sunday(env):
    _, worker, engagement, clock, owner = env
    clock.now = datetime(2026, 11, 14, 12, tzinfo=UTC)
    await subscribe(engagement, owner)
    await engagement.save_settings(owner, timezone="Asia/Tokyo")
    clock.now = datetime(2026, 11, 15, 10, tzinfo=UTC)
    await worker.plan()
    delivery = await worker.claim()
    assert delivery.timezone == "Asia/Tokyo"
    assert delivery.due_utc == clock.now


async def test_retry_never_spills_into_next_local_day(env):
    _, worker, _, clock, owner = await sunday(env)
    delivery = await worker.claim()
    assert await worker.authorize(delivery)
    await worker.complete(delivery, DeliveryResult(DeliveryStatus.RETRY, retry_after=6 * 3600))
    assert (await occurrences(worker, owner))[0].status == OccurrenceStatus.EXPIRED
    clock.now += timedelta(days=1)
    assert await worker.claim() is None


@pytest.mark.parametrize("changes", [{"enabled": False}, {"weekly_limit": 0}])
async def test_global_notification_optout_blocks_claimed_weekly_summary(env, changes):
    _, worker, engagement, _, owner = await sunday(env)
    delivery = await worker.claim()
    assert delivery is not None
    await engagement.save_settings(owner, **changes)
    assert not await worker.authorize(delivery)
    assert (await occurrences(worker, owner))[0].status == OccurrenceStatus.CANCELLED


async def test_timezone_change_after_sent_cannot_repeat_same_week(env):
    _, worker, engagement, clock, owner = await sunday(env)
    delivery = await worker.claim()
    assert await worker.authorize(delivery)
    await worker.complete(delivery, DeliveryResult(DeliveryStatus.SENT, message_id=1))
    await engagement.save_settings(owner, timezone="UTC")
    clock.now = datetime(2026, 11, 15, 19, tzinfo=UTC)
    await worker.plan()
    assert await worker.claim() is None
    rows = await occurrences(worker, owner)
    assert [row.status for row in rows] == [OccurrenceStatus.SENT, OccurrenceStatus.CANCELLED]
