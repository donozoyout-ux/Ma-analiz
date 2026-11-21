"""Stage 7A evidence adapters only. These are deliberately not ingestion providers."""
from datetime import date, datetime, timezone
from hashlib import sha256
import json

from app.providers.result_sources import parse_openfootball, score, utc

# Reviewed resource identities; never select a community league by fuzzy name.
PILOT = {
    "tr.sl": {"date": "2026-11-21", "file": "tr.1", "name": "Türkiye Süper Lig",
              "openfootball_name": "Turkish Süper Lig 2026/27", "oldb": []},
    "en.pl": {"date": "2026-11-21", "file": "en.1", "name": "İngiltere Premier League",
              "openfootball_name": "English Premier League 2026/27",
              "oldb": [("pl", 5996, 11), ("pl1", 5993, 11)]},
    "de.bl1": {"date": "2026-11-20", "file": "de.1", "name": "Almanya Bundesliga",
               "openfootball_name": "Deutsche Bundesliga 2026/27", "oldb": [("bl1", 4937, 10)]},
}


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_clock(observed_at):
    if not isinstance(observed_at, datetime) or observed_at.utcoffset() is None:
        raise ValueError("AWARE_OBSERVATION_REQUIRED")
    return observed_at.astimezone(timezone.utc)


def openfootball(payload, league, *, observed_at, source_url, scope=None):
    validate_clock(observed_at)
    scope = PILOT[league] if scope is None else scope
    if not isinstance(payload, dict) or payload.get("name") != scope["openfootball_name"]:
        raise ValueError("LEAGUE_RESOURCE_MISMATCH")
    # Reuse the conservative score/time decoder, then explicitly scope its league.
    rows = parse_openfootball(payload, date.fromisoformat(scope["date"]),
                              observed_at=observed_at, source_url=source_url)
    for row in rows:
        if not all(isinstance(row.get(k), str) and row[k].strip() for k in ("home_team", "away_team")):
            raise ValueError("TEAM_NAME_MISSING")
        row["league"] = league
        row["day_basis"] = "SOURCE_DATE_TIMEZONE_UNKNOWN"
        if row["start_time"] and utc(row["start_time"]).date().isoformat() != scope["date"]:
            raise ValueError("SOURCE_DATE_UTC_CONFLICT")
        row["identity_scope"] = scope["file"]
        row["attribution"] = "OpenFootball football.json (CC0-1.0)"
        row["source_final_confirmed"] = False
    return rows


def openligadb(payload, league, shortcut, league_id, matchday, *, observed_at, source_url, scope=None):
    observed_at = validate_clock(observed_at)
    scope = PILOT[league] if scope is None else scope
    if (shortcut, league_id, matchday) not in scope["oldb"]:
        raise ValueError("UNREVIEWED_LEAGUE_RESOURCE")
    if not isinstance(payload, list):
        raise ValueError("SOURCE_SCHEMA_INVALID")
    rows = []
    for raw in payload:
        if (raw.get("leagueShortcut") != shortcut or raw.get("leagueId") != league_id
                or raw.get("leagueSeason") != 2026
                or raw.get("group", {}).get("groupOrderID") != matchday):
            raise ValueError("LEAGUE_RESOURCE_MISMATCH")
        kickoff = utc(raw.get("matchDateTimeUTC"))
        if kickoff is None:
            raise ValueError("UTC_KICKOFF_MISSING")
        if kickoff.date().isoformat() != scope["date"]:
            continue
        home, away = raw.get("team1", {}), raw.get("team2", {})
        ids = [raw.get("matchID"), home.get("teamId"), away.get("teamId")]
        if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in ids):
            raise ValueError("SOURCE_IDENTITY_MISSING")
        if home["teamId"] == away["teamId"] or not all(
                isinstance(t.get("teamName"), str) and t["teamName"].strip() for t in (home, away)):
            raise ValueError("TEAM_IDENTITY_INVALID")
        scores = [score(r.get("pointsTeam1"), r.get("pointsTeam2"))
                  for r in raw.get("matchResults", []) if r.get("resultTypeID") == 2]
        unique = {tuple(s) for s in scores if s is not None}
        issues = []
        if len(unique) > 1:
            issues.append("CONFLICTING_FINAL_SCORE_FIELDS")
        if any(s is None for s in scores):
            issues.append("INVALID_SCORE")
        finished = raw.get("matchIsFinished") is True
        full = list(next(iter(unique))) if len(unique) == 1 else None
        if finished and (full is None or kickoff >= observed_at):
            issues.append("FINAL_EVIDENCE_INVALID")
        # Even an explicit source final is not a reconciled production FINAL.
        rows.append({"provider": "openligadb", "source_match_id": str(raw["matchID"]),
                     "identity_scope": f"{shortcut}:2026", "league": league,
                     "source_league_id": league_id, "home_team": home["teamName"], "away_team": away["teamName"],
                     "home_source_id": home["teamId"], "away_source_id": away["teamId"],
                     "start_time": kickoff.isoformat(), "day_basis": "UTC",
                     "source_status": ("FINISHED" if finished else "NOT_FINISHED"
                                       if raw.get("matchIsFinished") is False else "UNKNOWN"),
                     "reported_score": full, "final_confirmed": False,
                     "source_final_confirmed": finished and full is not None and not issues,
                     "issues": issues, "source_updated_at_raw": raw.get("lastUpdateDateTime"),
                     "observed_at": observed_at.isoformat(), "source_url": source_url,
                     "licence": "ODbL-1.0", "attribution": "Data: OpenLigaDB (https://www.openligadb.de), ODbL 1.0"})
    return rows


def bounded(rows, limit):
    """Conflicting repeated native IDs invalidate the source before truncation."""
    unique = {}
    for row in rows:
        identity = (row["provider"], row["identity_scope"], row.get("source_match_id") or row["record_fingerprint"])
        if identity in unique and unique[identity] != row:
            raise ValueError("DUPLICATE_SOURCE_ID_CONFLICT")
        unique[identity] = row
    ordered = sorted(unique.values(), key=lambda r: (r.get("start_time") or "", r["home_team"], r["away_team"]))
    # Inspection IDs are not canonical match IDs and never authorize import.
    return [{**r, "observation_key": digest([r["provider"], r["identity_scope"],
                  r.get("source_match_id") or r["record_fingerprint"]]),
             "technical_statistics": "NOT_AVAILABLE", "corners": "NOT_AVAILABLE",
             "cards": "NOT_AVAILABLE", "odds": "NOT_AVAILABLE", "writes_allowed": False}
            for r in ordered[:limit]], len(ordered)


def compare(rows):
    """Exact ordered names only produce review candidates, never identity merges."""
    output = []
    for i, left in enumerate(rows):
        for right in rows[i + 1:]:
            if (left["provider"], left["identity_scope"]) == (right["provider"], right["identity_scope"]):
                continue
            if (left["league"], left["home_team"], left["away_team"]) != (
                    right["league"], right["home_team"], right["away_team"]):
                continue
            conflicts = [k for k in ("start_time", "reported_score")
                         if left.get(k) is not None and right.get(k) is not None and left[k] != right[k]]
            if (left["source_status"] == "FINISHED" and right["source_status"] == "NOT_FINISHED") or (
                    right["source_status"] == "FINISHED" and left["source_status"] == "NOT_FINISHED"):
                conflicts.append("source_status")
            output.append({"left": left["observation_key"], "right": right["observation_key"],
                           "status": "CONFLICT" if conflicts else "REVIEW_REQUIRED",
                           "conflicting_fields": conflicts, "mapping_accepted": False})
    return output
