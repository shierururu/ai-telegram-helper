"""Shared safe process boundary and technical database check."""

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable

from alpha_course.infrastructure.config import ConfigurationError, Settings, load_settings
from alpha_course.infrastructure.database import check_database, create_database_engine
from alpha_course.infrastructure.logging import setup_logging

logger = logging.getLogger(__name__)


async def verify_database(settings: Settings) -> None:
    engine = create_database_engine(settings)
    try:
        await check_database(engine)
    finally:
        await engine.dispose()


async def technical_check(settings: Settings, role: str) -> None:
    await verify_database(settings)
    logger.info(
        "technical_check_ok role=%s schema_ready=true corpus_checked=false "
        "consultation_checked=false scheduler_checked=false",
        role,
    )


def execute(operation: Callable[[Settings], Awaitable[None]]) -> int:
    setup_logging()
    try:
        settings = load_settings()
        setup_logging(settings.log_level)
        loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        asyncio.run(operation(settings), loop_factory=loop_factory)
    except ConfigurationError:
        logger.error("startup_failed code=configuration_error")
        return 2
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        # Neither exception strings nor chained tracebacks are safe diagnostics.
        logger.error("startup_failed code=runtime_error type=%s", type(error).__name__)
        return 2
    return 0
