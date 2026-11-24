"""Add a bounded operational buffer for licensed technical observations.

No existing table is changed. Prepared only; stage 4 does not execute migrations.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0010_active_statistics"
down_revision = "0009_historical_coverage"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("active_statistics",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("match_id", UUID(as_uuid=True), sa.ForeignKey("active_matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("provider_match_id", sa.String(160), nullable=False),
        sa.Column("provider_statistic_id", sa.String(160), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("unit", sa.String(24), nullable=False),
        sa.Column("period", sa.String(24), nullable=False),
        sa.Column("home_value", sa.Numeric(14, 4), nullable=False),
        sa.Column("away_value", sa.Numeric(14, 4), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("licence", sa.String(100), nullable=False),
        sa.Column("permission_reference", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("match_id", "provider", "provider_statistic_id", "observed_at"))
    for name in ("match_id", "observed_at", "expires_at"):
        op.create_index(f"ix_active_statistics_{name}", "active_statistics", [name])


def downgrade():
    # Deliberately no automatic destructive downgrade of operational observations.
    raise RuntimeError("Archive and review active_statistics before a manual downgrade")
