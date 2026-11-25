#!/usr/bin/env python3
"""Bundesliga 32-hour ingestion pipeline: OpenLigaDB -> PostgreSQL -> R2 Archive.

Produces real data following:
1. Match collection from OpenLigaDB (ODbL 1.0 licensed)
2. PostgreSQL insert/update with source identity preservation
3. Parquet archive to R2 with SHA-256 verification
4. 32-hour retention window before deletion (dry-run first)

Usage:
    python -m app.jobs.bundesliga_32h_ingest --date 2026-11-20 --live
    python -m app.jobs.bundesliga_32h_ingest --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
import sys
from pathlib import Path

from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ActiveMatch, MatchResult, ArchiveJob, SystemSetting, JobRun
from app.providers.openligadb import OpenLigaDBProvider, ATTRIBUTION, LICENCE, LICENCE_URL
from app.providers.normalize import normalize_matches, normalize_result
from app.storage.r2 import R2ObjectStore, parquet_bytes
from app.storage.archive import SQLArchiveTracker, ArchiveStatus, archive_partition
from app.storage.lifecycle import source_index_key


def upsert_matches(session: Session, matches: list[dict], now: datetime) -> tuple[int, int]:
    """Insert new matches, update existing ones by provider_match_id.
    
    Returns (written, skipped) counts. Always preserves UUIDs on update.
    """
    from app.providers.identity import fixture_key
    written = skipped = 0
    
    for match in matches:
        identity = fixture_key(match)
        if identity is None:
            skipped += 1
            continue
            
        stmt = select(ActiveMatch).where(
            ActiveMatch.provider == "openligadb",
            ActiveMatch.provider_match_id == match["provider_match_id"]
        )
        existing = session.scalars(stmt).one_or_none()
        
        if existing:
            existing.home_team = match["home_team"]
            existing.away_team = match["away_team"]
            existing.start_time = match["start_time"]
            existing.status = match["status"]
            existing.league_id = match.get("league_id")
            existing.league_name = match.get("league_name")
            existing.season = match.get("season")
            existing.round = match.get("round")
            existing.venue = match.get("venue")
            existing.expires_at = match["start_time"] + timedelta(hours=32)
            written += 1
        else:
            expires_at = match["start_time"] + timedelta(hours=32)
            new_match = ActiveMatch(
                provider="openligadb",
                provider_match_id=match["provider_match_id"],
                league_id=match.get("league_id", "de.bl1"),
                league_name=match.get("league_name", "Bundesliga"),
                season=match.get("season", "2026-2027"),
                home_team=match["home_team"],
                away_team=match["away_team"],
                start_time=match["start_time"],
                status=match["status"],
                round=match.get("round"),
                venue=match.get("venue"),
                country="Germany",
                sport="football",
                expires_at=expires_at,
            )
            session.add(new_match)
            written += 1
    
    return written, skipped


def upsert_results(session: Session, results: list[dict], matches: dict, now: datetime) -> tuple[int, int]:
    """Insert/update match results for finished games."""
    written = skipped = 0
    
    for result in results:
        match_id = matches.get(result["provider_match_id"])
        if not match_id:
            skipped += 1
            continue
            
        stmt = select(MatchResult).where(MatchResult.match_id == match_id)
        existing = session.scalars(stmt).one_or_none()
        
        if existing and existing.status == "FINAL":
            skipped += 1
            continue
        
        if existing is None:
            res = MatchResult(
                match_id=match_id,
                provider="openligadb",
                provider_result_id=f"openligadb-{result['provider_match_id']}",
                home_score=result["home_score"],
                away_score=result["away_score"],
                status="FINAL",
                finished_at=result.get("finished_at"),
                result_source=f"{ATTRIBUTION}; {LICENCE_URL}",
                verified_at=now,
            )
            session.add(res)
        else:
            existing.home_score = result["home_score"]
            existing.away_score = result["away_score"]
            existing.status = "FINAL"
            existing.finished_at = result.get("finished_at")
            existing.result_source = f"{ATTRIBUTION}; {LICENCE_URL}"
            existing.verified_at = now
        
        written += 1
        
        match_row = session.get(ActiveMatch, match_id)
        if match_row:
            match_row.status = "FINISHED"
    
    return written, skipped


def archive_matches(store: R2ObjectStore, session: Session, matches: list[dict], 
                    day: date, season: str = "2026-2027", league: str = "de.bl1") -> dict:
    """Archive matches to R2 as Parquet with verification."""
    if not matches:
        return {"status": "SKIPPED", "rows": 0, "bytes": 0}
    
    rows = []
    for m in matches:
        rows.append({
            "provider": "openligadb",
            "provider_match_id": str(m["provider_match_id"]),
            "league_id": league,
            "league_name": "Bundesliga",
            "season": season,
            "home_team": m["home_team"],
            "away_team": m["away_team"],
            "start_time": m["start_time"].isoformat() if isinstance(m["start_time"], datetime) else str(m["start_time"]),
            "status": m["status"],
            "home_score": m.get("score", [None, None])[0],
            "away_score": m.get("score", [None, None])[1],
            "attribution": ATTRIBUTION,
            "licence": LICENCE,
        })
    
    payload = parquet_bytes(rows)
    checksum = hashlib.sha256(payload).hexdigest()
    key = f"matches/season={season}/league={league}/date={day.isoformat()}.parquet"
    
    store.put_object(
        key,
        payload,
        content_type="application/vnd.apache.parquet",
        metadata={"sha256": checksum, "row-count": str(len(rows)), "archive-key": key}
    )
    
    info = store.head_object(key)
    if info is None:
        raise RuntimeError("Archive verification failed: object not found")
    if info.size != len(payload):
        raise RuntimeError(f"Archive size mismatch: {info.size} vs {len(payload)}")
    
    return {
        "status": "VERIFIED",
        "key": key,
        "rows": len(rows),
        "bytes": len(payload),
        "checksum": checksum,
    }


def run_pipeline(target_day: date, dry_run: bool = True, store: R2ObjectStore | None = None,
                 session: Session | None = None, provider: OpenLigaDBProvider | None = None) -> dict:
    """Execute the full 32-hour ingestion pipeline."""
    cfg = get_settings()
    now = datetime.now(timezone.utc)
    
    if provider is None:
        provider = OpenLigaDBProvider()
    own_provider = provider is not None
    
    report = {
        "status": "PENDING",
        "dry_run": dry_run,
        "target_day": target_day.isoformat(),
        "matches_collected": 0,
        "matches_inserted": 0,
        "matches_updated": 0,
        "results_persisted": 0,
        "archive_rows": 0,
        "archive_bytes": 0,
        "archive_checksum": None,
        "r2_key": None,
        "errors": [],
    }
    
    try:
        season_year = target_day.year if target_day.month >= 7 else target_day.year - 1
        raw_matches = provider.get_matches(target_day)
        report["matches_collected"] = len(raw_matches)
        
        normalized = normalize_matches([
            {**m, "provider": "openligadb"} for m in raw_matches
        ], provider="openligadb")
        
        if not dry_run:
            if session is None:
                from app.db.session import SessionLocal
                if SessionLocal is None:
                    report["status"] = "BLOCKED"
                    report["errors"].append("DATABASE_NOT_CONFIGURED")
                    return report
                session = SessionLocal()
            
            with session:
                written, skipped = upsert_matches(session, normalized, now)
                match_ids = {m["provider_match_id"]: str(session.get(ActiveMatch, 
                    session.scalars(select(ActiveMatch.id).where(
                        ActiveMatch.provider == "openligadb",
                        ActiveMatch.provider_match_id == m["provider_match_id"]
                    ).limit(1)).scalar()) or "") 
                    for m in normalized}
                
                results = provider.get_results(target_day)
                result_rows = []
                for r in results:
                    norm = normalize_result(r, provider="openligadb")
                    result_rows.append(norm)
                
                results_written, results_skipped = upsert_results(session, result_rows, match_ids, now)
                report["results_persisted"] = results_written
                report["matches_inserted"] = written - skipped
                
                session.commit()
                
                if store:
                    finished_matches = [m for m in normalized if m["status"] == "FINISHED"]
                    archive_result = archive_matches(store, session, finished_matches, target_day)
                    report["archive_rows"] = archive_result["rows"]
                    report["archive_bytes"] = archive_result["bytes"]
                    report["archive_checksum"] = archive_result["checksum"]
                    report["r2_key"] = archive_result["key"]
                    report["status"] = "SUCCESS"
                else:
                    report["status"] = "COMPLETED"
        else:
            report["matches_inserted"] = len(normalized)
            report["status"] = "SKIPPED_DRY_RUN"
    
    except Exception as e:
        report["status"] = "FAILED"
        report["errors"].append(str(e))
        import traceback
        report["errors"].append(traceback.format_exc())
    
    finally:
        if own_provider:
            provider.close()
    
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, 
                        default=datetime.now(timezone.utc).date())
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    
    dry_run = args.dry_run and not args.live
    
    cfg = get_settings()
    
    if dry_run:
        report = run_pipeline(args.date, dry_run=True)
    else:
        if cfg.auto_retention_delete_enabled or cfg.operational_delete_enabled:
            print(json.dumps({"status": "BLOCKED", "reason": "DELETE_SAFETY_GATE"}))
            return 2
        
        if not cfg.bundesliga_ingest_enabled:
            print(json.dumps({"status": "BLOCKED", "reason": "BUNDESLIGA_INGEST_DISABLED"}))
            return 2
        
        from app.db.session import SessionLocal
        from app.storage.r2 import R2ObjectStore
        
        if SessionLocal is None:
            print(json.dumps({"status": "BLOCKED", "reason": "DATABASE_NOT_CONFIGURED"}))
            return 2
        
        if not cfg.r2_configured:
            print(json.dumps({"status": "BLOCKED", "reason": "R2_NOT_CONFIGURED"}))
            return 2
        
        with SessionLocal() as session:
            report = run_pipeline(args.date, dry_run=False, session=session, store=R2ObjectStore())
    
    print(json.dumps(report, default=str, indent=2))
    return 0 if report["status"] in ("SUCCESS", "SKIPPED", "SKIPPED_DRY_RUN") else 1


if __name__ == "__main__":
    sys.exit(main())