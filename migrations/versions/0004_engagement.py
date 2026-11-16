"""Notification limits and the seven-day learning journey."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_engagement"
down_revision = "0003_events_links_profile"
branch_labels = None
depends_on = None


def upgrade():
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("reminders", schema="app")
    }
    if "kind" in columns:
        if "learning_plans" not in sa.inspect(op.get_bind()).get_table_names(schema="app"):
            raise RuntimeError("legacy_engagement_schema_unrecognized")
        # Preserve historical classification verbatim. Legacy plan payloads have
        # a different contract and must not masquerade as current journeys.
        op.alter_column("reminders", "kind", new_column_name="legacy_kind", schema="app")
    op.add_column(
        "reminders",
        sa.Column("kind", sa.Text(), nullable=False, server_default="normal"),
        schema="app",
    )
    op.create_check_constraint(
        "reminder_kind_known", "reminders", "kind IN ('normal', 'journey')", schema="app"
    )
    op.create_table(
        "notification_settings",
        sa.Column("owner_id", sa.BigInteger(), primary_key=True),
        sa.Column("timezone", sa.Text()),
        sa.Column("quiet_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("quiet_start", sa.Time(), nullable=False, server_default=sa.text("'22:00'")),
        sa.Column("quiet_end", sa.Time(), nullable=False, server_default=sa.text("'08:00'")),
        sa.Column("weekly_limit", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint("weekly_limit BETWEEN 0 AND 50", name="notification_weekly_limit_range"),
        schema="app",
    )
    op.create_table(
        "journeys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("local_time", sa.Time(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("steps", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("owner_id", "operation_id", name="journey_operation_unique"),
        sa.CheckConstraint("status IN ('active', 'stopped', 'done')", name="journey_status_known"),
        schema="app",
    )
    op.create_index("journey_owner", "journeys", ["owner_id", "created_at"], schema="app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.notification_settings TO alfa_bot")
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.journeys TO alfa_bot")
    op.execute("GRANT SELECT ON app.notification_settings TO alfa_worker")


def downgrade():
    op.drop_table("journeys", schema="app")
    op.drop_table("notification_settings", schema="app")
    op.drop_constraint("reminder_kind_known", "reminders", schema="app")
    op.drop_column("reminders", "kind", schema="app")
