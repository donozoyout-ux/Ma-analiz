from datetime import datetime
from decimal import Decimal, InvalidOperation


def utc_datetime(value) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Provider timestamps must include a timezone")
    return parsed


def _team_name(value) -> str:
    name = value.get("name") if isinstance(value, dict) else value
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Provider match is missing a valid team name")
    return name.strip()


def normalize_match(payload: dict, *, provider: str) -> dict:
    match_id = payload.get("provider_match_id", payload.get("id"))
    if match_id is None or not str(match_id).strip():
        raise ValueError("Provider match is missing an ID")
    start = payload.get("start_time", payload.get("utcDate"))
    if start is None:
        raise ValueError("Provider match is missing start time")
    raw_league = payload.get("league")
    league = raw_league if isinstance(raw_league, dict) else {}
    value = {
        "provider": provider,
        "provider_match_id": str(match_id),
        "league_id": str(payload.get("league_id", league.get("id", ""))) or None,
        "league_name": payload.get("league_name", league.get("name")),
        "season": str(payload.get("season", "")) or None,
        "home_team": _team_name(payload.get("home_team", payload.get("homeTeam"))),
        "away_team": _team_name(payload.get("away_team", payload.get("awayTeam"))),
        "start_time": utc_datetime(start),
        "status": str(payload.get("status", "SCHEDULED")).upper(),
    }
    for field in ("venue", "round", "country", "sport"):
        item = payload.get(field)
        if item is not None:
            value[field] = str(item)
    return value


def normalize_matches(payloads: list[dict], *, provider: str) -> list[dict]:
    """Normalize and deduplicate provider responses by provider + provider ID."""
    unique: dict[tuple[str, str], dict] = {}
    for payload in payloads:
        match = normalize_match(payload, provider=provider)
        unique[(match["provider"], match["provider_match_id"])] = match
    return list(unique.values())


def normalize_odds(payload: dict, *, match_id: str, provider: str | None = None) -> dict:
    try:
        price = Decimal(str(payload["odds"]))
    except (KeyError, InvalidOperation) as exc:
        raise ValueError("Odds must be a valid decimal number") from exc
    if not price.is_finite() or price <= 1:
        raise ValueError("Decimal odds must be greater than one")
    captured = payload.get("captured_at") or payload.get("timestamp")
    if captured is None:
        raise ValueError("Odds snapshot is missing its capture timestamp")
    required = ("bookmaker", "market", "selection")
    if any(not isinstance(payload.get(field), str) or not payload[field].strip() for field in required):
        raise ValueError("Odds snapshot is missing bookmaker, market, or selection")
    return {
        "match_id": match_id,
        "provider": str(payload.get("provider", provider or "unknown")),
        "provider_odds_id": str(payload["provider_odds_id"]) if payload.get("provider_odds_id") is not None else None,
        "provider_market_id": str(payload["provider_market_id"]) if payload.get("provider_market_id") is not None else None,
        "provider_bookmaker_id": str(payload["provider_bookmaker_id"]) if payload.get("provider_bookmaker_id") is not None else None,
        "bookmaker": str(payload["bookmaker"]).strip(),
        "market": str(payload["market"]).strip().upper(),
        "selection": str(payload["selection"]).strip().upper(),
        "odds": price,
        "captured_at": utc_datetime(captured),
    }


_FINAL_RESULT_STATES = {"FINISHED", "FINAL", "FT", "FULL_TIME", "AET", "PEN"}


def normalize_result(payload: dict, *, provider: str) -> dict:
    match_id = payload.get("provider_match_id", payload.get("match_id", payload.get("id")))
    if match_id is None or not str(match_id).strip():
        raise ValueError("Provider result is missing match ID")
    raw_status = str(payload.get("status", "")).strip().upper()
    status = "FINAL" if raw_status in _FINAL_RESULT_STATES else "IN_PROGRESS"
    result = {
        "provider": provider,
        "provider_match_id": str(match_id),
        "status": status,
        "result_source": str(payload.get("result_source", provider)),
    }
    if status != "FINAL":
        return result
    try:
        home_score = int(payload.get("home_score", payload.get("homeScore")))
        away_score = int(payload.get("away_score", payload.get("awayScore")))
    except (TypeError, ValueError) as exc:
        raise ValueError("Final result requires integer home and away scores") from exc
    if home_score < 0 or away_score < 0:
        raise ValueError("Scores must be non-negative")
    finished = payload.get("finished_at", payload.get("finishedAt"))
    result.update({
        "home_score": home_score,
        "away_score": away_score,
        "finished_at": utc_datetime(finished) if finished else None,
    })
    return result
