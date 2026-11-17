"""Add bookmaker entities and canonical odds snapshot semantics.

Revision ID: 0004_canonical_odds
Revises: 0003_provider_market_ids
"""
from alembic import context, op
import sqlalchemy as sa

from app.models.active import Bookmaker

revision = "0004_canonical_odds"
down_revision = "0003_provider_market_ids"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _unique_constraints(table: str) -> set[str | None]:
    return {item.get("name") for item in sa.inspect(op.get_bind()).get_unique_constraints(table)}


def upgrade() -> None:
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    Bookmaker.__table__.create(bind=bind, checkfirst=True)
    columns = _columns("active_odds")
    additions = (
        ("bookmaker_entity_id", sa.Column("bookmaker_entity_id", sa.Uuid(),
             sa.ForeignKey("bookmakers.id"), nullable=True)),
        ("line", sa.Column("line", sa.Numeric(8, 3), nullable=True)),
        ("raw_line", sa.Column("raw_line", sa.String(80), nullable=True)),
        ("line_key", sa.Column("line_key", sa.String(80), nullable=False, server_default="")),
        ("source", sa.Column("source", sa.String(80), nullable=False, server_default="unknown")),
        ("period", sa.Column("period", sa.String(16), nullable=False, server_default="UNKNOWN")),
        ("status", sa.Column("status", sa.String(24), nullable=False, server_default="AVAILABLE")),
    )
    for name, column in additions:
        if name not in columns:
            op.add_column("active_odds", column)

    unique = _unique_constraints("active_odds")
    if "uq_active_odds_match_id" in unique:
        op.drop_constraint("uq_active_odds_match_id", "active_odds", type_="unique")
    unique = _unique_constraints("active_odds")
    if "uq_active_odds_snapshot_natural" not in unique:
        op.create_unique_constraint(
            "uq_active_odds_snapshot_natural", "active_odds",
            ["match_id", "bookmaker", "market", "selection", "line_key", "captured_at"],
        )


def downgrade() -> None:
    if context.is_offline_mode():
        return
    columns = _columns("active_odds")
    unique = _unique_constraints("active_odds")
    if "uq_active_odds_snapshot_natural" in unique:
        op.drop_constraint("uq_active_odds_snapshot_natural", "active_odds", type_="unique")
    if "uq_active_odds_match_id" not in _unique_constraints("active_odds"):
        op.create_unique_constraint("uq_active_odds_match_id", "active_odds",
                                    ["match_id", "bookmaker", "market", "selection", "captured_at"])
    for name in ("status", "period", "source", "line_key", "raw_line", "line", "bookmaker_entity_id"):
        if name in columns:
            op.drop_column("active_odds", name)
    Bookmaker.__table__.drop(bind=op.get_bind(), checkfirst=True)
