"""Opt-in checks against a migrated, isolated PostgreSQL database."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from alpha_course.infrastructure.database import check_database

pytestmark = pytest.mark.integration


@pytest.fixture
async def engines():
    names = (
        "ALFA_TEST_DATABASE_URL",
        "ALFA_TEST_BOT_DATABASE_URL",
        "ALFA_TEST_WORKER_DATABASE_URL",
    )
    if not all(os.environ.get(name) for name in names):
        pytest.skip("isolated PostgreSQL test credentials are not configured")
    values = tuple(create_async_engine(os.environ[name], hide_parameters=True) for name in names)
    try:
        yield values
    finally:
        for engine in values:
            await engine.dispose()


async def test_revision_and_empty_corpus(engines):
    migrator, bot, worker = engines
    for engine in engines:
        await check_database(engine)
    async with migrator.connect() as connection:
        assert (
            int((await connection.execute(text("SHOW server_version_num"))).scalar_one()) // 10000
            == 17
        )
    async with bot.connect() as connection:
        assert (
            await connection.execute(text("SELECT count(*) FROM kb.corpus_revisions"))
        ).scalar_one() == 0
    async with worker.connect() as connection:
        assert (await connection.execute(text("SELECT current_user"))).scalar_one() == "alfa_worker"


async def test_bot_can_manage_synthetic_profile(engines):
    _, bot, _ = engines
    profile_id = uuid4()
    # A negative generated identifier clearly marks this as synthetic.
    telegram_id = -(profile_id.int % (2**62) + 1)
    async with bot.begin() as connection:
        await connection.execute(
            text("INSERT INTO app.profiles (id, telegram_user_id) VALUES (:id, :telegram_id)"),
            {"id": profile_id, "telegram_id": telegram_id},
        )
        await connection.execute(
            text("UPDATE app.profiles SET sector = :sector, version = version + 1 WHERE id = :id"),
            {"id": profile_id, "sector": "synthetic test"},
        )
        assert (
            await connection.execute(
                text("SELECT version FROM app.profiles WHERE id = :id"), {"id": profile_id}
            )
        ).scalar_one() == 2
        await connection.execute(
            text("DELETE FROM app.profiles WHERE id = :id"), {"id": profile_id}
        )


@pytest.mark.parametrize(
    ("role_index", "statement"),
    [
        (1, "CREATE TABLE app.forbidden_test (id integer)"),
        (1, "CREATE SCHEMA forbidden_test"),
        (1, "CREATE TABLE public.forbidden_test (id integer)"),
        (1, "INSERT INTO kb.corpus_revisions (id) VALUES (NULL)"),
        (2, "SELECT * FROM app.profiles"),
        (2, "SELECT * FROM kb.corpus_revisions"),
        (2, "CREATE TABLE app.forbidden_test (id integer)"),
        (2, "CREATE SCHEMA forbidden_test"),
        (2, "CREATE TABLE public.forbidden_test (id integer)"),
    ],
)
async def test_runtime_permissions_are_enforced(engines, role_index, statement):
    async with engines[role_index].connect() as connection:
        with pytest.raises(DBAPIError) as error:
            await connection.execute(text(statement))
        assert error.value.orig.sqlstate == "42501"
        await connection.rollback()


async def test_profile_version_constraint(engines):
    _, bot, _ = engines
    async with bot.connect() as connection:
        with pytest.raises(DBAPIError) as error:
            await connection.execute(
                text(
                    "INSERT INTO app.profiles (id, telegram_user_id, version) VALUES (:id, :tid, 0)"
                ),
                {"id": uuid4(), "tid": -(uuid4().int % (2**62) + 1)},
            )
        assert error.value.orig.sqlstate == "23514"
        await connection.rollback()
