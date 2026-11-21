"""Stage 3: one reviewed fixture, fresh verification, SQL journal and immutable R2 parts."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
import re
from uuid import UUID

from sqlalchemy import or_, select, text

from app.analysis.result_verification import assess_result, team_key
from app.core.config import get_settings
from app.jobs import result_probe
from app.models import ActiveMatch, MatchResult, SystemSetting
from app.providers.result_sources import utc
from app.providers.identity import REVIEWED_IMPORT_KEY, fixture_key, lock_id
from app.storage.archive import SQLArchiveTracker, _verify_object, archive_partition

DAY = date(2026, 11, 20)
KEY = REVIEWED_IMPORT_KEY
LOCK_ID = 832373018869
ATTRIBUTION = "Data: OpenLigaDB (https://www.openligadb.de), ODbL 1.0"
RESULT_SOURCE = "OpenLigaDB; ODbL-1.0; https://www.openligadb.de; corroboration Goaloo:3018869"
PART = "verified-openligadb-83237"


class ImportBlocked(RuntimeError):
    """Only fixed error codes are exposed; never SQL, endpoints or credentials."""


class ImportIncomplete(RuntimeError):
    def __init__(self, progress):
        self.progress = progress


def candidate_from_live(report, now):
    """Recompute the decision; saved stage-2 JSON is never a persistence credential."""
    scope = report.get("scope", {})
    if (not report.get("live_test") or scope != {
        "league": "de.bl1", "date": DAY.isoformat(), "timezone": "Europe/Istanbul",
        "matchday": 10, "goaloo_target_id": "3018869",
    }):
        raise ImportBlocked("LIVE_SINGLE_FIXTURE_SCOPE_REQUIRED")
    decision = assess_result(report.get("observations", []), goaloo_match_id="3018869")
    final = decision.get("final_result")
    if report.get("status") != "RESULT_VERIFIED" or decision["status"] != "RESULT_VERIFIED" or not final:
        raise ImportBlocked("LIVE_RESULT_NOT_VERIFIED")
    if (final["primary_provider"] != "openligadb" or final["primary_source_match_id"] != "83237"
            or final["start_time"] != "2026-11-20T19:30:00+00:00"
            or (final["home_score"], final["away_score"]) != (1, 1)):
        raise ImportBlocked("REVIEWED_EVENT_CHANGED")
    provenance = []
    expected = {
        "openligadb": ("83237", "https://api.openligadb.de/getmatchdata/bl1/2026/10"),
        "goaloo": ("3018869", "https://football.goaloo.com/jsData/matchResult/json/2026-2027/s8_en.json"),
    }
    for provider, (identity, url) in expected.items():
        rows = [r for r in report["observations"] if r["provider"] == provider and r["source_match_id"] == identity]
        if len(rows) != 1 or not rows[0].get("final_confirmed") or rows[0].get("source_url") != url:
            raise ImportBlocked("EXPLICIT_FINAL_PROVENANCE_REQUIRED")
        row = rows[0]
        stamp = utc(row.get("observed_at"))
        requests = [r for r in report.get("requests", []) if r.get("url") == url and r.get("http_status") == 200]
        if stamp is None or not timedelta(0) <= now - stamp <= timedelta(minutes=10):
            raise ImportBlocked("LIVE_EVIDENCE_STALE_OR_FUTURE")
        if len(requests) != 1 or not re.fullmatch(r"[0-9a-f]{64}", requests[0].get("sha256", "")):
            raise ImportBlocked("SOURCE_RESPONSE_HASH_REQUIRED")
        request_stamp = utc(requests[0].get("observed_at"))
        if request_stamp is None or not timedelta(0) <= stamp - request_stamp <= timedelta(minutes=2):
            raise ImportBlocked("SOURCE_REQUEST_TIME_INVALID")
        provenance.append({"provider": provider, "source_match_id": identity,
                           "source_url": url, "observed_at": stamp.isoformat(),
                           "response_sha256": requests[0]["sha256"]})
    licensed = next(r for r in rows_for(report, "openligadb", "83237"))
    return {
        "event": {"provider": "openligadb", "provider_match_id": "83237", "league_id": "de.bl1",
                  "league_name": "Bundesliga", "season": "2026-2027", "home_team": licensed["home_team"],
                  "away_team": licensed["away_team"], "home_source_id": 40, "away_source_id": 65,
                  "start_time": final["start_time"], "status": "FINAL", "home_score": 1, "away_score": 1},
        "verified_at": final["verified_at"], "licence": "ODbL-1.0", "attribution": ATTRIBUTION,
        "licence_url": "https://www.openligadb.de/lizenz", "provenance": provenance,
    }


def rows_for(report, provider, identity):
    return [r for r in report["observations"] if r["provider"] == provider and r["source_match_id"] == identity]


def _aware(value):
    # SQLite's test driver drops timezone offsets; PostgreSQL columns are timestamptz.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def resolve_match(session, candidate):
    event = candidate["event"]
    kickoff = utc(event["start_time"])
    rows = session.scalars(select(ActiveMatch).where(or_(
        (ActiveMatch.provider == "openligadb") & (ActiveMatch.provider_match_id == "83237"),
        (ActiveMatch.provider == "goaloo") & (ActiveMatch.provider_match_id == "3018869"),
        (ActiveMatch.start_time >= kickoff - timedelta(hours=24)) &
        (ActiveMatch.start_time < kickoff + timedelta(hours=24)),
    )).with_for_update()).all()
    expected_pair = ("de.bayern-muenchen", "de.1-fc-koeln")
    matches = []
    for row in rows:
        native = (row.provider, row.provider_match_id) in {("openligadb", "83237"), ("goaloo", "3018869")}
        pair = (team_key(row.provider, row.home_team, reviewed=True),
                team_key(row.provider, row.away_team, reviewed=True))
        if not native and pair != expected_pair:
            continue
        allowed_leagues = {"de.bl1"} if row.provider == "openligadb" else {"8", "de.bl1"}
        if (not native or pair != expected_pair or _aware(row.start_time) != kickoff
                or row.league_id not in allowed_leagues or row.season != event["season"]):
            raise ImportBlocked("EXISTING_FIXTURE_IDENTITY_CONFLICT")
        if row.status not in {"SCHEDULED", "LIVE", "FINISHED"}:
            raise ImportBlocked("EXISTING_FIXTURE_STATUS_CONFLICT")
        matches.append(row)
    if len(matches) > 1:
        raise ImportBlocked("MULTIPLE_OPERATIONAL_MATCHES_REFUSE_MERGE")
    return matches[0] if matches else None


def prepare_sql(session, candidate, *, now, retention_days):
    """Match, result and complete retry snapshot share one SQL commit."""
    if any(not timedelta(0) <= now - utc(row["observed_at"]) <= timedelta(minutes=10)
           for row in candidate["provenance"]):
        raise ImportBlocked("LIVE_EVIDENCE_EXPIRED_BEFORE_SQL")
    journal = session.scalar(select(SystemSetting).where(SystemSetting.key == KEY).with_for_update())
    match = resolve_match(session, candidate)
    if journal:
        snapshot = journal.value
        if snapshot.get("version") != 1 or any(snapshot.get("candidate", {}).get(field) != candidate[field]
                                               for field in ("event", "licence", "attribution", "licence_url")):
            raise ImportBlocked("IMMUTABLE_IMPORT_CONFLICT")
        if match is None or str(match.id) != snapshot["match_id"] or match.status != "FINISHED":
            raise ImportBlocked("JOURNALED_MATCH_MISSING_OR_REPLACED")
        result = session.scalar(select(MatchResult).where(MatchResult.match_id == match.id).with_for_update())
        if (result is None or str(result.id) != snapshot["result_id"] or result.status != "FINAL"
                or (result.home_score, result.away_score) != (1, 1) or result.provider != "openligadb"
                or result.provider_result_id != "83237" or result.result_source != RESULT_SOURCE
                or result.finished_at is not None or result.verified_at is None
                or _aware(result.verified_at) != utc(snapshot["candidate"]["verified_at"])):
            raise ImportBlocked("JOURNALED_RESULT_CHANGED")
        if snapshot["archive_rows"] != archive_rows_for(match, result, snapshot["candidate"]):
            raise ImportBlocked("JOURNALED_ARCHIVE_SNAPSHOT_CHANGED")
        return journal, False
    if match is None:
        event = candidate["event"]
        match = ActiveMatch(provider="openligadb", provider_match_id="83237", league_id="de.bl1",
                            league_name=event["league_name"], season=event["season"],
                            home_team=event["home_team"], away_team=event["away_team"],
                            start_time=utc(event["start_time"]), status="FINISHED",
                            expires_at=utc(event["start_time"]) + timedelta(hours=48))
        session.add(match)
        session.flush()
    result = session.scalar(select(MatchResult).where(MatchResult.match_id == match.id).with_for_update())
    if result:
        # Never replace a prior result, even if its score happens to agree.
        raise ImportBlocked("EXISTING_UNJOURNALED_RESULT_REQUIRES_REVIEW")
    match.status = "FINISHED"
    match.expires_at = utc(candidate["event"]["start_time"]) + timedelta(hours=48)
    result = MatchResult(match_id=match.id, provider="openligadb", provider_result_id="83237",
                         home_score=1, away_score=1, status="FINAL", finished_at=None,
                         verified_at=utc(candidate["verified_at"]), result_source=RESULT_SOURCE)
    session.add(result)
    session.flush()
    journal = SystemSetting(key=KEY, value={"version": 1, "phase": "SQL_COMMITTED", "candidate": candidate,
                            "match_id": str(match.id), "result_id": str(result.id),
                            "archive_rows": archive_rows_for(match, result, candidate), "receipts": {}})
    session.add(journal)
    session.commit()
    return journal, True


def archive_rows_for(match, result, candidate):
    # Licensed primary values are archived. Existing operational provider IDs and names
    # stay unchanged in SQL and are linked separately, never silently re-keyed.
    common = {**candidate["event"], "verified_at": candidate["verified_at"],
              "licence": candidate["licence"], "attribution": ATTRIBUTION,
              "licence_url": candidate["licence_url"], "result_source": RESULT_SOURCE,
              "source_provenance_json": json.dumps(candidate["provenance"], sort_keys=True),
              "operational_provider": match.provider, "operational_provider_match_id": match.provider_match_id}
    return {
        "matches": {**common, "id": str(match.id), "status": "FINISHED"},
        "results": {**common, "match_id": str(match.id), "result_id": str(result.id),
                    "provider_result_id": "83237", "finished_at": None},
    }


def persist_verified(session, store, candidate, *, now, retention_days=14):
    journal, created = prepare_sql(session, candidate, now=now, retention_days=retention_days)
    postgres = {"status": "PASS", "created": created, "match_id": journal.value["match_id"],
                "result_id": journal.value["result_id"], "verified_at": journal.value["candidate"]["verified_at"]}
    receipts = {}
    try:
        for dataset, row in journal.value["archive_rows"].items():
            # JSONB is the durable retry snapshot; Parquet retains the existing
            # UTC timestamp types so the regular incremental archiver can compare
            # its SQL rows to these parts without false string/datetime conflicts.
            row = {field: utc(value) if field in {"start_time", "verified_at", "finished_at"} and value is not None else value
                   for field, value in row.items()}
            receipt = archive_partition(store, dataset=dataset, season="2026-2027", league="de.bl1",
                                        day=DAY, rows=[row], tracker=SQLArchiveTracker(session),
                                        part=PART, retries=1)
            decoded = _verify_object(store, receipt.key, expected_size=receipt.size, expected_rows=1,
                                     expected_checksum=receipt.checksum)
            if decoded != [row]:
                raise ImportBlocked("ARCHIVE_DECODED_CONTENT_MISMATCH")
            receipts[dataset] = {"key": receipt.key, "sha256": receipt.checksum, "row_count": 1,
                                 "size_bytes": receipt.size, "readback": "PASS", "content": "PASS"}
        journal.value = {**journal.value, "phase": "VERIFIED", "receipts": receipts}
        session.commit()
        # Re-read SQL after commits, never report success from an uncommitted ORM object.
        session.expire_all()
        stored = session.get(SystemSetting, KEY)
        result = session.get(MatchResult, UUID(stored.value["result_id"]))
        if (result is None or result.status != "FINAL" or (result.home_score, result.away_score) != (1, 1)
                or result.provider != "openligadb" or result.provider_result_id != "83237"
                or str(result.match_id) != stored.value["match_id"] or result.result_source != RESULT_SOURCE
                or result.verified_at is None or result.finished_at is not None
                or stored.value["phase"] != "VERIFIED" or stored.value["receipts"] != receipts
                or _aware(result.verified_at) != utc(stored.value["candidate"]["verified_at"])):
            raise ImportBlocked("SQL_READBACK_MISMATCH")
        return {"status": "VERIFIED", "postgres": {"status": "PASS", "created": created,
                "match_id": stored.value["match_id"], "result_id": str(result.id),
                "verified_at": _aware(result.verified_at).isoformat(), "home_score": 1, "away_score": 1},
                "r2": {"status": "PASS", "receipts": receipts}, "cleanup": "NOT_RUN"}
    except Exception as exc:
        session.rollback()
        # SQL and the immutable snapshot remain. Existing R2 parts are never removed.
        # FAILED archive jobs are resumed by archive_partition on the next fresh run.
        raise ImportIncomplete({"status": "FAILED_RETRYABLE", "postgres": postgres,
                                "r2": {"status": "INCOMPLETE_REVERIFY_ON_RETRY", "receipts": receipts},
                                "error_code": str(exc) if isinstance(exc, ImportBlocked)
                                else "ARCHIVE_OR_FINAL_SQL_VERIFICATION_FAILED"}) from None


@contextmanager
def production_session(settings):
    from app.db.session import engine
    from sqlalchemy.orm import Session
    if engine is None:
        raise ImportBlocked("DATABASE_NOT_CONFIGURED")
    event_lock = lock_id(fixture_key({"provider": "openligadb", "provider_match_id": "83237",
                                    "league_id": "de.bl1", "start_time": "2026-11-20T19:30:00Z",
                                    "home_team": "FC Bayern München", "away_team": "1. FC Köln"}))
    with engine.connect() as connection:
        # A connection-scoped lock survives the archive tracker's commits. A second
        # importer fails immediately; no expiring lease can admit concurrent writers.
        held = []
        try:
            from app.storage.lifecycle import LOCK_ID as lifecycle_lock
            for key in (lifecycle_lock, LOCK_ID, event_lock):
                acquired = connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar_one()
                connection.commit()
                if not acquired:
                    raise ImportBlocked("IMPORT_ALREADY_RUNNING")
                held.append(key)
            with Session(bind=connection, expire_on_commit=False) as session:
                yield session
        finally:
            connection.rollback()
            for key in reversed(held):
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            connection.commit()


def run(*, persist=False, probe=None, settings=None, session_context=None, store_factory=None):
    settings = settings or get_settings()
    output = {"stage": 3, "mode": "PERSIST" if persist else "DRY_RUN", "status": "BLOCKED",
              "postgres": {"status": "NOT_RUN"}, "r2": {"status": "NOT_RUN"}, "cleanup": "NOT_RUN",
              "scope": {"league": "de.bl1", "date": DAY.isoformat(), "openligadb_id": "83237", "goaloo_id": "3018869"}}
    storage_started = False
    try:
        report = (probe or result_probe.run)(DAY, 10, "3018869")
        output["source_verification"] = report
        candidate = candidate_from_live(report, datetime.now(timezone.utc))
        output["candidate"] = candidate
        output["archive_plan"] = {dataset: f"{dataset}/season=2026-2027/league=de.bl1/date={DAY}/part={PART}.parquet"
                                  for dataset in ("matches", "results")}
        missing = (["DATABASE_URL"] if settings.database_url_missing else []) + list(settings.r2_missing_env)
        invalid = (["DATABASE_URL"] if not settings.database_url_missing and not settings.database_url_valid else []) + list(settings.r2_invalid_env)
        output.update(missing_env=missing, invalid_env=invalid)
        if not persist:
            output["status"] = "DRY_RUN_PASS"
            return output
        if missing or invalid:
            raise ImportBlocked("STORAGE_NOT_CONFIGURED")
        from app.jobs.archive_preflight import check_postgres
        readiness = check_postgres(settings)
        output["postgres_preflight"] = readiness
        if readiness != {"connection": "PASS", "schema": "PASS"}:
            raise ImportBlocked("POSTGRES_SCHEMA_NOT_READY")
        from app.storage.r2 import R2ObjectStore
        with (session_context or production_session)(settings) as session:
            store = (store_factory or R2ObjectStore)()
            storage_started = True
            output.update(persist_verified(session, store, candidate, now=datetime.now(timezone.utc),
                                           retention_days=settings.active_state_retention_days))
    except ImportIncomplete as exc:
        output.update(exc.progress)
    except ImportBlocked as exc:
        output.update(status="BLOCKED", error_code=str(exc))
    except Exception:
        # Commit acknowledgement or upload failures can be ambiguous. Report UNKNOWN,
        # never pretend the operation was rolled back across both independent stores.
        output.update(status="FAILED_RETRYABLE", error_code="IMPORT_FAILED_SAFE_RETRY_REQUIRED")
    if storage_started and output["postgres"]["status"] == "NOT_RUN":
        output["postgres"]["status"] = "UNKNOWN_RECONCILE_ON_RETRY"
        output["r2"]["status"] = "UNKNOWN_RECONCILE_ON_RETRY"
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default: live reads only, no storage connections")
    mode.add_argument("--persist", action="store_true", help="fresh live verification then exactly one SQL/R2 import")
    args = parser.parse_args(argv)
    report = run(persist=args.persist)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["status"] in {"DRY_RUN_PASS", "VERIFIED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
