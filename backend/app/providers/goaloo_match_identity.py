"""Fail-closed Bundesliga cross-provider matching (Goaloo -> OpenLigaDB).

No writes or fuzzy/date-only automatic joins. One-to-one matches require
two reviewed team identities, exact ordered home/away, league/season and
UTC kickoff within 30 minutes, with no competing candidate.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import re
import unicodedata

_GOALOO_TZ = timezone(timedelta(hours=8))
# Reviewed exact source labels, not substring or fuzzy matching.
_ALIASES = {
    "bayern munchen": "bayern",
    "fc bayern munchen": "bayern",
    "borussia dortmund": "dortmund",
    "hamburger sv": "hamburg",
    "hamburg sv": "hamburg",
    "vfb stuttgart": "stuttgart",
    "vfl wolfsburg": "wolfsburg",
    "eintracht frankfurt": "frankfurt",
    "bayer leverkusen": "leverkusen",
    "rb leipzig": "leipzig",
    "sc freiburg": "freiburg",
    "sport club freiburg": "freiburg",
    "fsv mainz 05": "mainz",
    "1 fsv mainz 05": "mainz",
    "fc koln": "koln",
    "1 fc koln": "koln",
    "fc augsburg": "augsburg",
    "fc union berlin": "union berlin",
    "1 fc union berlin": "union berlin",
    "union berlin": "union berlin",
    "werder bremen": "bremen",
    "sv werder bremen": "bremen",
    "borussia monchengladbach": "monchengladbach",
    "sc paderborn 07": "paderborn",
    "schalke 04": "schalke",
    "fc schalke 04": "schalke",
    "tsg hoffenheim": "hoffenheim",
    "tsg 1899 hoffenheim": "hoffenheim",
    "sv elversberg": "elversberg",
    "sv 07 elversberg": "elversberg",
    "1 fc heidenheim": "heidenheim",
    "vfl bochum": "bochum",
}


def _label(raw: str) -> str:
    value = unicodedata.normalize("NFKD", str(raw)).casefold()
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("ß", "ss").replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _team(raw: str) -> str | None:
    # Don't silently equate unreviewed names or substring aliases.
    return _ALIASES.get(_label(raw))


def _utc(raw: str, *, goaloo: bool) -> datetime | None:
    try:
        if goaloo:
            parsed = datetime.strptime(str(raw), "%Y-%m-%d %H:%M").replace(tzinfo=_GOALOO_TZ)
        else:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class MappingDecision:
    goaloo_id: str
    openligadb_id: str | None
    status: str
    reason: str
    evidence: dict


def map_bundesliga(openliga: list[dict], goaloo: dict) -> list[MappingDecision]:
    """Resolve unique one-to-one fixture pairs; review rather than guessing."""
    league = goaloo.get("LeagueInfo") or []
    if len(league) < 3 or str(league[0]) != "8" or str(league[2]) != "2026-2027":
        raise ValueError("UNREVIEWED_LEAGUE_OR_SEASON")
    teams = {str(row[0]): str(row[1]) for row in goaloo.get("TeamInfo", [])
             if isinstance(row, list) and len(row) >= 2}
    open_matches = []
    for row in openliga:
        if not isinstance(row, dict):
            continue
        kickoff = _utc(row.get("matchDateTimeUTC"), goaloo=False)
        home = _team((row.get("team1") or {}).get("teamName", ""))
        away = _team((row.get("team2") or {}).get("teamName", ""))
        if kickoff and home and away and home != away and row.get("matchID") is not None:
            open_matches.append((str(row["matchID"]), kickoff, home, away))
    goals = {}
    for round_rows in (goaloo.get("ScheduleList") or {}).values():
        if not isinstance(round_rows, list):
            continue
        for row in round_rows:
            if isinstance(row, list) and len(row) >= 8:
                identifier = str(row[0])
                if identifier in goals and goals[identifier] != row:
                    # Conflicting source observations should never be auto-matched.
                    goals[identifier] = None
                elif identifier not in goals:
                    goals[identifier] = row
    tentative = {}
    for identifier, row in goals.items():
        if row is None:
            tentative[identifier] = ([], "CONFLICTING_GOALOO_FIXTURES")
            continue
        kickoff = _utc(row[3], goaloo=True)
        home, away = _team(teams.get(str(row[4]), "")), _team(teams.get(str(row[5]), ""))
        if not kickoff or not home or not away or home == away:
            tentative[identifier] = ([], "UNKNOWN_TEAM_OR_TIME")
            continue
        candidates = [(match_id, abs(int((kickoff - at).total_seconds())))
                      for match_id, at, h, a in open_matches
                      if h == home and a == away and abs((kickoff-at).total_seconds()) <= 1800]
        tentative[identifier] = (candidates, "EXACT_TEAMS_AND_TIME" if len(candidates) == 1 else "NO_UNIQUE_EXACT_MATCH")
    counts = Counter(candidates[0][0] for candidates, _ in tentative.values() if len(candidates) == 1)
    output = []
    for identifier, (candidates, reason) in tentative.items():
        if len(candidates) == 1 and counts[candidates[0][0]] == 1:
            match_id, delta = candidates[0]
            output.append(MappingDecision(identifier, match_id, "MATCHED",
                                          "EXACT_TEAMS_AND_TIME", {"kickoff_delta_seconds": delta,
                                          "league_id": "de.bl1", "season": "2026-2027"}))
        else:
            status = "NEEDS_REVIEW" if candidates or reason == "CONFLICTING_GOALOO_FIXTURES" else "NO_MATCH"
            output.append(MappingDecision(identifier, None, status,
                                          "DUPLICATE_TARGET" if len(candidates)==1 else reason,
                                          {"candidate_count": len(candidates), "league_id":"de.bl1"}))
    return output


def summary(decisions: list[MappingDecision]) -> dict[str, int]:
    counts = Counter(row.status for row in decisions)
    return {key: counts[key] for key in ("MATCHED", "NEEDS_REVIEW", "NO_MATCH")}
