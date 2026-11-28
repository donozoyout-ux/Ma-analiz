"""Dedicated historical tables outside the operational retention lifecycle."""
from sqlalchemy import Column, DateTime, ForeignKey, Index, JSON, String, Table
from app.db.base import Base

season_fixtures = Table(
    "season_fixtures", Base.metadata,
    Column("id", String(36), primary_key=True),
    Column("league", String(24), nullable=False),
    Column("season", String(9), nullable=False),
    Column("data", JSON, nullable=False),
    Column("verified_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_season_fixtures_scope", "league", "season"),
)

season_teams = Table(
    "season_teams", Base.metadata,
    Column("id", String(36), primary_key=True),
    Column("provider", String(35), nullable=False),
    Column("source_name", String(200), nullable=False),
)

season_odds = Table(
    "season_odds", Base.metadata,
    Column("id", String(64), primary_key=True),
    Column("fixture_id", ForeignKey("season_fixtures.id"), nullable=False, index=True),
    Column("data", JSON, nullable=False),
)

season_imports = Table(
    "season_imports", Base.metadata,
    Column("id", String(64), primary_key=True),
    Column("league", String(24), nullable=False),
    Column("season", String(9), nullable=False),
    Column("source_url", String(400), nullable=False),
    Column("revision", String(80)),
    Column("receipt", JSON, nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=False),
)

SEASON_TABLES = (season_teams, season_fixtures, season_odds, season_imports)
