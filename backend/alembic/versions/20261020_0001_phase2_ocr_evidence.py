"""Phase 2 OCR evidence tables: screenshot captures, OCR results, row observations.

Additive only. No lead/CRM tables. Review-only constraints are enforced in MySQL.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "20261020_0001"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
LONGTEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql")
TABLE_OPTS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}


def upgrade() -> None:
    op.create_table(
        "screenshot_captures",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("account_label", sa.String(90), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=True),
        sa.Column("mime_type", sa.String(32), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("source_mode", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("review_status", sa.String(32), nullable=False),
        sa.Column("cross_account_reuse", sa.Boolean(), nullable=False),
        sa.Column("evidence_storage_key", sa.String(128), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(255), nullable=True),
        sa.Column("received_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("account_label", "sha256", name="uq_screenshot_captures_account_sha256"),
        sa.CheckConstraint("status IN ('processing','completed','failed')", name="ck_screenshot_captures_status"),
        sa.CheckConstraint("review_status = 'unverified_review_only'", name="ck_screenshot_captures_review_only"),
        **TABLE_OPTS,
    )
    op.create_index("ix_screenshot_captures_sha256", "screenshot_captures", ["sha256"])

    op.create_table(
        "ocr_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("capture_id", sa.String(36), sa.ForeignKey("screenshot_captures.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("engine_name", sa.String(32), nullable=False),
        sa.Column("engine_version", sa.String(32), nullable=True),
        sa.Column("ocr_language", sa.String(16), nullable=False),
        sa.Column("tesseract_psm", sa.Integer(), nullable=False),
        sa.Column("preprocessing", sa.JSON(), nullable=False),
        sa.Column("processing_scale", sa.Float(), nullable=True),
        sa.Column("raw_text", LONGTEXT, nullable=True),
        sa.Column("mean_confidence", sa.Float(), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("lines", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("parser_version", sa.String(32), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(255), nullable=True),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("completed_at", TS, nullable=True),
        sa.UniqueConstraint("capture_id", "attempt", name="uq_ocr_results_capture_attempt"),
        sa.CheckConstraint("status IN ('completed','failed')", name="ck_ocr_results_status"),
        **TABLE_OPTS,
    )
    op.create_index("ix_ocr_results_capture_id", "ocr_results", ["capture_id"])

    op.create_table(
        "ocr_row_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ocr_result_id", sa.String(36), sa.ForeignKey("ocr_results.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("display_header_text", sa.String(512), nullable=True),
        sa.Column("phone_candidate_raw", sa.String(64), nullable=True),
        sa.Column("phone_candidate_normalized", sa.String(20), nullable=True),
        sa.Column("phone_status", sa.String(16), nullable=False),
        sa.Column("message_preview", sa.Text(), nullable=True),
        sa.Column("preview_truncated", sa.Boolean(), nullable=False),
        sa.Column("visible_timestamp_text", sa.String(64), nullable=True),
        sa.Column("timestamp_kind", sa.String(32), nullable=True),
        sa.Column("row_left", sa.Integer(), nullable=False),
        sa.Column("row_top", sa.Integer(), nullable=False),
        sa.Column("row_right", sa.Integer(), nullable=False),
        sa.Column("row_bottom", sa.Integer(), nullable=False),
        sa.Column("header_bounds", sa.JSON(), nullable=True),
        sa.Column("ocr_confidence", sa.Float(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("quarantined", sa.Boolean(), nullable=False),
        sa.Column("identity_status", sa.String(16), nullable=False),
        sa.Column("review_status", sa.String(16), nullable=False),
        sa.Column("sender_direction", sa.String(16), nullable=False),
        sa.Column("source_lines", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("ocr_result_id", "position", name="uq_ocr_row_observations_result_position"),
        sa.CheckConstraint("phone_status IN ('candidate','ambiguous','conflict','none')", name="ck_ocr_row_observations_phone_status"),
        sa.CheckConstraint("identity_status = 'unverified'", name="ck_ocr_row_observations_identity_unverified"),
        sa.CheckConstraint("review_status = 'needs_review'", name="ck_ocr_row_observations_review"),
        sa.CheckConstraint("sender_direction = 'unknown'", name="ck_ocr_row_observations_sender_unknown"),
        **TABLE_OPTS,
    )
    op.create_index("ix_ocr_row_observations_ocr_result_id", "ocr_row_observations", ["ocr_result_id"])
    op.create_index("ix_ocr_row_observations_phone_candidate_normalized", "ocr_row_observations", ["phone_candidate_normalized"])


def downgrade() -> None:
    op.drop_table("ocr_row_observations")
    op.drop_table("ocr_results")
    op.drop_table("screenshot_captures")
