"""Offline parser for manually obtained, appropriately licensed football-data.co.uk CSVs.

Strictly extracts corners and yellow/red cards; no HTTP, odds, database or R2.
Rows are source-reported observations, NOT independently verified match results.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from io import StringIO
from pathlib import Path

SOURCE = "football-data.co.uk"
SOURCE_ROOT = "https://www.football-data.co.uk/mmz4281"
MAX_CSV_BYTES = 3_000_000
MAX_ROWS = 500
SEASONS = {"2425": "2024-2025", "2526": "2025-2026", "2627": "2026-2027"}
LEAGUES = {"E0": ("en.pl", "Premier League"), "D1": ("de.bl1", "Bundesliga"), "SP1": ("es.ll", "La Liga")}
METRICS = ("HC", "AC", "HY", "AY", "HR", "AR")
REQUIRED = {"Div", "Date", "HomeTeam", "AwayTeam", *METRICS}


@dataclass(frozen=True)
class DisciplineRow:
    source_key: str
    league_id: str
    league_name: str
    season: str
    match_date: date
    home_team: str
    away_team: str
    home_corners: int | None
    away_corners: int | None
    home_yellows: int | None
    away_yellows: int | None
    home_reds: int | None
    away_reds: int | None


def _date(raw: str, season: str) -> date:
    parsed = None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw.strip(), fmt).date()
            break
        except ValueError:
            continue
    if parsed is None:
        raise ValueError("INVALID_MATCH_DATE")
    year = int(season[:4])
    if not date(year, 7, 1) <= parsed <= date(year + 1, 6, 30):
        raise ValueError("MATCH_OUTSIDE_SEASON")
    return parsed


def _count(raw: str | None) -> int | None:
    if raw is None or not raw.strip():
        return None
    s = raw.strip()
    if not s.isascii() or not s.isdecimal() or len(s) > 3:
        raise ValueError("INVALID_STAT_COUNT")
    number = int(s)
    if number > 100:
        raise ValueError("STAT_COUNT_OUT_OF_RANGE")
    return number


def parse_season(data: bytes, *, league: str, season_code: str) -> list[DisciplineRow]:
    if league not in LEAGUES or season_code not in SEASONS:
        raise ValueError("UNSUPPORTED_LEAGUE_OR_SEASON")
    if not data or len(data) > MAX_CSV_BYTES:
        raise ValueError("CSV_EMPTY_OR_TOO_LARGE")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV_ENCODING_UNSUPPORTED") from exc
    reader = csv.DictReader(StringIO(text, newline=""))
    if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)) or not REQUIRED.issubset(reader.fieldnames):
        raise ValueError("CSV_REQUIRED_COLUMNS_MISSING_OR_DUPLICATED")
    results: list[DisciplineRow] = []
    keys: set[str] = set()
    for index, raw in enumerate(reader):
        if index >= MAX_ROWS:
            raise ValueError("CSV_ROW_LIMIT_EXCEEDED")
        if None in raw:
            raise ValueError("CSV_EXTRA_COLUMNS")
        if not raw.get("Date") and not raw.get("HomeTeam") and not raw.get("AwayTeam"):
            continue
        if raw.get("Div") != league:
            raise ValueError("CSV_LEAGUE_MISMATCH")
        home, away = (raw.get("HomeTeam") or "").strip(), (raw.get("AwayTeam") or "").strip()
        if not home or not away or home.casefold() == away.casefold() or len(home) > 150 or len(away) > 150:
            raise ValueError("INVALID_TEAM_IDENTITY")
        day = _date(raw.get("Date") or "", SEASONS[season_code])
        stats = tuple(_count(raw.get(key)) for key in METRICS)
        if all(s is None for s in stats):
            continue
        identity = f"{SOURCE}|{league}|{SEASONS[season_code]}|{day.isoformat()}|{home.casefold()}|{away.casefold()}"
        source_key = sha256(identity.encode()).hexdigest()
        if source_key in keys:
            raise ValueError("CSV_DUPLICATE_MATCH_IDENTITY")
        keys.add(source_key)
        results.append(DisciplineRow(source_key, LEAGUES[league][0], LEAGUES[league][1],
                                      SEASONS[season_code], day, home, away, *stats))
    return results


def preview(path: str | Path, *, league: str, season_code: str) -> dict:
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_CSV_BYTES:
        raise ValueError("CSV_FILE_UNAVAILABLE_OR_TOO_LARGE")
    data = path.read_bytes()
    rows = parse_season(data, league=league, season_code=season_code)
    return {
        "source": SOURCE,
        "source_url": f"{SOURCE_ROOT}/{season_code}/{league}.csv",
        "league": LEAGUES[league][0],
        "season": SEASONS[season_code],
        "file_sha256": sha256(data).hexdigest(),
        "matches_with_any_stat": len(rows),
        "complete_corners": sum(r.home_corners is not None and r.away_corners is not None for r in rows),
        "complete_yellows": sum(r.home_yellows is not None and r.away_yellows is not None for r in rows),
        "complete_reds": sum(r.home_reds is not None and r.away_reds is not None for r in rows),
        "odds_imported": 0,
        "independent_verification": False,
        "rows": rows,
    }
