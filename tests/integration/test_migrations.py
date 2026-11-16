"""Destructive migration tests restricted to newly created synthetic databases.

The opt-in administrator URL must point to a dedicated test PostgreSQL server.
Each test creates and drops only its own UUID-named database.
"""

import importlib.util
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alpha_course.infrastructure.database import EXPECTED_REVISION

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def database_url():
    admin_url = os.environ.get("ALFA_TEST_MIGRATION_ADMIN_URL")
    if not admin_url:
        pytest.skip("isolated migration administrator credentials are not configured")
    name = f"migration_test_{uuid4().hex}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}" OWNER alfa_migrator'))
    url = make_url(admin_url).set(database=name, username="alfa_migrator")
    # This dedicated role password is supplied independently of the admin password.
    url = url.set(password=os.environ["ALFA_TEST_MIGRATION_PASSWORD"])
    try:
        yield url.render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def migrate(url, revision="head"):
    result = subprocess.run(
        ["alembic", "upgrade", revision],
        cwd=ROOT,
        env={**os.environ, "ALFA_DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    # Do not echo process output: driver diagnostics must not escape this test.
    assert result.returncode == 0, "isolated migration process failed"


@pytest.mark.parametrize("starting_revision", ["base", "0004_engagement", "legacy"])
def test_upgrade_preserves_history_and_runtime_grants(database_url, starting_revision):
    engine = create_engine(database_url, hide_parameters=True)
    try:
        if starting_revision != "base":
            migrate(
                database_url,
                "0002_reminders" if starting_revision == "legacy" else starting_revision,
            )
        if starting_revision == "legacy":
            spec = importlib.util.spec_from_file_location(
                "historical_migration", ROOT / "tests/fixtures/legacy_engagement_migration.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)):
                    module.upgrade()
                connection.execute(
                    text("UPDATE app.alembic_version SET version_num = '0003_engagement'")
                )
                connection.execute(
                    text("""
                    INSERT INTO app.profiles (id, telegram_user_id, sector, version)
                    VALUES ('00000000-0000-0000-0000-000000000001', -1, 'synthetic', 3);
                    INSERT INTO app.learning_plans
                    (id, owner_id, operation_id, goal, timezone, local_time, status, steps)
                    VALUES ('00000000-0000-0000-0000-000000000002', -1,
                    '00000000-0000-0000-0000-000000000003', 'synthetic', 'UTC',
                    '10:00', 'active', '[{"done": true,"material_id":"synthetic"}]');
                    INSERT INTO app.reminders
                    (id, owner_id, operation_id, event_text, frequency, local_time,
                    timezone, status, version, rules_version, confirmed_at_utc, kind, plan_id)
                    VALUES ('00000000-0000-0000-0000-000000000004', -1,
                    '00000000-0000-0000-0000-000000000005', 'synthetic', 'daily',
                    '10:00', 'UTC', 'active', 1, 1, now(), 'learning',
                    '00000000-0000-0000-0000-000000000002');
                    INSERT INTO app.reminder_occurrences
                    (id, reminder_id, version, due_utc, status, attempts, next_attempt_utc,
                    deferred_until_utc)
                    VALUES ('00000000-0000-0000-0000-000000000006',
                    '00000000-0000-0000-0000-000000000004', 1, '2030-01-01T10:00Z',
                    'pending', 0, '2030-01-01T10:00Z', '2030-01-02T10:00Z');
                    INSERT INTO app.engagement_answers
                    (id, owner_id, question, answer, vote, payload)
                    VALUES ('00000000-0000-0000-0000-000000000007', -1, 'synthetic',
                    'synthetic', 1, '{"synthetic": true}');
                    INSERT INTO app.engagement_states (owner_id, payload)
                    VALUES (-1, '{"enabled": false}');
                """)
                )
        migrate(database_url)
        migrate(database_url)
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM app.alembic_version")
            ).scalars().all() == [EXPECTED_REVISION]
            assert connection.execute(
                text("SELECT has_table_privilege('alfa_bot','app.journeys','INSERT')")
            ).scalar_one()
            assert connection.execute(
                text(
                    "SELECT has_table_privilege('alfa_worker','app.notification_settings','SELECT')"
                )
            ).scalar_one()
            assert not connection.execute(
                text("SELECT has_table_privilege('alfa_worker','app.profiles','SELECT')")
            ).scalar_one()
            if starting_revision == "legacy":
                assert connection.execute(
                    text("SELECT sector, version, onboarded FROM app.profiles")
                ).one() == ("synthetic", 3, False)
                assert connection.execute(
                    text("SELECT kind, legacy_kind, status, plan_id::text FROM app.reminders")
                ).one() == ("normal", "learning", "active", "00000000-0000-0000-0000-000000000002")
                assert (
                    connection.execute(text("SELECT steps FROM app.learning_plans")).scalar_one()
                    == '[{"done": true,"material_id":"synthetic"}]'
                )
                assert (
                    connection.execute(
                        text("SELECT payload FROM app.engagement_states")
                    ).scalar_one()
                    == '{"enabled": false}'
                )
                assert not connection.execute(
                    text("SELECT enabled FROM app.notification_settings WHERE owner_id=-1")
                ).scalar_one()
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM app.engagement_answers WHERE vote=1")
                    ).scalar_one()
                    == 1
                )
                assert connection.execute(
                    text(
                        "SELECT deferred_until_utc > due_utc AND "
                        "next_attempt_utc >= deferred_until_utc FROM app.reminder_occurrences"
                    )
                ).scalar_one()
    finally:
        engine.dispose()


@pytest.mark.parametrize("failure", ["unknown_revision", "duplicate_table"])
def test_migration_failure_is_safe_and_transactional(database_url, failure):
    migrate(database_url, "0002_reminders")
    engine = create_engine(database_url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            if failure == "unknown_revision":
                connection.execute(
                    text("UPDATE app.alembic_version SET version_num = 'unrecognized_synthetic'")
                )
            else:
                connection.execute(text("CREATE TABLE app.events (secret_synthetic text)"))
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=ROOT,
            env={**os.environ, "ALFA_DATABASE_URL": database_url},
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "synthetic-local" not in output
        assert database_url not in output
        assert "secret_synthetic" not in output
        expected = (
            "unknown_revision" if failure == "unknown_revision" else "database_error:sqlstate=42P07"
        )
        assert f"database_migration_failed:{expected}" in output
        with engine.connect() as connection:
            expected_revision = (
                "unrecognized_synthetic" if failure == "unknown_revision" else "0002_reminders"
            )
            assert (
                connection.execute(text("SELECT version_num FROM app.alembic_version")).scalar_one()
                == expected_revision
            )
            assert not connection.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema='app' AND table_name='profiles' "
                    "AND column_name='legal_form')"
                )
            ).scalar_one()
    finally:
        engine.dispose()


def test_merge_keeps_explicit_current_notification_preferences(database_url):
    migrate(database_url, "0004_engagement")
    engine = create_engine(database_url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE app.engagement_states (owner_id bigint PRIMARY KEY, payload text)"
                )
            )
            connection.execute(
                text("INSERT INTO app.engagement_states VALUES (-2, 'synthetic opaque payload')")
            )
            connection.execute(
                text("INSERT INTO app.notification_settings (owner_id, enabled) VALUES (-2, true)")
            )
        migrate(database_url)
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT enabled FROM app.notification_settings WHERE owner_id=-2")
            ).scalar_one()
            assert connection.execute(
                text(
                    "SELECT weekly_summary, weekly_summary_asked "
                    "FROM app.notification_settings WHERE owner_id=-2"
                )
            ).one() == (False, False)
    finally:
        engine.dispose()
