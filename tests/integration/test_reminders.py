"""Exercise reminder races using actual PostgreSQL transactions and runtime roles."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from test_database import engines as engines

from alpha_course.application.reminders import (
    DeliveryResult,
    DeliveryStatus,
    NotFound,
    ReminderError,
    Schedule,
    StaleReminder,
)
from alpha_course.domain.reminders import ReminderFrequency as Frequency
from alpha_course.infrastructure.models import ReminderAttempt, ReminderOccurrence
from alpha_course.infrastructure.reminders import LEASE, ReminderStore

pytestmark = pytest.mark.integration


@dataclass
class Clock:
    now: datetime = datetime(2026, 1, 1, 8, tzinfo=UTC)

    def __call__(self):
        return self.now

    def step(self, **parts):
        self.now += timedelta(**parts)

    def schedule(self, frequency=Frequency.ONCE):
        return Schedule(
            "test event", self.now.replace(tzinfo=None) + timedelta(minutes=1), "Etc/UTC", frequency
        )


@pytest.fixture
async def stores(engines):
    migrator, bot, worker = engines
    clock = Clock()
    owner = -(uuid4().int % (2**62) + 1)
    yield ReminderStore(bot, clock=clock), ReminderStore(worker, clock=clock), clock, owner
    async with migrator.begin() as connection:
        where_owner = "SELECT id FROM app.reminders WHERE owner_id = :owner"
        await connection.execute(
            text(
                "DELETE FROM app.reminder_attempts WHERE occurrence_id IN "
                "(SELECT id FROM app.reminder_occurrences WHERE reminder_id IN ("
                + where_owner
                + "))"
            ),
            {"owner": owner},
        )
        await connection.execute(
            text("DELETE FROM app.reminder_occurrences WHERE reminder_id IN (" + where_owner + ")"),
            {"owner": owner},
        )
        await connection.execute(
            text("DELETE FROM app.reminders WHERE owner_id = :owner"), {"owner": owner}
        )


async def test_idempotent_create_owner_check_and_concurrent_limit(stores):
    bot, _, clock, owner = stores
    key = uuid4()
    first, second = await asyncio.gather(
        bot.create(owner, key, clock.schedule()), bot.create(owner, key, clock.schedule())
    )
    assert first.id == second.id
    results = await asyncio.gather(
        *(bot.create(owner, uuid4(), clock.schedule()) for _ in range(5)), return_exceptions=True
    )
    assert sum(isinstance(result, ReminderError) for result in results) == 1
    assert len(await bot.list(owner)) == 5
    assert await bot.list(owner - 1) == []
    with pytest.raises(NotFound):
        await bot.disable(owner - 1, first.id)


async def test_claim_lease_recovery_and_once_sent(stores):
    bot, worker, clock, owner = stores
    reminder = await bot.create(owner, uuid4(), clock.schedule())
    clock.step(minutes=1)
    await asyncio.gather(worker.plan(), worker.plan())
    claims = await asyncio.gather(worker.claim(), worker.claim())
    assert sum(claim is not None for claim in claims) == 1
    abandoned = next(claim for claim in claims if claim is not None)
    clock.now += LEASE
    delivery = await worker.claim()
    assert delivery.token != abandoned.token
    assert not await worker.authorize(abandoned)
    assert await worker.authorize(delivery)
    await worker.complete(delivery, DeliveryResult(DeliveryStatus.SENT, message_id=42))
    async with worker.sessions() as session:
        occurrence = await session.get(ReminderOccurrence, delivery.occurrence_id)
        attempt = await session.get(ReminderAttempt, delivery.token)
        assert (occurrence.status, occurrence.attempts, attempt.message_id) == ("sent", 1, 42)
    assert (await bot.disable(owner, reminder.id)).status == "finished"
    assert await worker.claim() is None


async def test_recurring_change_stale_disable_and_stop_before_sending(stores):
    bot, worker, clock, owner = stores
    reminder = await bot.create(owner, uuid4(), clock.schedule(Frequency.DAILY))
    clock.step(days=2, minutes=2)
    await worker.plan()
    claim = await worker.claim()
    assert claim
    changed = await bot.update(
        owner, reminder.id, reminder.version, clock.schedule(Frequency.WEEKLY)
    )
    assert not await worker.authorize(claim)
    assert changed.frequency == Frequency.WEEKLY
    with pytest.raises(StaleReminder):
        await bot.disable(owner, reminder.id, version=reminder.version)
    clock.step(minutes=1)
    await worker.plan()
    fresh = await worker.claim()
    assert await worker.authorize(fresh)
    disabled = await bot.disable(owner, reminder.id, version=changed.version)
    await worker.complete(fresh, DeliveryResult(DeliveryStatus.SENT, message_id=42))
    assert (await bot.disable(owner, reminder.id)).version == disabled.version
    assert await bot.list(owner) == []
    async with worker.sessions() as session:
        statuses = (
            await session.scalars(
                select(ReminderOccurrence.status).where(
                    ReminderOccurrence.reminder_id == reminder.id
                )
            )
        ).all()
        assert sorted(statuses) == ["cancelled", "sent"]


async def test_retry_respects_flood_delay_and_unknown_is_not_retried(stores):
    bot, worker, clock, owner = stores
    await bot.create(owner, uuid4(), clock.schedule(Frequency.DAILY))
    clock.step(minutes=1)
    await worker.plan()
    first = await worker.claim()
    assert await worker.authorize(first)
    await worker.complete(first, DeliveryResult(DeliveryStatus.RETRY, retry_after=60))
    clock.step(seconds=59)
    assert await worker.claim() is None
    clock.step(seconds=1)
    second = await worker.claim()
    assert second.token != first.token
    assert await worker.authorize(second)
    clock.now += LEASE
    assert await worker.claim() is None
    async with worker.sessions() as session:
        occurrence = await session.get(ReminderOccurrence, second.occurrence_id)
        assert occurrence.status == "unknown"
    await worker.complete(second, DeliveryResult(DeliveryStatus.SENT, message_id=42))
    async with worker.sessions() as session:
        occurrence = await session.get(ReminderOccurrence, second.occurrence_id)
        assert occurrence.status == "sent"
    clock.step(days=1)
    await worker.plan()
    assert await worker.claim()


async def test_expired_once_and_flood_delay_outside_window(stores):
    bot, worker, clock, owner = stores
    expired = await bot.create(owner, uuid4(), clock.schedule())
    clock.step(hours=1)
    await worker.plan()
    assert await worker.claim() is None
    assert (await bot.disable(owner, expired.id)).status == "finished"
    await bot.create(owner, uuid4(), clock.schedule())
    clock.step(minutes=1)
    await worker.plan()
    delivery = await worker.claim()
    assert await worker.authorize(delivery)
    await worker.complete(delivery, DeliveryResult(DeliveryStatus.RETRY, retry_after=1801))
    async with worker.sessions() as session:
        occurrence = await session.get(ReminderOccurrence, delivery.occurrence_id)
        assert occurrence.status == "expired"
    assert await bot.list(owner) == []


async def test_permanent_chat_failure_disables_active_reminders(stores):
    bot, worker, clock, owner = stores
    await bot.create(owner, uuid4(), clock.schedule(Frequency.DAILY))
    await bot.create(owner, uuid4(), clock.schedule(Frequency.WEEKLY))
    clock.step(minutes=1)
    await worker.plan()
    delivery = await worker.claim()
    assert await worker.authorize(delivery)
    await worker.complete(delivery, DeliveryResult(DeliveryStatus.BLOCKED))
    assert await worker.claim() is None
    assert await bot.list(owner) == []
