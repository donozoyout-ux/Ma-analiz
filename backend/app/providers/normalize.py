from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


def utc_datetime(value) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Provider timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


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


def normalize_line(value) -> Decimal | None:
    """Normalize a decimal or split Asian line without losing its raw value."""
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip().replace("−", "-")
    try:
        if "/" in raw:
            parts = [Decimal(part.strip()) for part in raw.split("/")]
            if len(parts) != 2:
                raise InvalidOperation
            line = sum(parts, Decimal("0")) / Decimal("2")
        else:
            line = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValueError("Odds line must be a decimal or two-part split line") from None
    if not line.is_finite():
        raise ValueError("Odds line must be finite")
    return line.quantize(Decimal("0.001"))


def _canonical_market(value: str) -> str:
    folded = " ".join(value.strip().upper().replace("_", " ").replace("-", " ").split())
    if folded in {"1X2", "1 X 2", "FULLTIME RESULT", "FULL TIME RESULT", "MATCH RESULT", "MATCH WINNER"}:
        return "1X2"
    if folded in {"AH", "ASIAN HANDICAP", "ASIAN HANDICAP RESULT", "HANDICAP"}:
        return "AH"
    if folded in {"OU", "O/U", "OVER/UNDER", "OVER UNDER", "TOTAL GOALS", "GOALS OVER UNDER"}:
        return "OU"
    if folded in {"BTTS", "BOTH TEAMS TO SCORE"}:
        return "BTTS"
    return folded


def _canonical_selection(value: str, market: str) -> str:
    folded = " ".join(value.strip().upper().replace("_", " ").split())
    aliases = {"HOME": "HOME", "1": "HOME", "DRAW": "DRAW", "X": "DRAW",
               "AWAY": "AWAY", "2": "AWAY"}
    if folded in aliases:
        return aliases[folded]
    if folded.startswith("HOME ") or folded.startswith("HOME("):
        return "HOME"
    if folded.startswith("AWAY ") or folded.startswith("AWAY("):
        return "AWAY"
    if folded.startswith("DRAW ") or folded.startswith("DRAW("):
        return "DRAW"
    if folded.startswith("OVER"):
        return "OVER"
    if folded.startswith("UNDER"):
        return "UNDER"
    if market == "BTTS" and folded in {"YES", "NO"}:
        return folded
    return folded


def _canonical_period(payload: dict) -> str:
    raw = str(payload.get("period") or "").strip().upper().replace("-", "_").replace(" ", "_")
    status = str(payload.get("status") or "").strip().upper().replace("-", "_").replace(" ", "_")
    if (payload.get("is_live") is True or payload.get("in_play") is True
            or raw in {"LIVE", "INPLAY", "IN_PLAY", "R"}
            or status in {"LIVE", "INPLAY", "IN_PLAY", "R"}):
        return "LIVE"
    if raw in {"PREMATCH", "PRE_MATCH", "PRE_GAME", "PRE"}:
        return "PREMATCH"
    return "UNKNOWN"


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
    market = _canonical_market(payload["market"])
    line_value = payload.get("line")
    if line_value is None and market == "AH":
        line_value = payload.get("handicap")
    if line_value is None and market == "OU":
        line_value = payload.get("total")
    raw_line_value = payload.get("raw_line", line_value)
    line = normalize_line(line_value)
    period = _canonical_period(payload)
    raw_status = str(payload.get("status") or "").strip().upper()
    if payload.get("stopped") is True:
        raw_status = "SUSPENDED"
    status = raw_status if raw_status in {"ACTIVE", "AVAILABLE", "OPEN", "SUSPENDED", "CLOSED", "UNKNOWN"} else "AVAILABLE"
    source = str(payload.get("source") or payload.get("provider") or provider or "unknown").strip()
    if not source:
        source = "unknown"
    canonical_selection = _canonical_selection(payload["selection"], market)
    snapshot_type = str(payload.get("snapshot_type") or "OBSERVED").strip().upper()
    if snapshot_type not in {"OPENING", "LATEST", "CLOSING", "LIVE", "OBSERVED"}:
        raise ValueError("Odds snapshot type is unsupported")
    line_key = format(line.normalize(), "f") if line is not None else ""
    return {
        "match_id": match_id,
        "provider": str(payload.get("provider", provider or "unknown")),
        "provider_odds_id": str(payload["provider_odds_id"]) if payload.get("provider_odds_id") is not None else None,
        "provider_market_id": str(payload["provider_market_id"]) if payload.get("provider_market_id") is not None else None,
        "provider_bookmaker_id": str(payload["provider_bookmaker_id"]) if payload.get("provider_bookmaker_id") is not None else None,
        "bookmaker": str(payload["bookmaker"]).strip(),
        "bookmaker_country": str(payload["bookmaker_country"]).strip() if payload.get("bookmaker_country") else None,
        "market": market,
        "selection": canonical_selection,
        "line": line,
        "raw_line": str(raw_line_value).strip() if raw_line_value is not None else None,
        "line_key": line_key,
        "odds": price,
        "price": price,
        "captured_at": utc_datetime(captured),
        "source": source,
        "period": period,
        "snapshot_type": snapshot_type,
        "raw_value": str(payload["raw_value"]) if payload.get("raw_value") is not None else None,
        "status": status,
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
