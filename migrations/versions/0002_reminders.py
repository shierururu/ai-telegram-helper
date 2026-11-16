"""Persist user-confirmed reminders, their queue, and delivery attempts."""

import sqlalchemy as sa
from alembic import op

revision = "0002_reminders"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reminders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("event_text", sa.Text(), nullable=False),
        sa.Column("frequency", sa.Text(), nullable=False),
        sa.Column("local_time", sa.Time(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("weekday", sa.Integer()),
        sa.Column("next_due_utc", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("rules_version", sa.Integer(), nullable=False),
        sa.Column("confirmed_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "operation_id", name="reminder_operation_unique"),
        schema="app",
    )
    op.create_index(
        "reminder_due",
        "reminders",
        ["next_due_utc"],
        schema="app",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "reminder_occurrences",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("reminder_id", sa.Uuid(), sa.ForeignKey("app.reminders.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("due_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until_utc", sa.DateTime(timezone=True)),
        sa.Column("token", sa.Uuid()),
        sa.UniqueConstraint("reminder_id", "version", "due_utc", name="reminder_occurrence_unique"),
        schema="app",
    )
    op.create_index(
        "reminder_occurrence_queue",
        "reminder_occurrences",
        ["status", "next_attempt_utc", "lease_until_utc"],
        schema="app",
    )
    op.create_table(
        "reminder_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "occurrence_id",
            sa.Uuid(),
            sa.ForeignKey("app.reminder_occurrences.id"),
            nullable=False,
        ),
        sa.Column("started_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at_utc", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.Text()),
        sa.Column("message_id", sa.BigInteger()),
        schema="app",
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.reminders TO alfa_bot")
    op.execute("GRANT SELECT, UPDATE ON app.reminder_occurrences TO alfa_bot")
    op.execute("GRANT SELECT, UPDATE ON app.reminders TO alfa_worker")
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.reminder_occurrences TO alfa_worker")
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.reminder_attempts TO alfa_worker")


def downgrade():
    op.drop_table("reminder_attempts", schema="app")
    op.drop_table("reminder_occurrences", schema="app")
    op.drop_table("reminders", schema="app")
