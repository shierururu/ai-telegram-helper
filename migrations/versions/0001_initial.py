"""Initial runtime schemas and least-privilege grants; no corpus is seeded."""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SCHEMA kb")
    op.create_table(
        "profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("business_stage", sa.Text()),
        sa.Column("sector", sa.Text()),
        sa.Column("task", sa.Text()),
        sa.Column("depth", sa.Text()),
        sa.Column("timezone", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("version >= 1", name="profile_version_positive"),
        schema="app",
    )
    op.create_table(
        "corpus_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("source_checksum", sa.Text(), nullable=False),
        sa.Column("transformation_version", sa.Text(), nullable=False),
        schema="kb",
    )
    op.execute("REVOKE ALL ON SCHEMA app, kb FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA app TO alfa_bot, alfa_worker")
    op.execute("GRANT USAGE ON SCHEMA kb TO alfa_bot")
    op.execute("GRANT SELECT ON app.alembic_version TO alfa_bot, alfa_worker")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON app.profiles TO alfa_bot")
    op.execute("GRANT SELECT ON kb.corpus_revisions TO alfa_bot")


def downgrade():
    op.drop_table("corpus_revisions", schema="kb")
    op.drop_table("profiles", schema="app")
    op.execute("DROP SCHEMA kb")
    op.execute("REVOKE ALL ON app.alembic_version FROM alfa_bot, alfa_worker")
    op.execute("REVOKE USAGE ON SCHEMA app FROM alfa_bot, alfa_worker")
    # app remains because Alembic must update its version table after downgrade.
