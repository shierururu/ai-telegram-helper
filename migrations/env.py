"""Run migrations with the same ALFA_DATABASE_URL contract as runtime."""

import asyncio
import re

from alembic import context
from alembic.util.exc import CommandError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from alpha_course.infrastructure.config import ConfigurationError, load_settings
from alpha_course.infrastructure.models import Base


def run_migrations(connection):
    # Bootstrap the version schema before Alembic attempts to create its table.
    # Keep schema creation and the migration/version update in one transaction.
    with connection.begin():
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            version_table_schema="app",
        )
        with context.begin_transaction():
            context.run_migrations()


async def run_online():
    engine = create_async_engine(
        load_settings().database_url.get_secret_value(), hide_parameters=True
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline_migrations_not_supported")
try:
    asyncio.run(run_online())
except ConfigurationError as error:
    raise RuntimeError(str(error)) from None
except Exception as error:
    # Never render driver messages, SQL, parameters or connection details.
    if isinstance(error, DBAPIError):
        state = getattr(error.orig, "sqlstate", None)
        suffix = f":sqlstate={state}" if re.fullmatch(r"[0-9A-Z]{5}", state or "") else ""
        code = f"database_migration_failed:database_error{suffix}"
    elif isinstance(error, CommandError) and "Can't locate revision" in str(error):
        code = "database_migration_failed:unknown_revision"
    else:
        code = "database_migration_failed:internal_error"
    raise RuntimeError(code) from None
