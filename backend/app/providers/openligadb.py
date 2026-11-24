"""OpenLigaDB provider for Bundesliga (de.bl1).

Complies with OpenLigaDB terms:
- Attribution: Data: OpenLigaDB (https://www.openligadb.de), ODbL 1.0
- Rate limiting: Max 1 request per league per 60s
- Strict schema validation
- No fabricated odds, stats, or predictions
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
import re
import threading
import time
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from app.providers.base import (
    MatchProvider,
    ProviderAccessRestricted,
    ProviderError,
    ProviderInvalidResponse,
    ProviderRateLimited,
    ProviderUnavailable,
)
from app.providers.result_sources import utc, score

ISTANBUL = ZoneInfo("Europe/Istanbul")
ATTRIBUTION = "Data: OpenLigaDB (https://www.openligadb.de), ODbL 1.0"
LICENCE = "ODbL-1.0"
LICENCE_URL = "https://www.openligadb.de/lizenz"
TERMS_URL = "https://www.openligadb.de/nutzungsbedingungen"


class OpenLigaDBProvider:
    name: str = "openligadb"
    attribution: str = ATTRIBUTION
    licence: str = LICENCE
    licence_url: str = LICENCE_URL

    def __init__(
        self,
        base_url: str = "https://api.openligadb.de",
        *,
        timeout_seconds: float = 10.0,
        requests_per_minute: int = 1,
        max_retries: int = 3,
        client: httpx.Client | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "https":
            raise ValueError("OpenLigaDB requires HTTPS endpoint")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.min_interval = 60.0 / max(1, requests_per_minute)
        self.max_retries = max_retries
        self._lock = threading.Lock()
        self._last_request_time: float = 0.0
        self._client = client or httpx.Client(timeout=timeout_seconds, follow_redirects=False)
        self._own_client = client is None
        self.last_latency_ms: float | None = None
        self.last_successful_request: str | None = None

    def close(self) -> None:
        if self._own_client:
            self._client.close()

    def __enter__(self) -> OpenLigaDBProvider:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _throttle(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_request_time = time.monotonic()

    def _get(self, path: str) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        attempt = 0
        backoff = 1.0

        while attempt < self.max_retries:
            # Every retry is a new external request and must obey throttling.
            self._throttle()
            attempt += 1
            t0 = time.monotonic()
            try:
                resp = self._client.get(url, headers={"User-Agent": "MacAnaliz/1.0 (Bundesliga Safe Ingestion)"})
                self.last_latency_ms = (time.monotonic() - t0) * 1000
                if resp.status_code == 200:
                    self.last_successful_request = datetime.now(timezone.utc).isoformat()
                    try:
                        return resp.json()
                    except Exception as exc:
                        raise ProviderInvalidResponse("OPENLIGADB_INVALID_JSON") from exc
                elif resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", backoff))
                    if attempt >= self.max_retries:
                        raise ProviderRateLimited("OpenLigaDB rate limit reached", retry_after=retry_after)
                    time.sleep(retry_after)
                    backoff *= 2
                    continue
                elif 300 <= resp.status_code < 400:
                    raise ProviderAccessRestricted("OpenLigaDB redirect refused")
                elif resp.status_code in (401, 403):
                    raise ProviderAccessRestricted(f"OpenLigaDB access denied HTTP {resp.status_code}")
                elif resp.status_code >= 500:
                    if attempt >= self.max_retries:
                        raise ProviderUnavailable(f"OpenLigaDB server error HTTP {resp.status_code}")
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                else:
                    raise ProviderError(f"OpenLigaDB HTTP {resp.status_code}")
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= self.max_retries:
                    raise ProviderUnavailable(f"OpenLigaDB network failure: {exc}") from exc
                time.sleep(backoff)
                backoff *= 2

        raise ProviderUnavailable("OpenLigaDB max retries exceeded")

    def health_check(self) -> dict[str, Any]:
        """Read-only probe against OpenLigaDB current group."""
        t0 = time.monotonic()
        try:
            data = self._get("getcurrentgroup/bl1")
            latency = (time.monotonic() - t0) * 1000
            if not isinstance(data, dict) or "groupOrderID" not in data:
                return {
                    "provider": "OPENLIGADB",
                    "status": "DEGRADED",
                    "latency_ms": latency,
                    "error_code": "UNEXPECTED_HEALTH_SCHEMA",
                    "attribution": ATTRIBUTION,
                }
            return {
                "provider": "OPENLIGADB",
                "status": "PROVIDER_OK",
                "latency_ms": latency,
                "current_group": data.get("groupName"),
                "group_order_id": data.get("groupOrderID"),
                "attribution": ATTRIBUTION,
            }
        except Exception as exc:
            return {
                "provider": "OPENLIGADB",
                "status": "OFFLINE",
                "latency_ms": None,
                "error_code": str(exc),
                "attribution": ATTRIBUTION,
            }

    def get_leagues(self) -> list[dict[str, Any]]:
        return [{
            "league_id": "de.bl1",
            "name": "Bundesliga",
            "shortcut": "bl1",
            "country": "Germany",
            "sport": "football",
            "attribution": ATTRIBUTION,
        }]

    def get_seasons(self, league_id: str) -> list[dict[str, Any]]:
        if league_id not in ("de.bl1", "bl1"):
            return []
        return [{
            "season_id": "2026-2027",
            "year": 2026,
            "league_id": "de.bl1",
            "attribution": ATTRIBUTION,
        }]

    def get_matchdata_by_group(self, league_shortcut: str, season_year: int, group_order_id: int) -> list[dict[str, Any]]:
        if league_shortcut != "bl1":
            raise ValueError(f"Unsupported league shortcut: {league_shortcut}")
        path = f"getmatchdata/{league_shortcut}/{season_year}/{group_order_id}"
        payload = self._get(path)
        if not isinstance(payload, list):
            raise ProviderInvalidResponse("Expected list of matches from OpenLigaDB")
        return self._parse_matches(payload)

    def get_matches(self, day: date) -> list[dict[str, Any]]:
        """Fetch matches on given calendar day in Europe/Istanbul timezone."""
        # OpenLigaDB supports getmatchdata/bl1/{year}
        season_year = day.year if day.month >= 7 else day.year - 1
        path = f"getmatchdata/bl1/{season_year}"
        payload = self._get(path)
        if not isinstance(payload, list):
            raise ProviderInvalidResponse("Expected list of matches from OpenLigaDB")
        all_matches = self._parse_matches(payload)
        return [m for m in all_matches if m.get("match_date") == day]

    def get_competition_matches(self, league_shortcut: str, day: date) -> list[dict[str, Any]]:
        """Read-only daily UEFA fixtures, restricted to the reviewed 2026/27 catalog.

        Exact OpenLigaDB shortcuts are used, not an assumed or guessed season alias.
        No SQL/R2 writes and no odds or statistics are generated here.
        """
        from app.providers.european_cups import EUROPEAN_CUPS
        competition = next((item for item in EUROPEAN_CUPS.values()
                            if item["shortcut"] == league_shortcut), None)
        if competition is None:
            raise ValueError("Unsupported European competition")
        if not date(2026, 7, 1) <= day <= date(2027, 6, 30):
            raise ValueError("Only pilot season 2026/27 is supported")
        payload = self._get(f"getmatchdata/{league_shortcut}/2026")
        if not isinstance(payload, list):
            raise ProviderInvalidResponse("Expected a list of European fixtures")
        matches = self._parse_matches(payload, league_id=competition["league_id"],
                                      league_name=competition["name"], country="Europe")
        return [m for m in matches if m["match_date"] == day and m["season_year"] == 2026]

    def get_match(self, match_id: str) -> dict[str, Any] | None:
        if not match_id.isdigit():
            return None
        payload = self._get(f"getmatchdata/{match_id}")
        if not isinstance(payload, dict):
            return None
        matches = self._parse_matches([payload])
        return matches[0] if matches else None

    def get_odds(self, match_id: str) -> list[dict[str, Any]]:
        # OpenLigaDB does NOT provide odds data. Never fabricate odds.
        return []

    def get_statistics(self, match_id: str) -> list[dict[str, Any]]:
        # OpenLigaDB does NOT provide in-depth technical statistics. Never fabricate.
        return []

    def get_results(self, day: date) -> list[dict[str, Any]]:
        matches = self.get_matches(day)
        results = []
        for m in matches:
            if m.get("status") == "FINISHED" and m.get("score") is not None:
                results.append({
                    "provider": "openligadb",
                    "provider_match_id": m["provider_match_id"],
                    "home_score": m["score"][0],
                    "away_score": m["score"][1],
                    "status": "FINAL",
                    "result_source": f"{ATTRIBUTION}; {LICENCE_URL}",
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                })
        return results

    def _parse_matches(self, payload: list[dict[str, Any]], *, league_id: str = "de.bl1",
                       league_name: str = "Bundesliga", country: str = "Germany") -> list[dict[str, Any]]:
        observed_at = datetime.now(timezone.utc)
        parsed: list[dict[str, Any]] = []

        for raw in payload:
            if not isinstance(raw, dict):
                continue
            match_id = raw.get("matchID")
            if match_id is None:
                continue

            kickoff = utc(raw.get("matchDateTimeUTC"))
            if kickoff is None:
                continue

            home_team = (raw.get("team1") or {}).get("teamName")
            away_team = (raw.get("team2") or {}).get("teamName")
            if not home_team or not away_team:
                continue

            finished = raw.get("matchIsFinished") is True

            # Extract full time score (resultTypeID == 2 in OpenLigaDB Endergebnis)
            final_scores = [
                score(s.get("pointsTeam1"), s.get("pointsTeam2"))
                for s in raw.get("matchResults", [])
                if s.get("resultTypeID") == 2
            ]
            valid_scores = [s for s in final_scores if s is not None]
            full_time_score = valid_scores[0] if valid_scores and len(set(tuple(s) for s in valid_scores)) == 1 else None

            # Half time score (resultTypeID == 1)
            ht_scores = [
                score(s.get("pointsTeam1"), s.get("pointsTeam2"))
                for s in raw.get("matchResults", [])
                if s.get("resultTypeID") == 1
            ]
            half_time_score = ht_scores[0] if ht_scores and ht_scores[0] is not None else None

            status = "FINISHED" if finished else ("LIVE" if kickoff < observed_at else "SCHEDULED")

            group_name = (raw.get("group") or {}).get("groupName")
            season_year = raw.get("leagueSeason") or (kickoff.year if kickoff.month >= 7 else kickoff.year - 1)
            season_str = f"{season_year}-{season_year + 1}"

            parsed.append({
                "provider": "openligadb",
                "provider_match_id": str(match_id),
                "league_id": league_id,
                "league_name": league_name,
                "season": season_str,
                "season_year": season_year,
                "home_team": home_team.strip(),
                "away_team": away_team.strip(),
                "home_source_id": (raw.get("team1") or {}).get("teamId"),
                "away_source_id": (raw.get("team2") or {}).get("teamId"),
                "start_time": kickoff,
                "match_date": kickoff.astimezone(ISTANBUL).date(),
                "status": status,
                "score": full_time_score,
                "half_time_score": half_time_score,
                "round": group_name,
                "venue": None,
                "country": country,
                "sport": "football",
                "licence": LICENCE,
                "attribution": ATTRIBUTION,
                "licence_url": LICENCE_URL,
                "raw_source_updated_at": raw.get("lastUpdateDateTime"),
            })

        return parsed
