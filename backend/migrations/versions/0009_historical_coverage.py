"""Persist per-match source coverage independently from download success."""
from alembic import op
import sqlalchemy as sa

revision = "0009_historical_coverage"
down_revision = "0008_historical_ingest_leases"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("historical_ingest_days", sa.Column("coverage", sa.JSON(), nullable=False,
                                                    server_default=sa.text("'{}'")))


def downgrade():
    op.drop_column("historical_ingest_days", "coverage")
