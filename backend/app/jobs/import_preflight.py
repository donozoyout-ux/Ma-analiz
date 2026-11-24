"""Stage 7D bounded storage inspection: SELECT/catalog/HEAD/GET only, no audit writes."""
from contextlib import closing
from datetime import datetime, timezone

from sqlalchemy import inspect, select, text

from app.models import ActiveMatch, SystemSetting
from app.providers.identity import REVIEWED_IMPORT_KEY
from app.providers.multileague import digest
from app.providers.result_sources import utc
from app.storage.lifecycle import sql_snapshot


def flags():
    return {"SQL_WRITE": "NOT_RUN", "R2_WRITE": "NOT_RUN", "LIVE_DELETE": "NOT_RUN",
            "FINAL_IMPORT": "BLOCKED", "AUTO_RETENTION_DELETE_ENABLED": False,
            "OPERATIONAL_DELETE_ENABLED": False}


def protected_snapshot(session):
    """Bounded immutable baseline; intentionally includes the existing aliases/journal."""
    from app.models import MatchResult
    matches = session.scalars(select(ActiveMatch).where(
        ((ActiveMatch.provider == "openligadb") & (ActiveMatch.provider_match_id == "83237")) |
        ((ActiveMatch.provider == "goaloo") & (ActiveMatch.provider_match_id == "3018869")))).all()
    rows = []
    for match in matches:
        results = session.scalars(select(MatchResult).where(MatchResult.match_id == match.id)).all()
        rows.append({"match": sql_snapshot(match), "results": [sql_snapshot(r) for r in results]})
    keys = [REVIEWED_IMPORT_KEY, "source-identity:" + digest(["openligadb", "83237"]),
            "source-identity:" + digest(["goaloo", "3018869"])]
    return digest({"rows": sorted(rows, key=digest), "metadata": {
        key: item.value if (item := session.get(SystemSetting, key)) else None for key in keys}})


def candidate_scope(session, candidate):
    """One league/day plus exact native ID: detects new/conflicting rows after review."""
    from datetime import timedelta
    row = candidate["evidence"]["identity"]
    kickoff = utc(row["start_time"])
    matches = session.scalars(select(ActiveMatch).where(
        ((ActiveMatch.provider == row["provider"]) & (ActiveMatch.provider_match_id == row["source_match_id"])) |
        ((ActiveMatch.league_id.in_(["de.bl1", "bl1", "8"])) &
         (ActiveMatch.start_time >= kickoff - timedelta(hours=24)) &
         (ActiveMatch.start_time <= kickoff + timedelta(hours=24)))).limit(101)).all()
    if len(matches) > 100:
        from app.storage.import_review import ReviewBlocked
        raise ReviewBlocked("SQL_SCOPE_CAPACITY_EXCEEDED")
    keys = ["review-source:" + candidate["source_key"], "controlled-import:" + candidate["source_key"],
            "source-identity:" + digest([row["provider"], row["source_match_id"]])]
    return {"protected_sha256": protected_snapshot(session), "scope_sha256": digest({
        "rows": sorted([sql_snapshot(m) for m in matches], key=digest),
        "metadata": {k: v.value if (v := session.get(SystemSetting, k)) else None for k in keys}})}


def required_constraints(connection):
    inspector = inspect(connection)
    expected = {"active_matches": {("provider", "provider_match_id")},
                "match_results": {("match_id",)}, "archive_jobs": {("r2_key",)},
                "system_settings": {("key",)}}
    missing = []
    for table, groups in expected.items():
        found = {tuple(x.get("column_names") or []) for x in inspector.get_unique_constraints(table)}
        found |= {tuple(x.get("column_names") or []) for x in inspector.get_indexes(table) if x.get("unique")}
        found.add(tuple(inspector.get_pk_constraint(table).get("constrained_columns") or []))
        missing.extend(f"{table}:{','.join(g)}" for g in groups - found)
    from sqlalchemy import DateTime
    from sqlalchemy.dialects.postgresql import JSONB, UUID
    types = {"active_matches": {"id": UUID, "start_time": DateTime, "expires_at": DateTime},
             "system_settings": {"value": JSONB}}
    for table, expected_types in types.items():
        columns = {c["name"]: c["type"] for c in inspector.get_columns(table)}
        for name, kind in expected_types.items():
            actual = columns.get(name)
            if not isinstance(actual, kind) or kind is DateTime and not actual.timezone:
                missing.append(f"{table}:{name}:TYPE_INCOMPATIBLE")
    return {"status": "PASS" if not missing else "BLOCKED", "missing": missing}


def bounded_store(settings):
    from botocore.config import Config
    from app.storage.r2 import R2ObjectStore
    return R2ObjectStore(settings=settings, client_config=Config(connect_timeout=5, read_timeout=10,
        retries={"total_max_attempts": 1}))


def run(settings=None, *, candidate=None):
    from app.api.connections import r2_check, schema_report
    from app.core.config import get_settings
    from app.jobs.retention import capacity, storage_session, target_check
    from app.jobs.retention_errors import error_code
    cfg = settings or get_settings()
    report = {"stage": "7D", "mode": "READ_ONLY_PREFLIGHT", "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "BLOCKED", "postgres": "NOT_RUN", "r2": "NOT_RUN", "protected_target": {"status": "NOT_RUN"},
        "missing_env": (["DATABASE_URL"] if cfg.database_url_missing else []) + list(cfg.r2_missing_env),
        "invalid_env": ([] if cfg.database_url_valid or cfg.database_url_missing else ["DATABASE_URL"]) + list(cfg.r2_invalid_env),
        **flags()}
    if cfg.auto_retention_delete_enabled or cfg.operational_delete_enabled:
        report["error_code"] = "DELETE_FLAGS_MUST_BE_DISABLED"
        return report
    if report["missing_env"] or report["invalid_env"]:
        report["error_code"] = "STORAGE_CONFIGURATION_INCOMPLETE"
        return report
    phase = "SQL_CONNECTION"
    try:
        with storage_session(cfg, readonly=True) as session:
            report["postgres"] = "CONNECTED_READ_ONLY"
            if session.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
                raise RuntimeError()
            phase = "SCHEMA_CHECK"
            schema = schema_report(session.connection())
            # Never expose arbitrary catalog values or database identifiers.
            report["schema"] = {"status": schema["schema"], "issues": schema["schema_issues"],
                                "expected_revisions": schema["expected_revisions"]}
            if schema["schema"] != "READY":
                report["error_code"] = "IMPORT_SCHEMA_NOT_READY"
                return report
            report["constraints"] = required_constraints(session.connection())
            if report["constraints"]["status"] != "PASS":
                report["error_code"] = "IMPORT_STORAGE_CONTRACT_INCOMPATIBLE"
                return report
            report["capacity"] = capacity(session.execute(text("SELECT pg_database_size(current_database())")).scalar_one())
            phase = "R2_VERIFICATION"
            report["r2"] = r2_check(cfg)
            if report["r2"]["status"] != "ACCESSIBLE":
                report["error_code"] = "R2_READ_ONLY_PREFLIGHT_FAILED"
                return report
            phase = "R2_VERIFICATION"
            store = bounded_store(cfg)
            with closing(store.client):
                report["protected_target"] = target_check(session, store)
            if report["protected_target"]["status"] not in {"VERIFIED", "ARCHIVED_ONLY"}:
                report["error_code"] = "EXISTING_TARGET_CHAIN_NOT_VERIFIED"
                return report
            report["protected_sha256"] = protected_snapshot(session)
            if candidate:
                report["candidate_scope"] = candidate_scope(session, candidate)
            report.update(status="PASS", write_privileges="NOT_EXERCISED", migrations="NOT_RUN")
    except Exception as exc:
        report.update(error_code=error_code(exc, phase), failure_stage=phase)
    return report
