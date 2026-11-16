"""Monthly/yearly reminders, reminder links, full business profile and product events."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_events_links_profile"
down_revision = "0002_reminders"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("reminders", sa.Column("month_day", sa.Integer()), schema="app")
    op.add_column("reminders", sa.Column("link_url", sa.Text()), schema="app")
    op.add_column("reminders", sa.Column("link_title", sa.Text()), schema="app")
    op.create_check_constraint(
        "reminder_frequency_known",
        "reminders",
        "frequency IN ('once', 'daily', 'weekly', 'monthly', 'yearly')",
        schema="app",
    )
    op.create_check_constraint(
        "reminder_month_day_range",
        "reminders",
        "month_day IS NULL OR month_day BETWEEN 1 AND 31",
        schema="app",
    )
    op.add_column("profiles", sa.Column("legal_form", sa.Text()), schema="app")
    op.add_column("profiles", sa.Column("tax_regime", sa.Text()), schema="app")
    op.add_column("profiles", sa.Column("employees", sa.Boolean()), schema="app")
    op.add_column(
        "profiles",
        sa.Column("onboarded", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="app",
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("user_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("material_id", sa.Text()),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema="app",
    )
    op.create_index("event_kind_time", "events", ["kind", "created_at"], schema="app")
    op.create_index("event_user_time", "events", ["user_key", "created_at"], schema="app")
    op.execute("GRANT SELECT, INSERT ON app.events TO alfa_bot")
    op.execute("GRANT INSERT ON app.events TO alfa_worker")


def downgrade():
    op.drop_table("events", schema="app")
    for column in ("onboarded", "employees", "tax_regime", "legal_form"):
        op.drop_column("profiles", column, schema="app")
    op.drop_constraint("reminder_month_day_range", "reminders", schema="app")
    op.drop_constraint("reminder_frequency_known", "reminders", schema="app")
    for column in ("link_title", "link_url", "month_day"):
        op.drop_column("reminders", column, schema="app")
