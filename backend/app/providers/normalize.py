from datetime import datetime
from decimal import Decimal, InvalidOperation


def _utc_datetime(value) -> datetime:
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
    return {
        "provider": provider,
        "provider_match_id": str(match_id),
        "league_id": str(payload.get("league_id", league.get("id", ""))) or None,
        "league_name": payload.get("league_name", league.get("name")),
        "season": str(payload.get("season", "")) or None,
        "home_team": _team_name(payload.get("home_team", payload.get("homeTeam"))),
        "away_team": _team_name(payload.get("away_team", payload.get("awayTeam"))),
        "start_time": _utc_datetime(start),
        "status": str(payload.get("status", "SCHEDULED")).upper(),
    }


def normalize_matches(payloads: list[dict], *, provider: str) -> list[dict]:
    """Normalize and deduplicate provider responses by provider + provider ID."""
    unique: dict[tuple[str, str], dict] = {}
    for payload in payloads:
        match = normalize_match(payload, provider=provider)
        unique[(match["provider"], match["provider_match_id"])] = match
    return list(unique.values())


def normalize_odds(payload: dict, *, match_id: str) -> dict:
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
        "bookmaker": str(payload["bookmaker"]).strip(),
        "market": str(payload["market"]).strip(),
        "selection": str(payload["selection"]).strip(),
        "odds": price,
        "captured_at": _utc_datetime(captured),
    }
