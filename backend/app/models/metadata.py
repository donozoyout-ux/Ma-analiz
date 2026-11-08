from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ArchiveJob(Base):
    __tablename__ = "archive_jobs"
    __table_args__ = (CheckConstraint("status IN ('PENDING', 'UPLOADING', 'UPLOADED', 'VERIFIED', 'FAILED', 'DELETED')", name="valid_archive_status"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    archive_date: Mapped[date] = mapped_column(Date, nullable=False)
    dataset_type: Mapped[str] = mapped_column(String(40), nullable=False)
    league: Mapped[str | None] = mapped_column(String(120))
    r2_key: Mapped[str] = mapped_column(String(600), nullable=False, unique=True)
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DailyMetric(Base):
    __tablename__ = "daily_metrics"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    total_matches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    analyzed_matches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    predictions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hit_rate: Mapped[float | None] = mapped_column(Numeric(6, 5))
    coupons: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coupon_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coupon_losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    paper_pl: Mapped[float | None] = mapped_column(Numeric(12, 4))
    r2_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    postgres_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class Lesson(Base):
    __tablename__ = "lessons"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PROPOSAL")
    evidence_r2_key: Mapped[str | None] = mapped_column(String(600))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))




class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (UniqueConstraint("idempotency_key"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    job_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RUNNING", index=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(String(240))
    records_processed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
