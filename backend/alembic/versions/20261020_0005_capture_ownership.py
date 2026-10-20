"""Phase 9C nullable ownership fields; legacy evidence remains unassigned."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_0005"
down_revision = "20261020_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("screenshot_captures", sa.Column("zone_id", sa.String(36), sa.ForeignKey("zones.id", ondelete="RESTRICT"), nullable=True))
    op.add_column("screenshot_captures", sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True))
    op.add_column("screenshot_captures", sa.Column("capture_session_id", sa.String(36), nullable=True))
    op.create_index("ix_screenshot_captures_zone_id", "screenshot_captures", ["zone_id"])
    op.create_index("ix_screenshot_captures_created_by_user_id", "screenshot_captures", ["created_by_user_id"])
    op.create_index("ix_screenshot_captures_capture_session_id", "screenshot_captures", ["capture_session_id"])
    op.create_index("ix_screenshot_captures_owner", "screenshot_captures", ["created_by_user_id", "zone_id", "received_at"])


def downgrade() -> None:
    op.drop_index("ix_screenshot_captures_owner", table_name="screenshot_captures")
    op.drop_index("ix_screenshot_captures_capture_session_id", table_name="screenshot_captures")
    op.drop_index("ix_screenshot_captures_created_by_user_id", table_name="screenshot_captures")
    op.drop_index("ix_screenshot_captures_zone_id", table_name="screenshot_captures")
    op.drop_column("screenshot_captures", "capture_session_id")
    op.drop_column("screenshot_captures", "created_by_user_id")
    op.drop_column("screenshot_captures", "zone_id")
