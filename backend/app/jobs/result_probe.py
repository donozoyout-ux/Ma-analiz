"""Stage 2: explicit single Bundesliga day/target, reusable-source result evidence only."""
import argparse
import json
from datetime import date,datetime,timezone
from app.jobs.scraping_probe import season_for
from app.providers.evidence_http import EvidenceHTTP,EvidenceFailure
from app.providers.result_sources import parse_goaloo,parse_openfootball,parse_openligadb
from app.analysis.result_verification import assess_result,assess_technical_statistics
from pathlib import Path

POLICIES={
 "openfootball":{"licence":"CC0-1.0","reviewed_on":"2026-11-21",
     "licence_url":"https://raw.githubusercontent.com/openfootball/football.json/master/LICENSE.md",
     "freshness":"Daily generation is not guaranteed daily upstream maintenance; no finished flag assumed"},
 "openligadb":{"licence":"ODbL-1.0","reviewed_on":"2026-11-21",
     "licence_url":"https://www.openligadb.de/lizenz","terms_url":"https://www.openligadb.de/nutzungsbedingungen",
     "attribution":"Data: OpenLigaDB (https://www.openligadb.de), ODbL 1.0",
     "reuse":"Attribution; share-alike for public modified databases; no third-party image rights",
     "freshness":"Community maintained; no editorial accuracy guarantee or immutable public history",
     "access":"Free community access, no key; centrally cached and maximum one data request per league per 60s"},
}
ODDS={"status":"NO_APPROVED_OPEN_SOURCE","quotes":[],"sources":[
 {"source":"Goaloo","decision":"NOT_REQUESTED_ROBOTS_AJAX_DISALLOWED","evidence":"https://www.goaloo.com/robots.txt"},
 {"source":"football-data.co.uk","decision":"NOT_REQUESTED_PRODUCT_USE_RESTRICTED","evidence":"https://football-data.co.uk/data.php"},
 {"source":"OpenFootball/OpenLigaDB","decision":"ODDS_NOT_PROVIDED"},
 {"source":"The Odds API","decision":"NOT_ENABLED_KEY_AND_SUBSCRIPTION_SERVICE_NOT_OPEN_WEB_DATA",
  "evidence":"https://the-odds-api.com/terms-and-conditions.html","pricing":"https://the-odds-api.com/#get-access"}]}


def run(day,matchday,goaloo_match_id,*,http=None):
 if not 1<=matchday<=34 or not goaloo_match_id.isdigit():raise ValueError("INVALID_EXPLICIT_SCOPE")
 season=season_for(day);start=int(season[:4])
 own=http is None;http=http or EvidenceHTTP()
 report={"strategy":"WEB_SCRAPING_FIRST","stage":2,"writes":False,"sql":"NOT_USED","r2":"NOT_USED",
  "scope":{"league":"de.bl1","date":day.isoformat(),"timezone":"Europe/Istanbul","matchday":matchday,"goaloo_target_id":goaloo_match_id},
  "live_test":own,"observed_at":datetime.now(timezone.utc).isoformat(),"source_policies":POLICIES,
  "odds":ODDS,"sources":{},"observations":[],"requests":http.requests,
  "resource_granularity":"One OpenFootball/Goaloo league-season file and one explicit OpenLigaDB matchday resource; only the selected day is analysed. No date/league/season traversal."}
 resources=[
  ("goaloo",f"https://football.goaloo.com/jsData/matchResult/json/{season}/s8_en.json",parse_goaloo),
  ("openfootball",f"https://raw.githubusercontent.com/openfootball/football.json/master/{start}-{str(start+1)[-2:]}/de.1.json",parse_openfootball),
  ("openligadb",f"https://api.openligadb.de/getmatchdata/bl1/{start}/{matchday}",parse_openligadb)]
 try:
  for provider,url,parser in resources:
   try:
    payload=http.json(url)
    observed=datetime.now(timezone.utc)
    kwargs={"observed_at":observed,"source_url":url}
    rows=parser(payload,day,season,**kwargs) if provider=="goaloo" else parser(payload,day,**kwargs)
    report["observations"].extend(rows)
    report["sources"][provider]={"status":"OBSERVED" if rows else "NO_MATCH_ON_SELECTED_DAY","matches":len(rows),"source_url":url}
   except EvidenceFailure as exc:
    report["sources"][provider]={"status":"UNAVAILABLE","error_code":str(exc),"source_url":url}
   except Exception:
    report["sources"][provider]={"status":"INVALID_SOURCE_SCHEMA","error_code":"SOURCE_VALIDATION_FAILED","source_url":url}
  report["decision"]=assess_result(report["observations"],goaloo_match_id=goaloo_match_id)
  report["status"]=report["decision"]["status"]
  report["technical_statistics"]={"status":"NOT_FETCHED","usable_rows":0,"reason":"No new technical collection"}
  # Audit the earlier, explicitly labelled local evidence; never use it as a live fallback.
  prior_path=Path(__file__).resolve().parents[3]/"docs/reports/scraping-2026-11-20-bundesliga.json"
  if day==date(2026,11,20) and goaloo_match_id=="3018869" and prior_path.exists():
   prior=json.loads(prior_path.read_text(encoding="utf-8-sig"))
   observations=[]
   for match in prior.get("matches",[]):
    if match.get("provider_match_id")!=goaloo_match_id:continue
    for stat in match.get("statistics",[]):
     stamp=next((r["observed_at"] for r in prior.get("requests",[]) if r["url"]==stat.get("source_url")),None)
     observations.append({**stat,"observed_at":stamp})
   checks=assess_technical_statistics(observations,report["decision"],source_match_id=goaloo_match_id,
             observed_at=datetime.now(timezone.utc),permission_confirmed=False)
   report["technical_statistics"]={"status":"PRIOR_SNAPSHOT_REVIEWED_NOT_REFETCHED","prior_evidence":prior_path.name,
      "observed_rows":len(checks),"usable_rows":sum(c["usable"] for c in checks),
      "checks":[{"statistic_id":c["observation"].get("id"),"usable":c["usable"],"reasons":c["reasons"]} for c in checks]}
  if report["status"]=="RESULT_VERIFIED":
   final=report["decision"]["final_result"]
   report["pipeline_preparation"]={"status":"CANDIDATE_ONLY_NOT_PERSISTED","provider":final["primary_provider"],
      "provider_match_id":final["primary_source_match_id"],"canonical_league":"de.bl1",
      "result":final,"sql_write":False,"r2_write":False,
      "import_blockers":["No persistence enabled in this read-only command","Source attribution and canonical league/alias mapping must be retained in a separate controlled import"]}
 finally:
  if own:http.close()
 return report


def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument("--date",type=date.fromisoformat,required=True)
 parser.add_argument("--matchday",type=int,choices=range(1,35),required=True)
 parser.add_argument("--goaloo-match-id",required=True)
 args=parser.parse_args(argv)
 report=run(args.date,args.matchday,args.goaloo_match_id)
 print(json.dumps(report,ensure_ascii=True,indent=2))
 return 0 if report["status"]=="RESULT_VERIFIED" else 1


if __name__=="__main__":raise SystemExit(main())
