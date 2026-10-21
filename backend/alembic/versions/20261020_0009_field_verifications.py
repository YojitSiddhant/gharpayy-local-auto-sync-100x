"""Add human field-level OCR truth without changing existing evidence."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_0009"
down_revision = "20261020_0008"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "ocr_field_verifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("observation_id", sa.String(36), sa.ForeignKey("ocr_row_observations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("field_name", sa.String(16), nullable=False),
        sa.Column("observed_value", sa.Text(), nullable=True),
        sa.Column("verified_value", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("dataset_split", sa.String(16), nullable=False, server_default="unassigned"),
        sa.Column("approved_for_dataset", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("reviewer", sa.String(120), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("field_name IN ('phone','message','timestamp')", name="ck_ocr_field_verifications_field"),
        sa.CheckConstraint("status IN ('verified','missing','unreadable','incorrect')", name="ck_ocr_field_verifications_status"),
        sa.CheckConstraint("dataset_split IN ('unassigned','training','evaluation')", name="ck_ocr_field_verifications_split"),
        sa.UniqueConstraint("observation_id", "field_name", name="uq_ocr_field_verifications_observation_field"),
    )
    op.create_index("ix_ocr_field_verifications_observation_id", "ocr_field_verifications", ["observation_id"])
    op.create_table(
        "ocr_field_verification_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("verification_id", sa.String(36), sa.ForeignKey("ocr_field_verifications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("reviewer", sa.String(120), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ocr_field_verification_audits_verification_id", "ocr_field_verification_audits", ["verification_id"])

def downgrade() -> None:
    op.drop_index("ix_ocr_field_verification_audits_verification_id", table_name="ocr_field_verification_audits")
    op.drop_table("ocr_field_verification_audits")
    op.drop_index("ix_ocr_field_verifications_observation_id", table_name="ocr_field_verifications")
    op.drop_table("ocr_field_verifications")
