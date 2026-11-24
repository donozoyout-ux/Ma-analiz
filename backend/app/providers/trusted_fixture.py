"""Fresh, one-day/one-fixture evidence. Saved JSON is never accepted as a trusted proof."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
import json

from app.analysis.import_review import plan_report
from app.jobs.match_collector import LEAGUES, POLICY_PATH
from app.providers.collector_http import CollectorHTTP, now_utc
from app.providers.collector_policy import policy_digest
from app.providers.evidence_http import EvidenceFailure
from app.providers.multileague import PILOT, bounded, digest, openfootball, openligadb
from app.providers.result_sources import utc

_ISSUER = object()


@dataclass(frozen=True)
class TrustedFixture:
    report: dict
    seal: str
    live: bool
    _issuer: object

    def candidate(self, now):
        if self._issuer is not _ISSUER or digest(self.report) != self.seal:
            raise EvidenceFailure("TRUSTED_PROOF_REQUIRED_OR_CHANGED")
        plan = plan_report(self.report, now=now)
        primary = [r for r in plan["candidates"] if r["evidence"]["identity"]["provider"] == "openligadb"]
        if len(primary) != 1:
            raise EvidenceFailure("NO_EXACT_SINGLE_CANDIDATE")
        return primary[0]


def revalidate(day, *, match_id=None, state=None, http=None, clock=now_utc):
    """Only current matchday metadata + its single resource + one OpenFootball file."""
    now = clock()
    if (day not in {now.date(), now.date() + timedelta(days=1)} or not 2026 <= day.year <= 2027
            or (match_id is not None and (not str(match_id).isdigit() or str(match_id) == "83237"))):
        raise EvidenceFailure("SINGLE_FUTURE_BUNDESLIGA_SCOPE_REQUIRED")
    state = {} if state is None else state
    # Retain pacing/access blocks, but require new 200 bodies (not cached authority).
    state["http"] = {}
    own = http is None
    http = http or CollectorHTTP(state, clock=clock)
    policies = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    report = {"stage": "7B", "collector": "STAGE7D_TRUSTED_SINGLE_FIXTURE", "date_utc": day.isoformat(),
        "observed_at": now.isoformat(), "evidence_kind": "LIVE_SOURCE_OBSERVATION" if own else "INJECTED_TEST_TRANSPORT",
        "config_sha256": digest({"leagues": LEAGUES, "policies": policies, "schema_version": 1}),
        "requests": http.requests, "policies": [], "leagues": {"de.bl1": {"observations": []}},
        "SQL_WRITE": "NOT_RUN", "R2_WRITE": "NOT_RUN", "LIVE_DELETE": "NOT_RUN", "FINAL_IMPORT": "BLOCKED"}
    observations = report["leagues"]["de.bl1"]["observations"]
    try:
        if now.date().isoformat() > policies["expires_on"]:
            raise EvidenceFailure("LICENCE_REVIEW_EXPIRED")
        for key, document in policies["documents"].items():
            body, meta = http.get(key, document["url"])
            value = policy_digest(body, document.get("format", "raw"))
            if meta["cached"] or value != document["sha256"]:
                raise EvidenceFailure("FRESH_LICENCE_REVIEW_REQUIRED")
            report["policies"].append({"resource": key, "status": "REVIEWED_HASH_MATCH",
                "reviewed_content_sha256": value, "raw_response_sha256": meta["sha256"]})
        body, _ = http.get("group:bl1", "https://api.openligadb.de/getcurrentgroup/bl1")
        number = json.loads(body)["groupOrderID"]
        if type(number) is not int or not 1 <= number <= 34:
            raise EvidenceFailure("CURRENT_GROUP_INVALID")
        scope = {**PILOT["de.bl1"], "date": day.isoformat(), "oldb": [("bl1", 4937, number)]}
        url = f"https://api.openligadb.de/getmatchdata/bl1/2026/{number}"
        body, meta = http.get(f"matches:bl1:{number}", url)
        if meta["cached"]: raise EvidenceFailure("FRESH_HTTP_200_REQUIRED")
        rows = openligadb(json.loads(body), "de.bl1", "bl1", 4937, number,
                          observed_at=utc(meta["received_at"]), source_url=url, scope=scope)
        # Validate ALL same-day identities before selecting one; never hide conflicts by truncating.
        rows, _ = bounded(rows, len(rows))
        choices = [r for r in rows if str(r["source_match_id"]) == str(match_id)] if match_id else sorted(
            [r for r in rows if r["source_status"] == "NOT_FINISHED" and utc(r["start_time"]) > clock()
             and r["source_match_id"] != "83237"], key=lambda r: (r["start_time"], r["source_match_id"]))[:1]
        if len(choices) != 1:
            raise EvidenceFailure("NO_EXACT_SINGLE_SCHEDULED_FIXTURE")

        def add(rows, meta):
            samples, _ = bounded(rows, 1)
            for row in samples:
                row.update(season="2026-2027", raw_response_sha256=meta["sha256"],
                    observed_at=meta["received_at"], response_received_at=meta["received_at"],
                    checked_at=clock().isoformat(), evidence_kind="LIVE_HTTP_200", validation_status="REVIEW_REQUIRED",
                    home_source_id=row.get("home_source_id"), away_source_id=row.get("away_source_id"),
                    source_updated_at_raw=row.get("source_updated_at_raw"))
                observations.append(row)
        add(choices, meta)
        url = "https://raw.githubusercontent.com/openfootball/football.json/master/2026-27/de.1.json"
        body, meta = http.get("football:de.bl1", url)
        if meta["cached"]: raise EvidenceFailure("FRESH_HTTP_200_REQUIRED")
        peers = openfootball(json.loads(body), "de.bl1", observed_at=utc(meta["received_at"]), source_url=url, scope=scope)
        peers = [p for p in peers if (p["home_team"], p["away_team"]) == (choices[0]["home_team"], choices[0]["away_team"])]
        if len(peers) > 1:
            raise EvidenceFailure("AMBIGUOUS_CORROBORATOR")
        add(peers, meta)
        report["status"] = "SOURCE_OBSERVED_NOT_AUTHORIZED"
    except EvidenceFailure as exc:
        report.update(status="BLOCKED", error_code=str(exc))
    except Exception:
        report.update(status="BLOCKED", error_code="SOURCE_SCHEMA_OR_POLICY_INVALID")
    finally:
        state["http"] = {}
        if own: http.close()
    return TrustedFixture(deepcopy(report), digest(report), own, _ISSUER)
