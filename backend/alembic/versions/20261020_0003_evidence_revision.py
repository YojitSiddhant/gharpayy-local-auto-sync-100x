"""Optimistic concurrency: add screenshot_captures.revision.

Additive only. Existing rows get revision 0; no data is rewritten or deleted.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261020_0003"
down_revision = "20261020_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("screenshot_captures", sa.Column("revision", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("screenshot_captures", "revision")
