"""Add private phone fingerprints for cross-zone duplicate detection."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_0008"
down_revision = "20261020_0007"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("ocr_row_observations", sa.Column("phone_fingerprint", sa.String(length=64), nullable=True))
    op.create_index("ix_ocr_row_observations_phone_fingerprint", "ocr_row_observations", ["phone_fingerprint"])

def downgrade() -> None:
    op.drop_index("ix_ocr_row_observations_phone_fingerprint", table_name="ocr_row_observations")
    op.drop_column("ocr_row_observations", "phone_fingerprint")
