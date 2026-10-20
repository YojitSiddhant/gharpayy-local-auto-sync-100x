"""Phase 9B authentication, users, zones and sessions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "20261020_0004"
down_revision = "20261020_0003"
branch_labels = None
depends_on = None
TS = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
OPTS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}


def upgrade() -> None:
    op.create_table("zones", sa.Column("id", sa.String(36), primary_key=True), sa.Column("name", sa.String(120), nullable=False), sa.Column("slug", sa.String(120), nullable=False), sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"), sa.Column("created_at", TS, nullable=False), sa.Column("updated_at", TS, nullable=False), sa.UniqueConstraint("name", name="uq_zones_name"), sa.UniqueConstraint("slug", name="uq_zones_slug"), **OPTS)
    op.create_table("users", sa.Column("id", sa.String(36), primary_key=True), sa.Column("username", sa.String(120), nullable=False), sa.Column("normalized_username", sa.String(120), nullable=False), sa.Column("display_name", sa.String(160), nullable=False), sa.Column("password_hash", sa.String(512), nullable=False), sa.Column("role", sa.String(32), nullable=False), sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"), sa.Column("zone_id", sa.String(36), sa.ForeignKey("zones.id", ondelete="RESTRICT")), sa.Column("created_at", TS, nullable=False), sa.Column("updated_at", TS, nullable=False), sa.Column("password_changed_at", TS, nullable=False), sa.UniqueConstraint("normalized_username", name="uq_users_normalized_username"), sa.CheckConstraint("role IN ('SUPER_ADMIN','AUTO_SYNC_USER')", name="ck_users_role"), sa.CheckConstraint("(role = 'SUPER_ADMIN' AND zone_id IS NULL) OR (role = 'AUTO_SYNC_USER' AND zone_id IS NOT NULL)", name="ck_users_role_zone"), **OPTS)
    op.create_index("ix_users_zone_id", "users", ["zone_id"])
    op.create_index("ix_users_zone_active", "users", ["zone_id", "is_active"])
    op.create_table("auth_sessions", sa.Column("id", sa.String(36), primary_key=True), sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("token_hash", sa.String(64), nullable=False), sa.Column("created_at", TS, nullable=False), sa.Column("expires_at", TS, nullable=False), sa.Column("idle_expires_at", TS, nullable=False), sa.Column("last_activity_at", TS, nullable=False), sa.Column("revoked_at", TS), sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"), **OPTS)
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_user_active", "auth_sessions", ["user_id", "revoked_at", "expires_at"])


def downgrade() -> None:
    op.drop_table("auth_sessions"); op.drop_index("ix_users_zone_active", table_name="users"); op.drop_index("ix_users_zone_id", table_name="users"); op.drop_table("users"); op.drop_table("zones")
