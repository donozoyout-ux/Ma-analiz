"""Preserve provider market and bookmaker identifiers on odds snapshots.

Revision ID: 0003_provider_market_ids
Revises: 0002_results_pipeline
"""
from alembic import context, op
import sqlalchemy as sa

revision = "0003_provider_market_ids"
down_revision = "0002_results_pipeline"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if context.is_offline_mode():
        return
    for name in ("provider_market_id", "provider_bookmaker_id"):
        if name not in _columns("active_odds"):
            op.add_column("active_odds", sa.Column(name, sa.String(160), nullable=True))


def downgrade() -> None:
    if context.is_offline_mode():
        return
    current = _columns("active_odds")
    for name in ("provider_bookmaker_id", "provider_market_id"):
        if name in current:
            op.drop_column("active_odds", name)
