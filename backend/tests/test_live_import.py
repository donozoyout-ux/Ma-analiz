"""Stage 7D synthetic/offline tests. NO production credentials, writes or live fixtures."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from types import SimpleNamespace

import pytest

from test_controlled_import import NOW, POLICIES, candidate, session, synthetic_report
from app.jobs import import_preflight, live_import
from app.models import ActiveMatch, ArchiveJob, MatchResult, SystemSetting
from app.providers import trusted_fixture as tf
from app.providers.evidence_http import EvidenceFailure
from app.providers.multileague import digest
from app.security.import_operator import Operator, OperatorDenied
from app.storage.import_permission import ImmutableImportStore, authorized_scope, check_permission
from app.storage.import_review import ReviewBlocked
from app.storage.r2 import InMemoryObjectStore

TOKEN = "synthetic-offline-operator-token-1234567890"


def operator():
    return Operator.authenticate("offline.operator", hashlib.sha256(TOKEN.encode()).hexdigest(), TOKEN)


def proof(report=None):
    report = synthetic_report() if report is None else report
    return tf.TrustedFixture(report, digest(report), False, tf._ISSUER)


def approved(session, evidence=None):
    evidence = evidence or proof()
    item = evidence.candidate(NOW)
    return operator().approve(evidence, reason="Synthetic exact UTC fixture", now=NOW,
                              sql_scope=import_preflight.candidate_scope(session, item))


def persist(session, root, grant=None, evidence=None, clock=lambda: NOW):
    evidence = evidence or proof()
    return live_import.persist_in_session(session, ImmutableImportStore(session, root), proof=evidence,
        operator=operator(), grant=grant or approved(session, evidence), match_id="900001", clock=clock)


def test_operator_token_is_not_serialized_or_represented():
    op = operator()
    assert TOKEN not in repr(op)
    assert TOKEN not in json.dumps(op.sign({"safe": True}))


@pytest.mark.parametrize("identity,hashed,token,code", [
    (None, None, TOKEN, "NOT_CONFIGURED"),
    ("offline.operator", "f" * 64, TOKEN, "UNAUTHORIZED"),
    ("offline.operator", hashlib.sha256(TOKEN.encode()).hexdigest(), "short", "UNAUTHORIZED")])
def test_wrong_operator_credentials(identity, hashed, token, code):
    with pytest.raises(OperatorDenied, match=code):
        Operator.authenticate(identity, hashed, token)


@pytest.mark.parametrize("change", ["signature", "reason", "id", "expired", "future", "operation"])
def test_approval_signature_expiry_and_scope_before_writes(session, change):
    grant = approved(session)
    at = NOW
    if change == "signature": grant["signature"] = "f" * 64
    if change == "reason": grant["payload"]["reason"] += "tampered"
    if change == "id": grant["payload"]["match_id"] = "900002"
    if change == "expired": at += timedelta(minutes=11)
    if change == "future": at -= timedelta(seconds=1)
    if change == "operation": grant = operator().sign({**grant["payload"], "operation": "ARCHIVE_RECOVERY"})
    root = InMemoryObjectStore()
    with pytest.raises(OperatorDenied):
        persist(session, root, grant=grant, clock=lambda: at)
    assert not root._objects and session.query(ActiveMatch).count() == 0


def test_blocked_candidate_cannot_be_approved(session):
    report = synthetic_report()
    report["leagues"]["de.bl1"]["observations"][1]["start_time"] = "2026-11-21T12:00:00"
    with pytest.raises(OperatorDenied, match="CANDIDATE_NOT_ELIGIBLE"):
        approved(session, proof(report))


@pytest.mark.parametrize("change", ["native", "team", "utc", "hash", "stale", "licence"])
def test_fresh_revalidation_cannot_change_approved_evidence(session, change):
    grant = approved(session)
    report = synthetic_report()
    row, peer = report["leagues"]["de.bl1"]["observations"]
    if change == "native": row["source_match_id"] = "900002"
    if change == "team": peer["home_team"] = "Different Team"
    if change == "utc": peer["start_time"] = "2026-11-21T12:00:00"
    if change == "hash": row["raw_response_sha256"] = "f" * 64
    if change == "licence": report["policies"][0]["reviewed_content_sha256"] = "f" * 64
    if change == "stale":
        row["response_received_at"] = (NOW - timedelta(minutes=11)).isoformat()
    with pytest.raises(ReviewBlocked):
        persist(session, InMemoryObjectStore(), grant=grant, evidence=proof(report))
    assert session.query(ActiveMatch).count() == 0


def test_saved_json_and_injected_transport_cannot_open_postgres_scope(session):
    grant = approved(session)
    pg = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    with pytest.raises(ReviewBlocked, match="TRUSTED_LIVE_PROOF_REQUIRED"):
        with authorized_scope(pg, object(), operator=operator(), grant=grant, proof=proof(),
                              match_id="900001", clock=lambda: NOW):
            pytest.fail("production permission granted")
    altered = proof(); altered.report["date_utc"] = "2026-11-22"
    with pytest.raises(EvidenceFailure, match="TRUSTED_PROOF"):
        altered.candidate(NOW)
    invalid = tf.TrustedFixture(synthetic_report(), digest(synthetic_report()), True, object())
    with pytest.raises(EvidenceFailure): invalid.candidate(NOW)


def test_session_store_deadline_capability_and_no_delete(session):
    root = InMemoryObjectStore()
    wrapper = ImmutableImportStore(session, root)
    grant = approved(session)
    current = [NOW]
    with authorized_scope(session, wrapper, operator=operator(), grant=grant, proof=proof(),
                          match_id="900001", clock=lambda: current[0]):
        assert check_permission(session, wrapper)
        with pytest.raises(ReviewBlocked): check_permission(object(), wrapper)
        with pytest.raises(ReviewBlocked): check_permission(session, object())
        with pytest.raises(ReviewBlocked): wrapper.delete_object("anything")
        with pytest.raises(ReviewBlocked): wrapper.put_object("anything", b"unsafe")
        with pytest.raises(ReviewBlocked): wrapper.put_object_if_absent("other", b"unsafe")
        current[0] += timedelta(minutes=11)
        with pytest.raises(ReviewBlocked): check_permission(session)
    assert not check_permission(session) and not root._objects


def test_fixture_uuid_idempotency_archive_and_atomic_operator_audit(session):
    grant = approved(session)
    root = InMemoryObjectStore()
    first = persist(session, root, grant=grant)
    objects = deepcopy(root._objects)
    second = persist(session, root, grant=grant)
    assert first["created"] and not second["created"] and first["match_id"] == second["match_id"]
    assert first["protected_target"] == "UNCHANGED" and first["status"] == "OFFLINE_VERIFIED"
    assert root._objects == objects and len(objects) == 1
    match = session.query(ActiveMatch).one()
    assert match.status == "SCHEDULED" and session.query(MatchResult).count() == 0
    assert session.query(ArchiveJob).one().status == "VERIFIED"
    audit = session.get(SystemSetting, "operator-import:" + digest(grant["payload"]))
    assert audit.value["operator"] == "offline.operator" and TOKEN not in json.dumps(audit.value)
    assert first["receipts"][0]["content"] == first["receipts"][0]["sql_snapshot"] == "PASS"


def test_sql_scope_changed_after_review_blocks_without_archive(session):
    grant = approved(session)
    item = candidate()
    session.add(SystemSetting(key="source-identity:" + item["source_key"], value={"occupied": True}))
    session.commit()
    root = InMemoryObjectStore()
    with pytest.raises(ReviewBlocked, match="SQL_SNAPSHOT_CHANGED"):
        persist(session, root, grant=grant)
    assert not root._objects and session.query(ActiveMatch).count() == 0


def test_sql_commit_failure_rolls_back_every_initial_write(session, monkeypatch):
    grant = approved(session)
    def fail(): raise RuntimeError("SECRET DATABASE URL")
    monkeypatch.setattr(session, "commit", fail)
    root = InMemoryObjectStore()
    with pytest.raises(RuntimeError): persist(session, root, grant=grant)
    session.rollback()
    assert session.query(ActiveMatch).count() == 0 and not root._objects
    assert session.get(SystemSetting, "operator-import:" + digest(grant["payload"])) is None


@pytest.mark.parametrize("acknowledged", [False, True])
def test_sql_partial_ack_recovery_no_second_uuid(session, monkeypatch, acknowledged):
    grant = approved(session)
    commit = session.commit
    def fail():
        if acknowledged: commit()
        raise TimeoutError("secret SQL acknowledgement")
    monkeypatch.setattr(session, "commit", fail)
    root = InMemoryObjectStore()
    with pytest.raises(TimeoutError): persist(session, root, grant=grant)
    session.rollback()
    monkeypatch.setattr(session, "commit", commit)
    assert persist(session, root, grant=grant)["status"] == "OFFLINE_VERIFIED"
    assert session.query(ActiveMatch).count() == 1


@pytest.mark.parametrize("failure", ["upload", "upload_ack", "corrupt", "final_sql_ack"])
def test_archive_partial_failures_and_explicit_recovery(session, monkeypatch, failure):
    root = InMemoryObjectStore()
    grant = approved(session)
    put, get, commit = root.put_object_if_absent, root.get_object, session.commit
    def failed_put(*a, **kw):
        if failure == "upload_ack": put(*a, **kw)
        raise TimeoutError("secret upload detail")
    def failed_commit():
        journal = session.get(SystemSetting, "controlled-import:" + candidate()["source_key"])
        if journal and journal.value["phase"] == "ARCHIVE_VERIFIED":
            commit()
            raise TimeoutError("secret SQL acknowledgement")
        commit()
    if failure in {"upload", "upload_ack"}: monkeypatch.setattr(root, "put_object_if_absent", failed_put)
    if failure == "corrupt": monkeypatch.setattr(root, "get_object", lambda key: b"corrupt")
    if failure == "final_sql_ack": monkeypatch.setattr(session, "commit", failed_commit)
    with pytest.raises(Exception): persist(session, root, grant=grant)
    session.rollback()
    match_id = session.query(ActiveMatch).one().id
    before = deepcopy(root._objects)
    monkeypatch.setattr(root, "put_object_if_absent", put)
    monkeypatch.setattr(root, "get_object", get)
    monkeypatch.setattr(session, "commit", commit)
    journal = live_import.journal_view(session, candidate()["source_key"])
    recovery_grant = operator().approve_recovery(journal, reason="Synthetic explicit repair", now=NOW)
    result = live_import.persist_in_session(session, ImmutableImportStore(session, root), proof=None,
        operator=operator(), grant=recovery_grant, match_id="900001", clock=lambda: NOW, recovery=journal)
    assert result["status"] == "OFFLINE_VERIFIED" and session.query(ActiveMatch).one().id == match_id
    if before: assert root._objects == before


def test_recovery_snapshot_and_revocation_blocked(session):
    root = InMemoryObjectStore(); persist(session, root)
    journal = live_import.journal_view(session, candidate()["source_key"])
    grant = operator().approve_recovery(journal, reason="Synthetic repair", now=NOW)
    altered = deepcopy(journal); altered["phase"] = "changed"
    with pytest.raises(ReviewBlocked, match="SNAPSHOT_CHANGED"):
        live_import.persist_in_session(session, ImmutableImportStore(session, root), proof=None, operator=operator(),
            grant=grant, match_id="900001", clock=lambda: NOW, recovery=altered)
    source = session.get(SystemSetting, "review-source:" + candidate()["source_key"])
    source.value = {**source.value, "conflict": True}
    session.commit()
    with pytest.raises(ReviewBlocked, match="CURRENT_DURABLE_APPROVAL"):
        live_import.persist_in_session(session, ImmutableImportStore(session, root), proof=None, operator=operator(),
            grant=grant, match_id="900001", clock=lambda: NOW, recovery=journal)


def test_corrupt_existing_object_is_never_overwritten_by_recovery(session):
    root = InMemoryObjectStore(); persist(session, root)
    key = next(iter(root._objects))
    body, content_type, metadata = root._objects[key]
    root._objects[key] = (b"corrupt", content_type, metadata)
    journal = live_import.journal_view(session, candidate()["source_key"])
    grant = operator().approve_recovery(journal, reason="Investigate corrupt immutable object", now=NOW)
    with pytest.raises(Exception):
        live_import.persist_in_session(session, ImmutableImportStore(session, root), proof=None, operator=operator(),
            grant=grant, match_id="900001", clock=lambda: NOW, recovery=journal)
    session.rollback()
    assert root._objects[key][0] == b"corrupt" and session.query(ActiveMatch).count() == 1


def test_new_unreviewed_source_version_blocks_recovery(session):
    from app.storage.import_review import ReviewRepository
    root = InMemoryObjectStore(); persist(session, root)
    journal = live_import.journal_view(session, candidate()["source_key"])
    grant = operator().approve_recovery(journal, reason="Synthetic repair", now=NOW)
    report = synthetic_report()
    report["leagues"]["de.bl1"]["observations"][0]["raw_response_sha256"] = "f" * 64
    ReviewRepository(session).record(candidate(report), now=NOW)
    session.commit()
    with pytest.raises(ReviewBlocked, match="CURRENT_DURABLE_APPROVAL"):
        live_import.persist_in_session(session, ImmutableImportStore(session, root), proof=None, operator=operator(),
            grant=grant, match_id="900001", clock=lambda: NOW, recovery=journal)


def test_existing_bayern_metadata_baseline_preserved(session):
    from app.providers.identity import REVIEWED_IMPORT_KEY
    session.add(SystemSetting(key=REVIEWED_IMPORT_KEY, value={"synthetic": "protected-old-journal"}))
    session.commit()
    original = import_preflight.protected_snapshot(session)
    persist(session, InMemoryObjectStore())
    assert import_preflight.protected_snapshot(session) == original


def test_preflight_missing_configuration_never_connects(monkeypatch):
    cfg = SimpleNamespace(database_url_missing=True, database_url_valid=False,
        r2_missing_env=["R2_ENDPOINT"], r2_invalid_env=[], auto_retention_delete_enabled=False,
        operational_delete_enabled=False)
    report = import_preflight.run(cfg)
    assert report["postgres"] == report["r2"] == "NOT_RUN"
    assert report["missing_env"] == ["DATABASE_URL", "R2_ENDPOINT"]
    assert report["SQL_WRITE"] == report["R2_WRITE"] == report["LIVE_DELETE"] == "NOT_RUN"
    cfg.auto_retention_delete_enabled = True
    assert import_preflight.run(cfg)["error_code"] == "DELETE_FLAGS_MUST_BE_DISABLED"


def test_production_driver_requires_live_proof_before_preflight(session, monkeypatch):
    grant = approved(session)
    monkeypatch.setattr(import_preflight, "run", lambda *a, **kw: pytest.fail("preflight reached"))
    with pytest.raises(ReviewBlocked, match="TRUSTED_LIVE_PROOF"):
        live_import.controlled_persist(None, proof=proof(), operator=operator(), grant=grant,
                                       match_id="900001", clock=lambda: NOW)


def test_cli_default_and_unconfigured_persist_do_not_fetch_or_write(monkeypatch, capsys):
    monkeypatch.setattr(live_import, "revalidate", lambda *a, **kw: pytest.fail("HTTP reached"))
    assert live_import.main([]) == 1
    assert "EXPLICIT_SINGLE_UTC_DAY_REQUIRED" in capsys.readouterr().out
    from app.core.config import get_settings
    cfg = get_settings().model_copy(update={"controlled_import_operator_id": None,
                                           "controlled_import_operator_token_sha256": None})
    monkeypatch.setattr("app.core.config.get_settings", lambda: cfg)
    assert live_import.main(["--persist", "--confirm-match-id", "900001"]) == 1
    assert "IMPORT_OPERATOR_NOT_CONFIGURED" in capsys.readouterr().out


def test_manual_only_preflight_workflow_has_no_import_or_schedule():
    from pathlib import Path
    text = (Path(__file__).parents[2] / ".github/workflows/import-preflight.yml").read_text()
    assert "workflow_dispatch:" in text and "schedule:" not in text
    assert "--persist" not in text and "--approve" not in text
    assert "--preflight" in text and "OPERATIONAL_DELETE_ENABLED: 'false'" in text
    assert "AUTO_RETENTION_DELETE_ENABLED: 'false'" in text


class PublicFixtureHTTP:
    """Synthetic bytes only. Exercises the new parser path, not live provenance."""
    def __init__(self, mode="valid"):
        self.requests, self.mode = [], mode

    def get(self, key, url):
        if key in POLICIES["documents"]:
            body = key.encode()
        elif key == "group:bl1":
            body = json.dumps({"groupOrderID": 10}).encode()
        elif key == "matches:bl1:10":
            raw = {"matchID": 900001, "leagueShortcut": "bl1", "leagueId": 4937, "leagueSeason": 2026,
                "group": {"groupOrderID": 10}, "matchDateTimeUTC": "2026-11-21T12:00:00Z",
                "team1": {"teamId": 90011, "teamName": "Synthetic Home"},
                "team2": {"teamId": 90012, "teamName": "Synthetic Away"},
                "matchIsFinished": False, "matchResults": []}
            if self.mode == "empty": data = []
            elif self.mode == "duplicate": data = [raw, {**raw, "team1": {"teamId": 90013, "teamName": "Conflict"}}]
            elif self.mode == "finished": data = [{**raw, "matchIsFinished": True}]
            else: data = [raw]
            body = json.dumps(data).encode()
        else:
            raw = {"date": "2026-11-21", "utcDate": "2026-11-21T12:00:00Z", "time": "13:00",
                   "team1": "Synthetic Home", "team2": "Synthetic Away"}
            if self.mode == "utc": raw.pop("utcDate")
            if self.mode == "names": raw["team1"] = "Synthetic Home FC"
            body = json.dumps({"name": "Deutsche Bundesliga 2026/27", "matches": [raw]}).encode()
        if self.mode == "denied": raise EvidenceFailure("ACCESS_STOP_OR_REDIRECT")
        meta = {"sha256": hashlib.sha256(body).hexdigest(), "received_at": NOW.isoformat(),
                "cached": self.mode == "cached"}
        self.requests.append({"resource": key, "sha256": meta["sha256"], "observed_at": NOW.isoformat(), "http_status": 200})
        return body, meta


@pytest.mark.parametrize("mode,eligible", [("valid", True), ("utc", False), ("names", False),
    ("empty", False), ("duplicate", False), ("cached", False), ("denied", False), ("finished", False), ("licence", False)])
def test_trusted_revalidation_single_day_single_candidate_synthetic(monkeypatch, mode, eligible):
    def policy(body, _format):
        return "f" * 64 if mode == "licence" else POLICIES["documents"][body.decode()]["sha256"]
    monkeypatch.setattr(tf, "policy_digest", policy)
    http = PublicFixtureHTTP(mode)
    evidence = tf.revalidate(NOW.date(), http=http, clock=lambda: NOW)
    assert not evidence.live
    if eligible or mode in {"utc", "names"}:
        item = evidence.candidate(NOW)
        assert item["technical_eligibility"] is eligible
        if not eligible: assert "INDEPENDENT_FIXTURE_UTC" in item["blockers"]
    else:
        assert evidence.report["status"] == "BLOCKED"
        with pytest.raises(EvidenceFailure): evidence.candidate(NOW)
    assert sum(r["resource"].startswith("matches:") for r in http.requests) <= 1
    assert evidence.report["SQL_WRITE"] == evidence.report["R2_WRITE"] == "NOT_RUN"


def test_new_transport_clears_http_authority_not_origin_cooldown(monkeypatch):
    state = {"http": {"cached": "untrusted"}, "blocks": {"example": "blocked"}, "last_api_request": "prior"}
    def make_http(actual, **kwargs):
        assert actual["http"] == {} and actual["blocks"] == state["blocks"]
        assert actual["last_api_request"] == "prior"
        result = PublicFixtureHTTP()
        result.close = lambda: None
        return result
    monkeypatch.setattr(tf, "CollectorHTTP", make_http)
    monkeypatch.setattr(tf, "policy_digest", lambda body, fmt: POLICIES["documents"][body.decode()]["sha256"])
    evidence = tf.revalidate(NOW.date(), state=state, clock=lambda: NOW)
    assert evidence.live and evidence.candidate(NOW)["technical_eligibility"]
    assert state["http"] == {} and state["blocks"] == {"example": "blocked"}


@pytest.mark.parametrize("day,match_id", [(NOW.date() - timedelta(days=1), None),
    (NOW.date() + timedelta(days=2), None), (NOW.date(), "83237"), (NOW.date(), "not-an-id")])
def test_scope_rejected_before_any_http(day, match_id):
    with pytest.raises(EvidenceFailure, match="SINGLE_FUTURE_BUNDESLIGA"):
        tf.revalidate(day, match_id=match_id, clock=lambda: NOW)


def test_preflight_real_path_is_read_only_and_never_commits(session, monkeypatch):
    from contextlib import contextmanager
    from app.api import connections
    from app.jobs import retention
    from app.core.config import Settings
    cfg = Settings(_env_file=None, database_url="postgresql://synthetic:synthetic@localhost/synthetic",
        r2_endpoint="https://synthetic.r2.cloudflarestorage.com", r2_access_key_id="synthetic",
        r2_secret_access_key="synthetic", r2_bucket="synthetic")
    @contextmanager
    def storage(settings, *, readonly):
        assert readonly is True
        class ReadOnly:
            def __getattr__(self, name): return getattr(session, name)
            def execute(self, statement):
                sql = str(statement)
                if sql == "SHOW transaction_read_only": return SimpleNamespace(scalar_one=lambda: "on")
                if sql == "SELECT pg_database_size(current_database())": return SimpleNamespace(scalar_one=lambda: 1234)
                pytest.fail("unapproved SQL")
        yield ReadOnly()
    monkeypatch.setattr(retention, "storage_session", storage)
    monkeypatch.setattr(session, "commit", lambda: pytest.fail("preflight committed"))
    monkeypatch.setattr(connections, "schema_report", lambda c: {"schema": "READY", "schema_issues": [], "expected_revisions": []})
    monkeypatch.setattr(connections, "r2_check", lambda cfg: {"status": "ACCESSIBLE"})
    monkeypatch.setattr(import_preflight, "required_constraints", lambda c: {"status": "PASS"})
    monkeypatch.setattr(import_preflight, "bounded_store", lambda c: SimpleNamespace(client=SimpleNamespace(close=lambda: None)))
    monkeypatch.setattr(retention, "target_check", lambda s, r: {"status": "VERIFIED"})
    result = import_preflight.run(cfg, candidate=candidate())
    assert result["status"] == "PASS" and result["postgres"] == "CONNECTED_READ_ONLY"
    assert result["write_privileges"] == "NOT_EXERCISED" and result["migrations"] == "NOT_RUN"
    assert result["candidate_scope"] == import_preflight.candidate_scope(session, candidate())


def test_preflight_error_never_leaks_credentials(monkeypatch):
    from contextlib import contextmanager
    from app.core.config import Settings
    from app.jobs import retention
    @contextmanager
    def failed(*a, **kw):
        raise RuntimeError("postgresql://SECRET:SECRET@private.invalid/db")
        yield
    monkeypatch.setattr(retention, "storage_session", failed)
    cfg = Settings(_env_file=None, database_url="postgresql://synthetic:synthetic@localhost/synthetic",
        r2_endpoint="https://synthetic.r2.cloudflarestorage.com", r2_access_key_id="synthetic",
        r2_secret_access_key="synthetic", r2_bucket="synthetic")
    result = import_preflight.run(cfg)
    assert result["error_code"] == "SQL_CONNECTION_FAILED" and "SECRET" not in json.dumps(result)
