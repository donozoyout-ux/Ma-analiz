from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from typing import Callable, Protocol

from app.storage.r2 import ObjectStore, parquet_bytes, parquet_row_count


class ArchiveStatus(StrEnum):
    PENDING = "PENDING"
    UPLOADING = "UPLOADING"
    UPLOADED = "UPLOADED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    DELETED = "DELETED"


_ALLOWED: dict[ArchiveStatus, set[ArchiveStatus]] = {
    ArchiveStatus.PENDING: {ArchiveStatus.UPLOADING, ArchiveStatus.FAILED},
    ArchiveStatus.UPLOADING: {ArchiveStatus.UPLOADING, ArchiveStatus.UPLOADED, ArchiveStatus.FAILED},
    ArchiveStatus.UPLOADED: {ArchiveStatus.UPLOADING, ArchiveStatus.VERIFIED, ArchiveStatus.FAILED},
    ArchiveStatus.VERIFIED: {ArchiveStatus.VERIFIED, ArchiveStatus.FAILED, ArchiveStatus.DELETED},
    ArchiveStatus.FAILED: {ArchiveStatus.PENDING},
    ArchiveStatus.DELETED: {ArchiveStatus.DELETED},
}


class InvalidArchiveTransition(RuntimeError):
    pass


def validate_transition(current: ArchiveStatus | None, target: ArchiveStatus) -> None:
    if current is None:
        if target != ArchiveStatus.PENDING:
            raise InvalidArchiveTransition(f"New archive job must start PENDING, got {target}")
        return
    if target not in _ALLOWED[current]:
        raise InvalidArchiveTransition(f"Illegal archive transition {current} -> {target}")


@dataclass(frozen=True)
class ArchiveReceipt:
    key: str
    row_count: int
    size: int
    checksum: str
    status: ArchiveStatus = ArchiveStatus.VERIFIED


class ArchiveTracker(Protocol):
    def get_status(self, key: str) -> ArchiveStatus | None: ...
    def set_status(self, *, key: str, day: date, dataset: str, league: str,
                   status: ArchiveStatus, rows: int | None = None, size: int | None = None,
                   checksum: str | None = None) -> None: ...


class SQLArchiveTracker:
    """Persists archive transitions in the compact archive_jobs metadata table."""
    def __init__(self, session):
        self.session = session

    def get_status(self, key: str) -> ArchiveStatus | None:
        from app.models import ArchiveJob
        job = self.session.query(ArchiveJob).filter_by(r2_key=key).one_or_none()
        return ArchiveStatus(job.status) if job else None

    def set_status(self, *, key: str, day: date, dataset: str, league: str,
                   status: ArchiveStatus, rows: int | None = None, size: int | None = None,
                   checksum: str | None = None) -> None:
        from app.models import ArchiveJob
        job = self.session.query(ArchiveJob).filter_by(r2_key=key).with_for_update().one_or_none()
        current = ArchiveStatus(job.status) if job else None
        validate_transition(current, status)
        if job is None:
            job = ArchiveJob(archive_date=day, dataset_type=dataset, league=league, r2_key=key, status=status.value)
            self.session.add(job)
        else:
            job.status = status.value
        if rows is not None:
            job.row_count = rows
        if size is not None:
            job.file_size_bytes = size
        if checksum is not None:
            job.checksum = checksum
        if status == ArchiveStatus.VERIFIED:
            job.verified_at = datetime.now(timezone.utc)
        self.session.commit()


def _safe_partition(value: str) -> str:
    if not value or value in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise ValueError(f"Unsafe archive path segment: {value!r}")
    return value


def _verify_object(store: ObjectStore, key: str, *, expected_size: int, expected_rows: int, expected_checksum: str) -> None:
    info = store.head_object(key)
    if info is None or info.size != expected_size:
        raise IOError("Archive object is missing or has an unexpected size")
    payload = store.get_object(key)
    if parquet_row_count(payload) != expected_rows:
        raise IOError("Archive row count verification failed")
    actual_checksum = hashlib.sha256(payload).hexdigest()
    if actual_checksum != expected_checksum:
        raise IOError("Archive checksum verification failed")
    if not info.metadata or info.metadata.get("sha256") != expected_checksum:
        raise IOError("Archive metadata checksum verification failed")
    if info.metadata.get("row-count") != str(expected_rows):
        raise IOError("Archive metadata row-count verification failed")
    if info.metadata.get("archive-key") != key:
        raise IOError("Archive metadata key verification failed")


def archive_partition(
    store: ObjectStore, *, dataset: str, season: str, league: str,
    day: date, rows: list[dict], retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
    tracker: ArchiveTracker | None = None,
) -> ArchiveReceipt:
    """Write one deterministic Parquet partition and verify it before reporting VERIFIED."""
    if not rows:
        raise ValueError("Refusing to archive an empty partition")
    if retries < 1:
        raise ValueError("retries must be at least one")
    dataset, season, league = map(_safe_partition, (dataset, season, league))
    payload = parquet_bytes(rows)
    checksum = hashlib.sha256(payload).hexdigest()
    key = f"{dataset}/season={season}/league={league}/date={day.isoformat()}.parquet"
    if tracker:
        previous = tracker.get_status(key)
        if previous == ArchiveStatus.DELETED:
            raise RuntimeError("Archive is already DELETED; refusing to recreate its source job")
        if previous == ArchiveStatus.VERIFIED:
            try:
                _verify_object(store, key, expected_size=len(payload), expected_rows=len(rows), expected_checksum=checksum)
                return ArchiveReceipt(key, len(rows), len(payload), checksum)
            except Exception as exc:
                tracker.set_status(key=key, day=day, dataset=dataset, league=league, status=ArchiveStatus.FAILED,
                                   rows=len(rows), size=len(payload), checksum=checksum)
                raise RuntimeError("Previously verified archive failed re-verification") from exc
        if previous in (None, ArchiveStatus.FAILED):
            tracker.set_status(key=key, day=day, dataset=dataset, league=league, status=ArchiveStatus.PENDING, rows=len(rows))
    elif tracker is None:
        # The upload is still useful without a database ledger, but callers cannot use it to authorize DB cleanup.
        pass

    # Deterministic keys are immutable. A pre-existing object is reusable only
    # if its complete bytes and verification metadata match this payload.
    try:
        existing = store.head_object(key)
    except Exception as exc:
        if tracker and tracker.get_status(key) != ArchiveStatus.FAILED:
            tracker.set_status(key=key, day=day, dataset=dataset, league=league,
                               status=ArchiveStatus.FAILED, rows=len(rows), size=len(payload), checksum=checksum)
        raise RuntimeError("Archive object existence check failed; source records must be retained") from exc
    if existing is not None:
        try:
            existing_payload = store.get_object(key)
            metadata = existing.metadata or {}
            reusable = (
                existing.size == len(payload)
                and hashlib.sha256(existing_payload).hexdigest() == checksum
                and parquet_row_count(existing_payload) == len(rows)
                and metadata.get("sha256") == checksum
                and metadata.get("row-count") == str(len(rows))
                and metadata.get("archive-key") == key
            )
        except Exception:
            reusable = False
        if not reusable:
            if tracker and tracker.get_status(key) != ArchiveStatus.FAILED:
                tracker.set_status(key=key, day=day, dataset=dataset, league=league,
                                   status=ArchiveStatus.FAILED, rows=len(rows), size=len(payload), checksum=checksum)
            raise RuntimeError("Conflicting archive object exists; refusing to overwrite it")
        if tracker:
            current = tracker.get_status(key)
            if current == ArchiveStatus.PENDING:
                tracker.set_status(key=key, day=day, dataset=dataset, league=league,
                                   status=ArchiveStatus.UPLOADING, rows=len(rows))
                current = ArchiveStatus.UPLOADING
            if current == ArchiveStatus.UPLOADING:
                tracker.set_status(key=key, day=day, dataset=dataset, league=league,
                                   status=ArchiveStatus.UPLOADED, rows=len(rows), size=len(payload), checksum=checksum)
                current = ArchiveStatus.UPLOADED
            if current == ArchiveStatus.UPLOADED:
                tracker.set_status(key=key, day=day, dataset=dataset, league=league,
                                   status=ArchiveStatus.VERIFIED, rows=len(rows), size=len(payload), checksum=checksum)
        return ArchiveReceipt(key, len(rows), len(payload), checksum)

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            if tracker:
                tracker.set_status(key=key, day=day, dataset=dataset, league=league, status=ArchiveStatus.UPLOADING, rows=len(rows))
            store.put_object(key, payload, content_type="application/vnd.apache.parquet",
                             metadata={"sha256": checksum, "row-count": str(len(rows)), "archive-key": key})
            if tracker:
                tracker.set_status(key=key, day=day, dataset=dataset, league=league, status=ArchiveStatus.UPLOADED,
                                   rows=len(rows), size=len(payload), checksum=checksum)
            _verify_object(store, key, expected_size=len(payload), expected_rows=len(rows), expected_checksum=checksum)
            if tracker:
                tracker.set_status(key=key, day=day, dataset=dataset, league=league, status=ArchiveStatus.VERIFIED,
                                   rows=len(rows), size=len(payload), checksum=checksum)
            return ArchiveReceipt(key, len(rows), len(payload), checksum)
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                sleep(float(2**attempt))
    if tracker:
        tracker.set_status(key=key, day=day, dataset=dataset, league=league, status=ArchiveStatus.FAILED,
                           rows=len(rows), size=len(payload), checksum=checksum)
    raise RuntimeError(f"Archive failed after {retries} attempts; source records must be retained") from last_error


def cleanup_database_rows_after_verified(session, *, key: str, delete_rows: Callable) -> None:
    """Delete only rows covered by a persisted VERIFIED job and older than a 1-day buffer.

    delete_rows(session, archive_job) must scope its deletion to this job's partition and
    must not commit independently. Both status update and deletion share one transaction.
    """
    from app.models import ArchiveJob
    job = session.query(ArchiveJob).filter_by(r2_key=key).with_for_update().one_or_none()
    today = datetime.now(timezone.utc).date()
    if (job is None or job.status != ArchiveStatus.VERIFIED.value or not job.checksum
            or not job.verified_at or not job.row_count or job.archive_date >= today - timedelta(days=1)):
        session.rollback()
        raise RuntimeError("Archive is not verified and outside the safety buffer; refusing database cleanup")
    try:
        delete_rows(session, job)
        job.status = ArchiveStatus.DELETED.value
        job.deleted_at = datetime.now(timezone.utc)
        session.commit()
    except Exception:
        session.rollback()
        raise
