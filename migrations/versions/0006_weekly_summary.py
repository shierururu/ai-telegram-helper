"""Voluntary Sunday summaries; existing users remain unsubscribed."""

import sqlalchemy as sa
from alembic import op

revision = "0006_weekly_summary"
down_revision = "0005_legacy_compatibility"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("weekly_summary", "weekly_summary_asked"):
        op.add_column(
            "notification_settings",
            sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()),
            schema="app",
        )
    op.drop_constraint("reminder_kind_known", "reminders", schema="app")
    op.create_check_constraint(
        "reminder_kind_known",
        "reminders",
        "kind IN ('normal', 'journey', 'weekly_summary')",
        schema="app",
    )
    op.create_index(
        "weekly_summary_owner_unique",
        "reminders",
        ["owner_id"],
        unique=True,
        schema="app",
        postgresql_where=sa.text("kind = 'weekly_summary'"),
    )
    op.execute("GRANT SELECT ON app.events TO alfa_worker")


def downgrade():
    raise RuntimeError("weekly_summary_downgrade_requires_restore")
