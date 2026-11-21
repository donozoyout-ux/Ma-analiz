"""Explicitly licensed technical observations; no implicit source or permission fallback."""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlsplit

from sqlalchemy import select

from app.models import ActiveMatch, ActiveStatistic, MatchResult
from app.storage.lifecycle import policy_for, technical_valid, aware
from app.providers.result_sources import utc


def collect_permitted_statistics(session, caller, *, day):
    from app.core.config import get_settings
    from app.providers.base import ProviderFailure
    if not callable(getattr(caller.provider, "get_statistics", None)):
        return {"status": "NOT_SUPPORTED", "written": 0, "rejected": 0}
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    matches = session.scalars(select(ActiveMatch).join(MatchResult, MatchResult.match_id == ActiveMatch.id).where(
        ActiveMatch.provider == caller.provider.name, ActiveMatch.start_time >= start,
        ActiveMatch.start_time < start + timedelta(days=1), MatchResult.status == "FINAL",
        MatchResult.verified_at.is_not(None)).order_by(ActiveMatch.id).limit(get_settings().lifecycle_batch_size)).all()
    written = rejected = blocked = 0
    for match in matches:
        if not policy_for(session, match, "statistics"):
            blocked += 1
            continue  # permission check precedes all source requests
        try:
            rows = caller.call("get_statistics", match.provider_match_id)
            observed = datetime.now(timezone.utc)
            rows = [{**row, "observed_at": row.get("observed_at") or observed.isoformat()} for row in rows]
            counts = store_permitted_statistics(session, match, rows, now=observed)
            written += counts["written"]; rejected += counts["rejected"]
        except ProviderFailure:
            blocked += 1
    session.commit()
    return {"status": "PARTIAL" if rejected or blocked else "SUCCESS" if written else "SKIPPED",
            "written": written, "rejected": rejected, "blocked_matches": blocked}


def store_permitted_statistics(session, match, observations, *, now):
    policy = policy_for(session, match, "statistics")
    if not policy:
        return {"written": 0, "rejected": len(observations), "reason": "REUSE_PERMISSION_NOT_CONFIRMED"}
    result = session.scalar(select(MatchResult).where(MatchResult.match_id == match.id))
    written = rejected = 0
    for row in observations:
        try:
            if (row.get("provider", row.get("source")) != match.provider
                    or str(row.get("provider_match_id")) != match.provider_match_id
                    or row.get("name") not in {"Shots", "Shots On Goal", "Corner Kicks", "Possession", "Fouls", "Yellow Cards", "Red Cards", "Offsides"}):
                raise ValueError("IDENTITY_OR_METRIC_NOT_REVIEWED")
            stamp = utc(row.get("observed_at") or row.get("retrieved_at"))
            if stamp is None: raise ValueError("OBSERVATION_TIME_REQUIRED")
            url = urlsplit(row.get("source_url", ""))
            if url.scheme != "https" or not url.hostname or url.username or url.password or url.query:
                raise ValueError("SAFE_SOURCE_URL_REQUIRED")
            values = []
            for field in ("home_value", "away_value"):
                raw = row.get(field)
                if isinstance(raw, bool) or not isinstance(raw, (int, float, Decimal)):
                    raise ValueError("REAL_NUMERIC_VALUE_REQUIRED")
                value = Decimal(str(raw))
                if not value.is_finite() or value != value.quantize(Decimal("0.0001")) or abs(value) >= Decimal("10000000000"):
                    raise ValueError("LOSSLESS_NUMERIC_RANGE_REQUIRED")
                values.append(value)
            source_id = str(row.get("id") or row.get("statistic_id") or "")
            if not source_id or len(source_id) > 160: raise ValueError("SOURCE_STATISTIC_ID_REQUIRED")
            entity = ActiveStatistic(match_id=match.id, provider=match.provider, provider_match_id=match.provider_match_id,
                provider_statistic_id=source_id, name=row["name"], unit=row.get("unit"), period=row.get("period"),
                home_value=values[0], away_value=values[1], observed_at=stamp, verified_at=now,
                source_url=row["source_url"], licence=policy["licence"], permission_reference=policy["reference"],
                expires_at=stamp + timedelta(hours=48))
            if not technical_valid(entity, result, now, match): raise ValueError("FULL_TIME_VALIDATION_REQUIRED")
            existing = session.scalar(select(ActiveStatistic).where(ActiveStatistic.match_id == match.id,
                ActiveStatistic.provider == match.provider, ActiveStatistic.provider_statistic_id == source_id,
                ActiveStatistic.observed_at == stamp))
            if existing:
                # An observed snapshot may not be silently edited on a retry.
                if any(getattr(existing, key) != getattr(entity, key) for key in
                       ("name", "unit", "period", "home_value", "away_value", "source_url", "licence", "permission_reference")):
                    raise ValueError("CONFLICTING_OBSERVATION")
                continue
            session.add(entity)
            session.flush()
            written += 1
        except (ValueError, TypeError, KeyError, ArithmeticError):
            rejected += 1
    return {"written": written, "rejected": rejected}
