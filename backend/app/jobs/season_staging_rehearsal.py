"""Disposable PostgreSQL + in-memory R2 rehearsal for one pinned 2024-25 UCL season.

CI only. Requires a locally hosted database whose name is exactly season_test.
Never accepts production, remote PostgreSQL, or actual R2 credentials.
"""
from __future__ import annotations

import json
import os

import httpx
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.historical.season_transfer import transfer_source
from app.models.season_bulk import season_fixtures, season_imports
from app.providers.season_files import DownloadBudget, source_for
from app.storage.r2 import InMemoryObjectStore


def main() -> int:
    url = os.getenv("SEASON_BULK_TEST_DATABASE_URL", "")
    parsed = make_url(url)
    if (parsed.get_backend_name() != "postgresql"
            or parsed.host not in {"localhost", "127.0.0.1", "::1"}
            or parsed.database != "season_test"):
        raise RuntimeError("DISPOSABLE_POSTGRES_ONLY")
    source = source_for("uefa.cl", "2024-2025")
    assert source is not None and source.revision is not None and source.licence == "CC0-1.0"
    engine = create_engine(url)
    store = InMemoryObjectStore()
    try:
        with engine.connect() as conn:
            if conn.scalar(text("SELECT version_num FROM alembic_version")) != "0011_season_bulk":
                raise RuntimeError("SEASON_MIGRATION_REQUIRED")
            baseline = conn.scalar(text("SELECT COUNT(*) FROM active_matches"))
        with httpx.Client(trust_env=False) as client:
            with Session(engine) as session, session.begin():
                first = transfer_source(session, store, source, client, DownloadBudget(),
                                        approval="IMPORT_REVIEWED_SEASON_ONLY")
            with Session(engine) as session, session.begin():
                second = transfer_source(session, store, source, client, DownloadBudget(0),
                                         approval="IMPORT_REVIEWED_SEASON_ONLY")
        with engine.connect() as conn:
            fixtures = conn.scalar(select(func.count()).select_from(season_fixtures))
            imports = conn.scalar(select(func.count()).select_from(season_imports))
            after = conn.scalar(text("SELECT COUNT(*) FROM active_matches"))
        if not (first["fixtures"] == first["regulation_scores"] == fixtures == 189
                and first["verification"] == "ARCHIVE_VERIFIED" and second["reused"]
                and imports == 1 and baseline == after == 0):
            raise RuntimeError("STAGING_REHEARSAL_MISMATCH")
        for key in ("raw", "matches", "manifest"):
            receipt = first[key]
            body = store.get_object(receipt["key"])
            import hashlib
            if len(body) != receipt["size"] or hashlib.sha256(body).hexdigest() != receipt["sha256"]:
                raise RuntimeError("STAGING_ARCHIVE_MISMATCH")
        print(json.dumps({
            "status": "ISOLATED_POSTGRES_REHEARSAL_PASSED",
            "league": "uefa.cl", "season": "2024-2025", "fixtures": fixtures,
            "regulation_scores": first["regulation_scores"], "imports": imports,
            "objects_verified": 3, "replay_reused": second["reused"],
            "existing_active_matches_unchanged": True,
            "actual_r2_writes": 0, "production_writes": 0,
        }, sort_keys=True))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
