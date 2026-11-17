"""Persist provider opening/latest snapshot labels and source raw value."""
from alembic import op
import sqlalchemy as sa


revision = "0006_odds_snapshot_semantics"
down_revision = "0005_backfill_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("active_odds", sa.Column("snapshot_type", sa.String(length=16), nullable=False, server_default="OBSERVED"))
    op.add_column("active_odds", sa.Column("raw_value", sa.Text(), nullable=True))
    op.drop_constraint("uq_active_odds_snapshot_natural", "active_odds", type_="unique")
    op.create_unique_constraint("uq_active_odds_snapshot_natural", "active_odds",
        ["match_id", "bookmaker", "market", "selection", "line_key", "snapshot_type", "captured_at"])
    op.alter_column("active_odds", "snapshot_type", server_default=None)


def downgrade() -> None:
    op.drop_constraint("uq_active_odds_snapshot_natural", "active_odds", type_="unique")
    op.create_unique_constraint("uq_active_odds_snapshot_natural", "active_odds",
        ["match_id", "bookmaker", "market", "selection", "line_key", "captured_at"])
    op.drop_column("active_odds", "raw_value")
    op.drop_column("active_odds", "snapshot_type")
