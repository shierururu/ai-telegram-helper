"""Private Telegram entrypoint."""

import argparse
import asyncio
import logging

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
    MenuButtonWebApp,
    WebAppInfo,
)

from alpha_course.application.assistant import Assistant
from alpha_course.application.knowledge import KnowledgeBase
from alpha_course.entrypoints.common import execute, technical_check
from alpha_course.infrastructure.config import ConfigurationError, Settings
from alpha_course.infrastructure.database import check_database, create_database_engine
from alpha_course.infrastructure.engagement import EngagementStore
from alpha_course.infrastructure.events import EventStore
from alpha_course.infrastructure.experiments import Experiments
from alpha_course.infrastructure.reminders import ReminderStore
from alpha_course.infrastructure.tunnel import watch_tunnel
from alpha_course.infrastructure.yandex import YandexAI
from alpha_course.telegram.engagement import create_engagement_router
from alpha_course.telegram.games import create_games_router
from alpha_course.telegram.menu import create_menu_router
from alpha_course.telegram.profiles import ProfileStore
from alpha_course.telegram.reminder_actions import create_reminder_actions_router
from alpha_course.telegram.router import create_router
from alpha_course.telegram.tools import create_tools_router
from alpha_course.webapp import Services, run_webapp

logger = logging.getLogger(__name__)
COMMANDS = [
    BotCommand(command="start", description="Начать и настроить советы"),
    BotCommand(command="reminders", description="Мои напоминания"),
    BotCommand(command="route", description="План на 7 дней: шаг в день"),
    BotCommand(command="profile", description="Профиль бизнеса"),
    BotCommand(command="progress", description="Мои успехи и серия"),
    BotCommand(command="settings", description="Город и итоги недели"),
    BotCommand(command="help", description="Что умеет бот"),
]


class ActivityMiddleware(BaseMiddleware):
    """Count private user actions without storing their content."""

    def __init__(self, events):
        self.events = events

    async def __call__(self, handler, event, data):
        message = getattr(event, "message", None) or event
        user = getattr(event, "from_user", None)
        chat = getattr(message, "chat", None)
        if user and chat and chat.type == "private" and user.id == chat.id:
            try:
                await self.events.log(user.id, "user_active")
            except Exception:
                logger.warning("event_log_failed")
        return await handler(event, data)


async def run(settings: Settings, *, check: bool = False) -> None:
    if check:
        await technical_check(settings, "bot")
        return
    token = settings.require_bot_token()
    try:
        knowledge = KnowledgeBase.from_path(settings.corpus_path)
    except (OSError, ValueError, KeyError) as error:
        raise ConfigurationError("Knowledge corpus is missing or outdated") from error
    logger.info(
        "knowledge_loaded materials=%s chunks=%s", len(knowledge.materials), len(knowledge.chunks)
    )
    model = None
    if settings.yandex_api_key and (key := settings.yandex_api_key.get_secret_value().strip()):
        model = YandexAI(
            key,
            folder_id=settings.yandex_folder_id,
            timeout_seconds=settings.yandex_timeout_seconds,
            reasoning_effort=getattr(settings, "yandex_reasoning_effort", "none"),
        )
    else:
        logger.warning("yandex_key_missing local_search_only=true")
    vectors_task = None
    if model is not None and not knowledge.load_vectors(settings.index_dir):
        # Build semantic vectors in the background; lexical search works meanwhile.
        vectors_task = asyncio.create_task(knowledge.build_vectors(model, settings.index_dir))
    engine = create_database_engine(settings)
    events = EventStore(engine)
    experiments = Experiments(events, enabled=getattr(settings, "ab_paid_offer", True))
    web_runner = None
    tunnel_task = None
    tunnel_endpoint = getattr(settings, "webapp_tunnel_url", None)
    try:
        await check_database(engine)
        dispatcher = Dispatcher(events_isolation=SimpleEventIsolation())
        dispatcher.message.middleware(ActivityMiddleware(events))
        dispatcher.callback_query.middleware(ActivityMiddleware(events))
        reminders = ReminderStore(engine)
        engagement = EngagementStore(engine)
        dispatcher.include_router(create_reminder_actions_router(reminders, events))
        assistant = Assistant(
            knowledge,
            model,
            deadline_seconds=getattr(settings, "answer_deadline_seconds", 25.0),
        )
        profile_store = ProfileStore(engine)
        dispatcher.include_router(create_menu_router(lambda: settings.webapp_url))
        dispatcher.include_router(create_tools_router(assistant, profile_store, events, model))
        dispatcher.include_router(create_games_router(assistant, profile_store, events, model))
        dispatcher.include_router(
            create_engagement_router(
                engagement, assistant, profile_store, events=events, model=model
            )
        )
        dispatcher.include_router(
            create_router(
                assistant,
                profile_store,
                reminders,
                events=events,
                engagement=engagement,
                model=model,
                admin_ids=settings.admin_set(),
                experiments=experiments,
            )
        )
        if settings.webapp_url or tunnel_endpoint or settings.webapp_dev:
            web_runner = await run_webapp(
                Services(
                    assistant=assistant,
                    profiles=profile_store,
                    reminders=reminders,
                    engagement=engagement,
                    events=events,
                    model=model,
                    experiments=experiments,
                ),
                bot_token=token,
                dev=settings.webapp_dev,
                port=settings.webapp_port,
            )
        async with Bot(
            token=token, default=DefaultBotProperties(link_preview_is_disabled=True)
        ) as bot:
            await bot.delete_my_commands(scope=BotCommandScopeDefault())
            await bot.set_my_commands(COMMANDS, scope=BotCommandScopeAllPrivateChats())
            if settings.webapp_url:
                try:
                    await bot.set_chat_menu_button(
                        menu_button=MenuButtonWebApp(
                            text="Навигатор", web_app=WebAppInfo(url=settings.webapp_url)
                        )
                    )
                except Exception as error:
                    logger.warning("webapp_menu_button_failed error=%s", type(error).__name__)
            elif tunnel_endpoint:
                async def publish_tunnel_url(url: str) -> None:
                    settings.webapp_url = url
                    await bot.set_chat_menu_button(
                        menu_button=MenuButtonWebApp(
                            text="Навигатор", web_app=WebAppInfo(url=url)
                        )
                    )

                tunnel_task = asyncio.create_task(watch_tunnel(tunnel_endpoint, publish_tunnel_url))
            logger.info("bot_started semantic=%s", knowledge.semantic_ready)
            try:
                await dispatcher.start_polling(bot, close_bot_session=False, handle_signals=True)
            finally:
                if tunnel_task is not None:
                    tunnel_task.cancel()
                    await asyncio.gather(tunnel_task, return_exceptions=True)
    finally:
        if web_runner is not None:
            await web_runner.cleanup()
        if vectors_task is not None:
            vectors_task.cancel()
        if model is not None:
            await model.close()
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    return execute(lambda settings: run(settings, check=args.check))


if __name__ == "__main__":
    raise SystemExit(main())
