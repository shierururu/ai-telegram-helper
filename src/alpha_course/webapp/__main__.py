"""Dev entry point: serves only the Mini App (port ALFA_WEBAPP_PORT, default 8080), no polling.

Dev mode is always on here, so `X-Dev-User: <id>` works for direct requests from this machine.
Open http://localhost:8080/?dev=1 to use the front end without Telegram.
"""

import asyncio
import logging

from alpha_course.application.assistant import Assistant
from alpha_course.application.knowledge import KnowledgeBase
from alpha_course.entrypoints.common import execute
from alpha_course.infrastructure.config import ConfigurationError, Settings
from alpha_course.infrastructure.database import check_database, create_database_engine
from alpha_course.infrastructure.engagement import EngagementStore
from alpha_course.infrastructure.events import EventStore
from alpha_course.infrastructure.experiments import Experiments
from alpha_course.infrastructure.lifecycle import idle_until_stopped, stop_on_signals
from alpha_course.infrastructure.reminders import ReminderStore
from alpha_course.infrastructure.yandex import YandexAI
from alpha_course.telegram.profiles import ProfileStore
from alpha_course.webapp import Services, run_webapp

logger = logging.getLogger(__name__)


async def run(settings: Settings) -> None:
    try:
        knowledge = KnowledgeBase.from_path(settings.corpus_path)
    except (OSError, ValueError, KeyError) as error:
        raise ConfigurationError("Knowledge corpus is missing or outdated") from error
    model = None
    if settings.yandex_api_key and (key := settings.yandex_api_key.get_secret_value().strip()):
        model = YandexAI(
            key,
            folder_id=settings.yandex_folder_id,
            timeout_seconds=settings.yandex_timeout_seconds,
            reasoning_effort=settings.yandex_reasoning_effort,
        )
        knowledge.load_vectors(settings.index_dir)
    else:
        logger.warning("yandex_key_missing local_search_only=true")
    engine = create_database_engine(settings)
    runner = None
    try:
        await check_database(engine)
        events = EventStore(engine)
        services = Services(
            assistant=Assistant(
                knowledge, model, deadline_seconds=settings.answer_deadline_seconds
            ),
            profiles=ProfileStore(engine),
            reminders=ReminderStore(engine),
            engagement=EngagementStore(engine),
            events=events,
            model=model,
            experiments=Experiments(events, enabled=getattr(settings, "ab_paid_offer", True)),
        )
        token = settings.telegram_bot_token
        runner = await run_webapp(
            services,
            bot_token=token.get_secret_value().strip() if token else None,
            dev=True,
            port=settings.webapp_port,
            host="127.0.0.1",
        )
        logger.info("webapp_dev_ready url=http://localhost:%s/?dev=1", settings.webapp_port)
        stop = asyncio.Event()
        with stop_on_signals(stop):
            await idle_until_stopped(stop, 3600)
    finally:
        if runner is not None:
            await runner.cleanup()
        if model is not None:
            await model.close()
        await engine.dispose()


def main() -> int:
    return execute(run)


if __name__ == "__main__":
    raise SystemExit(main())
