"""Single-day observations. A reported score is distinct from a confirmed final."""
from datetime import date, datetime, timezone
from hashlib import sha256
from zoneinfo import ZoneInfo
import json
from app.providers.goaloo import GoalooProvider

ISTANBUL=ZoneInfo("Europe/Istanbul")


def utc(value):
    try:
        parsed=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError,TypeError):return None


def score(home,away):
    return [home,away] if all(isinstance(v,int) and not isinstance(v,bool) and v>=0 for v in (home,away)) else None


def parse_openligadb(payload,day: date,*,observed_at: datetime,source_url:str):
    if not isinstance(payload,list):raise ValueError("OPENLIGADB_SCHEMA_INVALID")
    output=[]
    for raw in payload:
        if not isinstance(raw,dict):raise ValueError("OPENLIGADB_ROW_INVALID")
        kickoff=utc(raw.get("matchDateTimeUTC"))
        if kickoff is None:raise ValueError("UTC_KICKOFF_MISSING")
        if kickoff.astimezone(ISTANBUL).date()!=day:continue
        if raw.get("leagueShortcut")!="bl1":raise ValueError("LEAGUE_MISMATCH")
        results=[s for s in raw.get("matchResults",[]) if s.get("resultTypeID")==2]
        reported=[score(s.get("pointsTeam1"),s.get("pointsTeam2")) for s in results]
        valid=[v for v in reported if v is not None]
        unique={tuple(v) for v in valid}
        issues=[]
        if len(unique)>1:issues.append("CONFLICTING_FINAL_SCORE_FIELDS")
        if any(v is None for v in reported):issues.append("FINAL_SCORE_FIELD_INVALID")
        if raw.get("matchID") is None:issues.append("SOURCE_MATCH_ID_MISSING")
        if kickoff>=observed_at:issues.append("KICKOFF_NOT_IN_PAST")
        finished=raw.get("matchIsFinished") is True
        if not finished:issues.append("SOURCE_NOT_FINISHED")
        if not valid:issues.append("FULL_TIME_SCORE_MISSING")
        full=valid[0] if valid and len(unique)==1 else None
        home,away=raw.get("team1",{}),raw.get("team2",{})
        if not home.get("teamName") or not away.get("teamName") or home.get("teamId") is None or away.get("teamId") is None:
            issues.append("TEAM_IDENTITY_MISSING")
        output.append({"provider":"openligadb","source_match_id":str(raw.get("matchID")),
            "league":"de.bl1","home_team":home.get("teamName"),"away_team":away.get("teamName"),
            "home_source_id":home.get("teamId"),"away_source_id":away.get("teamId"),
            "start_time":kickoff.isoformat(),"source_status":"FINISHED" if finished else "NOT_FINISHED",
            "reported_score":full,"final_confirmed":finished and full is not None and not issues,
            "issues":issues,"source_url":source_url,"observed_at":observed_at.isoformat(),
            "source_updated_at_raw":raw.get("lastUpdateDateTime"),"licence":"ODbL-1.0",
            "attribution":"Data: OpenLigaDB (https://www.openligadb.de), ODbL 1.0"})
    return output


def parse_openfootball(payload,day:date,*,observed_at:datetime,source_url:str):
    if not isinstance(payload,dict) or not isinstance(payload.get("matches"),list):raise ValueError("OPENFOOTBALL_SCHEMA_INVALID")
    output=[]
    for raw in payload["matches"]:
        if raw.get("date")!=day.isoformat():continue
        ft=(raw.get("score") or {}).get("ft")
        reported=score(*ft) if isinstance(ft,list) and len(ft)==2 else None
        kickoff=utc(raw.get("utcDate"))  # Never guess the timezone of a bare HH:MM.
        fingerprint=sha256(json.dumps([raw.get("date"),raw.get("team1"),raw.get("team2"),raw.get("time")],ensure_ascii=True).encode()).hexdigest()
        output.append({"provider":"openfootball","source_match_id":None,"record_fingerprint":fingerprint,
            "identity_kind":"CONTENT_FINGERPRINT_NOT_SOURCE_ID","league":"de.bl1",
            "home_team":raw.get("team1"),"away_team":raw.get("team2"),
            "start_time":kickoff.isoformat() if kickoff else None,"source_date":raw.get("date"),"source_time_raw":raw.get("time"),
            "source_status":"FULL_TIME_SCORE_REPORTED" if reported else "FIXTURE_ONLY",
            "reported_score":reported,"final_confirmed":False,
            "issues":["NO_EXPLICIT_FINISHED_FLAG"]+([] if kickoff else ["KICKOFF_TIMEZONE_NOT_DECLARED"]),
            "source_url":source_url,"observed_at":observed_at.isoformat(),"licence":"CC0-1.0"})
    return output


def parse_goaloo(payload,day:date,season:str,*,observed_at:datetime,source_url:str):
    # Reuse the existing decoder against bytes already fetched under robots control.
    provider=GoalooProvider(league_ids=("8",),season_ids=(season,))
    try:
        provider._schedule_cache[("8",season)]=payload
        matches=provider.get_matches_on_day(day,"8",season)
    finally:
        provider.close()
    output=[]
    outcomes={r["provider_match_id"]:r for r in provider._results(matches)}
    for match in matches:
        result=outcomes.get(match["provider_match_id"])
        full=score(result["home_score"],result["away_score"]) if result else None
        output.append({"provider":"goaloo","source_match_id":match["provider_match_id"],"league":"de.bl1",
            "home_team":match["home_team"],"away_team":match["away_team"],"start_time":match["start_time"].isoformat(),
            "source_status":match["status"],"reported_score":full,
            "final_confirmed":match["status"]=="FINISHED" and full is not None and match["start_time"]<observed_at,
            "issues":[] if result else ["SOURCE_FINAL_RESULT_UNAVAILABLE"],"source_url":source_url,
            "observed_at":observed_at.isoformat(),"licence":"REDISTRIBUTION_NOT_VERIFIED"})
    return output
