"""Explicit, archive-first independent Bundesliga result verification.

No automatic production execution. Two fresh final-score observations must
match the locked SQL record. The immutable proof is R2 readback-verified
before one SQL transaction persists verified_at and its archive ledger.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from sqlalchemy import select
from app.analysis.football_data_corroboration import corroborate
from app.models import ActiveMatch, ArchiveJob, MatchResult
from app.providers.result_sources import utc
from app.storage.archive import archive_partition


class VerificationBlocked(ValueError):
    pass


def _fresh(value, now):
    stamp = utc(value)
    if stamp is None or not timedelta(0) <= now - stamp <= timedelta(minutes=15):
        raise VerificationBlocked("SOURCE_EVIDENCE_STALE")
    return stamp


def verify_bundesliga_result(session, store, primary, secondary, *,
                             now=None, dry_run=True, live_approved=False):
    """Verify exactly one existing OpenLigaDB FINAL result, never create/replace it.

    Caller supplies observations fetched separately through approved APIs.
    No scraping, fetching, deletion, configuration changes or external locks.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise VerificationBlocked("UTC_TIMESTAMP_REQUIRED")
    now = now.astimezone(timezone.utc)
    if len(primary) != 1 or len(secondary) != 1:
        raise VerificationBlocked("ONE_MATCH_ONE_CORROBORATOR_REQUIRED")
    _fresh(primary[0].get("observed_at"), now)
    _fresh(secondary[0].get("observed_at"), now)
    decision = corroborate(primary, secondary)
    if decision["status"] != "MATCHED_CANDIDATES_NOT_PERSISTED" or len(decision["matches"]) != 1:
        raise VerificationBlocked("SOURCE_IDENTITY_OR_SCORE_NOT_VERIFIED")
    candidate = decision["matches"][0]
    first, second = primary[0], secondary[0]
    if (str(first.get("source_match_id")) != candidate["openligadb_match_id"]
            or str(second.get("source_match_id")) != candidate["football_data_match_id"]
            or first.get("source_url") != "https://api.openligadb.de/getmatchdata/bl1/2026/1"
            or second.get("source_url") != "https://api.football-data.org/v4/competitions/BL1/matches"
            or first.get("final_confirmed") is not True
            or second.get("final_confirmed") is not True):
        raise VerificationBlocked("INDEPENDENT_SOURCE_PROVENANCE_INVALID")
    match = session.scalar(select(ActiveMatch).where(
        ActiveMatch.provider == "openligadb",
        ActiveMatch.provider_match_id == candidate["openligadb_match_id"],
        ActiveMatch.league_id == "de.bl1",
        ActiveMatch.season == "2026-2027",
    ).with_for_update())
    if match is None or match.status != "FINISHED":
        raise VerificationBlocked("SQL_MATCH_NOT_FINISHED")
    kickoff = utc(match.start_time)
    if kickoff is None or kickoff != utc(first.get("start_time")):
        raise VerificationBlocked("SQL_FIXTURE_TIME_CONFLICT")
    if (match.home_team != first.get("home_team") or
            match.away_team != first.get("away_team")):
        raise VerificationBlocked("SQL_FIXTURE_NAME_CONFLICT")
    result = session.scalar(select(MatchResult).where(MatchResult.match_id == match.id).with_for_update())
    if (result is None or result.status != "FINAL" or result.provider != "openligadb"
            or result.home_score != candidate["score"][0]
            or result.away_score != candidate["score"][1]):
        raise VerificationBlocked("SQL_FINAL_RESULT_CONFLICT")
    if result.verified_at is not None:
        return {"status": "ALREADY_VERIFIED_NO_WRITE", "match_id": str(match.id),
                "verified_at": utc(result.verified_at).isoformat(), "writes": 0}
    if dry_run:
        return {"status": "DRY_RUN_MATCHED", "match_id": str(match.id),
                "openligadb_match_id": candidate["openligadb_match_id"],
                "football_data_match_id": candidate["football_data_match_id"],
                "writes": 0}
    if not live_approved or store is None:
        raise VerificationBlocked("LIVE_VERIFICATION_NEEDS_EXPLICIT_APPROVAL_AND_R2")
    # Store minimal proof only, not raw football-data.org response content.
    proof = {
        "match_id": str(match.id), "result_id": str(result.id),
        "league": "de.bl1", "season": "2026-2027",
        "utc_kickoff": kickoff.isoformat(),
        "openligadb_match_id": candidate["openligadb_match_id"],
        "football_data_match_id": candidate["football_data_match_id"],
        "home_score": result.home_score, "away_score": result.away_score,
        "openligadb_observed_at": utc(first["observed_at"]).isoformat(),
        "football_data_observed_at": utc(second["observed_at"]).isoformat(),
        "verified_at": now.isoformat(),
        "decision": "EXACT_IDENTITIES_AND_FINAL_SCORE_AGREE",
        "source_1": "OpenLigaDB / ODbL-1.0",
        "source_2": "football-data.org / official API (attribution required)",
    }
    digest = sha256(json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    receipt = archive_partition(
        store, dataset="verification-evidence", season="2026-2027", league="de.bl1",
        day=kickoff.date(), rows=[proof], part="independent-" + digest[:32], retries=1)
    # Archive successfully persisted and read back before changing SQL.
    try:
        result.verified_at = now
        job = session.scalar(select(ArchiveJob).where(ArchiveJob.r2_key == receipt.key).with_for_update())
        if job is None:
            session.add(ArchiveJob(
                archive_date=kickoff.date(), dataset_type="verification-evidence",
                league="de.bl1", r2_key=receipt.key,
                row_count=receipt.row_count, file_size_bytes=receipt.size,
                checksum=receipt.checksum, status="VERIFIED", verified_at=now))
        elif (job.status != "VERIFIED" or job.checksum != receipt.checksum
                or job.file_size_bytes != receipt.size or job.row_count != 1):
            raise VerificationBlocked("EVIDENCE_LEDGER_CONFLICT")
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {"status": "VERIFIED_SQL_UPDATED", "match_id": str(match.id),
            "verified_at": now.isoformat(), "evidence_key": receipt.key,
            "evidence_sha256": receipt.checksum, "writes": 1,
            "lifecycle_archive": "NOT_YET_ARCHIVED",
            "retention_delete_enabled": False}
