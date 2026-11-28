"""Explicit transaction-scoped ledger operations; no scheduler or HTTP writer.

The caller must authorize the publication/verification event. A database record
alone does not prove the prediction was delivered to an external audience.
"""
from datetime import timezone
from decimal import Decimal
import hashlib
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import load_only

from app.jobs.prediction_ledger_rules import outcome_1x2, utc
from app.models import ActiveMatch, MatchResult, Prediction
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
    """Append only from a matching verified FINAL result; never modify old events."""
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 128:
        raise ValueError("INVALID_IDEMPOTENCY_KEY")
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": str(publication_id)})
    publication = session.get(PredictionPublication, publication_id)
    result = session.get(MatchResult, result_id, options=[load_only(MatchResult.id, MatchResult.match_id,
        MatchResult.provider, MatchResult.status, MatchResult.home_score, MatchResult.away_score,
        MatchResult.verified_at, MatchResult.result_source)])
    existing = session.scalar(select(PredictionSettlement).where(PredictionSettlement.idempotency_key == idempotency_key))
    if existing is not None and result is None:
        if existing.publication_id == publication_id and existing.result_id == result_id:
            return existing
        raise ValueError("IDEMPOTENCY_CONFLICT")
    if publication is None or result is None or result.match_id != publication.match_id or result.provider != publication.provider:
        raise ValueError("RESULT_IDENTITY_MISMATCH")
    now = session.execute(text("SELECT clock_timestamp()" )).scalar_one().astimezone(timezone.utc)
    if (result.verified_at is None or not result.result_source or not result.result_source.strip() or
            utc(result.verified_at) < utc(publication.published_at) or utc(result.verified_at) > now):
        raise ValueError("RESULT_NOT_ELIGIBLE")
    if result.status == "CANCELLED":
        outcome = "VOID"
    elif (result.status == "FINAL" and utc(result.verified_at) >= utc(publication.kickoff_at) and
          all(type(score) is int and score >= 0 for score in (result.home_score, result.away_score))):
        outcome = outcome_1x2(market=publication.market, selection=publication.selection,
            result_status=result.status, home=result.home_score, away=result.away_score,
            verified_at=result.verified_at, result_source=result.result_source,
            kickoff_at=publication.kickoff_at, observed_now=now)
    else:
        raise ValueError("RESULT_NOT_ELIGIBLE")
    snapshot = (publication_id, result.id, result.result_source, utc(result.verified_at), result.home_score, result.away_score, outcome)
    if existing is not None:
        actual = (existing.publication_id, existing.result_id, existing.result_source, utc(existing.verified_at), existing.home_score, existing.away_score, existing.outcome)
        if actual != snapshot:
            raise ValueError("IDEMPOTENCY_CONFLICT")
        return existing
    latest = session.scalar(select(PredictionSettlement).where(PredictionSettlement.publication_id == publication_id).order_by(PredictionSettlement.version.desc()).limit(1))
    if latest is not None and (latest.publication_id, latest.result_id, latest.result_source, utc(latest.verified_at), latest.home_score, latest.away_score, latest.outcome) == snapshot:
        raise ValueError("DUPLICATE_RESULT_USE_ORIGINAL_IDEMPOTENCY_KEY")
    event = PredictionSettlement(id=uuid4(), publication_id=publication_id, idempotency_key=idempotency_key,
        version=1 if latest is None else latest.version + 1, supersedes_id=None if latest is None else latest.id,
        result_id=result.id, result_source=result.result_source, verified_at=utc(result.verified_at),
        home_score=result.home_score, away_score=result.away_score, outcome=outcome)
    session.add(event)
    session.flush()
    return event
