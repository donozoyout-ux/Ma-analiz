"""Fail-closed read-only football-data.org corroboration; no persistence."""
import unicodedata
from app.providers.result_sources import utc


def name_key(name):
    return " ".join(unicodedata.normalize("NFKC", str(name or "")).casefold().split())


def corroborate(primary, secondary):
    result = {"status": "PENDING_INDEPENDENT_VERIFICATION", "matches": [],
              "conflicts": [], "unmatched": [], "writes_allowed": False,
              "persistence_ready": False}
    if not isinstance(primary, list) or not isinstance(secondary, list):
        raise ValueError("INVALID_OBSERVATIONS")
    used = set()
    for row in primary:
        if row.get("provider") != "openligadb" or row.get("league") != "de.bl1":
            continue
        fixture = str(row.get("source_match_id"))
        kickoff = utc(row.get("start_time"))
        score = row.get("reported_score")
        if (not kickoff or not row.get("final_confirmed") or
                not isinstance(score, list) or len(score) != 2 or
                any(type(x) is not int or x < 0 for x in score)):
            result["unmatched"].append({"match": fixture, "reason": "PRIMARY_NOT_FINAL"})
            continue
        peers = [p for p in secondary
            if p.get("provider") == "football-data.org" and p.get("league") == "de.bl1"
            and utc(p.get("start_time")) == kickoff
            and name_key(p.get("home_team")) == name_key(row.get("home_team"))
            and name_key(p.get("away_team")) == name_key(row.get("away_team"))]
        if len(peers) != 1 or str(peers[0].get("source_match_id")) in used:
            result["unmatched"].append({"match": fixture, "reason": "EXACT_IDENTITY_NOT_MATCHED"})
            continue
        p = peers[0]
        if not p.get("final_confirmed") or p.get("source_status") != "FINISHED":
            result["unmatched"].append({"match": fixture, "reason": "SECONDARY_NOT_FINAL"})
        elif p.get("reported_score") != score:
            result["conflicts"].append({"match": fixture, "reason": "SCORE_CONFLICT"})
        else:
            used.add(str(p["source_match_id"]))
            result["matches"].append({"openligadb_match_id": fixture,
                "football_data_match_id": str(p["source_match_id"]),
                "score": score, "start_time": kickoff.isoformat(),
                "decision": "CANDIDATE_ONLY_NOT_PERSISTED"})
    if result["conflicts"]:
        result["status"] = "CONFLICT"
    elif result["matches"] and not result["unmatched"]:
        result["status"] = "MATCHED_CANDIDATES_NOT_PERSISTED"
    return result
