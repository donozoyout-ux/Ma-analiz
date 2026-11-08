from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ActiveMatch(Base):
    __tablename__ = "active_matches"
    __table_args__ = (UniqueConstraint("provider", "provider_match_id"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_match_id: Mapped[str] = mapped_column(String(160), nullable=False)
    league_id: Mapped[str | None] = mapped_column(String(100))
    league_name: Mapped[str | None] = mapped_column(String(200))
    season: Mapped[str | None] = mapped_column(String(32))
    home_team: Mapped[str] = mapped_column(String(200), nullable=False)
    away_team: Mapped[str] = mapped_column(String(200), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="SCHEDULED")
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class ActiveOdds(Base):
    __tablename__ = "active_odds"
    __table_args__ = (UniqueConstraint("match_id", "bookmaker", "market", "selection", "captured_at"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    match_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("active_matches.id", ondelete="CASCADE"), nullable=False, index=True)
    bookmaker: Mapped[str] = mapped_column(String(120), nullable=False)
    market: Mapped[str] = mapped_column(String(80), nullable=False)
    selection: Mapped[str] = mapped_column(String(120), nullable=False)
    odds: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (UniqueConstraint("match_id", "market", "selection", "model_version"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    match_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("active_matches.id", ondelete="CASCADE"), nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(80), nullable=False)
    selection: Mapped[str] = mapped_column(String(120), nullable=False)
    probability: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    value_score: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    evidence_status: Mapped[str] = mapped_column(String(16), nullable=False)
    model_version: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class Coupon(Base):
    __tablename__ = "coupons"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    total_odds: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PAPER_PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class CouponItem(Base):
    __tablename__ = "coupon_items"
    coupon_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("coupons.id", ondelete="CASCADE"), primary_key=True)
    match_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("active_matches.id", ondelete="CASCADE"), primary_key=True)
    market: Mapped[str] = mapped_column(String(80), nullable=False)
    selection: Mapped[str] = mapped_column(String(120), nullable=False)
    odds: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    prediction_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("predictions.id", ondelete="SET NULL"), nullable=True)
