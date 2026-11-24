from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.config import Settings, get_settings
from app.storage.r2 import R2NotConfigured, R2ObjectStore


def database_capacity(size: int) -> dict:
    limit = 1_000_000_000  # conservative decimal 1 GB
    ratio = size / limit
    return {"bytes": size, "limit_bytes": limit, "used_percent": round(ratio * 100, 2),
            "status": "CRITICAL" if ratio >= .9 else "HIGH" if ratio >= .8 else "WARNING" if ratio >= .7 else "HEALTHY",
            "action": "ARCHIVE_AND_REVIEW_BLOCKERS_NEVER_FORCE_DELETE"}


def postgres_status(size_bytes: int, settings: Settings) -> str:
    mb = size_bytes / (1024 * 1024)
    normal, warning, high, critical = (
        settings.postgres_normal_mb, settings.postgres_warning_mb,
        settings.postgres_high_mb, settings.postgres_critical_mb,
    )
    if not (0 < normal < warning < high <= critical):
        raise ValueError("PostgreSQL storage thresholds must satisfy 0 < normal < warning < high <= critical")
    if mb < normal:
        return "healthy"
    if mb < warning:
        return "warning"
    if mb < high:
        return "high"
    if mb < critical:
        return "high"
    return "critical"


class StorageMonitor:
    def __init__(self, engine: Engine | None, settings: Settings | None = None):
        self.engine = engine
        self.settings = settings or get_settings()

    def get_database_size(self) -> int | None:
        if self.engine is None:
            return None
        with self.engine.connect() as connection:
            return int(connection.execute(text("SELECT pg_database_size(current_database())")).scalar_one())

    def historical_ingestion_status(self) -> str:
        """Return a PostgreSQL-only guard status without contacting R2 or providers."""
        if self.engine is None:
            return "DATABASE_NOT_CONFIGURED"
        if getattr(getattr(self.engine, "dialect", None), "name", None) != "postgresql":
            return "NOT_APPLICABLE"
        try:
            size = self.get_database_size()
            if size is None:
                return "DATABASE_NOT_CONFIGURED"
            return postgres_status(size, self.settings)
        except Exception:
            return "DATABASE_STORAGE_CHECK_FAILED"

    def get_table_sizes(self, limit: int = 10) -> list[dict]:
        if self.engine is None:
            return []
        statement = text("""
            SELECT relname AS table, pg_total_relation_size(relid)::bigint AS size_bytes,
                   COALESCE(n_live_tup, 0)::bigint AS rows_estimate
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
            LIMIT :limit
        """)
        with self.engine.connect() as connection:
            rows = connection.execute(statement, {"limit": max(1, min(limit, 100))}).mappings().all()
        return [dict(row) for row in rows]

    def get_largest_tables(self, limit: int = 10) -> list[dict]:
        return self.get_table_sizes(limit)

    def snapshot(self) -> dict:
        db_bytes = None
        table_rows: list[dict] = []
        if self.engine is None:
            db_status = "DATABASE_NOT_CONFIGURED"
        else:
            try:
                db_bytes = self.get_database_size()
                table_rows = self.get_table_sizes()
                db_status = postgres_status(db_bytes or 0, self.settings)
            except Exception:
                db_status = "OFFLINE"
                db_bytes = None
                table_rows = []

        r2_bytes = None
        object_count = None
        configured = self.settings.r2_configured
        if self.settings.r2_missing_env:
            r2_status = "R2_NOT_CONFIGURED"
        elif self.settings.r2_invalid_env:
            r2_status = "R2_ERROR"
        else:
            try:
                store = R2ObjectStore()
                objects = store.list_objects()
                r2_bytes = sum(obj.size for obj in objects)
                object_count = len(objects)
                r2_status = "ONLINE"
            except R2NotConfigured:
                r2_status = "R2_NOT_CONFIGURED"
            except Exception:
                r2_status = "DEGRADED"

        return {
            "postgres": {
                "configured": self.engine is not None,
                "bytes": db_bytes,
                "mb": round(db_bytes / (1024 * 1024), 3) if db_bytes is not None else None,
                "status": db_status,
                "capacity": database_capacity(db_bytes) if db_bytes is not None else {"status": "NOT_RUN", "bytes": None, "limit_bytes": 1_000_000_000},
                "thresholds_mb": {
                    "normal": self.settings.postgres_normal_mb,
                    "warning": self.settings.postgres_warning_mb,
                    "high": self.settings.postgres_high_mb,
                    "critical": self.settings.postgres_critical_mb,
                },
                "largest_tables": table_rows,
            },
            "r2": {
                "configured": configured,
                "bytes": r2_bytes,
                "object_count": object_count,
                "status": r2_status,
            },
        }
