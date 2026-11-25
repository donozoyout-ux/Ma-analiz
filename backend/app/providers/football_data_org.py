"""Licensed football-data.org Bundesliga v4 final-score observations.

Explicit one-day scope, bounded request, read-only. No scraped HTML, no SQL/R2
writes, and no implicit trust in team-name aliases.
"""
from datetime import date, datetime, timedelta, timezone
import os
import httpx

BASE_URL = "https://api.football-data.org/v4/competitions/BL1/matches"


class FootballDataFailure(ValueError):
    pass


def parse_bundesliga_finals(payload: dict, *, day: date, observed_at: datetime) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
        raise FootballDataFailure("FOOTBALL_DATA_SCHEMA_INVALID")
    if len(payload["matches"]) > 30:
        raise FootballDataFailure("FOOTBALL_DATA_SCOPE_OVERFLOW")
    results = []
    for match in payload["matches"]:
        if not isinstance(match, dict):
            raise FootballDataFailure("FOOTBALL_DATA_MATCH_INVALID")
        competition = match.get("competition") or payload.get("competition")
        if not isinstance(competition, dict) or competition.get("code") != "BL1":
            raise FootballDataFailure("FOOTBALL_DATA_LEAGUE_MISMATCH")
        timestamp = match.get("utcDate")
        from app.providers.result_sources import utc
        kickoff = utc(timestamp)
        if kickoff is None:
            raise FootballDataFailure("FOOTBALL_DATA_KICKOFF_MISSING")
        if kickoff.date() != day:
            continue
        home, away = match.get("homeTeam") or {}, match.get("awayTeam") or {}
        score = (match.get("score") or {}).get("fullTime") or {}
        values = [score.get("home"), score.get("away")]
        if not all(type(v) is int and v >= 0 for v in values):
            continue
        if match.get("status") != "FINISHED":
            continue
        if observed_at.tzinfo is None or kickoff >= observed_at.astimezone(timezone.utc):
            raise FootballDataFailure("FOOTBALL_DATA_TIME_INVALID")
        if not all(type(side.get("id")) is int and side.get("name") for side in (home, away)):
            raise FootballDataFailure("FOOTBALL_DATA_TEAM_ID_MISSING")
        if type(match.get("id")) is not int:
            raise FootballDataFailure("FOOTBALL_DATA_MATCH_ID_MISSING")
        results.append({
            "provider": "football-data.org",
            "source_match_id": str(match["id"]),
            "league": "de.bl1",
            "home_team": home["name"],
            "away_team": away["name"],
            "home_source_id": home["id"],
            "away_source_id": away["id"],
            "start_time": kickoff.isoformat(),
            "source_status": "FINISHED",
            "reported_score": values,
            "final_confirmed": True,
            "observed_at": observed_at.isoformat(),
            "source_url": BASE_URL,
            "reuse_policy": "FOOTBALL_DATA_TERMS_ATTRIBUTION_REQUIRED",
        })
    return results


def fetch_bundesliga_finals(day: date, *, token: str | None = None, client=None, observed_at=None):
    """At most one authenticated official API request for the explicit UTC day."""
    token = token or os.getenv("FOOTBALL_DATA_ORG_API_TOKEN")
    if not token:
        raise FootballDataFailure("FOOTBALL_DATA_TOKEN_NOT_CONFIGURED")
    observed_at = observed_at or datetime.now(timezone.utc)
    if day > observed_at.date() or day < observed_at.date() - timedelta(days=31):
        raise FootballDataFailure("FOOTBALL_DATA_DATE_OUT_OF_BOUNDS")
    own = client is None
    client = client or httpx.Client(timeout=8, follow_redirects=False)
    try:
        response = client.get(BASE_URL, headers={"X-Auth-Token": token},
            params={"dateFrom": day.isoformat(),
                    "dateTo": (day + timedelta(days=1)).isoformat()})
        if response.status_code != 200:
            # Never expose auth token, request URL or provider response body.
            raise FootballDataFailure("FOOTBALL_DATA_REMOTE_UNAVAILABLE")
        if len(response.content) > 512_000:
            raise FootballDataFailure("FOOTBALL_DATA_RESPONSE_TOO_LARGE")
        try:
            payload = response.json()
        except ValueError as exc:
            raise FootballDataFailure("FOOTBALL_DATA_JSON_INVALID") from None
        return parse_bundesliga_finals(payload, day=day, observed_at=observed_at)
    except httpx.HTTPError:
        raise FootballDataFailure("FOOTBALL_DATA_NETWORK_UNAVAILABLE") from None
    finally:
        if own:
            client.close()
