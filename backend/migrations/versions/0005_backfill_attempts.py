"""Track resumable date checkpoints and their retry attempts.

Revision ID: 0005_backfill_attempts
Revises: 0004_canonical_odds
"""
from alembic import context, op
import sqlalchemy as sa

revision = "0005_backfill_attempts"
down_revision = "0004_canonical_odds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        return
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("historical_ingest_days")}
    if "attempt_count" not in columns:
        op.add_column("historical_ingest_days", sa.Column("attempt_count", sa.Integer(),
                                                            nullable=False, server_default="0"))
    if "last_attempt_at" not in columns:
        op.add_column("historical_ingest_days", sa.Column("last_attempt_at", sa.DateTime(timezone=True),
                                                            nullable=True))
    if "records_failed" not in columns:
        op.add_column("historical_ingest_days", sa.Column("records_failed", sa.BigInteger(),
                                                            nullable=False, server_default="0"))
    if "failed_match_ids" not in columns:
        op.add_column("historical_ingest_days", sa.Column("failed_match_ids", sa.JSON(),
                                                            nullable=False, server_default="[]"))


def downgrade() -> None:
    if context.is_offline_mode():
        return
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("historical_ingest_days")}
    for name in ("failed_match_ids", "records_failed", "last_attempt_at", "attempt_count"):
        if name in columns:
            op.drop_column("historical_ingest_days", name)
