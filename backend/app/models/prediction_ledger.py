"""Durable records deliberately have no foreign key to retention-managed rows."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class PredictionPublication(Base):
    __tablename__ = "prediction_publications"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    prediction_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    match_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_match_id: Mapped[str] = mapped_column(String(160), nullable=False)
    league_id: Mapped[str] = mapped_column(String(80), nullable=False)
    season: Mapped[str] = mapped_column(String(32), nullable=False)
    home_team: Mapped[str] = mapped_column(String(200), nullable=False)
    away_team: Mapped[str] = mapped_column(String(200), nullable=False)
    kickoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()"))
    market: Mapped[str] = mapped_column(String(80), nullable=False)
    selection: Mapped[str] = mapped_column(String(120), nullable=False)
    probability: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    model_version: Mapped[str] = mapped_column(String(80), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(80), nullable=False)
    feature_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    decimal_odds: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    odds_snapshot_ref: Mapped[str | None] = mapped_column(String(300))
    odds_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    odds_permission_ref: Mapped[str | None] = mapped_column(String(300))
    publication_version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("prediction_publications.id", ondelete="RESTRICT"), unique=True)


class PredictionSettlement(Base):
    __tablename__ = "prediction_settlements"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    publication_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("prediction_publications.id", ondelete="RESTRICT"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("prediction_settlements.id", ondelete="RESTRICT"))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    result_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    evidence_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("prediction_evidence.id", ondelete="RESTRICT"))
    result_source: Mapped[str | None] = mapped_column(String(120))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()"))


class PredictionEvidence(Base):
    __tablename__ = "prediction_evidence"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    publication_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("prediction_publications.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    authority: Mapped[str] = mapped_column(String(120), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    key_id: Mapped[str] = mapped_column(String(120), nullable=False)
    public_key_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()"))
    supersedes_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("prediction_evidence.id", ondelete="RESTRICT"))
