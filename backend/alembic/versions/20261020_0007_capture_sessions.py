"""Phase 9E server-owned capture sessions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "20261020_0007"
down_revision = "20261020_0006"
branch_labels = None
depends_on = None
TS = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade() -> None:
    op.create_table("capture_sessions", sa.Column("id", sa.String(36), primary_key=True), sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False), sa.Column("zone_id", sa.String(36), sa.ForeignKey("zones.id", ondelete="RESTRICT"), nullable=False), sa.Column("status", sa.String(16), nullable=False, server_default="active"), sa.Column("started_at", TS, nullable=False), sa.Column("stopped_at", TS), sa.Column("expires_at", TS, nullable=False), sa.Column("stop_reason", sa.String(64)), sa.CheckConstraint("status IN ('active','stopped','expired')", name="ck_capture_sessions_status"), mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci")
    op.create_index("ix_capture_sessions_user_id", "capture_sessions", ["user_id"])
    op.create_index("ix_capture_sessions_zone_id", "capture_sessions", ["zone_id"])
    op.create_index("ix_capture_sessions_user_status", "capture_sessions", ["user_id", "status"])
    op.create_index("ix_capture_sessions_zone_status", "capture_sessions", ["zone_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_capture_sessions_zone_status", table_name="capture_sessions")
    op.drop_index("ix_capture_sessions_user_status", table_name="capture_sessions")
    op.drop_index("ix_capture_sessions_zone_id", table_name="capture_sessions")
    op.drop_index("ix_capture_sessions_user_id", table_name="capture_sessions")
    op.drop_table("capture_sessions")
