"""Persistent source-reported corner/card observations, independent from active match retention."""
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HistoricalDisciplineMatch(Base):
    __tablename__ = "historical_discipline_matches"
    __table_args__ = (
        CheckConstraint("league_id IN ('en.pl','de.bl1','es.ll')", name="discipline_known_league"),
        CheckConstraint("season IN ('2024-2025','2025-2026','2026-2027')", name="discipline_known_season"),
        CheckConstraint("source_key ~ '^[0-9a-f]{64}$'", name="discipline_source_key_hash"),
        CheckConstraint("source_file_sha256 ~ '^[0-9a-f]{64}$'", name="discipline_file_hash"),
        CheckConstraint("r2_archive_checksum ~ '^[0-9a-f]{64}$'", name="discipline_r2_hash"),
        CheckConstraint(
            "home_corners IS NOT NULL OR away_corners IS NOT NULL OR "
            "home_yellows IS NOT NULL OR away_yellows IS NOT NULL OR "
            "home_reds IS NOT NULL OR away_reds IS NOT NULL",
            name="discipline_any_stat",
        ),
        *(CheckConstraint(f"{field} IS NULL OR ({field} BETWEEN 0 AND 100)", name=f"discipline_{field}_range")
          for field in ("home_corners", "away_corners", "home_yellows", "away_yellows", "home_reds", "away_reds")),
    )

    source_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    r2_archive_key: Mapped[str] = mapped_column(String(600), nullable=False, index=True)
    r2_archive_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    permission_reference: Mapped[str] = mapped_column(Text, nullable=False)
    league_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    league_name: Mapped[str] = mapped_column(String(80), nullable=False)
    season: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    match_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    home_team: Mapped[str] = mapped_column(String(150), nullable=False)
    away_team: Mapped[str] = mapped_column(String(150), nullable=False)
    home_corners: Mapped[int | None] = mapped_column(Integer)
    away_corners: Mapped[int | None] = mapped_column(Integer)
    home_yellows: Mapped[int | None] = mapped_column(Integer)
    away_yellows: Mapped[int | None] = mapped_column(Integer)
    home_reds: Mapped[int | None] = mapped_column(Integer)
    away_reds: Mapped[int | None] = mapped_column(Integer)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
