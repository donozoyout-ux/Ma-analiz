"""Add historical season fixtures, odds, source teams and import receipts."""
from alembic import op
import sqlalchemy as sa

revision = "0011_season_bulk"
down_revision = "0010_active_statistics"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("season_teams",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(35), nullable=False),
        sa.Column("source_name", sa.String(200), nullable=False))
    op.create_table("season_fixtures",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("league", sa.String(24), nullable=False),
        sa.Column("season", sa.String(9), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_season_fixtures_scope", "season_fixtures", ["league", "season"])
    op.create_table("season_odds",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("fixture_id", sa.String(36), sa.ForeignKey("season_fixtures.id"), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False))
    op.create_index("ix_season_odds_fixture_id", "season_odds", ["fixture_id"])
    op.create_table("season_imports",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("league", sa.String(24), nullable=False),
        sa.Column("season", sa.String(9), nullable=False),
        sa.Column("source_url", sa.String(400), nullable=False),
        sa.Column("revision", sa.String(80)),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    # Never destroy user-imported historical seasons through an automatic rollback.
    # A manual, independently approved recovery plan must restore from a
    # database snapshot and reconcile immutable R2 objects.
    raise RuntimeError("SEASON_BULK_DOWNGRADE_REQUIRES_MANUAL_REVIEW")
