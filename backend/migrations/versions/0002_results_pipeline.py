"""Add provider results and settlement fields.

Revision ID: 0002_results_pipeline
Revises: 0001_initial
"""
from alembic import context, op
import sqlalchemy as sa

from app import models  # noqa: F401
from app.models.active import MatchResult
from app.models.metadata import HistoricalIngestDay

revision = "0002_results_pipeline"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def _add(table: str, name: str, column: sa.Column) -> None:
    if name not in _columns(table):
        op.add_column(table, column)


def upgrade() -> None:
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    MatchResult.__table__.create(bind=bind, checkfirst=True)
    HistoricalIngestDay.__table__.create(bind=bind, checkfirst=True)

    for name, column in (
        ("venue", sa.Column("venue", sa.String(200), nullable=True)),
        ("round", sa.Column("round", sa.String(80), nullable=True)),
        ("country", sa.Column("country", sa.String(100), nullable=True)),
        ("sport", sa.Column("sport", sa.String(80), nullable=True)),
    ):
        _add("active_matches", name, column)

    _add("active_odds", "provider", sa.Column("provider", sa.String(80), nullable=False, server_default="unknown"))
    if "provider" in _columns("active_odds"):
        op.alter_column("active_odds", "provider", server_default=None)
    _add("active_odds", "provider_odds_id", sa.Column("provider_odds_id", sa.String(160), nullable=True))

    for name, column in (
        ("decimal_odds", sa.Column("decimal_odds", sa.Numeric(10, 4), nullable=True)),
        ("paper_stake", sa.Column("paper_stake", sa.Numeric(12, 4), nullable=False, server_default="1")),
        ("status", sa.Column("status", sa.String(16), nullable=False, server_default="PENDING")),
        ("paper_return", sa.Column("paper_return", sa.Numeric(12, 4), nullable=True)),
        ("settled_at", sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True)),
    ):
        _add("predictions", name, column)
    for name in ("paper_stake", "status"):
        op.alter_column("predictions", name, server_default=None)

    for name, column in (
        ("paper_stake", sa.Column("paper_stake", sa.Numeric(12, 4), nullable=False, server_default="1")),
        ("paper_profit", sa.Column("paper_profit", sa.Numeric(12, 4), nullable=True)),
        ("settled_at", sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True)),
    ):
        _add("coupons", name, column)
    op.alter_column("coupons", "paper_stake", server_default=None)

    _add("coupon_items", "status", sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"))
    op.alter_column("coupon_items", "status", server_default=None)
    _add("coupon_items", "settled_at", sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True))

    for name, column in (
        ("predictions_pending", sa.Column("predictions_pending", sa.Integer(), nullable=False, server_default="0")),
        ("paper_stake", sa.Column("paper_stake", sa.Numeric(12, 4), nullable=True)),
    ):
        _add("daily_metrics", name, column)
    op.alter_column("daily_metrics", "predictions_pending", server_default=None)
    for name in ("r2_bytes", "postgres_bytes"):
        if name in _columns("daily_metrics"):
            op.alter_column("daily_metrics", name, nullable=True)

    for name, column in (
        ("records_seen", sa.Column("records_seen", sa.BigInteger(), nullable=False, server_default="0")),
        ("records_written", sa.Column("records_written", sa.BigInteger(), nullable=False, server_default="0")),
        ("records_skipped", sa.Column("records_skipped", sa.BigInteger(), nullable=False, server_default="0")),
        ("records_failed", sa.Column("records_failed", sa.BigInteger(), nullable=False, server_default="0")),
        ("error_code", sa.Column("error_code", sa.String(80), nullable=True)),
        ("error_message", sa.Column("error_message", sa.String(240), nullable=True)),
    ):
        _add("job_runs", name, column)
    for name in ("records_seen", "records_written", "records_skipped", "records_failed"):
        op.alter_column("job_runs", name, server_default=None)

    constraints = {item["name"] for item in sa.inspect(bind).get_check_constraints("job_runs")}
    if "ck_job_runs_valid_job_status" not in constraints:
        op.create_check_constraint(
            "ck_job_runs_valid_job_status", "job_runs",
            "status IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'SKIPPED', 'FAILED')",
        )


def downgrade() -> None:
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    constraints = {item["name"] for item in sa.inspect(bind).get_check_constraints("job_runs")}
    if "ck_job_runs_valid_job_status" in constraints:
        op.drop_constraint("ck_job_runs_valid_job_status", "job_runs", type_="check")
    for table, names in (
        ("job_runs", ("records_seen", "records_written", "records_skipped", "records_failed", "error_code", "error_message")),
        ("daily_metrics", ("predictions_pending", "paper_stake")),
        ("coupon_items", ("status", "settled_at")),
        ("coupons", ("paper_stake", "paper_profit", "settled_at")),
        ("predictions", ("decimal_odds", "paper_stake", "status", "paper_return", "settled_at")),
        ("active_odds", ("provider", "provider_odds_id")),
        ("active_matches", ("venue", "round", "country", "sport")),
    ):
        current = _columns(table)
        for name in names:
            if name in current:
                op.drop_column(table, name)
    if sa.inspect(bind).has_table("historical_ingest_days"):
        HistoricalIngestDay.__table__.drop(bind=bind, checkfirst=True)
    if sa.inspect(bind).has_table("match_results"):
        MatchResult.__table__.drop(bind=bind, checkfirst=True)
