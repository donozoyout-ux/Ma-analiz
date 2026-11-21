"""Read-only GitHub + optional storage watchdog. No retry dispatch or outbound messages."""
from datetime import datetime, timezone
import json
import os
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError

WORKFLOW = "retention-scheduler.yml"


def github_get(repo, path, token):
    url = f"https://api.github.com/repos/{repo}" + (f"/{path}" if path else "")
    request = Request(url, headers={"Authorization":f"Bearer {token}", "Accept":"application/vnd.github+json",
                                   "X-GitHub-Api-Version":"2022-11-28", "User-Agent":"MaAnalizRetentionWatchdog/1"})
    with urlopen(request, timeout=20) as response: return json.load(response)


def stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (ValueError, TypeError, AttributeError): return None


def assess(workflow, runs, repo, *, now, storage=None):
    errors, warnings, checks = [], [], {}
    if workflow.get("state") != "active": errors.append("RETENTION_WORKFLOW_NOT_ACTIVE")
    created = stamp(workflow.get("created_at"))
    for mode, minutes in (("archive", 45), ("auto", 120)):
        scheduled = [r for r in runs if r.get("event") == "schedule" and
                     r.get("display_title", "").startswith(f"Retention {mode}")]
        completed = [r for r in scheduled if r.get("conclusion") == "success"]
        success = max(completed, key=lambda r:r.get("created_at", ""), default=None)
        at = stamp(success.get("created_at")) if success else None
        age = (now-at).total_seconds() if at else None
        bootstrap = created and 0 <= (now-created).total_seconds() < minutes*60
        if age is None or age > minutes*60 or age < 0:
            (warnings if bootstrap and not success else errors).append(f"NO_RECENT_SUCCESSFUL_{mode.upper()}_SCHEDULE")
        latest = max(scheduled, key=lambda r:r.get("created_at", ""), default=None)
        if latest and latest.get("conclusion") in {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}:
            errors.append(f"LATEST_{mode.upper()}_FAILED_OR_CANCELLED")
        checks[mode] = {"threshold_minutes": minutes, "last_success_age_seconds": age,
                        "latest_run_id": latest.get("id") if latest else None}
    pushed = stamp(repo.get("pushed_at"))
    if repo.get("private") is False and pushed and (now-pushed).days >= 50:
        warnings.append("PUBLIC_REPO_LAST_PUSH_OLDER_THAN_50_DAYS_REVIEW_INACTIVITY")
    from app.jobs.scheduler_health import extend
    return extend({"status": "FAILED" if errors else "PASS", "errors": errors, "warnings": warnings, "checks": checks,
            "limitation": "THIS_WATCHDOG_CAN_ALSO_BE_DISABLED_AFTER_60_DAYS;_INDEPENDENT_MONITORING_REQUIRED"},
            runs=runs, storage=storage, now=now)


def publish(result):
    print(json.dumps(result, sort_keys=True))
    output = os.environ.get("WATCHDOG_REPORT_PATH")
    if output:
        from pathlib import Path
        try:
            Path(output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        except OSError:
            print("::error::WATCHDOG_REPORT_WRITE_FAILED")
            return 1
    for code in result["errors"]: print(f"::error::{code}")
    for code in result["warnings"]: print(f"::warning::{code}")
    return 1 if result["errors"] else 0


def unavailable(code):
    return publish({"status":"FAILED", "state":"INSUFFICIENT_EVIDENCE", "errors":[code], "warnings":[],
        "storage_evidence":{"status":"NOT_VERIFIED"}, "LIVE_DELETE":"NOT_RUN", "SQL_ARCHIVE_WRITE":"NOT_RUN"})


def main():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not token:
        return unavailable("WATCHDOG_GITHUB_CONFIGURATION_MISSING")
    def get(path):
        return github_get(repo, path, token)
    try:
        workflow = get(f"actions/workflows/{WORKFLOW}")
        runs = get(f"actions/workflows/{WORKFLOW}/runs?per_page=100")["workflow_runs"]
        metadata = get("")
        storage = {"status": "NOT_VERIFIED"}
        if os.environ.get("WATCHDOG_STORAGE_CHECK") == "true":
            from app.jobs.retention_validation import run
            storage = run()
        result = assess(workflow, runs, metadata, now=datetime.now(timezone.utc), storage=storage)
    except HTTPError as exc:
        return unavailable(f"WATCHDOG_GITHUB_HTTP_{int(exc.code)}")
    except Exception:
        return unavailable("WATCHDOG_OBSERVATION_NOT_VERIFIED")
    return publish(result)


if __name__ == "__main__": raise SystemExit(main())
