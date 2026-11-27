"""Allowlisted season files and bounded, read-only downloads."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import re
import time
import unicodedata
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

import httpx

SEASONS = ("2024-2025", "2025-2026", "2026-2027")
LEAGUES = {
    "uefa.cl": ("Şampiyonlar Ligi", "Avrupa kupaları"),
    "uefa.el": ("Avrupa Ligi", "Avrupa kupaları"),
    "uefa.ecl": ("Konferans Ligi", "Avrupa kupaları"),
    "tr.sl": ("Süper Lig", "Süper Lig"),
    "de.bl1": ("Bundesliga", "Bundesliga"),
    "be.1": ("Belçika Pro League", "Belçika"),
    **{key: (name, "Diğer ligler") for key, name in (
        ("nl.1", "Eredivisie"), ("pt.1", "Primeira Liga"), ("sco.1", "Premiership"),
        ("fr.1", "Ligue 1"), ("es.1", "La Liga"), ("en.pl", "Premier League"), ("it.1", "Serie A"),
    )},
}
OPENFOOTBALL_REV = "abfaeddc2ee3d14f99ecc163c9ddb46cb4d67cef"
DOMESTIC_REV = "e6744429ee395bc86f247348c6184bb08d4eb361"
MAX_BYTES = 2 * 1024 * 1024
CET = ZoneInfo("Europe/Paris")
MONTH = {name: i for i, name in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
DAY = re.compile(r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\w{3}) (\d{1,2})(?: (\d{4}))?")
FIXTURE = re.compile(r"(?:(\d{2}:\d{2})\s+)?(.+?)\s+v\s+(.+?\([A-Z]{3}\))(?:\s+(.*))?")
PAIR = r"\d{1,2}-\d{1,2}"
SCORE = re.compile(
    rf"(?:(?P<pen>{PAIR}) pen\. )?(?P<final>{PAIR})(?P<extra> a\.e\.t\.)?"
    rf"(?: \((?P<first>{PAIR})(?:, (?P<half>{PAIR}))?\))?"
)


@dataclass(frozen=True)
class Source:
    league: str
    season: str
    provider: str
    format: str
    url: str
    licence: str
    licence_url: str
    revision: str | None = None


def source_for(league: str, season: str) -> Source | None:
    if season not in SEASONS:
        raise ValueError("Unsupported season")
    short = f"{season[:4]}-{season[-2:]}"
    if league in {"uefa.cl", "uefa.el", "uefa.ecl"}:
        if season == "2026-2027":
            from app.providers.european_cups import EUROPEAN_CUPS

            shortcut = next(item["shortcut"] for item in EUROPEAN_CUPS.values() if item["league_id"] == league)
            return Source(league, season, "openligadb", "openligadb",
                          f"https://api.openligadb.de/getmatchdata/{shortcut}/2026", "ODbL-1.0",
                          "https://www.openligadb.de/lizenz")
        if season == "2025-2026" and league == "uefa.el":
            return Source(league, season, "openligadb", "openligadb",
                          "https://api.openligadb.de/getmatchdata/uel/2025", "ODbL-1.0",
                          "https://www.openligadb.de/lizenz")
        if season == "2025-2026" and league == "uefa.ecl":
            return None
        filename = {"uefa.cl": "cl", "uefa.el": "el", "uefa.ecl": "conf"}[league]
        base = f"https://raw.githubusercontent.com/openfootball/champions-league/{OPENFOOTBALL_REV}"
        return Source(league, season, "openfootball", "football.txt", f"{base}/{short}/{filename}.txt",
                      "CC0-1.0", f"{base}/LICENSE.md", OPENFOOTBALL_REV)
    if league in {"tr.sl", "de.bl1", "be.1"}:
        filename = {"tr.sl": "tr.1", "de.bl1": "de.1", "be.1": "be.1"}[league]
        if season == "2026-2027" and league != "de.bl1":
            return None
        base = f"https://raw.githubusercontent.com/openfootball/football.json/{DOMESTIC_REV}"
        return Source(league, season, "openfootball", "football.json", f"{base}/{short}/{filename}.json",
                      "CC0-1.0", f"{base}/LICENSE.md", DOMESTIC_REV)
    return None


@dataclass
class DownloadBudget:
    remaining_attempts: int = 15
    remaining_bytes: int = 12 * 1024 * 1024


def download(source: Source, client: httpx.Client, budget: DownloadBudget) -> bytes:
    for attempt in range(3):
        if budget.remaining_attempts <= 0:
            raise ValueError("DOWNLOAD_QUOTA_EXCEEDED")
        budget.remaining_attempts -= 1
        try:
            with client.stream("GET", source.url, timeout=httpx.Timeout(12, connect=4),
                               follow_redirects=False, headers={"User-Agent": "MacAnaliz/1.0 (+https://github.com/donozoyout-ux/Ma-analiz)"}) as response:
                if response.status_code == 429:
                    raise ValueError("SOURCE_RATE_LIMITED")
                if response.status_code not in (502, 503, 504):
                    response.raise_for_status()
                else:
                    raise httpx.ReadError("Temporary source error")
                data = bytearray()
                for chunk in response.iter_bytes(64 * 1024):
                    budget.remaining_bytes -= len(chunk)
                    if budget.remaining_bytes < 0 or len(data) + len(chunk) > MAX_BYTES:
                        raise ValueError("DOWNLOAD_SIZE_LIMIT")
                    data.extend(chunk)
                return bytes(data)
        except httpx.TransportError:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("Unreachable")


def _name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def fixture(source: Source, home: str, away: str, day: date, kickoff: datetime | None,
            stage: str, round_name: str, scores: dict, native_id: str | None = None,
            status: str | None = None) -> dict:
    if not home.strip() or not away.strip() or _name(home) == _name(away):
        raise ValueError("INVALID_TEAMS")
    if not date(int(source.season[:4]), 7, 1) <= day < date(int(source.season[-4:]), 7, 1):
        raise ValueError("FIXTURE_OUTSIDE_SEASON")
    identity = native_id or "|".join((round_name if stage == "DOMESTIC_LEAGUE" else stage, _name(home), _name(away)))
    prefix = f"ma-analiz/season/{source.league}/{source.season}"
    return {
        "id": str(uuid5(NAMESPACE_URL, f"{prefix}/{source.provider}/{identity}")),
        "league": source.league, "season": source.season, "provider": source.provider,
        "source_match_id": identity, "home_team": home, "away_team": away,
        "home_team_id": str(uuid5(NAMESPACE_URL, f"ma-analiz/team/{source.provider}/{_name(home)}")),
        "away_team_id": str(uuid5(NAMESPACE_URL, f"ma-analiz/team/{source.provider}/{_name(away)}")),
        "date": day.isoformat(), "kickoff_utc": kickoff.isoformat() if kickoff else None,
        "stage": stage, "round": round_name, "tie_id": None, "leg": None,
        "status": status or ("FINISHED" if scores.get("regulation") is not None else "SCHEDULED"),
        "half_time": scores.get("half_time"), "regulation": scores.get("regulation"),
        "extra_time": scores.get("extra_time"), "penalties": scores.get("penalties"),
        "verification": scores.get("verification", "SOURCE_REPORTED"), "odds_status": "ODDS_UNAVAILABLE",
        "licence": source.licence, "licence_url": source.licence_url, "source_url": source.url,
    }


def _pair(value: str | None) -> list[int] | None:
    return [int(n) for n in value.split("-")] if value else None


def parse_football_txt(source: Source, raw: bytes) -> list[dict]:
    day = kickoff_time = round_name = None
    rows = []
    for line in raw.decode("utf-8-sig").splitlines():
        line = line.strip()
        if line.startswith("▪"):
            round_name = line[1:].strip()
            day = kickoff_time = None
        elif header := DAY.fullmatch(line):
            month, number, year = header.groups()
            month_number = MONTH[month]
            day = date(int(year or (source.season[:4] if month_number >= 7 else source.season[-4:])),
                       month_number, int(number))
            kickoff_time = None
        elif " v " in line and not line.startswith(("#", "=")):
            match = FIXTURE.fullmatch(line)
            if match is None or day is None or round_name is None:
                raise ValueError("UNRECOGNIZED_FIXTURE")
            clock, home, away, score_text = match.groups()
            if clock:
                kickoff_time = datetime.strptime(clock, "%H:%M").time()
            kickoff = datetime.combine(day, kickoff_time, CET).astimezone(timezone.utc) if kickoff_time else None
            scores = {}
            if score_text:
                score = SCORE.fullmatch(score_text)
                if not score or (score["pen"] and not score["extra"]) or (score["extra"] and not score["first"]):
                    raise ValueError(f"UNRECOGNIZED_SCORE: {score_text}")
                scores = {
                    "regulation": _pair(score["first"] if score["extra"] else score["final"]),
                    "half_time": _pair(score["half"] if score["extra"] else score["first"]),
                    "extra_time": _pair(score["final"]) if score["extra"] else None,
                    "penalties": _pair(score["pen"]),
                }
            stage = ("LEAGUE_PHASE" if round_name.startswith("League") else
                     round_name.removeprefix("Finals, ").split(",")[0].upper().replace(" ", "_"))
            rows.append(fixture(source, home, away, day, kickoff, stage, round_name, scores))
    count = re.search(rb"(?m)^# Matches\s+(\d+)", raw)
    if not count or int(count[1]) != len(rows):
        raise ValueError("SOURCE_ROW_COUNT_MISMATCH")
    return rows


def parse_season(source: Source, raw: bytes) -> list[dict]:
    if source.format == "football.txt":
        rows = parse_football_txt(source, raw)
    elif source.format == "openligadb":
        payload = json.loads(raw)
        if not isinstance(payload, list):
            raise ValueError("INVALID_SEASON_FILE")
        rows = []
        for match in payload:
            round_name = match["group"]["groupName"]
            stage = "LEAGUE_PHASE" if "spieltag" in round_name.lower() else {
                "Achtelfinale": "ROUND_OF_16", "Endspiel": "FINAL",
            }.get(round_name)
            if int(match["leagueSeason"]) != int(source.season[:4]) or stage is None:
                raise ValueError("UNREVIEWED_OPENLIGADB_SCOPE")
            kickoff = datetime.fromisoformat(match["matchDateTimeUTC"].replace("Z", "+00:00"))
            if kickoff.utcoffset() is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)  # field explicitly names UTC
            kickoff = kickoff.astimezone(timezone.utc)
            scores = {}
            if match["matchIsFinished"] is True:
                for result_type, field in ((1, "half_time"), (2, "regulation")):
                    if field == "regulation" and stage != "LEAGUE_PHASE":
                        # This source does not label the 90-minute score separately in knockout fixtures.
                        scores["verification"] = "REGULATION_UNCONFIRMED"
                        continue
                    candidates = {(item["pointsTeam1"], item["pointsTeam2"])
                                  for item in match.get("matchResults", []) if item["resultTypeID"] == result_type}
                    if len(candidates) == 1 and all(type(n) is int and n >= 0 for n in next(iter(candidates))):
                        scores[field] = list(next(iter(candidates)))
                    elif candidates:
                        scores["verification"] = "SOURCE_CONFLICT"
            status = "FINISHED" if match["matchIsFinished"] else "SCHEDULED"
            rows.append(fixture(source, match["team1"]["teamName"], match["team2"]["teamName"],
                                kickoff.astimezone(CET).date(), kickoff, stage, round_name,
                                scores, str(match["matchID"]), status))
    else:
        rows = []
        for match in json.loads(raw)["matches"]:
            day = date.fromisoformat(match["date"])
            scores = match.get("score") or {}
            # Bare arrays have no named result period.
            if not isinstance(scores, dict):
                scores = {}
            rows.append(fixture(source, match["team1"], match["team2"], day, None, "DOMESTIC_LEAGUE",
                                match["round"], {"half_time": scores.get("ht"), "regulation": scores.get("ft")}))
    ties: dict[tuple, list[dict]] = {}
    for row in rows:
        if row["stage"] not in {"LEAGUE_PHASE", "FINAL", "DOMESTIC_LEAGUE"}:
            teams = tuple(sorted((row["home_team_id"], row["away_team_id"])))
            ties.setdefault((row["stage"], *teams), []).append(row)
    for key, legs in ties.items():
        if len(legs) > 2 or (len(legs) == 2 and legs[0]["home_team_id"] == legs[1]["home_team_id"]):
            raise ValueError("AMBIGUOUS_KNOCKOUT_TIE")
        tie_id = str(uuid5(NAMESPACE_URL, "/".join((source.league, source.season, *key))))
        for leg, row in enumerate(sorted(legs, key=lambda r: r["date"]), 1):
            row.update(tie_id=tie_id, leg=leg)
    if not rows or len(rows) > 1000 or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("INVALID_OR_DUPLICATE_SEASON_ROWS")
    return rows


def summarize_source(source: Source, raw: bytes, rows: list[dict]) -> dict:
    return {
        "league": source.league, "season": source.season,
        "status": "PARTIAL" if source.league == "uefa.el" and source.season == "2025-2026" else "AVAILABLE",
        "source": source.url, "licence": source.licence,
        "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
        "fixtures": len(rows), "regulation_scores": sum(r["regulation"] is not None for r in rows),
        "source_conflicts": sum(r["verification"] == "SOURCE_CONFLICT" for r in rows),
        "finished_reported": sum(r["status"] == "FINISHED" for r in rows),
        "half_time_scores": sum(r["half_time"] is not None for r in rows),
        "kickoffs_utc": sum(r["kickoff_utc"] is not None for r in rows),
        "extra_time": sum(r["extra_time"] is not None for r in rows),
        "penalties": sum(r["penalties"] is not None for r in rows),
        "odds": 0, "odds_status": "ODDS_UNAVAILABLE",
    }
