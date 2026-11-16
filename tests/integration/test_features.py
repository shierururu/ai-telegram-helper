"""Reminder links, snooze, frequencies, profile persistence and product events on PostgreSQL."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_database import engines as engines

from alpha_course.application.profile import BusinessProfile
from alpha_course.application.reminders import (
    DeliveryResult,
    DeliveryStatus,
    NotFound,
    Schedule,
)
from alpha_course.domain.reminders import ReminderFrequency as Frequency
from alpha_course.infrastructure.events import EventStore, user_key
from alpha_course.infrastructure.reminders import ReminderStore
from alpha_course.telegram.profiles import ProfileStore

pytestmark = pytest.mark.integration
NOW = datetime(2026, 1, 31, 8, tzinfo=UTC)


@pytest.fixture
async def owner(engines):
    migrator, _, _ = engines
    owner = -(uuid4().int % (2**62) + 1)
    yield owner
    async with migrator.begin() as connection:
        params = {"owner": owner, "key": user_key(owner)}
        await connection.execute(
            text(
                "DELETE FROM app.reminder_attempts WHERE occurrence_id IN (SELECT o.id FROM "
                "app.reminder_occurrences o JOIN app.reminders r ON r.id = o.reminder_id "
                "WHERE r.owner_id = :owner)"
            ),
            params,
        )
        await connection.execute(
            text(
                "DELETE FROM app.reminder_occurrences WHERE reminder_id IN "
                "(SELECT id FROM app.reminders WHERE owner_id = :owner)"
            ),
            params,
        )
        await connection.execute(text("DELETE FROM app.reminders WHERE owner_id = :owner"), params)
        await connection.execute(
            text("DELETE FROM app.profiles WHERE telegram_user_id = :owner"), params
        )
        await connection.execute(text("DELETE FROM app.events WHERE user_key = :key"), params)


async def test_link_is_stored_updated_kept_and_delivered(engines, owner):
    _, bot_engine, worker_engine = engines
    bot = ReminderStore(bot_engine, clock=lambda: NOW)
    schedule = Schedule("Урок", datetime(2026, 1, 31, 9, 0), "Etc/UTC", Frequency.DAILY)
    record = await bot.create(
        owner, uuid4(), schedule, link_url="https://example.test/a", link_title="Курс"
    )
    assert (record.link_url, record.link_title) == ("https://example.test/a", "Курс")
    kept = await bot.update(owner, record.id, record.version, schedule)
    assert kept.link_url == "https://example.test/a"
    changed = await bot.update(
        owner, record.id, kept.version, schedule, link_url=None, link_title=None
    )
    assert (changed.link_url, changed.link_title) == (None, None)
    again = await bot.update(
        owner, record.id, changed.version, schedule, link_url="https://example.test/b"
    )
    worker = ReminderStore(worker_engine, clock=lambda: NOW + timedelta(hours=1, minutes=1))
    await worker.plan()
    delivery = await worker.claim()
    assert delivery.link_url == again.link_url == "https://example.test/b"
    assert delivery.link_title is None


async def test_monthly_reminder_keeps_anchor_day_through_worker(engines, owner):
    _, bot_engine, worker_engine = engines
    now = {"value": NOW}
    bot = ReminderStore(bot_engine, clock=lambda: now["value"])
    worker = ReminderStore(worker_engine, clock=lambda: now["value"])
    schedule = Schedule("Налог", datetime(2026, 1, 31, 9, 0), "Etc/UTC", Frequency.MONTHLY)
    record = await bot.create(owner, uuid4(), schedule)
    assert record.month_day == 31
    seen = []
    for target in (
        datetime(2026, 1, 31, 9, 1),
        datetime(2026, 2, 28, 9, 1),
        datetime(2026, 3, 31, 9, 1),
    ):
        now["value"] = target.replace(tzinfo=UTC)
        await worker.plan()
        delivery = await worker.claim()
        assert delivery is not None
        seen.append((await bot.list(owner))[0].next_due_utc)
        assert await worker.authorize(delivery)
        await worker.complete(delivery, DeliveryResult(DeliveryStatus.SENT, message_id=1))
    assert [(d.month, d.day) for d in seen] == [(2, 28), (3, 31), (4, 30)]


async def test_snooze_is_owner_scoped_idempotent_and_keeps_link(engines, owner):
    _, bot_engine, _ = engines
    bot = ReminderStore(bot_engine, clock=lambda: NOW)
    schedule = Schedule("Звонок", datetime(2026, 2, 1, 9, 0), "Europe/Moscow", Frequency.DAILY)
    original = await bot.create(
        owner, uuid4(), schedule, link_url="https://example.test/a", link_title="Курс"
    )
    hour = await bot.snooze(owner, original.id, "h1", "10")
    assert (await bot.snooze(owner, original.id, "h1", "10")).id == hour.id
    assert hour.frequency == "once"
    assert hour.next_due_utc == NOW + timedelta(hours=1)
    assert hour.link_url == "https://example.test/a"
    tomorrow = await bot.snooze(owner, original.id, "d1", "10")
    # 09:00 Moscow time on the next local day (NOW is already 11:00 on Jan 31 there).
    assert tomorrow.next_due_utc == datetime(2026, 2, 1, 6, tzinfo=UTC)
    with pytest.raises(NotFound):
        await bot.snooze(owner - 1, original.id, "h1")
    assert await bot.get(owner - 1, original.id) is None
    assert (await bot.get(owner, original.id)).id == original.id


async def test_profile_round_trip_onboarding_and_clear(engines, owner):
    _, bot_engine, _ = engines
    store = ProfileStore(bot_engine)
    assert (await store.load(owner)).is_empty()
    assert not await store.is_onboarded(owner)
    profile = BusinessProfile(
        stage="запуск",
        industry="услуги",
        goal="привлечь клиентов",
        legal_form="ИП",
        tax_regime="УСН «Доходы»",
        employees=False,
        level="novice",
    )
    await store.save(owner, profile)
    assert await store.load(owner) == profile
    await store.save(owner, BusinessProfile(stage="развитие", employees=True))
    assert await store.load(owner) == BusinessProfile(stage="развитие", employees=True)
    await store.mark_onboarded(owner)
    assert await store.is_onboarded(owner)
    await store.clear(owner)
    assert (await store.load(owner)).is_empty()
    assert await store.is_onboarded(owner)


async def test_events_hash_user_and_summarize(engines, owner):
    migrator, bot_engine, worker_engine = engines
    events, worker_events = EventStore(bot_engine), EventStore(worker_engine)
    await events.log(owner, "answer_shown", material_id="m1", topic="налоги")
    await events.log(owner, "material_click", material_id="m1")
    await worker_events.log(owner, "reminder_delivered")
    await events.log(owner, "reminder_done")
    async with migrator.connect() as connection:
        rows = (
            await connection.execute(
                text("SELECT user_key, payload FROM app.events WHERE user_key = :key"),
                {"key": user_key(owner)},
            )
        ).all()
    assert len(rows) == 4
    assert str(abs(owner)) not in rows[0].user_key and len(rows[0].user_key) == 64
    assert {"topic": "налоги"} in [row.payload for row in rows]
    summary = await events.summary(days=1)
    assert summary["counts"]["reminder_done"] >= 1
    assert summary["users"] >= 1
    assert summary["click_through"] is not None
    assert summary["reminder_done_rate"] is not None


async def test_event_logging_never_raises(owner, caplog):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from sqlalchemy.exc import SQLAlchemyError

    # Inject a connection failure: the worker now legitimately reads weekly facts.
    begin = Mock(side_effect=SQLAlchemyError("simulated connection failure"))
    broken = EventStore(SimpleNamespace(begin=begin))
    await broken.log(owner, "ok", payload_value=object())
    begin.assert_called_once_with()
    assert "event_log_failed kind=ok" in caplog.text
