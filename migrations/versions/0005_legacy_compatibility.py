"""Join current and retired histories without deleting legacy progress.

Profiles and reminders are upgraded by the current branch. Historical learning
plans, feedback and engagement state remain intact for explicit later recovery.
No payload is guessed, converted, or dropped.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_legacy_compatibility"
down_revision = ("0004_engagement", "0003_engagement")
branch_labels = None
depends_on = None


def upgrade():
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("reminder_occurrences", schema="app")
    }
    if "deferred_until_utc" in columns:
        # Current workers schedule retries by next_attempt_utc. Keep a typed
        # historical deferral effective without erasing its original timestamp.
        op.execute("""
            UPDATE app.reminder_occurrences
            SET next_attempt_utc = GREATEST(next_attempt_utc, deferred_until_utc)
            WHERE deferred_until_utc IS NOT NULL
        """)
    if "engagement_states" in sa.inspect(op.get_bind()).get_table_names(schema="app"):
        # Historical preferences have a different payload contract. Preserve
        # opt-outs conservatively until users explicitly enable current settings.
        # Never overwrite an existing current preference.
        op.execute("""
            INSERT INTO app.notification_settings (owner_id, enabled)
            SELECT owner_id, false FROM app.engagement_states
            ON CONFLICT (owner_id) DO NOTHING
        """)


def downgrade():
    raise RuntimeError("legacy_compatibility_downgrade_requires_restore")
