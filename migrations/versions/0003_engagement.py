"""Recognize the retired engagement revision without replaying retired features.

Only databases produced by the historical branch carry this revision. Its original
DDL is intentionally not replayed on current installations. The merge revision
retains existing legacy tables; it does not claim to translate their payloads.
"""

revision = "0003_engagement"
down_revision = "0002_reminders"
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    raise RuntimeError("legacy_engagement_downgrade_requires_restore")
