"""Explicit transaction-scoped ledger operations; no scheduler or HTTP writer.

The caller must authorize the publication/verification event. A database record
alone does not prove the prediction was delivered to an external audience.
"""
from decimal import Decimal
import hashlib
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import load_only

from app.jobs.prediction_ledger_rules import outcome_1x2, utc
from app.models import ActiveMatch, Prediction
from app.models.prediction_ledger import PredictionPublication, PredictionSettlement


def publish(session, *, prediction_id, idempotency_key: str, publication_version: int = 1, supersedes_id=None):
    """Append at an explicit publication boundary using the DB's clock.

    No odds are accepted until a separately verified licensing/provenance
    contract exists. The surrounding transaction belongs to the caller.
    """
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 128:
        raise ValueError("INVALID_IDEMPOTENCY_KEY")
    # Serialize a retry before evaluating either unique index. Otherwise two
    # inserts can race on the version index instead of the idempotency index.
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": "publication:" + idempotency_key})
    existing = session.scalar(select(PredictionPublication).where(PredictionPublication.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.prediction_id != prediction_id or existing.publication_version != publication_version or existing.supersedes_id != supersedes_id:
            raise ValueError("IDEMPOTENCY_CONFLICT")
        return existing
    prediction = session.get(Prediction, prediction_id, options=[load_only(Prediction.id, Prediction.match_id,
        Prediction.market, Prediction.selection, Prediction.probability, Prediction.model_version,
        Prediction.feature_version, Prediction.feature_snapshot, Prediction.analysis_timestamp)])
    if prediction is None:
        raise ValueError("PREDICTION_NOT_FOUND")
    match = session.get(ActiveMatch, prediction.match_id, options=[load_only(ActiveMatch.id, ActiveMatch.provider,
        ActiveMatch.provider_match_id, ActiveMatch.league_id, ActiveMatch.season, ActiveMatch.home_team,
        ActiveMatch.away_team, ActiveMatch.start_time)])
    if match is None or not prediction.feature_snapshot or not prediction.feature_version:
        raise ValueError("MISSING_PUBLICATION_EVIDENCE")
    if type(publication_version) is not int or publication_version < 1 or (publication_version == 1) != (supersedes_id is None):
        raise ValueError("INVALID_PUBLICATION_VERSION")
    if not isinstance(prediction.probability, Decimal) or not prediction.probability.is_finite() or not Decimal(0) <= prediction.probability <= Decimal(1):
        raise ValueError("INVALID_PROBABILITY")
    if any(not isinstance(v, str) or not v.strip() for v in (match.provider, match.provider_match_id, match.league_id, match.season, match.home_team, match.away_team, prediction.market, prediction.selection, prediction.model_version)):
        raise ValueError("MISSING_PUBLICATION_IDENTITY")
    values = dict(id=uuid4(), idempotency_key=idempotency_key, prediction_id=prediction.id, match_id=match.id,
                  provider=match.provider, provider_match_id=match.provider_match_id,
                  league_id=match.league_id, season=match.season, home_team=match.home_team,
                  away_team=match.away_team, kickoff_at=utc(match.start_time),
                  market=prediction.market, selection=prediction.selection,
                  probability=prediction.probability, model_version=prediction.model_version,
                  feature_version=prediction.feature_version,
                  feature_sha256=hashlib.sha256(prediction.feature_snapshot.encode("utf-8")).hexdigest(),
                  publication_version=publication_version, supersedes_id=supersedes_id)
    # Concurrent same-key retries wait on the unique index, then compare payload.
    stmt = insert(PredictionPublication).values(**values).on_conflict_do_nothing(index_elements=["idempotency_key"]).returning(PredictionPublication.id)
    inserted = session.execute(stmt).scalar_one_or_none()
    if inserted:
        return session.get(PredictionPublication, inserted)
    existing = session.scalar(select(PredictionPublication).where(PredictionPublication.idempotency_key == idempotency_key))
    if existing is None or any(getattr(existing, key) != value for key, value in values.items() if key not in {"id"}):
        raise ValueError("IDEMPOTENCY_CONFLICT")
    return existing


def settle_final(session, *, publication_id, result_id, idempotency_key: str):
    """Legacy temporary MatchResult cannot prove regulation time or independence."""
    raise ValueError("VERIFIED_REGULATION_EVIDENCE_REQUIRED")


def settle_evidence(session, *, publication_id, evidence_id, idempotency_key: str):
    from app.models.prediction_ledger import PredictionEvidence
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 128:
        raise ValueError("INVALID_IDEMPOTENCY_KEY")
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": str(publication_id)})
    existing = session.scalar(select(PredictionSettlement).where(PredictionSettlement.idempotency_key == idempotency_key))
    if existing:
        if existing.publication_id != publication_id or existing.evidence_id != evidence_id:
            raise ValueError("IDEMPOTENCY_CONFLICT")
        return existing
    publication = session.get(PredictionPublication, publication_id)
    evidence = session.get(PredictionEvidence, evidence_id)
    if not publication or not evidence or evidence.publication_id != publication_id or evidence.kind != "RESULT":
        raise ValueError("VERIFIED_REGULATION_EVIDENCE_REQUIRED")
    if session.scalar(select(PredictionEvidence.id).where(PredictionEvidence.supersedes_id == evidence_id)):
        raise ValueError("SUPERSEDED_RESULT_EVIDENCE")
    if session.scalar(select(PredictionSettlement.id).where(PredictionSettlement.evidence_id == evidence_id)):
        raise ValueError("DUPLICATE_RESULT_USE_ORIGINAL_IDEMPOTENCY_KEY")
    data = evidence.payload
    outcome = "VOID" if data["status"] == "CANCELLED" else outcome_1x2(
        market=publication.market, selection=publication.selection, result_status=data["status"],
        home=data.get("home_score"), away=data.get("away_score"), verified_at=evidence.received_at,
        result_source=evidence.authority, kickoff_at=publication.kickoff_at,
        observed_now=session.scalar(text("SELECT clock_timestamp()")))
    latest = session.scalar(select(PredictionSettlement).where(PredictionSettlement.publication_id == publication_id).order_by(PredictionSettlement.version.desc()).limit(1))
    event = PredictionSettlement(id=uuid4(), publication_id=publication_id, idempotency_key=idempotency_key,
        version=1 if latest is None else latest.version + 1, supersedes_id=None if latest is None else latest.id,
        evidence_id=evidence.id, result_id=evidence.id, result_source=evidence.authority,
        verified_at=evidence.received_at, home_score=data.get("home_score"), away_score=data.get("away_score"), outcome=outcome)
    session.add(event)
    session.flush()
    return event
