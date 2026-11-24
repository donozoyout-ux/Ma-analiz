from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func
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
    venue: Mapped[str | None] = mapped_column(String(200))
    round: Mapped[str | None] = mapped_column(String(80))
    country: Mapped[str | None] = mapped_column(String(100))
    sport: Mapped[str | None] = mapped_column(String(80))
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class Bookmaker(Base):
    __tablename__ = "bookmakers"
    __table_args__ = (UniqueConstraint("provider", "name"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_bookmaker_id: Mapped[str | None] = mapped_column(String(160))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    country: Mapped[str | None] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ActiveOdds(Base):
    __tablename__ = "active_odds"
    __table_args__ = (UniqueConstraint(
        "match_id", "bookmaker", "market", "selection", "line_key", "snapshot_type", "captured_at",
        name="uq_active_odds_snapshot_natural",
    ),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    match_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("active_matches.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False, default="unknown")
    provider_odds_id: Mapped[str | None] = mapped_column(String(160))
    provider_market_id: Mapped[str | None] = mapped_column(String(160))
    provider_bookmaker_id: Mapped[str | None] = mapped_column(String(160))
    bookmaker_entity_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("bookmakers.id"))
    bookmaker: Mapped[str] = mapped_column(String(120), nullable=False)
    market: Mapped[str] = mapped_column(String(80), nullable=False)
    selection: Mapped[str] = mapped_column(String(120), nullable=False)
    line: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    raw_line: Mapped[str | None] = mapped_column(String(80))
    line_key: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    odds: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="unknown")
    period: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    snapshot_type: Mapped[str] = mapped_column(String(16), nullable=False, default="OBSERVED")
    raw_value: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="AVAILABLE")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class MatchResult(Base):
    __tablename__ = "match_results"
    __table_args__ = (UniqueConstraint("match_id"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    match_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("active_matches.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_result_id: Mapped[str | None] = mapped_column(String(160))
    home_score: Mapped[int | None]
    away_score: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="IN_PROGRESS")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_source: Mapped[str] = mapped_column(String(120), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ActiveStatistic(Base):
    """Only explicitly permitted, verified full-time technical observations."""
    __tablename__ = "active_statistics"
    __table_args__ = (UniqueConstraint("match_id", "provider", "provider_statistic_id", "observed_at"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    match_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("active_matches.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_match_id: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_statistic_id: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    unit: Mapped[str] = mapped_column(String(24), nullable=False)
    period: Mapped[str] = mapped_column(String(24), nullable=False)
    home_value: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    away_value: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    licence: Mapped[str] = mapped_column(String(100), nullable=False)
    permission_reference: Mapped[str] = mapped_column(Text, nullable=False)
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
    decimal_odds: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    market_probability: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    final_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    risk_level: Mapped[str | None] = mapped_column(String(16))
    analysis_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    feature_version: Mapped[str] = mapped_column(String(80), nullable=False, default="canonical-features-1")
    feature_snapshot: Mapped[str | None] = mapped_column(Text)
    paper_stake: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("1"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)
    paper_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class Coupon(Base):
    __tablename__ = "coupons"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    total_odds: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    paper_stake: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("1"))
    paper_profit: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PAPER_PENDING", index=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class CouponItem(Base):
    __tablename__ = "coupon_items"
    coupon_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("coupons.id", ondelete="CASCADE"), primary_key=True)
    match_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("active_matches.id", ondelete="CASCADE"), primary_key=True)
    market: Mapped[str] = mapped_column(String(80), nullable=False)
    selection: Mapped[str] = mapped_column(String(120), nullable=False)
    odds: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    prediction_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("predictions.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
