"""Stage 7C pure review planning. A saved collector report never grants authority."""
from datetime import timedelta
import json
import re

from app.jobs.match_collector import LEAGUES, POLICY_PATH
from app.providers.multileague import digest
from app.providers.result_sources import score, utc

HASH = re.compile(r"[0-9a-f]{64}")
STATES = {"REVIEW_REQUIRED", "CONFLICT", "REJECTED", "APPROVED_FIXTURE"}
IDENTITY_FIELDS = ("provider", "source_match_id", "league", "season", "home_source_id", "away_source_id",
                   "home_team", "away_team", "start_time")


def fresh(value, now):
    stamp = utc(value)
    return stamp is not None and timedelta(0) <= now - stamp <= timedelta(minutes=10)


def fingerprint(value):
    return isinstance(value, str) and HASH.fullmatch(value) is not None


def snapshot(row, league):
    """Allowlisted evidence only; reject secret-bearing/arbitrary source URLs."""
    provider = row.get("provider")
    url = row.get("source_url", "")
    if provider == "openligadb":
        match = re.fullmatch(r"https://api\.openligadb\.de/getmatchdata/(bl1|pl|pl1)/2026/([1-9][0-9]?)", url)
        if not match:
            raise ValueError("UNAPPROVED_SOURCE_URL")
        resource = f"matches:{match[1]}:{match[2]}"
    elif provider == "openfootball":
        code = {"de.bl1": "de.1", "en.pl": "en.1"}.get(league)
        if url != f"https://raw.githubusercontent.com/openfootball/football.json/master/2026-27/{code}.json":
            raise ValueError("UNAPPROVED_SOURCE_URL")
        resource = f"football:{league}"
    else:
        raise ValueError("PROVIDER_NOT_REVIEWED")
    allowed = (*IDENTITY_FIELDS, "source_league_id", "identity_scope", "record_fingerprint", "source_status",
               "reported_score", "source_final_confirmed", "licence", "attribution", "observed_at", "response_received_at",
               "checked_at", "source_updated_at_raw", "source_updated_at_utc", "raw_response_sha256", "evidence_kind",
               "validation_status")
    item = {key: row.get(key) for key in allowed}
    if item["league"] != league or item["season"] != "2026-2027":
        raise ValueError("LEAGUE_OR_SEASON_MISMATCH")
    for key in ("home_team", "away_team"):
        if not isinstance(item[key], str) or not re.fullmatch(r"[\w .()'’\-]{1,200}", item[key]) or not item[key].strip():
            raise ValueError("TEAM_SCHEMA_INVALID")
    for key in ("observed_at", "response_received_at", "checked_at", "source_updated_at_raw", "source_updated_at_utc", "start_time"):
        if item[key] is not None and (not isinstance(item[key], str) or not re.fullmatch(r"[0-9TZtz :+.\-]{1,64}", item[key])):
            raise ValueError("TIMESTAMP_SCHEMA_INVALID")
    for key in ("home_source_id", "away_source_id"):
        if item[key] is not None and (type(item[key]) is not int or item[key] <= 0):
            raise ValueError("TEAM_NATIVE_ID_INVALID")
    if ((item["source_league_id"] is not None and type(item["source_league_id"]) is not int)
            or (item["record_fingerprint"] is not None and not fingerprint(item["record_fingerprint"]))
            or (item["source_final_confirmed"] is not None and type(item["source_final_confirmed"]) is not bool)):
        raise ValueError("SOURCE_SCHEMA_INVALID")
    expected_attribution = ("Data: OpenLigaDB (https://www.openligadb.de), ODbL 1.0" if provider == "openligadb"
                            else "OpenFootball football.json (CC0-1.0)")
    if item["attribution"] != expected_attribution or item["licence"] not in {"ODbL-1.0", "CC0-1.0"}:
        raise ValueError("LICENCE_SCHEMA_INVALID")
    if (not fingerprint(item["raw_response_sha256"]) or item["source_status"] not in
            {"FINISHED", "NOT_FINISHED", "UNKNOWN", "FIXTURE_ONLY", "FULL_TIME_SCORE_REPORTED"}
            or item["identity_scope"] not in {"bl1:2026", "pl:2026", "pl1:2026", "de.1", "en.1"}
            or item["evidence_kind"] not in {"LIVE_HTTP_200", "CACHED_CONDITIONAL_REVALIDATION"}
            or item["validation_status"] not in {"REVIEW_REQUIRED", "CONFLICT"}):
        raise ValueError("OBSERVATION_SCHEMA_INVALID")
    if item["reported_score"] is not None and (not isinstance(item["reported_score"], list)
            or len(item["reported_score"]) != 2 or score(*item["reported_score"]) is None):
        raise ValueError("SCORE_SCHEMA_INVALID")
    if item["source_match_id"] is not None and not re.fullmatch(r"[1-9][0-9]{0,15}", str(item["source_match_id"])):
        raise ValueError("NATIVE_ID_INVALID")
    if item["source_match_id"] is not None:
        item["source_match_id"] = str(item["source_match_id"])
    if utc(item["start_time"]) is not None:
        item["start_time"] = utc(item["start_time"]).isoformat()
    # Unknown fields, request bodies and caller-supplied ready flags are discarded.
    if len(json.dumps(item, ensure_ascii=True)) > 8000:
        raise ValueError("EVIDENCE_TOO_LARGE")
    item.update(source_url=url, resource=resource)
    return item


def checks_for(item, report, now, policies):
    received = utc(item["response_received_at"])
    evidence = [r for r in report.get("requests", []) if r.get("resource") == item["resource"]
                and r.get("http_status") == 200 and r.get("sha256") == item["raw_response_sha256"]
                and utc(r.get("observed_at")) == received]
    policy_ok = now.date().isoformat() <= policies["expires_on"]
    for key, doc in policies["documents"].items():
        declared = [r for r in report.get("policies", []) if r.get("resource") == key
                    and r.get("status") == "REVIEWED_HASH_MATCH" and r.get("reviewed_content_sha256") == doc["sha256"]]
        document_ok = len(declared) == 1 and any(r.get("resource") == key and r.get("http_status") == 200
            and r.get("sha256") == declared[0].get("raw_response_sha256") and fresh(r.get("observed_at"), now)
            for r in report.get("requests", []))
        policy_ok = policy_ok and document_ok
    kickoff = utc(item["start_time"])
    teams = [item["home_source_id"], item["away_source_id"]]
    expected_license = "ODbL-1.0" if item["provider"] == "openligadb" else "CC0-1.0"
    expected_config = digest({"leagues": LEAGUES, "policies": policies, "schema_version": 1})
    return {
        "collector_config_matches": report.get("config_sha256") == expected_config,
        "source_response_bound": len(evidence) == 1 and fingerprint(item["raw_response_sha256"]),
        "fresh_http_200": item["evidence_kind"] == "LIVE_HTTP_200" and fresh(item["response_received_at"], now)
                          and utc(item["observed_at"]) == received,
        "licence_and_terms_current": bool(policy_ok) and item["licence"] == expected_license and bool(item["attribution"]),
        "utc_kickoff": kickoff is not None,
        "selected_utc_day": kickoff is not None and kickoff.date().isoformat() == report.get("date_utc"),
        "season_utc_window": kickoff is not None and "2026-07-01" <= kickoff.date().isoformat() <= "2027-06-30",
        "native_match_id": item["source_match_id"] is not None,
        "native_team_ids": all(type(v) is int and v > 0 for v in teams) and teams[0] != teams[1],
        "reviewed_league_identity": (item["provider"] == "openligadb" and item["league"] == "de.bl1"
             and item["source_league_id"] == 4937 and item["identity_scope"] == "bl1:2026"
             and re.fullmatch(r"https://api\.openligadb\.de/getmatchdata/bl1/2026/([1-9]|[12][0-9]|3[0-4])", item["source_url"]) is not None),
        "no_source_conflict": item["validation_status"] != "CONFLICT",
        "future_fixture": kickoff is not None and kickoff > now and item["source_status"] == "NOT_FINISHED",
    }


def plan_report(report, *, now, policies=None):
    if now.utcoffset() != timedelta(0):
        raise ValueError("UTC_REVIEW_TIME_REQUIRED")
    policies = policies if policies is not None else json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if report.get("stage") != "7B" or not isinstance(report.get("leagues"), dict):
        raise ValueError("STAGE7B_REPORT_REQUIRED")
    report_hash = digest(report)
    normalized, rejected = [], []
    for league, group in report["leagues"].items():
        if league not in LEAGUES or not isinstance(group, dict) or len(group.get("observations", [])) > 6:
            raise ValueError("BOUNDED_LEAGUE_SCOPE_REQUIRED")
        for row in group.get("observations", []):
            try:
                item = snapshot(row, league)
                if item not in normalized:
                    normalized.append(item)
            except Exception:
                rejected.append({"league": league, "state": "REJECTED", "blockers": ["SOURCE_SCHEMA_OR_URL_INVALID"]})
    items = []
    for item in normalized:
        checks = checks_for(item, report, now, policies)
        rivals = [peer for peer in normalized if peer is not item and peer["league"] == item["league"] and (
            (peer["provider"], peer["source_match_id"]) == (item["provider"], item["source_match_id"]) and item["source_match_id"] is not None
            or (peer["home_team"], peer["away_team"]) == (item["home_team"], item["away_team"]))]
        if any((utc(peer["start_time"]) is not None and utc(item["start_time"]) is not None
                    and utc(peer["start_time"]) != utc(item["start_time"]))
               or (peer["reported_score"] is not None and item["reported_score"] is not None and peer["reported_score"] != item["reported_score"])
               or {peer["source_status"], item["source_status"]} == {"FINISHED", "NOT_FINISHED"}
               or ((peer["provider"], peer["source_match_id"]) == (item["provider"], item["source_match_id"])
                   and any(peer[k] != item[k] for k in IDENTITY_FIELDS)) for peer in rivals):
            checks["no_source_conflict"] = False
        # Corroboration is exact and independently sourced; no guessed UTC or fuzzy aliases.
        peers = [peer for peer in normalized if peer["provider"] != item["provider"] and peer["league"] == item["league"]
                 and (peer["home_team"], peer["away_team"]) == (item["home_team"], item["away_team"])
                 and utc(peer["start_time"]) is not None and utc(peer["start_time"]) == utc(item["start_time"])]
        peer_checks = [checks_for(peer, report, now, policies) for peer in peers]
        checks["independent_fixture_utc"] = any(all(p[k] for k in ("collector_config_matches", "source_response_bound",
                    "fresh_http_200", "licence_and_terms_current", "utc_kickoff", "no_source_conflict")) for p in peer_checks)
        blockers = [key.upper() for key, passed in checks.items() if not passed]
        if item["league"] == "en.pl": blockers.append("PL_PL1_UNRESOLVED")
        if item["league"] == "tr.sl": blockers.append("SOURCE_NOT_AVAILABLE")
        if (item["provider"], str(item["source_match_id"])) in {("openligadb", "83237"), ("goaloo", "3018869")}:
            blockers.append("PRESERVE_EXISTING_VERIFIED_IMPORT_ROUTE")
        observed_final = item["source_status"] == "FINISHED"
        if observed_final:
            blockers.append("INDEPENDENT_FINAL_ADAPTER_NOT_APPROVED")
        identity = {k: item[k] for k in IDENTITY_FIELDS}
        native_key = digest([item["provider"], item["source_match_id"]] if item["source_match_id"] else
                            [item["provider"], item["league"], item["record_fingerprint"]])
        evidence = {"schema_version": 1, "identity": identity, "observation": item, "report_sha256": report_hash,
                    "checks": checks, "corroboration": peers}
        evidence["source_version"] = digest({"identity": identity, "primary_sha256": item["raw_response_sha256"],
            "status": item["source_status"], "score": item["reported_score"], "config": report.get("config_sha256"),
            "corroboration": sorted((p["provider"], str(p["source_match_id"]), p["raw_response_sha256"]) for p in peers)})
        items.append({"source_key": native_key, "evidence_sha256": digest(evidence), "evidence": evidence,
                      "state": "CONFLICT" if not checks["no_source_conflict"] else "REVIEW_REQUIRED",
                      "kind": "FINAL_OBSERVATION" if observed_final else "FIXTURE_OBSERVATION",
                      "source_reported_score": item["reported_score"], "verified_final_score": None,
                      "technical_eligibility": not blockers, "blockers": sorted(set(blockers)), "production_ready": False,
                      "required_authority": ["DURABLE_OPERATOR_REVIEW", "TRUSTED_FRESH_REVALIDATION", "STAGE7D_WRITE_GATE"]})
    return {"stage": "7C", "mode": "READ_ONLY_DRY_RUN", "as_of": now.isoformat(), "report_sha256": report_hash,
            "authority": "SAVED_REPORT_IS_NOT_WRITE_PERMISSION", "SQL_WRITE": "NOT_RUN", "R2_WRITE": "NOT_RUN",
            "LIVE_DELETE": "NOT_RUN", "AUTO_RETENTION_DELETE_ENABLED": False, "production_ready": False,
            "candidates": items, "rejected": rejected,
            "leagues": {k: {"state": "NOT_AVAILABLE" if k == "tr.sl" else "REVIEW_REQUIRED",
                "production_ready": False} for k in LEAGUES}}
