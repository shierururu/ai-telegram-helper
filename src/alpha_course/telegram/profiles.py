"""Persistence for the optional business profile."""

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from alpha_course.application.profile import BusinessProfile
from alpha_course.infrastructure.models import Profile

# BusinessProfile field -> app.profiles column.
COLUMNS = {
    "stage": "business_stage",
    "industry": "sector",
    "goal": "task",
    "legal_form": "legal_form",
    "tax_regime": "tax_regime",
    "employees": "employees",
    "level": "depth",
}


class ProfileStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def load(self, owner: int) -> BusinessProfile:
        columns = [getattr(Profile, column) for column in COLUMNS.values()]
        statement = select(*columns).where(Profile.telegram_user_id == owner)
        async with self.engine.connect() as connection:
            row = (await connection.execute(statement)).one_or_none()
        return BusinessProfile(*row) if row else BusinessProfile()

    async def save(self, owner: int, profile: BusinessProfile) -> None:
        values = {column: getattr(profile, name) for name, column in COLUMNS.items()}
        statement = insert(Profile).values(telegram_user_id=owner, **values)
        async with self.engine.begin() as connection:
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[Profile.telegram_user_id],
                    set_={**values, "version": Profile.version + 1},
                )
            )

    async def clear(self, owner: int) -> None:
        """Forget the business details; the onboarding flag stays so the intro is not repeated."""
        values = {column: None for column in COLUMNS.values()}
        async with self.engine.begin() as connection:
            await connection.execute(
                update(Profile)
                .where(Profile.telegram_user_id == owner)
                .values(**values, version=Profile.version + 1)
            )

    async def is_onboarded(self, owner: int) -> bool:
        statement = select(Profile.onboarded).where(Profile.telegram_user_id == owner)
        async with self.engine.connect() as connection:
            return bool((await connection.execute(statement)).scalar_one_or_none())

    async def mark_onboarded(self, owner: int) -> None:
        statement = insert(Profile).values(telegram_user_id=owner, onboarded=True)
        async with self.engine.begin() as connection:
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[Profile.telegram_user_id],
                    set_={"onboarded": True, "version": Profile.version + 1},
                )
            )
