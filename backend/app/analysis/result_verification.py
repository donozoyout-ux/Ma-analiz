"""Fail-closed reconciliation; no persistence and no fuzzy aliases or time guesses."""
import unicodedata
from app.providers.result_sources import utc

# Manually reviewed for exactly one fixture, not a global fuzzy-name transform.
# Evidence and source team IDs: docs/reports/result-alias-review-2026-11-20.json.
_REVIEWED_TEAMS = {
    ("goaloo", "bayern munchen"): "de.bayern-muenchen",
    ("goaloo", "fc koln"): "de.1-fc-koeln",
    ("openligadb", "fc bayern münchen"): "de.bayern-muenchen",
    ("openligadb", "1. fc köln"): "de.1-fc-koeln",
    ("openfootball", "fc bayern münchen"): "de.bayern-muenchen",
    ("openfootball", "1. fc köln"): "de.1-fc-koeln",
}


def team_key(source,name,reviewed=False):
    key=" ".join(unicodedata.normalize("NFKC",str(name or "")).casefold().split())
    return _REVIEWED_TEAMS.get((source,key),key) if reviewed else key


def assess_result(observations, *, goaloo_match_id):
    targets=[r for r in observations if r["provider"]=="goaloo" and r["source_match_id"]==goaloo_match_id]
    decision={"status":"UNVERIFIED","final_result":None,"conflicts":[],"issues":[],"matched_sources":[],"warnings":[],
              "persistence_ready":False,"writes_allowed":False}
    if len(targets)!=1:
        decision["issues"].append("TARGET_SOURCE_ID_MISSING_OR_AMBIGUOUS")
        return decision
    target=targets[0]
    reviewed=(goaloo_match_id=="3018869" and target.get("league")=="de.bl1"
              and utc(target.get("start_time"))==utc("2026-11-20T19:30:00Z"))
    def pair(row):
        use_review=reviewed
        if row["provider"]=="openligadb" and use_review:
            use_review=(row.get("source_match_id")=="83237" and row.get("home_source_id")==40 and row.get("away_source_id")==65)
        return (team_key(row["provider"],row.get("home_team"),use_review),team_key(row["provider"],row.get("away_team"),use_review))
    decision["identity_review"]="result-alias-review-2026-11-20" if reviewed else "EXACT_NAMES_ONLY"
    peers=[r for r in observations if pair(r)==pair(target)]
    known=[]
    for row in peers:
        if row.get("league")!="de.bl1":
            decision["conflicts"].append({"provider":row["provider"],"field":"league"});continue
        kickoff=utc(row.get("start_time"))
        if kickoff is None:
            decision["warnings"].append(f"INCOMPLETE_CORROBORATOR_KICKOFF:{row['provider']}");continue
        if kickoff!=utc(target.get("start_time")):
            decision["conflicts"].append({"provider":row["provider"],"field":"start_time"});continue
        observed=utc(row.get("observed_at"))
        if observed is None or kickoff>=observed:
            decision["issues"].append(f"KICKOFF_NOT_PROVEN_PAST:{row['provider']}")
        known.append(row)
    represented={r["provider"] for r in known}
    incomplete={r["provider"] for r in peers if not r.get("start_time")}
    for source in {r["provider"] for r in observations}-represented-incomplete:
        decision["issues"].append(f"SOURCE_IDENTITY_NOT_RECONCILED:{source}")
    decision["matched_sources"]=sorted(represented)
    scores={tuple(r["reported_score"]) for r in known if r.get("reported_score") is not None}
    if len(scores)>1:decision["conflicts"].append({"field":"final_score","values":[list(s) for s in sorted(scores)]})
    if any(r.get("source_status") in {"SCHEDULED","NOT_FINISHED","LIVE","UNKNOWN"} for r in known):
        decision["issues"].append("SOURCE_NOT_FINISHED")
    finals=[r for r in known if r.get("final_confirmed") and r.get("reported_score") is not None]
    licensed_finals=[r for r in finals if r.get("licence") in {"ODbL-1.0","CC0-1.0"}]
    if not licensed_finals:decision["issues"].append("NO_EXPLICIT_FINAL_FROM_REUSABLE_SOURCE")
    if len(represented)<2:decision["issues"].append("NO_INDEPENDENT_FIXTURE_CORROBORATION")
    if any(set(r.get("issues",[])) - {"NO_EXPLICIT_FINISHED_FLAG"} for r in known):
        decision["issues"].append("SOURCE_VALIDATION_ISSUES")
    decision["issues"]=sorted(set(decision["issues"]))
    if decision["conflicts"]:decision["status"]="CONFLICT"
    elif not decision["issues"] and len(scores)==1:
        decision["status"]="RESULT_VERIFIED"
        primary=licensed_finals[0]
        decision["final_result"]={"status":"FINAL","home_team":primary["home_team"],"away_team":primary["away_team"],
            "primary_provider":primary["provider"],"primary_source_match_id":primary["source_match_id"],
            "licence":primary["licence"],"verified_at":max(utc(r["observed_at"]) for r in known).isoformat(),
            "start_time":target["start_time"],"home_score":next(iter(scores))[0],"away_score":next(iter(scores))[1],
            "source_identities":[{"provider":r["provider"],"source_match_id":r["source_match_id"]} for r in known]}
        # Even verified evidence is not a licence/provenance-complete storage rollout.
        decision["persistence_ready"]=False
    return decision


def assess_technical_statistics(rows,decision,*,source_match_id,observed_at,permission_confirmed=False):
    from math import isfinite
    from datetime import datetime
    accepted=[]
    for row in rows:
        reasons=[]
        if decision.get("status")!="RESULT_VERIFIED":reasons.append("FINAL_CHAIN_NOT_VERIFIED")
        identities=(decision.get("final_result") or {}).get("source_identities",[])
        if not any(r.get("provider")=="goaloo" and r.get("source_match_id")==str(source_match_id) for r in identities):
            reasons.append("FINAL_CHAIN_SOURCE_ID_MISMATCH")
        if not permission_confirmed:reasons.append("REUSE_PERMISSION_NOT_CONFIRMED")
        if str(row.get("provider_match_id"))!=str(source_match_id):reasons.append("SOURCE_ID_MISMATCH")
        if not row.get("source_url") or row.get("source")!="goaloo":reasons.append("PROVENANCE_MISSING")
        stamp=utc(row.get("observed_at") or row.get("retrieved_at"))
        if stamp is None or stamp>observed_at:reasons.append("OBSERVATION_TIME_INVALID")
        verified_at=utc((decision.get("final_result") or {}).get("verified_at"))
        if stamp is None or verified_at is None or stamp<verified_at:reasons.append("OBSERVED_BEFORE_FINAL_CONFIRMATION")
        if row.get("period")!="ftstat":reasons.append("NOT_FULL_TIME_PERIOD")
        if row.get("unit") not in {"count","percent","minutes"}:reasons.append("UNIT_UNVERIFIED")
        if row.get("name") not in {"Shots","Shots On Goal","Corner Kicks","Possession","Fouls","Yellow Cards","Red Cards","Offsides"}:
            reasons.append("METRIC_NOT_REVIEWED")
        for field in ("home_value","away_value"):
            value=row.get(field)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(value) or value<0:
                reasons.append("VALUE_MISSING_OR_INVALID")
            elif row.get("unit")=="percent" and value>100:
                reasons.append("PERCENT_OUT_OF_RANGE")
            elif row.get("unit")=="count" and not float(value).is_integer():
                reasons.append("COUNT_NOT_INTEGER")
        accepted.append({"observation":row,"usable":not reasons,"reasons":sorted(set(reasons))})
    return accepted
