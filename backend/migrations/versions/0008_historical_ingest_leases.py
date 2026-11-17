"""Add owner and expiry fields for historical ingestion checkpoints."""
from alembic import op
import sqlalchemy as sa


revision = "0008_historical_ingest_leases"
down_revision = "0007_prediction_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("historical_ingest_days", sa.Column("lease_token", sa.String(length=64), nullable=True))
    op.add_column("historical_ingest_days", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("historical_ingest_days", "lease_expires_at")
    op.drop_column("historical_ingest_days", "lease_token")
