"""Durable review/journal: offline by default; scoped Stage 7D operator capability for PG."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import threading
from uuid import UUID

from sqlalchemy import func, select
from app.models import ActiveMatch, SystemSetting
from app.analysis.import_review import IDENTITY_FIELDS, STATES
from app.analysis.import_review import fresh
from app.providers.multileague import digest
from app.providers.result_sources import utc
from app.storage.lifecycle import aware, sql_snapshot
from app.storage.r2 import InMemoryObjectStore

OFFLINE_LOCK = threading.Lock()
MAX_EVIDENCE = 1000
MAX_REVIEWS = 2000


class ReviewBlocked(RuntimeError):
    """Fixed safe codes only."""


def offline_only(session, store=None):
    from app.storage.import_permission import check_permission
    if check_permission(session, store):
        return
    if session.get_bind().dialect.name != "sqlite" or (store is not None and not isinstance(store, InMemoryObjectStore)):
        raise ReviewBlocked("STAGE7C_PRODUCTION_PERSIST_DISABLED")


@contextmanager
def offline_import_lock(session, store):
    offline_only(session, store)
    if not OFFLINE_LOCK.acquire(blocking=False):
        raise ReviewBlocked("OFFLINE_IMPORT_ALREADY_RUNNING")
    try:
        yield
    finally:
        OFFLINE_LOCK.release()


def immutable_setting(session, key, value):
    offline_only(session)
    current = session.get(SystemSetting, key)
    if current:
        if current.value != value:
            raise ReviewBlocked("IMMUTABLE_REVIEW_RECORD_CHANGED")
    else:
        session.add(SystemSetting(key=key, value=deepcopy(value)))
        session.flush()
    return key


class ReviewRepository:
    """Uses protected system_settings, never collector cache. No schema migration."""
    def __init__(self, session):
        self.session = session

    def record(self, candidate, *, now):
        offline_only(self.session)
        evidence = candidate["evidence"]
        version = digest(evidence)
        if version != candidate["evidence_sha256"]:
            raise ReviewBlocked("EVIDENCE_VERSION_CHANGED")
        identity = evidence["identity"]
        row = evidence["observation"]
        expected_key = digest([row["provider"], row["source_match_id"]] if row["source_match_id"] else
                              [row["provider"], row["league"], row["record_fingerprint"]])
        if expected_key != candidate["source_key"] or identity != {k: row[k] for k in IDENTITY_FIELDS}:
            raise ReviewBlocked("SOURCE_IDENTITY_CHANGED")
        if candidate["state"] not in {"REVIEW_REQUIRED", "CONFLICT"}:
            raise ReviewBlocked("SOURCE_REVIEW_STATE_INVALID")
        key = "review-source:" + expected_key
        current = self.session.get(SystemSetting, key)
        state = deepcopy(current.value) if current else {"identity": identity, "versions": [], "reviews": []}
        if version not in state["versions"]:
            if len(state["versions"]) >= 32:
                raise ReviewBlocked("REVIEW_HISTORY_CAPACITY_REACHED")
            state["versions"].append(version)
        conflict = state["identity"] != identity
        blockers = sorted(set(candidate["blockers"] + [k.upper() for k, value in evidence["checks"].items() if value is not True]))
        value = {"evidence": evidence, "evidence_sha256": version,
                 "state": "CONFLICT" if conflict else candidate["state"], "blockers": candidate["blockers"],
                 "recorded_at": now.isoformat(), "source_key": expected_key}
        value["blockers"] = blockers
        existing = self.session.get(SystemSetting, "review-evidence:" + version)
        if not existing:
            count = self.session.scalar(select(func.count()).select_from(SystemSetting).where(SystemSetting.key.like("review-evidence:%")))
            if count >= MAX_EVIDENCE:
                raise ReviewBlocked("GLOBAL_REVIEW_CAPACITY_REACHED")
            immutable_setting(self.session, "review-evidence:" + version, value)
        elif any(existing.value[k] != value[k] for k in ("evidence", "evidence_sha256", "source_key")):
            raise ReviewBlocked("IMMUTABLE_REVIEW_RECORD_CHANGED")
        if conflict:
            state["conflict"] = True
        if current:
            current.value = state
        else:
            self.session.add(SystemSetting(key=key, value=state))
        self.session.flush()
        return version

    def decide(self, version, *, state, reviewer, reason, now, previous=None, authorization_ref=None):
        offline_only(self.session)
        if state not in STATES or not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 80 or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
            raise ReviewBlocked("REVIEW_ACTOR_AND_REASON_REQUIRED")
        stored = self.session.get(SystemSetting, "review-evidence:" + version)
        if stored is None or digest(stored.value["evidence"]) != version:
            raise ReviewBlocked("DURABLE_EVIDENCE_REQUIRED")
        source = self.session.get(SystemSetting, "review-source:" + stored.value["source_key"])
        history = deepcopy(source.value)
        head = history["reviews"][-1] if history["reviews"] else None
        if previous != head:
            raise ReviewBlocked("REVIEW_HEAD_CHANGED")
        if len(history["reviews"]) >= 64:
            raise ReviewBlocked("REVIEW_HISTORY_CAPACITY_REACHED")
        count = self.session.scalar(select(func.count()).select_from(SystemSetting).where(SystemSetting.key.like("review-audit:%")))
        if count >= MAX_REVIEWS:
            raise ReviewBlocked("GLOBAL_REVIEW_CAPACITY_REACHED")
        if state == "APPROVED_FIXTURE":
            if (stored.value["blockers"] or history.get("conflict") or stored.value["state"] == "CONFLICT"
                    or not all(v is True for v in stored.value["evidence"]["checks"].values())
                    or not fresh(stored.value["evidence"]["observation"]["response_received_at"], now)):
                raise ReviewBlocked("TECHNICAL_BLOCKERS_CANNOT_BE_OVERRIDDEN")
            if history["versions"][-1] != version:
                raise ReviewBlocked("LATEST_SOURCE_VERSION_REQUIRED")
        review = {"evidence_sha256": version, "state": state, "reviewer": reviewer, "reason": reason,
                  "source_version": stored.value["evidence"]["source_version"],
                  "decided_at": now.isoformat(), "previous": previous, "schema_version": 1}
        if authorization_ref:
            review["authorization_ref"] = authorization_ref
        review_id = digest(review)
        immutable_setting(self.session, "review-audit:" + review_id, review)
        history["reviews"].append(review_id)
        source.value = history
        self.session.flush()
        return review_id

    def approved(self, candidate, review_id, *, now):
        stored = self.session.get(SystemSetting, "review-evidence:" + candidate["evidence_sha256"])
        review = self.session.get(SystemSetting, "review-audit:" + review_id)
        source = self.session.get(SystemSetting, "review-source:" + candidate["source_key"])
        if (not stored or not review or not source or digest(review.value) != review_id
                or stored.value["evidence"] != candidate["evidence"]
                or review.value["state"] != "APPROVED_FIXTURE"
                or review.value["source_version"] != candidate["evidence"]["source_version"]
                or source.value["reviews"][-1] != review_id or source.value.get("conflict")
                or source.value["versions"][-1] != candidate["evidence_sha256"]
                or candidate["blockers"]):
            raise ReviewBlocked("CURRENT_DURABLE_APPROVAL_REQUIRED")
        stamp = utc(review.value["decided_at"])
        if stamp is None or not 0 <= (now - stamp).total_seconds() <= 600:
            raise ReviewBlocked("REVIEW_EXPIRED_OR_FUTURE")
        return review.value


def prepare_fixture_sql(session, candidate, *, review_id, now, authorization_ref=None):
    """Stage 3 journal extension; PG requires the Stage 7D session-local capability."""
    offline_only(session)
    ReviewRepository(session).approved(candidate, review_id, now=now)
    row = candidate["evidence"]["observation"]
    if (row["provider"], row["source_match_id"]) in {("openligadb", "83237"), ("goaloo", "3018869")}:
        raise ReviewBlocked("PRESERVE_EXISTING_VERIFIED_IMPORT_ROUTE")
    if (row["provider"] != "openligadb" or row["league"] != "de.bl1" or row["source_status"] != "NOT_FINISHED"
            or utc(row["start_time"]) <= now):
        raise ReviewBlocked("REVIEWED_FUTURE_FIXTURE_ONLY")
    journal_key = "controlled-import:" + candidate["source_key"]
    journal = session.get(SystemSetting, journal_key)
    expected = {k: row[k] for k in IDENTITY_FIELDS}
    matches = session.scalars(select(ActiveMatch).where(
        ((ActiveMatch.provider == row["provider"]) & (ActiveMatch.provider_match_id == row["source_match_id"])) |
        ((ActiveMatch.start_time >= utc(row["start_time"]) - timedelta(hours=24)) &
         (ActiveMatch.start_time <= utc(row["start_time"]) + timedelta(hours=24)) & (ActiveMatch.home_team == row["home_team"]) &
         (ActiveMatch.away_team == row["away_team"])))).all()
    # Also detect the reviewed Bayern alias without broadening its mapping.
    from app.providers.identity import reviewed_fixture_key
    if reviewed_fixture_key({"provider": row["provider"], "provider_match_id": row["source_match_id"],
        "league_id": row["league"], "home_team": row["home_team"], "away_team": row["away_team"], "start_time": row["start_time"]}):
        raise ReviewBlocked("PRESERVE_EXISTING_VERIFIED_IMPORT_ROUTE")
    if journal:
        value = journal.value
        match = session.get(ActiveMatch, UUID(value["match_id"]))
        if not match:
            # A permanent identity reservation prevents resurrection after retention.
            raise ReviewBlocked("OPERATIONAL_ROW_ABSENT_REQUIRES_ARCHIVE_RECONCILIATION")
        if value["identity"] != expected or value["sql_snapshot"] != sql_snapshot(match):
            raise ReviewBlocked("JOURNALED_SQL_OR_IDENTITY_CHANGED")
        if len(matches) != 1 or matches[0].id != match.id:
            raise ReviewBlocked("MULTIPLE_OPERATIONAL_MATCHES_REFUSE_MERGE")
        return journal, match, False
    if matches:
        raise ReviewBlocked("EXISTING_FIXTURE_REQUIRES_EXPLICIT_ALIAS_REVIEW")
    from app.providers.identity import reviewed_fixture_key as existing_alias
    others = session.scalars(select(ActiveMatch).where(ActiveMatch.provider != row["provider"],
        ActiveMatch.league_id.in_(["de.bl1", "bl1", "8"]),
        ActiveMatch.start_time >= utc(row["start_time"]) - timedelta(hours=24),
        ActiveMatch.start_time <= utc(row["start_time"]) + timedelta(hours=24))).all()
    for other in others:
        reviewed = existing_alias({"provider": other.provider, "provider_match_id": other.provider_match_id,
            "league_id": other.league_id, "home_team": other.home_team, "away_team": other.away_team,
            "start_time": aware(other.start_time)})
        if reviewed and (row["home_source_id"], row["away_source_id"]) != (40, 65):
            continue
        raise ReviewBlocked("UNREVIEWED_CROSS_PROVIDER_IDENTITY_IN_SCOPE")
    reservation = "source-identity:" + digest([row["provider"], row["source_match_id"]])
    if session.get(SystemSetting, reservation):
        raise ReviewBlocked("SOURCE_IDENTITY_ALREADY_RESERVED")
    match = ActiveMatch(provider=row["provider"], provider_match_id=row["source_match_id"], league_id=row["league"],
        league_name="Bundesliga", season=row["season"], home_team=row["home_team"], away_team=row["away_team"],
        start_time=utc(row["start_time"]), status="SCHEDULED", expires_at=utc(row["start_time"]) + timedelta(hours=48),
        last_updated_at=now)
    session.add(match)
    session.flush()
    immutable_setting(session, reservation, {"provider": row["provider"], "provider_match_id": row["source_match_id"],
        "match_id": str(match.id), "season": row["season"], "league_id": row["league"],
        "review_id": review_id, "evidence_sha256": candidate["evidence_sha256"]})
    journal = SystemSetting(key=journal_key, value={"schema_version": 1, "phase": "SQL_COMMITTED", "identity": expected,
        "match_id": str(match.id), "sql_snapshot": sql_snapshot(match), "review_id": review_id,
        "evidence_sha256": candidate["evidence_sha256"], "receipts": []})
    if authorization_ref:
        journal.value = {**journal.value, "operator_grant_sha256": authorization_ref}
    session.add(journal)
    immutable_setting(session, "import-audit:" + digest([candidate["source_key"], "SQL_COMMITTED"]),
        {"phase": "SQL_COMMITTED", "match_id": str(match.id), "review_id": review_id,
         "evidence_sha256": candidate["evidence_sha256"], "at": now.isoformat()})
    session.commit()
    return journal, match, True
