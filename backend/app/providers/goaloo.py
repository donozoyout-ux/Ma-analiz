"""Goaloo adapter built against the source contract verified in live requests.

The source is undocumented and can change. Every envelope and row shape is
validated; unsupported match status codes stop ingestion instead of guessing.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from time import perf_counter
from typing import Any

import httpx

from app.providers.base import ProviderInvalidResponse, ProviderNotConfigured, ProviderUnavailable


BOOKMAKERS = {"3": "Crown", "8": "Bet365", "31": "Sbobet"}
_UTC_PLUS_8 = timezone(timedelta(hours=8))
_SCORE = re.compile(r"^(\d+)\s*[-:]\s*(\d+)$")


class GoalooProvider:
    name = "goaloo"
    provider_label = "GOOLOO"

    def __init__(self, *, base_url: str = "https://football.goaloo.com",
                 odds_base_url: str = "https://www.goaloo.com", league_ids: tuple[str, ...] = (),
                 season_ids: tuple[str, ...] = (), timeout_seconds: float = 15,
                 client: httpx.Client | None = None):
        if timeout_seconds <= 0:
            raise ValueError("Timeout must be positive")
        self.base_url, self.odds_base_url = base_url.rstrip("/"), odds_base_url.rstrip("/")
        self.league_ids, self.season_ids = tuple(league_ids), tuple(season_ids)
        self._client = client or httpx.Client(timeout=timeout_seconds, follow_redirects=True)
        self._owns_client = client is None
        self.last_latency_ms: float | None = None
        self.last_successful_request: datetime | None = None
        self._fixture_cache: dict[str, dict[str, Any]] = {}

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _get(self, url: str, *, referer: str | None = None, params: dict[str, Any] | None = None) -> Any:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; MacAnaliz/2.0)", "Accept": "application/json,text/html"}
        if referer:
            headers.update({"Referer": referer, "X-Requested-With": "XMLHttpRequest"})
        started = perf_counter()
        try:
            response = self._client.get(url, params=params, headers=headers)
        except httpx.TimeoutException:
            self.last_latency_ms = (perf_counter() - started) * 1000
            raise ProviderUnavailable("PROVIDER_UNAVAILABLE: Goaloo request timed out") from None
        except httpx.TransportError:
            self.last_latency_ms = (perf_counter() - started) * 1000
            raise ProviderUnavailable("PROVIDER_UNAVAILABLE: Goaloo network request failed") from None
        self.last_latency_ms = (perf_counter() - started) * 1000
        if response.status_code in {403, 404, 429} or response.status_code >= 500:
            raise ProviderUnavailable(f"PROVIDER_UNAVAILABLE: Goaloo returned HTTP {response.status_code}")
        if not response.is_success:
            raise ProviderInvalidResponse(f"PROVIDER_INVALID_RESPONSE: Goaloo returned HTTP {response.status_code}")
        content = response.content
        if content.startswith(b"\xef\xbb\xbf"):
            content = content[3:]
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo response was not JSON") from None
        if not isinstance(payload, (dict, list)):
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo response shape was not an object or array")
        self.last_successful_request = datetime.now(timezone.utc)
        return payload

    def health_check(self) -> dict[str, Any]:
        started = perf_counter()
        try:
            response = self._client.get(self.base_url + "/", headers={"User-Agent": "Mozilla/5.0"})
        except httpx.HTTPError:
            self.last_latency_ms = (perf_counter() - started) * 1000
            raise ProviderUnavailable("PROVIDER_UNAVAILABLE: Goaloo health request failed") from None
        self.last_latency_ms = (perf_counter() - started) * 1000
        if not response.is_success or "html" not in response.headers.get("Content-Type", "").lower():
            raise ProviderUnavailable("PROVIDER_UNAVAILABLE: Goaloo web source is unavailable")
        self.last_successful_request = datetime.now(timezone.utc)
        return {"provider": self.provider_label, "status": "OK", "latency_ms": self.last_latency_ms,
                "last_successful_request": self.last_successful_request.isoformat()}

    def _seasons(self, league_id: str) -> list[str]:
        payload = self._get(f"{self.base_url}/jsData/leagueSeason/sea{league_id}.json")
        seasons = payload.get("SeasonList") if isinstance(payload, dict) else None
        if not isinstance(seasons, list) or any(not isinstance(item, str) for item in seasons):
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo SeasonList schema changed")
        return seasons

    def get_seasons(self, league_id: str) -> list[dict]:
        if not str(league_id).isdigit():
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo league ID must be numeric")
        return [{"id": season, "name": season, "league_id": str(league_id)} for season in self._seasons(str(league_id))]

    def _schedule(self, league_id: str, season: str) -> dict[str, Any]:
        if not re.fullmatch(r"\d{4}-\d{4}", season) or not str(league_id).isdigit():
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: invalid Goaloo season or league ID")
        payload = self._get(f"{self.base_url}/jsData/matchResult/json/{season}/s{league_id}_en.json")
        if not isinstance(payload, dict) or not all(key in payload for key in ("LeagueInfo", "TeamInfo", "ScheduleList")):
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo schedule schema changed")
        if not isinstance(payload["LeagueInfo"], list) or not isinstance(payload["TeamInfo"], list) or not isinstance(payload["ScheduleList"], dict):
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo schedule sections have invalid types")
        return payload

    @staticmethod
    def _league(payload: dict[str, Any], league_id: str, season: str) -> dict[str, Any]:
        row = payload["LeagueInfo"]
        if len(row) < 3 or str(row[0]) != league_id or str(row[2]) != season:
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo LeagueInfo identity mismatch")
        return {"id": league_id, "name": str(row[1]), "season": season, "source": "goaloo"}

    def get_leagues(self) -> list[dict]:
        if not self.league_ids:
            raise ProviderNotConfigured("PROVIDER_NOT_CONFIGURED: set ENABLED_LEAGUES for Goaloo")
        leagues = []
        for league_id in self.league_ids:
            seasons = self._seasons(league_id)
            if not seasons:
                continue
            season = next((value for value in self.season_ids if value in seasons), seasons[0])
            payload = self._schedule(league_id, season)
            leagues.append(self._league(payload, league_id, season))
        return leagues

    @staticmethod
    def _kickoff(value: Any) -> datetime:
        if not isinstance(value, str):
            raise ProviderUnavailable("PROVIDER_UNAVAILABLE: Goaloo kickoff format is unknown")
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=_UTC_PLUS_8)
        except ValueError:
            raise ProviderUnavailable("PROVIDER_UNAVAILABLE: Goaloo kickoff format is unknown") from None
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _teams(payload: dict[str, Any]) -> dict[str, str]:
        teams = {}
        for row in payload["TeamInfo"]:
            if not isinstance(row, list) or len(row) < 2:
                raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo TeamInfo row schema changed")
            teams[str(row[0])] = str(row[1])
        return teams

    def _season_choices(self, league_id: str) -> list[str]:
        available = self._seasons(league_id)
        selected = [season for season in self.season_ids if season in available]
        return selected or available[:1]

    def _match_rows(self, league_id: str, season: str) -> list[dict[str, Any]]:
        payload = self._schedule(league_id, season)
        league = self._league(payload, league_id, season)
        teams = self._teams(payload)
        result = []
        for round_rows in payload["ScheduleList"].values():
            if not isinstance(round_rows, list):
                raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo ScheduleList round is not an array")
            for row in round_rows:
                if not isinstance(row, list) or len(row) < 8:
                    raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo match row schema changed")
                status_code = str(row[2])
                status = {"-1": "FINISHED", "0": "SCHEDULED"}.get(status_code, "UNKNOWN")
                try:
                    home, away = teams[str(row[4])], teams[str(row[5])]
                except KeyError:
                    raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo fixture references unknown team") from None
                start_time = self._kickoff(row[3])
                score = str(row[6])
                normalized = {"provider": self.name, "provider_match_id": str(row[0]),
                    "league_id": league_id, "league_name": league["name"], "season": season,
                    "home_team": home, "away_team": away, "start_time": start_time,
                    "status": status,
                    "provider_status_code": status_code, "source_score": score}
                self._fixture_cache[normalized["provider_match_id"]] = normalized
                result.append(normalized)
        return result

    def get_matches(self, day: date) -> list[dict]:
        if not self.league_ids:
            raise ProviderNotConfigured("PROVIDER_NOT_CONFIGURED: set ENABLED_LEAGUES for Goaloo")
        result = []
        for league_id in self.league_ids:
            for season in self._season_choices(league_id):
                for match in self._match_rows(league_id, season):
                    if match["start_time"].date() != day:
                        continue
                    if match["status"] == "UNKNOWN":
                        raise ProviderUnavailable("PROVIDER_UNAVAILABLE: unverified Goaloo match status code")
                    result.append(match)
        return result

    def get_match(self, match_id: str) -> dict | None:
        cached = self._fixture_cache.get(str(match_id))
        if cached:
            return cached
        if not self.league_ids:
            raise ProviderNotConfigured("PROVIDER_NOT_CONFIGURED: set ENABLED_LEAGUES for Goaloo")
        for league_id in self.league_ids:
            for season in self._season_choices(league_id):
                for match in self._match_rows(league_id, season):
                    if match["provider_match_id"] == str(match_id):
                        return match
        return None

    @staticmethod
    def _price(value: Any, *, hong_kong: bool) -> Decimal | None:
        if value is None or str(value).strip() in {"", "0", "-"}:
            return None
        try:
            price = Decimal(str(value)) + (Decimal("1") if hong_kong else Decimal("0"))
        except InvalidOperation:
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo odds price is not numeric") from None
        if not price.is_finite() or price <= 1:
            return None
        return price

    def get_odds(self, match_id: str) -> list[dict]:
        match = self.get_match(match_id)
        if match is None:
            return []
        page = f"{self.odds_base_url}/football/match/oddscomp-{match_id}"
        payload = self._get(f"{self.odds_base_url}/ajax/soccerajax", referer=page,
                            params={"type": 14, "t": 1, "id": match_id, "h": 0, "s": -1})
        if not isinstance(payload, dict) or payload.get("ErrCode") != 0 or not isinstance(payload.get("Data"), dict):
            raise ProviderUnavailable("PROVIDER_UNAVAILABLE: Goaloo odds endpoint returned an error envelope")
        rows = payload["Data"].get("mixodds")
        if not isinstance(rows, list):
            raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo mixodds schema changed")
        output: list[dict] = []
        capture = datetime.now(timezone.utc)
        for bookmaker in rows:
            if not isinstance(bookmaker, dict):
                raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo bookmaker row is not an object")
            cid = str(bookmaker.get("cid", ""))
            name = BOOKMAKERS.get(cid)
            if name is None:
                continue
            for market, field, sides, hk in (
                ("1X2", "euro", (("u", "HOME"), ("g", "DRAW"), ("d", "AWAY")), False),
                ("AH", "ah", (("u", "HOME"), ("d", "AWAY")), True),
                ("OU", "ou", (("u", "OVER"), ("d", "UNDER")), True),
            ):
                prices = bookmaker.get(field)
                if prices is None:
                    continue
                if not isinstance(prices, dict):
                    raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo market quote is not an object")
                for quote_key, snapshot_type, period in (("f", "OPENING", "PREMATCH"),
                                                          ("l", "LATEST", "PREMATCH"),
                                                          ("r", "LIVE", "LIVE")):
                    quote = prices.get(quote_key)
                    if quote is None:
                        continue
                    if not isinstance(quote, dict):
                        raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo quote snapshot schema changed")
                    line = quote.get("g") if market in {"AH", "OU"} else None
                    for key, selection in sides:
                        price = self._price(quote.get(key), hong_kong=hk)
                        if price is None:
                            continue
                        canonical_line = None
                        if market == "AH" and line not in (None, ""):
                            try:
                                raw_decimal = Decimal(str(line).split("/")[0])
                                if "/" in str(line):
                                    other = Decimal(str(line).split("/")[1])
                                    raw_decimal = (raw_decimal + other) / 2
                                canonical_line = -raw_decimal
                            except (InvalidOperation, IndexError):
                                raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo handicap line is invalid") from None
                        elif market == "OU":
                            canonical_line = line
                        # Goaloo's `l` value is only identified as latest by the
                        # observed contract. A finished fixture does not prove it
                        # was the final pre-kickoff capture, so never relabel it.
                        actual_type = snapshot_type
                        output.append({"provider": self.name, "provider_odds_id": f"{cid}:{field}:{quote_key}:{key}",
                            "provider_market_id": market, "provider_bookmaker_id": cid,
                            "bookmaker": name, "market": market, "selection": selection,
                            "line": canonical_line, "raw_line": str(line) if line is not None else None,
                            "odds": price, "captured_at": capture, "source": "goaloo",
                            "period": period, "snapshot_type": actual_type,
                            "status": "AVAILABLE", "raw_value": json.dumps(quote, ensure_ascii=False, separators=(",", ":"))})
        return output

    def get_results(self, day: date) -> list[dict]:
        return [{"provider_match_id": row["provider_match_id"], "status": "FINISHED",
                 "home_score": int(match.group(1)), "away_score": int(match.group(2)),
                 "result_source": "goaloo"}
                for row in self.get_matches(day) if row["status"] == "FINISHED"
                if (match := _SCORE.match(str(row.get("source_score", ""))))]

    def get_statistics(self, match_id: str) -> list[dict]:
        raise ProviderUnavailable("PROVIDER_UNAVAILABLE: Goaloo statistics endpoint has not been verified")
