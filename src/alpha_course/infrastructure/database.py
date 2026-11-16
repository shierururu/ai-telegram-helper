"""Database connectivity checks; these do not imply corpus readiness."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alpha_course.infrastructure.config import Settings

EXPECTED_REVISION = "0006_weekly_summary"


def create_database_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url.get_secret_value(), pool_pre_ping=True, hide_parameters=True
    )


async def check_database(engine: AsyncEngine) -> None:
    """Check connectivity and the exact schema revision, independently of corpus data."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
        revisions = (
            (await connection.execute(text("SELECT version_num FROM app.alembic_version")))
            .scalars()
            .all()
        )
        if revisions != [EXPECTED_REVISION]:
            raise RuntimeError("database_schema_revision_mismatch")
