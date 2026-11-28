"""Offline signed-evidence boundary. No transport, environment loading or signing keys.

Only a separately operated verifier may use the verifier DB role. Production
trust configuration/transport is deliberately absent; defaults fail closed.
"""
import base64
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import select, text

from app.jobs.prediction_ledger_rules import utc
from app.models.prediction_ledger import PredictionPublication, PredictionEvidence


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def publication_digest(publication) -> str:
    """Bind receipt to the entire user-visible immutable prediction, not its ID alone."""
    fields = ("id", "match_id", "provider", "provider_match_id", "league_id", "season",
              "home_team", "away_team", "market", "selection", "probability", "model_version",
              "feature_version", "feature_sha256", "publication_version")
    payload = {name: str(getattr(publication, name)) for name in fields}
    payload.update({name: utc(getattr(publication, name)).isoformat() for name in ("published_at", "kickoff_at")})
    return hashlib.sha256(canonical(payload)).hexdigest()


@dataclass(frozen=True)
class TrustPolicy:
    authority: str
    key_id: str
    public_key: bytes
    kind: str
    # Approved source -> (independent organization, licence/attribution decision ref).
    sources: tuple[tuple[str, str, str], ...] = ()
    audience: str = ""


def verify_envelope(payload, signature, policy):
    if not isinstance(policy, TrustPolicy) or payload.get("authority") != policy.authority or payload.get("key_id") != policy.key_id or payload.get("kind") != policy.kind:
        raise ValueError("UNTRUSTED_EVIDENCE_AUTHORITY")
    raw = canonical(payload)
    if len(raw) > 32768:
        raise ValueError("EVIDENCE_TOO_LARGE")
    try:
        Ed25519PublicKey.from_public_bytes(policy.public_key).verify(base64.b64decode(signature, validate=True), raw)
    except (ValueError, InvalidSignature):
        raise ValueError("INVALID_EVIDENCE_SIGNATURE") from None
    return hashlib.sha256(raw).hexdigest()


def require_runtime_role(session, role):
    if role not in {"ma_ledger_writer", "ma_ledger_verifier", "ma_ledger_reader"}:
        raise ValueError("UNKNOWN_LEDGER_ROLE")
    safe = session.scalar(text("""SELECT NOT r.rolsuper AND NOT r.rolcreaterole AND NOT r.rolcreatedb
        AND NOT r.rolbypassrls AND pg_has_role(current_user,:role,'USAGE')
        AND NOT has_schema_privilege(current_user,'public','CREATE')
        AND NOT EXISTS(SELECT 1 FROM pg_class c WHERE c.relname IN
          ('prediction_publications','prediction_settlements','prediction_evidence') AND c.relowner=r.oid)
        FROM pg_roles r WHERE r.rolname=current_user"""), {"role":role})
    if not safe:
        raise ValueError("UNSAFE_LEDGER_RUNTIME_ROLE")
    other = {"ma_ledger_writer":"ma_ledger_verifier", "ma_ledger_verifier":"ma_ledger_writer"}.get(role)
    if other and session.scalar(text("SELECT pg_has_role(current_user,:other,'MEMBER')"), {"other":other}):
        raise ValueError("LEDGER_ROLE_SEPARATION_REQUIRED")


def prepare_publication_for_delivery(session, *, prediction_id, idempotency_key, enabled=False):
    """Disabled publication boundary; caller must commit before dispatching.

    The committed row is an intent, never delivery proof. A separately operated
    public transport and signed external observation are still required. No
    transport is selected or invoked here; use idempotency_key on its outbox.
    """
    if not enabled:
        raise ValueError("PUBLICATION_INTEGRATION_DISABLED")
    require_runtime_role(session, "ma_ledger_writer")
    from app.jobs.prediction_ledger import publish
    publication = publish(session, prediction_id=prediction_id, idempotency_key=idempotency_key)
    return {"publication_id":str(publication.id), "publication_sha256":publication_digest(publication),
            "delivery_status":"PUBLICATION_DELIVERY_UNVERIFIED"}


def accept_evidence(session, *, publication_id, payload, signature, policy=None, enabled=False):
    """Verifier-only transaction; fixture policy is injected in isolated tests.

    This verifies an attestation by a trusted independent authority, not reality
    by cryptography alone. No caller boolean can bypass signature validation.
    """
    if not enabled:
        raise ValueError("EVIDENCE_INTEGRATION_DISABLED")
    require_runtime_role(session, "ma_ledger_verifier")
    digest = verify_envelope(payload, signature, policy)
    publication = session.get(PredictionPublication, publication_id)
    if publication is None:
        raise ValueError("PUBLICATION_NOT_FOUND")
    if payload.get("schema") != "ma-ledger-evidence/v1" or not isinstance(payload.get("event_id"), str) or not 0 < len(payload["event_id"]) <= 128:
        raise ValueError("INVALID_EVIDENCE_SCHEMA")
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                    {"key": "evidence:" + policy.authority + ":" + payload["event_id"]})
    prior = session.scalar(select(PredictionEvidence).where(PredictionEvidence.authority == policy.authority,
                                                          PredictionEvidence.event_id == payload["event_id"]))
    if prior:
        if prior.payload_sha256 != digest or prior.publication_id != publication_id or prior.signature != signature:
            raise ValueError("EVIDENCE_REPLAY_CONFLICT")
        return prior
    now = utc(session.scalar(text("SELECT clock_timestamp()")))
    observed = utc(datetime.fromisoformat(payload["observed_at"]))
    if observed > now:
        raise ValueError("FUTURE_EVIDENCE")
    identity = {key: str(getattr(publication, key)) for key in
                ("match_id", "provider", "provider_match_id", "league_id", "season", "home_team", "away_team")}
    identity["kickoff_at"] = utc(publication.kickoff_at).isoformat()
    if payload.get("match") != identity:
        raise ValueError("EVIDENCE_MATCH_MISMATCH")
    if policy.kind == "DELIVERY":
        if (payload.get("publication_id") != str(publication.id) or
                payload.get("publication_sha256") != publication_digest(publication) or
                payload.get("audience") != policy.audience or not policy.audience or
                payload.get("access") != "PUBLICLY_RETRIEVED" or
                not utc(publication.published_at) <= observed <= now < utc(publication.kickoff_at)):
            raise ValueError("PUBLICATION_DELIVERY_UNVERIFIED")
        if payload.get("supersedes_id") is not None:
            raise ValueError("INVALID_DELIVERY_REVISION")
    elif policy.kind == "RESULT":
        if observed < utc(publication.kickoff_at):
            raise ValueError("RESULT_BEFORE_KICKOFF")
        status = payload.get("status")
        if status not in {"FINAL", "CANCELLED", "RETRACTED"} or payload.get("period") != "REGULATION_90_PLUS_STOPPAGE":
            raise ValueError("RESULT_PERIOD_UNVERIFIED")
        scores = (payload.get("home_score"), payload.get("away_score"))
        if status == "FINAL" and any(type(v) is not int or v < 0 for v in scores):
            raise ValueError("INVALID_REGULATION_SCORE")
        if status != "FINAL" and scores != (None, None):
            raise ValueError("NONFINAL_SCORE_PRESENT")
        sources = payload.get("sources", [])
        allowed = {name: (org, permission) for name, org, permission in policy.sources}
        organizations = set()
        if not isinstance(sources, list) or len(sources) < 2:
            raise ValueError("INDEPENDENT_RESULT_SOURCES_REQUIRED")
        for source in sources:
            approved = allowed.get(source.get("name"))
            if not approved or source.get("permission_ref") != approved[1]:
                raise ValueError("SOURCE_PERMISSION_UNVERIFIED")
            if (source.get("period") != payload["period"] or source.get("status") != status or
                    (source.get("home_score"), source.get("away_score")) != scores or
                    not source.get("source_match_id") or not source.get("artifact_ref")):
                raise ValueError("INDEPENDENT_RESULT_DISAGREEMENT")
            checksum = source.get("sha256", "")
            if len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
                raise ValueError("INVALID_SOURCE_DIGEST")
            organizations.add(approved[0])
        if len(organizations) < 2:
            raise ValueError("INDEPENDENT_RESULT_SOURCES_REQUIRED")
    else:
        raise ValueError("UNKNOWN_EVIDENCE_KIND")
    evidence = PredictionEvidence(id=uuid4(), publication_id=publication_id, kind=policy.kind,
        authority=policy.authority, event_id=payload["event_id"], key_id=policy.key_id,
        public_key_sha256=hashlib.sha256(policy.public_key).hexdigest(),
        payload=payload, payload_sha256=digest, signature=signature, observed_at=observed,
        supersedes_id=UUID(payload["supersedes_id"]) if payload.get("supersedes_id") else None)
    session.add(evidence)
    session.flush()
    return evidence
