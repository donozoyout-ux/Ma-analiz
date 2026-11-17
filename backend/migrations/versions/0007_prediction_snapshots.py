"""Persist reproducible prediction inputs and stored analysis decisions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0007_prediction_snapshots"
down_revision = "0006_odds_snapshot_semantics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("predictions", sa.Column("market_probability", sa.Numeric(6, 5), nullable=True))
    op.add_column("predictions", sa.Column("final_score", sa.Numeric(6, 2), nullable=True))
    op.add_column("predictions", sa.Column("risk_level", sa.String(length=16), nullable=True))
    op.add_column("predictions", sa.Column("analysis_timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.add_column("predictions", sa.Column("feature_version", sa.String(length=80), server_default="canonical-features-1", nullable=False))
    op.add_column("predictions", sa.Column("feature_snapshot", sa.Text(), nullable=True))
    op.alter_column("predictions", "analysis_timestamp", server_default=None)
    op.alter_column("predictions", "feature_version", server_default=None)

    op.create_table("match_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("match_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("analysis_status", sa.String(length=24), nullable=False),
        sa.Column("final_decision", sa.String(length=16), nullable=False),
        sa.Column("model_version", sa.String(length=80), nullable=False),
        sa.Column("feature_version", sa.String(length=80), nullable=False),
        sa.Column("analysis_output", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["active_matches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "analysis_timestamp", "model_version"))
    op.create_index("ix_match_analyses_match_id", "match_analyses", ["match_id"])
    op.create_index("ix_match_analyses_analysis_timestamp", "match_analyses", ["analysis_timestamp"])


def downgrade() -> None:
    op.drop_index("ix_match_analyses_analysis_timestamp", table_name="match_analyses")
    op.drop_index("ix_match_analyses_match_id", table_name="match_analyses")
    op.drop_table("match_analyses")
    for column in ("feature_snapshot", "feature_version", "analysis_timestamp", "risk_level", "final_score", "market_probability"):
        op.drop_column("predictions", column)
