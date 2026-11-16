"""Opt-in learning, feedback and notification preferences."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_engagement"
down_revision = "0002_reminders"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "reminders",
        sa.Column("kind", sa.Text(), nullable=False, server_default="normal"),
        schema="app",
    )
    op.add_column("reminders", sa.Column("plan_id", sa.Uuid()), schema="app")
    op.add_column(
        "reminder_occurrences",
        sa.Column("deferred_until_utc", sa.DateTime(timezone=True)),
        schema="app",
    )
    op.create_table(
        "engagement_states",
        sa.Column("owner_id", sa.BigInteger(), primary_key=True),
        sa.Column("payload", sa.Text(), nullable=False),
        schema="app",
    )
    op.create_table(
        "learning_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("local_time", sa.Time(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("steps", sa.Text(), nullable=False),
        sa.UniqueConstraint("owner_id", "operation_id"),
        schema="app",
    )
    op.create_table(
        "engagement_answers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("vote", sa.Integer()),
        sa.Column("payload", JSONB(), nullable=False),
        schema="app",
    )
    for table in ("engagement_states", "learning_plans", "engagement_answers"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON app.{table} TO alfa_bot")
    op.execute("GRANT SELECT ON app.engagement_states TO alfa_worker")


def downgrade():
    for table in ("engagement_answers", "learning_plans", "engagement_states"):
        op.drop_table(table, schema="app")
    op.drop_column("reminder_occurrences", "deferred_until_utc", schema="app")
    op.drop_column("reminders", "plan_id", schema="app")
    op.drop_column("reminders", "kind", schema="app")
