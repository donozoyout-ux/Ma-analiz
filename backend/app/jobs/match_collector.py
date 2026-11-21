"""Stage 7B public-source collector. There is deliberately no production writer."""
import argparse
from contextlib import contextmanager
from datetime import date, timedelta
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from app.providers.collector_http import CollectorHTTP, now_utc
from app.providers.collector_policy import policy_digest
from app.providers.evidence_http import EvidenceFailure
from app.providers.multileague import PILOT, bounded, compare, digest, openfootball, openligadb
from app.providers.result_sources import utc

POLICY_PATH = Path(__file__).resolve().parents[1] / "providers/collector_policies.json"
LEAGUES = {
    "de.bl1": {"mode": "CONTROLLED_PILOT", "season": "2026-2027", "candidates": [("bl1", 4937)]},
    "en.pl": {"mode": "QUARANTINE_ONLY", "season": "2026-2027", "candidates": [("pl", 5996), ("pl1", 5993)]},
    "tr.sl": {"mode": "NOT_AVAILABLE", "season": "2026-2027", "candidates": []},
}


def atomic_json(path, value):
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def local_state(directory):
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / "collector.lock"
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError:
        raise EvidenceFailure("LOCAL_COLLECTOR_LOCK_BUSY") from None
    try:
        handle.close()
        path = directory / "state.json"
        if path.exists():
            if path.stat().st_size > 4_000_000:
                raise EvidenceFailure("LOCAL_STATE_INVALID")
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                if (state.get("version") != 1 or any(not isinstance(state.get(k, {}), dict)
                                                    for k in ("http", "blocks", "seen", "identities"))):
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise EvidenceFailure("LOCAL_STATE_INVALID") from None
        else:
            state = {"version": 1}
        yield state
        atomic_json(path, state)
    finally:
        lock.unlink(missing_ok=True)


def collect(state, *, day=None, http=None, clock=now_utc, policies=None):
    at = clock()
    day = day or at.date()
    if at.utcoffset() is None or day not in {at.date(), at.date() - timedelta(days=1)}:
        raise EvidenceFailure("ONLY_TODAY_OR_YESTERDAY_UTC_ALLOWED")
    report = {"stage": "7B", "run_id": uuid4().hex, "observed_at": at.isoformat(), "date_utc": day.isoformat(),
              "evidence_kind": "LIVE_SOURCE_OBSERVATION" if http is None else "INJECTED_TEST_TRANSPORT",
              "SQL_WRITE": "NOT_RUN", "R2_WRITE": "NOT_RUN", "LIVE_DELETE": "NOT_RUN",
              "AUTO_RETENTION_DELETE_ENABLED": False, "production_ready": False,
              "leagues": {}, "source_errors": [], "policies": [], "warnings": []}
    last = utc(state.get("last_completed_at"))
    if last and (at - last).total_seconds() > 18 * 3600:
        report["warnings"].append("COLLECTOR_GAP_OVER_18_HOURS")
    if not last:
        report["warnings"].append("NO_PREVIOUS_RUN_STATE_CACHE_NOT_A_LEDGER")
    own = http is None
    http = http or CollectorHTTP(state, clock=clock)
    report["requests"] = http.requests
    seen = state.setdefault("seen", {})
    identities = state.setdefault("identities", {})
    policies = policies if policies is not None else json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    report["config_sha256"] = digest({"leagues": LEAGUES, "policies": policies, "schema_version": 1})

    def fetch(key, url):
        previous = state.get("http", {}).get(key, {}).get("sha256")
        body, meta = http.get(key, url)
        return body, {**meta, "resource_changed": previous is not None and previous != meta["sha256"]}

    def failure(key, code):
        report["source_errors"].append({"resource": key, "error_code": code})

    try:
        if not date(2026, 7, 1) <= day <= date(2027, 6, 30):
            raise EvidenceFailure("SEASON_CONFIGURATION_REVIEW_REQUIRED")
        if at.date() > date.fromisoformat(policies["expires_on"]):
            raise EvidenceFailure("LICENCE_REVIEW_EXPIRED")
        allowed = {"openligadb": True, "openfootball": True}
        for key, document in policies["documents"].items():
            provider = key.split("_")[0]
            try:
                body, meta = fetch(key, document["url"])
                fingerprint = policy_digest(body, document.get("format", "raw"))
                verified = fingerprint == document["sha256"]
                report["policies"].append({"resource": key, "raw_response_sha256": meta["sha256"], "reviewed_content_sha256": fingerprint,
                                          "status": "REVIEWED_HASH_MATCH" if verified else "LICENCE_REVIEW_REQUIRED"})
                if not verified:
                    allowed[provider] = False
                    failure(key, "LICENCE_OR_TERMS_CHANGED")
            except EvidenceFailure as exc:
                allowed[provider] = False
                failure(key, str(exc))
            except Exception:
                allowed[provider] = False
                failure(key, "LEGAL_DOCUMENT_STRUCTURE_CHANGED")

        for league, config in LEAGUES.items():
            result = {**config, "observations": [], "sources": [], "production_provider_selected": False}
            report["leagues"][league] = result
            if config["mode"] == "NOT_AVAILABLE":
                continue
            scope = {**PILOT[league], "date": day.isoformat()}

            def observe(provider, key, url, identity=None):
                source = {"resource": key, "provider": provider, "source_url": url}
                result["sources"].append(source)
                if not allowed[provider]:
                    source["status"] = "LICENCE_OR_ACCESS_REVIEW_REQUIRED"
                    return
                try:
                    body, meta = fetch(key, url)
                    received = utc(meta["received_at"])
                    if received is None or received > clock():
                        raise ValueError()
                    kwargs = {"observed_at": received, "source_url": url, "scope": scope}
                    payload = json.loads(body)
                    rows = (openligadb(payload, league, *identity, **kwargs) if identity
                            else openfootball(payload, league, **kwargs))
                    for row in rows:
                        if any(len(row[k]) > 200 for k in ("home_team", "away_team")):
                            raise ValueError()
                        if row.get("source_match_id") is not None and len(row["source_match_id"]) > 160:
                            raise ValueError()
                        updated = row.get("source_updated_at_raw")
                        if updated is not None and (not isinstance(updated, str) or len(updated) > 128):
                            raise ValueError()
                    rows, total = bounded(rows, 2)
                    source.update(status="OBSERVED" if rows else "NO_MATCH_ON_SELECTED_DAY", selected_day_count=total, **meta)
                    for row in rows:
                        row.update(season=config["season"], raw_response_sha256=meta["sha256"],
                                   response_received_at=meta["received_at"], checked_at=clock().isoformat(),
                                   evidence_kind="CACHED_CONDITIONAL_REVALIDATION" if meta["cached"] else "LIVE_HTTP_200",
                                   source_updated_at_utc=(utc(row.get("source_updated_at_raw")).isoformat()
                                                          if utc(row.get("source_updated_at_raw")) else None),
                                   home_source_id=row.get("home_source_id"), away_source_id=row.get("away_source_id"),
                                   source_updated_at_raw=row.get("source_updated_at_raw"),
                                   source_update_time_status="EXPLICIT_UTC" if utc(row.get("source_updated_at_raw")) else "UNKNOWN",
                                   verified_final_score=None, validation_status="REVIEW_REQUIRED", production_ready=False)
                        row["quarantine_reasons"] = sorted(set(row["issues"] + ["NO_APPROVED_CROSS_SOURCE_IDENTITY_AND_UTC_CONFIRMATION"]
                            + (["PROVIDER_PL_PL1_UNRESOLVED"] if league == "en.pl" else [])
                            + (["SOURCE_TEAM_IDS_NOT_AVAILABLE"] if row["home_source_id"] is None else [])))
                        if any("CONFLICT" in issue for issue in row["issues"]):
                            row["validation_status"] = "CONFLICT"
                        # A source's unrelated-row update changes the response SHA, not this match's content version.
                        content = {k: row[k] for k in ("league", "season", "source_match_id", "home_team", "away_team",
                            "home_source_id", "away_source_id", "start_time", "source_status", "reported_score", "issues")}
                        version = digest(content)
                        key_ = row["observation_key"]
                        identity = [row[k] for k in ("league", "season", "source_match_id", "home_source_id",
                                                    "away_source_id", "home_team", "away_team")]
                        if key_ in identities and identities[key_] != identity:
                            row["validation_status"] = "CONFLICT"
                            row["quarantine_reasons"].append("SOURCE_IDENTITY_CHANGED")
                        identities.setdefault(key_, identity)
                        row["change"] = "UNCHANGED" if seen.get(key_) == version else "UPDATED" if key_ in seen else "NEW"
                        seen[key_] = version
                        row["content_version"] = version
                    result["observations"].extend(rows)
                except EvidenceFailure as exc:
                    source.update(status="UNAVAILABLE", error_code=str(exc))
                    failure(key, str(exc))
                except Exception:
                    source.update(status="REJECTED", error_code="SOURCE_SCHEMA_IDENTITY_OR_UTC_INVALID")
                    failure(key, "SOURCE_SCHEMA_IDENTITY_OR_UTC_INVALID")

            observe("openfootball", f"football:{league}",
                    f"https://raw.githubusercontent.com/openfootball/football.json/master/2026-27/{scope['file']}.json")
            for shortcut, league_id in config["candidates"]:
                if not allowed["openligadb"]:
                    continue
                key = f"group:{shortcut}"
                try:
                    body, meta = fetch(key, f"https://api.openligadb.de/getcurrentgroup/{shortcut}")
                    group = json.loads(body)
                    number = group["groupOrderID"]
                    if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= 38:
                        raise ValueError()
                    scope["oldb"] = [(shortcut, league_id, number)]
                    result["sources"].append({"resource": key, "status": "CURRENT_GROUP_OBSERVED", "matchday": number, **meta})
                    observe("openligadb", f"matches:{shortcut}:{number}",
                            f"https://api.openligadb.de/getmatchdata/{shortcut}/2026/{number}", (shortcut, league_id, number))
                except EvidenceFailure as exc:
                    failure(key, str(exc))
                except Exception:
                    failure(key, "CURRENT_GROUP_SCHEMA_INVALID")
            result["comparisons"] = compare(result["observations"])
            for comparison in result["comparisons"]:
                if comparison["status"] == "CONFLICT":
                    for row in result["observations"]:
                        if row["observation_key"] in {comparison["left"], comparison["right"]}:
                            row["validation_status"] = "CONFLICT"
                            row["quarantine_reasons"].append("CROSS_SOURCE_CONFLICT")
        conflicts = any(row["validation_status"] == "CONFLICT" for result in report["leagues"].values()
                        for row in result["observations"])
        report["status"] = "PARTIAL_SOURCE_FAILURE" if report["source_errors"] else "DATA_CONFLICT" if conflicts else "READ_ONLY_COMPLETE"
    except EvidenceFailure as exc:
        failure("collector", str(exc))
        report["status"] = "BLOCKED"
    finally:
        if own:
            http.close()
    state["last_completed_at"] = clock().isoformat()
    state["seen"] = dict(list(seen.items())[-2000:])
    state["identities"] = {k: identities[k] for k in state["seen"] if k in identities}
    # Bounded ephemeral cache, not a history archive or durable SQL import ledger.
    state["http"] = dict(list(state.get("http", {}).items())[-16:])
    while len(json.dumps(state)) > 3_000_000 and state["http"]:
        del state["http"][next(iter(state["http"]))]
    while len(json.dumps(state)) > 3_000_000 and state["seen"]:
        oldest = next(iter(state["seen"]))
        del state["seen"][oldest]
        state["identities"].pop(oldest, None)
    report["counts"] = {kind: sum(row["change"] == kind for result in report["leagues"].values()
                               for row in result["observations"]) for kind in ("NEW", "UPDATED", "UNCHANGED")}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, help="Only today/yesterday UTC, one day per run")
    parser.add_argument("--state-dir", type=Path, default=Path(".collector"))
    args = parser.parse_args(argv)
    directory = args.state_dir
    try:
        with local_state(directory) as state:
            report = collect(state, day=args.date)
            atomic_json(directory / "report.json", report)
    except Exception as exc:
        code = str(exc) if isinstance(exc, EvidenceFailure) else "COLLECTOR_INTERNAL_FAILURE"
        if not re.fullmatch(r"[A-Z0-9_]{1,80}", code):
            code = "COLLECTOR_INTERNAL_FAILURE"
        # Fixed codes only; no exception text, environment, URL or traceback.
        report = {"status": "BLOCKED", "error_code": code, "run_id": uuid4().hex,
                  "SQL_WRITE": "NOT_RUN", "R2_WRITE": "NOT_RUN", "LIVE_DELETE": "NOT_RUN"}
        # Do not overwrite another process's report when its local lock is held.
        if code != "LOCAL_COLLECTOR_LOCK_BUSY":
            try:
                directory.mkdir(parents=True, exist_ok=True)
                atomic_json(directory / "report.json", report)
            except OSError:
                report["error_code"] = "LOCAL_AUDIT_WRITE_FAILED"
    print(json.dumps({k: report[k] for k in ("run_id", "status", "SQL_WRITE", "R2_WRITE", "LIVE_DELETE", "error_code", "counts", "warnings") if k in report}))
    return 0 if report["status"] == "READ_ONLY_COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
