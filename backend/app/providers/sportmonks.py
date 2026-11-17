"""Sportmonks Football API 3 adapter.

Provider response shapes stay inside this module. API tokens are sent in the
documented Authorization header and are never included in request URLs/errors.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from time import perf_counter
from typing import Any

import httpx

from app.providers.base import (
    ProviderAuthError,
    ProviderInvalidResponse,
    ProviderNotFound,
    ProviderRateLimited,
    ProviderUnavailable,
)
from app.providers.normalize import normalize_match, utc_datetime


_FINISHED = {"FT", "AET", "FT_PEN", "FULL_TIME", "FINISHED", "PEN"}
_LAST_SUCCESSFUL_REQUEST: datetime | None = None
_STATUS_MAP = {
    "NS": "SCHEDULED", "NOT_STARTED": "SCHEDULED", "SCHEDULED": "SCHEDULED",
    "1H": "LIVE", "2H": "LIVE", "HT": "PAUSED", "ET": "LIVE", "BT": "PAUSED",
    "LIVE": "LIVE", "IN_PLAY": "IN_PLAY", "POSTPONED": "POSTPONED", "PST": "POSTPONED",
    "CANCELLED": "CANCELLED", "CANCELED": "CANCELLED", "CAN": "CANCELLED",
    "SUSPENDED": "SUSPENDED", "SUS": "SUSPENDED", "FT": "FINISHED", "AET": "FINISHED",
    "FT_PEN": "FINISHED", "FULL_TIME": "FINISHED", "FINISHED": "FINISHED", "PEN": "FINISHED",
}


def _identifier(value: Any, field: str) -> str:
    if value is None or not str(value).strip():
        raise ProviderInvalidResponse(f"PROVIDER_INVALID_RESPONSE: missing {field}")
    return str(value)


def _object(value: Any, field: str) -> dict:
    if not isinstance(value, dict):
        raise ProviderInvalidResponse(f"PROVIDER_INVALID_RESPONSE: invalid {field}")
    return value


def _season_name(season: Any) -> str:
    season_data = _object(season, "season")
    name = season_data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: season name is missing")
    return name.strip()


def _timestamp(value: Any) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: invalid provider timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _market_name(value: str) -> str:
    normalized = "_".join(value.strip().lower().replace("-", " ").split())
    if normalized in {"1x2", "fulltime_result", "full_time_result", "match_result", "match_winner"}:
        return "1X2"
    if normalized in {"over_under", "over/under", "total_goals", "goals_over_under"}:
        return "OU"
    if normalized in {"asian_handicap", "asian_handicap_result", "handicap"}:
        return "AH"
    if normalized in {"btts", "both_teams_to_score"}:
        return "BTTS"
    return value.strip()


class SportmonksProvider:
    name = "sportmonks"
    provider_label = "SPORTMONKS"

    def __init__(self, *, token: str, base_url: str = "https://api.sportmonks.com/v3/football",
                 timeout_seconds: float = 15, max_pages: int = 100,
                 client: httpx.Client | None = None):
        if not token or not token.strip():
            raise ValueError("Sportmonks token is required")
        if timeout_seconds <= 0 or max_pages < 1:
            raise ValueError("Timeout and page limits must be positive")
        self._token = token.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_pages = max_pages
        self._client = client or httpx.Client(timeout=timeout_seconds)
        # Apply auth per request so injected clients and the production client
        # follow the same documented contract without putting the token in a URL.
        self._owns_client = client is None
        self.last_latency_ms: float | None = None
        self.last_successful_request: datetime | None = _LAST_SUCCESSFUL_REQUEST
        self.last_records_received = 0
        self.last_records_rejected = 0
        self.historical_odds_supported: bool | None = None
        self.opening_odds_supported: bool | None = None
        self.current_odds_supported: bool | None = None
        self.observed_markets: set[str] = set()
        self.observed_bookmakers: set[str] = set()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        started = perf_counter()
        try:
            response = self._client.get(f"{self.base_url}/{path.lstrip('/')}", params=params or {},
                                        headers={"Authorization": self._token})
        except httpx.TimeoutException:
            self.last_latency_ms = (perf_counter() - started) * 1000
            raise ProviderUnavailable("PROVIDER_UNAVAILABLE: Sportmonks request timed out") from None
        except httpx.TransportError:
            self.last_latency_ms = (perf_counter() - started) * 1000
            raise ProviderUnavailable("PROVIDER_UNAVAILABLE: Sportmonks network request failed") from None
        self.last_latency_ms = (perf_counter() - started) * 1000
        if response.status_code in (401, 403):
            raise ProviderAuthError("PROVIDER_AUTH_ERROR: Sportmonks rejected credentials or account access")
        if response.status_code == 429:
            retry_after = None
            header = response.headers.get("Retry-After")
            if header:
                try:
                    retry_after = max(0.0, float(header))
                except ValueError:
                    try:
                        retry_after = max(0.0, (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds())
                    except (TypeError, ValueError, OverflowError):
                        retry_after = None
            raise ProviderRateLimited("PROVIDER_RATE_LIMITED: Sportmonks request quota exceeded", retry_after)
        if response.status_code == 404:
            raise ProviderNotFound("PROVIDER_NOT_FOUND: Sportmonks resource not found")
        if response.status_code >= 500:
            raise ProviderUnavailable("PROVIDER_UNAVAILABLE: Sportmonks server error")
        if not response.is_success:
            raise ProviderInvalidResponse(f"PROVIDER_INVALID_RESPONSE: Sportmonks returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Sportmonks response was not JSON") from None
        if not isinstance(payload, dict) or "data" not in payload:
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Sportmonks response envelope is invalid")
        global _LAST_SUCCESSFUL_REQUEST
        self.last_successful_request = datetime.now(timezone.utc)
        _LAST_SUCCESSFUL_REQUEST = self.last_successful_request
        return payload

    def _pages(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        collected: list[dict] = []
        base_params = dict(params or {})
        for page in range(1, self.max_pages + 1):
            payload = self._get(path, {**base_params, "page": page})
            data = payload["data"]
            if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
                raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: expected a list of provider records")
            collected.extend(data)
            meta = payload.get("meta") or {}
            pagination = meta.get("pagination") if isinstance(meta, dict) else None
            has_more = pagination.get("has_more", False) if isinstance(pagination, dict) else False
            if not has_more:
                return collected
        raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: pagination exceeded the configured safety limit")

    def health_check(self) -> dict[str, Any]:
        payload = self._get("leagues", {"per_page": 1})
        if not isinstance(payload["data"], list):
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: league health response was not a list")
        return {"provider": self.provider_label, "status": "OK", "latency_ms": self.last_latency_ms,
                "error_code": None, "last_successful_request": self.last_successful_request.isoformat()}

    def get_leagues(self) -> list[dict]:
        rows = self._pages("leagues", {"include": "country;currentSeason", "per_page": 50})
        leagues: list[dict] = []
        for row in rows:
            country = row.get("country") if isinstance(row.get("country"), dict) else {}
            current_season = row.get("currentSeason") if isinstance(row.get("currentSeason"), dict) else None
            leagues.append({
                "id": _identifier(row.get("id"), "league id"),
                "name": _identifier(row.get("name"), "league name"),
                "country": country.get("name"),
                "current_season": ({"id": str(current_season["id"]), "name": current_season.get("name")}
                                   if current_season and current_season.get("id") is not None else None),
            })
        return leagues

    def get_seasons(self, league_id: str) -> list[dict]:
        league = _identifier(league_id, "league id")
        payload = self._get(f"leagues/{league}", {"include": "seasons"})
        data = _object(payload["data"], "league")
        seasons = data.get("seasons")
        if not isinstance(seasons, list):
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: league seasons were not included")
        result = []
        for season in seasons:
            season = _object(season, "season")
            result.append({"id": _identifier(season.get("id"), "season id"),
                           "name": _identifier(season.get("name"), "season name"),
                           "league_id": league,
                           "starting_at": season.get("starting_at"),
                           "ending_at": season.get("ending_at"),
                           "is_current": season.get("is_current")})
        return result

    def _fixture_match(self, fixture: dict) -> dict:
        league = _object(fixture.get("league"), "fixture league")
        season = _object(fixture.get("season"), "fixture season")
        state = _object(fixture.get("state"), "fixture state")
        participants = fixture.get("participants")
        if not isinstance(participants, list):
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: fixture participants are missing")
        teams: dict[str, str] = {}
        for participant in participants:
            if not isinstance(participant, dict) or not isinstance(participant.get("meta"), dict):
                continue
            location = participant["meta"].get("location")
            if location in ("home", "away") and isinstance(participant.get("name"), str):
                teams[location] = participant["name"]
        if set(teams) != {"home", "away"}:
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: fixture home/away participants are incomplete")
        timestamp = fixture.get("starting_at_timestamp")
        if timestamp is not None:
            try:
                start_time = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
            except (ValueError, TypeError, OSError, OverflowError):
                raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: fixture timestamp is invalid") from None
        else:
            start_time = _timestamp(fixture.get("starting_at"))
        raw_status = state.get("short_name") or state.get("developer_name") or state.get("name")
        if not isinstance(raw_status, str) or not raw_status.strip():
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: fixture state is missing")
        status = _STATUS_MAP.get(raw_status.strip().upper(), "UNKNOWN")
        canonical = {
            "id": _identifier(fixture.get("id"), "fixture id"),
            "league_id": _identifier(fixture.get("league_id", league.get("id")), "league id"),
            "league_name": _identifier(league.get("name"), "league name"),
            "season": _season_name(season),
            "home_team": teams["home"], "away_team": teams["away"],
            "start_time": start_time, "status": status,
        }
        round_data = fixture.get("round")
        if isinstance(round_data, dict) and round_data.get("name") is not None:
            canonical["round"] = str(round_data["name"])
        elif fixture.get("round_id") is not None:
            canonical["round"] = str(fixture["round_id"])
        match = normalize_match(canonical, provider=self.name)
        match["provider_season_id"] = _identifier(fixture.get("season_id", season.get("id")), "season id")
        return match

    def _fixtures(self, day: date) -> list[dict]:
        return self._pages(f"fixtures/date/{day.isoformat()}", {
            "include": "league;season;participants;state;scores;round", "per_page": 50,
        })

    def get_matches(self, day: date) -> list[dict]:
        rows = self._fixtures(day)
        self.last_records_received, self.last_records_rejected = len(rows), 0
        matches = []
        for row in rows:
            try:
                matches.append(self._fixture_match(row))
            except (ProviderInvalidResponse, ValueError):
                self.last_records_rejected += 1
        if rows and not matches:
            raise ProviderInvalidResponse(
                f"PROVIDER_INVALID_RESPONSE: rejected all {len(rows)} fixture records"
            )
        return matches

    def get_match(self, match_id: str) -> dict | None:
        fixture_id = _identifier(match_id, "fixture id")
        try:
            payload = self._get(f"fixtures/{fixture_id}", {
                "include": "league;season;participants;state;scores;round",
            })
        except ProviderNotFound:
            return None
        return self._fixture_match(_object(payload["data"], "fixture"))

    def get_statistics(self, match_id: str) -> list[dict]:
        fixture_id = _identifier(match_id, "fixture id")
        payload = self._get(f"fixtures/{fixture_id}", {"include": "statistics.type"})
        fixture = _object(payload["data"], "fixture")
        statistics = fixture.get("statistics")
        if not isinstance(statistics, list):
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: fixture statistics were not included")
        output = []
        for item in statistics:
            item = _object(item, "fixture statistic")
            output.append({"id": item.get("id"), "fixture_id": fixture_id,
                           "team_id": item.get("participant_id", item.get("team_id")),
                           "type_id": item.get("type_id"), "value": item.get("data", item.get("value")),
                           "source": self.name})
        return output

    def get_odds(self, match_id: str) -> list[dict]:
        fixture_id = _identifier(match_id, "fixture id")
        payload = self._get(f"odds/premium/fixtures/{fixture_id}", {
            "include": "market;bookmaker;history",
        })
        rows = payload["data"]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: premium odds data is not a list")
        normalized: list[dict] = []
        has_history = False
        has_current = False
        for row in rows:
            market_obj = row.get("market") if isinstance(row.get("market"), dict) else {}
            bookmaker_obj = row.get("bookmaker") if isinstance(row.get("bookmaker"), dict) else {}
            market_id = _identifier(row.get("market_id"), "odds market id")
            bookmaker_id = _identifier(row.get("bookmaker_id"), "odds bookmaker id")
            market_name = market_obj.get("name") or row.get("market_description")
            bookmaker_name = bookmaker_obj.get("name")
            if not isinstance(market_name, str) or not market_name.strip():
                market_name = market_id
            market_name = _market_name(market_name)
            if not isinstance(bookmaker_name, str) or not bookmaker_name.strip():
                bookmaker_name = bookmaker_id
            bookmaker_country = bookmaker_obj.get("country")
            if isinstance(bookmaker_country, dict):
                bookmaker_country = bookmaker_country.get("name")
            history = row.get("history")
            snapshots = []
            if history is not None:
                if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
                    raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: premium odds history is malformed")
                for item in history:
                    captured_value = item.get("bookmaker_update")
                    if captured_value is None:
                        raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: odds history timestamp is missing")
                    snapshots.append((item.get("value"), _timestamp(captured_value), item.get("id")))
                    has_history = True
            # Preserve the actual current value alongside the history when the API
            # supplies a timestamp; never manufacture a timestamp for missing data.
            captured_value = row.get("updated_at") or row.get("created_at") or row.get("latest_bookmaker_update")
            if captured_value is not None:
                snapshots.append((row.get("value"), _timestamp(captured_value), None))
                has_current = True
            if not snapshots:
                raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: odds timestamp is missing")
            odd_id = _identifier(row.get("id"), "odds id")
            deduplicated: dict[tuple[datetime, str], tuple[Any, datetime, Any]] = {}
            for value, captured_at, snapshot_id in snapshots:
                if value is None:
                    raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: odds value is missing")
                deduplicated.setdefault((captured_at, str(value)), (value, captured_at, snapshot_id))
            self.observed_markets.add(market_name)
            self.observed_bookmakers.add(bookmaker_name.strip())
            for value, captured_at, history_id in deduplicated.values():
                selection = row.get("label") or row.get("name")
                if not isinstance(selection, str) or not selection.strip():
                    raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: odds selection is missing")
                normalized.append({
                    "provider": self.name,
                    "provider_odds_id": f"{odd_id}:{history_id}" if history_id is not None else odd_id,
                    "bookmaker": bookmaker_name.strip(), "market": market_name.strip(),
                    "bookmaker_country": bookmaker_country,
                    "selection": selection.strip(), "odds": value, "captured_at": captured_at,
                    "line": row.get("handicap") if market_name == "AH" else row.get("total") if market_name == "OU" else None,
                    "period": "PREMATCH", "status": "SUSPENDED" if row.get("stopped") is True else "AVAILABLE",
                    "source": self.provider_label,
                    "provider_market_id": market_id, "provider_bookmaker_id": bookmaker_id,
                })
        self.historical_odds_supported = has_history if rows else None
        self.opening_odds_supported = has_history if rows else None
        self.current_odds_supported = has_current if rows else None
        return normalized

    def get_results(self, day: date) -> list[dict]:
        rows = self._fixtures(day)
        results = []
        for row in rows:
            state = row.get("state") if isinstance(row.get("state"), dict) else {}
            state_name = state.get("short_name") or state.get("developer_name") or state.get("name")
            if not isinstance(state_name, str) or state_name.strip().upper() not in _FINISHED:
                continue
            scores = row.get("scores")
            if not isinstance(scores, list):
                raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: final fixture scores are missing")
            current: dict[str, int] = {}
            for score_row in scores:
                if not isinstance(score_row, dict) or score_row.get("description") != "CURRENT":
                    continue
                score = score_row.get("score")
                if isinstance(score, dict) and score.get("participant") in ("home", "away"):
                    current[score["participant"]] = score.get("goals")
            if set(current) != {"home", "away"}:
                raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: final current score is incomplete")
            if any(not isinstance(current[side], int) or isinstance(current[side], bool) or current[side] < 0
                   for side in ("home", "away")):
                raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: final current score is invalid")
            finished_at = row.get("finished_at")
            results.append({
                "provider": self.name,
                "provider_match_id": _identifier(row.get("id"), "fixture id"),
                "home_score": current["home"], "away_score": current["away"],
                "status": "FINAL",
                "finished_at": _timestamp(finished_at) if finished_at else None,
                "result_source": self.provider_label,
            })
        return results
