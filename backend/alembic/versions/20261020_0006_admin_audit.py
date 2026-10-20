"""Phase 9D authenticated Super Admin audit events."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "20261020_0006"
down_revision = "20261020_0005"
branch_labels = None
depends_on = None
TS = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade() -> None:
    op.create_table("admin_audit_events", sa.Column("id", sa.String(36), primary_key=True), sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False), sa.Column("action", sa.String(64), nullable=False), sa.Column("target_type", sa.String(32), nullable=False), sa.Column("target_id", sa.String(36), nullable=False), sa.Column("details", sa.JSON(), nullable=True), sa.Column("created_at", TS, nullable=False), mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci")
    op.create_index("ix_admin_audit_events_actor_user_id", "admin_audit_events", ["actor_user_id"])
    op.create_index("ix_admin_audit_events_created", "admin_audit_events", ["created_at"])
    op.create_index("ix_admin_audit_events_target", "admin_audit_events", ["target_type", "target_id"])


def downgrade() -> None:
    op.drop_index("ix_admin_audit_events_target", table_name="admin_audit_events")
    op.drop_index("ix_admin_audit_events_created", table_name="admin_audit_events")
    op.drop_index("ix_admin_audit_events_actor_user_id", table_name="admin_audit_events")
    op.drop_table("admin_audit_events")
