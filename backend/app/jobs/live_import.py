"""Stage 7D manual-only, single-fixture gate. Default is read-only. Never scheduled."""
import argparse
from contextlib import closing
from copy import deepcopy
from datetime import date
from getpass import getpass
import json
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.jobs import import_preflight as preflight
from app.jobs.controlled_import import _finish_offline_archive
from app.jobs.verified_import import fixture_key, production_session
from app.models import SystemSetting
from app.providers.collector_http import now_utc
from app.providers.multileague import digest
from app.providers.trusted_fixture import revalidate
from app.security.import_operator import Operator, OperatorDenied
from app.storage.import_permission import ImmutableImportStore, authorized_scope, check_permission
from app.storage.import_review import ReviewBlocked, ReviewRepository, prepare_fixture_sql


def operator_from_prompt(settings):
    secret = settings.controlled_import_operator_token_sha256
    # Do not request a credential if trusted operator configuration is absent.
    if not secret or not settings.controlled_import_operator_id:
        raise OperatorDenied("IMPORT_OPERATOR_NOT_CONFIGURED")
    return Operator.authenticate(settings.controlled_import_operator_id, secret.get_secret_value(),
                                 getpass("Operator token (hidden): "))


def journal_view(session, source_key):
    item = session.get(SystemSetting, "controlled-import:" + source_key)
    if not item:
        raise ReviewBlocked("IMPORT_JOURNAL_REQUIRED_NO_R2_ONLY_RESTORE")
    return {**deepcopy(item.value), "source_key": source_key,
            "protected_sha256": preflight.protected_snapshot(session)}


def require_current_review(session, journal):
    source = session.get(SystemSetting, "review-source:" + journal["source_key"])
    review = session.get(SystemSetting, "review-audit:" + journal["review_id"])
    versions = source.value.get("versions", []) if source else []
    latest = session.get(SystemSetting, "review-evidence:" + versions[-1]) if versions else None
    if (not source or source.value.get("conflict") or not source.value.get("reviews")
            or source.value.get("identity") != journal["identity"]
            or source.value["reviews"][-1] != journal["review_id"] or not review
            or review.value.get("state") != "APPROVED_FIXTURE" or digest(review.value) != journal["review_id"]
            or not latest or latest.value["evidence"]["source_version"] != review.value["source_version"]
            or digest(latest.value["evidence"]) != versions[-1]
            or latest.value.get("state") == "CONFLICT"):
        raise ReviewBlocked("CURRENT_DURABLE_APPROVAL_REQUIRED")


def persist_in_session(session, store, *, proof, operator, grant, match_id, clock=now_utc, recovery=None):
    """Called only under Stage 3's global -> import -> event locks in production.

    Reuses Stage 7C SQL transaction/journal and Stage 5 immutable archive verifier.
    SQLite/memory callers exercise exactly this path without production writes.
    """
    with authorized_scope(session, store, operator=operator, grant=grant, proof=proof,
                          match_id=match_id, clock=clock, recovery=recovery) as (candidate, payload):
        source_key = candidate["source_key"]
        baseline = preflight.protected_snapshot(session)
        authorization = digest(payload)
        from app.storage.import_review import immutable_setting
        audit_key = "operator-import:" + authorization
        audit_value = {"operator": payload["operator"], "operation": payload["operation"], "reason": payload["reason"],
            "created_at": payload["created_at"], "expires_at": payload["expires_at"],
            "source_key": source_key, "grant_sha256": authorization,
            "evidence_sha256": payload.get("candidate", {}).get("evidence_sha256"),
            "journal_snapshot": payload.get("journal_snapshot")}
        created = False
        if recovery:
            current = journal_view(session, source_key)
            if digest(current) != payload["journal_snapshot"]:
                raise ReviewBlocked("RECOVERY_SNAPSHOT_CHANGED")
            require_current_review(session, current)
        else:
            if baseline != payload["sql_scope"]["protected_sha256"]:
                raise ReviewBlocked("PROTECTED_BASELINE_CHANGED")
            existing = session.get(SystemSetting, "controlled-import:" + source_key)
            if existing:
                current = journal_view(session, source_key)
                if current.get("operator_grant_sha256") != authorization:
                    raise ReviewBlocked("EXISTING_IMPORT_REQUIRES_RECOVERY_APPROVAL")
                require_current_review(session, current)
            else:
                if preflight.candidate_scope(session, candidate) != payload["sql_scope"]:
                    raise ReviewBlocked("APPROVED_SQL_SNAPSHOT_CHANGED")
                # Audit + source identity + match + SQL journal commit atomically.
                immutable_setting(session, audit_key, audit_value)
                repo = ReviewRepository(session)
                original = payload["candidate"]
                repo.record(original, now=clock())
                head = session.get(SystemSetting, "review-source:" + source_key).value["reviews"]
                review_id = repo.decide(original["evidence_sha256"], state="APPROVED_FIXTURE",
                    reviewer=payload["operator"], reason=payload["reason"], now=clock(),
                    previous=head[-1] if head else None, authorization_ref=authorization)
                # Fresh responses may change observation timestamps, never the approved source version.
                repo.record(candidate, now=clock())
                check_permission(session, store)
                _, _, created = prepare_fixture_sql(session, candidate, review_id=review_id, now=clock(),
                                                     authorization_ref=authorization)
        check_permission(session, store)
        # For recovery, audit the attempt before archive tracker's first commit.
        immutable_setting(session, audit_key, audit_value)
        result = _finish_offline_archive(session, store, source_key, now=clock())
        if preflight.protected_snapshot(session) != baseline:
            raise ReviewBlocked("PROTECTED_BASELINE_CHANGED")
        session.commit()
        return {**result, "created": created, "operator_authorization_sha256": authorization,
                "protected_target": "UNCHANGED", "FINAL_IMPORT": "BLOCKED"}


def controlled_persist(cfg, *, proof, operator, grant, match_id, clock=now_utc, recovery=None):
    """No automatic caller. Signature/preflight checked before any writable connection."""
    operation = "ARCHIVE_RECOVERY" if recovery else "FIXTURE_IMPORT"
    operator.verify(grant, now=clock(), match_id=match_id, operation=operation)
    if str(match_id) == "83237":
        raise ReviewBlocked("PRESERVE_EXISTING_VERIFIED_IMPORT_ROUTE")
    if recovery:
        if digest(recovery) != grant["payload"]["journal_snapshot"]:
            raise ReviewBlocked("RECOVERY_SNAPSHOT_CHANGED")
        row = recovery["identity"]
    else:
        if proof is None or not proof.live:
            raise ReviewBlocked("TRUSTED_LIVE_PROOF_REQUIRED")
        candidate = proof.candidate(clock())
        if candidate["blockers"] or candidate["evidence"]["identity"]["source_match_id"] != str(match_id):
            raise ReviewBlocked("CANDIDATE_NOT_ELIGIBLE")
        row = candidate["evidence"]["identity"]
    checked = preflight.run(cfg)
    if checked["status"] != "PASS":
        raise ReviewBlocked("READ_ONLY_PREFLIGHT_REQUIRED")
    event = fixture_key({"provider": row["provider"], "provider_match_id": row["source_match_id"],
        "league_id": row["league"], "home_team": row["home_team"], "away_team": row["away_team"],
        "start_time": row["start_time"]})
    url = cfg.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url, hide_parameters=True, poolclass=NullPool, connect_args={"connect_timeout": 10})
    storage_started = False
    try:
        with production_session(cfg, event_identity=event, engine_override=engine) as session:
            # Preserve TLS/search_path options from DATABASE_URL; do not override libpq options.
            session.execute(text("SET statement_timeout = '30s'"))
            session.execute(text("SET lock_timeout = '5s'"))
            session.commit()
            raw = preflight.bounded_store(cfg)
            with closing(raw.client):
                store = ImmutableImportStore(session, raw)
                storage_started = True
                result = persist_in_session(session, store, proof=proof, operator=operator,
                    grant=grant, match_id=match_id, clock=clock, recovery=recovery)
                return {**result, "SQL_WRITE": "VERIFIED", "R2_WRITE": "VERIFIED"}
    except Exception:
        if storage_started:
            # Even a commit acknowledgement can fail AFTER committing. Never claim rollback/success.
            return {"status": "FAILED_RETRYABLE", "error_code": "IMPORT_RECONCILIATION_REQUIRED",
                "SQL_WRITE": "UNKNOWN_RECONCILE_REQUIRED", "R2_WRITE": "UNKNOWN_REVERIFY_REQUIRED",
                "LIVE_DELETE": "NOT_RUN", "FINAL_IMPORT": "BLOCKED"}
        raise
    finally:
        engine.dispose()


def read_grant(path):
    if not path or path.stat().st_size > 100_000:
        raise OperatorDenied("IMPORT_APPROVAL_FILE_INVALID")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise OperatorDenied("IMPORT_APPROVAL_FILE_INVALID") from None


def write_grant(path, grant):
    if path is None:
        raise OperatorDenied("IMPORT_APPROVAL_PATH_REQUIRED")
    # Never overwrite approvals; signature is not the authentication token.
    with path.open("x", encoding="utf-8") as file:
        json.dump(grant, file, ensure_ascii=True, indent=2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    for name in ("preflight", "dry-run", "approve", "persist", "approve-recovery", "recover"):
        modes.add_argument("--" + name, action="store_true")
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--match-id")
    parser.add_argument("--confirm-match-id")
    parser.add_argument("--reason")
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    output = {"stage": "7D", "status": "BLOCKED", **preflight.flags()}
    try:
        from app.core.config import get_settings
        cfg = get_settings()
        if args.preflight:
            output = preflight.run(cfg)
        elif args.recover or args.approve_recovery:
            # Explicit candidate ID only; never accept an arbitrary source key / saved journal as authority.
            if not args.confirm_match_id or not args.confirm_match_id.isdigit() or args.confirm_match_id == "83237":
                raise ReviewBlocked("EXPLICIT_NEW_MATCH_ID_REQUIRED")
            operator = operator_from_prompt(cfg)
            grant = read_grant(args.approval) if args.recover else None
            if grant:
                operator.verify(grant, now=now_utc(), match_id=args.confirm_match_id, operation="ARCHIVE_RECOVERY")
            checked = preflight.run(cfg)
            if checked["status"] != "PASS":
                raise ReviewBlocked("READ_ONLY_PREFLIGHT_REQUIRED")
            from app.jobs.retention import storage_session
            with storage_session(cfg, readonly=True) as session:
                journal = journal_view(session, digest(["openligadb", args.confirm_match_id]))
                require_current_review(session, journal)
            if args.approve_recovery:
                grant = operator.approve_recovery(journal, reason=args.reason, now=now_utc())
                write_grant(args.approval, grant)
                output.update(status="RECOVERY_APPROVAL_CREATED_NO_WRITES")
            else:
                output.update(controlled_persist(cfg, proof=None, operator=operator, grant=grant,
                    match_id=args.confirm_match_id, recovery=journal))
        else:
            grant = operator = None
            selected_day, selected_id = args.date, args.match_id
            if args.persist:
                operator = operator_from_prompt(cfg)
                grant = read_grant(args.approval)
                operator.verify(grant, now=now_utc(), match_id=args.confirm_match_id, operation="FIXTURE_IMPORT")
                selected_id = args.confirm_match_id
                selected_day = date.fromisoformat(grant["payload"]["candidate"]["evidence"]["identity"]["start_time"][:10])
            if selected_day is None:
                raise ReviewBlocked("EXPLICIT_SINGLE_UTC_DAY_REQUIRED")
            from app.jobs.match_collector import local_state
            with local_state(Path(".trusted-fixture")) as state:
                proof = revalidate(selected_day, match_id=selected_id, state=state)
            candidate = proof.candidate(now_utc())
            output.update(source_evidence=proof.report, candidate=candidate)
            checked = preflight.run(cfg, candidate=candidate)
            output["preflight"] = checked
            if candidate["blockers"] or checked["status"] != "PASS":
                output["error_code"] = "CANDIDATE_OR_PREFLIGHT_BLOCKED"
            elif args.approve:
                operator = operator_from_prompt(cfg)
                grant = operator.approve(proof, reason=args.reason, now=now_utc(), sql_scope=checked["candidate_scope"])
                write_grant(args.approval, grant)
                output.update(status="APPROVAL_CREATED_NO_WRITES")
            elif args.persist:
                output.update(controlled_persist(cfg, proof=proof, operator=operator, grant=grant,
                                                  match_id=selected_id))
            else:
                output.update(status="DRY_RUN_PASS_NOT_AUTHORIZED")
    except (OperatorDenied, ReviewBlocked) as exc:
        # These classes carry code literals from our code only, never remote exception messages.
        output.update(status="BLOCKED", error_code=str(exc))
    except Exception:
        output.update(status="BLOCKED", error_code="IMPORT_CONFIGURATION_OR_EVIDENCE_INVALID")
    rendered = json.dumps(output, ensure_ascii=True, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if output["status"] in {"PASS", "VERIFIED", "DRY_RUN_PASS_NOT_AUTHORIZED",
        "APPROVAL_CREATED_NO_WRITES", "RECOVERY_APPROVAL_CREATED_NO_WRITES"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
