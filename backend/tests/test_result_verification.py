"""All fixtures below are offline and never appear in the live evidence report."""
from copy import deepcopy
from datetime import date,datetime,timezone,timedelta
import httpx
import pytest
from app.providers.result_sources import parse_openligadb,parse_openfootball
from app.analysis.result_verification import assess_result,assess_technical_statistics
from app.providers.evidence_http import EvidenceHTTP,EvidenceFailure

DAY=date(2026,11,20)
AT=datetime(2026,11,20,21,30,tzinfo=timezone.utc)
URL="https://api.openligadb.de/getmatchdata/bl1/2026/10"


def raw_result(**changes):
 return {"matchID":83237,"leagueShortcut":"bl1","matchDateTimeUTC":"2026-11-20T19:30:00Z",
  "team1":{"teamName":"FC Bayern München","teamId":40},"team2":{"teamName":"1. FC Köln","teamId":65},
  "matchIsFinished":True,"matchResults":[{"resultTypeID":1,"pointsTeam1":0,"pointsTeam2":1},
  {"resultTypeID":2,"pointsTeam1":1,"pointsTeam2":1}],**changes}


def chain():
 peer=parse_openligadb([raw_result()],DAY,observed_at=AT,source_url=URL)[0]
 goal={**peer,"provider":"goaloo","source_match_id":"3018869","home_team":"Bayern Munchen","away_team":"FC Koln",
       "licence":"REDISTRIBUTION_NOT_VERIFIED"}
 return [goal,peer]


def decision(rows=None):return assess_result(chain() if rows is None else rows,goaloo_match_id="3018869")


def test_explicit_finished_flag_and_final_type_are_required_not_elapsed_time_or_score():
 for flag in (False,"true",1,None):
  row=parse_openligadb([raw_result(matchIsFinished=flag)],DAY,observed_at=AT,source_url=URL)[0]
  assert not row["final_confirmed"] and row["reported_score"]==[1,1]
 row=parse_openligadb([raw_result()],DAY,observed_at=AT,source_url=URL)[0]
 assert row["final_confirmed"] and row["reported_score"]==[1,1]
 # Missing full-time entry cannot be replaced by the halftime score.
 row=parse_openligadb([raw_result(matchResults=[{"resultTypeID":1,"pointsTeam1":3,"pointsTeam2":2}])],DAY,observed_at=AT,source_url=URL)[0]
 assert row["reported_score"] is None and not row["final_confirmed"]


@pytest.mark.parametrize("bad",[True,-1,1.5,None])
def test_invalid_score_never_becomes_final(bad):
 r=raw_result(matchResults=[{"resultTypeID":2,"pointsTeam1":bad,"pointsTeam2":0}])
 assert not parse_openligadb([r],DAY,observed_at=AT,source_url=URL)[0]["final_confirmed"]


def test_missing_identity_or_conflicting_scores_never_become_final():
 for r in [raw_result(matchID=None),raw_result(team1={}),raw_result(matchResults=[
  {"resultTypeID":2,"pointsTeam1":1,"pointsTeam2":1},{"resultTypeID":2,"pointsTeam1":2,"pointsTeam2":1}])]:
  assert not parse_openligadb([r],DAY,observed_at=AT,source_url=URL)[0]["final_confirmed"]


def test_scope_utc_and_future_kickoff_are_checked():
 with pytest.raises(ValueError,match="UTC_KICKOFF_MISSING"):
  parse_openligadb([raw_result(matchDateTimeUTC="2026-11-20T19:30:00")],DAY,observed_at=AT,source_url=URL)
 assert parse_openligadb([raw_result(matchDateTimeUTC="2026-11-20T22:30:00Z")],DAY,observed_at=AT,source_url=URL)==[]
 assert not parse_openligadb([raw_result()],DAY,observed_at=AT-timedelta(hours=3),source_url=URL)[0]["final_confirmed"]


def test_openfootball_full_time_score_alone_is_not_a_finished_flag_or_timezone_proof():
 rows=parse_openfootball({"matches":[{"date":"2026-11-20","time":"20:30","team1":"H","team2":"A","score":{"ft":[1,1]}}]},DAY,observed_at=AT,source_url="https://source.test")
 assert rows[0]["reported_score"]==[1,1] and not rows[0]["final_confirmed"]
 assert rows[0]["start_time"] is None and rows[0]["source_match_id"] is None


def test_two_sources_with_reviewed_exact_identity_and_score_form_a_read_only_final_chain():
 d=decision()
 assert d["status"]=="RESULT_VERIFIED" and d["final_result"]["home_score"]==1
 assert d["final_result"]["primary_provider"]=="openligadb"
 assert not d["writes_allowed"] and not d["persistence_ready"]
 # A lagging repository with no score/timezone is a warning, not a contradictory final.
 incomplete={**chain()[1],"provider":"openfootball","source_match_id":None,"start_time":None,
             "final_confirmed":False,"source_status":"FIXTURE_ONLY","reported_score":None,
             "issues":["NO_EXPLICIT_FINISHED_FLAG","KICKOFF_TIMEZONE_NOT_DECLARED"]}
 d=decision(chain()+[incomplete])
 assert d["status"]=="RESULT_VERIFIED" and d["warnings"]


def test_conflicting_score_or_kickoff_is_quarantined():
 for change in ({"reported_score":[2,1]},{"start_time":"2026-11-20T19:31:00Z"}):
  rows=chain();rows[1].update(change)
  d=decision(rows)
  assert d["status"]=="CONFLICT" and d["final_result"] is None


def test_unfinished_peer_missing_corroborator_and_future_metadata_cannot_pass():
 for rows in [chain()[:1],[{**chain()[0],"source_status":"SCHEDULED","final_confirmed":False},chain()[1]],
              [chain()[0],{**chain()[1],"observed_at":"2026-11-20T18:00:00Z"}]]:
  assert decision(rows)["final_result"] is None


def test_reviewed_alias_does_not_apply_to_wrong_team_id_fixture_or_reversed_teams():
 for change in ({"home_source_id":41},{"source_match_id":"83238"},{"home_team":"1. FC Köln","away_team":"FC Bayern München"},
                {"away_team":"1. FC Köln II"}):
  rows=chain();rows[1].update(change)
  assert decision(rows)["final_result"] is None
 rows=chain();rows[0]["source_match_id"]="different-goaloo-id"
 assert assess_result(rows,goaloo_match_id="different-goaloo-id")["final_result"] is None


def statistic(**changes):
 return {"provider_match_id":"3018869","source":"goaloo","source_url":"https://www.goaloo.com/football/match/live-3018869",
         "observed_at":(AT+timedelta(minutes=1)).isoformat(),"period":"ftstat","name":"Shots","unit":"count",
         "home_value":10,"away_value":4,**changes}


def check(stat,**kwargs):
 return assess_technical_statistics([stat],decision(),source_match_id="3018869",observed_at=AT+timedelta(minutes=2),**kwargs)[0]


def test_technical_values_require_permission_final_time_identity_period_and_reviewed_unit():
 assert check(statistic(),permission_confirmed=True)["usable"]
 assert not check(statistic())["usable"]
 changes=[{"observed_at":(AT-timedelta(minutes=20)).isoformat()},{"period":"htstat"},
          {"unit":"source_unspecified"},{"source_url":None},{"provider_match_id":"other"},
          {"home_value":None},{"home_value":True},{"home_value":float("nan")},{"home_value":1.5},
          {"unit":"percent","home_value":101},{"name":"Unreviewed metric"}]
 for change in changes:assert not check(statistic(**change),permission_confirmed=True)["usable"]


def test_robots_denied_urls_and_redirects_are_not_followed():
 sent=[]
 def handler(req):
  sent.append(req.url.path)
  if req.url.path=="/robots.txt":return httpx.Response(200,text="User-agent: *\nDisallow: /blocked",request=req)
  return httpx.Response(302,headers={"Location":"https://other.invalid/"},request=req)
 with httpx.Client(transport=httpx.MockTransport(handler),follow_redirects=True) as client:
  http=EvidenceHTTP(client=client,sleep=lambda _:None)
  with pytest.raises(EvidenceFailure,match="ROBOTS_DISALLOWED"):http.get("https://api.openligadb.de/blocked")
  with pytest.raises(EvidenceFailure,match="ACCESS_STOP_HTTP_302"):http.get("https://api.openligadb.de/allowed")
 assert sent==["/robots.txt","/allowed"]


def test_evidence_cache_limits_requests_and_429_is_not_retried():
 sent=[]
 def handler(req):
  sent.append(req.url.path)
  return httpx.Response(404 if req.url.path=="/robots.txt" else 429,request=req)
 with httpx.Client(transport=httpx.MockTransport(handler)) as client:
  http=EvidenceHTTP(client=client,sleep=lambda _:None)
  with pytest.raises(EvidenceFailure,match="ACCESS_STOP_HTTP_429"):http.get("https://api.openligadb.de/data")
  assert len(sent)==2
  with pytest.raises(EvidenceFailure,match="UNAPPROVED_ORIGIN"):http.get("https://unapproved.invalid/data")


def test_cli_cannot_expand_to_multiple_days_or_persist(capsys):
 from app.jobs.result_probe import main
 with pytest.raises(SystemExit):main(["--date","2026-11-20","--matchday","10","--goaloo-match-id","3018869","--persist"])



def test_successful_evidence_is_cached_and_openligadb_data_calls_are_spaced():
 sent=[];waits=[]
 def handler(req):
  sent.append(req.url.path)
  return httpx.Response(404,request=req) if req.url.path=="/robots.txt" else httpx.Response(200,json={},request=req)
 with httpx.Client(transport=httpx.MockTransport(handler)) as client:
  http=EvidenceHTTP(client=client,sleep=waits.append,clock=lambda:0)
  http.json("https://api.openligadb.de/data/1")
  http.json("https://api.openligadb.de/data/1")
  http.json("https://api.openligadb.de/data/2")
 assert sent==["/robots.txt","/data/1","/data/2"] and 60 in waits
