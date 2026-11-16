"""Poll persisted reminders and deliver authorized occurrences to Telegram."""

import argparse
import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace

from aiogram import Bot
from sqlalchemy.exc import SQLAlchemyError

from alpha_course.application.reminders import DeliveryResult, DeliveryStatus
from alpha_course.entrypoints.common import execute, technical_check
from alpha_course.infrastructure.config import Settings
from alpha_course.infrastructure.database import check_database, create_database_engine
from alpha_course.infrastructure.events import EventStore
from alpha_course.infrastructure.lifecycle import stop_on_signals
from alpha_course.infrastructure.reminder_delivery import send_reminder
from alpha_course.infrastructure.reminders import BATCH_SIZE, Delivery, ReminderStore

logger = logging.getLogger(__name__)


async def poll(
    store: ReminderStore,
    send: Callable[[Delivery], Awaitable[DeliveryResult]],
    stop: asyncio.Event,
    interval: float,
    events: EventStore | None = None,
) -> None:
    while not stop.is_set():
        try:
            await store.plan()
            for _ in range(BATCH_SIZE):
                if stop.is_set():
                    break
                delivery = await store.claim()
                if delivery is None:
                    break
                if delivery.kind == "weekly_summary":
                    if events is None:
                        continue
                    progress = await events.weekly_progress(
                        delivery.owner_id, delivery.timezone, store.clock()
                    )
                    delivery = replace(delivery, event_text=progress.format_text())
                if await store.authorize(delivery):
                    result = await send(delivery)
                    await store.complete(delivery, result)
                    if events is not None and result.status == DeliveryStatus.SENT:
                        await events.log(
                            delivery.owner_id,
                            "weekly_summary_delivered"
                            if delivery.kind == "weekly_summary"
                            else "reminder_delivered",
                        )
                    logger.info("reminder_attempt outcome=%s", result.status)
        except SQLAlchemyError:
            logger.warning("reminder_poll_failed code=database_error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


async def run(settings: Settings, *, check: bool = False) -> None:
    if check:
        await technical_check(settings, "worker")
        return
    token = settings.require_bot_token()
    engine = create_database_engine(settings)
    try:
        await check_database(engine)
        async with Bot(token=token) as bot:
            stop = asyncio.Event()
            with stop_on_signals(stop):
                await poll(
                    ReminderStore(engine),
                    lambda delivery: send_reminder(bot, delivery),
                    stop,
                    settings.worker_poll_interval_seconds,
                    EventStore(engine),
                )
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    return execute(lambda settings: run(settings, check=args.check))


if __name__ == "__main__":
    raise SystemExit(main())
