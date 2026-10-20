"""Evidence review: capture metadata, corrections, review state, trash, audit events.

Additive only: new nullable/defaulted columns and two new tables. No data is rewritten or deleted.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "20261020_0002"
down_revision = "20261020_0001"
branch_labels = None
depends_on = None

TS = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
LONGTEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql")
TABLE_OPTS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}

CAPTURE_COLUMNS = [
    sa.Column("screenshot_label", sa.String(255), nullable=True),
    sa.Column("captured_at", TS, nullable=True),
    sa.Column("source_label", sa.String(255), nullable=True),
    sa.Column("original_width", sa.Integer(), nullable=True),
    sa.Column("original_height", sa.Integer(), nullable=True),
    sa.Column("crop", sa.JSON(), nullable=True),
    sa.Column("corrected_text", LONGTEXT, nullable=True),
    sa.Column("review_state", sa.String(16), nullable=False, server_default="review"),
    sa.Column("reviewed_at", TS, nullable=True),
    sa.Column("reviewed_by", sa.String(120), nullable=True),
    sa.Column("trashed_at", TS, nullable=True),
    sa.Column("trash_reason", sa.String(500), nullable=True),
    sa.Column("trashed_by", sa.String(120), nullable=True),
    sa.Column("region_proposals", sa.JSON(), nullable=True),
    sa.Column("duplicate_hits", sa.Integer(), nullable=False, server_default="0"),
]


def upgrade() -> None:
    for column in CAPTURE_COLUMNS:
        op.add_column("screenshot_captures", column)
    op.create_check_constraint("ck_screenshot_captures_review_state", "screenshot_captures", "review_state IN ('review','reviewed')")
    op.add_column("ocr_row_observations", sa.Column("observed_name", sa.String(255), nullable=True))

    op.create_table(
        "ocr_text_corrections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("capture_id", sa.String(36), sa.ForeignKey("screenshot_captures.id", ondelete="CASCADE"), nullable=False),
        sa.Column("old_text", LONGTEXT, nullable=True),
        sa.Column("new_text", LONGTEXT, nullable=False),
        sa.Column("reviewer", sa.String(120), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        **TABLE_OPTS,
    )
    op.create_index("ix_ocr_text_corrections_capture_id", "ocr_text_corrections", ["capture_id"])

    op.create_table(
        "evidence_audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("capture_id", sa.String(36), nullable=True),
        sa.Column("capture_sha256", sa.String(64), nullable=True),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("reviewer", sa.String(120), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint("action IN ('trash','restore','review','retry','purge')", name="ck_evidence_audit_events_action"),
        **TABLE_OPTS,
    )
    op.create_index("ix_evidence_audit_events_capture_id", "evidence_audit_events", ["capture_id"])


def downgrade() -> None:
    op.drop_table("evidence_audit_events")
    op.drop_table("ocr_text_corrections")
    op.drop_column("ocr_row_observations", "observed_name")
    op.drop_constraint("ck_screenshot_captures_review_state", "screenshot_captures", type_="check")
    for column in reversed(CAPTURE_COLUMNS):
        op.drop_column("screenshot_captures", column.name)
